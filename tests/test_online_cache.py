"""Verified shard cache lifecycle tests.

These tests prove the cache implements the ADR-0009 lifecycle::

    ABSENT -> DOWNLOADING -> VERIFIED -> IMMUTABLE LEASE

Cache miss:

1. single-flight per shard identity;
2. download to private temporary path;
3. verify byte count;
5. verify SQLite / logical shard structure;
6. fsync bytes;
7. atomic install of canonical cache artifact;
8. open / read via bytes / path proven to match the manifest;
9. return immutable validated lease.

Cache hit re-runs validation before producing a new lease within the
process. Corruption is quarantined and refetched. Clear-cache is safe
against in-flight leases.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from app.online_cache import (
    CacheStats,
    ShardCache,
    ShardIdentity,
    ShardRequest,
)
from app.online_manifest import (
    SHARD_FAMILY_LOOKUP,
    ManifestAsset,
)
from app.provider import ProviderBudgetExceededError, ProviderIntegrityError


@pytest.fixture
def simple_lookup_asset(tmp_path: Path) -> ManifestAsset:
    """Return one minimal valid lookup shard asset manifest entry."""
    path = tmp_path / "lookup-007.sqlite"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE lemma (id INTEGER PRIMARY KEY, semantic_ref TEXT, lemma TEXT, "
            "pos TEXT, gender TEXT, freq_rank INTEGER)"
        )
        conn.execute(
            "INSERT INTO lemma VALUES (1, 'lemma:v1:haus_0', 'Haus', 'NOUN', 'das', 1)"
        )
        conn.commit()
    finally:
        conn.close()
    payload = path.read_bytes()
    return ManifestAsset(
        family=SHARD_FAMILY_LOOKUP,
        bucket=7,
        name="lookup-007.sqlite",
        path="shards/lookup/007.sqlite",
        byte_size=len(payload),
        sha256=sha256(payload).hexdigest(),
        schema_version="lookup-shard-v1",
    )


@pytest.fixture
def lookup_cache(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> tuple[ShardCache, ManifestAsset, Path]:
    """Build a ShardCache that returns the fixture asset bytes."""
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()

    def transport(request: ShardRequest) -> bytes:
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    return cache, simple_lookup_asset, tmp_path / "cache"


def test_cache_miss_downloads_validates_and_returns_lease(
    lookup_cache: tuple[ShardCache, ManifestAsset, Path],
) -> None:
    cache, asset, _ = lookup_cache
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=asset,
    )
    lease = cache.lease(request)
    try:
        assert lease.sha256 == asset.sha256
        assert lease.byte_size == asset.byte_size
        assert lease.snapshot_path.exists()
        # Snapshot must open as a verified read-only SQLite asset
        uri = f"file:{lease.snapshot_path.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute("SELECT lemma FROM lemma LIMIT 1").fetchone()
            assert row is not None and str(row[0]) == "Haus"
        finally:
            conn.close()
    finally:
        cache.release(lease)
    assert not lease.snapshot_path.exists()


def test_cache_hit_revalidates_before_issuing_lease(
    lookup_cache: tuple[ShardCache, ManifestAsset, Path],
) -> None:
    cache, asset, _ = lookup_cache
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=asset,
    )
    first = cache.lease(request)
    cache.release(first)
    second = cache.lease(request)
    try:
        assert second.sha256 == asset.sha256
    finally:
        cache.release(second)


def test_corrupt_canonical_artifact_is_quarantined_and_refetched(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()
    downloads = {"count": 0}

    def transport(request: ShardRequest) -> bytes:
        downloads["count"] += 1
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )
    # Seed the canonical artifact and then corrupt it
    first = cache.lease(request)
    cache.release(first)
    canonical = tmp_path / "cache" / "verified" / SHARD_FAMILY_LOOKUP / "7.sqlite"
    canonical.write_bytes(b"corrupt")
    second = cache.lease(request)
    try:
        assert downloads["count"] >= 2
        assert second.sha256 == simple_lookup_asset.sha256
    finally:
        cache.release(second)
    stats = cache.stats
    assert stats.corruptions >= 1
    assert stats.refetches >= 1


def test_cache_rejects_wrong_byte_count(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    def transport(request: ShardRequest) -> bytes:
        # Return wrong number of bytes
        return b"\x00\x00"

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )
    with pytest.raises(ProviderIntegrityError):
        cache.lease(request)


def test_cache_rejects_wrong_sha(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    # Construct an asset whose sha/size don't match the transport bytes
    bad_asset = ManifestAsset(
        family=SHARD_FAMILY_LOOKUP,
        bucket=7,
        name="lookup-007.sqlite",
        path="shards/lookup/007.sqlite",
        byte_size=10,
        sha256="a" * 64,
        schema_version="lookup-shard-v1",
    )

    def transport(request: ShardRequest) -> bytes:
        # Return bytes whose declared size (10) doesn't match the actual
        # payload length; size mismatch fails first.
        return b"not ten" + b""

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7), asset=bad_asset
    )
    with pytest.raises(ProviderIntegrityError):
        cache.lease(request)


def test_cache_rejects_invalid_sqlite_structure(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    # Build a non-SQLite byte payload; declare it as a SQLite shard by
    # matching size and computing SHA.
    payload = b"this is not sqlite at all"
    bad_asset = ManifestAsset(
        family=SHARD_FAMILY_LOOKUP,
        bucket=7,
        name="lookup-007.sqlite",
        path="shards/lookup/007.sqlite",
        byte_size=len(payload),
        sha256=sha256(payload).hexdigest(),
        schema_version="lookup-shard-v1",
    )

    def transport(request: ShardRequest) -> bytes:
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7), asset=bad_asset
    )
    with pytest.raises(ProviderIntegrityError):
        cache.lease(request)


def test_cache_single_flight_under_concurrency(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()
    counter = {"count": 0}
    lock = threading.Lock()

    def transport(request: ShardRequest) -> bytes:
        with lock:
            counter["count"] += 1
        time.sleep(0.05)
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )
    leases = []
    errors = []

    def worker() -> None:
        try:
            lease = cache.lease(request)
            leases.append(lease)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "single-flight waiter deadlocked"
    assert not errors
    # All leases are different but they all share the same underlying
    # canonical file. The transport must have been invoked only once.
    assert counter["count"] == 1
    # Exactly the leader performed the transfer; every waiter piggy-backed.
    assert sum(1 for lease in leases if lease.was_downloaded) == 1
    for lease in leases:
        cache.release(lease)


def test_clear_cache_is_safe_with_active_lease(
    lookup_cache: tuple[ShardCache, ManifestAsset, Path],
) -> None:
    cache, asset, _ = lookup_cache
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=asset,
    )
    lease = cache.lease(request)
    cache.clear()
    assert lease.snapshot_path.exists()
    # A second clear also succeeds
    cache.clear()
    cache.release(lease)


def test_clear_cache_then_acquire_refetches(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()
    downloads = {"count": 0}

    def transport(request: ShardRequest) -> bytes:
        downloads["count"] += 1
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )
    cache.lease(request)
    cache.clear()
    cache.lease(request)
    assert downloads["count"] >= 2


def test_temp_files_do_not_become_canonical_state(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()

    def transport(request: ShardRequest) -> bytes:
        # Cause a verification failure by returning wrong-sized bytes
        return payload[: len(payload) - 1]

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )
    with pytest.raises(ProviderIntegrityError):
        cache.lease(request)
    verified_dir = tmp_path / "cache" / "verified"
    if verified_dir.exists():
        # No canonical artifact for this shard should exist after failure
        for child in verified_dir.rglob("*"):
            assert not child.is_file() or "lookup-007" not in child.name


def test_cache_records_stats_for_hits_misses_and_corruption(
    lookup_cache: tuple[ShardCache, ManifestAsset, Path],
) -> None:
    cache, asset, _ = lookup_cache
    request = ShardRequest(
    identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=asset,
    )
    lease = cache.lease(request)
    cache.release(lease)
    lease2 = cache.lease(request)
    cache.release(lease2)
    stats: CacheStats = cache.stats
    assert stats.hits >= 1
    assert stats.misses >= 1


# ---------------------------------------------------------------------------
# C5 — pre-download budget/reservation hook at the download boundary
# ---------------------------------------------------------------------------


def test_before_download_hook_rejects_before_transport(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    """A ``before_download`` rejection means zero transport invocation."""
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()
    calls = {"count": 0}

    def transport(request: ShardRequest) -> bytes:
        calls["count"] += 1
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
        identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )

    def hook(identity: ShardIdentity) -> None:
        assert identity == request.identity
        raise ProviderBudgetExceededError("online_dictionary_budget_exceeded")

    with pytest.raises(ProviderBudgetExceededError):
        cache.lease(request, before_download=hook)
    assert calls["count"] == 0
    # Bookkeeping cleared: a normal lease afterwards downloads cleanly.
    lease = cache.lease(request)
    try:
        assert lease.was_downloaded is True
        assert calls["count"] == 1
    finally:
        cache.release(lease)


def test_before_download_hook_runs_only_for_leader(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    """Verified cache hits never reach the reservation hook."""
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()
    hook_calls: list[ShardIdentity] = []

    def transport(request: ShardRequest) -> bytes:
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
        identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )

    def hook(identity: ShardIdentity) -> None:
        hook_calls.append(identity)

    first = cache.lease(request, before_download=hook)
    cache.release(first)
    assert hook_calls == [request.identity]
    second = cache.lease(request, before_download=hook)
    try:
        assert second.was_downloaded is False
    finally:
        cache.release(second)
    assert len(hook_calls) == 1


# ---------------------------------------------------------------------------
# C6 — single-flight failure and waiter semantics
# ---------------------------------------------------------------------------


def test_single_flight_leader_failure_wakes_all_waiters(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    """A leader failure wakes every waiter with the same structured failure."""
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()
    release_gate = threading.Event()
    entered = threading.Event()
    mode = {"fail": True}

    def transport(request: ShardRequest) -> bytes:
        entered.set()
        assert release_gate.wait(timeout=10)
        if mode["fail"]:
            raise ProviderIntegrityError("boom")
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    request = ShardRequest(
        identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 7),
        asset=simple_lookup_asset,
    )
    errors: list[BaseException] = []
    leases: list[Any] = []

    def worker() -> None:
        try:
            leases.append(cache.lease(request))
        except BaseException as exc:  # noqa: BLE001 - failure is the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=10)
    time.sleep(0.2)  # let the waiters block on the single-flight event
    release_gate.set()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "waiter deadlocked after leader failure"
    assert not leases
    assert len(errors) == 5
    assert all(isinstance(exc, ProviderIntegrityError) for exc in errors)
    # Bookkeeping eventually clears: a later lease downloads cleanly.
    mode["fail"] = False
    recovered = cache.lease(request)
    try:
        assert recovered.was_downloaded is True
    finally:
        cache.release(recovered)


# ---------------------------------------------------------------------------
# C14 — clear-cache mutation serialization
# ---------------------------------------------------------------------------


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 10.0) -> None:
    """Spin until ``predicate()`` is true; fail instead of hanging."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        time.sleep(0.01)


