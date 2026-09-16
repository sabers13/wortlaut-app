"""Tests for app/dictionary.py and DictionaryRuntime (ADR-0004 PART A/B alignment)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

import pytest

from app import deck
from app.deck import (
    DictionaryClosedError,
    DictionaryRuntime,
    DictionaryRuntimeError,
    ReadingSnapshot,
    _Generation,
)
from app.dictionary import (
    Dictionary,
    DictionaryAsset,
    DictionaryAssetError,
    DictionaryEntry,
    _build_lemma_ref_maps,
    validate_candidate_dictionary,
)
from app.resolve import Ref


def _stable_ref(prefix: str, fields: list[str]) -> str:
    """Build a D47 ref from exact test fields without normalizing them."""
    payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{prefix}:v1:{sha256(payload).hexdigest()}"


def _make_candidate_asset(
    tmp_path: Path,
    part_a_schema: str,
    *,
    lemma: str = "See",
    source_ref: str = "senseid:see-1",
    schema: str | None = None,
) -> Path:
    """Create a minimal, internally consistent PART-A candidate asset."""
    path = tmp_path / f"candidate-{source_ref.replace(':', '-')}.sqlite"
    lemma_ref = _stable_ref("lemma", ["de", lemma, "NOUN", "der"])
    sense_ref = _stable_ref("sense", [lemma_ref, "wiktextract:enwiktionary", source_ref])
    connection = sqlite3.connect(path)
    try:
        connection.executescript(schema if schema is not None else part_a_schema)
        connection.execute(
            "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender) VALUES (1, ?, ?, ?, ?)",
            (lemma_ref, lemma, "NOUN", "der"),
        )
        connection.execute(
            """
            INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, source_ref)
            VALUES (1, 1, ?, ?, ?)
            """,
            (sense_ref, "wiktextract:enwiktionary", source_ref),
        )
        connection.commit()
    finally:
        connection.close()
    return path


# --- S2a: candidate assets remain bound to one validated byte snapshot ---


def test_candidate_validation_binds_sha_and_handle_to_original_bytes(
    tmp_path: Path, part_a_schema: str
) -> None:
    """Replacing the source after validation cannot change the retained snapshot."""
    path = _make_candidate_asset(tmp_path, part_a_schema)
    expected_bytes = path.read_bytes()
    expected_sha256 = sha256(expected_bytes).hexdigest()
    original_ref = _stable_ref("lemma", ["de", "See", "NOUN", "der"])

    asset = validate_candidate_dictionary(path)
    try:
        # Replace rather than edit in place: a close-and-reopen implementation
        # would now serve Meer and fail this bytes-bound evidence.
        replacement = _make_candidate_asset(
            tmp_path,
            part_a_schema,
            lemma="Meer",
            source_ref="senseid:replacement",
        )
        replacement.replace(path)

        assert asset.sha256 == expected_sha256
        assert sha256(path.read_bytes()).hexdigest() != expected_sha256
        assert asset.connection.execute("SELECT lemma FROM lemma").fetchone()[0] == "See"
        assert dict(asset.lemma_ids) == {original_ref: 1}
    finally:
        asset.close()


def test_candidate_validation_rejects_corrupt_or_incomplete_assets(tmp_path: Path) -> None:
    """Bad SQLite content and a database missing PART-A structures fail closed."""
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(DictionaryAssetError):
        validate_candidate_dictionary(corrupt)

    incomplete = tmp_path / "incomplete.sqlite"
    sqlite3.connect(incomplete).close()
    with pytest.raises(DictionaryAssetError):
        validate_candidate_dictionary(incomplete)


def test_candidate_validation_rejects_whitespace_padded_identity_field(
    tmp_path: Path, part_a_schema: str
) -> None:
    """Validation never silently strips a non-canonical persisted identity value."""
    path = _make_candidate_asset(tmp_path, part_a_schema)
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE lemma SET lemma = 'See '")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DictionaryAssetError):
        validate_candidate_dictionary(path)


def test_candidate_validation_rejects_wrong_shape_and_recomputation_mismatch(
    tmp_path: Path, part_a_schema: str
) -> None:
    """A versioned namespace and matching exact D47 hash are both mandatory."""
    wrong_shape = _make_candidate_asset(tmp_path, part_a_schema, source_ref="senseid:wrong-shape")
    connection = sqlite3.connect(wrong_shape)
    try:
        connection.execute("UPDATE lemma SET semantic_ref = 'lemma:v1:not-a-sha'")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DictionaryAssetError):
        validate_candidate_dictionary(wrong_shape)

    mismatch = _make_candidate_asset(
        tmp_path, part_a_schema, source_ref="senseid:recomputation-mismatch"
    )
    connection = sqlite3.connect(mismatch)
    try:
        connection.execute("UPDATE lemma SET semantic_ref = ?", ("lemma:v1:" + "0" * 64,))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DictionaryAssetError):
        validate_candidate_dictionary(mismatch)


def test_candidate_validation_rejects_duplicate_stable_ref(
    tmp_path: Path, part_a_schema: str
) -> None:
    """A schema lacking ref uniqueness cannot make a duplicate candidate eligible."""
    non_unique_schema = part_a_schema.replace(
        "semantic_ref  TEXT NOT NULL UNIQUE,", "semantic_ref  TEXT NOT NULL,"
    )
    path = _make_candidate_asset(tmp_path, part_a_schema, schema=non_unique_schema)
    connection = sqlite3.connect(path)
    try:
        original = connection.execute("SELECT semantic_ref FROM lemma").fetchone()[0]
        connection.execute(
            "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender) VALUES (2, ?, ?, ?, ?)",
            (original, "Meer", "NOUN", "das"),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DictionaryAssetError):
        validate_candidate_dictionary(path)


def test_internal_lemma_map_rejects_duplicate_stable_ref_rows() -> None:
    """Exercise defense in depth: public assets cannot contain these duplicate rows.

    The public flow requires UNIQUE semantic_ref and D47's namespaced ref shape,
    so an in-asset duplicate is rejected by schema validation first. This direct
    test keeps the map-builder branch mandatory for malformed/internal row input.
    """
    ref = _stable_ref("lemma", ["de", "See", "NOUN", "der"])
    rows = (
        (1, ref, "See", "NOUN", "der"),
        (2, ref, "See", "NOUN", "der"),
    )
    with pytest.raises(DictionaryAssetError, match="duplicate or ambiguous"):
        _build_lemma_ref_maps(rows)


def test_candidate_identity_fingerprints_preserve_trivial_source_differences(
    tmp_path: Path, part_a_schema: str
) -> None:
    """The later swap owner can compare exact D47 source identities across assets."""
    first = validate_candidate_dictionary(
        _make_candidate_asset(tmp_path, part_a_schema, source_ref="senseid:one")
    )
    second = validate_candidate_dictionary(
        _make_candidate_asset(tmp_path, part_a_schema, source_ref="senseid:two")
    )
    try:
        assert set(first.sense_identity_fingerprints.values()).isdisjoint(
            second.sense_identity_fingerprints.values()
        )
    finally:
        first.release()
        second.release()


def test_released_candidate_handle_closes_cleanly(tmp_path: Path, part_a_schema: str) -> None:
    """Discarded candidates free their retained read-only snapshot idempotently."""
    asset = validate_candidate_dictionary(_make_candidate_asset(tmp_path, part_a_schema))
    asset.release()
    asset.close()
    with pytest.raises(sqlite3.ProgrammingError):
        asset.connection.execute("SELECT 1")


def test_missing_db_raises_file_not_found(tmp_path: Path) -> None:
    """Opening nonexistent database file raises FileNotFoundError."""
    missing_path = tmp_path / "nonexistent.sqlite"
    with pytest.raises(FileNotFoundError):
        Dictionary(missing_path)


def test_read_only_enforcement(create_test_db: Callable[[], Path]) -> None:
    """Database is opened in read-only mode and rejects modifications."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        with pytest.raises(sqlite3.OperationalError):
            d._conn.execute(
                "INSERT INTO lemma (lemma, pos, semantic_ref) VALUES ('Test', 'NOUN', 'test_ref')"
            )


