"""Performance and selection policy integration tests for v2.0.1.

Verifies:
1. Lookup (/vocab/lookup), sentence highlight (/vocab/highlight), and CSV import
   (/vocab/import/csv) perform ZERO example shard leases.
2. Note/card materialization leases at most MAX_CARD_EXAMPLES (2) example shards.
3. Rendered card back examples are capped at MAX_CARD_EXAMPLES (2).
4. English-first candidate and sense selection policy with deterministic fallback.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.dictionary_mode import _LOCAL_FILENAME
from app.dictionary_session import OnlineSessionInfo
from app.online_cache import ShardCache, ShardRequest
from app.online_filter import BloomFilter
from app.online_manifest import (
    ENTRY_FAMILY_SIZE,
    EXAMPLE_FAMILY_SIZE,
    LOOKUP_FAMILY_SIZE,
    MANIFEST_SCHEMA_VERSION,
    SHARD_FAMILY_EXAMPLE,
    SHARD_FAMILY_FILTER,
    ManifestAsset,
    OnlineManifest,
    TrustedDistribution,
)
from app.provider import ProviderIntegrityError
from app.provider_online import OnlineDictionaryProvider
from app.selection import (
    MAX_CARD_EXAMPLES,
    filter_candidates,
    rank_candidates,
    select_default_sense_ref,
)
from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref
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


def _full_part_a_schema() -> str:
    text = (Path(__file__).resolve().parent.parent / "reference" / "schema.sql").read_text(
        encoding="utf-8"
    )
    part_a, marker, _ = text.partition("-- PART B")
    if not marker:
        raise RuntimeError("schema.sql missing the PART B marker")
    return part_a


@pytest.fixture(scope="module")
def perf_dict(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a rich local dictionary fixture with multiple examples, English and German senses."""
    root = tmp_path_factory.mktemp("perf_dict")
    db = root / "local.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(_full_part_a_schema())

    # Lemmas:
    # 1. Haus (NOUN, n) - English + German meanings, 5 examples
    # 2. Frieden (NOUN, m) - English + German meanings, 3 examples
    # 3. Krieg (NOUN, m) - German only meaning, 0 examples
    # 4. Buch (NOUN, n) - Sense 1 German only, Sense 2 English + German, 0 examples
    lemmas_data = [
        (1, "Haus", "NOUN", "n", "das"),
        (2, "Frieden", "NOUN", "m", "der"),
        (3, "Krieg", "NOUN", "m", "der"),
        (4, "Buch", "NOUN", "n", "das"),
    ]
    for ident, lemma, pos, gender, _art in lemmas_data:
        lref = compute_lemma_semantic_ref(lemma, pos, gender)
        conn.execute(
            "INSERT INTO lemma "
            "(id, semantic_ref, lemma, pos, gender, plural_none, source, license) "
            "VALUES (?, ?, ?, ?, ?, 0, 'fixture', 'CC0')",
            (ident, lref, lemma, pos, gender),
        )

    # Senses & meanings
    # Haus: 1 sense, both DE and EN
    sref_haus = compute_sense_semantic_ref(
        compute_lemma_semantic_ref("Haus", "NOUN", "n"), "fixture", "s:haus:1"
    )
    conn.execute(
        "INSERT INTO sense "
        "(id, lemma_id, semantic_ref, source_namespace, source_ref, ord, source, license) "
        "VALUES (1, 1, ?, 'fixture', 's:haus:1', 0, 'fixture', 'CC0')",
        (sref_haus,),
    )
    conn.execute(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (1, 1, 'en', 'translation', 0, 'house, building', 'fixture', 'CC0')"
    )
    conn.execute(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (2, 1, 'de', 'definition', 1, 'Gebäude zum Wohnen', 'fixture', 'CC0')"
    )

    # Frieden: 1 sense, both DE and EN
    sref_frieden = compute_sense_semantic_ref(
        compute_lemma_semantic_ref("Frieden", "NOUN", "m"), "fixture", "s:frieden:1"
    )
    conn.execute(
        "INSERT INTO sense "
        "(id, lemma_id, semantic_ref, source_namespace, source_ref, ord, source, license) "
        "VALUES (2, 2, ?, 'fixture', 's:frieden:1', 0, 'fixture', 'CC0')",
        (sref_frieden,),
    )
    conn.execute(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (3, 2, 'en', 'translation', 0, 'peace', 'fixture', 'CC0')"
    )
    conn.execute(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (4, 2, 'de', 'definition', 1, 'Zustand der Ruhe', 'fixture', 'CC0')"
    )

    # Krieg: 1 sense, DE only
    sref_krieg = compute_sense_semantic_ref(
        compute_lemma_semantic_ref("Krieg", "NOUN", "m"), "fixture", "s:krieg:1"
    )
    conn.execute(
        "INSERT INTO sense "
        "(id, lemma_id, semantic_ref, source_namespace, source_ref, ord, source, license) "
        "VALUES (3, 3, ?, 'fixture', 's:krieg:1', 0, 'fixture', 'CC0')",
        (sref_krieg,),
    )
    conn.execute(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (5, 3, 'de', 'definition', 0, 'Bewaffneter Konflikt', 'fixture', 'CC0')"
    )

    # Buch: 2 senses. Sense 4 is DE only, Sense 5 has EN.
    sref_buch1 = compute_sense_semantic_ref(
        compute_lemma_semantic_ref("Buch", "NOUN", "n"), "fixture", "s:buch:1"
    )
    sref_buch2 = compute_sense_semantic_ref(
        compute_lemma_semantic_ref("Buch", "NOUN", "n"), "fixture", "s:buch:2"
    )
    conn.execute(
        "INSERT INTO sense "
        "(id, lemma_id, semantic_ref, source_namespace, source_ref, ord, source, license) "
        "VALUES (4, 4, ?, 'fixture', 's:buch:1', 0, 'fixture', 'CC0')",
        (sref_buch1,),
    )
    conn.execute(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (6, 4, 'de', 'definition', 0, 'Gedrucktes Werk', 'fixture', 'CC0')"
    )
    conn.execute(
        "INSERT INTO sense "
        "(id, lemma_id, semantic_ref, source_namespace, source_ref, ord, source, license) "
        "VALUES (5, 4, ?, 'fixture', 's:buch:2', 1, 'fixture', 'CC0')",
        (sref_buch2,),
    )
    conn.execute(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (7, 5, 'en', 'translation', 0, 'book, volume', 'fixture', 'CC0')"
    )

    # Examples: 5 examples for Haus (id 1..5), 3 examples for Frieden (id 6..8)
    examples_data = [
        (1, "Das Haus ist alt.", "The house is old."),
        (2, "Ich baue ein Haus.", "I build a house."),
        (3, "Wir gehen ins Haus.", "We go into the house."),
        (4, "Ein großes Haus.", "A big house."),
        (5, "Das ist mein Haus.", "That is my house."),
        (6, "Wir wollen Frieden.", "We want peace."),
        (7, "Frieden auf Erden.", "Peace on Earth."),
        (8, "Der Frieden währt lange.", "Peace lasts long."),
    ]
    for ex_id, de, en in examples_data:
        conn.execute(
            "INSERT INTO example (id, de, en, source, license, token_count, has_proper) "
            "VALUES (?, ?, ?, 'fixture', 'CC0', 4, 0)",
            (ex_id, de, en),
        )

    for ex_id in [1, 2, 3, 4, 5]:
        conn.execute("INSERT INTO example_lemma (lemma_id, example_id) VALUES (1, ?)", (ex_id,))
    for ex_id in [6, 7, 8]:
        conn.execute("INSERT INTO example_lemma (lemma_id, example_id) VALUES (2, ?)", (ex_id,))

    conn.executemany(
        "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
        [("Häuser", 1), ("Häusern", 1), ("Bücher", 4)],
    )

    conn.commit()
    conn.close()
    return db