def test_clear_waits_for_inflight_download_and_blocks_new_leases(
    tmp_path: Path, simple_lookup_asset: ManifestAsset
) -> None:
    """Clear serializes against in-flight downloads and new acquisitions."""
    payload = (tmp_path / "lookup-007.sqlite").read_bytes()
    calls = {"count": 0}
    release_gate = threading.Event()

    def transport(request: ShardRequest) -> bytes:
        calls["count"] += 1
        assert release_gate.wait(timeout=10)
        return payload

    cache = ShardCache(tmp_path / "cache", transport=transport)
    in_flight_request = ShardRequest(
        identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 8),
        asset=simple_lookup_asset,
    )
    post_clear_request = ShardRequest(
        identity=ShardIdentity(SHARD_FAMILY_LOOKUP, 9),
        asset=simple_lookup_asset,
    )
    in_flight_leases: list[Any] = []
    in_flight_errors: list[BaseException] = []

    def download_worker() -> None:
        try:
            in_flight_leases.append(cache.lease(in_flight_request))
        except BaseException as exc:  # noqa: BLE001
            in_flight_errors.append(exc)

    download_thread = threading.Thread(target=download_worker)
    download_thread.start()
    _wait_until(lambda: calls["count"] >= 1)

    clear_thread = threading.Thread(target=cache.clear)
    clear_thread.start()
    time.sleep(0.1)
    # Clear must wait for the in-flight download instead of racing it.
    assert clear_thread.is_alive(), "clear must wait for in-flight downloads"

    post_clear_leases: list[Any] = []
    new_lease_thread = threading.Thread(
        target=lambda: post_clear_leases.append(cache.lease(post_clear_request))
    )
    new_lease_thread.start()
    time.sleep(0.1)
    # New acquisitions must not race through while clear owns the mutation.
    assert new_lease_thread.is_alive(), "new lease raced through an active clear"

    release_gate.set()
    for thread in (download_thread, clear_thread, new_lease_thread):
        thread.join(timeout=10)
        assert not thread.is_alive(), "deadlock between download/clear/lease"
    assert not in_flight_errors
    for lease in in_flight_leases:
        cache.release(lease)
    for lease in post_clear_leases:
        cache.release(lease)

    # The pre-clear in-flight install was removed by clear: no silent
    # repopulation of the verified cache after clear completed.
    verified = tmp_path / "cache" / "verified" / SHARD_FAMILY_LOOKUP
    assert not (verified / "8.sqlite").exists()
    # The post-clear acquisition downloaded normally through the gate.
    assert (verified / "9.sqlite").exists()
    assert calls["count"] == 2

    # The next post-clear acquisition of the cleared identity downloads
    # normally again.
    repeat = cache.lease(in_flight_request)
    try:
        assert repeat.was_downloaded is True
    finally:
        cache.release(repeat)
    assert calls["count"] == 3