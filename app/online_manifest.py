"""Trusted Online dictionary manifest parsing.

ADR-0009 fixes the Online corpus to 256 lookup shards, 256 entry shards,
64 example shards, and one membership filter (577 assets total). The
manifest is the single source of truth for the corpus identity; the
provider refuses to operate against any other source.

The committed ``release/dictionary-online-manifest-v2.json`` is a
schema-shaped fixture for the Slice 11 acceptance suite. It is **not** a
production asset manifest.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.routing import bucket256_v1

SHARD_FAMILY_LOOKUP: str = "lookup"
SHARD_FAMILY_ENTRY: str = "entry"
SHARD_FAMILY_EXAMPLE: str = "example"
SHARD_FAMILY_FILTER: str = "membership_filter"

VALID_FAMILIES: frozenset[str] = frozenset(
    {SHARD_FAMILY_LOOKUP, SHARD_FAMILY_ENTRY, SHARD_FAMILY_EXAMPLE, SHARD_FAMILY_FILTER}
)

LOOKUP_FAMILY_SIZE: int = 256
ENTRY_FAMILY_SIZE: int = 256
EXAMPLE_FAMILY_SIZE: int = 64
FILTER_FAMILY_SIZE: int = 1

TOTAL_ASSETS: int = (
    LOOKUP_FAMILY_SIZE + ENTRY_FAMILY_SIZE + EXAMPLE_FAMILY_SIZE + FILTER_FAMILY_SIZE
)

DEFAULT_DATASET_TOKEN: str = (
    "1698b9979099098bf8d6e6fd7f9194134a927d428e3c2b1905a626eb8ee67d4c"
)
MANIFEST_SCHEMA_VERSION: str = "online-manifest-v1"

_HEX64_RE = re.compile(r"^[0-9a-f]{64}\Z")
_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]+\Z")
_PATH_RE = re.compile(r"^[A-Za-z0-9._\-/]+\Z")


class ManifestError(ValueError):
    """Raised when an online manifest fails validation."""


@dataclass(frozen=True, slots=True)
class ManifestAsset:
    """One entry in the Online manifest."""

    family: str
    bucket: int
    name: str
    path: str
    byte_size: int
    sha256: str
    schema_version: str = ""

    def __post_init__(self) -> None:
        if self.family not in VALID_FAMILIES:
            raise ManifestError(f"unknown shard family: {self.family!r}")
        if not isinstance(self.bucket, int) or isinstance(self.bucket, bool):
            raise ManifestError(f"bucket must be an int, got {type(self.bucket).__name__}")
        if self.bucket < 0:
            raise ManifestError("bucket must be non-negative")
        if not _NAME_RE.fullmatch(self.name):
            raise ManifestError(f"invalid asset name: {self.name!r}")
        if not _PATH_RE.fullmatch(self.path):
            raise ManifestError(f"invalid asset path: {self.path!r}")
        if self.path != self.path.strip() or ".." in self.path.split("/"):
            raise ManifestError(f"path traversal forbidden: {self.path!r}")
        if self.path.startswith("/") or self.path.endswith("/"):
            raise ManifestError(f"asset path must be relative: {self.path!r}")
        if not isinstance(self.byte_size, int) or isinstance(self.byte_size, bool):
            raise ManifestError(f"byte_size must be an int, got {type(self.byte_size).__name__}")
        if self.byte_size < 0:
            raise ManifestError("byte_size must be non-negative")
        if not isinstance(self.sha256, str) or not _HEX64_RE.fullmatch(self.sha256):
            raise ManifestError(
                f"sha256 must be a 64-character lowercase hex string: {self.sha256!r}"
            )

    def expected_filename(self) -> str:
        """Return the canonical on-disk filename for this asset."""
        return self.name


@dataclass(frozen=True, slots=True)
class TrustedDistribution:
    """Approved Product distribution configuration."""

    base_origin: str
    release_tag: str
    redirect_policy: str = "github_release_redirect_only"

    def __post_init__(self) -> None:
        if not isinstance(self.base_origin, str) or not self.base_origin:
            raise ManifestError("base_origin must be a non-empty string")
        if not self.base_origin.startswith("https://"):
            raise ManifestError("base_origin must use https scheme")
        parsed = _parse_origin(self.base_origin)
        if parsed is None:
            raise ManifestError(f"invalid base_origin: {self.base_origin!r}")
        if getattr(parsed, "username", None) or getattr(parsed, "password", None):
            raise ManifestError("base_origin must not contain userinfo")
        if parsed.path not in ("", "/"):
            raise ManifestError("base_origin must not include a path")
        if not isinstance(self.release_tag, str) or not self.release_tag:
            raise ManifestError("release_tag must be a non-empty string")
        if self.redirect_policy != "github_release_redirect_only":
            raise ManifestError(
                f"unsupported redirect_policy: {self.redirect_policy!r}"
            )


@dataclass(frozen=True, slots=True)
class OnlineManifest:
    """Validated immutable view of one Online dictionary manifest."""

    dataset_token: str
    schema_version: str
    distribution: TrustedDistribution
    assets: tuple[ManifestAsset, ...]
    filter_family_size: int = FILTER_FAMILY_SIZE
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def lookup_assets(self) -> tuple[ManifestAsset, ...]:
        """Return only the lookup-shard assets."""
        return tuple(a for a in self.assets if a.family == SHARD_FAMILY_LOOKUP)

    @property
    def entry_assets(self) -> tuple[ManifestAsset, ...]:
        """Return only the entry-shard assets."""
        return tuple(a for a in self.assets if a.family == SHARD_FAMILY_ENTRY)

    @property
    def example_assets(self) -> tuple[ManifestAsset, ...]:
        """Return only the example-shard assets."""
        return tuple(a for a in self.assets if a.family == SHARD_FAMILY_EXAMPLE)

    @property
    def filter_assets(self) -> tuple[ManifestAsset, ...]:
        """Return only the membership-filter assets."""
        return tuple(a for a in self.assets if a.family == SHARD_FAMILY_FILTER)


def _parse_origin(url: str) -> Any | None:
    """Parse ``url`` into a stdlib ``SplitResult`` or return ``None``."""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return parsed


def _validate_dataset_token(value: Any) -> str:
    """Validate a v2 logical dataset token."""
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise ManifestError(f"dataset_token must be a 64-character lowercase hex string: {value!r}")
    return value


def _validate_schema_version(value: Any) -> str:
    """Validate the manifest's schema version string."""
    if not isinstance(value, str) or not value.strip():
        raise ManifestError("schema_version must be a non-empty string")
    return value.strip()


