"""Deterministic membership filter for the Online dictionary.

ADR-0009 requires a Bloom-style membership accelerator over the
authoritative lemma set that has:

* zero false negatives for runtime checks against ``Q`` and
  ``Q.lower()``;
* a statistical false-positive rate (no deterministic guarantee);
* deterministic construction so the same authoritative input yields
  the same filter bytes.

The builder inserts each authoritative lemma ``X`` under the closure
rule from :func:`app.online_manifest.lookup_buckets_from_query`: both
``bucket256_v1(X)`` and ``bucket256_v1(sqlite_ascii_lower(X))`` are
inserted. Runtime checks probe both ``Q`` and ``Q.lower()``.

The filter is sized dynamically from the actual unique closure-key
count using the standard Bloom formulas:

    m = ceil(-n * ln(p) / (ln(2)^2))
    k = max(1, round((m / n) * ln(2)))

with target false-positive rate ``p = 0.01``. Hash positions are
derived from a deterministic SHA-256 double-hashing scheme:

    position_i = (h1 + i * h2) % m   for i = 0 .. k-1

The serialized payload is self-describing (magic, version, size_bits,
hash_count, then the bit payload), so the loader reads the actual
parameters and never assumes a hardcoded production size. Malformed
or truncated payloads fail closed.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256

# Magic + version header is a fixed 8-byte prefix.
_BLOOM_MAGIC: bytes = b"WFBL"
_BLOOM_VERSION: int = 1
_BLOOM_HEADER_FORMAT: str = ">4sBIQ"  # magic, version, hash_count, size_bits
_BLOOM_HEADER_LEN: int = struct.calcsize(_BLOOM_HEADER_FORMAT)

_TARGET_FALSE_POSITIVE_RATE: float = 0.01
_LN2: float = math.log(2.0)
_LN2_SQUARED: float = _LN2 * _LN2


def bloom_size_bits(n: int, target_fpr: float = _TARGET_FALSE_POSITIVE_RATE) -> int:
    """Return the deterministic bit-array size for ``n`` keys at ``target_fpr``.

    Rounded up to the next whole byte so the bit array is byte-aligned.
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("n must be an int")
    if n <= 0:
        raise ValueError("n must be positive")
    if not (0.0 < target_fpr < 1.0):
        raise ValueError("target_fpr must be in (0, 1)")
    raw = -float(n) * math.log(float(target_fpr)) / _LN2_SQUARED
    bits = int(math.ceil(raw))
    # Round up to whole bytes; an empty filter is still at least one byte.
    byte_aligned = ((bits + 7) // 8) * 8
    return max(byte_aligned, 8)


def bloom_hash_count(n: int, size_bits: int) -> int:
    """Return the optimal Bloom hash count for ``n`` keys in ``size_bits`` bits."""
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("n must be an int")
    if not isinstance(size_bits, int) or isinstance(size_bits, bool):
        raise ValueError("size_bits must be an int")
    if n <= 0 or size_bits <= 0:
        raise ValueError("n and size_bits must be positive")
    return max(1, int(round((float(size_bits) / float(n)) * _LN2)))


def _hash_positions(text: str, size_bits: int) -> tuple[int, int]:
    """Return two deterministic integer hash positions for ``text``.

    Uses SHA-256-derived 64-bit integer halves. ``h1`` and ``h2`` are
    independent 64-bit big-endian integers. ``position_i`` is computed
    via the standard Bloom double-hashing formula:

        position_i = (h1 + i * h2) % size_bits
    """
    digest = sha256(text.encode("utf-8")).digest()
    h1 = int.from_bytes(digest[0:8], "big", signed=False)
    h2 = (
        int.from_bytes(digest[8:16], "big", signed=False) | 1
    )  # ensure odd for double-hash coverage
    p1 = h1 % size_bits
    p2 = (h1 + h2) % size_bits
    return p1, p2


def _positions(text: str, size_bits: int, hash_count: int) -> list[int]:
    """Return ``hash_count`` deterministic positions in ``[0, size_bits)``.

    Two positions are produced from one SHA-256 digest; subsequent
    positions are generated via ``(h1 + i*h2) % size_bits``.
    """
    digest = sha256(text.encode("utf-8")).digest()
    h1 = int.from_bytes(digest[0:8], "big", signed=False)
    h2 = int.from_bytes(digest[8:16], "big", signed=False) | 1
    out: list[int] = []
    for i in range(hash_count):
        out.append((h1 + i * h2) % size_bits)
    return out


def _sqlite_ascii_lower(value: str) -> str:
    """Reproduce SQLite built-in ``lower()`` for ASCII-oriented text.

    The Online corpus indexes each lemma ``X`` under both
    ``bucket256_v1(X)`` and ``bucket256_v1(sqlite_ascii_lower(X))``.
    SQLite ``lower()`` lowers ASCII A-Z to a-z and passes all other code
    points through unchanged; non-ASCII German letters are therefore NOT
    lowered by SQLite.
    """
    return "".join(
        ch.lower() if "A" <= ch <= "Z" else ch
        for ch in value
    )


@dataclass(frozen=True, slots=True)
class BloomFilter:
    """Immutable membership filter exposing deterministic serialization."""

    bits: bytearray
    size_bits: int
    hash_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.size_bits, int) or isinstance(self.size_bits, bool):
            raise ValueError("size_bits must be an int")
        if self.size_bits <= 0 or self.size_bits % 8 != 0:
            raise ValueError("size_bits must be a positive multiple of 8")
        if len(self.bits) * 8 < self.size_bits:
            raise ValueError("bits buffer is smaller than size_bits")
        if not isinstance(self.hash_count, int) or isinstance(self.hash_count, bool):
            raise ValueError("hash_count must be an int")
        if self.hash_count <= 0:
            raise ValueError("hash_count must be positive")

    @classmethod
    def empty(
        cls,
        size_bits: int = 512,
        *,
        hash_count: int | None = None,
    ) -> "BloomFilter":
        """Construct an empty filter of the given size and hash count."""
        if hash_count is None:
            hash_count = 7
        return cls(bits=bytearray(size_bits // 8), size_bits=size_bits, hash_count=hash_count)

    @classmethod
    def from_closure_keys(
        cls,
        keys: Iterable[str],
        *,
        target_fpr: float = _TARGET_FALSE_POSITIVE_RATE,
    ) -> "BloomFilter":
        """Construct a filter by inserting each key under its own hash.

        ``keys`` is the deduplicated closure-key set the builder produces:
        for each authoritative lemma ``X``, both ``X`` and
        ``sqlite_ascii_lower(X)`` are inserted, then deduplicated. The
        filter size is computed from the actual unique key count, not a
        hardcoded production figure.
        """
        unique_keys: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            unique_keys.append(key)
        n = len(unique_keys)
        size_bits = bloom_size_bits(n, target_fpr=target_fpr)
        hash_count = bloom_hash_count(n, size_bits)
        bits = bytearray(size_bits // 8)
        for key in unique_keys:
            for pos in _positions(key, size_bits, hash_count):
                bits[pos // 8] |= 1 << (pos % 8)
        return cls(bits=bits, size_bits=size_bits, hash_count=hash_count)

    @classmethod
    def from_authoritative_lemmas(
        cls,
        lemmas: Iterable[str],
        *,
        size_bits: int = 512,
    ) -> "BloomFilter":
        """Construct a filter by inserting each lemma under both forms.

        Kept as a back-compat helper for fixtures that pass only lemma
        texts. The filter is sized for the deduplicated closure-key set
        (``X`` and ``sqlite_ascii_lower(X)`` per lemma) and uses a
        deterministic hash count. ``size_bits`` is ignored; the filter
        is sized from the actual key count.
        """
        return cls.from_closure_keys(
            (key for lemma in lemmas for key in (lemma, _sqlite_ascii_lower(lemma)))
        )

    def contains(self, text: str) -> bool:
        """Return ``True`` when the filter reports membership for ``text``.

        A ``False`` result means "definitely not a member". A ``True``
        result is a probabilistic false-positive candidate; it is never a
        false negative for any text inserted via
        :meth:`from_closure_keys` /
        :meth:`from_authoritative_lemmas`.
        """
        digest = sha256(text.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[0:8], "big", signed=False)
        h2 = int.from_bytes(digest[8:16], "big", signed=False) | 1
        for i in range(self.hash_count):
            pos = (h1 + i * h2) % self.size_bits
            byte = self.bits[pos // 8]
            if not (byte & (1 << (pos % 8))):
                return False
        return True

    def contains_query(self, query: str) -> bool:
        """Return ``True`` if the filter accepts either ``Q`` or ``Q.lower()``."""
        return self.contains(query) or self.contains(query.lower())

    def to_bytes(self) -> bytes:
        """Return the deterministic self-describing filter payload."""
        if len(self.bits) * 8 < self.size_bits:
            raise ValueError("bits buffer too small for size_bits")
        payload_bytes = bytes(self.bits[: self.size_bits // 8])
        header = struct.pack(
            _BLOOM_HEADER_FORMAT,
            _BLOOM_MAGIC,
            _BLOOM_VERSION,
            self.hash_count,
            self.size_bits,
        )
        return header + payload_bytes

    @classmethod
    def from_bytes(cls, payload: bytes) -> "BloomFilter":
        """Construct a filter from a previously-serialized payload.

        Fails closed on truncated, malformed, or version-mismatched
        payloads. Reads the actual ``size_bits`` and ``hash_count``
        recorded in the header; never assumes a hardcoded production
        size.
        """
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise ValueError("payload must be bytes-like")
        raw = bytes(payload)
        if len(raw) < _BLOOM_HEADER_LEN:
            raise ValueError("payload too short for Bloom header")
        magic, version, hash_count, size_bits = struct.unpack(
            _BLOOM_HEADER_FORMAT, raw[:_BLOOM_HEADER_LEN]
        )
        if magic != _BLOOM_MAGIC:
            raise ValueError(f"invalid Bloom magic: {magic!r}")
        if version != _BLOOM_VERSION:
            raise ValueError(f"unsupported Bloom version: {version}")
        if size_bits <= 0 or size_bits % 8 != 0:
            raise ValueError(f"invalid size_bits: {size_bits}")
        if hash_count <= 0:
            raise ValueError(f"invalid hash_count: {hash_count}")
        expected_payload_len = size_bits // 8
        if len(raw) - _BLOOM_HEADER_LEN < expected_payload_len:
            raise ValueError("payload truncated relative to declared size_bits")
        bits = bytearray(raw[_BLOOM_HEADER_LEN : _BLOOM_HEADER_LEN + expected_payload_len])
        return cls(bits=bits, size_bits=size_bits, hash_count=hash_count)


__all__ = [
    "BloomFilter",
    "_sqlite_ascii_lower",
    "bloom_hash_count",
    "bloom_size_bits",
]
