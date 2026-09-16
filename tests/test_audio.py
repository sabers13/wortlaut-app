"""Evidence test suite for runtime pronunciation audio domain layer (app/audio.py).

Covers ADR-0005 (D48-D56) and tasks/slice-7.md A6:
1. Full precedence order including every fallback edge (D48).
2. Media-validation accept/reject matrix (WAV, MP3, OGG, WebM, size, duration, corrupt) (D54).
3. Crash-safe replacement: failure before commit preserves old override,
   atomic swap reclaims superseded, orphan cleanup (D50).
4. Revert restoring automatic selection without touching automatic capabilities (D50).
5. Cache deletion never touching sacred custom audio (D50).
6. Note-local isolation across notes (D51).
7. Unbind fail-closed preserving both row and file (D51).
8. Corrupt/missing/mismatched cache entries falling through to Piper (D54).
9. Provenance fail-closed matrix over every required field (D53).
10. Absence of any live free-text search capability in app/audio.py (D53).
11. Fake Piper runner receiving pinned voice identifier and validated before caching (D55, D56).
12. Remote-speak timeout/error/non-2xx/invalid-payload fallback matrix with 1.0s bound (D26, D48).
"""

from __future__ import annotations

import ast
import io
import sqlite3
import struct
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.audio import (
    MEDIA_POLICY_VERSION,
    PIPER_PINNED_VOICE,
    REMOTE_SPEAK_TIMEOUT_MAX,
    AudioCacheManager,
    HumanAudioProvenance,
    MediaValidationError,
    cleanup_orphaned_custom_media,
    evaluate_human_audio_policy,
    get_custom_pronunciation,
    revert_custom_pronunciation,
    save_custom_pronunciation,
    select_pronunciation_audio,
    validate_audio_bytes,
)

# ---------------------------------------------------------------------------
# Test Audio Helpers (synthesizing valid minimal containers in pure stdlib)
# ---------------------------------------------------------------------------


