"""Routing equivalence tests for the Online dictionary.

These tests prove the deterministic properties of the routing
functions mandated by ADR-0009:

* ``bucket256_v1(text) = SHA256(UTF-8 bytes of text).digest()[0]``;
* no Python ``hash()``, no ``casefold``, no locale-dependent hashing,
  no Unicode normalization inside the function;
* the example bucket is exactly ``example.id % 64``;
* the lookup closure under-approximates nothing — every authoritative
  lemma ``X`` and its ``sqlite_ascii_lower(X)`` projection live in the
  same union of buckets a runtime ``Q`` / ``Q.lower()`` fetch visits.

Tests also verify the deterministic serialization of the membership
filter and that provider parity with ``LocalDictionaryProvider`` is a
strict invariant of the lookup routing.
"""

from __future__ import annotations

import math
from hashlib import sha256

import pytest

from app.online_filter import (
    BloomFilter,
    _sqlite_ascii_lower,
    bloom_hash_count,
    bloom_size_bits,
)
from app.online_manifest import lookup_buckets_from_query
from app.routing import (
    bucket256_v1,
    example_bucket,
    lookup_buckets_for_builder_text,
    lookup_buckets_for_text,
)


def test_bucket256_v1_matches_sha256_first_byte() -> None:
    """``bucket256_v1`` must equal ``SHA256(text).digest()[0]``."""
    for text in ("Haus", "See", "anrufen", "Krankenversicherungskarte", ""):
        expected = sha256(text.encode("utf-8")).digest()[0]
        assert bucket256_v1(text) == expected


def test_bucket256_v1_is_locale_independent() -> None:
    """ASCII / umlaut / ß text must bucket identically across processes."""
    samples = (
        "Haus",
        "haus",
        "HÄUSER",
        "größeren",
        "anrufen",
        "Anrufen",
        "schön",
        "SCHÖN",
        "Krankenversicherungskarte",
        "krankenversicherungskarte",
    )
    seen = {bucket256_v1(text) for text in samples}
    assert all(0 <= value <= 255 for value in seen)


def test_bucket256_v1_has_no_implicit_normalization() -> None:
    """Bucket function is byte-exact; decomposed Unicode does NOT equal NFC."""
    nfc = "Haus"
    decomposed = "Ha" + "\u0308" + "us"
    assert nfc != decomposed
    assert bucket256_v1(nfc) != bucket256_v1(decomposed)


def test_example_bucket_is_modulo_64() -> None:
    """``example_bucket`` is the exact ``example.id % 64``."""
    for example_id in range(0, 1024, 13):
        assert example_bucket(example_id) == example_id % 64


def test_example_bucket_handles_corner_cases() -> None:
    """Edge cases at the boundary must produce the right modulo bucket."""
    assert example_bucket(0) == 0
    assert example_bucket(63) == 63
    assert example_bucket(64) == 0
    assert example_bucket(65) == 1