def _validate_distribution(value: Any) -> TrustedDistribution:
    """Validate the trusted distribution configuration."""
    if not isinstance(value, Mapping):
        raise ManifestError("distribution must be an object")
    try:
        return TrustedDistribution(
            base_origin=str(value["base_origin"]),
            release_tag=str(value["release_tag"]),
            redirect_policy=str(value.get("redirect_policy", "github_release_redirect_only")),
        )
    except KeyError as exc:
        raise ManifestError(f"distribution missing required field: {exc.args[0]}") from exc


def _validate_assets(value: Any) -> tuple[ManifestAsset, ...]:
    """Validate the asset list and assert the fixed family sizes."""
    if not isinstance(value, list):
        raise ManifestError("assets must be a list")
    assets: list[ManifestAsset] = []
    seen_identities: set[tuple[str, int]] = set()
    seen_paths: set[str] = set()
    family_buckets: dict[str, set[int]] = {
        SHARD_FAMILY_LOOKUP: set(),
        SHARD_FAMILY_ENTRY: set(),
        SHARD_FAMILY_EXAMPLE: set(),
        SHARD_FAMILY_FILTER: set(),
    }
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ManifestError("each asset entry must be an object")
        try:
            asset = ManifestAsset(
                family=str(raw["family"]),
                bucket=int(raw["bucket"]),
                name=str(raw["name"]),
                path=str(raw["path"]),
                byte_size=int(raw["byte_size"]),
                sha256=str(raw["sha256"]),
                schema_version=str(raw.get("schema_version", "")),
            )
        except KeyError as exc:
            raise ManifestError(f"asset missing required field: {exc.args[0]}") from exc
        identity = (asset.family, asset.bucket)
        if identity in seen_identities:
            raise ManifestError(
                f"duplicate asset identity: family={asset.family} bucket={asset.bucket}"
            )
        if asset.path in seen_paths:
            raise ManifestError(f"duplicate asset path: {asset.path}")
        seen_identities.add(identity)
        seen_paths.add(asset.path)
        family_buckets[asset.family].add(asset.bucket)
        assets.append(asset)

    expected: dict[str, set[int]] = {
        SHARD_FAMILY_LOOKUP: set(range(LOOKUP_FAMILY_SIZE)),
        SHARD_FAMILY_ENTRY: set(range(ENTRY_FAMILY_SIZE)),
        SHARD_FAMILY_EXAMPLE: set(range(EXAMPLE_FAMILY_SIZE)),
        SHARD_FAMILY_FILTER: set(range(FILTER_FAMILY_SIZE)),
    }
    for family, expected_buckets in expected.items():
        actual = family_buckets[family]
        if actual != expected_buckets:
            missing = sorted(expected_buckets - actual)
            extra = sorted(actual - expected_buckets)
            raise ManifestError(
                f"family {family!r} bucket set mismatch: missing={missing} extra={extra}"
            )
    if len(assets) != TOTAL_ASSETS:
        raise ManifestError(
            f"manifest declares {len(assets)} assets, expected exactly {TOTAL_ASSETS}"
        )
    return tuple(assets)