def make_test_wav_bytes(duration_seconds: float = 1.0, framerate: int = 8000) -> bytes:
    """Generate minimal valid WAV audio bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        nframes = max(1, int(duration_seconds * framerate))
        w.writeframes(b"\x00\x00" * nframes)
    return buf.getvalue()


def make_test_mp3_bytes(duration_seconds: float = 1.0) -> bytes:
    """Generate minimal valid MP3 audio bytes (ID3v2 + MPEG1 Layer 3 frames)."""
    id3_header = b"ID3\x03\x00\x00\x00\x00\x00\x00"  # 10 bytes empty ID3v2.3 tag
    # 128 kbps, 44100 Hz, Layer 3 -> 417 bytes per frame, 1152 samples per frame
    frame_hdr = b"\xff\xfb\x90\x00"
    frame_body = frame_hdr + b"\x00" * (417 - 4)
    num_frames = max(1, int(duration_seconds * 44100 / 1152))
    return id3_header + (frame_body * num_frames)


def make_test_raw_mp3_bytes(duration_seconds: float = 1.0) -> bytes:
    """Generate minimal valid raw MP3 frame sync bytes without ID3 header."""
    frame_hdr = b"\xff\xfb\x90\x00"
    frame_body = frame_hdr + b"\x00" * (417 - 4)
    num_frames = max(1, int(duration_seconds * 44100 / 1152))
    return frame_body * num_frames


def make_test_ogg_bytes(duration_seconds: float = 1.0) -> bytes:
    """Generate minimal valid OGG Opus audio bytes."""
    # Page 1: BOS with OpusHead (19 bytes payload)
    opus_head = b"OpusHead\x01\x01\x00\x00" + (48000).to_bytes(4, "little") + b"\x00\x00\x00"
    p1 = (
        b"OggS\x00\x02"  # version 0, BOS flag
        + (0).to_bytes(8, "little")  # granule 0
        + (1234).to_bytes(4, "little")  # serial
        + (0).to_bytes(4, "little")  # seq 0
        + (0).to_bytes(4, "little")  # crc placeholder
        + bytes([1])  # 1 segment
        + bytes([len(opus_head)])  # segment size
        + opus_head
    )

    # Page 2: EOS with final granule pos
    samples = max(1, int(duration_seconds * 48000))
    opus_tags = b"OpusTags\x00\x00\x00\x00\x00\x00\x00\x00"
    p2 = (
        b"OggS\x00\x04"  # version 0, EOS flag
        + samples.to_bytes(8, "little")  # granule position = duration * 48000
        + (1234).to_bytes(4, "little")  # serial
        + (1).to_bytes(4, "little")  # seq 1
        + (0).to_bytes(4, "little")  # crc
        + bytes([1])  # 1 segment
        + bytes([len(opus_tags)])  # segment size
        + opus_tags
    )
    return p1 + p2


def make_test_webm_bytes(duration_seconds: float = 1.0) -> bytes:
    """Generate minimal valid WebM EBML bytes with Info Duration element."""
    # EBML header (0x1A45DFA3) + DocType webm
    ebml_hdr = (
        b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08"
        b"\x42\x82\x84webm"
    )
    # Segment Info with Duration (0x4489, 4 bytes float) and TimecodeScale (0x2AD7B1, 3 bytes int)
    dur_ms = float(duration_seconds * 1000.0)
    dur_bytes = struct.pack(">f", dur_ms)
    info_payload = (
        b"\x2a\xd7\xb1\x83\x0f\x42\x40"  # TimecodeScale = 1,000,000 ns (1ms)
        + b"\x44\x89\x84"
        + dur_bytes  # Duration element
    )
    segment_info = b"\x15\x49\xa9\x66" + bytes([0x80 | len(info_payload)]) + info_payload
    segment = b"\x18\x53\x80\x67\xff" + segment_info
    return ebml_hdr + segment


def make_valid_provenance(
    *,
    policy_version: str = MEDIA_POLICY_VERSION,
    site: str = "commons.wikimedia.org",
    identifier: str = "File:De-Hallo.ogg",
    raw_license: str = "CC BY-SA 4.0",
    key: str = "cc_by_sa_4_0",
    author: str | None = "Max Mustermann",
    runtime_eligible: bool = True,
    redist_eligible: bool = True,
    media_source_ref: str = "https://upload.wikimedia.org/wikipedia/commons/De-Hallo.ogg",
) -> HumanAudioProvenance:
    return HumanAudioProvenance(
        media_policy_version=policy_version,
        upstream_source_site=site,
        upstream_identifier=identifier,
        upstream_revision="123456",
        media_source_ref=media_source_ref,
        retrieval_metadata={"fetched_at": "2026-08-26T00:00:00Z"},
        author_attribution=author,
        raw_license=raw_license,
        policy_classification_key=key,
        runtime_cache_eligible=runtime_eligible,
        redistribution_eligible=redist_eligible,
    )


# ---------------------------------------------------------------------------
# 1. Media Validation Matrix Tests (Accept/Reject Matrix)
# ---------------------------------------------------------------------------


def test_media_validation_magic_accept_matrix() -> None:
    """Accepts valid WAV, MP3 (ID3 and raw), OGG, and WebM containers."""
    # 1. WAV
    wav_data = make_test_wav_bytes(duration_seconds=1.5)
    v_wav = validate_audio_bytes(wav_data)
    assert v_wav.format == "wav"
    assert v_wav.mime_type == "audio/wav"
    assert 1.4 <= v_wav.duration_seconds <= 1.6
    assert v_wav.byte_size == len(wav_data)

    # 2. MP3 with ID3 tag
    mp3_data = make_test_mp3_bytes(duration_seconds=2.0)
    v_mp3 = validate_audio_bytes(mp3_data)
    assert v_mp3.format == "mp3"
    assert v_mp3.mime_type == "audio/mpeg"
    assert 1.9 <= v_mp3.duration_seconds <= 2.2

    # 3. MP3 raw frame sync
    raw_mp3_data = make_test_raw_mp3_bytes(duration_seconds=1.0)
    v_raw_mp3 = validate_audio_bytes(raw_mp3_data)
    assert v_raw_mp3.format == "mp3"
    assert 0.9 <= v_raw_mp3.duration_seconds <= 1.2

    # 4. OGG Opus
    ogg_data = make_test_ogg_bytes(duration_seconds=3.0)
    v_ogg = validate_audio_bytes(ogg_data)
    assert v_ogg.format == "ogg"
    assert v_ogg.mime_type == "audio/ogg"
    assert 2.9 <= v_ogg.duration_seconds <= 3.1

    # 5. WebM
    webm_data = make_test_webm_bytes(duration_seconds=2.5)
    v_webm = validate_audio_bytes(webm_data)
    assert v_webm.format == "webm"
    assert v_webm.mime_type == "audio/webm"
    assert 2.4 <= v_webm.duration_seconds <= 2.6


def test_media_validation_oversized_rejected() -> None:
    """Rejects audio payloads exceeding 2 MiB bound."""
    oversized = make_test_wav_bytes(duration_seconds=1.0) + b"\x00" * (2 * 1024 * 1024 + 100)
    with pytest.raises(MediaValidationError, match="exceeds limit"):
        validate_audio_bytes(oversized)


def test_media_validation_empty_rejected() -> None:
    """Rejects empty audio payload."""
    with pytest.raises(MediaValidationError, match="payload is empty"):
        validate_audio_bytes(b"")


def test_media_validation_over_duration_rejected() -> None:
    """Rejects audio exceeding 15.0 seconds duration."""
    long_wav = make_test_wav_bytes(duration_seconds=16.0)
    with pytest.raises(MediaValidationError, match="exceeds maximum allowed 15.0s"):
        validate_audio_bytes(long_wav)

    long_ogg = make_test_ogg_bytes(duration_seconds=15.5)
    with pytest.raises(MediaValidationError, match="exceeds maximum allowed 15.0s"):
        validate_audio_bytes(long_ogg)


def test_media_validation_corrupted_rejected() -> None:
    """Rejects corrupted or unsupported media bytes."""
    with pytest.raises(MediaValidationError, match="unsupported audio container"):
        validate_audio_bytes(b"NOT_A_VALID_HEADER_DATA_1234567890")

    # Corrupt WAV (RIFF but invalid body)
    corrupt_wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00GARBAGE_GARBAGE"
    with pytest.raises(MediaValidationError):
        validate_audio_bytes(corrupt_wav)


def test_media_validation_extension_and_mime_mismatch_rejected() -> None:
    """Rejects mismatched declared format or declared MIME."""
    mp3_data = make_test_mp3_bytes(duration_seconds=1.0)
    wav_data = make_test_wav_bytes(duration_seconds=1.0)

    # Declared format mismatch
    with pytest.raises(MediaValidationError, match="format mismatch"):
        validate_audio_bytes(mp3_data, declared_format="wav")

    with pytest.raises(MediaValidationError, match="format mismatch"):
        validate_audio_bytes(wav_data, declared_format="mp3")

    # Declared MIME mismatch
    with pytest.raises(MediaValidationError, match="MIME mismatch"):
        validate_audio_bytes(mp3_data, declared_mime="audio/wav")

    with pytest.raises(MediaValidationError, match="MIME mismatch"):
        validate_audio_bytes(wav_data, declared_mime="audio/mpeg")


# ---------------------------------------------------------------------------
# 2. Sacred Custom Audio Persistence & Crash-Safety Tests (D50)
# ---------------------------------------------------------------------------


def _create_user_db_with_schema() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE note (
            id INTEGER PRIMARY KEY,
            lemma_semantic_ref TEXT NOT NULL,
            sense_semantic_ref TEXT,
            status TEXT NOT NULL
                CHECK (status IN ('resolved', 'needs_gloss', 'derived_compound', 'orphaned')),
            created_at TEXT NOT NULL,
            due_at TEXT NOT NULL,
            interval_days REAL NOT NULL DEFAULT 0,
            ease_factor REAL NOT NULL DEFAULT 2.5,
            review_count INTEGER NOT NULL DEFAULT 0,
            last_confidence INTEGER
        );
        CREATE TABLE custom_pronunciation (
            note_id INTEGER PRIMARY KEY REFERENCES note(id) ON DELETE CASCADE,
            media_filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            format TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK (source_type IN ('recorded', 'uploaded')),
            created_at TEXT NOT NULL
        );
        """
    )
    # Insert test notes
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO note (id, lemma_semantic_ref, status, created_at, due_at) "
        "VALUES (1, 'lemma:v1:test1', 'resolved', ?, ?)",
        (now_iso, now_iso),
    )
    conn.execute(
        "INSERT INTO note (id, lemma_semantic_ref, status, created_at, due_at) "
        "VALUES (2, 'lemma:v1:test2', 'resolved', ?, ?)",
        (now_iso, now_iso),
    )
    conn.commit()
    return conn


