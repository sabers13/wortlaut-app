"""Dictionary mode bootstrap, session-scoped provider wiring, and lifecycle.

Slice 12 introduces:

* A session-only dictionary mode preference (no persisted backend state)
  reflected through :class:`DictionaryModeController`. Restart behavior
  is derived from the then-current Offline asset per ADR-0009.
* A :class:`DictionarySession` object that owns one provider for the
  current process and exposes the same oracle-shaped reads used by
  ``app/api.py`` — exact/surface lookup, sense lookup, and full
  candidate materialization — through the Slice-11 contract
  (``DictionaryProvider``).
* A pre-flight :func:`measure_offline_install_peak` that derives a
  conservative peak-usage number for the full ``--install-dictionary``
  path. The number is the installer's ``temp file + canonical target +
  atomic rename + private validation snapshot`` chain, with a safety
  multiplier.
* Removal of the managed canonical full Offline dictionary while Online
  is active (with the Online provider's own immutable shard cache
  untouched), and the matching Offline-active structured rejection.
* Selection semantics:

  - ``--dictionary-mode offline`` selects Offline (LocalDictionaryProvider).
  - ``--dictionary-mode online`` selects Online (OnlineDictionaryProvider)
    for the running process. It never persists and never falls back
    silently on failure.
  - Default with a verified canonical full Offline dictionary selects
    Offline automatically.
  - Default without one produces the runtime chooser (no dictionary
    network activity before user action).

The provider boundary is the same as Slice 11: the public ops return
typed records, never a raw ``sqlite3.Connection`` (ADR-0009). API
consumers depend on the controller and the provider; they do not
reach into the runtime asset handle (AGENTS R2 / R9 / R13).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

from app.dictionary import DictionaryAssetError, validate_candidate_dictionary

DictionaryModeName = Literal["offline", "online", "unconfigured"]
_REMOVABLE_FILENAME: str = "dictionary.sqlite"
_LOCAL_FILENAME: str = "dictionary.sqlite"
_PEAK_SAFETY_MULTIPLIER: float = 1.50
# Default dictionary asset byte count for the in-process preflight when
# no canonical Offline manifest is loaded yet (e.g. fresh chooser state).
# The bound reflects the well-known ~945 MB v2 asset; the install path
# uses the actual declared manifest byte count whenever one is available.
OFFLINE_INSTALL_DEFAULT_MANIFEST_BYTES: int = 945_418_240
# Conservative absolute minimum threshold (bytes) used by the
# installer preflight when the caller cannot supply a manifest triple.
# Roughly the manifest size plus a 30% safety margin for the
# installer-temp + canonical-target + validation-snapshot chain.
_OFFLINE_INSTALL_MIN_THRESHOLD_BYTES: int = int(
    OFFLINE_INSTALL_DEFAULT_MANIFEST_BYTES * _PEAK_SAFETY_MULTIPLIER
)


@dataclass(frozen=True)
class OfflineInstallTriple:
    """Server-owned trusted Offline install definition.

    Derived from ``release/dictionary-manifest-v2.json`` at launcher
    / API construction time. The browser / API caller cannot supply
    any of its fields; the ``install-offline`` endpoint, the
    ``remove-offline`` endpoint, and the ``use-offline`` endpoint all
    read this triple directly. ``download_url`` is the trusted URL
    on the committed Wortlaut GitHub Release; ``manifest_path`` is
    the source the launcher / E2E harness used to build the triple.
    """

    version: str
    filename: str
    sha256: str
    bytes: int
    download_url: str
    manifest_path: Path


@dataclass(frozen=True)
class StartupModeDecision:
    """The startup mode decision derived from explicit / canonical state.

    ``mode`` is one of ``"offline"``, ``"online"``, ``"unconfigured"``.
    ``canonical_offline_path`` is the resolved managed canonical path
    whether or not it currently exists. ``canonical_offline_present`` is
    True iff the file exists AND matches the manifest identity AND
    opens without corruption. ``canonical_offline_valid`` follows the
    full identity check; the chooser is shown only when validity fails
    on default startup.
    """

    mode: DictionaryModeName
    canonical_offline_path: Path
    canonical_offline_present: bool
    canonical_offline_valid: bool
    note: str = ""


@dataclass(frozen=True)
class OfflineInstallPeak:
    """Reported peak disk usage for the full Offline install path."""

    measured_bytes: int
    inflation_multiplier: float
    safety_threshold_bytes: int
    components: Mapping[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "measured_bytes": int(self.measured_bytes),
            "inflation_multiplier": float(self.inflation_multiplier),
            "safety_threshold_bytes": int(self.safety_threshold_bytes),
            "components": {k: int(v) for k, v in self.components.items()},
        }


@dataclass(frozen=True)
class OfflineInstallRefused(RuntimeError):
    """Raised when the preflight refuses the Offline download."""

    code: str
    detail: str
    available_bytes: int
    required_bytes: int

    def __str__(self) -> str:
        return (
            f"{self.code}: {self.detail} "
            f"(available={self.available_bytes}, required={self.required_bytes})"
        )


def _free_bytes(path: Path) -> int:
    """Return the available bytes for ``path``'s filesystem, or 0 on failure.

    Uses ``shutil.disk_usage`` which is implemented in terms of
    ``statvfs`` on POSIX. Failures fall back to 0; the preflight will
    then refuse the download as a conservative default rather than
    permit an unsafe install.
    """
    try:
        target = path if path.is_dir() else path.parent
        target.mkdir(parents=True, exist_ok=True)
        return int(shutil.disk_usage(target).free)
    except OSError:
        return 0


def _validate_target_path(path: Path) -> Path:
    """Resolve and ensure ``path`` is a non-empty, non-escaping absolute path."""
    if not isinstance(path, Path):
        path = Path(path)
    resolved = path.resolve()
    if ".." in resolved.parts:
        raise ValueError(f"path traversal forbidden: {path}")
    return resolved


def _canonical_offline_full_path(
    managed_dir: Path, manifest_filename: str
) -> Path:
    """Return the expected canonical full-asset path within ``managed_dir``."""
    if not manifest_filename or "/" in manifest_filename or "\\" in manifest_filename:
        raise ValueError(
            f"manifest filename must be a single portable segment: {manifest_filename!r}"
        )
    if manifest_filename != _LOCAL_FILENAME:
        # The Slice 12 chooser contract uses a single canonical
        # filename inside the managed directory; the assistant must
        # never accept a path-traveling filename here.
        raise ValueError(
            f"unexpected Offline manifest filename: {manifest_filename!r}"
        )
    return (managed_dir / manifest_filename).resolve()


def _manifest_identity_or_none(
    manifest_filename: str,
    manifest_sha256: str | None,
    manifest_bytes: int | None,
) -> tuple[str, str, int] | None:
    """Return a fully-trusted manifest identity triple if available.

    ``None`` when any required field is missing. The triple is used only
    to validate the canonical file against the manifest before declaring
    it "valid" for default Offline startup.
    """
    if not manifest_filename or not manifest_sha256 or manifest_bytes is None:
        return None
    return (manifest_filename, manifest_sha256, int(manifest_bytes))


def decide_startup_mode(
    *,
    managed_dir: Path,
    explicit_mode: DictionaryModeName | None,
    manifest_filename: str,
    manifest_sha256: str | None,
    manifest_bytes: int | None,
) -> StartupModeDecision:
    """Resolve the canonical startup mode decision per ADR-0009.

    Parameters
    ----------
    managed_dir:
        The canonical managed dictionary directory (resolved).
    explicit_mode:
        One of ``"offline"``, ``"online"``, or ``None``. ``None`` means
        "default" (auto from canonical Offline state, or unconfigured
        chooser).
    manifest_filename, manifest_sha256, manifest_bytes:
        The Offline manifest triple used to validate the canonical file
        for default startup.
    """
    resolved_dir = _validate_target_path(managed_dir)
    canonical_path = _canonical_offline_full_path(
        resolved_dir, _LOCAL_FILENAME
    )
    present = canonical_path.is_file()
    valid = False
    if present:
        identity = _manifest_identity_or_none(
            manifest_filename, manifest_sha256, manifest_bytes
        )
        if identity is not None:
            try:
                asset = validate_candidate_dictionary(canonical_path)
            except (DictionaryAssetError, sqlite3.Error, OSError):
                asset = None
            if asset is not None:
                try:
                    if (
                        asset.path.name == identity[0]
                        and asset.sha256.lower() == identity[1].lower()
                        and canonical_path.stat().st_size == identity[2]
                    ):
                        valid = True
                finally:
                    try:
                        asset.close()
                    except Exception:
                        pass

    if explicit_mode == "online":
        return StartupModeDecision(
            mode="online",
            canonical_offline_path=canonical_path,
            canonical_offline_present=present,
            canonical_offline_valid=valid,
            note="explicit --dictionary-mode online",
        )
    if explicit_mode == "offline":
        return StartupModeDecision(
            mode="offline",
            canonical_offline_path=canonical_path,
            canonical_offline_present=present,
            canonical_offline_valid=valid,
            note="explicit --dictionary-mode offline",
        )
    # Default startup
    if valid:
        return StartupModeDecision(
            mode="offline",
            canonical_offline_path=canonical_path,
            canonical_offline_present=True,
            canonical_offline_valid=True,
            note="default offline (canonical valid)",
        )
    return StartupModeDecision(
        mode="unconfigured",
        canonical_offline_path=canonical_path,
        canonical_offline_present=present,
        canonical_offline_valid=False,
        note="default chooser (no valid canonical offline)",
    )


def measure_offline_install_peak(
    *,
    manifest_bytes: int,
    install_dir: Path,
    snapshot_dir: Path | None = None,
    snapshot_factory: Callable[[Path], Path] | None = None,
) -> OfflineInstallPeak:
    """Conservative peak disk-usage estimate for the full Offline install.

    The estimate accounts for:

    * the actual manifest bytes (downloaded/installer temp file);
    * the canonical destination file;
    * the installer's unlinked validation snapshot copy;
    * the ``DictionaryRuntime`` validation snapshot (legacy private
      handle) — currently 0 since Slice 11 is no longer in the path;
    * the validator reference copy inside the provider (also 0);

    A conservative safety multiplier is applied. The preflight refuses
    any install where ``free_bytes < safety_threshold_bytes``.
    """
    if not isinstance(manifest_bytes, int) or isinstance(manifest_bytes, bool):
        raise TypeError("manifest_bytes must be an int")
    if manifest_bytes <= 0:
        raise ValueError("manifest_bytes must be positive")

    install_path = _validate_target_path(install_dir)
    snapshot_path = _validate_target_path(snapshot_dir) if snapshot_dir else install_path

    components: dict[str, int] = {
        "manifest_bytes": int(manifest_bytes),
        "canonical_target": int(manifest_bytes),
        "installer_temp": int(manifest_bytes),
        "validator_snapshot": int(manifest_bytes),
        "runtime_snapshot": 0,
        "provider_snapshot": 0,
    }
    measured = sum(components.values())

    # If a snapshot factory is provided, respect its result. (Tests may
    # observe the snapshot path used by the live installer here.)
    if snapshot_factory is not None:
        try:
            candidate = snapshot_factory(snapshot_path)
            if candidate:
                components["snapshot_path_used"] = int(os.path.getsize(candidate))
                measured = sum(
                    v for k, v in components.items()
                    if k.endswith(("_bytes", "_target", "_temp", "_snapshot"))
                    or k == "snapshot_path_used"
                )
        except OSError:
            pass

    threshold = int(measured * _PEAK_SAFETY_MULTIPLIER)
    return OfflineInstallPeak(
        measured_bytes=int(measured),
        inflation_multiplier=_PEAK_SAFETY_MULTIPLIER,
        safety_threshold_bytes=threshold,
        components=MappingProxyType(dict(components)),
    )


def preflight_offline_install(
    *,
    manifest_bytes: int,
    install_dir: Path,
) -> OfflineInstallPeak:
    """Run the conservative free-space preflight and refuse on shortage.

    Raises :class:`OfflineInstallRefused` if the available free space is
    below the conservative safety threshold. The function never imports
    or touches the dictionary file; it only inspects the destination
    filesystem and the declared byte count.
    """
    peak = measure_offline_install_peak(
        manifest_bytes=manifest_bytes, install_dir=install_dir
    )
    available = _free_bytes(install_dir)
    if available < peak.safety_threshold_bytes:
        raise OfflineInstallRefused(
            code="offline_install_insufficient_disk_space",
            detail=(
                "Full Offline dictionary download requires more free space "
                f"than the {manifest_bytes}-byte asset alone. "
                f"available={available} bytes, "
                f"required>=threshold={peak.safety_threshold_bytes} bytes "
                f"({peak.inflation_multiplier:.2f}x safety)."
            ),
            available_bytes=int(available),
            required_bytes=int(peak.safety_threshold_bytes),
        )
    return peak


def remove_canonical_offline(
    *,
    managed_dir: Path,
    target_filename: str | None = None,
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> tuple[bool, str]:
    """Attempt to remove the managed canonical full Offline asset only.

    Returns ``(removed, status_detail)``. The removal is permissive about
    which filename to remove (``dictionary.sqlite`` by default) and
    only accepts paths inside the resolved managed directory. No user
    data is touched. Returns ``(False, ...)`` with a structured reason
    when the target path does not exist, lives outside the managed
    directory, fails the SHA/size match, or cannot be unlinked.

    The returned ``status_detail`` string is a small machine-readable
    keyword prefixed by a colon-separated code, suitable for the
    Settings UI.
    """
    resolved_dir = _validate_target_path(managed_dir)
    filename = target_filename or _LOCAL_FILENAME
    if "/" in filename or "\\" in filename or ".." in filename:
        return False, "offline_removal_rejected:invalid_filename"
    target = (resolved_dir / filename).resolve()
    try:
        target.relative_to(resolved_dir)
    except ValueError:
        return False, "offline_removal_rejected:path_outside_managed_dir"

    if not target.is_file():
        return False, "offline_removal_rejected:target_not_present"

    # Identity confirmation (optional): if the caller expects a specific
    # SHA/size, the candidate must match. This guards against deleting
    # a non-canonical asset by mistake.
    if expected_sha256 is not None or expected_bytes is not None:
        try:
            actual_bytes = target.stat().st_size
        except OSError:
            return False, "offline_removal_rejected:stat_failed"
        if expected_bytes is not None and actual_bytes != int(expected_bytes):
            return False, "offline_removal_rejected:size_mismatch"
        if expected_sha256 is not None:
            from app.dict_install import compute_sha256

            try:
                actual_sha = compute_sha256(target)
            except OSError:
                return False, "offline_removal_rejected:hash_failed"
            if actual_sha.lower() != expected_sha256.lower():
                return False, "offline_removal_rejected:sha_mismatch"

    try:
        target.unlink()
    except FileNotFoundError:
        return False, "offline_removal_rejected:target_not_present"
    except OSError as exc:
        return False, f"offline_removal_rejected:unlink_failed:{exc.errno}"

    return True, "offline_removal_succeeded"


def session_status(
    *,
    mode: DictionaryModeName,
    canonical_offline_path: Path,
    canonical_offline_present: bool,
    canonical_offline_valid: bool,
    online_active: bool,
) -> dict[str, Any]:
    """Return the chooser/runtime status dictionary for the Settings UI."""
    return {
        "mode": mode,
        "canonical_offline_path": str(canonical_offline_path),
        "canonical_offline_present": bool(canonical_offline_present),
        "canonical_offline_valid": bool(canonical_offline_valid),
        "online_active": bool(online_active),
    }


__all__ = [
    "DictionaryModeName",
    "OfflineInstallPeak",
    "OfflineInstallRefused",
    "OfflineInstallTriple",
    "StartupModeDecision",
    "decide_startup_mode",
    "measure_offline_install_peak",
    "preflight_offline_install",
    "remove_canonical_offline",
    "session_status",
]