def parse_manifest(value: str | bytes | Mapping[str, Any]) -> OnlineManifest:
    """Parse and validate one manifest payload."""
    if isinstance(value, (str, bytes)):
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"manifest is not valid JSON: {exc.msg}") from exc
    else:
        data = value
    if not isinstance(data, Mapping):
        raise ManifestError("manifest root must be an object")

    dataset_token = _validate_dataset_token(data.get("dataset_token"))
    schema_version = _validate_schema_version(data.get("schema_version"))
    distribution = _validate_distribution(data.get("distribution"))
    assets = _validate_assets(data.get("assets"))

    return OnlineManifest(
        dataset_token=dataset_token,
        schema_version=schema_version,
        distribution=distribution,
        assets=assets,
        source=MappingProxyType(dict(data)),
    )


def load_manifest(path: Path | str) -> OnlineManifest:
    """Load and validate an online manifest file from disk."""
    text = Path(path).read_text()
    return parse_manifest(text)


def manifest_hash(manifest: OnlineManifest) -> str:
    """Compute the canonical deterministic digest of a validated manifest."""
    canonical = {
        "dataset_token": manifest.dataset_token,
        "schema_version": manifest.schema_version,
        "distribution": {
            "base_origin": manifest.distribution.base_origin,
            "release_tag": manifest.distribution.release_tag,
            "redirect_policy": manifest.distribution.redirect_policy,
        },
        "assets": sorted(
            (
                {
                    "family": asset.family,
                    "bucket": asset.bucket,
                    "name": asset.name,
                    "path": asset.path,
                    "byte_size": asset.byte_size,
                    "sha256": asset.sha256,
                    "schema_version": asset.schema_version,
                }
                for asset in manifest.assets
            ),
            key=lambda item: (item["family"], item["bucket"]),
        ),
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def expected_bucket_for_lookup(text: str) -> int:
    """Return the deterministic lookup bucket for one normalized lookup text."""
    return bucket256_v1(text)


def lookup_buckets_from_query(query: str) -> tuple[int, ...]:
    """Return the deduplicated lookup buckets for one runtime query.

    Mirror of :func:`app.routing.lookup_buckets_for_text`; retained here so
    the manifest module documents the closure rule alongside the family
    identities.
    """
    primary = bucket256_v1(query)
    secondary = bucket256_v1(query.lower())
    if primary == secondary:
        return (primary,)
    return (primary, secondary)


def validate_buckets_for_text(
    rows: Iterable[tuple[str, str]],
    *,
    expected_family_size: int,
) -> None:
    """Validate that every ``(X, sqlite_ascii_lower(X))`` is bucket-closed.

    Helper used by the builder. ``rows`` yields ``(X, sqlite_ascii_lower)``
    pairs in the exact placement order. Raises :class:`ManifestError` on the
    first row whose union of buckets exceeds the family size.
    """
    family_size = int(expected_family_size)
    if family_size <= 0 or family_size > 256:
        raise ManifestError(f"unsupported family size: {family_size}")
    for raw_x, raw_lower in rows:
        b1 = bucket256_v1(raw_x)
        b2 = bucket256_v1(raw_lower)
        for bucket in (b1, b2):
            if bucket < 0 or bucket >= family_size:
                raise ManifestError(
                    f"bucket {bucket} out of range [0,{family_size}) for text {raw_x!r}"
                )


__all__ = [
    "DEFAULT_DATASET_TOKEN",
    "ENTRY_FAMILY_SIZE",
    "EXAMPLE_FAMILY_SIZE",
    "FILTER_FAMILY_SIZE",
    "LOOKUP_FAMILY_SIZE",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestAsset",
    "ManifestError",
    "OnlineManifest",
    "SHARD_FAMILY_ENTRY",
    "SHARD_FAMILY_EXAMPLE",
    "SHARD_FAMILY_FILTER",
    "SHARD_FAMILY_LOOKUP",
    "TOTAL_ASSETS",
    "TrustedDistribution",
    "VALID_FAMILIES",
    "expected_bucket_for_lookup",
    "load_manifest",
    "lookup_buckets_from_query",
    "manifest_hash",
    "parse_manifest",
    "validate_buckets_for_text",
]