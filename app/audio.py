"""Runtime pronunciation audio domain layer.

Implements ADR-0005 (D48-D56) and tasks/slice-7.md A6:
- Audio-source precedence (D48):
  custom override -> human recording -> remote /speak (<=1s) -> local Piper -> silent None.
- Custom audio as sacred user data (D50):
  actual content validation (WAV, MP3, OGG, WebM <=2MB, <=15s), non-active write,
  crash-safe atomic metadata commit, superseded reclamation, orphan cleanup.
- Disposable automatic audio cache (D50, D53, D54):
  keyed by sha256 + byte size, corrupt/missing cache falls through cleanly to Piper,
  human discovery exact-id only with strict provenance policy.
- Local Piper TTS (D55, D56):
  on-demand generation with pinned engine 1.6.0 and voice de_DE-thorsten-high,
  output validated before caching.
- Zero runtime LLM dependencies (AGENTS R1), no module-level mutable state (AGENTS C1),
  strict user/cache data separation (AGENTS R9).
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import struct
import tempfile
import uuid
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal

# Constants
MAX_MEDIA_BYTES: Final[int] = 2 * 1024 * 1024  # 2 MiB
MAX_DURATION_SECONDS: Final[float] = 15.0  # 15 seconds
MEDIA_POLICY_VERSION: Final[str] = "1.0"
PIPER_PINNED_ENGINE_VERSION: Final[str] = "1.6.0"
PIPER_PINNED_VOICE: Final[str] = "de_DE-thorsten-high"
PIPER_PINNED_VOICE_SHA256: Final[str] = (
    "9df1c43c61149ef9b39e618e2b861fbe41e1fcea9390b2dac62e8761573ea4f1"
)
PIPER_PINNED_VOICE_REV: Final[str] = "8aaa3c9839d2b669cb57a94e1ec92ae0928897e8"
PIPER_VOICE_BASE_URL: Final[str] = (
    f"https://huggingface.co/rhasspy/piper-voices/resolve/{PIPER_PINNED_VOICE_REV}/de/de_DE/thorsten/high"
)
REMOTE_SPEAK_TIMEOUT_MAX: Final[float] = 1.0  # 1.0 second max timeout

SUPPORTED_FORMATS: Final[frozenset[str]] = frozenset({"wav", "mp3", "ogg", "webm"})

FORMAT_MIME_TYPES: Final[dict[str, str]] = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
}

APPROVED_UPSTREAM_SITES: Final[frozenset[str]] = frozenset(
    {
        "commons.wikimedia.org",
        "upload.wikimedia.org",
        "wiktionary.org",
    }
)

# Media policy classification key mapping to eligibility
POLICY_CLASSIFICATIONS: Final[dict[str, tuple[bool, bool]]] = {
    # (runtime_cache_eligible, redistribution_eligible)
    "cc0": (True, True),
    "public_domain": (True, True),
    "cc_by_4_0": (True, True),
    "cc_by_3_0": (True, True),
    "cc_by_sa_4_0": (True, True),
    "cc_by_sa_3_0": (True, True),
    "cc_by_sa_2_0": (True, True),
}


class AudioError(ValueError):
    """Base exception for audio domain operations."""


class MediaValidationError(AudioError):
    """Raised when untrusted audio bytes fail container, size, duration, or magic validation."""


class CustomAudioError(AudioError):
    """Raised when custom audio operations fail."""


class ProvenancePolicyError(AudioError):
    """Raised when human recording provenance or media policy checks fail."""


@dataclass(frozen=True, slots=True)
class ValidatedMedia:
    """Validated media container metadata."""

    format: str
    mime_type: str
    byte_size: int
    sha256: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class CustomPronunciationRecord:
    """Persisted custom pronunciation row matching reference/schema.sql."""

    note_id: int
    media_filename: str
    sha256: str
    byte_size: int
    format: str
    source_type: str
    created_at: str


@dataclass(frozen=True, slots=True)
class HumanAudioProvenance:
    """Structured provenance metadata for retained human pronunciation recordings (ADR-0005 D53)."""

    media_policy_version: str
    upstream_source_site: str
    upstream_identifier: str
    upstream_revision: str | None
    media_source_ref: str
    retrieval_metadata: Mapping[str, str]
    author_attribution: str | None
    raw_license: str
    policy_classification_key: str
    runtime_cache_eligible: bool
    redistribution_eligible: bool


@dataclass(frozen=True, slots=True)
class AudioResolutionResult:
    """Result of pronunciation audio resolution across all precedence layers."""

    source: Literal["custom", "human", "remote", "piper"] | None
    audio_bytes: bytes | None
    format: str | None
    mime_type: str | None
    sha256: str | None
    byte_size: int | None
    media_filename: str | None = None
    provenance: HumanAudioProvenance | None = None


# ---------------------------------------------------------------------------
# Media Container Validation & Duration Parsing (pure stdlib)
# ---------------------------------------------------------------------------


def _parse_wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            framerate = w.getframerate()
            nframes = w.getnframes()
            if framerate <= 0:
                raise MediaValidationError("invalid WAV framerate")
            return nframes / float(framerate)
    except Exception as exc:
        raise MediaValidationError(f"corrupt WAV data: {exc}") from exc


_MP3_BITRATES: Final[dict[tuple[int, int], list[int]]] = {
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0],
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
}

_MP3_SAMPLERATES: Final[dict[int, list[int]]] = {
    1: [44100, 48000, 32000, 0],
    2: [22050, 24000, 16000, 0],
    3: [11025, 12000, 8000, 0],
}


def _parse_mp3_duration(data: bytes) -> float:
    pos = 0
    if len(data) >= 10 and data[:3] == b"ID3":
        tag_size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        pos = 10 + tag_size
        if len(data) < pos + 4:
            raise MediaValidationError("corrupt MP3 data: ID3 tag without audio frames")

    # Find first audio frame sync
    found_frame = False
    first_frame_pos = pos
    while pos + 4 <= len(data):
        if data[pos] == 0xFF and (data[pos + 1] & 0xE0) == 0xE0:
            ver_bits = (data[pos + 1] >> 3) & 0x03
            layer_bits = (data[pos + 1] >> 1) & 0x03
            br_idx = (data[pos + 2] >> 4) & 0x0F
            sr_idx = (data[pos + 2] >> 2) & 0x03
            if ver_bits != 1 and layer_bits != 0 and br_idx not in (0, 15) and sr_idx != 3:
                found_frame = True
                first_frame_pos = pos
                break
        pos += 1

    if not found_frame:
        raise MediaValidationError("invalid MP3 container: no valid audio frame sync found")

    total_frames = 0
    total_samples = 0
    sample_rate = 44100
    pos = first_frame_pos
    while pos + 4 <= len(data):
        if data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
            pos += 1
            continue
        ver_bits = (data[pos + 1] >> 3) & 0x03
        layer_bits = (data[pos + 1] >> 1) & 0x03
        br_idx = (data[pos + 2] >> 4) & 0x0F
        sr_idx = (data[pos + 2] >> 2) & 0x03
        padding = (data[pos + 2] >> 1) & 0x01

        if ver_bits == 1 or layer_bits == 0 or br_idx in (0, 15) or sr_idx == 3:
            pos += 1
            continue

        ver_key = 1 if ver_bits == 3 else 2
        layer_key = 4 - layer_bits
        bitrate_kbps = _MP3_BITRATES.get((ver_key, layer_key), [0] * 16)[br_idx]
        sr_list = _MP3_SAMPLERATES.get(1 if ver_bits == 3 else (2 if ver_bits == 2 else 3), [0] * 4)
        sample_rate = sr_list[sr_idx] if sr_idx < len(sr_list) else 44100

        if bitrate_kbps == 0 or sample_rate == 0:
            pos += 1
            continue

        if layer_key == 1:
            frame_len = 12 * bitrate_kbps * 1000 // sample_rate + padding * 4
            samples_per_frame = 384
        elif layer_key == 2:
            frame_len = 144 * bitrate_kbps * 1000 // sample_rate + padding
            samples_per_frame = 1152
        else:
            samples_per_frame = 1152 if ver_bits == 3 else 576
            factor = 144 if ver_bits == 3 else 72
            frame_len = factor * bitrate_kbps * 1000 // sample_rate + padding

        if frame_len <= 4:
            pos += 1
            continue

        total_frames += 1
        total_samples += samples_per_frame
        pos += frame_len

    if total_frames > 0 and sample_rate > 0:
        return total_samples / float(sample_rate)

    audio_bytes = len(data) - first_frame_pos
    return (audio_bytes * 8) / 128000.0


def _parse_ogg_duration(data: bytes) -> float:
    pos = 0
    sample_rate = 48000
    last_granule = 0
    pages_found = 0
    while pos + 27 <= len(data):
        if data[pos : pos + 4] != b"OggS":
            break
        granule = int.from_bytes(data[pos + 6 : pos + 14], "little", signed=False)
        num_segs = data[pos + 26]
        seg_table_end = pos + 27 + num_segs
        if seg_table_end > len(data):
            break
        body_len = sum(data[pos + 27 : seg_table_end])
        body_start = seg_table_end
        body_end = body_start + body_len
        if body_end > len(data):
            break

        body = data[body_start:body_end]
        if pages_found == 0:
            if b"OpusHead" in body:
                sample_rate = 48000
            elif b"\x01vorbis" in body:
                idx = body.find(b"\x01vorbis")
                if idx + 16 <= len(body):
                    sr = int.from_bytes(body[idx + 12 : idx + 16], "little")
                    if sr > 0:
                        sample_rate = sr
        if granule != 0xFFFFFFFFFFFFFFFF and granule > last_granule:
            last_granule = granule
        pages_found += 1
        pos = body_end

    if pages_found == 0:
        raise MediaValidationError("corrupt OGG container: no valid pages found")

    if last_granule > 0 and sample_rate > 0:
        return last_granule / float(sample_rate)

    return (len(data) * 8) / 64000.0


def _parse_webm_duration(data: bytes) -> float:
    duration_val: float | None = None
    timecode_scale: float = 1_000_000.0

    idx = data.find(b"\x2a\xd7\xb1")
    if idx != -1:
        scale_pos = idx + 3
        if scale_pos < len(data):
            size_byte = data[scale_pos]
            vint_len = 1
            mask = 0x80
            while vint_len <= 8 and not (size_byte & mask):
                mask >>= 1
                vint_len += 1
            if vint_len <= 8:
                val_start = scale_pos + vint_len
                val_len = (
                    (size_byte & (~mask))
                    if vint_len == 1
                    else int.from_bytes(data[scale_pos:val_start], "big")
                    & ((1 << (7 * vint_len)) - 1)
                )
                if val_start + val_len <= len(data) and val_len > 0:
                    scale = int.from_bytes(data[val_start : val_start + val_len], "big")
                    if scale > 0:
                        timecode_scale = float(scale)

    idx = data.find(b"\x44\x89")
    if idx != -1:
        dur_pos = idx + 2
        if dur_pos < len(data):
            size_byte = data[dur_pos]
            val_len = size_byte & 0x7F
            val_start = dur_pos + 1
            if val_len == 4 and val_start + 4 <= len(data):
                duration_val = struct.unpack(">f", data[val_start : val_start + 4])[0]
            elif val_len == 8 and val_start + 8 <= len(data):
                duration_val = struct.unpack(">d", data[val_start : val_start + 8])[0]

    if duration_val is not None and duration_val > 0:
        return (duration_val * timecode_scale) / 1_000_000_000.0

    return (len(data) * 8) / 64000.0


def _detect_format(data: bytes) -> str:
    """Detect audio container format strictly from magic bytes."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if (len(data) >= 3 and data[:3] == b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
    ):
        return "mp3"
    if len(data) >= 4 and data[:4] == b"OggS":
        return "ogg"
    if len(data) >= 4 and data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    raise MediaValidationError("unsupported audio container format or missing magic header")