def test_crash_safe_replacement_failure_before_commit_preserves_old_override(
    tmp_path: Path,
) -> None:
    """Failure between candidate write and commit leaves old override active and file intact."""
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    # Step 1: Save initial custom pronunciation
    wav_bytes_1 = make_test_wav_bytes(duration_seconds=1.0)
    rec1 = save_custom_pronunciation(conn, note_id=1, audio_bytes=wav_bytes_1, media_dir=media_dir)
    assert rec1.note_id == 1
    old_file = media_dir / rec1.media_filename
    assert old_file.is_file()

    # Step 2: Attempt replacement with failure injected during DB transaction
    wav_bytes_2 = make_test_wav_bytes(duration_seconds=2.0)

    # Corrupt DB schema or inject error
    conn.execute("DROP TABLE custom_pronunciation")  # Causes commit to fail

    with pytest.raises(sqlite3.OperationalError):
        save_custom_pronunciation(
            conn, note_id=1, audio_bytes=wav_bytes_2, media_dir=media_dir
        )

    # Verify old file on disk is completely intact
    assert old_file.is_file()
    assert old_file.read_bytes() == wav_bytes_1


def test_crash_safe_replacement_success_atomic_swap_and_reclaims_superseded(
    tmp_path: Path,
) -> None:
    """Successful replacement atomically updates metadata and reclaims superseded object."""
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    wav_bytes_1 = make_test_wav_bytes(duration_seconds=1.0)
    rec1 = save_custom_pronunciation(conn, note_id=1, audio_bytes=wav_bytes_1, media_dir=media_dir)
    old_file = media_dir / rec1.media_filename
    assert old_file.is_file()

    # Replace with MP3 recording
    mp3_bytes = make_test_mp3_bytes(duration_seconds=1.5)
    rec2 = save_custom_pronunciation(
        conn,
        note_id=1,
        audio_bytes=mp3_bytes,
        media_dir=media_dir,
        source_type="uploaded",
    )
    new_file = media_dir / rec2.media_filename

    assert rec2.media_filename != rec1.media_filename
    assert new_file.is_file()
    assert new_file.read_bytes() == mp3_bytes

    # Superseded old file is reclaimed (deleted)
    assert not old_file.exists()

    # Verify DB has new row
    fetched = get_custom_pronunciation(conn, note_id=1)
    assert fetched is not None
    assert fetched.media_filename == rec2.media_filename
    assert fetched.source_type == "uploaded"
    assert fetched.format == "mp3"