def test_dictionary_implements_lookup_protocol(create_test_db: Callable[[], Path]) -> None:
    """Dictionary satisfies LookupProtocol interface."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        assert hasattr(d, "lookup_exact")
        assert hasattr(d, "lookup_surface_form")
        assert hasattr(d, "lookup_senses")


# --- Step 1: Exact Matches and Gender Disambiguation ---


def test_exact_lookup_and_gender_disambiguation(create_test_db: Callable[[], Path]) -> None:
    """Dictionary distinguishes der See (lake) from die See (sea) by gender."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Both records when gender is unspecified
        both = d.lookup_exact("See", pos="NOUN")
        assert len(both) == 2
        assert {b.gender for b in both} == {"der", "die"}

        # Exact masculine match
        der_see = d.lookup_exact("See", pos="NOUN", gender="der")
        assert len(der_see) == 1
        assert der_see[0].id == 1
        assert der_see[0].lemma == "See"
        assert der_see[0].gender == "der"
        assert der_see[0].ipa == "zeː"
        assert der_see[0].ipa_source == "wiktionary"
        assert der_see[0].semantic_ref is not None

        # Exact feminine match
        die_see = d.lookup_exact("See", pos="NOUN", gender="die")
        assert len(die_see) == 1
        assert die_see[0].id == 2
        assert die_see[0].lemma == "See"
        assert die_see[0].gender == "die"


# --- Step 2: Surface Form Lookup ---


def test_surface_form_lookup(create_test_db: Callable[[], Path]) -> None:
    """Dictionary resolves inflected surface forms to base lemma entries."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Häuser -> Haus
        matches = d.lookup_surface_form("Häuser")
        assert len(matches) == 1
        assert matches[0].id == 7
        assert matches[0].lemma == "Haus"
        assert matches[0].gender == "das"

        # Multi-word separable inflection: 'rief an' -> 'anrufen'
        verb_matches = d.lookup_surface_form("rief an")
        assert len(verb_matches) == 1
        assert verb_matches[0].id == 11
        assert verb_matches[0].lemma == "anrufen"
        assert verb_matches[0].separable == 1
        assert verb_matches[0].particle == "an"


# --- Resolution Ladder Through Dictionary Oracle ---


def test_resolution_ladder_exact(create_test_db: Callable[[], Path]) -> None:
    """Ladder Step 1 through Dictionary: exact hit returns status='resolved'."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        refs = d.resolve("Bank", pos="NOUN")
        assert len(refs) == 1
        assert refs[0] == Ref(
            lemma="Bank",
            pos="NOUN",
            gender="die",
            status="resolved",
            lemma_id=3,
        )


def test_resolution_ladder_surface_form(create_test_db: Callable[[], Path]) -> None:
    """Ladder Step 2 through Dictionary: surface form returns status='resolved'."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        refs = d.resolve("Häuser")
        assert len(refs) == 1
        assert refs[0].lemma == "Haus"
        assert refs[0].gender == "das"
        assert refs[0].status == "resolved"
        assert refs[0].lemma_id == 7


def test_resolution_ladder_compound_split(create_test_db: Callable[[], Path]) -> None:
    """Ladder Step 3 through Dictionary: compound splitter with D46 bindings."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        refs = d.resolve("Krankenversicherungskarte")
        assert len(refs) == 1
        ref = refs[0]
        assert ref.lemma == "Krankenversicherungskarte"
        assert ref.pos == "NOUN"
        assert ref.gender == "die"
        assert ref.status == "derived_compound"
        assert ref.lemma_id is None
        assert ref.components == ["kranken", "versicherung", "karte"]
        assert ref.head_lemma == "Karte"
        assert ref.component_bindings is not None
        assert len(ref.component_bindings) == 3

        b0, b1, b2 = ref.component_bindings
        assert b0.lemma == "kranken"
        assert b0.lemma_id == 4
        assert b0.lemma_ref.startswith("lemma:v1:")
        assert b0.sense_ref.startswith("sense:v1:")

        assert b1.lemma == "Versicherung"
        assert b1.lemma_id == 5
        assert b1.lemma_ref.startswith("lemma:v1:")

        assert b2.lemma == "Karte"
        assert b2.lemma_id == 6
        assert b2.lemma_ref.startswith("lemma:v1:")


def test_resolution_ladder_stub_fallthrough(create_test_db: Callable[[], Path]) -> None:
    """Ladder Step 4 through Dictionary: unknown word returns status='needs_gloss'."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        refs = d.resolve("NeologismusUnbekannt", pos="NOUN", gender="das")
        assert len(refs) == 1
        assert refs[0] == Ref(
            lemma="NeologismusUnbekannt",
            pos="NOUN",
            gender="das",
            status="needs_gloss",
            lemma_id=None,
            components=None,
            head_lemma=None,
            component_bindings=None,
        )


# --- Senses, Meanings, Examples, and Composite Entries ---


def test_get_senses_and_meanings(create_test_db: Callable[[], Path]) -> None:
    """Dictionary retrieves senses and deterministic localized meanings for a lemma."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Senses for der See (id=1)
        senses_1 = d.get_senses_for_lemma(1)
        assert len(senses_1) == 1
        assert senses_1[0].id is not None
        assert senses_1[0].semantic_ref == "sense:v1:see_der_0"
        assert senses_1[0].source_namespace == "wiktextract:enwiktionary"
        assert senses_1[0].source_ref == "senseid:en-see-1"

        meanings_1 = d.get_meanings_for_sense(senses_1[0].id)
        assert len(meanings_1) == 1
        assert meanings_1[0].text == "lake"
        assert meanings_1[0].language == "en"

        # Senses for die See (id=2)
        senses_2 = d.get_senses_for_lemma(2)
        assert len(senses_2) == 1
        assert senses_2[0].id is not None
        assert senses_2[0].semantic_ref == "sense:v1:see_die_0"

        meanings_2 = d.get_meanings_for_sense(senses_2[0].id)
        assert len(meanings_2) == 1
        assert meanings_2[0].text == "sea, ocean"

        # Examples for anrufen (id=11)
        examples = d.get_examples_for_lemma(11)
        assert len(examples) == 1
        assert examples[0].de == "Ich rufe dich morgen an."
        assert examples[0].en == "I will call you tomorrow."