@pytest.fixture(scope="module")
def perf_online_corpus(
    perf_dict: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, OnlineManifest, bytes, str]:
    """Build Online shards once per module in output_root."""
    output_root = tmp_path_factory.mktemp("online_corpus")
    output_dir = output_root / "corpus"
    output_dir.mkdir(parents=True, exist_ok=True)

    actual_token = sha256(perf_dict.read_bytes()).hexdigest()

    source_conn = sqlite3.connect(f"file:{perf_dict.as_posix()}?mode=ro", uri=True)
    source_conn.row_factory = sqlite3.Row
    try:
        lemmas = list(_read_authoritative_lemmas(source_conn))
        senses = list(_read_authoritative_senses(source_conn))
        meanings = list(_read_authoritative_meanings(source_conn))
        surface_forms = list(_read_authoritative_surface_forms(source_conn))
        examples = list(_read_authoritative_examples(source_conn))
        example_lemma = list(_read_authoritative_example_lemma(source_conn))
    finally:
        source_conn.close()

    lookup_partitions, surface_partitions, sense_route_partitions = _partition_lookup_shards(
        lemmas, surface_forms, senses
    )
    entry_partitions = _partition_entry_shards(
        lemmas, senses, meanings, surface_forms, example_lemma, examples
    )
    example_partitions = _partition_example_shards(examples)

    assets: list[ManifestAsset] = []
    for bucket in range(LOOKUP_FAMILY_SIZE):
        canonical = output_dir / f"lookup-{bucket:03d}.sqlite"
        tmp = output_dir / f".lookup-{bucket:03d}.sqlite.tmp"
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        _write_lookup_shard(
            conn,
            bucket,
            lookup_partitions.get(bucket, []),
            surface_partitions.get(bucket, []),
            sense_route_partitions.get(bucket, ()),
        )
        conn.close()
        os.replace(tmp, canonical)
        payload = canonical.read_bytes()
        assets.append(
            ManifestAsset(
                family="lookup",
                bucket=bucket,
                name=f"lookup-{bucket:03d}.sqlite",
                path=f"shards/lookup/{bucket:03d}.sqlite",
                byte_size=len(payload),
                sha256=sha256(payload).hexdigest(),
                schema_version="lookup-v1",
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
        canonical = output_dir / f"entry-{bucket:03d}.sqlite"
        tmp = output_dir / f".entry-{bucket:03d}.sqlite.tmp"
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        _write_entry_shard(
            conn,
            bucket,
            state["lemmas"],
            state["senses"],
            state["meanings"],
            state["surface_forms"],
            state["example_lemma"],
        )
        conn.close()
        os.replace(tmp, canonical)
        payload = canonical.read_bytes()
        assets.append(
            ManifestAsset(
                family="entry",
                bucket=bucket,
                name=f"entry-{bucket:03d}.sqlite",
                path=f"shards/entry/{bucket:03d}.sqlite",
                byte_size=len(payload),
                sha256=sha256(payload).hexdigest(),
                schema_version="entry-v1",
            )
        )

    for bucket in range(EXAMPLE_FAMILY_SIZE):
        canonical = output_dir / f"example-{bucket:03d}.sqlite"
        tmp = output_dir / f".example-{bucket:03d}.sqlite.tmp"
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        _write_example_shard(conn, bucket, example_partitions.get(bucket, []))
        conn.close()
        os.replace(tmp, canonical)
        payload = canonical.read_bytes()
        assets.append(
            ManifestAsset(
                family="example",
                bucket=bucket,
                name=f"example-{bucket:03d}.sqlite",
                path=f"shards/example/{bucket:03d}.sqlite",
                byte_size=len(payload),
                sha256=sha256(payload).hexdigest(),
                schema_version="example-v1",
            )
        )

    closure_keys: list[str] = []
    seen_closure: set[str] = set()
    for row in lemmas:
        text = str(row[2])
        for variant in (text, text.lower()):
            if variant in seen_closure:
                continue
            seen_closure.add(variant)
            closure_keys.append(variant)
    filter_bytes = BloomFilter.from_closure_keys(closure_keys).to_bytes()
    assets.append(
        ManifestAsset(
            family=SHARD_FAMILY_FILTER,
            bucket=0,
            name="membership-filter.bin",
            path="shards/membership-filter.bin",
            byte_size=len(filter_bytes),
            sha256=sha256(filter_bytes).hexdigest(),
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
    return output_dir, manifest, filter_bytes, actual_token


class LeaseRecorder:
    """Instruments a ShardCache to record all leased shard requests."""

    def __init__(self, provider: OnlineDictionaryProvider) -> None:
        self.provider = provider
        self.recorded: list[ShardRequest] = []
        self._original_lease = provider._cache.lease

        def recording_lease(
            request: ShardRequest,
            *,
            before_download: Any = None,
        ) -> Any:
            self.recorded.append(request)
            return self._original_lease(request, before_download=before_download)

        provider._cache.lease = recording_lease  # type: ignore[method-assign]

    def reset(self) -> None:
        self.recorded.clear()

    @property
    def example_leases(self) -> list[ShardRequest]:
        return [r for r in self.recorded if r.identity.family == SHARD_FAMILY_EXAMPLE]


@pytest.fixture
def perf_app(
    perf_online_corpus: tuple[Path, OnlineManifest, bytes, str],
    tmp_path: Path,
) -> Generator[tuple[Any, LeaseRecorder], None, None]:
    from app.standalone import ensure_user_db

    corpus_dir, manifest, filter_bytes, actual_token = perf_online_corpus

    user_db = tmp_path / "flashcards.sqlite"
    ensure_user_db(user_db)

    cache_dir = tmp_path / "online-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def transport(request: ShardRequest) -> bytes:
        for asset in manifest.assets:
            if asset.family == request.identity.family and asset.bucket == request.identity.bucket:
                return (corpus_dir / asset.name).read_bytes()
        raise ProviderIntegrityError("missing fixture shard")

    cache = ShardCache(cache_dir, transport=transport)
    provider = OnlineDictionaryProvider(
        manifest=manifest,
        cache=cache,
        filter_payload=filter_bytes,
        dataset_token=actual_token,
    )
    recorder = LeaseRecorder(provider)

    info = OnlineSessionInfo(
        dataset_token=actual_token,
        asset_token=str(provider.asset_token),
        cache_dir=str(cache_dir),
    )

    def online_factory() -> Any:
        f_cache_dir = tmp_path / "online-factory-cache"
        f_cache_dir.mkdir(parents=True, exist_ok=True)
        c = ShardCache(f_cache_dir, transport=transport)
        p = OnlineDictionaryProvider(
            manifest=manifest,
            cache=c,
            filter_payload=filter_bytes,
            dataset_token=actual_token,
        )
        inf = OnlineSessionInfo(
            dataset_token=actual_token,
            asset_token=str(p.asset_token),
            cache_dir=str(f_cache_dir),
        )
        return p, inf

    app = create_app(
        dict_path=None,
        user_db_path=user_db,
        cors_origins=["http://127.0.0.1:8000"],
        online_provider=provider,
        online_session_info=info,
        online_provider_factory=online_factory,
        managed_dictionary_dir=tmp_path / "dictionary-slot",
        manifest_filename=_LOCAL_FILENAME,
    )
    yield app, recorder
    try:
        provider.close()
    except Exception:
        pass


@pytest.fixture
def client(
    perf_app: tuple[Any, LeaseRecorder],
) -> Generator[tuple[TestClient, LeaseRecorder], None, None]:
    app, recorder = perf_app
    with TestClient(app, base_url="http://127.0.0.1:8000") as test_client:
        yield test_client, recorder


# ---------------------------------------------------------------------------
# Requirement A: Zero example shard leases on lookup, highlight, and CSV import
# ---------------------------------------------------------------------------


def test_zero_example_leases_on_get_lookup(client: tuple[TestClient, LeaseRecorder]) -> None:
    http_client, recorder = client
    recorder.reset()

    response = http_client.get(
        "/vocab/lookup?q=Haus",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["candidates"]) >= 1
    assert payload["candidates"][0]["lemma"] == "Haus"

    # Must perform ZERO example leases
    assert len(recorder.example_leases) == 0, (
        f"Expected 0 example leases, got {recorder.example_leases}"
    )


def test_zero_example_leases_on_post_lookup(client: tuple[TestClient, LeaseRecorder]) -> None:
    http_client, recorder = client
    recorder.reset()

    response = http_client.post(
        "/vocab/lookup",
        json={"query": "Frieden"},
        headers={
            "Host": "127.0.0.1:8000",
            "X-Flashcards-Request": "1",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["candidates"]) >= 1
    assert payload["candidates"][0]["lemma"] == "Frieden"

    # Must perform ZERO example leases
    assert len(recorder.example_leases) == 0, (
        f"Expected 0 example leases, got {recorder.example_leases}"
    )


def test_zero_example_leases_on_highlight(client: tuple[TestClient, LeaseRecorder]) -> None:
    http_client, recorder = client
    recorder.reset()

    response = http_client.post(
        "/vocab/highlight",
        json={
            "sentence_text": "Das Haus ist alt.",
            "selected_span": {"start": 4, "end": 8},
            "lesson_label": "Lesson 1",
        },
        headers={
            "Host": "127.0.0.1:8000",
            "X-Flashcards-Request": "1",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["candidates"]) >= 1
    assert any(c["lemma"] == "Haus" for c in payload["candidates"])

    # Must perform ZERO example leases
    assert len(recorder.example_leases) == 0, (
        f"Expected 0 example leases, got {recorder.example_leases}"
    )


def test_zero_example_leases_on_import_csv(client: tuple[TestClient, LeaseRecorder]) -> None:
    http_client, recorder = client
    recorder.reset()

    response = http_client.post(
        "/vocab/import/csv",
        json={
            "csv_text": "Haus\nFrieden",
            "deck_name": "Import Deck",
            "meaning_languages": ["en", "de"],
        },
        headers={
            "Host": "127.0.0.1:8000",
            "X-Flashcards-Request": "1",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["notes_created"] >= 1

    # Candidate resolution and note creation during CSV import must perform ZERO example leases
    assert len(recorder.example_leases) == 0, (
        f"Expected 0 example leases, got {recorder.example_leases}"
    )


# ---------------------------------------------------------------------------
# Requirement B: Bounded example leases (<= 2) and rendered card examples <= 2
# ---------------------------------------------------------------------------


def test_card_materialization_bounds_example_leases_and_rendered_card(
    client: tuple[TestClient, LeaseRecorder],
) -> None:
    http_client, recorder = client

    # First lookup Haus to get its candidate details
    lookup_res = http_client.get(
        "/vocab/lookup?q=Haus",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert lookup_res.status_code == 200
    lookup_data = lookup_res.json()
    cand = lookup_data["candidates"][0]
    lem_ref = cand.get("ref") or cand.get("lemma_semantic_ref")
    sense_ref = cand["senses"][0]["sense_semantic_ref"]
    active_token = lookup_data["asset_token"]

    # Reset recorder before card creation
    recorder.reset()

    # Create note and cards
    create_res = http_client.post(
        "/vocab/cards",
        json={
            "asset_token": active_token,
            "deck": "Study Deck",
            "selections": [
                {
                    "ref": lem_ref,
                    "sense_ref": sense_ref,
                }
            ],
        },
        headers={
            "Host": "127.0.0.1:8000",
            "X-Flashcards-Request": "1",
            "Content-Type": "application/json",
        },
    )
    assert create_res.status_code == 201
    cards_payload = create_res.json()
    assert len(cards_payload["notes"]) == 1

    # Shard leases for examples must be strictly bounded <= MAX_CARD_EXAMPLES (2)
    assert len(recorder.example_leases) <= MAX_CARD_EXAMPLES, (
        f"Expected at most {MAX_CARD_EXAMPLES} example leases during card materialization, "
        f"got {len(recorder.example_leases)}"
    )

    # Now fetch next study card to inspect rendered card back
    next_card_res = http_client.get(
        "/vocab/cards/next",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert next_card_res.status_code == 200
    card_data = next_card_res.json()
    assert card_data is not None
    assert card_data["card"] is not None
    card = card_data["card"]
    assert "back" in card
    rendered_examples = card["back"]["examples"]

    # Hard requirement: len(card.back.examples) <= 2
    assert len(rendered_examples) <= MAX_CARD_EXAMPLES
    assert len(rendered_examples) > 0  # Haus had 5 examples in fixture, so it gets 2
    assert len(rendered_examples) == 2


# ---------------------------------------------------------------------------
# Requirement C & D: English-first policy and deterministic fallback
# ---------------------------------------------------------------------------


def test_english_first_sense_ranking(client: tuple[TestClient, LeaseRecorder]) -> None:
    http_client, _ = client

    # Buch has 2 senses in the fixture:
    # Sense 1: German only ("Gedrucktes Werk")
    # Sense 2: English ("book, volume")
    response = http_client.get(
        "/vocab/lookup?q=Buch",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 200
    cand = response.json()["candidates"][0]
    senses = cand["senses"]
    assert len(senses) == 2

    # The English-bearing sense must be ranked first
    first_sense_meanings = senses[0]["meanings"]
    has_english = any(m["language"] == "en" for m in first_sense_meanings)
    assert has_english, f"Expected first sense to have English meaning, got {senses[0]}"
    assert any("book" in m["text"] for m in first_sense_meanings)


def test_deterministic_fallback_when_no_english(client: tuple[TestClient, LeaseRecorder]) -> None:
    http_client, _ = client

    # Krieg has only German definition in the fixture
    response = http_client.get(
        "/vocab/lookup?q=Krieg",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert response.status_code == 200
    cand = response.json()["candidates"][0]
    assert cand["lemma"] == "Krieg"
    assert len(cand["senses"]) == 1
    assert cand["senses"][0]["meanings"][0]["language"] == "de"
    assert "Konflikt" in cand["senses"][0]["meanings"][0]["text"]


def test_selection_policy_helpers() -> None:
    """Direct unit checks on selection policy functions."""
    cand_en = {
        "lemma": "Frieden",
        "senses": [
            {"ref": "s1", "meanings": [{"language": "en", "text": "peace"}]},
        ],
    }
    cand_de = {
        "lemma": "Krieg",
        "senses": [
            {"ref": "s2", "meanings": [{"language": "de", "text": "Krieg"}]},
        ],
    }

    # Candidate ranking
    ranked = rank_candidates([cand_de, cand_en])
    assert ranked[0]["lemma"] == "Frieden"

    # Candidate filtering
    filtered = filter_candidates([cand_de, cand_en])
    assert len(filtered) == 1
    assert filtered[0]["lemma"] == "Frieden"

    # Sense selection: exactly 1 English sense -> auto-selected
    default_ref = select_default_sense_ref(cand_en)
    assert default_ref == "s1"

    # Multiple English senses (>1) -> deterministically selects first English sense
    # for automated operations
    cand_ambiguous = {
        "lemma": "Bank",
        "senses": [
            {"ref": "b1", "meanings": [{"language": "en", "text": "bank"}]},
            {"ref": "b2", "meanings": [{"language": "en", "text": "bench"}]},
        ],
    }
    assert select_default_sense_ref(cand_ambiguous) == "b1"