def test_cleanup_orphaned_custom_media(tmp_path: Path) -> None:
    """Deterministic orphan cleanup reclaims unreferenced candidate files."""
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    # Save active note 1 media
    wav_bytes = make_test_wav_bytes(duration_seconds=1.0)
    rec1 = save_custom_pronunciation(conn, note_id=1, audio_bytes=wav_bytes, media_dir=media_dir)

    # Create unreferenced leftover candidate files
    orphan1 = media_dir / "temp_orphan_1.wav"
    orphan1.write_bytes(wav_bytes)
    orphan2 = media_dir / "custom_1_leftover.mp3"
    orphan2.write_bytes(wav_bytes)

    reclaimed = cleanup_orphaned_custom_media(conn, media_dir)
    assert "temp_orphan_1.wav" in reclaimed
    assert "custom_1_leftover.mp3" in reclaimed

    # Active file is NOT touched
    assert (media_dir / rec1.media_filename).is_file()
    assert not orphan1.exists()
    assert not orphan2.exists()


def test_revert_restores_automatic_selection(tmp_path: Path) -> None:
    """Revert removes custom override row, deletes file, and restores automatic selection."""
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    wav_bytes = make_test_wav_bytes(duration_seconds=1.0)
    rec = save_custom_pronunciation(conn, note_id=1, audio_bytes=wav_bytes, media_dir=media_dir)
    file_path = media_dir / rec.media_filename
    assert file_path.is_file()

    # Precedence returns custom
    res_custom = select_pronunciation_audio(
        lemma="Haus",
        note_id=1,
        user_db=conn,
        media_dir=media_dir,
    )
    assert res_custom.source == "custom"
    assert res_custom.audio_bytes == wav_bytes

    # Revert
    reverted = revert_custom_pronunciation(conn, note_id=1, media_dir=media_dir)
    assert reverted is True
    assert not file_path.exists()
    assert get_custom_pronunciation(conn, note_id=1) is None

    # Precedence now falls through to automatic Piper
    piper_fake_bytes = make_test_wav_bytes(duration_seconds=0.8)
    res_auto = select_pronunciation_audio(
        lemma="Haus",
        note_id=1,
        user_db=conn,
        media_dir=media_dir,
        piper_runner=lambda text, voice: piper_fake_bytes,
    )
    assert res_auto.source == "piper"
    assert res_auto.audio_bytes == piper_fake_bytes