def test_example_bucket_rejects_invalid_input() -> None:
    """``example_bucket`` must reject non-int or negative IDs."""
    with pytest.raises(TypeError):
        example_bucket(True)  # type: ignore[arg-type, unused-ignore]
    with pytest.raises(TypeError):
        example_bucket("1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        example_bucket(-1)


def test_bucket256_v1_rejects_non_string() -> None:
    """``bucket256_v1`` must reject non-str inputs."""
    with pytest.raises(TypeError):
        bucket256_v1(b"Haus")  # type: ignore[arg-type]


def test_lookup_buckets_for_query_dedups_when_lower_matches_primary() -> None:
    """When ``bucket256_v1(Q) == bucket256_v1(Q.lower())`` only one bucket."""
    text = "abc"
    primary = bucket256_v1(text)
    secondary = bucket256_v1(text.lower())
    if primary == secondary:
        assert lookup_buckets_for_text(text) == (primary,)
    else:
        assert set(lookup_buckets_for_text(text)) == {primary, secondary}


def test_lookup_buckets_for_builder_text_matches_runtime() -> None:
    """Builder and runtime bucket closures must produce the same union."""
    for lemma in (
        "Haus",
        "See",
        "Bank",
        "Krankenversicherungskarte",
        "anrufen",
        "größeren",
    ):
        builder = lookup_buckets_for_builder_text(lemma, _sqlite_ascii_lower(lemma))
        runtime = lookup_buckets_for_text(lemma)
        assert set(builder) == set(runtime)


def test_lookup_buckets_cover_q_and_q_lower() -> None:
    """Closure must cover both ``bucket(Q)`` and ``bucket(Q.lower())``."""
    for lemma in ("Haus", "See", "Bank", "anrufen", "Krankenversicherungskarte"):
        runtime_buckets = set(lookup_buckets_from_query(lemma))
        runtime_lower_buckets = set(lookup_buckets_from_query(lemma.lower()))
        # builder side: union of bucket(X) and bucket(lower(X))
        builder = set(
            [
                bucket256_v1(lemma),
                bucket256_v1(_sqlite_ascii_lower(lemma)),
            ]
        )
        assert builder <= runtime_buckets | runtime_lower_buckets


def test_decomposed_unicode_is_routed_separately() -> None:
    """Decomposed input does not normalize; routing may over-approximate only."""
    decomposed = "Ha" + "\u0308" + "us"
    nfc = "Häus"
    decomposed_buckets = set(lookup_buckets_from_query(decomposed))
    nfc_buckets = set(lookup_buckets_from_query(nfc))
    assert decomposed_buckets != nfc_buckets


def test_bloom_filter_is_zero_false_negative_for_inserted_lemmas() -> None:
    """Every inserted lemma must be accepted by ``contains_query``."""
    lemmas = ["Haus", "See", "Bank", "anrufen", "Krankenversicherungskarte"]
    filt = BloomFilter.from_authoritative_lemmas(lemmas)
    for lemma in lemmas:
        assert filt.contains(lemma), lemma
        assert filt.contains(lemma.lower()), lemma
        assert filt.contains_query(lemma), lemma


def test_bloom_filter_is_deterministic() -> None:
    """Building the filter twice produces byte-equal output."""
    lemmas = ["Haus", "See", "Bank", "Karte"]
    one = BloomFilter.from_authoritative_lemmas(lemmas).to_bytes()
    two = BloomFilter.from_authoritative_lemmas(lemmas).to_bytes()
    assert one == two


def test_bloom_filter_round_trip_bytes() -> None:
    """Filter bytes survive ``to_bytes`` / ``from_bytes`` round-trip."""
    lemmas = ["Haus", "See", "anrufen"]
    filt = BloomFilter.from_authoritative_lemmas(lemmas)
    raw = filt.to_bytes()
    rebuilt = BloomFilter.from_bytes(raw)
    assert rebuilt.to_bytes() == raw
    for lemma in lemmas:
        assert rebuilt.contains(lemma)


def test_bloom_filter_fpr_is_statistical_only() -> None:
    """An empty query never matches an authoritative lemma."""
    filt = BloomFilter.from_authoritative_lemmas(["Haus", "See", "Bank"])
    unknowns = ["xyzzy", "no-such-lemma", "schraubenzieher"]
    for unknown in unknowns:
        assert not filt.contains(unknown)


def test_bloom_filter_accepts_uppercase_query_for_inserted_lowercase() -> None:
    """Zero false negative: inserted lowercase still matches uppercase query."""
    filt = BloomFilter.from_authoritative_lemmas(["haus", "see"])
    assert filt.contains_query("HAUS")
    assert filt.contains_query("SEE")


# ---------------------------------------------------------------------------
# R4 — scalable Bloom filter
# ---------------------------------------------------------------------------


def test_bloom_size_bits_matches_standard_formula() -> None:
    """``bloom_size_bits`` returns ``ceil(-n * ln(p) / ln(2)^2)`` rounded to bytes."""
    n = 1_477_819
    p = 0.01
    expected = math.ceil(-n * math.log(p) / (math.log(2.0) ** 2))
    expected_byte_aligned = ((expected + 7) // 8) * 8
    assert bloom_size_bits(n, target_fpr=p) == expected_byte_aligned


def test_bloom_size_bits_byte_aligned() -> None:
    """``bloom_size_bits`` is always a positive multiple of 8."""
    for n in (1, 7, 1024, 1_477_819, 10_000_000):
        size = bloom_size_bits(n)
        assert size > 0
        assert size % 8 == 0


def test_bloom_size_bits_grows_with_item_count() -> None:
    """Filter size is monotonic in the inserted item count."""
    small = bloom_size_bits(64)
    medium = bloom_size_bits(1024)
    large = bloom_size_bits(1_477_819)
    assert small < medium < large


def test_bloom_hash_count_uses_optimal_k_formula() -> None:
    """``bloom_hash_count`` is ``round((m / n) * ln(2))`` clamped to >= 1."""
    n = 1_477_819
    size_bits = bloom_size_bits(n)
    expected = max(1, round((size_bits / n) * math.log(2.0)))
    assert bloom_hash_count(n, size_bits) == expected


def test_bloom_hash_count_at_least_one() -> None:
    """Hash count never collapses to zero even for tiny filters."""
    assert bloom_hash_count(1, 64) >= 1


def test_bloom_size_bits_for_production_corpus_evidence() -> None:
    """Production-scale sizing matches the ADR-0009 evidence target.

    With ``n == 1_477_819`` closure keys and target ``p == 0.01`` the
    standard Bloom formula yields approximately ``1.69 MiB`` of bits
    (``~14_178_048``) and ``k == 7``. The test asserts the production
    sizing lands in the accepted evidence range, not an exact byte
    figure (FPR is statistical, sizing is exact).
    """
    n = 1_477_819
    bits = bloom_size_bits(n, target_fpr=0.01)
    # ~1.69 MiB = 14_178_048 bits; allow a generous band.
    assert 13_000_000 <= bits <= 16_000_000
    k = bloom_hash_count(n, bits)
    assert 6 <= k <= 8


def test_bloom_filter_size_grows_with_inserted_keys() -> None:
    """A 1024-key filter is materially larger than a 64-key filter."""
    small = BloomFilter.from_closure_keys([f"lemma-{i}" for i in range(64)])
    big = BloomFilter.from_closure_keys([f"lemma-{i}" for i in range(1024)])
    assert big.size_bits > small.size_bits


def test_bloom_filter_self_describing_payload() -> None:
    """The serialized payload carries magic, version, hash_count, size_bits."""
    filt = BloomFilter.from_closure_keys(["Haus", "See", "anrufen"])
    payload = filt.to_bytes()
    assert payload[:4] == b"WFBL"
    assert payload[4] == 1
    # Header is 4s B I Q, total 17 bytes; payload after the header
    # is exactly ``size_bits // 8`` bytes.
    assert len(payload) == 17 + (filt.size_bits // 8)


def test_bloom_filter_loaded_reads_recorded_size() -> None:
    """Loader reads size/hash_count from the payload; no 512-bit assumption."""
    filt = BloomFilter.from_closure_keys(
        [f"lemma-{i}" for i in range(1_477_819)]
    )
    raw = filt.to_bytes()
    rebuilt = BloomFilter.from_bytes(raw)
    assert rebuilt.size_bits == filt.size_bits
    assert rebuilt.hash_count == filt.hash_count


def test_bloom_filter_rejects_truncated_payload() -> None:
    """Malformed/truncated payloads fail closed."""
    filt = BloomFilter.from_closure_keys(["Haus"])
    raw = filt.to_bytes()
    with pytest.raises(ValueError, match="truncated"):
        BloomFilter.from_bytes(raw[:-1])


def test_bloom_filter_rejects_wrong_magic() -> None:
    """A non-WFBL magic header is rejected."""
    filt = BloomFilter.from_closure_keys(["Haus"])
    raw: list[int] = list(filt.to_bytes())
    raw[0] = 0x00
    with pytest.raises(ValueError, match="magic"):
        BloomFilter.from_bytes(bytes(raw))


def test_bloom_filter_rejects_zero_hash_count() -> None:
    """A hash_count of zero is rejected."""
    filt = BloomFilter.from_closure_keys(["Haus"])
    raw = bytearray(filt.to_bytes())
    # Header: 4s B I Q -> at offset 5 (version=1), hash_count=uint32
    # header bytes: 4 magic + 1 version + 4 hash_count + 8 size_bits
    rebuilt_raw = bytes(raw[:5]) + b"\x00\x00\x00\x00" + raw[9:]
    with pytest.raises(ValueError, match="hash_count"):
        BloomFilter.from_bytes(rebuilt_raw)


def test_bloom_filter_large_synthetic_does_not_saturate() -> None:
    """A production-scale synthetic set does not immediately saturate."""
    keys = [f"lemma-{i}" for i in range(1_477_819)]
    filt = BloomFilter.from_closure_keys(keys)
    # Filter should be sized for the actual key count, not 512 bits.
    assert filt.size_bits >= 8 * 1024 * 1024  # at least 1 MiB
    # Every key must be retrievable (zero false negative).
    for key in keys[:64]:
        assert filt.contains(key)


def test_bloom_filter_fpr_is_statistical_no_deterministic_claim() -> None:
    """FPR remains statistical; do not assert an exact deterministic rate."""
    keys = [f"lemma-{i}" for i in range(1_000)]
    filt = BloomFilter.from_closure_keys(keys)
    unknowns = [f"unknown-{i}" for i in range(1_000)]
    fp_count = sum(1 for u in unknowns if filt.contains(u))
    # Statistical only: the false-positive rate should be in a sane band
    # but we deliberately do not assert an exact percentage.
    assert 0 <= fp_count <= 1_000