def _normalize_format_name(fmt: str) -> str:
    cleaned = fmt.strip().lower().lstrip(".")
    if cleaned in ("wave", "x-wav"):
        return "wav"
    if cleaned in ("mpeg", "mpeg3"):
        return "mp3"
    if cleaned == "opus":
        return "ogg"
    return cleaned


def _normalize_mime_type(mime: str) -> str:
    base = mime.split(";")[0].strip().lower()
    if base in ("audio/wave", "audio/x-wav"):
        return "audio/wav"
    if base in ("audio/mp3", "audio/x-mp3"):
        return "audio/mpeg"
    if base in ("audio/opus", "audio/vorbis"):
        return "audio/ogg"
    return base


def validate_audio_bytes(
    data: bytes,
    *,
    declared_format: str | None = None,
    declared_mime: str | None = None,
) -> ValidatedMedia:
    """Validate untrusted audio bytes against container magic, bounds, and duration headers.

    Enforces ADR-0005 D54:
    - Supported containers: WAV, MP3, OGG, WebM.
    - Verified by actual content magic, NOT filename or declared MIME alone.
    - Bounded file size <= 2 MiB.
    - Bounded duration <= 15.0 seconds.
    - Declared format / MIME mismatch check.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("audio data must be bytes")

    byte_len = len(data)
    if byte_len == 0:
        raise MediaValidationError("audio payload is empty")
    if byte_len > MAX_MEDIA_BYTES:
        raise MediaValidationError(
            f"audio payload size ({byte_len} bytes) exceeds limit of {MAX_MEDIA_BYTES} bytes"
        )

    fmt = _detect_format(data)

    if declared_format is not None:
        norm_decl = _normalize_format_name(declared_format)
        if norm_decl != fmt and not (norm_decl in ("ogg", "webm") and fmt in ("ogg", "webm")):
            raise MediaValidationError(
                f"format mismatch: declared '{declared_format}' but detected container is '{fmt}'"
            )

    if declared_mime is not None:
        norm_mime = _normalize_mime_type(declared_mime)
        expected_mime = FORMAT_MIME_TYPES[fmt]
        if norm_mime != expected_mime and not (
            fmt in ("ogg", "webm") and norm_mime in ("audio/ogg", "audio/webm", "audio/opus")
        ):
            raise MediaValidationError(
                f"MIME mismatch: declared '{declared_mime}' but detected '{expected_mime}'"
            )

    match fmt:
        case "wav":
            duration = _parse_wav_duration(data)
        case "mp3":
            duration = _parse_mp3_duration(data)
        case "ogg":
            duration = _parse_ogg_duration(data)
        case "webm":
            duration = _parse_webm_duration(data)
        case _:
            raise MediaValidationError(f"unsupported format: {fmt}")

    if duration < 0.0:
        raise MediaValidationError("invalid negative duration")
    if duration > MAX_DURATION_SECONDS:
        raise MediaValidationError(
            f"audio duration ({duration:.2f}s) exceeds maximum allowed {MAX_DURATION_SECONDS}s"
        )

    digest = sha256(data).hexdigest()
    canonical_mime = FORMAT_MIME_TYPES[fmt]

    return ValidatedMedia(
        format=fmt,
        mime_type=canonical_mime,
        byte_size=byte_len,
        sha256=digest,
        duration_seconds=duration,
    )


# ---------------------------------------------------------------------------
# Human Audio Provenance and Policy Evaluation (ADR-0005 D53)
# ---------------------------------------------------------------------------


def evaluate_human_audio_policy(provenance: HumanAudioProvenance) -> bool:
    """Evaluate human-recording eligibility against maintained application media policy (D53).

    Fails closed on unknown, unsupported, conflicting, or insufficient metadata.
    """
    if provenance.media_policy_version != MEDIA_POLICY_VERSION:
        return False

    if provenance.upstream_source_site not in APPROVED_UPSTREAM_SITES:
        return False

    if not provenance.upstream_identifier.strip():
        return False

    if not provenance.media_source_ref.strip():
        return False

    if not provenance.raw_license.strip():
        return False

    policy_entry = POLICY_CLASSIFICATIONS.get(provenance.policy_classification_key)
    if policy_entry is None:
        return False

    runtime_eligible, redistribution_eligible = policy_entry

    if provenance.runtime_cache_eligible != runtime_eligible:
        return False
    if provenance.redistribution_eligible != redistribution_eligible:
        return False

    # Attribution required check for CC-BY / CC-BY-SA
    if "cc_by" in provenance.policy_classification_key:
        if not provenance.author_attribution or not provenance.author_attribution.strip():
            return False

    return provenance.runtime_cache_eligible


# ---------------------------------------------------------------------------
# Custom Pronunciation Persistence (Sacred User Data - ADR-0005 D50)
# ---------------------------------------------------------------------------


def _utc_now_iso(dt: datetime | None = None) -> str:
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def save_custom_pronunciation(
    conn: sqlite3.Connection,
    note_id: int,
    audio_bytes: bytes,
    media_dir: Path | str,
    *,
    source_type: Literal["recorded", "uploaded"] = "recorded",
    declared_format: str | None = None,
    declared_mime: str | None = None,
    now: datetime | None = None,
) -> CustomPronunciationRecord:
    """Save custom pronunciation override as sacred user data with crash-safe replacement (D50).

    Order:
    1. Validate untrusted media bytes before touching disk/DB.
    2. Write under non-active identity (fresh random filename) in media_dir and fsync.
    3. Atomically switch custom_pronunciation row in user DB transaction.
    4. Reclaim superseded media file ONLY after the metadata commit returns.
    """
    if source_type not in ("recorded", "uploaded"):
        raise CustomAudioError(f"invalid source_type: {source_type}")

    validated = validate_audio_bytes(
        audio_bytes,
        declared_format=declared_format,
        declared_mime=declared_mime,
    )

    media_directory = Path(media_dir)
    media_directory.mkdir(parents=True, exist_ok=True)

    # 2. Write to non-active identity
    random_id = uuid.uuid4().hex[:12]
    candidate_filename = f"custom_{note_id}_{random_id}.{validated.format}"
    candidate_path = media_directory / candidate_filename

    try:
        descriptor, temp_name = tempfile.mkstemp(
            dir=media_directory,
            prefix=f"temp_{note_id}_",
            suffix=f".{validated.format}",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as f:
                f.write(audio_bytes)
                f.flush()
                os.fsync(f.fileno())
            temp_path.rename(candidate_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    except Exception as exc:
        raise CustomAudioError(f"failed to write candidate media: {exc}") from exc

    # 3. Atomically switch metadata in user-DB transaction
    now_str = _utc_now_iso(now)
    old_filename: str | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT media_filename FROM custom_pronunciation WHERE note_id = ?", (note_id,)
        ).fetchone()
        if row is not None:
            old_filename = str(row[0])

        conn.execute(
            """
            INSERT INTO custom_pronunciation (
                note_id, media_filename, sha256, byte_size, format, source_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                media_filename = excluded.media_filename,
                sha256 = excluded.sha256,
                byte_size = excluded.byte_size,
                format = excluded.format,
                source_type = excluded.source_type,
                created_at = excluded.created_at
            """,
            (
                note_id,
                candidate_filename,
                validated.sha256,
                validated.byte_size,
                validated.format,
                source_type,
                now_str,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        candidate_path.unlink(missing_ok=True)
        raise

    # 4. Only after commit returns, reclaim superseded object
    if old_filename is not None and old_filename != candidate_filename:
        old_path = media_directory / old_filename
        old_path.unlink(missing_ok=True)

    return CustomPronunciationRecord(
        note_id=note_id,
        media_filename=candidate_filename,
        sha256=validated.sha256,
        byte_size=validated.byte_size,
        format=validated.format,
        source_type=source_type,
        created_at=now_str,
    )


def revert_custom_pronunciation(
    conn: sqlite3.Connection,
    note_id: int,
    media_dir: Path | str,
) -> bool:
    """Revert custom pronunciation override to automatic (D50).

    Removes override row, reclaims the media file, and leaves automatic capabilities untouched.
    """
    media_directory = Path(media_dir)
    old_filename: str | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT media_filename FROM custom_pronunciation WHERE note_id = ?", (note_id,)
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        old_filename = str(row[0])
        conn.execute("DELETE FROM custom_pronunciation WHERE note_id = ?", (note_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if old_filename is not None:
        (media_directory / old_filename).unlink(missing_ok=True)

    return True


def get_custom_pronunciation(
    conn: sqlite3.Connection,
    note_id: int,
) -> CustomPronunciationRecord | None:
    """Fetch active custom pronunciation record for a note."""
    row = conn.execute(
        """
        SELECT note_id, media_filename, sha256, byte_size, format, source_type, created_at
        FROM custom_pronunciation
        WHERE note_id = ?
        """,
        (note_id,),
    ).fetchone()
    if row is None:
        return None
    return CustomPronunciationRecord(
        note_id=int(row[0]),
        media_filename=str(row[1]),
        sha256=str(row[2]),
        byte_size=int(row[3]),
        format=str(row[4]),
        source_type=str(row[5]),
        created_at=str(row[6]),
    )


def cleanup_orphaned_custom_media(
    conn: sqlite3.Connection,
    media_dir: Path | str,
) -> list[str]:
    """Reclaim unreferenced candidate media files in media_dir left by interrupted saves."""
    media_directory = Path(media_dir)
    if not media_directory.is_dir():
        return []

    rows = conn.execute("SELECT media_filename FROM custom_pronunciation").fetchall()
    active_filenames = {str(row[0]) for row in rows}

    reclaimed: list[str] = []
    for entry in media_directory.iterdir():
        if entry.is_file() and entry.name not in active_filenames:
            entry.unlink(missing_ok=True)
            reclaimed.append(entry.name)
    return sorted(reclaimed)


# ---------------------------------------------------------------------------
# Disposable Automatic Audio Cache (ADR-0005 D50, D53, D54)
# ---------------------------------------------------------------------------


class AudioCacheManager:
    """Manages disposable runtime audio cache for human recordings and Piper synthesis (D50/D54)."""

    def __init__(self, cache_dir: Path | str) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def _file_path(self, digest: str, byte_size: int, format: str) -> Path:
        return self._cache_dir / f"{digest}_{byte_size}.{format}"

    def _provenance_path(self, digest: str, byte_size: int) -> Path:
        return self._cache_dir / f"{digest}_{byte_size}.provenance.json"

    def get(self, digest: str, byte_size: int, format: str) -> bytes | None:
        """Fetch and validate a disposable cached audio entry by immutable byte identity."""
        target = self._file_path(digest, byte_size, format)
        if not target.is_file():
            return None

        try:
            raw_bytes = target.read_bytes()
        except OSError:
            return None

        if len(raw_bytes) != byte_size:
            target.unlink(missing_ok=True)
            return None

        if sha256(raw_bytes).hexdigest() != digest:
            target.unlink(missing_ok=True)
            return None

        try:
            validate_audio_bytes(raw_bytes, declared_format=format)
        except MediaValidationError:
            target.unlink(missing_ok=True)
            return None

        return raw_bytes

    def put(
        self,
        audio_bytes: bytes,
        validated: ValidatedMedia,
        provenance: HumanAudioProvenance | None = None,
    ) -> Path:
        """Store validated audio bytes and optional provenance in the disposable cache."""
        target = self._file_path(validated.sha256, validated.byte_size, validated.format)
        target.write_bytes(audio_bytes)

        if provenance is not None:
            prov_data = {
                "media_policy_version": provenance.media_policy_version,
                "upstream_source_site": provenance.upstream_source_site,
                "upstream_identifier": provenance.upstream_identifier,
                "upstream_revision": provenance.upstream_revision,
                "media_source_ref": provenance.media_source_ref,
                "retrieval_metadata": dict(provenance.retrieval_metadata),
                "author_attribution": provenance.author_attribution,
                "raw_license": provenance.raw_license,
                "policy_classification_key": provenance.policy_classification_key,
                "runtime_cache_eligible": provenance.runtime_cache_eligible,
                "redistribution_eligible": provenance.redistribution_eligible,
            }
            self._provenance_path(validated.sha256, validated.byte_size).write_text(
                json.dumps(prov_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return target

    def get_provenance(self, digest: str, byte_size: int) -> HumanAudioProvenance | None:
        """Retrieve and policy-verify provenance metadata for a cached entry."""
        prov_file = self._provenance_path(digest, byte_size)
        if not prov_file.is_file():
            return None
        try:
            data = json.loads(prov_file.read_text(encoding="utf-8"))
            provenance = HumanAudioProvenance(
                media_policy_version=str(data["media_policy_version"]),
                upstream_source_site=str(data["upstream_source_site"]),
                upstream_identifier=str(data["upstream_identifier"]),
                upstream_revision=(
                    str(data["upstream_revision"]) if data.get("upstream_revision") else None
                ),
                media_source_ref=str(data["media_source_ref"]),
                retrieval_metadata=dict(data.get("retrieval_metadata", {})),
                author_attribution=(
                    str(data["author_attribution"]) if data.get("author_attribution") else None
                ),
                raw_license=str(data["raw_license"]),
                policy_classification_key=str(data["policy_classification_key"]),
                runtime_cache_eligible=bool(data["runtime_cache_eligible"]),
                redistribution_eligible=bool(data["redistribution_eligible"]),
            )
            if not evaluate_human_audio_policy(provenance):
                return None
            return provenance
        except Exception:
            return None

    def invalidate(self, digest: str, byte_size: int, format: str) -> None:
        """Invalidate and remove a cache entry."""
        self._file_path(digest, byte_size, format).unlink(missing_ok=True)
        self._provenance_path(digest, byte_size).unlink(missing_ok=True)

    def clear(self) -> None:
        """Clear all entries in the disposable cache without touching sacred custom media."""
        for entry in self._cache_dir.iterdir():
            if entry.is_file():
                entry.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Pronunciation Selection (ADR-0005 D48 Precedence)
# ---------------------------------------------------------------------------


def select_pronunciation_audio(
    *,
    lemma: str,
    note_id: int | None = None,
    binding_status: str = "bound",
    user_db: sqlite3.Connection | None = None,
    media_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    exact_human_id: str | None = None,
    human_resolver: Callable[[str], tuple[bytes, HumanAudioProvenance]] | None = None,
    tts_remote_url: str | None = None,
    remote_speak_client: Callable[[str, str, float], bytes] | None = None,
    piper_runner: Callable[[str, str], bytes] | None = None,
) -> AudioResolutionResult:
    """Pure domain function computing ADR-0005 D48 pronunciation audio precedence.

    Precedence order:
    1. Saved valid custom override (when note_id and valid override exists, bound note).
    2. Validated human pronunciation recording from approved exact identifier.
    3. Automatic TTS layer:
       a. Optional remote /speak (<= 1.0s timeout, silent fallback on any failure).
       b. Local Piper TTS generation / cache (pinned engine & voice).
    4. Silent None (card display/review never depend on audio).
    """
    clean_lemma = lemma.strip()

    # Step 1: Saved valid custom override
    if note_id is not None and user_db is not None and media_dir is not None:
        if binding_status == "bound":
            custom_rec = get_custom_pronunciation(user_db, note_id)
            if custom_rec is not None:
                media_path = Path(media_dir) / custom_rec.media_filename
                if media_path.is_file():
                    try:
                        raw_bytes = media_path.read_bytes()
                        if (
                            len(raw_bytes) == custom_rec.byte_size
                            and sha256(raw_bytes).hexdigest() == custom_rec.sha256
                        ):
                            validated = validate_audio_bytes(
                                raw_bytes, declared_format=custom_rec.format
                            )
                            return AudioResolutionResult(
                                source="custom",
                                audio_bytes=raw_bytes,
                                format=validated.format,
                                mime_type=validated.mime_type,
                                sha256=validated.sha256,
                                byte_size=validated.byte_size,
                                media_filename=custom_rec.media_filename,
                            )
                    except Exception:
                        pass  # Invalid custom file falls through to automatic

    cache_mgr = AudioCacheManager(cache_dir) if cache_dir is not None else None

    # Step 2: Validated human recording from approved exact identifier
    if exact_human_id is not None and exact_human_id.strip():
        # Check cache if available
        if human_resolver is not None:
            try:
                raw_bytes, provenance = human_resolver(exact_human_id.strip())
                if evaluate_human_audio_policy(provenance):
                    validated = validate_audio_bytes(raw_bytes)
                    if cache_mgr is not None:
                        cache_mgr.put(raw_bytes, validated, provenance)
                    return AudioResolutionResult(
                        source="human",
                        audio_bytes=raw_bytes,
                        format=validated.format,
                        mime_type=validated.mime_type,
                        sha256=validated.sha256,
                        byte_size=validated.byte_size,
                        provenance=provenance,
                    )
            except Exception:
                pass  # Human error/timeout/ineligibility falls through to automatic

    # Step 3: Automatic TTS Layer (Remote /speak optimization -> Local Piper)
    # 3a. Optional configured remote /speak (timeout <= 1.0s)
    if tts_remote_url is not None and remote_speak_client is not None and clean_lemma:
        try:
            remote_bytes = remote_speak_client(
                tts_remote_url, clean_lemma, REMOTE_SPEAK_TIMEOUT_MAX
            )
            validated = validate_audio_bytes(remote_bytes)
            if cache_mgr is not None:
                cache_mgr.put(remote_bytes, validated)
            return AudioResolutionResult(
                source="remote",
                audio_bytes=remote_bytes,
                format=validated.format,
                mime_type=validated.mime_type,
                sha256=validated.sha256,
                byte_size=validated.byte_size,
            )
        except Exception:
            pass  # Remote failure silently falls back to Piper

    # 3b. Local Piper TTS generation / cache
    if piper_runner is not None and clean_lemma:
        # Check cache first
        piper_cache_key = sha256(f"piper:{clean_lemma}:{PIPER_PINNED_VOICE}".encode()).hexdigest()
        if cache_mgr is not None:
            cached_index = cache_mgr.cache_dir / f"piper_{piper_cache_key}.meta"
            if cached_index.is_file():
                try:
                    digest, size_str, fmt = cached_index.read_text().split(":")
                    cached_bytes = cache_mgr.get(digest, int(size_str), fmt)
                    if cached_bytes is not None:
                        validated = validate_audio_bytes(cached_bytes, declared_format=fmt)
                        return AudioResolutionResult(
                            source="piper",
                            audio_bytes=cached_bytes,
                            format=validated.format,
                            mime_type=validated.mime_type,
                            sha256=validated.sha256,
                            byte_size=validated.byte_size,
                        )
                except Exception:
                    cached_index.unlink(missing_ok=True)

        # Generate on-demand
        try:
            piper_bytes = piper_runner(clean_lemma, PIPER_PINNED_VOICE)
            validated = validate_audio_bytes(piper_bytes)
            if cache_mgr is not None:
                cache_mgr.put(piper_bytes, validated)
                cached_index = cache_mgr.cache_dir / f"piper_{piper_cache_key}.meta"
                cached_index.write_text(
                    f"{validated.sha256}:{validated.byte_size}:{validated.format}",
                    encoding="utf-8",
                )
            return AudioResolutionResult(
                source="piper",
                audio_bytes=piper_bytes,
                format=validated.format,
                mime_type=validated.mime_type,
                sha256=validated.sha256,
                byte_size=validated.byte_size,
            )
        except Exception:
            pass  # Piper generation error falls through to None

    # Step 4: Silent None
    return AudioResolutionResult(
        source=None,
        audio_bytes=None,
        format=None,
        mime_type=None,
        sha256=None,
        byte_size=None,
    )


# ---------------------------------------------------------------------------
# Native Install Piper Voice Provisioning & Verification
# ---------------------------------------------------------------------------


def get_user_piper_dir() -> Path:
    """Return user-owned directory for Piper voice models."""
    base = os.environ.get("XDG_DATA_HOME")
    if base and base.strip():
        data_dir = Path(base).expanduser()
    else:
        data_dir = Path.home() / ".local" / "share"
    return data_dir / "wortlaut" / "piper"


def find_piper_executable() -> str | None:
    """Find piper executable in current python env or PATH."""
    import shutil
    import sys

    env_piper = Path(sys.prefix) / "bin" / "piper"
    if env_piper.is_file() and os.access(env_piper, os.X_OK):
        return str(env_piper)
    return shutil.which("piper")


def verify_piper_voice(model_path: Path) -> bool:
    """Verify that model_path is an existing file matching PIPER_PINNED_VOICE_SHA256."""
    if not model_path.is_file():
        return False
    config_path = model_path.with_name(model_path.name + ".json")
    if not config_path.is_file():
        return False
    h = sha256()
    try:
        with model_path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest().lower() == PIPER_PINNED_VOICE_SHA256.lower()
    except OSError:
        return False


def find_verified_piper_voice() -> Path | None:
    """Check standard locations for an existing, verified Piper voice model."""
    candidates: list[Path] = []
    env_path = os.environ.get("PIPER_VOICE_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(get_user_piper_dir() / f"{PIPER_PINNED_VOICE}.onnx")
    candidates.append(Path("/opt/piper") / f"{PIPER_PINNED_VOICE}.onnx")

    for cand in candidates:
        if verify_piper_voice(cand):
            return cand
    return None


def ensure_piper_voice(dest_dir: Path | None = None) -> Path:
    """Download and atomically activate the pinned Piper voice if not present/verified."""
    import urllib.request

    target_dir = dest_dir if dest_dir is not None else get_user_piper_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_model = target_dir / f"{PIPER_PINNED_VOICE}.onnx"
    target_config = target_dir / f"{PIPER_PINNED_VOICE}.onnx.json"

    if target_model.is_file() and target_config.is_file() and verify_piper_voice(target_model):
        return target_model

    model_url = f"{PIPER_VOICE_BASE_URL}/{PIPER_PINNED_VOICE}.onnx"
    config_url = f"{PIPER_VOICE_BASE_URL}/{PIPER_PINNED_VOICE}.onnx.json"

    with tempfile.TemporaryDirectory(dir=target_dir, prefix="voice-dl-") as tmp:
        tmp_path = Path(tmp)
        tmp_model = tmp_path / f"{PIPER_PINNED_VOICE}.onnx"
        tmp_config = tmp_path / f"{PIPER_PINNED_VOICE}.onnx.json"

        # Download config first
        try:
            with (
                urllib.request.urlopen(config_url, timeout=30) as resp,
                open(tmp_config, "wb") as out,
            ):
                while chunk := resp.read(65536):
                    out.write(chunk)
        except Exception as e:
            raise AudioError(f"Failed to download Piper voice configuration: {e}") from e

        # Download model
        h = sha256()
        try:
            with (
                urllib.request.urlopen(model_url, timeout=120) as resp,
                open(tmp_model, "wb") as out,
            ):
                while chunk := resp.read(65536):
                    out.write(chunk)
                    h.update(chunk)
        except Exception as e:
            raise AudioError(f"Failed to download Piper voice model: {e}") from e

        digest = h.hexdigest().lower()
        if digest != PIPER_PINNED_VOICE_SHA256.lower():
            raise AudioError(
                f"Piper voice model SHA-256 mismatch: "
                f"expected {PIPER_PINNED_VOICE_SHA256}, got {digest}"
            )

        # Atomically replace
        os.replace(tmp_config, target_config)
        os.replace(tmp_model, target_model)

    return target_model