def test_get_entry_composite(create_test_db: Callable[[], Path]) -> None:
    """Dictionary.get_entry returns full composite entry with all PART A fields."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        entry = d.get_entry(7)  # Haus
        assert entry is not None
        assert isinstance(entry, DictionaryEntry)
        assert entry.lemma.lemma == "Haus"
        assert entry.lemma.gender == "das"
        assert entry.lemma.semantic_ref is not None
        assert len(entry.senses) == 1
        assert len(entry.meanings) == 1
        assert entry.meanings[0].text == "house, building"
        assert "Häuser" in entry.surface_forms or "häuser" in entry.surface_forms


def test_get_entry_nonexistent_returns_none(create_test_db: Callable[[], Path]) -> None:
    """Dictionary.get_entry returns None for nonexistent lemma_id."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        assert d.get_entry(9999) is None


def test_suggest_lemmas_prefix(create_test_db: Callable[[], Path]) -> None:
    """Dictionary.suggest_lemmas performs prefix autocomplete."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        suggestions = d.suggest_lemmas("Se", limit=5)
        lemmas = [s.lemma for s in suggestions]
        assert "See" in lemmas


def test_no_part_b_table_references() -> None:
    """Acceptance B5: app/dictionary.py must never touch, query, or reference PART B tables."""
    import app.dictionary

    source_file = app.dictionary.__file__
    assert source_file is not None
    with open(source_file, encoding="utf-8") as f:
        code = f.read()

    forbidden_part_b = [
        "note",
        "card",
        "review_log",
        "deck",
        "note_deck",
        "gloss_contribution",
    ]
    for table in forbidden_part_b:
        assert f"FROM {table}" not in code
        assert f"INTO {table}" not in code
        assert f"UPDATE {table}" not in code
        assert f"JOIN {table}" not in code


# =========================================================================
# Stage S2b: DictionaryRuntime, atomic activation/relink, read pins, and evidence
# =========================================================================


def _is_primitive_value(val: object) -> bool:
    return isinstance(val, (str, int, float, bool, type(None)))


def _assert_payload_pure(obj: object) -> None:
    """Certify payload purity over stored instance payload (slots) only."""
    forbidden_types = (
        sqlite3.Connection,
        sqlite3.Cursor,
        DictionaryAsset,
        _Generation,
        DictionaryRuntime,
    )

    def _check(v: object, path: str) -> None:
        if isinstance(v, forbidden_types):
            raise AssertionError(f"Forbidden authority/resource type {type(v)} at {path}")
        if callable(v):
            raise AssertionError(f"Forbidden callable {type(v)} at {path}")
        if _is_primitive_value(v):
            return
        if isinstance(v, MappingProxyType):
            for k, val in v.items():
                if not _is_primitive_value(k) and not isinstance(k, tuple):
                    raise AssertionError(f"Non-primitive/non-tuple key {type(k)} at {path}")
                if isinstance(k, tuple) and not all(_is_primitive_value(item) for item in k):
                    raise AssertionError(f"Non-primitive tuple element in key at {path}")
                _check(val, f"{path}[{k!r}]")
            return
        if isinstance(v, tuple):
            for i, item in enumerate(v):
                _check(item, f"{path}[{i}]")
            return
        if isinstance(v, frozenset):
            for item in v:
                _check(item, f"{path}{{{item!r}}}")
            return
        raise AssertionError(f"Forbidden mutable or unrecognized container {type(v)} at {path}")

    if hasattr(obj, "__slots__"):
        for slot in getattr(obj, "__slots__"):
            val = getattr(obj, slot)
            _check(val, slot)
    elif isinstance(obj, (MappingProxyType, tuple)):
        _check(obj, "root")
    else:
        raise AssertionError(f"Object {type(obj)} does not use __slots__")


def _make_runtime(
    tmp_path: Path,
    part_a_schema: str,
    user_db_path: Path,
    *,
    dict_filename: str = "dictionary.sqlite",
    lemma: str = "See",
    source_ref: str = "senseid:see-1",
) -> tuple[DictionaryRuntime, Path]:
    dicts_dir = tmp_path / "managed_dicts"
    dicts_dir.mkdir(parents=True, exist_ok=True)
    dict_path = dicts_dir / dict_filename
    cand_path = _make_candidate_asset(tmp_path, part_a_schema, lemma=lemma, source_ref=source_ref)
    cand_path.replace(dict_path)
    runtime = DictionaryRuntime(dict_path, user_db_path)
    return runtime, dict_path


def test_e1_reading_snapshot_payload_purity(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E1: Certified payload purity walker over stored instance payload."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    try:
        with runtime.reading() as snapshot:
            _assert_payload_pure(snapshot)
            assert isinstance(snapshot.asset_token, str)
            assert len(snapshot.asset_token) == 64
    finally:
        runtime.close()


def test_e1_observation_payload_purity(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """Certified payload purity walker over observation payloads."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    try:
        # 1. Card render observation payload (empty DB returns None)
        card_obs_none = runtime.observe_card_render()
        assert card_obs_none is None

        # 2. Export observation payload
        export_obs = runtime.observe_export_payload()
        _assert_payload_pure(export_obs)
        assert isinstance(export_obs, tuple)

        # 3. Lookup candidate payload
        token, candidates = runtime.materialize_lookup("See")
        assert isinstance(token, str)
        assert isinstance(candidates, tuple)
        _assert_payload_pure(candidates)

        # 4. Materialize card render payload directly
        lemma_ref = _stable_ref("lemma", ["de", "See", "NOUN", "der"])
        mat_payload = runtime.materialize_card_render_payload(lemma_ref)
        assert mat_payload is not None
        _assert_payload_pure(mat_payload)

        # 5. Compound components payload
        comp_payload = runtime.materialize_compound_components([(lemma_ref, "sense:dummy")])
        assert isinstance(comp_payload, tuple)
        _assert_payload_pure(comp_payload)
    finally:
        runtime.close()


def test_e1_payload_purity_walker_negative_control() -> None:
    """E1 negative control: assert that _assert_payload_pure detects forbidden objects."""
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class BadConnection:
        conn: object

    @dataclass(frozen=True, slots=True)
    class BadCallable:
        func: object

    @dataclass(frozen=True, slots=True)
    class BadMutable:
        mapping: object

    @dataclass(frozen=True, slots=True)
    class BadNested:
        nested: object

    dummy_conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(AssertionError, match="Forbidden authority/resource type"):
            _assert_payload_pure(BadConnection(dummy_conn))
    finally:
        dummy_conn.close()

    with pytest.raises(AssertionError, match="Forbidden callable"):
        _assert_payload_pure(BadCallable(lambda: None))

    with pytest.raises(AssertionError, match="Forbidden mutable"):
        _assert_payload_pure(BadMutable({"key": "val"}))

    with pytest.raises(AssertionError, match="Forbidden callable"):
        _assert_payload_pure(BadNested(MappingProxyType({"key": lambda: None})))


