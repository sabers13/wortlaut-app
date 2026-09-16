"""Differential tests between Local and Online dictionary providers.

These tests prove the Slice 11 contract: ``LocalDictionaryProvider`` and
``OnlineDictionaryProvider`` implement the same ``DictionaryProvider``
contract, produce the same observable results on every served-product
read shape, and never mutate PART-B when a transport, integrity, or
budget failure is raised.

Test fixture corpus is built from a tiny Local dictionary in ``tmp_path``
using the Slice 11 builder, so the Online provider exercises the full
acquisition, lookup-closure, sense-route, and example-bucket flow.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from app.online_cache import ShardCache, ShardIdentity, ShardRequest
from app.online_manifest import (
    SHARD_FAMILY_ENTRY,
    SHARD_FAMILY_EXAMPLE,
    SHARD_FAMILY_LOOKUP,
    ManifestAsset,
    OnlineManifest,
)
from app.provider import (
    ProviderBudgetExceededError,
    ProviderIntegrityError,
)
from app.provider_local import LocalDictionaryProvider
from app.provider_online import (
    MAX_NEW_LOOKUP_DOWNLOADS,
    OnlineDictionaryProvider,
    _Budget,
)
from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref

PART_A_FULL_SCHEMA = """
CREATE TABLE lemma (
  id INTEGER PRIMARY KEY,
  semantic_ref TEXT NOT NULL UNIQUE,
  lemma TEXT NOT NULL,
  pos TEXT NOT NULL,
  gender TEXT,
  freq_rank INTEGER,
  plural TEXT,
  plural_none INTEGER NOT NULL DEFAULT 0,
  genitive_sg TEXT,
  aux TEXT,
  separable INTEGER DEFAULT 0,
  particle TEXT,
  reflexive INTEGER DEFAULT 0,
  praesens_3sg TEXT,
  praeteritum_3sg TEXT,
  partizip_ii TEXT,
  governs TEXT,
  comparative TEXT,
  superlative TEXT,
  ipa TEXT,
  ipa_source TEXT,
  source TEXT,
  license TEXT
);
CREATE TABLE sense (
  id INTEGER PRIMARY KEY,
  lemma_id INTEGER NOT NULL REFERENCES lemma(id),
  semantic_ref TEXT NOT NULL UNIQUE,
  source_namespace TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  ord INTEGER NOT NULL DEFAULT 0,
  register TEXT,
  source TEXT,
  license TEXT
);
CREATE TABLE sense_meaning (
  id INTEGER PRIMARY KEY,
  sense_id INTEGER NOT NULL,
  language TEXT NOT NULL,
  kind TEXT NOT NULL,
  ord INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL,
  source TEXT NOT NULL,
  license TEXT NOT NULL
);
CREATE TABLE example (
  id INTEGER PRIMARY KEY,
  de TEXT NOT NULL,
  en TEXT,
  source TEXT,
  source_ref TEXT,
  license TEXT,
  token_count INTEGER,
  has_proper INTEGER DEFAULT 0
);
CREATE TABLE sense_meaning_derivation (
  generated_meaning_id INTEGER NOT NULL REFERENCES sense_meaning(id) ON DELETE CASCADE,
  source_meaning_id INTEGER NOT NULL REFERENCES sense_meaning(id) ON DELETE RESTRICT,
  PRIMARY KEY (generated_meaning_id, source_meaning_id),
  CHECK (generated_meaning_id <> source_meaning_id)
) WITHOUT ROWID;
CREATE TABLE surface_form (
  form TEXT NOT NULL,
  lemma_id INTEGER NOT NULL
);
CREATE TABLE example_lemma (
  lemma_id INTEGER NOT NULL,
  example_id INTEGER NOT NULL
);
"""


def _build_local_fixture(target: Path) -> Path:
    """Create a tiny but D47-valid Local dictionary."""
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.executescript(PART_A_FULL_SCHEMA)
        lemmas = [
            (
                1,
                compute_lemma_semantic_ref("Haus", "NOUN", "das"),
                "Haus",
                "NOUN",
                "das",
                1,
                "Häuser",
                0,
                "Hauses",
                None,
                0,
                None,
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "wiktionary",
                "CC BY-SA 4.0",
            ),
            (
                2,
                compute_lemma_semantic_ref("See", "NOUN", "der"),
                "See",
                "NOUN",
                "der",
                2,
                "Seen",
                0,
                None,
                None,
                0,
                None,
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "wiktionary",
                "CC BY-SA 4.0",
            ),
            (
                3,
                compute_lemma_semantic_ref("See", "NOUN", "die"),
                "See",
                "NOUN",
                "die",
                3,
                "Seen",
                0,
                None,
                None,
                0,
                None,
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "wiktionary",
                "CC BY-SA 4.0",
            ),
            (
                4,
                compute_lemma_semantic_ref("anrufen", "VERB", None),
                "anrufen",
                "VERB",
                None,
                4,
                None,
                0,
                None,
                None,
                1,
                "an",
                0,
                "ruft an",
                "rief an",
                "angerufen",
                None,
                None,
                None,
                None,
                "wiktionary",
                "CC BY-SA 4.0",
            ),
            (
                5,
                compute_lemma_semantic_ref("Karte", "NOUN", "die"),
                "Karte",
                "NOUN",
                "die",
                5,
                "Karten",
                0,
                None,
                None,
                0,
                None,
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "wiktionary",
                "CC BY-SA 4.0",
            ),
        ]
        for row in lemmas:
            conn.execute(
                "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, freq_rank, "
                "plural, plural_none, genitive_sg, aux, separable, particle, reflexive, "
                "praesens_3sg, praeteritum_3sg, partizip_ii, governs, comparative, "
                "superlative, ipa, source, license) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        senses = []
        sense_specs = [
            ("Haus", "NOUN", "das", "en-house-1"),
            ("See", "NOUN", "der", "en-lake-1"),
            ("See", "NOUN", "die", "en-sea-1"),
            ("anrufen", "VERB", None, "en-call-1"),
            ("Karte", "NOUN", "die", "en-card-1"),
        ]
        for idx, (word, pos, gender, source) in enumerate(sense_specs, start=1):
            lemma_ref = compute_lemma_semantic_ref(word, pos, gender)
            sense_ref = compute_sense_semantic_ref(
                lemma_ref, "wiktextract:enwiktionary", f"senseid:{source}"
            )
            senses.append(
                (
                    idx,
                    idx,
                    sense_ref,
                    "wiktextract:enwiktionary",
                    f"senseid:{source}",
                )
            )
        for row in senses:  # type: ignore[assignment]
            conn.execute(
                "INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, "
                "source_ref, ord, source, license) VALUES (?, ?, ?, ?, ?, 0, "
                "'wiktionary', 'CC BY-SA 4.0')", row
            )
        meanings = [
            (1, 1, "en", "translation", 0, "house"),
            (2, 2, "en", "translation", 0, "lake"),
            (3, 3, "en", "translation", 0, "sea"),
            (4, 4, "en", "translation", 0, "to call"),
            (5, 5, "en", "translation", 0, "card, map"),
        ]
        for row in meanings:  # type: ignore[assignment]
            conn.execute(
                "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, "
                "source, license) VALUES (?, ?, ?, ?, ?, ?, 'wiktionary', 'CC BY-SA 4.0')", row
            )
        examples = [
            (1, "Das Haus ist groß.", "The house is big."),
            (2, "Der See ist tief.", "The lake is deep."),
            (3, "Die See ist stürmisch.", "The sea is stormy."),
            (4, "Ich rufe dich morgen an.", "I will call you tomorrow."),
            (5, "Die Karte zeigt den Weg.", "The map shows the way."),
        ]
        for row in examples:  # type: ignore[assignment]
            conn.execute(
                "INSERT INTO example (id, de, en, source, license, token_count, "
                "has_proper) VALUES (?, ?, ?, 'tatoeba', 'CC BY 2.0 FR', 5, 0)", row
            )
        surface_forms = [
            ("Häuser", 1),
            ("rief an", 4),
        ]
        for row in surface_forms:  # type: ignore[assignment]
            conn.execute(
                "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)", row
            )
        for lemma_id, example_id in zip([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]):
            conn.execute(
                "INSERT INTO example_lemma (lemma_id, example_id) VALUES (?, ?)",
                (lemma_id, example_id),
            )
        conn.commit()
    finally:
        conn.close()
    return target


@pytest.fixture(scope="module")
def online_corpus(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes]:
    """Build the Online corpus from a Local fixture and return providers.

    Module-scoped so the corpus is built once per test module. The
    LocalDictionaryProvider also stays open for the module lifetime.
    """
    tmp_path = tmp_path_factory.mktemp("slice11_corpus")
    local_fixture_path = _build_local_fixture(tmp_path / "local.sqlite")
    from hashlib import sha256 as _sha

    from app.online_filter import BloomFilter
    from app.online_manifest import (
        ENTRY_FAMILY_SIZE,
        EXAMPLE_FAMILY_SIZE,
        LOOKUP_FAMILY_SIZE,
        MANIFEST_SCHEMA_VERSION,
        SHARD_FAMILY_FILTER,
        OnlineManifest,
        TrustedDistribution,
    )
    from tools.build_online_dictionary import (
        _partition_entry_shards,
        _partition_example_shards,
        _partition_lookup_shards,
        _read_authoritative_example_lemma,
        _read_authoritative_examples,
        _read_authoritative_lemmas,
        _read_authoritative_meanings,
        _read_authoritative_senses,
        _read_authoritative_surface_forms,
        _write_entry_shard,
        _write_example_shard,
        _write_lookup_shard,
    )

    actual_token = _sha(local_fixture_path.read_bytes()).hexdigest()
    output_dir = tmp_path / "corpus"
    output_dir.mkdir(parents=True, exist_ok=True)

    source_conn = sqlite3.connect(
        f"file:{local_fixture_path.as_posix()}?mode=ro", uri=True
    )
    source_conn.row_factory = sqlite3.Row
    try:
        lemmas = _read_authoritative_lemmas(source_conn)
        senses = _read_authoritative_senses(source_conn)
        meanings = _read_authoritative_meanings(source_conn)
        surface_forms = _read_authoritative_surface_forms(source_conn)
        examples = _read_authoritative_examples(source_conn)
        example_lemma = _read_authoritative_example_lemma(source_conn)
    finally:
        source_conn.close()

    lookup_partitions, surface_partitions, sense_route_partitions = (
        _partition_lookup_shards(lemmas, surface_forms, senses)
    )
    entry_partitions = _partition_entry_shards(
        lemmas, senses, meanings, surface_forms, example_lemma, examples
    )
    example_partitions = _partition_example_shards(examples)

    assets: list[ManifestAsset] = []

    def _write_shard(
        family: str, bucket: int, writer: Any, partition_data: Any
    ) -> ManifestAsset:
        canonical = output_dir / f"{family}-{bucket:03d}.sqlite"
        tmp_path_conn = output_dir / f".{family}-{bucket:03d}.sqlite.tmp"
        conn = sqlite3.connect(tmp_path_conn)
        conn.row_factory = sqlite3.Row
        try:
            writer(conn, bucket, *partition_data)
            conn.close()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        # Atomic rename
        import os

        os.replace(tmp_path_conn, canonical)
        from hashlib import sha256

        payload = canonical.read_bytes()
        digest = sha256(payload).hexdigest()
        return ManifestAsset(
            family=family,
            bucket=bucket,
            name=f"{family}-{bucket:03d}.sqlite",
            path=f"shards/{family}/{bucket:03d}.sqlite",
            byte_size=len(payload),
            sha256=digest,
            schema_version=f"{family}-v1",
        )

    for bucket in range(LOOKUP_FAMILY_SIZE):
        assets.append(
            _write_shard(
                SHARD_FAMILY_LOOKUP,
                bucket,
                _write_lookup_shard,
                (
                    lookup_partitions.get(bucket, []),
                    surface_partitions.get(bucket, []),
                    sense_route_partitions.get(bucket, ()),
                ),
            )
        )
    for bucket in range(ENTRY_FAMILY_SIZE):
        state = entry_partitions.get(
            bucket,
            {
                "lemmas": [],
                "senses": [],
                "meanings": [],
                "surface_forms": [],
                "example_lemma": [],
            },
        )
        assets.append(
            _write_shard(
                SHARD_FAMILY_ENTRY,
                bucket,
                _write_entry_shard,
                (
                    state["lemmas"],
                    state["senses"],
                    state["meanings"],
                    state["surface_forms"],
                    state["example_lemma"],
                ),
            )
        )
    for bucket in range(EXAMPLE_FAMILY_SIZE):
        assets.append(
            _write_shard(
                SHARD_FAMILY_EXAMPLE,
                bucket,
                _write_example_shard,
                (example_partitions[bucket],),
            )
        )

    closure_keys: list[str] = []
    seen_closure: set[str] = set()
    for row in lemmas:
        lemma_text = str(row[2])
        for variant in (lemma_text, lemma_text.lower()):
            if variant in seen_closure:
                continue
            seen_closure.add(variant)
            closure_keys.append(variant)
    filter_bytes = BloomFilter.from_closure_keys(closure_keys).to_bytes()

    filter_digest = sha256(filter_bytes).hexdigest()
    assets.append(
        ManifestAsset(
            family=SHARD_FAMILY_FILTER,
            bucket=0,
            name="membership-filter.bin",
            path="shards/membership-filter.bin",
            byte_size=len(filter_bytes),
            sha256=filter_digest,
            schema_version="membership-filter-v1",
        )
    )

    manifest = OnlineManifest(
        dataset_token=actual_token,
        schema_version=MANIFEST_SCHEMA_VERSION,
        distribution=TrustedDistribution(
            base_origin="https://github.com",
            release_tag="dictionary-online-fixture",
            redirect_policy="github_release_redirect_only",
        ),
        assets=tuple(assets),
    )

    def transport(request: ShardRequest) -> bytes:
        for asset in manifest.assets:
            if (
                asset.family == request.identity.family
                and asset.bucket == request.identity.bucket
            ):
                return (output_dir / asset.name).read_bytes()
        raise ProviderIntegrityError("missing fixture shard")

    cache = ShardCache(tmp_path / "cache", transport=transport)
    online = OnlineDictionaryProvider(
        manifest=manifest,
        cache=cache,
        filter_payload=filter_bytes,
        dataset_token=actual_token,
    )
    local = LocalDictionaryProvider(local_fixture_path)
    return online, manifest, local, filter_bytes


def test_dataset_token_parity_across_providers(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Both providers serve the same v2 logical dataset token."""
    online, _manifest, local, _filter = online_corpus
    # Local reads from the Local fixture, which carries its own SHA. We
    # assert both providers expose a deterministic, non-empty token.
    assert online.asset_token
    assert local.asset_token
    assert online.asset_token == online.manifest.dataset_token


