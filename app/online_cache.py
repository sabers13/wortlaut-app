"""Verified shard cache and immutable leases for the Online provider.

ADR-0009 lifecycle:

    ABSENT
      -> DOWNLOADING
      -> VERIFIED
      -> IMMUTABLE LEASE

The cache is single-flight per shard identity, downloads to a private
temporary path, verifies byte count, SHA-256, and SQLite/logical shard
structure, fsyncs bytes, then atomically installs the canonical artifact.
Reads against the cache always re-verify before handing out a new
validated lease within the process; ``safe_unlink`` is used for clear-
cache and active leases retain their private snapshot.

Every lease carries a ``was_downloaded`` boolean. The Online provider
charges its per-operation budget against NEW remote lookup-shard
downloads only: a verified cached read (``was_downloaded == False``)
is free, a missing-path download (``was_downloaded == True``) charges,
and a corrupt cached artifact that has to be refetched
(``was_downloaded == True``) also charges — the previous candidate's
path-existence predicate under-charged in that case.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

from app.online_manifest import (
    ManifestAsset,
)
from app.provider import ProviderIntegrityError


@dataclass(frozen=True)
class ShardIdentity:
    """One stable shard identity: ``(family, bucket)``."""

    family: str
    bucket: int

    def __post_init__(self) -> None:
        if not isinstance(self.family, str) or not self.family:
            raise ValueError("family must be a non-empty string")
        if not isinstance(self.bucket, int) or isinstance(self.bucket, bool):
            raise ValueError("bucket must be an int")
        if self.bucket < 0:
            raise ValueError("bucket must be non-negative")


@dataclass(frozen=True)
class ShardLease:
    """One validated immutable lease to a verified shard snapshot.

    The lease references a private unlinked snapshot path; callers may
    open it for reads but must not delete it. The lease owner is the
    cache, which retains the snapshot for the duration of any active
    lease.

    ``was_downloaded`` is ``True`` when this lease was produced by an
    actual remote download in this process (miss, corruption refetch,
    clear-cache rebuild). It is ``False`` for a verified cached re-read
    or for the single-flight waiters that piggy-back on a concurrent
    download.
    """

    identity: ShardIdentity
    asset: ManifestAsset
    snapshot_path: Path
    sha256: str
    byte_size: int
    schema_version: str
    was_downloaded: bool = False

    @property
    def family(self) -> str:
        return self.identity.family

    @property
    def bucket(self) -> int:
        return self.identity.bucket


@dataclass(frozen=True)
class ShardRequest:
    """Cache-miss request raised by the provider for a missing shard."""

    identity: ShardIdentity
    asset: ManifestAsset


@dataclass(frozen=True)
class CacheStats:
    """Counters observed by the cache during one process."""

    hits: int = 0
    misses: int = 0
    refetches: int = 0
    corruptions: int = 0
    clears: int = 0
    downloads: int = 0
    active_leases: int = 0


@dataclass(frozen=True, slots=True)
class _PrivateSnapshot:
    """Retained private snapshot path; deleted by the cache after release."""

    path: Path
    sha256: str


@dataclass(slots=True)
class _InflightState:
    """Single-flight state for one in-progress shard download.

    The leader downloads, records either ``payload`` (success) or
    ``error`` (failure, including pre-download budget rejection), then
    signals ``event``. Waiters always wake: on success they receive a
    lease with ``was_downloaded=False``; on failure they receive the
    same structured failure the leader observed. ``waiters`` counts the
    parties (leader plus waiters) still holding a reference; the last
    party to leave removes the bookkeeping.
    """

    event: threading.Event
    waiters: int = 0
    payload: bytes | None = None
    error: BaseException | None = None


@dataclass
class _StatsCounter:
    hits: int = 0
    misses: int = 0
    refetches: int = 0
    corruptions: int = 0
    clears: int = 0
    downloads: int = 0
    active_leases: int = 0

    def record_hit(self) -> None:
        self.hits += 1

    def record_miss(self) -> None:
        self.misses += 1

    def record_corruption(self) -> None:
        self.corruptions += 1
        self.refetches += 1

    def record_clear(self) -> None:
        self.clears += 1

    def record_download(self) -> None:
        self.downloads += 1

    def record_lease(self) -> None:
        # lease install opens and pins one private snapshot; release
        # drops the count elsewhere (kept here for symmetry but no-op).
        return

    def snapshot(self) -> CacheStats:
        return CacheStats(
            hits=self.hits,
            misses=self.misses,
            refetches=self.refetches,
            corruptions=self.corruptions,
            clears=self.clears,
            downloads=self.downloads,
            active_leases=self.active_leases,
        )


class ShardCache:
    """Process-local verified shard cache with single-flight and immutable leases.

    The cache stores canonical verified shards under ``cache_dir/verified``.
    On a hit it copies the verified bytes into a private temporary
    snapshot, fsyncs, and returns a :class:`ShardLease`; the verified
    bytes remain untouched on disk and the private snapshot is what the
    consumer reads.

    Cache-miss downloads are single-flight per identity: the first call
    (the leader) performs the remote transfer while concurrent calls
    against the same identity wait on the same event. The leader's lease
    reports ``was_downloaded=True``; waiters receive leases with
    ``was_downloaded=False`` because only the leader performed the
    transfer. A leader failure (transport error, validation error, or
    pre-download budget rejection) always signals the event, so every
    waiter wakes with the same structured failure instead of blocking
    forever. Corruption quarantines and refetches set
    ``was_downloaded=True`` for the recovery lease.

    An optional ``before_download`` hook runs for the single-flight
    leader immediately before the transport is invoked (and before a
    corruption refetch). It may raise to reject the download before any
    network transfer; the rejection is broadcast to waiters like any
    other leader failure and the transport is never called.

    :meth:`clear` serializes cache mutation against new acquisitions and
    in-flight downloads: it blocks relevant new leases while active,
    waits for in-flight downloads to finish, removes the canonical
    verified files (so no pre-clear install can repopulate the cache
    afterward), then wakes waiters. Active immutable private leases
    remain usable throughout.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        transport: Callable[[ShardRequest], bytes],
        expected_sizes: Mapping[ShardIdentity, int] | None = None,
        structure_validator: Callable[[ShardIdentity, bytes, Path], None] | None = None,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / "verified").mkdir(parents=True, exist_ok=True)
        self._transport = transport
        self._expected_sizes: Mapping[ShardIdentity, int] = (
            MappingProxyType(dict(expected_sizes)) if expected_sizes else MappingProxyType({})
        )
        self._structure_validator = structure_validator or _default_structure_validator
        self._inflight: dict[ShardIdentity, _InflightState] = {}
        self._lease_to_id: dict[ShardLease, int] = {}
        self._lock = threading.Lock()
        self._clear_cond = threading.Condition(self._lock)
        self._clearing = False
        self._active_downloads = 0
        self._lock_serialise = threading.Lock()
        self._stats = _StatsCounter()
        self._leases: dict[int, _PrivateSnapshot] = {}
        self._lease_counter = 0
        self._closed = False

    @property
    def cache_dir(self) -> Path:
        """Return the cache root directory."""
        return self._cache_dir

    @property
    def stats(self) -> CacheStats:
        """Return a snapshot of the cache counters."""
        return self._stats.snapshot()

    def lease(
        self,
        request: ShardRequest,
        *,
        before_download: Callable[[ShardIdentity], None] | None = None,
    ) -> ShardLease:
        """Return an immutable verified lease for ``request``.

        On miss the cache downloads through ``transport``, verifies, and
        installs a verified canonical artifact under
        ``cache_dir/verified/<family>/<bucket>``. On hit it re-runs the
        size/SHA/structure verification before producing the lease.

        The returned lease's ``was_downloaded`` field is ``True`` only
        when this call performed an actual remote download (miss,
        corruption refetch, or clear-cache rebuild). Verified cached
        reads see ``False``, and single-flight waiters that piggy-backed
        on a concurrent leader download also see ``False`` — only the
        leader reports ``True``. The Online provider uses that signal to
        charge its per-operation budget against new remote lookup-shard
        downloads only.

        ``before_download`` runs only for the single-flight leader,
        immediately before the transport is invoked (including before a
        corruption refetch). If it raises, the transport is never called
        and waiters receive the same failure.
        """
        if not isinstance(request, ShardRequest):
            raise TypeError("request must be a ShardRequest")

        if self._closed:
            raise ProviderIntegrityError("shard cache is closed")

        # Serialize against an active clear: new acquisitions wait while
        # clear owns the cache mutation.
        with self._clear_cond:
            while self._clearing:
                self._clear_cond.wait()

        canonical_path = self._canonical_path(request.identity)
        # Validate the existing verified artifact first; treat corruption
        # by quarantining and refetching.
        if canonical_path.exists():
            try:
                payload = self._read_and_validate(canonical_path, request.asset)
            except ProviderIntegrityError:
                self._stats.record_corruption()
                self._quarantine(canonical_path)
                return self._download_path(request, before_download)
            lease = self._install_lease(request, payload, was_downloaded=False)
            self._stats.record_hit()
            return lease

        return self._download_path(request, before_download)

    def release(self, lease: ShardLease) -> None:
        """Release one lease and delete its private snapshot."""
        if not isinstance(lease, ShardLease):
            raise TypeError("lease must be a ShardLease")
        with self._lock_serialise:
            lease_id = self._lease_to_id.pop(lease, None)
            snapshot = self._leases.pop(lease_id, None) if lease_id is not None else None
        if snapshot is not None:
            self._stats.active_leases = max(0, self._stats.active_leases - 1)
            try:
                Path(snapshot.path).unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> None:
        """Remove all canonical verified files under the cache root.

        Active leases remain valid and continue to point to their private
        snapshots; only the canonical verified files are removed.

        Mutation is serialized: new acquisitions block at the lease gate
        while clear is active, and clear waits for in-flight downloads to
        finish before removing files — so a pre-clear in-flight canonical
        install cannot silently repopulate the verified cache after clear
        has completed. Waiters on the single-flight event are unaffected
        (they wait on a per-identity event, not the mutation gate), so no
        deadlock with single-flight is possible.
        """
        with self._clear_cond:
            self._stats.record_clear()
            self._clearing = True
            try:
                while self._active_downloads > 0:
                    self._clear_cond.wait()
                verified_dir = self._cache_dir / "verified"
                if verified_dir.exists():
                    # Move the verified dir aside atomically to avoid races
                    # against active acquisitions, then unlink.
                    tmp: Path | None = self._cache_dir / "verified.delete"
                    if tmp is not None and tmp.exists():
                        shutil.rmtree(tmp, ignore_errors=True)
                    try:
                        assert tmp is not None
                        os.replace(verified_dir, tmp)
                    except OSError:
                        shutil.rmtree(verified_dir, ignore_errors=True)
                        tmp = None
                    if tmp is not None:
                        shutil.rmtree(tmp, ignore_errors=True)
                    verified_dir.mkdir(parents=True, exist_ok=True)
            finally:
                self._clearing = False
                self._clear_cond.notify_all()

    def close(self) -> None:
        """Idempotently close the cache. Active leases are invalidated."""
        self._closed = True
        with self._lock_serialise:
            for lease_id, snapshot in list(self._leases.items()):
                try:
                    Path(snapshot.path).unlink(missing_ok=True)
                except OSError:
                    pass
                self._leases.pop(lease_id, None)
            self._lease_to_id.clear()

    def _canonical_path(self, identity: ShardIdentity) -> Path:
        return self._cache_dir / "verified" / identity.family / f"{identity.bucket}.sqlite"

    def _read_and_validate(self, path: Path, asset: ManifestAsset) -> bytes:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ProviderIntegrityError(f"failed to read verified shard: {exc}") from exc
        if len(payload) != asset.byte_size:
            raise ProviderIntegrityError(
                f"verified shard byte size mismatch: expected {asset.byte_size} got {len(payload)}"
            )
        if sha256(payload).hexdigest() != asset.sha256:
            raise ProviderIntegrityError("verified shard SHA-256 mismatch")
        self._structure_validator(_identity_for_asset(asset), payload, path)
        return payload

    def _download_path(
        self,
        request: ShardRequest,
        before_download: Callable[[ShardIdentity], None] | None,
    ) -> ShardLease:
        """Acquire a verified lease via the single-flight download path.

        The first caller for an identity becomes the leader and performs
        the remote transfer; concurrent callers wait on the leader's
        event. The leader's lease reports ``was_downloaded=True`` while
        every waiter reports ``was_downloaded=False``. A leader failure
        — transport error, validation error, or a ``before_download``
        rejection — always signals the event, so every waiter wakes with
        the same structured failure and no deadlock is possible.
        """
        # Single-flight per identity. The inflight bookkeeping is
        # keyed by the request identity; concurrent calls to OTHER
        # identities must not overwrite this identity's bookkeeping.
        with self._lock:
            state = self._inflight.get(request.identity)
            if state is None:
                state = _InflightState(event=threading.Event())
                self._inflight[request.identity] = state
                should_download = True
            else:
                should_download = False
            state.waiters += 1

        if should_download:
            self._stats.record_miss()
            with self._lock:
                self._active_downloads += 1
            try:
                # Budget/reservation hook: runs only for the leader,
                # immediately before the transport, and before a
                # corruption refetch. A rejection means zero transport.
                if before_download is not None:
                    before_download(request.identity)
                payload = self._download_and_validate(request)
            except BaseException as exc:
                with self._lock:
                    state.payload = None
                    state.error = exc
                    state.event.set()
                    self._active_downloads -= 1
                    self._clear_cond.notify_all()
                with self._lock:
                    state.waiters -= 1
                    if state.waiters <= 0:
                        self._inflight.pop(request.identity, None)
                raise
            with self._lock:
                state.payload = payload
                state.error = None
                state.event.set()
                self._active_downloads -= 1
                self._clear_cond.notify_all()
            try:
                return self._install_lease(request, payload, was_downloaded=True)
            finally:
                with self._lock:
                    state.waiters -= 1
                    if state.waiters <= 0:
                        self._inflight.pop(request.identity, None)

        self._stats.record_miss()
        state.event.wait()
        with self._lock:
            waited_payload = state.payload
            waited_error = state.error
            state.waiters -= 1
            if state.waiters <= 0:
                self._inflight.pop(request.identity, None)
        if waited_error is not None:
            raise waited_error
        if not waited_payload:
            raise ProviderIntegrityError("shard download produced no payload")
        # Waiters piggy-back on the leader's transfer; only the leader
        # observes a fresh download.
        return self._install_lease(request, waited_payload, was_downloaded=False)

    def _download_and_validate(self, request: ShardRequest) -> bytes:
        payload = self._transport(request)
        if not isinstance(payload, (bytes, bytearray)):
            raise ProviderIntegrityError("shard transport returned non-bytes")
        if len(payload) != request.asset.byte_size:
            raise ProviderIntegrityError(
                "downloaded byte size mismatch: expected "
                f"{request.asset.byte_size} got {len(payload)}"
            )
        if sha256(payload).hexdigest() != request.asset.sha256:
            raise ProviderIntegrityError("downloaded SHA-256 mismatch")
        # Validate structure against a private temp file before install.
        descriptor, tmp_name = tempfile.mkstemp(suffix=".sqlite")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(descriptor, "wb") as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
                if not stat.S_ISREG(os.fstat(tmp.fileno()).st_mode):
                    raise ProviderIntegrityError("downloaded snapshot is not a regular file")
            self._structure_validator(request.identity, payload, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        # Canonical install: write to private tmp, fsync, atomic rename.
        canonical = self._canonical_path(request.identity)
        canonical.parent.mkdir(parents=True, exist_ok=True)
        descriptor, final_name = tempfile.mkstemp(
            suffix=".sqlite", dir=str(canonical.parent)
        )
        final_path = Path(final_name)
        try:
            with os.fdopen(descriptor, "wb") as final:
                final.write(payload)
                final.flush()
                os.fsync(final.fileno())
            os.replace(final_path, canonical)
        except OSError:
            final_path.unlink(missing_ok=True)
            raise
        self._stats.record_download()
        return payload

    def _install_lease(
        self, request: ShardRequest, payload: bytes, *, was_downloaded: bool
    ) -> ShardLease:
        with self._lock_serialise:
            descriptor, snap_name = tempfile.mkstemp(suffix=".sqlite")
            snap_path = Path(snap_name)
            try:
                with os.fdopen(descriptor, "wb") as snap:
                    snap.write(payload)
                    snap.flush()
                    os.fsync(snap.fileno())
            except Exception:
                snap_path.unlink(missing_ok=True)
                raise
            self._lease_counter += 1
            self._leases[self._lease_counter] = _PrivateSnapshot(
                path=snap_path, sha256=request.asset.sha256
            )
            self._stats.active_leases += 1
            lease = ShardLease(
                identity=request.identity,
                asset=request.asset,
                snapshot_path=snap_path,
                sha256=request.asset.sha256,
                byte_size=request.asset.byte_size,
                schema_version=request.asset.schema_version,
                was_downloaded=was_downloaded,
            )
            self._lease_to_id[lease] = self._lease_counter
            return lease

    def _quarantine(self, path: Path) -> None:
        """Move a corrupt canonical artifact to a quarantine directory."""
        quarantine = self._cache_dir / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        try:
            target = quarantine / path.name
            if target.exists():
                target.unlink(missing_ok=True)
            os.replace(path, target)
        except OSError:
            path.unlink(missing_ok=True)


def _identity_for_asset(asset: ManifestAsset) -> ShardIdentity:
    return ShardIdentity(family=asset.family, bucket=asset.bucket)


def _default_structure_validator(
    identity: ShardIdentity,
    payload: bytes,
    path: Path,
) -> None:
    """Validate that ``payload`` opens as a read-only SQLite database.

    Lookup, entry, and example shards are SQLite databases; the
    membership filter is opaque bytes that are validated by the caller
    (the online provider) against the size and digest in the manifest.
    """
    if identity.family == "membership_filter":
        # Filter is opaque bytes; SHA + size are validated by caller.
        return
    try:
        uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if row is None or str(row[0]).lower() != "ok":
                raise ProviderIntegrityError("shard integrity_check failed")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ProviderIntegrityError(
            f"shard {identity.family}/{identity.bucket} is not a valid SQLite asset: {exc}"
        ) from exc


__all__ = [
    "CacheStats",
    "ShardCache",
    "ShardIdentity",
    "ShardLease",
    "ShardRequest",
]
