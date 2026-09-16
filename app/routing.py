"""Deterministic shard-routing helpers for the Online dictionary corpus.

ADR-0009 mandates one routing function for the Online lookup family and one
exact integer-modulo routing for the example family:

    bucket256_v1(text) = SHA256(UTF-8 bytes of text).digest()[0]  (0..255)
    example_bucket(example_id) = example_id % 64                   (0..63)

Both functions are pure and locale-independent. The lookup function performs
no casefold, no Unicode normalization, and no Python ``hash()``; the example
function is an integer modulo and never depends on textual representation.
"""

from __future__ import annotations

from hashlib import sha256


def bucket256_v1(text: str) -> int:
    """Return the deterministic lookup bucket for ``text`` `` ``[0..255]``.

    The bucket is the first byte of ``SHA256(text.encode("utf-8"))`` treated
    as an unsigned integer. Two exact-equal inputs always produce the same
    bucket; the function intentionally performs no normalization of any kind
    so that lookup routing can over-approximate across multiple
    representations placed at the builder.
    """
    if not isinstance(text, str):
        raise TypeError("bucket256_v1 requires a str input")
    digest = sha256(text.encode("utf-8")).digest()
    return digest[0]


def example_bucket(example_id: int) -> int:
    """Return the example shard bucket for an authoritative ``example.id``.

    The bucket is ``example_id % 64``. ``example.id`` is an internal
    active-asset routing identity only — never durable PART-B identity.
    """
    if not isinstance(example_id, int) or isinstance(example_id, bool):
        raise TypeError("example_bucket requires an int example_id")
    if example_id < 0:
        raise ValueError("example_id must be non-negative")
    return example_id % 64


def lookup_buckets_for_text(text: str) -> tuple[int, ...]:
    """Return the over-approximating bucket set for a runtime query ``text``.

    For every authoritative lookup row ``X`` the builder places that row in
    the union of ``bucket256_v1(X)`` and ``bucket256_v1(sqlite_ascii_lower(X))``
    (deduplicated when equal). This function returns the symmetric union for
    a runtime query: ``bucket256_v1(Q)`` union ``bucket256_v1(Q.lower())``,
    deduplicated. The runtime then applies the exact lookup predicate on the
    fetched candidates.
    """
    primary = bucket256_v1(text)
    secondary = bucket256_v1(text.lower())
    if primary == secondary:
        return (primary,)
    return (primary, secondary)


def lookup_buckets_for_builder_text(text: str, sqlite_ascii_lower: str) -> tuple[int, ...]:
    """Return the deduplicated bucket set for one authoritative lookup row.

    Mirrors the closure rule for builder placement: union
    ``bucket256_v1(text)`` and ``bucket256_v1(sqlite_ascii_lower(text))``,
    deduplicated when equal. ``sqlite_ascii_lower`` is the SQLite built-in
    ``lower()`` projection of ``text``; the caller is responsible for using
    the same value as ``Dictionary.lookup_exact`` does.
    """
    if sqlite_ascii_lower is None:
        raise ValueError("sqlite_ascii_lower must be a string")
    primary = bucket256_v1(text)
    secondary = bucket256_v1(sqlite_ascii_lower)
    if primary == secondary:
        return (primary,)
    return (primary, secondary)


__all__ = [
    "bucket256_v1",
    "example_bucket",
    "lookup_buckets_for_text",
    "lookup_buckets_for_builder_text",
]