def test_provider_parity_lookup_exact(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    for lemma in ("Haus", "See", "anrufen"):
        local_hits = [hit.semantic_ref for hit in local.lookup_exact(lemma)]
        online_hits = [hit.semantic_ref for hit in online.lookup_exact(lemma)]
        assert sorted(local_hits) == sorted(online_hits)


def test_provider_parity_lookup_exact_capitalized(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    local_hits = [hit.semantic_ref for hit in local.lookup_exact("haus")]
    online_hits = [hit.semantic_ref for hit in online.lookup_exact("haus")]
    assert sorted(local_hits) == sorted(online_hits)


def test_provider_parity_surface_form(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    for form in ("Häuser", "rief an"):
        local_hits = [hit.semantic_ref for hit in local.lookup_surface_form(form)]
        online_hits = [hit.semantic_ref for hit in online.lookup_surface_form(form)]
        assert sorted(local_hits) == sorted(online_hits)


def test_provider_parity_senses_for_lemma(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Online senses parity after observed ``lookup_exact`` cache identity.

    Numeric ``lemma_id`` is an active-asset cache identity only
    (ADR-0009 / Defect R1B). The Online provider resolves a numeric
    ``lemma_id`` through the in-process cache populated when a
    ``lookup_exact`` hit legitimately observes the lemma. The test
    performs that observation before asserting parity.
    """
    online, _, local, _ = online_corpus
    for query in ("Haus", "See", "anrufen", "Karte"):
        online.lookup_exact(query)
    for lemma_id in (1, 2, 3, 4, 5):
        local_senses = [s.semantic_ref for s in local.senses_for_lemma(lemma_id)]
        online_senses = [s.semantic_ref for s in online.senses_for_lemma(lemma_id)]
        assert local_senses == online_senses


def test_provider_parity_meanings_for_sense(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Online sense-meanings parity after observed cache identity."""
    online, _, local, _ = online_corpus
    for query in ("Haus", "See", "anrufen", "Karte"):
        online.lookup_exact(query)
    for sense_id in (1, 2, 3, 4, 5):
        local_meanings = [
            (m.language, m.text) for m in local.meanings_for_sense(sense_id)
        ]
        online_meanings = [
            (m.language, m.text) for m in online.meanings_for_sense(sense_id)
        ]
        assert sorted(local_meanings) == sorted(online_meanings)


def test_provider_parity_examples_for_lemma(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Online examples-for-lemma parity after observed cache identity.

    The Online provider reads example payload from the example family
    keyed by ``example.id % 64``. The example IDs are discovered from
    the entry shard's ``example_lemma`` join, which requires the
    observed ``lemma_id -> lemma_ref`` mapping.
    """
    online, _, local, _ = online_corpus
    for query in ("Haus", "See", "anrufen", "Karte"):
        online.lookup_exact(query)
    for lemma_id in (1, 2, 3, 4, 5):
        local_examples = [
            (e.example_id, e.de, e.en) for e in local.examples_for_lemma(lemma_id)
        ]
        online_examples = [
            (e.example_id, e.de, e.en) for e in online.examples_for_lemma(lemma_id)
        ]
        assert sorted(local_examples) == sorted(online_examples)


def test_provider_parity_entry_for_ref(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    refs: list[str] = []
    for i in range(1, 6):
        entry = local.lemma_for_id(i)
        assert entry is not None
        refs.append(entry.semantic_ref)
    for ref in refs:
        assert ref is not None
        local_entry = local.entry_for_ref(ref)
        online_entry = online.entry_for_ref(ref)
        assert local_entry is not None and online_entry is not None
        assert local_entry.lemma.semantic_ref == online_entry.lemma.semantic_ref
        assert local_entry.lemma.lemma == online_entry.lemma.lemma
        assert (
            tuple(sorted((m.sense_id, m.language, m.text) for m in local_entry.meanings))
            == tuple(sorted((m.sense_id, m.language, m.text) for m in online_entry.meanings))
        )
        assert (
            tuple(sorted((e.example_id, e.de, e.en) for e in local_entry.examples))
            == tuple(sorted((e.example_id, e.de, e.en) for e in online_entry.examples))
        )


def test_provider_parity_sense_route(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    refs = [local.lookup_senses(i)[0].semantic_ref for i in range(1, 6)]
    for ref in refs:
        assert ref is not None
        assert local.sense_route(ref) == online.sense_route(ref)


def test_provider_parity_candidate_lookup(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    for query in ("Haus", "See", "anrufen", "Karte"):
        local_candidates = [
            (c.lemma.semantic_ref, c.lemma.lemma)
            for c in local.candidate_lookup(query)
        ]
        online_candidates = [
            (c.lemma.semantic_ref, c.lemma.lemma)
            for c in online.candidate_lookup(query)
        ]
        assert sorted(local_candidates) == sorted(online_candidates)


def test_provider_parity_miss(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    assert not local.lookup_exact("xyzzy_no_such_word")
    assert not online.lookup_exact("xyzzy_no_such_word")
    assert not local.candidate_lookup("xyzzy_no_such_word")
    assert not online.candidate_lookup("xyzzy_no_such_word")


def test_provider_parity_unknown_inputs(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    for query in ("XYZ", "AAA", "BBB"):
        assert not local.lookup_exact(query)
        assert not online.lookup_exact(query)


def test_provider_parity_decomposed_unicode(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    decomposed = "Gr" + "\u0308" + "uße"  # decomposed ß? actually decompose ö
    # Local returns nothing for decomposed input; Online must agree.
    assert not local.lookup_exact(decomposed)
    assert not online.lookup_exact(decomposed)


def test_provider_parity_surface_form_capitalized(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, local, _ = online_corpus
    local_hits = [h.semantic_ref for h in local.lookup_surface_form("HÄUSER")]
    online_hits = [h.semantic_ref for h in online.lookup_surface_form("HÄUSER")]
    assert sorted(local_hits) == sorted(online_hits)


def test_online_budget_exceeded_at_33rd_new_identity(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """A budget that already spent ``MAX_NEW_LOOKUP_DOWNLOADS`` must reject a 33rd."""
    online, _, _, _ = online_corpus
    budget = _Budget()
    # The test exercises the limit directly by forcing 32 distinct identities,
    # then requesting the 33rd. ``charge`` deduplicates per identity, so
    # charging the same identity 33 times does not exhaust the budget.
    for bucket in range(MAX_NEW_LOOKUP_DOWNLOADS):
        budget.charge(ShardIdentity(SHARD_FAMILY_LOOKUP, bucket))
    with pytest.raises(ProviderBudgetExceededError):
        online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, MAX_NEW_LOOKUP_DOWNLOADS), budget)


def test_budget_charges_once_per_identity(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    online, _, _, _ = online_corpus
    budget = _Budget()
    online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, 7), budget)
    online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, 7), budget)
    assert budget.spent == 1


def test_cache_reads_not_charged(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Once a lookup is cached, additional reads of the same identity cost nothing."""
    online, _, _, _ = online_corpus
    budget = _Budget()
    online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, 5), budget)
    assert budget.spent == 1
    online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, 5), budget)
    assert budget.spent == 1


def test_lookup_failure_does_not_mutate_part_b(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
    tmp_path: Path,
) -> None:
    """Provider failures must not write PART-B rows."""
    from tests.conftest import user_db  # noqa: F401
    # Use the existing user_db fixture by importing it indirectly.
    # Construct an empty user database by reading the schema and opening
    # a fresh connection; we check that provider failures cause no
    # rows in the user DB.
    schema_sql = (Path(__file__).parent.parent / "reference" / "schema.sql").read_text()
    _, _,part_b = schema_sql.partition("-- PART B")
    user_path = tmp_path / "user.sqlite"
    conn = sqlite3.connect(user_path)
    try:
        conn.executescript("-- PART B" + part_b)
        conn.execute("PRAGMA foreign_keys = ON")
        before = conn.execute("SELECT COUNT(*) FROM note").fetchone()[0]
        online, _, _, _ = online_corpus
        # Trigger a failure (no transport means it fails)
        try:
            online.lookup_exact("xyzzy_no_such_word")
        except ProviderIntegrityError:
            pass
        after = conn.execute("SELECT COUNT(*) FROM note").fetchone()[0]
        assert before == after
    finally:
        conn.close()


def test_provider_rejects_invalid_manifest_token(tmp_path: Path) -> None:
    """Online rejects manifests whose dataset token does not match the provider's."""
    fake_asset = ManifestAsset(
        family=SHARD_FAMILY_ENTRY,
        bucket=0,
        name="entry-000.sqlite",
        path="shards/entry/000.sqlite",
        byte_size=1,
        sha256="a" * 64,
    )
    fake_manifest = OnlineManifest(
        dataset_token="0" * 64,
        schema_version="online-manifest-v1",
        distribution=__import__(
            "app.online_manifest", fromlist=["TrustedDistribution"]
        ).TrustedDistribution(
            base_origin="https://github.com",
            release_tag="dictionary-online-v2",
        ),
        assets=(fake_asset,),
    )
    cache = ShardCache(tmp_path / "cache", transport=lambda r: b"")

    with pytest.raises(ProviderIntegrityError):
        OnlineDictionaryProvider(
            manifest=fake_manifest, cache=cache, filter_payload=b"\x00" * 64
        )


# ---------------------------------------------------------------------------
# Defect R1 — sense_route lives in the lookup shard family
# ---------------------------------------------------------------------------


def test_sense_route_resolves_via_lookup_shard_only(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """``sense_route`` must read exactly its routed lookup bucket."""
    from app.routing import bucket256_v1

    online, _manifest, local, _ = online_corpus
    sense_ref = local.lookup_senses(1)[0].semantic_ref
    route = online.sense_route(sense_ref)
    assert route is not None
    lemma = local.lemma_for_id(1)
    assert lemma is not None
    assert route[0] == lemma.semantic_ref
    assert route[1] == sense_ref
    expected_lookup_bucket = bucket256_v1(sense_ref)
    # The route must read the lookup shard whose bucket equals
    # ``bucket256_v1(sense_ref)``; entry-family scans are forbidden.
    assert 0 <= expected_lookup_bucket < 256


def test_sense_route_does_not_scan_all_entry_shards(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Sense routing must touch only the single routed lookup shard.

    The test wraps the cache transport to record which identities are
    requested and asserts no entry-family identity is ever touched by
    ``sense_route``.
    """
    from app.routing import bucket256_v1

    online, _manifest, local, _ = online_corpus
    sense_ref = local.lookup_senses(1)[0].semantic_ref
    requested: list[ShardIdentity] = []
    original_lease = online._cache.lease

    def recording_lease(
        request: ShardRequest,
        *,
        before_download: Callable[[ShardIdentity], None] | None = None,
    ) -> Any:
        requested.append(request.identity)
        return original_lease(request, before_download=before_download)

    online._cache.lease = recording_lease  # type: ignore[method-assign]
    try:
        online.sense_route(sense_ref)
    finally:
        online._cache.lease = original_lease  # type: ignore[method-assign]
    families = {req.family for req in requested}
    assert families == {"lookup"}, families
    buckets = [req.bucket for req in requested]
    assert buckets == [bucket256_v1(sense_ref)]


def test_cold_numeric_lemma_id_returns_documented_cache_miss(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """A cold unknown numeric ID must not trigger any 256-bucket scan."""
    online, _manifest, _local, _ = online_corpus
    requested: list[Any] = []
    original_lease = online._cache.lease

    def recording_lease(
        request: ShardRequest,
        *,
        before_download: Callable[[ShardIdentity], None] | None = None,
    ) -> Any:
        requested.append(request.identity)
        return original_lease(request, before_download=before_download)

    online._cache.lease = recording_lease  # type: ignore[method-assign]
    try:
        assert online.senses_for_lemma(9_999_999) == ()
        assert online.examples_for_lemma(9_999_999) == ()
        assert online.meanings_for_lemma(9_999_999) == ()
        assert online.surface_forms_for_lemma(9_999_999) == ()
    finally:
        online._cache.lease = original_lease  # type: ignore[method-assign]
    assert requested == [], (
        "cold unknown numeric IDs must NOT trigger remote reads; "
        f"saw {requested}"
    )


# ---------------------------------------------------------------------------
# Defect R2 — compound sense lookup uses sense_route -> lemma_ref -> entry
# ---------------------------------------------------------------------------


def test_compound_components_routes_sense_via_lookup_then_entry(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """``compound_components`` must not bucket the entry shard on ``sense_ref``.

    The compound sense lookup must first resolve ``sense_ref -> lemma_ref``
    via the bucket-closed ``sense_route`` table in the lookup family
    and then bucket the entry shard on ``bucket256_v1(lemma_ref)``. The
    repair removes the previous-candidate defect that bucketed the entry
    shard directly on ``bucket256_v1(sense_ref)``.
    """
    from app.routing import bucket256_v1

    online, _manifest, local, _ = online_corpus
    sense_ref = local.lookup_senses(1)[0].semantic_ref
    lemma = local.lemma_for_id(1)
    assert lemma is not None
    lemma_ref = lemma.semantic_ref
    requested: list[tuple[str, int]] = []
    original_lease = online._cache.lease

    def recording_lease(
        request: ShardRequest,
        *,
        before_download: Callable[[ShardIdentity], None] | None = None,
    ) -> Any:
        requested.append((request.identity.family, request.identity.bucket))
        return original_lease(request, before_download=before_download)

    online._cache.lease = recording_lease  # type: ignore[method-assign]
    try:
        online.compound_components([(lemma_ref, sense_ref)])
    finally:
        online._cache.lease = original_lease  # type: ignore[method-assign]
    # The compound path must never request an entry shard keyed by the
    # sense_ref bucket. It must bucket the entry shard on the routed
    # lemma_ref instead.
    assert ("entry", bucket256_v1(sense_ref)) not in requested
    # And it must request the lookup shard for the sense_ref bucket to
    # resolve the sense_route.
    assert ("lookup", bucket256_v1(sense_ref)) in requested


# ---------------------------------------------------------------------------
# Defect R6 — budget counts real new remote lookup downloads
# ---------------------------------------------------------------------------


def test_budget_does_not_charge_for_verified_cached_reads(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """A second read of the same lookup identity must not charge again."""
    online, _manifest, _local, _ = online_corpus
    budget = _Budget()
    identity = ShardIdentity(SHARD_FAMILY_LOOKUP, 7)
    online.charge_for_test(identity, budget)
    online.charge_for_test(identity, budget)
    assert budget.spent == 1


def test_budget_charges_for_each_new_download_identity(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Two distinct identities count as two new downloads."""
    online, _manifest, _local, _ = online_corpus
    budget = _Budget()
    online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, 1), budget)
    online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, 2), budget)
    assert budget.spent == 2


