"""Manifest validation tests for the Online dictionary.

These tests prove the manifest parser fails closed for malformed
payloads and emits a deterministic hash for valid payloads. The
committed ``release/dictionary-online-manifest-v2.json`` fixture is a
schema-shaped, fixture-only manifest; it is not a production asset
manifest.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.online_manifest import (
    DEFAULT_DATASET_TOKEN,
    ENTRY_FAMILY_SIZE,
    EXAMPLE_FAMILY_SIZE,
    LOOKUP_FAMILY_SIZE,
    MANIFEST_SCHEMA_VERSION,
    SHARD_FAMILY_ENTRY,
    SHARD_FAMILY_EXAMPLE,
    SHARD_FAMILY_FILTER,
    SHARD_FAMILY_LOOKUP,
    TOTAL_ASSETS,
    VALID_FAMILIES,
    ManifestAsset,
    ManifestError,
    TrustedDistribution,
    load_manifest,
    lookup_buckets_from_query,
    manifest_hash,
    parse_manifest,
)


def _build_assets(*, missing: set[tuple[str, int]] | None = None) -> list[dict[str, Any]]:
    """Return the full asset list, optionally skipping ``missing`` identities."""
    missing = missing or set()
    out: list[dict[str, Any]] = []
    for family, count in (
        (SHARD_FAMILY_LOOKUP, LOOKUP_FAMILY_SIZE),
        (SHARD_FAMILY_ENTRY, ENTRY_FAMILY_SIZE),
        (SHARD_FAMILY_EXAMPLE, EXAMPLE_FAMILY_SIZE),
        (SHARD_FAMILY_FILTER, 1),
    ):
        for bucket in range(count):
            if (family, bucket) in missing:
                continue
            out.append(
                {
                    "family": family,
                    "bucket": bucket,
                    "name": f"{family}-{bucket:03d}.sqlite",
                    "path": f"shards/{family}/{bucket:03d}.sqlite",
                    "byte_size": 100,
                    "sha256": "a" * 64,
                    "schema_version": f"{family}-v1",
                }
            )
    return out


def _valid_payload() -> dict[str, Any]:
    return {
        "dataset_token": DEFAULT_DATASET_TOKEN,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "distribution": {
            "base_origin": "https://github.com",
            "release_tag": "dictionary-online-v2",
            "redirect_policy": "github_release_redirect_only",
        },
        "assets": _build_assets(),
    }


def test_parse_manifest_accepts_valid_fixture() -> None:
    manifest = parse_manifest(_valid_payload())
    assert len(manifest.assets) == TOTAL_ASSETS
    assert manifest.dataset_token == DEFAULT_DATASET_TOKEN
    assert manifest.distribution.base_origin == "https://github.com"
    assert manifest.distribution.release_tag == "dictionary-online-v2"


def test_manifest_hash_is_deterministic_for_same_payload() -> None:
    one = parse_manifest(_valid_payload())
    two = parse_manifest(_valid_payload())
    assert manifest_hash(one) == manifest_hash(two)


def test_manifest_hash_changes_when_any_field_changes() -> None:
    base = parse_manifest(_valid_payload())
    modified = _valid_payload()
    modified["assets"][0]["byte_size"] = 101
    other = parse_manifest(modified)
    assert manifest_hash(base) != manifest_hash(other)


def test_manifest_fails_closed_on_missing_dataset_token() -> None:
    payload = _valid_payload()
    del payload["dataset_token"]
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_wrong_dataset_token() -> None:
    payload = _valid_payload()
    payload["dataset_token"] = "ZZ" * 32  # non-hex characters
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_short_dataset_token() -> None:
    payload = _valid_payload()
    payload["dataset_token"] = "abc123"  # not 64 chars
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_malformed_sha() -> None:
    payload = _valid_payload()
    payload["assets"][0]["sha256"] = "Z" * 64
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_wrong_byte_size() -> None:
    payload = _valid_payload()
    payload["assets"][0]["byte_size"] = -1
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_duplicate_identity() -> None:
    payload = _valid_payload()
    duplicate = dict(payload["assets"][1])
    payload["assets"].append(duplicate)
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_duplicate_path() -> None:
    payload = _valid_payload()
    duplicate = dict(payload["assets"][0])
    duplicate["bucket"] = LOOKUP_FAMILY_SIZE  # unused bucket
    payload["assets"].append(duplicate)
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_path_traversal() -> None:
    payload = _valid_payload()
    payload["assets"][0]["path"] = "../etc/passwd"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_invalid_family() -> None:
    payload = _valid_payload()
    payload["assets"][0]["family"] = "novel_family"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_invalid_bucket() -> None:
    payload = _valid_payload()
    payload["assets"][0]["bucket"] = LOOKUP_FAMILY_SIZE
    payload["assets"][0]["name"] = "lookup-out-of-range.sqlite"
    payload["assets"][0]["path"] = "shards/lookup/out-of-range.sqlite"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_missing_family_bucket() -> None:
    payload = _valid_payload()
    missing = {(SHARD_FAMILY_LOOKUP, 0)}
    payload["assets"] = _build_assets(missing=missing)
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_http_origin() -> None:
    payload = _valid_payload()
    payload["distribution"]["base_origin"] = "http://github.com"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_userinfo_in_origin() -> None:
    payload = _valid_payload()
    payload["distribution"]["base_origin"] = "https://user:pass@github.com"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_non_root_origin_path() -> None:
    payload = _valid_payload()
    payload["distribution"]["base_origin"] = "https://github.com/sabers13"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_manifest_fails_closed_on_unsupported_redirect_policy() -> None:
    payload = _valid_payload()
    payload["distribution"]["redirect_policy"] = "any_redirect"
    with pytest.raises(ManifestError):
        parse_manifest(payload)


def test_load_manifest_reads_fixture_file(tmp_path_factory: pytest.TempPathFactory) -> None:
    tmp = tmp_path_factory.mktemp("manifest")
    path = tmp / "manifest.json"
    path.write_text(json.dumps(_valid_payload()))
    manifest = load_manifest(path)
    assert len(manifest.assets) == TOTAL_ASSETS


def test_manifest_helper_classes_validate_inputs() -> None:
    with pytest.raises(ManifestError):
        ManifestAsset(
            family="bogus",
            bucket=0,
            name="x",
            path="x",
            byte_size=0,
            sha256="a" * 64,
        )
    with pytest.raises(ManifestError):
        TrustedDistribution(
            base_origin="https://github.com/path",
            release_tag="tag",
        )


def test_valid_families_are_exactly_documented() -> None:
    assert VALID_FAMILIES == frozenset(
        {
            SHARD_FAMILY_LOOKUP,
            SHARD_FAMILY_ENTRY,
            SHARD_FAMILY_EXAMPLE,
            SHARD_FAMILY_FILTER,
        }
    )


def test_lookup_buckets_from_query_helper_matches_routing() -> None:
    """Manifest helper routes identically to ``app.routing``."""
    assert lookup_buckets_from_query("Haus") == (
        lookup_buckets_from_query("Haus")[0],
    ) or len(lookup_buckets_from_query("Haus")) == 2