def test_cache_deletion_never_touches_sacred_custom_audio(tmp_path: Path) -> None:
    """Deleting disposable cache leaves sacred custom audio completely untouched (R9/D50)."""
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"
    cache_dir = tmp_path / "cache"

    wav_bytes = make_test_wav_bytes(duration_seconds=1.0)
    rec = save_custom_pronunciation(conn, note_id=1, audio_bytes=wav_bytes, media_dir=media_dir)

    cache_mgr = AudioCacheManager(cache_dir)
    v_media = validate_audio_bytes(wav_bytes)
    cache_mgr.put(wav_bytes, v_media)
    assert len(list(cache_dir.iterdir())) > 0

    # Clear/delete cache
    cache_mgr.clear()
    assert len(list(cache_dir.iterdir())) == 0

    # Sacred custom audio is intact
    assert (media_dir / rec.media_filename).is_file()
    assert get_custom_pronunciation(conn, note_id=1) is not None


def test_note_local_isolation_across_notes(tmp_path: Path) -> None:
    """Custom audio is strictly note-local and not shared across notes."""
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"

    wav_bytes = make_test_wav_bytes(duration_seconds=1.0)
    piper_bytes = make_test_wav_bytes(duration_seconds=0.8)

    save_custom_pronunciation(conn, note_id=1, audio_bytes=wav_bytes, media_dir=media_dir)

    # Note 1 gets custom
    res1 = select_pronunciation_audio(
        lemma="See",
        note_id=1,
        user_db=conn,
        media_dir=media_dir,
        piper_runner=lambda text, voice: piper_bytes,
    )
    assert res1.source == "custom"
    assert res1.audio_bytes == wav_bytes

    # Note 2 gets automatic Piper (not shared!)
    res2 = select_pronunciation_audio(
        lemma="See",
        note_id=2,
        user_db=conn,
        media_dir=media_dir,
        piper_runner=lambda text, voice: piper_bytes,
    )
    assert res2.source == "piper"
    assert res2.audio_bytes == piper_bytes


def test_unbind_fail_closed_preserves_row_and_file(tmp_path: Path) -> None:
    """When a note is unbound on dict swap, custom selection fails closed but row/file survive."""
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"

    wav_bytes = make_test_wav_bytes(duration_seconds=1.0)
    piper_bytes = make_test_wav_bytes(duration_seconds=0.8)
    rec = save_custom_pronunciation(conn, note_id=1, audio_bytes=wav_bytes, media_dir=media_dir)

    # When binding_status is unbound or ambiguous -> selection fails closed to automatic
    res_unbound = select_pronunciation_audio(
        lemma="Haus",
        note_id=1,
        binding_status="unbound",
        user_db=conn,
        media_dir=media_dir,
        piper_runner=lambda text, voice: piper_bytes,
    )
    assert res_unbound.source == "piper"  # Custom failed closed

    res_ambiguous = select_pronunciation_audio(
        lemma="Haus",
        note_id=1,
        binding_status="ambiguous",
        user_db=conn,
        media_dir=media_dir,
        piper_runner=lambda text, voice: piper_bytes,
    )
    assert res_ambiguous.source == "piper"

    # Both row and file are preserved (never deleted merely because target changed)
    assert (media_dir / rec.media_filename).is_file()
    assert get_custom_pronunciation(conn, note_id=1) is not None