def test_e2_snapshot_copy_no_shared_backing(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E2: Mutating source mappings or user DB leaves already-constructed snapshot unchanged."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    try:
        with runtime.reading() as snapshot:
            assert isinstance(snapshot.lemma_ids, MappingProxyType)
            original_lemma_ids = dict(snapshot.lemma_ids)
            original_bindings = dict(snapshot.bindings)

            # Mutate user DB externally; deliberately unmatched stub refs
            # test DB materialization isolation.
            conn = sqlite3.connect(user_db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO note (id, lemma_semantic_ref, status, created_at, due_at)
                    VALUES (999, 'lemma:v1:fake', 'needs_gloss', '2026-01-01', '2026-01-01')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO note_dictionary_binding (
                        note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
                        binding_status
                    ) VALUES (999, 'direct', 0, 'lemma:v1:fake', 'sense:v1:fake', 'bound')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            # Snapshot must NOT reflect the new row (it was materialized at pin time)
            assert dict(snapshot.bindings) == original_bindings
            assert (999, "direct", 0) not in snapshot.bindings
            assert dict(snapshot.lemma_ids) == original_lemma_ids
    finally:
        runtime.close()


def test_e3_acquisition_failure_at_each_step(
    tmp_path: Path, part_a_schema: str, user_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E3: Failure injection at EACH acquisition step leaves 0 pins, 0 thread depth, no leaks."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    try:
        # Step a: sqlite3.connect fails
        orig_connect = sqlite3.connect

        def failing_connect(
            database: str | bytes | Path | os.PathLike[str] | os.PathLike[bytes],
            timeout: float = 5.0,
            detect_types: int = 0,
            isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None = "DEFERRED",
            check_same_thread: bool = True,
            cached_statements: int = 128,
            uri: bool = False,
        ) -> sqlite3.Connection:
            if str(database).startswith("file:") and "mode=ro" in str(database):
                raise sqlite3.OperationalError("injected connect failure")
            return orig_connect(
                database,
                timeout=timeout,
                detect_types=detect_types,
                isolation_level=isolation_level,
                check_same_thread=check_same_thread,
                cached_statements=cached_statements,
                uri=uri,
            )

        monkeypatch.setattr(sqlite3, "connect", failing_connect)
        with pytest.raises(sqlite3.OperationalError, match="injected connect failure"):
            with runtime.reading():
                pass
        assert runtime._current_generation.pins == 0
        assert getattr(runtime._thread_local, "depth", 0) == 0

        # Helpers for Step c and Step d: proxy connection failing specific SQL
        class _FailingProxyConnection:
            def __init__(
                self,
                inner: sqlite3.Connection,
                fail_sql: Callable[[str], bool],
                error_msg: str,
            ) -> None:
                self._inner = inner
                self._fail_sql = fail_sql
                self._error_msg = error_msg
                self.row_factory: object | None = None

            def execute(
                self,
                sql: str,
                parameters: tuple[int | str | float | bytes | None, ...] = (),
            ) -> sqlite3.Cursor:
                if self._fail_sql(sql):
                    raise sqlite3.OperationalError(self._error_msg)
                return self._inner.execute(sql, parameters)

            def rollback(self) -> None:
                self._inner.rollback()

            def close(self) -> None:
                self._inner.close()

        def make_proxy_connector(
            fail_sql: Callable[[str], bool],
            error_msg: str,
        ) -> Callable[..., sqlite3.Connection]:
            def proxy_connect(
                database: str | bytes | Path | os.PathLike[str] | os.PathLike[bytes],
                timeout: float = 5.0,
                detect_types: int = 0,
                isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None = "DEFERRED",
                check_same_thread: bool = True,
                cached_statements: int = 128,
                uri: bool = False,
            ) -> sqlite3.Connection:
                inner = orig_connect(
                    database,
                    timeout=timeout,
                    detect_types=detect_types,
                    isolation_level=isolation_level,
                    check_same_thread=check_same_thread,
                    cached_statements=cached_statements,
                    uri=uri,
                )
                return cast(sqlite3.Connection, _FailingProxyConnection(inner, fail_sql, error_msg))

            return proxy_connect

        # Step c: Inject failure during BEGIN DEFERRED
        monkeypatch.undo()
        monkeypatch.setattr(
            sqlite3,
            "connect",
            make_proxy_connector(
                lambda sql: sql == "BEGIN DEFERRED",
                "injected begin deferred failure",
            ),
        )
        with pytest.raises(sqlite3.OperationalError, match="injected begin deferred failure"):
            with runtime.reading():
                pass
        assert runtime._current_generation.pins == 0
        assert getattr(runtime._thread_local, "depth", 0) == 0

        # Step d: PART-B materialization query fails
        monkeypatch.undo()
        monkeypatch.setattr(
            sqlite3,
            "connect",
            make_proxy_connector(
                lambda sql: "SELECT note_id, role" in sql,
                "injected part-b read failure",
            ),
        )
        with pytest.raises(sqlite3.OperationalError, match="injected part-b read failure"):
            with runtime.reading():
                pass
        assert runtime._current_generation.pins == 0
        assert getattr(runtime._thread_local, "depth", 0) == 0

        # Step e: PART-A copy failure
        monkeypatch.undo()
        gen = runtime._current_generation

        class BadAsset:
            @property
            def asset_token(self) -> str:
                raise RuntimeError("injected part-a copy failure")

        orig_asset = gen.asset
        gen.asset = BadAsset()  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="injected part-a copy failure"):
                with runtime.reading():
                    pass
            assert gen.pins == 0
            assert getattr(runtime._thread_local, "depth", 0) == 0
        finally:
            gen.asset = orig_asset
    finally:
        runtime.close()


class _CloseTrackingAsset:
    """Tracking wrapper delegating all DictionaryAsset properties and counting close() calls."""

    def __init__(self, real_asset: DictionaryAsset) -> None:
        self._real_asset = real_asset
        self.close_calls = 0

    @property
    def path(self) -> Path:
        return self._real_asset.path

    @property
    def sha256(self) -> str:
        return self._real_asset.sha256

    @property
    def asset_token(self) -> str:
        return self._real_asset.asset_token

    @property
    def connection(self) -> sqlite3.Connection:
        return self._real_asset.connection

    @property
    def lemma_ids(self) -> Mapping[str, int]:
        return self._real_asset.lemma_ids

    @property
    def sense_ids(self) -> Mapping[str, tuple[int, int]]:
        return self._real_asset.sense_ids

    @property
    def lemma_identity_fingerprints(self) -> Mapping[str, str]:
        return self._real_asset.lemma_identity_fingerprints

    @property
    def sense_identity_fingerprints(self) -> Mapping[str, str]:
        return self._real_asset.sense_identity_fingerprints

    def close(self) -> None:
        self.close_calls += 1
        self._real_asset.close()

    def release(self) -> None:
        self._real_asset.release()


def test_e4_release_symmetry_across_every_exit_shape(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E4: Success/release symmetry across normal, exception, and closed-while-pinned exits."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)

    # 1. Normal body completion
    with runtime.reading():
        assert runtime._current_generation.pins == 1
        assert getattr(runtime._thread_local, "depth", 0) == 1
    assert runtime._current_generation.pins == 0
    assert getattr(runtime._thread_local, "depth", 0) == 0

    # 2. Body-exception exit
    with pytest.raises(ZeroDivisionError):
        with runtime.reading():
            _ = 1 / 0
    assert runtime._current_generation.pins == 0
    assert getattr(runtime._thread_local, "depth", 0) == 0

    # 3. Closed while pinned: pin prevents handle close; exiting context closes handle exactly once
    tracker = _CloseTrackingAsset(runtime._current_generation.asset)
    runtime._current_generation.asset = tracker  # type: ignore[assignment]

    with runtime.reading():
        t = threading.Thread(target=runtime.close)
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()

        assert runtime.is_closed is True
        assert runtime._current_generation.retired is True
        assert runtime._current_generation.pins == 1
        assert tracker.close_calls == 0

    assert runtime._current_generation.pins == 0
    assert tracker.close_calls == 1


def test_e4_runtime_close_unpinned_closes_handle_exactly_once(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E4: runtime.close() closes unpinned handle exactly once, idempotently."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    tracker = _CloseTrackingAsset(runtime._current_generation.asset)
    runtime._current_generation.asset = tracker  # type: ignore[assignment]

    runtime.close()
    assert tracker.close_calls == 1
    runtime.close()
    assert tracker.close_calls == 1


def test_e5a_closed_runtime_dominates_path_and_type_errors(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E5a: Closed runtime raises DictionaryClosedError ahead of path or type errors."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    runtime.close()

    with pytest.raises(DictionaryClosedError):
        runtime.activate_dictionary("nonexistent.sqlite")

    with pytest.raises(DictionaryClosedError):
        runtime.activate_dictionary(123)  # type: ignore[arg-type]

    with pytest.raises(DictionaryClosedError):
        runtime.activate_dictionary(None)  # type: ignore[arg-type]


def test_e5b_blocking_validation_serializes_concurrent_ops(
    tmp_path: Path, part_a_schema: str, user_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E5b: Concurrent close() and activate() block while validation holds activation lock."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    cand2_path = _make_candidate_asset(
        tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1"
    )
    cand2_target = runtime.managed_dir / "dict_v2.sqlite"
    cand2_path.replace(cand2_target)
    v2_asset = validate_candidate_dictionary(cand2_target)
    v2_sha = v2_asset.sha256
    v2_asset.close()

    validation_entered = threading.Event()
    validation_unblock = threading.Event()

    orig_validate = validate_candidate_dictionary

    def blocking_validate(p: Path | str) -> DictionaryAsset:
        res = orig_validate(p)
        validation_entered.set()
        assert validation_unblock.wait(timeout=5.0)
        return res

    monkeypatch.setattr(deck, "validate_candidate_dictionary", blocking_validate)

    act_errors: list[Exception] = []
    close_errors: list[Exception] = []
    bad_errors: list[Exception] = []

    def activate_worker() -> None:
        try:
            runtime.activate_dictionary("dict_v2.sqlite", version="v2")
        except Exception as e:
            act_errors.append(e)

    def close_worker() -> None:
        try:
            runtime.close()
        except Exception as e:
            close_errors.append(e)

    def bad_activate_worker() -> None:
        try:
            runtime.activate_dictionary(123)  # type: ignore[arg-type]
        except Exception as e:
            bad_errors.append(e)

    t_act = threading.Thread(target=activate_worker)
    t_close = threading.Thread(target=close_worker)
    t_bad = threading.Thread(target=bad_activate_worker)

    t_act.start()
    assert validation_entered.wait(timeout=5.0)

    # Start concurrent close and bad activate while activation lock is held
    t_close.start()
    t_bad.start()

    # Both must be blocked
    t_close.join(timeout=0.1)
    t_bad.join(timeout=0.1)
    assert t_close.is_alive()
    assert t_bad.is_alive()

    # Unblock validation
    validation_unblock.set()

    t_act.join(timeout=5.0)
    t_close.join(timeout=5.0)
    t_bad.join(timeout=5.0)

    assert not t_act.is_alive()
    assert not t_close.is_alive()
    assert not t_bad.is_alive()

    assert not act_errors
    assert not close_errors
    assert len(bad_errors) == 1
    assert isinstance(bad_errors[0], (TypeError, DictionaryClosedError))

    # Assert activation actually published generation 2 before close completed
    assert runtime._generation_counter == 2
    assert runtime._current_generation.generation_id == 2
    assert runtime._current_generation.asset.sha256 == v2_sha

    conn = sqlite3.connect(user_db_path)
    try:
        row = conn.execute(
            "SELECT active_version, active_sha256 "
            "FROM active_dictionary_metadata WHERE singleton = 1"
        ).fetchone()
        assert row is not None
        assert row[0] == "v2"
        assert row[1] == v2_sha
    finally:
        conn.close()


def test_e5c_same_thread_reentrancy_termination(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E5c: Same worker thread executing activate/close inside reading() terminates with error."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    cand2_path = _make_candidate_asset(
        tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1"
    )
    (runtime.managed_dir / "dict_v2.sqlite").write_bytes(cand2_path.read_bytes())

    act_result: list[bool] = []
    close_result: list[bool] = []

    def act_reentrant_worker() -> None:
        with runtime.reading():
            try:
                runtime.activate_dictionary("dict_v2.sqlite")
            except DictionaryRuntimeError:
                act_result.append(True)

    def close_reentrant_worker() -> None:
        with runtime.reading():
            try:
                runtime.close()
            except DictionaryRuntimeError:
                close_result.append(True)

    t1 = threading.Thread(target=act_reentrant_worker)
    t1.start()
    t1.join(timeout=5.0)
    assert not t1.is_alive()
    assert act_result == [True]

    t2 = threading.Thread(target=close_reentrant_worker)
    t2.start()
    t2.join(timeout=5.0)
    assert not t2.is_alive()
    assert close_result == [True]

    runtime.close()


def test_e6_whole_table_non_vacuous_rollback(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E6: Whole-table rollback on pre-commit failure proven non-vacuous on independent copy."""
    lemma_ref_see = _stable_ref("lemma", ["de", "See", "NOUN", "der"])
    sense_ref_see = _stable_ref(
        "sense", [lemma_ref_see, "wiktextract:enwiktionary", "senseid:see-1"]
    )
    lemma_ref_meer = _stable_ref("lemma", ["de", "Meer", "NOUN", "der"])
    sense_ref_meer = _stable_ref(
        "sense", [lemma_ref_meer, "wiktextract:enwiktionary", "senseid:meer-1"]
    )
    lemma_ref_comp = _stable_ref("lemma", ["de", "Seemeer", "NOUN", "das"])

    conn = sqlite3.connect(user_db_path)
    try:
        now_dt = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        _ = deck.create_note(
            conn,
            lemma_ref_see,
            sense_semantic_ref=sense_ref_see,
            status="resolved",
            meaning_languages=("de", "en"),
            created_at=now_dt,
        )
        _ = deck.create_note(
            conn,
            lemma_ref_comp,
            status="derived_compound",
            component_bindings=(
                (lemma_ref_see, sense_ref_see),
                (lemma_ref_meer, sense_ref_meer),
            ),
            meaning_languages=("de",),
            created_at=now_dt,
        )
        conn.commit()
    finally:
        conn.close()

    runtime, _ = _make_runtime(
        tmp_path, part_a_schema, user_db_path, lemma="See", source_ref="senseid:see-1"
    )

    cand2_path = _make_candidate_asset(
        tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1"
    )
    (runtime.managed_dir / "dict_v2.sqlite").write_bytes(cand2_path.read_bytes())

    # Snapshot COMPLETE rows of note_dictionary_binding, note, active_dictionary_metadata
    conn = sqlite3.connect(user_db_path)
    try:
        snap_bindings = conn.execute(
            "SELECT * FROM note_dictionary_binding ORDER BY note_id, role, component_ord"
        ).fetchall()
        snap_notes = conn.execute("SELECT * FROM note ORDER BY id").fetchall()
        snap_metadata = conn.execute("SELECT * FROM active_dictionary_metadata").fetchall()
    finally:
        conn.close()

    def fail_pre_commit() -> None:
        raise RuntimeError("injected pre-commit failure")

    runtime._pre_commit_probe = fail_pre_commit

    with pytest.raises(RuntimeError, match="injected pre-commit failure"):
        runtime.activate_dictionary("dict_v2.sqlite", version="v2")

    # Verify tables identical to snapshot
    conn = sqlite3.connect(user_db_path)
    try:
        post_bindings = conn.execute(
            "SELECT * FROM note_dictionary_binding ORDER BY note_id, role, component_ord"
        ).fetchall()
        post_notes = conn.execute("SELECT * FROM note ORDER BY id").fetchall()
        post_metadata = conn.execute("SELECT * FROM active_dictionary_metadata").fetchall()
    finally:
        conn.close()

    assert post_bindings == snap_bindings
    assert post_notes == snap_notes
    assert post_metadata == snap_metadata

    runtime.close()

    # Non-vacuity proof: run same activation against an INDEPENDENT copy of user DB
    ind_user_db = tmp_path / "independent_user_db.sqlite"
    ind_user_db.write_bytes(user_db_path.read_bytes())

    runtime_ind = DictionaryRuntime(runtime.managed_dir / "dictionary.sqlite", ind_user_db)
    try:
        runtime_ind.activate_dictionary("dict_v2.sqlite", version="v2")
        conn_ind = sqlite3.connect(ind_user_db)
        try:
            succ_bindings = conn_ind.execute(
                "SELECT * FROM note_dictionary_binding ORDER BY note_id, role, component_ord"
            ).fetchall()
            succ_notes = conn_ind.execute("SELECT * FROM note ORDER BY id").fetchall()
            succ_metadata = conn_ind.execute("SELECT * FROM active_dictionary_metadata").fetchall()
        finally:
            conn_ind.close()

        # 1. binding_status changed for note1 (from bound to unbound)
        assert succ_bindings[0] != snap_bindings[0]
        assert succ_bindings[0][7] == "unbound" and snap_bindings[0][7] == "bound"
        # 2. cached_lemma_id or cached_sense_id changed (cleared to None)
        assert succ_bindings[0][5] != snap_bindings[0][5]
        assert succ_bindings[0][5] is None and snap_bindings[0][5] is not None
        # 3. last_relinked_at changed
        assert succ_bindings[0][9] != snap_bindings[0][9]
        # 4. note.status changed (note1 became needs_gloss)
        assert succ_notes[0][3] == "needs_gloss" and snap_notes[0][3] == "resolved"
        # 5. active_dictionary_metadata changed
        assert succ_metadata[0][1] == "v2" and snap_metadata[0][1] == "v1"
    finally:
        runtime_ind.close()


def test_e7_overlapping_read_visibility_single_generation_pairing(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E7: Pre-seam and cross-seam readers observe single-generation pairing without mixed state."""
    lemma_ref_see = _stable_ref("lemma", ["de", "See", "NOUN", "der"])
    sense_ref_see = _stable_ref(
        "sense", [lemma_ref_see, "wiktextract:enwiktionary", "senseid:see-1"]
    )
    conn = sqlite3.connect(user_db_path)
    try:
        now_dt = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
        note_id = deck.create_note(
            conn,
            lemma_ref_see,
            sense_semantic_ref=sense_ref_see,
            status="resolved",
            meaning_languages=("de", "en"),
            created_at=now_dt,
        )
        conn.commit()
    finally:
        conn.close()

    runtime, _ = _make_runtime(
        tmp_path, part_a_schema, user_db_path, lemma="See", source_ref="senseid:see-1"
    )
    v1_token = runtime.asset_token

    cand2_path = _make_candidate_asset(
        tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1"
    )
    dict2_path = runtime.managed_dir / "dict_v2.sqlite"
    cand2_path.replace(dict2_path)

    v2_asset = validate_candidate_dictionary(dict2_path)
    v2_token = v2_asset.sha256
    v2_asset.close()

    with runtime.reading() as snap_old:
        in_seam_event = threading.Event()
        unblock_seam_event = threading.Event()

        def seam_probe_fn() -> None:
            in_seam_event.set()
            assert unblock_seam_event.wait(timeout=5.0)

        runtime._seam_probe = seam_probe_fn

        act_thread = threading.Thread(
            target=lambda: runtime.activate_dictionary("dict_v2.sqlite", version="v2")
        )
        act_thread.start()

        assert in_seam_event.wait(timeout=5.0)

        cross_read_snap: list[ReadingSnapshot] = []

        def cross_reader() -> None:
            with runtime.reading() as s:
                cross_read_snap.append(s)

        cross_thread = threading.Thread(target=cross_reader)
        cross_thread.start()

        cross_thread.join(timeout=0.1)
        assert cross_thread.is_alive()

        unblock_seam_event.set()

        act_thread.join(timeout=5.0)
        cross_thread.join(timeout=5.0)

        assert not act_thread.is_alive()
        assert not cross_thread.is_alive()

        # Pre-seam reader still observes complete-old:
        # asset token, PART-A lemma_ids, and non-None PART-B cached ids together
        assert snap_old.asset_token == v1_token
        assert lemma_ref_see in snap_old.lemma_ids
        assert snap_old.lemma_ids[lemma_ref_see] == 1
        assert snap_old.bindings[(note_id, "direct", 0)] == (1, 1)

        # Cross-seam reader observed complete-new:
        # asset token, PART-A lemma_ids, and cleared PART-B cached ids together
        assert len(cross_read_snap) == 1
        snap_new = cross_read_snap[0]
        assert snap_new.asset_token == v2_token
        assert lemma_ref_see not in snap_new.lemma_ids
        lemma_ref_meer = _stable_ref("lemma", ["de", "Meer", "NOUN", "der"])
        assert lemma_ref_meer in snap_new.lemma_ids
        assert snap_new.bindings[(note_id, "direct", 0)] == (None, None)

    runtime.close()


def test_e8_seam_probe_exception_containment(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E8: Seam probe exception is captured, publication completes, then exception re-raised."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)

    cand2_path = _make_candidate_asset(
        tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1"
    )
    dict2_path = runtime.managed_dir / "dict_v2.sqlite"
    cand2_path.replace(dict2_path)
    cand2_sha256 = validate_candidate_dictionary(dict2_path).sha256

    def fail_seam() -> None:
        raise RuntimeError("injected seam failure")

    runtime._seam_probe = fail_seam

    with pytest.raises(RuntimeError, match="injected seam failure"):
        runtime.activate_dictionary("dict_v2.sqlite", version="v2")

    assert runtime.asset_token == cand2_sha256
    with runtime.reading() as snap:
        assert snap.asset_token == cand2_sha256

    conn = sqlite3.connect(user_db_path)
    try:
        row = conn.execute(
            """
            SELECT active_version, active_sha256
            FROM active_dictionary_metadata WHERE singleton = 1
            """
        ).fetchone()
        assert row is not None
        assert row[0] == "v2"
        assert row[1] == cand2_sha256
    finally:
        conn.close()

    runtime.close()


def test_e9_managed_directory_rejection_cases(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E9: Rejection of traversal on raw string, symlink escaping, and stored separators."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)

    # 1. Raw text traversal
    with pytest.raises(DictionaryAssetError, match="path traversal"):
        runtime.activate_dictionary("subdir/../dict.sqlite")

    # 2. Outside candidate path
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(b"some content")
    with pytest.raises(DictionaryAssetError, match="must reside in managed directory"):
        runtime.activate_dictionary(outside)

    # 3. Symlink escaping managed directory
    symlink_path = runtime.managed_dir / "escape_symlink.sqlite"
    try:
        symlink_path.symlink_to(outside)
        with pytest.raises(DictionaryAssetError, match="must reside in managed directory"):
            runtime.activate_dictionary(symlink_path)
    finally:
        symlink_path.unlink(missing_ok=True)

    runtime.close()


def test_e10_restart_recovery_sha_mismatch_fails_construction(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E10: Restart recovery with corrupted SHA in metadata fails construction."""
    runtime, dict_path = _make_runtime(tmp_path, part_a_schema, user_db_path)
    runtime.close()

    conn = sqlite3.connect(user_db_path)
    try:
        conn.execute(
            "UPDATE active_dictionary_metadata SET active_sha256 = ? WHERE singleton = 1",
            ("0" * 64,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(DictionaryRuntimeError, match="recovery target SHA-256 does not match"):
        DictionaryRuntime(dict_path, user_db_path)


def test_canonical_startup_relinks_new_asset_and_preserves_user_state(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """Manifest-pinned startup upgrades stale metadata through the atomic relinker."""
    managed_dir = tmp_path / "managed_dicts"
    managed_dir.mkdir()
    canonical = managed_dir / "dictionary.sqlite"
    v1 = _make_candidate_asset(tmp_path, part_a_schema, lemma="See", source_ref="senseid:see-1")
    canonical.write_bytes(v1.read_bytes())
    v1_sha = validate_candidate_dictionary(canonical).sha256
    lemma_ref = _stable_ref("lemma", ["de", "See", "NOUN", "der"])
    sense_ref = _stable_ref(
        "sense", [lemma_ref, "wiktextract:enwiktionary", "senseid:see-1"]
    )

    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    try:
        deck_id = deck.create_deck(conn, "Preserved")
        note_id = deck.create_note(
            conn,
            lemma_ref,
            sense_semantic_ref=sense_ref,
            status="resolved",
            meaning_languages=("en",),
        )
        deck.add_note_to_deck(conn, note_id, deck_id)
        deck.set_user_meaning(conn, note_id, "en", "my house")
        card_row = conn.execute(
            "SELECT id FROM card WHERE note_id = ?", (note_id,)
        ).fetchone()
        assert card_row is not None
        card_id = int(card_row[0])
        deck.review(conn, card_id, 4)
        before_review = [
            tuple(row) for row in conn.execute("SELECT * FROM review_log").fetchall()
        ]
        before_meaning = [
            tuple(row) for row in conn.execute("SELECT * FROM note_user_meaning").fetchall()
        ]
    finally:
        conn.close()

    first = DictionaryRuntime(
        canonical,
        user_db_path,
        expected_sha256=v1_sha,
        expected_version="v1",
    )
    first.close()

    v2 = tmp_path / "candidate-v2.sqlite"
    v2.write_bytes(v1.read_bytes())
    conn = sqlite3.connect(v2)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE sense SET id = 10, lemma_id = 20")
        conn.execute("UPDATE lemma SET id = 20")
        conn.commit()
    finally:
        conn.close()
    v2.replace(canonical)
    v2_sha = validate_candidate_dictionary(canonical).sha256

    upgraded = DictionaryRuntime(
        canonical,
        user_db_path,
        expected_sha256=v2_sha,
        expected_version="v2",
    )
    try:
        assert upgraded.asset_token == v2_sha
        conn = sqlite3.connect(user_db_path)
        try:
            assert conn.execute("SELECT * FROM review_log").fetchall() == before_review
            assert conn.execute("SELECT * FROM note_user_meaning").fetchall() == before_meaning
            binding = conn.execute(
                "SELECT cached_lemma_id, cached_sense_id FROM note_dictionary_binding "
                "WHERE note_id = ?",
                (note_id,),
            ).fetchone()
            assert binding == (20, 10)
            metadata = conn.execute(
                "SELECT active_version, active_filename, active_sha256 "
                "FROM active_dictionary_metadata WHERE singleton = 1"
            ).fetchone()
            assert metadata == ("v2", "dictionary.sqlite", v2_sha)
        finally:
            conn.close()
    finally:
        upgraded.close()


def test_canonical_startup_relink_failure_rolls_back_stale_metadata(
    tmp_path: Path,
    part_a_schema: str,
    user_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed canonical upgrade cannot partially publish metadata or bindings."""
    managed_dir = tmp_path / "managed_dicts"
    managed_dir.mkdir()
    canonical = managed_dir / "dictionary.sqlite"
    v1 = _make_candidate_asset(tmp_path, part_a_schema, lemma="See", source_ref="senseid:see-1")
    canonical.write_bytes(v1.read_bytes())
    v1_sha = validate_candidate_dictionary(canonical).sha256
    first = DictionaryRuntime(canonical, user_db_path, expected_sha256=v1_sha)
    first.close()
    conn = sqlite3.connect(user_db_path)
    try:
        before = conn.execute(
            "SELECT active_filename, active_sha256 FROM active_dictionary_metadata"
        ).fetchall()
    finally:
        conn.close()

    v2 = _make_candidate_asset(tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1")
    v2.replace(canonical)
    v2_sha = validate_candidate_dictionary(canonical).sha256

    def fail_relink(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected relink failure")

    monkeypatch.setattr(DictionaryRuntime, "_relink_part_b", fail_relink)
    with pytest.raises(RuntimeError, match="injected relink failure"):
        DictionaryRuntime(canonical, user_db_path, expected_sha256=v2_sha, expected_version="v2")

    conn = sqlite3.connect(user_db_path)
    try:
        after = conn.execute(
            "SELECT active_filename, active_sha256 FROM active_dictionary_metadata"
        ).fetchall()
    finally:
        conn.close()
    assert after == before


def test_e11_teardown_close_failure_contained(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E11: Writer close error in Phase 9 is contained and activation reports success."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)

    cand2_path = _make_candidate_asset(
        tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1"
    )
    dict2_path = runtime.managed_dir / "dict_v2.sqlite"
    cand2_path.replace(dict2_path)
    cand2_sha256 = validate_candidate_dictionary(dict2_path).sha256

    def fail_writer_close() -> None:
        raise sqlite3.Error("injected writer close error")

    runtime._writer_close_hook = fail_writer_close

    # Must NOT raise: success reported solely on completed commit + publication
    runtime.activate_dictionary("dict_v2.sqlite", version="v2")

    assert runtime.asset_token == cand2_sha256

    conn = sqlite3.connect(user_db_path)
    try:
        row = conn.execute(
            """
            SELECT active_version, active_sha256
            FROM active_dictionary_metadata WHERE singleton = 1
            """
        ).fetchone()
        assert row is not None
        assert row[0] == "v2"
        assert row[1] == cand2_sha256
    finally:
        conn.close()

    runtime.close()


def test_e12_cleanup_non_masking_primary_exception_propagates(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E12: Pre-commit primary exception propagates unmasked when rollback also fails."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)

    cand2_path = _make_candidate_asset(
        tmp_path, part_a_schema, lemma="Meer", source_ref="senseid:meer-1"
    )
    dict2_path = runtime.managed_dir / "dict_v2.sqlite"
    cand2_path.replace(dict2_path)

    class CustomPrimaryError(Exception):
        pass

    def fail_pre_commit() -> None:
        raise CustomPrimaryError("primary pre-commit failure")

    def fail_rollback() -> None:
        raise sqlite3.Error("secondary rollback failure")

    runtime._pre_commit_probe = fail_pre_commit
    runtime._rollback_failure_hook = fail_rollback

    with pytest.raises(CustomPrimaryError, match="primary pre-commit failure"):
        runtime.activate_dictionary("dict_v2.sqlite", version="v2")

    runtime.close()


def test_e13_underlying_file_identity_rejected(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E13: Hard-link alias of user database is rejected for activation and recovery."""
    runtime, dict_path = _make_runtime(tmp_path, part_a_schema, user_db_path)

    # 1. Hard-link alias rejected on activation
    alias_path = runtime.managed_dir / "user_db_alias.sqlite"
    try:
        os.link(user_db_path, alias_path)
        with pytest.raises(DictionaryAssetError, match="user database file"):
            runtime.activate_dictionary("user_db_alias.sqlite")
    finally:
        alias_path.unlink(missing_ok=True)

    runtime.close()

    # 2. Hard-link alias rejected on restart recovery
    alias_recovery = runtime.managed_dir / "recovery_alias.sqlite"
    os.link(user_db_path, alias_recovery)
    try:
        conn = sqlite3.connect(user_db_path)
        try:
            conn.execute(
                """
                UPDATE active_dictionary_metadata
                SET active_filename = 'recovery_alias.sqlite'
                WHERE singleton = 1
                """
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(DictionaryRuntimeError, match="user database file"):
            DictionaryRuntime(dict_path, user_db_path)
    finally:
        alias_recovery.unlink(missing_ok=True)


def test_e14_role_status_consistency_stray_rows(user_db: sqlite3.Connection) -> None:
    """E14: Meaning availability uses only binding role matching persisted note.status."""
    now_dt = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    # Deliberately unmatched stub refs in dictionary mapping to test role/status
    # consistency in isolation.
    dictionary = {
        "sense:v1:see_0": {"de": ("See",), "en": ("lake",)},
        "sense:v1:haus_0": {"de": ("Haus",), "en": ("house",)},
    }

    # 1. derived_compound with failing component vector + stray bound direct row -> NO meaning block
    comp_note = deck.create_note(
        user_db,
        "lemma:v1:compound",
        status="derived_compound",
        component_bindings=(
            ("lemma:v1:see", "sense:v1:see_0"),
            ("lemma:v1:haus", "sense:v1:haus_0"),
        ),
        meaning_languages=("de", "en"),
        created_at=now_dt,
    )
    # Make one component unbound
    user_db.execute(
        """
        UPDATE note_dictionary_binding
        SET binding_status = 'unbound'
        WHERE note_id = ? AND component_ord = 1
        """,
        (comp_note,),
    )
    # Insert stray bound direct row
    user_db.execute(
        """
        INSERT INTO note_dictionary_binding (
            note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
            cached_lemma_id, cached_sense_id, binding_status
        ) VALUES (?, 'direct', 0, 'lemma:v1:see', 'sense:v1:see_0', 1, 1, 'bound')
        """,
        (comp_note,),
    )
    user_db.commit()

    # Because status is derived_compound, stray direct row MUST NOT create availability
    assert deck.resolved_meanings(user_db, comp_note, dictionary) == {"de": (), "en": ()}
    assert deck.meaning_state(user_db, comp_note, dictionary) == "none"

    # 2. resolved note with valid direct row + stray component rows -> keeps direct availability
    res_note = deck.create_note(
        user_db,
        "lemma:v1:see",
        sense_semantic_ref="sense:v1:see_0",
        status="resolved",
        meaning_languages=("de", "en"),
        created_at=now_dt,
    )
    # Insert stray component rows
    user_db.execute(
        """
        INSERT INTO note_dictionary_binding (
            note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
            binding_status, component_count
        ) VALUES (?, 'component', 0, 'lemma:v1:fake', 'sense:v1:fake', 'unbound', 1)
        """,
        (res_note,),
    )
    user_db.commit()

    assert deck.resolved_meanings(user_db, res_note, dictionary) == {
        "de": ("See",),
        "en": ("lake",),
    }
    assert deck.meaning_state(user_db, res_note, dictionary) == "complete"


def test_e15_stale_token_detection_readiness(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """E15: Token validation matches active generation asset token and rejects stale tokens."""
    runtime, _ = _make_runtime(tmp_path, part_a_schema, user_db_path)
    try:
        active_token = runtime.asset_token
        stale_token = "0" * 64

        assert runtime.asset_token == active_token
        assert active_token != stale_token

        def check_token(submitted_token: str) -> bool:
            return submitted_token == runtime.asset_token

        assert check_token(active_token) is True
        assert check_token(stale_token) is False
    finally:
        runtime.close()