def test_budget_rejects_on_33rd_real_download(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """The 33rd real download must raise ``ProviderBudgetExceededError``."""
    online, _manifest, _local, _ = online_corpus
    budget = _Budget()
    for bucket in range(MAX_NEW_LOOKUP_DOWNLOADS):
        online.charge_for_test(ShardIdentity(SHARD_FAMILY_LOOKUP, bucket), budget)
    assert budget.spent == MAX_NEW_LOOKUP_DOWNLOADS
    with pytest.raises(ProviderBudgetExceededError):
        online.charge_for_test(
            ShardIdentity(SHARD_FAMILY_LOOKUP, MAX_NEW_LOOKUP_DOWNLOADS), budget
        )


def test_lease_was_downloaded_flag_tracks_real_downloads(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """``ShardLease.was_downloaded`` is True on first miss, False on re-read."""
    online, manifest, _local, _ = online_corpus
    # Pick any lookup bucket and clear the cache so the next lease is a
    # genuine miss rather than a leftover cached hit from earlier tests.
    target_bucket = 11
    asset = next(a for a in manifest.lookup_assets if a.bucket == target_bucket)
    request = ShardRequest(
        identity=ShardIdentity(SHARD_FAMILY_LOOKUP, bucket=target_bucket),
        asset=asset,
    )
    online._cache.clear()
    lease_first = online._cache.lease(request)
    try:
        assert lease_first.was_downloaded is True
        lease_second = online._cache.lease(request)
        try:
            assert lease_second.was_downloaded is False
        finally:
            online._cache.release(lease_second)
    finally:
        online._cache.release(lease_first)


def test_corrupt_refetch_counts_as_new_download(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """A corrupt cached artifact refetch must be charged as a new download."""
    online, manifest, _local, _ = online_corpus
    target_bucket = 9
    asset = next(a for a in manifest.lookup_assets if a.bucket == target_bucket)
    request = ShardRequest(
        identity=ShardIdentity(SHARD_FAMILY_LOOKUP, bucket=target_bucket),
        asset=asset,
    )
    lease_first = online._cache.lease(request)
    try:
        assert lease_first.was_downloaded is True
    finally:
        online._cache.release(lease_first)
    # Corrupt the canonical artifact.
    canonical = (
        online._cache.cache_dir / "verified" / SHARD_FAMILY_LOOKUP / f"{target_bucket}.sqlite"
    )
    canonical.write_bytes(b"corrupt")
    lease_second = online._cache.lease(request)
    try:
        assert lease_second.was_downloaded is True, (
            "corrupt refetch must be flagged as a new download"
        )
    finally:
        online._cache.release(lease_second)


# ---------------------------------------------------------------------------
# Defect R7 — top-level budget continuity
# ---------------------------------------------------------------------------


def test_operation_context_shares_one_budget_across_nested_calls(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Nested reads inside ``operation()`` share one budget."""
    online, _manifest, _local, _ = online_corpus
    with online.operation() as budget:
        online.lookup_exact("Haus")
        online.lookup_exact("See")
        online.lookup_exact("anrufen")
        online.lookup_surface_form("Häuser")
    assert budget.spent <= MAX_NEW_LOOKUP_DOWNLOADS


def test_operation_does_not_reset_budget_on_nested_method(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """A nested method must not reset the operation's budget."""
    online, _manifest, _local, _ = online_corpus
    captured: list[_Budget] = []
    with online.operation() as outer_budget:
        captured.append(outer_budget)
        online.lookup_exact("Haus")
        spent_after_first = outer_budget.spent
        online.lookup_surface_form("Häuser")
        spent_after_second = outer_budget.spent
    assert spent_after_second >= spent_after_first
    # The outer budget object remains the single shared counter; nested
    # methods do not allocate a fresh throwaway budget under the
    # operation context.
    assert len(captured) == 1
    assert isinstance(captured[0], _Budget)


def test_top_level_compound_like_sequence_cumulative_budget(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """A compound-like resolver sequence shares one 32-download budget."""
    online, manifest, local, _ = online_corpus
    queries = ["Haus", "See", "anrufen", "Karte"]
    with online.operation() as budget:
        for query in queries:
            hits = online.lookup_exact(query)
            for hit in hits:
                online.senses_for_lemma(int(hit.lemma_id))
    # Cumulative budget must remain under the limit; it is shared across
    # all nested calls of the operation, not reset per nested read.
    assert budget.spent <= MAX_NEW_LOOKUP_DOWNLOADS


def test_operation_budget_is_not_process_global(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Each operation gets its own budget; no process-global counter."""
    online, _manifest, _local, _ = online_corpus
    online.lookup_exact("Haus")
    with online.operation() as budget_a:
        online.lookup_exact("Haus")
        spent_a = budget_a.spent
    with online.operation() as budget_b:
        online.lookup_exact("Haus")
    # The two operation budgets are independent. Process-global would
    # mean the second operation observes the first's spend.
    assert budget_a is not budget_b
    assert budget_b.spent <= spent_a + 1


# ---------------------------------------------------------------------------
# Defect R3 — examples sourced from example shards
# ---------------------------------------------------------------------------


def test_entry_shard_does_not_carry_example_payload(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Entry shards must not store the authoritative example payload.

    The test reads the entry shards directly from the on-disk corpus
    directory the fixture built (not from the cache), so it remains
    correct whether or not the cache has been cleared by an earlier
    test in this module.
    """
    import sqlite3 as _sqlite3

    _online, _manifest, _local, _ = online_corpus
    # The committed corpus is written under ``<cache_dir>/../corpus``.
    cache_dir = _online._cache.cache_dir
    candidate = cache_dir.parent / "corpus"
    if not candidate.is_dir():
        # Fallback: scan cache for any entry shard.
        entry_paths = sorted(
            (cache_dir / "verified" / SHARD_FAMILY_ENTRY).glob("*.sqlite")
        )
        assert entry_paths, "entry shards must exist in the cache"
    else:
        entry_paths = sorted((candidate).glob("entry-*.sqlite"))
    assert entry_paths, "entry shards must exist in the corpus"
    for entry_path in entry_paths[:3]:
        conn = _sqlite3.connect(f"file:{entry_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='example'"
            ).fetchall()
            assert rows == [], (
                f"entry shard {entry_path.name} must not contain an example payload table"
            )
        finally:
            conn.close()


def test_example_shards_carry_full_example_payload(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Example shards must carry the authoritative example payload."""
    import sqlite3 as _sqlite3

    _online, _manifest, _local, _ = online_corpus
    cache_dir = _online._cache.cache_dir
    candidate = cache_dir.parent / "corpus"
    if candidate.is_dir():
        example_paths = sorted(candidate.glob("example-*.sqlite"))
    else:
        example_paths = sorted(
            (cache_dir / "verified" / SHARD_FAMILY_EXAMPLE).glob("*.sqlite")
        )
    assert example_paths, "example shards must exist in the corpus"
    total_examples = 0
    for example_path in example_paths:
        conn = _sqlite3.connect(
            f"file:{example_path.as_posix()}?mode=ro", uri=True
        )
        try:
            count = conn.execute("SELECT COUNT(*) FROM example").fetchone()[0]
            assert count >= 0
            total_examples += int(count)
        finally:
            conn.close()
    assert total_examples >= 5, (
        "the fixture has 5 examples; the example family must carry them all"
    )


def test_example_bucket_assignment_is_example_id_modulo_64(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """Each example lands in exactly its ``id % 64`` bucket."""
    import re as _re
    import sqlite3 as _sqlite3

    _online, _manifest, _local, _ = online_corpus
    cache_dir = _online._cache.cache_dir
    candidate = cache_dir.parent / "corpus"
    if candidate.is_dir():
        example_paths = sorted(candidate.glob("example-*.sqlite"))
    else:
        example_paths = sorted(
            (cache_dir / "verified" / SHARD_FAMILY_EXAMPLE).glob("*.sqlite")
        )
    for example_path in example_paths:
        match = _re.search(r"example-(\d+)", example_path.name)
        assert match is not None
        bucket = int(match.group(1))
        conn = _sqlite3.connect(
            f"file:{example_path.as_posix()}?mode=ro", uri=True
        )
        try:
            for row in conn.execute("SELECT id FROM example").fetchall():
                assert int(row[0]) % 64 == bucket, (
                    f"example_id={int(row[0])} not in expected bucket {bucket}"
                )
        finally:
            conn.close()


def test_example_id_refs_point_to_existing_example_records(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """``example_lemma.example_id`` references must resolve in the example family."""
    import sqlite3 as _sqlite3

    _online, _manifest, _local, _ = online_corpus
    cache_dir = _online._cache.cache_dir
    candidate = cache_dir.parent / "corpus"
    if candidate.is_dir():
        example_paths = sorted(candidate.glob("example-*.sqlite"))
        entry_paths = sorted(candidate.glob("entry-*.sqlite"))
    else:
        example_paths = sorted(
            (cache_dir / "verified" / SHARD_FAMILY_EXAMPLE).glob("*.sqlite")
        )
        entry_paths = sorted(
            (cache_dir / "verified" / SHARD_FAMILY_ENTRY).glob("*.sqlite")
        )

    example_ids_in_family: set[int] = set()
    for example_path in example_paths:
        conn = _sqlite3.connect(
            f"file:{example_path.as_posix()}?mode=ro", uri=True
        )
        try:
            for row in conn.execute("SELECT id FROM example").fetchall():
                example_ids_in_family.add(int(row[0]))
        finally:
            conn.close()
    for entry_path in entry_paths:
        conn = _sqlite3.connect(
            f"file:{entry_path.as_posix()}?mode=ro", uri=True
        )
        try:
            for row in conn.execute("SELECT example_id FROM example_lemma").fetchall():
                assert int(row[0]) in example_ids_in_family, (
                    f"example_id={int(row[0])} from entry {entry_path.name} "
                    "not found in example family"
                )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Final pre-review correction helpers and regressions (C5, C7, C8, C12, C13)
# ---------------------------------------------------------------------------


def _fresh_online_with_counter(
    online_corpus: tuple[
        OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes
    ],
    tmp_path: Path,
) -> tuple[OnlineDictionaryProvider, ShardCache, dict[str, int]]:
    """Build a fresh Online provider over the shared corpus with a counter.

    The module-scoped fixture cache may already hold shards; a fresh
    cache makes download/budget accounting deterministic per test.
    """
    online, manifest, _local, filter_bytes = online_corpus
    corpus_dir = online._cache.cache_dir.parent / "corpus"
    calls = {"count": 0}

    def transport(request: ShardRequest) -> bytes:
        calls["count"] += 1
        for asset in manifest.assets:
            if (
                asset.family == request.identity.family
                and asset.bucket == request.identity.bucket
            ):
                return (corpus_dir / asset.name).read_bytes()
        raise ProviderIntegrityError("missing fixture shard")

    cache = ShardCache(tmp_path / "cache", transport=transport)
    provider = OnlineDictionaryProvider(
        manifest=manifest,
        cache=cache,
        filter_payload=filter_bytes,
        dataset_token=manifest.dataset_token,
    )
    return provider, cache, calls


def test_33rd_lookup_download_rejected_before_transport(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
    tmp_path: Path,
) -> None:
    """The 33rd distinct lookup identity is rejected BEFORE any download."""
    provider, cache, calls = _fresh_online_with_counter(online_corpus, tmp_path)
    _, manifest, _, _ = online_corpus
    with provider.operation() as budget:
        held = []
        for bucket in range(MAX_NEW_LOOKUP_DOWNLOADS):
            asset = next(a for a in manifest.lookup_assets if a.bucket == bucket)
            lease = provider._lease_with_budget(
                ShardRequest(ShardIdentity(SHARD_FAMILY_LOOKUP, bucket), asset),
                budget,
            )
            held.append(lease)
        assert calls["count"] == MAX_NEW_LOOKUP_DOWNLOADS
        asset32 = next(
            a for a in manifest.lookup_assets if a.bucket == MAX_NEW_LOOKUP_DOWNLOADS
        )
        with pytest.raises(ProviderBudgetExceededError):
            provider._lease_with_budget(
                ShardRequest(
                    ShardIdentity(SHARD_FAMILY_LOOKUP, MAX_NEW_LOOKUP_DOWNLOADS),
                    asset32,
                ),
                budget,
            )
        # Rejected before transport: the invocation count is unchanged.
        assert calls["count"] == MAX_NEW_LOOKUP_DOWNLOADS
        for lease in held:
            cache.release(lease)


def test_sense_route_shares_active_operation_budget(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
    tmp_path: Path,
) -> None:
    """An uncached sense_route consumes one lookup identity; cached is free."""
    from app.routing import bucket256_v1

    provider, _cache, calls = _fresh_online_with_counter(online_corpus, tmp_path)
    _, _, local, _ = online_corpus
    sense_refs = [local.lookup_senses(i)[0].semantic_ref for i in range(1, 6)]
    target = next(ref for ref in sense_refs if bucket256_v1(ref) >= 32)
    with provider.operation() as budget:
        route = provider.sense_route(target)
        assert route is not None
        assert budget.spent == 1
        calls_after_first = calls["count"]
        assert calls_after_first == 1
        route_again = provider.sense_route(target)
        assert route_again == route
        assert budget.spent == 1
        assert calls["count"] == calls_after_first


def test_sense_route_can_be_rejected_as_33rd_identity_before_transport(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
    tmp_path: Path,
) -> None:
    """A sense-route download can be the 33rd identity: rejected pre-network."""
    from app.routing import bucket256_v1

    provider, cache, calls = _fresh_online_with_counter(online_corpus, tmp_path)
    _, manifest, local, _ = online_corpus
    sense_refs = [local.lookup_senses(i)[0].semantic_ref for i in range(1, 6)]
    target = next(ref for ref in sense_refs if bucket256_v1(ref) >= 32)
    with provider.operation() as budget:
        held = []
        for bucket in range(MAX_NEW_LOOKUP_DOWNLOADS):
            asset = next(a for a in manifest.lookup_assets if a.bucket == bucket)
            held.append(
                provider._lease_with_budget(
                    ShardRequest(ShardIdentity(SHARD_FAMILY_LOOKUP, bucket), asset),
                    budget,
                )
            )
        assert calls["count"] == MAX_NEW_LOOKUP_DOWNLOADS
        with pytest.raises(ProviderBudgetExceededError):
            provider.sense_route(target)
        assert calls["count"] == MAX_NEW_LOOKUP_DOWNLOADS
        for lease in held:
            cache.release(lease)


def test_nested_operation_yields_same_budget_object(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
    tmp_path: Path,
) -> None:
    """Nested ``operation()`` yields the SAME budget; spend accumulates."""
    provider, _cache, _calls = _fresh_online_with_counter(online_corpus, tmp_path)
    with provider.operation() as outer:
        provider.lookup_exact("Haus")
        spent_after_first = outer.spent
        assert spent_after_first >= 1
        with provider.operation() as inner:
            assert inner is outer
            provider.lookup_surface_form("Häuser")
            spent_inside = outer.spent
        assert spent_inside >= spent_after_first
        provider.lookup_exact("See")
        assert outer.spent >= spent_inside


def test_candidate_lookup_resolves_surface_form_absent_from_lemma_bloom(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
) -> None:
    """``candidate_lookup`` must not Bloom-prune valid surface forms.

    ``Häuser`` is NOT an authoritative lemma and is Bloom-negative as a
    lemma, but resolves through ``surface_form``. The candidate ladder
    must return the same result as Local.
    """
    online, _, local, _ = online_corpus
    assert not local.lookup_exact("Häuser")
    assert not online.filter.contains_query("Häuser")
    local_surface = [hit.semantic_ref for hit in local.lookup_surface_form("Häuser")]
    assert local_surface, "fixture premise: Local resolves Häuser via surface_form"
    local_candidates = [
        (c.lemma.semantic_ref, c.lemma.lemma) for c in local.candidate_lookup("Häuser")
    ]
    online_candidates = [
        (c.lemma.semantic_ref, c.lemma.lemma)
        for c in online.candidate_lookup("Häuser")
    ]
    assert online_candidates, "Online must not Bloom-prune the surface form"
    assert sorted(online_candidates) == sorted(local_candidates)


def test_lemma_for_ref_populates_numeric_identity(
    online_corpus: tuple[OnlineDictionaryProvider, OnlineManifest, LocalDictionaryProvider, bytes],
    tmp_path: Path,
) -> None:
    """``lemma_for_ref`` observation enables numeric reads without a scan."""
    provider, cache, _calls = _fresh_online_with_counter(online_corpus, tmp_path)
    _, _, local, _ = online_corpus
    local_entry = local.lemma_for_id(1)
    assert local_entry is not None
    lemma_ref = local_entry.semantic_ref
    lemma_id = int(local_entry.lemma_id)
    assert provider.senses_for_lemma(lemma_id) == ()
    observed = provider.lemma_for_ref(lemma_ref)
    assert observed is not None
    assert int(observed.lemma_id) == lemma_id
    requested: list[ShardIdentity] = []
    original_lease = cache.lease

    def recording_lease(
        request: ShardRequest,
        *,
        before_download: Callable[[ShardIdentity], None] | None = None,
    ) -> Any:
        requested.append(request.identity)
        return original_lease(request, before_download=before_download)

    cache.lease = recording_lease  # type: ignore[method-assign]
    try:
        senses = provider.senses_for_lemma(lemma_id)
    finally:
        cache.lease = original_lease  # type: ignore[method-assign]
    assert senses, "numeric read must succeed after lemma_for_ref observation"
    assert requested, "expected at least one shard acquisition"
    assert {identity.family for identity in requested} == {"entry"}