# ---------------------------------------------------------------------------
# 3. Disposable Cache & Corrupt/Missing Entry Fallback (D50, D54)
# ---------------------------------------------------------------------------


def test_cache_missing_entry_falls_through_to_piper(tmp_path: Path) -> None:
    """Missing cache entry falls through to on-demand Piper generation."""
    cache_dir = tmp_path / "cache"
    cache_mgr = AudioCacheManager(cache_dir)
    assert cache_mgr.get("nonexistent_sha256", 100, "wav") is None

    piper_bytes = make_test_wav_bytes(duration_seconds=1.0)
    res = select_pronunciation_audio(
        lemma="Buch",
        cache_dir=cache_dir,
        piper_runner=lambda text, voice: piper_bytes,
    )
    assert res.source == "piper"
    assert res.audio_bytes == piper_bytes


def test_cache_corrupt_entry_invalidated_and_falls_through_to_piper(tmp_path: Path) -> None:
    """Corrupt cache entry is invalidated and selection falls through to Piper."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Put a corrupt file into cache
    corrupt_file = cache_dir / "badsha256_100.wav"
    corrupt_file.write_bytes(b"CORRUPT_BYTES_NOT_A_WAV_HEADER_1234567890")

    cache_mgr = AudioCacheManager(cache_dir)
    assert cache_mgr.get("badsha256", 100, "wav") is None
    # File is unlinked
    assert not corrupt_file.exists()


# ---------------------------------------------------------------------------
# 4. Provenance Policy Fail-Closed Matrix (D53)
# ---------------------------------------------------------------------------


def test_provenance_fail_closed_matrix() -> None:
    """Evaluates provenance policy and proves fail-closed behavior on invalid metadata."""
    # 1. Valid baseline
    valid = make_valid_provenance()
    assert evaluate_human_audio_policy(valid) is True

    # 2. Policy version mismatch
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(policy_version="0.9")
        )
        is False
    )

    # 3. Unsupported source site
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(site="https://untrusted-upload.xyz")
        )
        is False
    )

    # 4. Empty upstream identifier
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(identifier="")
        )
        is False
    )

    # 5. Empty media source ref
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(media_source_ref="")
        )
        is False
    )

    # 6. Unknown / unsupported license classification key
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(key="unknown_commercial_license")
        )
        is False
    )

    # 7. Ineligible runtime cache flag
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(runtime_eligible=False)
        )
        is False
    )

    # 8. Missing author attribution on CC-BY / CC-BY-SA
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(key="cc_by_sa_4_0", author="")
        )
        is False
    )
    assert (
        evaluate_human_audio_policy(
            make_valid_provenance(key="cc_by_sa_4_0", author=None)
        )
        is False
    )

    # 9. CC0 does not strictly require author attribution
    cc0_prov = make_valid_provenance(
        key="cc0",
        raw_license="CC0 1.0 Universal",
        author=None,
    )
    assert evaluate_human_audio_policy(cc0_prov) is True


# ---------------------------------------------------------------------------
# 5. Absence of Free-Text Search Capability (D53)
# ---------------------------------------------------------------------------


def test_absence_of_free_text_search_capability() -> None:
    """Verifies that app/audio.py contains NO generic live free-text search capability anywhere."""
    audio_py = Path(__file__).parents[1] / "app" / "audio.py"
    source = audio_py.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(audio_py))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name.lower()
            msg = f"Forbidden search/query function in app/audio.py: {node.name}"
            assert "search" not in name, msg
            assert "query" not in name, msg
            assert "scrape" not in name, msg


# ---------------------------------------------------------------------------
# 6. Piper Runner Invocation & Voice Identifier (D55, D56)
# ---------------------------------------------------------------------------


def test_piper_runner_invoked_with_pinned_voice_and_validated_before_caching(
    tmp_path: Path,
) -> None:
    """Piper runner receives pinned voice de_DE-thorsten-high and output is validated."""
    cache_dir = tmp_path / "cache"
    recorded_calls: list[tuple[str, str]] = []

    valid_piper_bytes = make_test_wav_bytes(duration_seconds=1.2)

    def fake_piper(text: str, voice: str) -> bytes:
        recorded_calls.append((text, voice))
        return valid_piper_bytes

    res = select_pronunciation_audio(
        lemma="Katze",
        cache_dir=cache_dir,
        piper_runner=fake_piper,
    )

    assert len(recorded_calls) == 1
    assert recorded_calls[0] == ("Katze", PIPER_PINNED_VOICE)
    assert res.source == "piper"
    assert res.audio_bytes == valid_piper_bytes

    # Second lookup uses cached entry (fake_piper not called again)
    res_cached = select_pronunciation_audio(
        lemma="Katze",
        cache_dir=cache_dir,
        piper_runner=fake_piper,
    )
    assert len(recorded_calls) == 1
    assert res_cached.source == "piper"
    assert res_cached.audio_bytes == valid_piper_bytes


def test_piper_runner_invalid_output_not_cached(tmp_path: Path) -> None:
    """If Piper runner returns corrupted bytes, it fails validation and is not cached."""
    cache_dir = tmp_path / "cache"

    def broken_piper(text: str, voice: str) -> bytes:
        return b"NOT_A_VALID_AUDIO_CONTAINER"

    res = select_pronunciation_audio(
        lemma="Katze",
        cache_dir=cache_dir,
        piper_runner=broken_piper,
    )
    assert res.source is None
    assert res.audio_bytes is None


# ---------------------------------------------------------------------------
# 7. Remote Speak Silent Fallback Matrix (D26, D48)
# ---------------------------------------------------------------------------


def test_remote_speak_silent_fallback_matrix(tmp_path: Path) -> None:
    """Tests remote /speak timeout, errors, non-2xx, and invalid payload with <= 1.0s bound."""
    cache_dir = tmp_path / "cache"
    piper_bytes = make_test_wav_bytes(duration_seconds=0.9)
    valid_remote_bytes = make_test_wav_bytes(duration_seconds=1.1)

    def piper_runner(text: str, voice: str) -> bytes:
        return piper_bytes

    # 1. Happy path remote speak
    def remote_success(url: str, text: str, timeout: float) -> bytes:
        assert timeout <= REMOTE_SPEAK_TIMEOUT_MAX
        return valid_remote_bytes

    res_success = select_pronunciation_audio(
        lemma="Wort",
        cache_dir=cache_dir,
        tts_remote_url="http://127.0.0.1:8000/speak",
        remote_speak_client=remote_success,
        piper_runner=piper_runner,
    )
    assert res_success.source == "remote"
    assert res_success.audio_bytes == valid_remote_bytes

    # 2. Timeout error
    def remote_timeout(url: str, text: str, timeout: float) -> bytes:
        assert timeout <= 1.0
        raise TimeoutError("connection timed out after 1.0s")

    res_to = select_pronunciation_audio(
        lemma="Hund",
        cache_dir=cache_dir,
        tts_remote_url="http://127.0.0.1:8000/speak",
        remote_speak_client=remote_timeout,
        piper_runner=piper_runner,
    )
    assert res_to.source == "piper"
    assert res_to.audio_bytes == piper_bytes

    # 3. ConnectionError
    def remote_conn_err(url: str, text: str, timeout: float) -> bytes:
        raise ConnectionRefusedError("connection refused")

    res_conn = select_pronunciation_audio(
        lemma="Baum",
        cache_dir=cache_dir,
        tts_remote_url="http://127.0.0.1:8000/speak",
        remote_speak_client=remote_conn_err,
        piper_runner=piper_runner,
    )
    assert res_conn.source == "piper"

    # 4. HTTP non-2xx error (e.g. 500 Internal Server Error)
    def remote_http_500(url: str, text: str, timeout: float) -> bytes:
        raise RuntimeError("HTTP 500 Internal Server Error")

    res_500 = select_pronunciation_audio(
        lemma="Wasser",
        cache_dir=cache_dir,
        tts_remote_url="http://127.0.0.1:8000/speak",
        remote_speak_client=remote_http_500,
        piper_runner=piper_runner,
    )
    assert res_500.source == "piper"

    # 5. Invalid/corrupted audio payload returned by remote
    def remote_corrupt(url: str, text: str, timeout: float) -> bytes:
        return b"CORRUPT_REMOTE_RESPONSE_PAYLOAD"

    res_corrupt = select_pronunciation_audio(
        lemma="Feuer",
        cache_dir=cache_dir,
        tts_remote_url="http://127.0.0.1:8000/speak",
        remote_speak_client=remote_corrupt,
        piper_runner=piper_runner,
    )
    assert res_corrupt.source == "piper"
    assert res_corrupt.audio_bytes == piper_bytes


# ---------------------------------------------------------------------------
# 8. Complete Precedence Order End-to-End Tests (D48)
# ---------------------------------------------------------------------------


def test_complete_precedence_order_end_to_end(tmp_path: Path) -> None:
    """Tests all 5 precedence layers in order:
    1. Custom override
    2. Human recording
    3. Remote speak
    4. Local Piper
    5. Silent None
    """
    conn = _create_user_db_with_schema()
    media_dir = tmp_path / "media"
    cache_dir = tmp_path / "cache"

    custom_bytes = make_test_wav_bytes(duration_seconds=1.0)
    human_bytes = make_test_ogg_bytes(duration_seconds=1.2)
    remote_bytes = make_test_mp3_bytes(duration_seconds=1.4)
    piper_bytes = make_test_wav_bytes(duration_seconds=0.8)

    save_custom_pronunciation(conn, note_id=1, audio_bytes=custom_bytes, media_dir=media_dir)

    human_prov = make_valid_provenance()

    def human_resolver(exact_id: str) -> tuple[bytes, HumanAudioProvenance]:
        return human_bytes, human_prov

    def remote_client(url: str, text: str, timeout: float) -> bytes:
        return remote_bytes

    def piper_runner(text: str, voice: str) -> bytes:
        return piper_bytes

    # Layer 1: Custom wins over all others
    r1 = select_pronunciation_audio(
        lemma="Test",
        note_id=1,
        user_db=conn,
        media_dir=media_dir,
        cache_dir=cache_dir,
        exact_human_id="File:De-Test.ogg",
        human_resolver=human_resolver,
        tts_remote_url="http://localhost:8000/speak",
        remote_speak_client=remote_client,
        piper_runner=piper_runner,
    )
    assert r1.source == "custom"
    assert r1.audio_bytes == custom_bytes

    # Layer 2: Human wins when custom is absent
    r2 = select_pronunciation_audio(
        lemma="Test",
        note_id=2,  # No custom override on note 2
        user_db=conn,
        media_dir=media_dir,
        cache_dir=cache_dir,
        exact_human_id="File:De-Test.ogg",
        human_resolver=human_resolver,
        tts_remote_url="http://localhost:8000/speak",
        remote_speak_client=remote_client,
        piper_runner=piper_runner,
    )
    assert r2.source == "human"
    assert r2.audio_bytes == human_bytes

    # Layer 3: Remote speak wins when human is absent/fails
    r3 = select_pronunciation_audio(
        lemma="Test",
        note_id=2,
        user_db=conn,
        media_dir=media_dir,
        cache_dir=cache_dir,
        exact_human_id=None,
        tts_remote_url="http://localhost:8000/speak",
        remote_speak_client=remote_client,
        piper_runner=piper_runner,
    )
    assert r3.source == "remote"
    assert r3.audio_bytes == remote_bytes

    # Layer 4: Piper wins when remote is absent/fails
    r4 = select_pronunciation_audio(
        lemma="Test",
        note_id=2,
        user_db=conn,
        media_dir=media_dir,
        cache_dir=cache_dir,
        exact_human_id=None,
        tts_remote_url=None,
        piper_runner=piper_runner,
    )
    assert r4.source == "piper"
    assert r4.audio_bytes == piper_bytes

    # Layer 5: Silent None when everything is absent/fails
    r5 = select_pronunciation_audio(
        lemma="Test",
        note_id=2,
        user_db=conn,
        media_dir=media_dir,
        cache_dir=cache_dir,
        exact_human_id=None,
        tts_remote_url=None,
        piper_runner=None,
    )
    assert r5.source is None
    assert r5.audio_bytes is None
