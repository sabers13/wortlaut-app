"""Dictionary release installer: manifest, verification, atomic activation.

The dictionary is a read-only distributable asset (AGENTS R9). This
module owns the boundary that turns an unverified file into an active
asset:

* Manifest parsing — a small JSON document that names the dictionary
  version, file size, SHA-256, classification, and download URL.
* Verification, in two deliberately separate tiers:
    - ``verify_dictionary_identity`` — release identity only (exact byte
      size, streaming SHA-256). No SQLite is opened. This is the tier
      canonical launcher startup uses before any user data is touched.
    - ``verify_dictionary_bytes`` — identity plus ``PRAGMA quick_check``
      and the full PART-A schema validation performed by
      ``app.dictionary.validate_candidate_dictionary``. This is the
      installer's full-verification tier; ``DictionaryRuntime``
      performs the equivalent full validation again at activation, so
      the ~945 MB asset is never validated twice in the same startup.
* Atomic install — every successful install ends in a rename of a
  fully-verified temp file onto the active slot. Failed downloads leave
  no partial artifact on disk; a verified existing dictionary is
  reused without ever being overwritten (ADR-0001 §12).

No provider credentials, tokens, or embedded API keys are accepted by
this module. The manifest's ``download_url`` field is host-agnostic and
may point at a GitHub Release asset, a public artifact mirror, or a
local filesystem location; the same verification path applies to all
of them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

# Maximum dictionary bytes we'll accept.  The current source-backed
# Stage-02 candidate is ~900 MB; a generous 2 GiB ceiling lets a future
# enriched dictionary install without revisiting this module while
# still rejecting anything obviously malformed (4 GiB SQLite files are
# not portable).
_MAX_DICTIONARY_BYTES: Final[int] = 2 * 1024 * 1024 * 1024

# Default chunk size for streaming verification reads.
_CHUNK_BYTES: Final[int] = 1024 * 1024


class DictionaryInstallerError(ValueError):
    """Raised when dictionary install/verification cannot proceed safely."""


@dataclass(frozen=True)
class DictionaryManifest:
    """Validated release manifest for a single dictionary version.

    The manifest is intentionally minimal: only fields the installer
    needs to make a fail-closed decision. Anything else belongs in the
    release ``ATTRIBUTION`` / ``LICENSE`` files beside the dictionary.
    """

    version: str
    filename: str
    sha256: str
    bytes: int
    classification: str
    attribution: str
    download_url: str | None
    manifest_path: Path

    @property
    def expected_sha256(self) -> str:
        """Alias retained for downstream readability."""
        return self.sha256

    @property
    def expected_bytes(self) -> int:
        """Alias retained for downstream readability."""
        return self.bytes


_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")
_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def load_manifest(path: Path | str) -> DictionaryManifest:
    """Parse and validate one release manifest file.

    Required fields:
        version, filename, sha256, bytes, classification, attribution.
    Optional:
        download_url (must be an absolute http(s) URL or ``file://`` URI
        when present; an empty string is treated as missing).
    """
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file():
        raise DictionaryInstallerError(f"manifest not found: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DictionaryInstallerError(
            f"manifest cannot be read as JSON: {manifest_path}"
        ) from exc
    return parse_manifest_payload(payload, manifest_path=manifest_path)


def parse_manifest_payload(
    payload: Any, *, manifest_path: Path
) -> DictionaryManifest:
    """Validate a parsed manifest payload without rereading disk."""
    if not isinstance(payload, dict):
        raise DictionaryInstallerError(
            f"manifest at {manifest_path} must decode to an object"
        )

    def _require_str(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise DictionaryInstallerError(
                f"manifest field {key!r} must be a non-empty string"
            )
        return value.strip()

    version = _require_str("version")
    filename = _require_str("filename")
    if not _FILENAME_RE.fullmatch(filename):
        raise DictionaryInstallerError(
            f"manifest filename is not a portable single-segment name: {filename!r}"
        )
    if "/" in filename or "\\" in filename or ".." in filename:
        raise DictionaryInstallerError(
            f"manifest filename must not contain path separators or '..': {filename!r}"
        )
    sha256_value = _require_str("sha256").lower()
    if not _SHA256_RE.fullmatch(sha256_value):
        raise DictionaryInstallerError(
            f"manifest sha256 must be a 64-character lowercase hex string: {sha256_value!r}"
        )
    bytes_value = payload.get("bytes")
    if not isinstance(bytes_value, int) or isinstance(bytes_value, bool):
        raise DictionaryInstallerError("manifest 'bytes' must be an integer")
    if bytes_value <= 0 or bytes_value > _MAX_DICTIONARY_BYTES:
        raise DictionaryInstallerError(
            f"manifest 'bytes' must be in 1..{_MAX_DICTIONARY_BYTES} (got {bytes_value})"
        )
    classification = _require_str("classification")
    attribution = _require_str("attribution")

    raw_url = payload.get("download_url", None)
    download_url: str | None = None
    if raw_url is not None:
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise DictionaryInstallerError(
                "manifest 'download_url' must be a non-empty string when present"
            )
        url = raw_url.strip()
        if not (
            url.startswith("https://")
            or url.startswith("http://")
            or url.startswith("file://")
        ):
            raise DictionaryInstallerError(
                "manifest 'download_url' must be http(s):// or file:// "
                f"(got {url!r})"
            )
        if any(token in url.lower() for token in ("@", "token=", "api_key=", "apikey=")):
            raise DictionaryInstallerError(
                "manifest 'download_url' must not embed credentials"
            )
        download_url = url

    return DictionaryManifest(
        version=version,
        filename=filename,
        sha256=sha256_value,
        bytes=bytes_value,
        classification=classification,
        attribution=attribution,
        download_url=download_url,
        manifest_path=manifest_path,
    )


def expected_filename(manifest: DictionaryManifest) -> str:
    """Return the on-disk filename the installer will activate."""
    return manifest.filename


def expected_sha256(manifest: DictionaryManifest) -> str:
    """Return the SHA-256 the installer expects from the asset bytes."""
    return manifest.sha256


def expected_bytes(manifest: DictionaryManifest) -> int:
    """Return the byte size the installer expects from the asset."""
    return manifest.bytes


def compute_sha256(path: Path | str, *, chunk: int = _CHUNK_BYTES) -> str:
    """Compute SHA-256 over a file's bytes by streaming."""
    target = Path(path)
    h = hashlib.sha256()
    with target.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _open_readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def verify_dictionary_identity(
    candidate: Path | str,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> str:
    """Verify exact release identity only: file presence, byte size, SHA-256.

    This is the lightweight precheck used before any full validation. It
    deliberately does NOT open the file as SQLite, run ``PRAGMA
    quick_check``, or call :func:`app.dictionary.validate_candidate_dictionary`
    — those remain the responsibility of :func:`verify_dictionary_bytes` (the
    installer's full verification) and ``DictionaryRuntime`` (runtime
    integrity/schema activation). Keeping this helper narrow lets callers
    that only need to prove "this file is exactly the release the manifest
    names" do so with a single streaming read instead of a full
    PART-A validation pass.

    Returns the recomputed SHA-256 on success; raises
    :class:`DictionaryInstallerError` on any mismatch. The candidate is
    left untouched.
    """
    target = Path(candidate)
    if not target.is_file():
        raise DictionaryInstallerError(f"candidate file not found: {target}")
    actual_bytes = target.stat().st_size
    if actual_bytes != expected_bytes:
        raise DictionaryInstallerError(
            f"dictionary size mismatch: got {actual_bytes} bytes, "
            f"expected {expected_bytes}"
        )
    actual_sha = compute_sha256(target)
    if actual_sha != expected_sha256.lower():
        raise DictionaryInstallerError(
            f"dictionary SHA-256 mismatch: got {actual_sha}, "
            f"expected {expected_sha256}"
        )
    return actual_sha


def verify_dictionary_bytes(
    candidate: Path | str,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> str:
    """Verify byte size, SHA-256, SQLite quick_check, and full PART-A validation.

    Returns the recomputed SHA-256 on success; raises
    :class:`DictionaryInstallerError` on any mismatch. The candidate is
    left untouched so a subsequent atomic rename can install it. Identity
    (size/SHA) is delegated to :func:`verify_dictionary_identity` so the two
    checks share one implementation instead of drifting apart.
    """
    target = Path(candidate)
    actual_sha = verify_dictionary_identity(
        target, expected_sha256=expected_sha256, expected_bytes=expected_bytes
    )
    try:
        conn = _open_readonly_sqlite(target)
        try:
            quick_check = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise DictionaryInstallerError(
            f"dictionary is not a readable SQLite file: {target}"
        ) from exc
    if not quick_check or any(str(row[0]).lower() != "ok" for row in quick_check):
        raise DictionaryInstallerError(
            f"dictionary SQLite quick_check failed: {quick_check!r}"
        )
    # Full PART-A schema validation is the canonical gate from
    # app.dictionary; reusing it here keeps the installer consistent
    # with live activation.
    from app.dictionary import (  # noqa: PLC0415
        DictionaryAssetError,
        validate_candidate_dictionary,
    )

    try:
        asset = validate_candidate_dictionary(target)
    except DictionaryAssetError as exc:
        raise DictionaryInstallerError(
            f"dictionary PART-A validation failed: {exc}"
        ) from exc
    try:
        if asset.sha256 != actual_sha:
            raise DictionaryInstallerError(
                "dictionary validated snapshot SHA differs from candidate digest"
            )
    finally:
        asset.close()
    return actual_sha


def _download_to_temp(
    url: str,
    *,
    temp_dir: Path,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Stream the URL to a temporary file under ``temp_dir``."""
    # stdlib urllib only; the dependency graph must stay LLM-free and
    # free of opaque HTTP clients.
    from urllib.request import Request, urlopen  # noqa: PLC0415

    request = Request(url, headers={"User-Agent": "flashcard-installer/1.0"})
    try:
        response = urlopen(request, timeout=60)
    except (OSError, ValueError) as exc:
        raise DictionaryInstallerError(
            f"dictionary download failed for {url}: {exc}"
        ) from exc
    fd, temp_name = tempfile.mkstemp(
        prefix=".dictionary-", suffix=".partial", dir=str(temp_dir)
    )
    temp_path = Path(temp_name)
    try:
        total: int | None = None
        cl = response.headers.get("Content-Length")
        if cl is not None and cl.isdigit():
            total = int(cl)
        written = 0
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = response.read(_CHUNK_BYTES)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(written, total)
        if total is not None and written != total:
            raise DictionaryInstallerError(
                f"dictionary download truncated: wrote {written} of {total} bytes"
            )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _atomic_rename(source: Path, destination: Path) -> None:
    """Atomically replace ``destination`` with ``source``."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def install_dictionary(
    manifest: DictionaryManifest,
    *,
    target_dir: Path | str,
    download_dir: Path | str | None = None,
    progress: Callable[[int, int | None], None] | None = None,
) -> Path:
    """Install the manifest's dictionary into ``target_dir``.

    Behaviour:
        1. If a dictionary already exists at
           ``target_dir/manifest.filename`` and verifies against the
           manifest, it is returned unchanged.
        2. If the manifest has no ``download_url``, the installer
           fails closed with a clear error — the user must place a
           verified dictionary at the target path manually.
        3. Otherwise, the URL is streamed into a temp file under
           ``download_dir`` (or ``target_dir``), then fully verified,
           then atomically renamed into place.
    """
    target_directory = Path(target_dir).resolve()
    target_directory.mkdir(parents=True, exist_ok=True)
    target_path = target_directory / manifest.filename

    if target_path.is_file():
        try:
            verify_dictionary_bytes(
                target_path,
                expected_sha256=manifest.sha256,
                expected_bytes=manifest.bytes,
            )
            return target_path
        except DictionaryInstallerError:
            # The existing file is broken or stale. We never overwrite
            # a valid dictionary (ADR-0001 §12) and we never silently
            # replace an unreadable one either — the caller can decide
            # explicitly by removing the file.
            raise DictionaryInstallerError(
                f"existing dictionary at {target_path} failed verification; "
                "remove the file before retrying to force reinstall"
            )

    if not manifest.download_url:
        raise DictionaryInstallerError(
            "no verified dictionary found at "
            f"{target_path} and the manifest has no download_url; "
            "place the dictionary file manually before launching"
        )

    download_directory = (
        Path(download_dir).resolve() if download_dir is not None else target_directory
    )
    download_directory.mkdir(parents=True, exist_ok=True)

    temp_path = _download_to_temp(
        manifest.download_url, temp_dir=download_directory, progress=progress
    )
    try:
        verify_dictionary_bytes(
            temp_path,
            expected_sha256=manifest.sha256,
            expected_bytes=manifest.bytes,
        )
        _atomic_rename(temp_path, target_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return target_path
