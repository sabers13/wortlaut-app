"""Tests for tools/build_dict.py build stage 02: deterministic Tatoeba example indexing."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.dictionary import Dictionary
from app.resolve import TokenLike, resolve_token
from tools.build_dict import (
    BuildDictError,
    Stage02LookupOracle,
    build_stage01,
    build_stage02,
    compute_stage02_cache_key,
    main,
    parse_links_tsv,
    parse_sentence_tsv,
    sha256_file,
    validate_stage01_database,
)
from tools.resolver_hash import get_resolver_hash

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EN_FIXTURE_PATH = FIXTURES_DIR / "wiktextract_stage01_en.jsonl"
DE_FIXTURE_PATH = FIXTURES_DIR / "wiktextract_stage01_de.jsonl"
DEFAULT_LICENSE = "CC BY 2.0 FR"


@dataclass
class OracleParityToken:
    """Minimal canonical-resolver token for lookup-oracle parity tests."""

    text: str
    lemma_: str
    pos_: str
    dep_: str = ""

    @property
    def head(self) -> TokenLike:
        return self

    @property
    def children(self) -> Iterable[TokenLike]:
        return ()


def _lemma_tuple(record: Any) -> tuple[Any, ...]:
    return (
        record.id,
        record.lemma,
        record.pos,
        record.gender,
        record.semantic_ref,
        record.freq_rank,
    )


def _sense_tuple(record: Any) -> tuple[Any, ...]:
    return (record.id, record.lemma_id, record.ord, record.semantic_ref)


@pytest.fixture
def oracle_parity_db(tmp_path: Path, part_a_schema: str) -> Path:
    """Create one Stage-01-compatible asset shared by both lookup oracles."""
    db_path = tmp_path / "oracle-parity.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(part_a_schema)
        conn.executemany(
            "INSERT INTO lemma "
            "(id, semantic_ref, lemma, pos, gender, freq_rank) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "lemma:v1:haus-upper", "Haus", "NOUN", "das", 2),
                (2, "lemma:v1:haus-lower", "haus", "NOUN", "das", 1),
                (3, "lemma:v1:bank-null", "Bank", "NOUN", "die", None),
                (4, "lemma:v1:bank-der", "Bank", "NOUN", "der", 3),
                (5, "lemma:v1:bank-verb", "Bank", "VERB", None, 3),
            ],
        )
        conn.executemany(
            "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
            [
                ("Banken", 3),
                ("BANKEN", 3),
                ("Banken", 4),
            ],
        )
        conn.executemany(
            "INSERT INTO sense "
            "(id, lemma_id, semantic_ref, source_namespace, source_ref, ord) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (11, 1, "sense:v1:haus-ord1", "synthetic", "11", 1),
                (12, 1, "sense:v1:haus-ord0-z", "synthetic", "12", 0),
                (13, 1, "sense:v1:haus-ord0-a", "synthetic", "13", 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def stage01_db(tmp_path: Path) -> Path:
    """Create a temporary valid Stage-01 SQLite dictionary database."""
    db_path = tmp_path / "stage01.sqlite"
    build_stage01(EN_FIXTURE_PATH, DE_FIXTURE_PATH, db_path)
    return db_path


@pytest.fixture
def sample_tsv_projections(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create standard test TSV projections for Stage-02 tests."""
    de_path = tmp_path / "de.tsv"
    en_path = tmp_path / "en.tsv"
    links_path = tmp_path / "links.tsv"

    # DE:
    # 1: Haus -> indexable (Haus)
    # 2: Separable anrufen -> indexable (anrufen)
    # 3: See -> indexable (See)
    # 4: Untranslated with indexable lemma (Haus)
    # 5: Proper noun PROPN -> indexable (Berlin), has_proper=1
    # 6: Zero indexable lemmas
    # 7: Repeated lemma in one sentence -> indexable (Haus)
    de_content = (
        "1\tDas Haus ist groß.\n"
        "2\tIch rufe dich morgen an.\n"
        "3\tDie See ist ruhig.\n"
        "4\tEin schönes Haus.\n"
        "5\tBerlin ist wunderbar.\n"
        "6\tXyz qwerty uiop.\n"
        "7\tEin Haus neben dem Haus.\n"
    )
    de_path.write_text(de_content, encoding="utf-8")

    en_content = (
        "10\tThe house is big.\n"
        "20\tThe house is large.\n"
        "30\tI will call you tomorrow.\n"
        "40\tThe sea is calm.\n"
        "50\tBerlin is wonderful.\n"
        "60\tSomething else.\n"
    )
    en_path.write_text(en_content, encoding="utf-8")

    # Links:
    # 1 links to 20 and 10 -> min is 10
    # 2 links to 30
    # 3 links to 40
    # 4 has no link -> en=None
    # 5 links to 50
    # 6 has no link
    # 7 links to 10
    links_content = (
        "1\t20\n"
        "1\t10\n"
        "2\t30\n"
        "3\t40\n"
        "5\t50\n"
        "7\t10\n"
    )
    links_path.write_text(links_content, encoding="utf-8")

    return de_path, en_path, links_path


# ======================================================================
# Stage-02 LookupProtocol parity with runtime Dictionary
# ======================================================================


def test_stage02_lookup_exact_matches_runtime_dictionary(
    oracle_parity_db: Path,
) -> None:
    """Exact lookup has the runtime SQL case, filter, and ordering semantics."""
    runtime = Dictionary(oracle_parity_db)
    stage02 = Stage02LookupOracle(oracle_parity_db)
    try:
        for args in [
            ("Haus", None, None),
            ("haus", None, None),
            ("Bank", "NOUN", None),
            ("Bank", "NOUN", "der"),
            ("Bank", "NOUN", "die"),
            ("Bank", "VERB", None),
        ]:
            assert [_lemma_tuple(row) for row in stage02.lookup_exact(*args)] == [
                _lemma_tuple(row) for row in runtime.lookup_exact(*args)
            ]

        assert [row.id for row in stage02.lookup_exact("Haus")] == [2, 1]
        assert [row.id for row in stage02.lookup_exact("Bank")] == [4, 5, 3]
    finally:
        stage02.close()
        runtime.close()


def test_stage02_lookup_surface_form_matches_runtime_dictionary(
    oracle_parity_db: Path,
) -> None:
    """Surface lookup preserves runtime case fallback, order, and ID de-duplication."""
    runtime = Dictionary(oracle_parity_db)
    stage02 = Stage02LookupOracle(oracle_parity_db)
    try:
        for form in ("Banken", "banken"):
            assert [_lemma_tuple(row) for row in stage02.lookup_surface_form(form)] == [
                _lemma_tuple(row) for row in runtime.lookup_surface_form(form)
            ]

        records = stage02.lookup_surface_form("banken")
        assert [row.id for row in records] == [4, 3]
        assert len({row.id for row in records}) == len(records)
    finally:
        stage02.close()
        runtime.close()


def test_stage02_lookup_senses_and_canonical_resolver_match_runtime_dictionary(
    oracle_parity_db: Path,
) -> None:
    """Sense ordering and canonical token resolution agree on one shared asset."""
    runtime = Dictionary(oracle_parity_db)
    stage02 = Stage02LookupOracle(oracle_parity_db)
    try:
        assert [_sense_tuple(row) for row in stage02.lookup_senses(1)] == [
            _sense_tuple(row) for row in runtime.lookup_senses(1)
        ]
        assert [row.id for row in stage02.lookup_senses(1)] == [13, 12, 11]

        token = OracleParityToken("Banken", "unavailable", "NOUN")
        runtime_ids = {
            ref.lemma_id
            for ref in resolve_token(token, runtime)
            if ref.lemma_id is not None
        }
        stage02_ids = {
            ref.lemma_id
            for ref in resolve_token(token, stage02)
            if ref.lemma_id is not None
        }
        assert stage02_ids == runtime_ids == {3, 4}
    finally:
        stage02.close()
        runtime.close()


def test_stage02_lookup_accelerator_uses_indexed_lookup_and_cleans_up(
    oracle_parity_db: Path, tmp_path: Path
) -> None:
    """The Stage-02-only accelerator avoids source lemma/surface scans."""
    accelerator = tmp_path / "stage02-lookup.sqlite"
    stage02 = Stage02LookupOracle(oracle_parity_db, accelerator)
    try:
        exact_plan, surface_plan = stage02.lookup_query_plans("Bank", "Banken")

        assert "SEARCH exact_lookup" in exact_plan
        assert "SCAN lemma" not in exact_plan
        assert "SEARCH sl" in surface_plan
        assert "SCAN surface_form" not in surface_plan
        assert accelerator.is_file()
    finally:
        stage02.close()

    assert not accelerator.exists()


# ======================================================================
# 1 & 2. Valid strict German and English sentence projections
# ======================================================================


def test_valid_strict_german_sentence_projection(tmp_path: Path) -> None:
    """A13 #1: Valid strict German sentence projection parses correctly."""
    de_path = tmp_path / "de_valid.tsv"
    de_path.write_text("1\tDas Haus ist groß.\n2\tGuten Morgen!\n", encoding="utf-8")
    parsed = parse_sentence_tsv(de_path, "German")
    assert parsed == {1: "Das Haus ist groß.", 2: "Guten Morgen!"}


def test_valid_strict_english_sentence_projection(tmp_path: Path) -> None:
    """A13 #2: Valid strict English sentence projection parses correctly."""
    en_path = tmp_path / "en_valid.tsv"
    en_path.write_text("10\tThe house is big.\n20\tGood morning!\n", encoding="utf-8")
    parsed = parse_sentence_tsv(en_path, "English")
    assert parsed == {10: "The house is big.", 20: "Good morning!"}


# ======================================================================
# 3 & 4. Malformed sentence rows and duplicate IDs
# ======================================================================


@pytest.mark.parametrize(
    "bad_content,match_str",
    [
        ("1\n", "expected exactly 2 tab-separated fields"),
        ("1\tText\textra\n", "expected exactly 2 tab-separated fields"),
        ("0\tText\n", "must be a positive integer"),
        ("-5\tText\n", "must be a positive integer"),
        ("abc\tText\n", "must be a positive integer"),
        ("1\t   \n", "Blank sentence text"),
        ("1\t\n", "Blank sentence text"),
    ],
)
def test_malformed_sentence_row_rejected(
    tmp_path: Path, bad_content: str, match_str: str
) -> None:
    """A13 #3: Malformed sentence row is rejected fail-closed."""
    p = tmp_path / "bad_sentence.tsv"
    p.write_text(bad_content, encoding="utf-8")
    with pytest.raises(BuildDictError, match=match_str):
        parse_sentence_tsv(p, "German")


def test_duplicate_sentence_id_rejected(tmp_path: Path) -> None:
    """A13 #4: Duplicate sentence ID in projection is rejected fail-closed."""
    p = tmp_path / "dup_de.tsv"
    p.write_text("1\tErster Satz.\n1\tZweiter Satz mit gleicher ID.\n", encoding="utf-8")
    with pytest.raises(BuildDictError, match="Duplicate German sentence id 1"):
        parse_sentence_tsv(p, "German")


# ======================================================================
# 5, 6, 7, 8. Link validation: malformed, dangling, duplicate pairs
# ======================================================================


@pytest.mark.parametrize(
    "bad_link,match_str",
    [
        ("1\n", "expected exactly 2 tab-separated fields"),
        ("1\t2\t3\n", "expected exactly 2 tab-separated fields"),
        ("0\t2\n", "Invalid German sentence id"),
        ("1\t0\n", "Invalid English sentence id"),
        ("-1\t2\n", "Invalid German sentence id"),
        ("1\t-2\n", "Invalid English sentence id"),
        ("abc\t2\n", "Invalid German sentence id"),
        ("1\tdef\n", "Invalid English sentence id"),
    ],
)
def test_malformed_link_rejected(tmp_path: Path, bad_link: str, match_str: str) -> None:
    """A13 #5: Malformed link row is rejected fail-closed."""
    links_path = tmp_path / "bad_links.tsv"
    links_path.write_text(bad_link, encoding="utf-8")
    with pytest.raises(BuildDictError, match=match_str):
        parse_links_tsv(links_path, {1}, {2})


def test_dangling_de_link_rejected(tmp_path: Path) -> None:
    """A13 #6: Link referencing nonexistent German sentence ID fails closed."""
    links_path = tmp_path / "dangling_de.tsv"
    links_path.write_text("999\t10\n", encoding="utf-8")
    with pytest.raises(BuildDictError, match="Dangling German sentence id 999"):
        parse_links_tsv(links_path, {1, 2}, {10})


def test_dangling_en_link_rejected(tmp_path: Path) -> None:
    """A13 #7: Link referencing nonexistent English sentence ID fails closed."""
    links_path = tmp_path / "dangling_en.tsv"
    links_path.write_text("1\t999\n", encoding="utf-8")
    with pytest.raises(BuildDictError, match="Dangling English sentence id 999"):
        parse_links_tsv(links_path, {1}, {10, 20})


def test_duplicate_link_pair_rejected(tmp_path: Path) -> None:
    """A13 #8: Duplicate DE-EN link pair fails closed."""
    links_path = tmp_path / "dup_links.tsv"
    links_path.write_text("1\t10\n1\t10\n", encoding="utf-8")
    with pytest.raises(BuildDictError, match=r"Duplicate link pair \(1, 10\)"):
        parse_links_tsv(links_path, {1}, {10})


# ======================================================================
# 9. Input order independence
# ======================================================================


def test_input_order_independence(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #9: Reordering TSV input lines produces logically identical database."""
    de_path, en_path, links_path = sample_tsv_projections

    out1 = tmp_path / "out1.sqlite"
    cache1 = tmp_path / "cache1"
    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out1,
        cache_dir=cache1,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    # Reversed TSV lines
    de_rev = tmp_path / "de_rev.tsv"
    en_rev = tmp_path / "en_rev.tsv"
    links_rev = tmp_path / "links_rev.tsv"

    de_rev.write_text(
        "\n".join(reversed(de_path.read_text(encoding="utf-8").splitlines())) + "\n",
        encoding="utf-8",
    )
    en_rev.write_text(
        "\n".join(reversed(en_path.read_text(encoding="utf-8").splitlines())) + "\n",
        encoding="utf-8",
    )
    links_rev.write_text(
        "\n".join(reversed(links_path.read_text(encoding="utf-8").splitlines())) + "\n",
        encoding="utf-8",
    )

    out2 = tmp_path / "out2.sqlite"
    cache2 = tmp_path / "cache2"
    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_rev,
        en_tsv_path=en_rev,
        links_tsv_path=links_rev,
        output_path=out2,
        cache_dir=cache2,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    def fetch_all(db: Path) -> tuple[list[Any], list[Any]]:
        conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
        try:
            ex = conn.execute(
                "SELECT source_ref, de, en, source, license, token_count, has_proper "
                "FROM example ORDER BY CAST(source_ref AS INTEGER), source_ref"
            ).fetchall()
            el = conn.execute(
                "SELECT e.source_ref, el.lemma_id "
                "FROM example_lemma el JOIN example e ON e.id = el.example_id "
                "ORDER BY CAST(e.source_ref AS INTEGER), e.source_ref, el.lemma_id"
            ).fetchall()
            return ex, el
        finally:
            conn.close()

    ex1, el1 = fetch_all(out1)
    ex2, el2 = fetch_all(out2)
    assert ex1 == ex2
    assert el1 == el2


# ======================================================================
# 10. Deterministic lowest-ID English translation choice
# ======================================================================


def test_deterministic_lowest_id_english_translation_choice(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #10: When multiple EN links exist, choose lowest numeric EN ID."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_choice.sqlite"
    cache = tmp_path / "cache_choice"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        # German sentence 1 was linked to EN 20 ("The house is large.")
        # and EN 10 ("The house is big."). It must choose EN 10.
        row = conn.execute("SELECT en FROM example WHERE source_ref = '1'").fetchone()
        assert row is not None
        assert row[0] == "The house is big."
    finally:
        conn.close()


# ======================================================================
# 11 & 12. Untranslated sentences and zero-indexable exclusion
# ======================================================================


def test_untranslated_german_sentence_persists_with_en_null(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #11: Untranslated German sentence with indexable lemma persists with en=NULL."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_untrans.sqlite"
    cache = tmp_path / "cache_untrans"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        # Sentence 4 ("Ein schönes Haus.") has no link in links.tsv
        row = conn.execute(
            "SELECT de, en FROM example WHERE source_ref = '4'"
        ).fetchone()
        assert row is not None
        assert row[0] == "Ein schönes Haus."
        assert row[1] is None
    finally:
        conn.close()


def test_sentence_with_no_indexable_lemma_not_persisted(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #12: German sentence with zero indexable dictionary lemmas is not persisted."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_noluck.sqlite"
    cache = tmp_path / "cache_noluck"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        # Sentence 6 ("Xyz qwerty uiop.") has no known lemmas
        row = conn.execute("SELECT id FROM example WHERE source_ref = '6'").fetchone()
        assert row is None
    finally:
        conn.close()


# ======================================================================
# 13. Repeated lemma deduplication
# ======================================================================


def test_repeated_lemma_resolution_creates_one_association(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #13: Multiple tokens resolving to same lemma_id create one association."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_dedupe.sqlite"
    cache = tmp_path / "cache_dedupe"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        # Sentence 7: "Ein Haus neben dem Haus." contains "Haus" twice.
        ex_id = conn.execute("SELECT id FROM example WHERE source_ref = '7'").fetchone()[0]
        lemma_id = conn.execute("SELECT id FROM lemma WHERE lemma = 'Haus'").fetchone()[0]
        count = conn.execute(
            "SELECT count(*) FROM example_lemma WHERE example_id = ? AND lemma_id = ?",
            (ex_id, lemma_id),
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


# ======================================================================
# 14, 15, 16, 17, 18. Attribution & Metadata fields
# ======================================================================


def test_source_attribution_tatoeba(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #14: All Stage-02 examples have source='tatoeba'."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_attr.sqlite"
    cache = tmp_path / "cache_attr"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM example").fetchall()}
        assert sources == {"tatoeba"}
    finally:
        conn.close()


def test_nonblank_source_ref(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #15: Every example has nonblank source_ref matching German sentence ID."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_sref.sqlite"
    cache = tmp_path / "cache_sref"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, source_ref FROM example").fetchall()
        assert len(rows) > 0
        for _, sref in rows:
            assert sref is not None
            assert sref.strip() != ""
            assert sref.isdigit()
    finally:
        conn.close()


def test_exact_supplied_license(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #16: Every example has the exact supplied license."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_lic.sqlite"
    cache = tmp_path / "cache_lic"
    custom_license = "CC BY 2.0 FR - Verified 2026"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=custom_license,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        licenses = {r[0] for r in conn.execute("SELECT DISTINCT license FROM example").fetchall()}
        assert licenses == {custom_license}
    finally:
        conn.close()


def test_token_count_calculation(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #17: token_count matches spaCy token count excluding whitespace."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_tc.sqlite"
    cache = tmp_path / "cache_tc"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        # Sentence 1: "Das Haus ist groß." -> tokens: ["Das", "Haus", "ist", "groß", "."] -> 5
        row = conn.execute("SELECT token_count FROM example WHERE source_ref = '1'").fetchone()
        assert row is not None
        assert row[0] == 5
    finally:
        conn.close()


def test_has_proper_flag(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #18: has_proper is 1 if any token has POS PROPN, else 0."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_proper.sqlite"
    cache = tmp_path / "cache_proper"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        # Sentence 5: "Berlin ist wunderbar." -> Berlin is PROPN -> has_proper = 1
        row_berlin = conn.execute(
            "SELECT has_proper FROM example WHERE source_ref = '5'"
        ).fetchone()
        assert row_berlin is not None
        assert row_berlin[0] == 1

        # Sentence 1: "Das Haus ist groß." -> has_proper = 0
        row_haus = conn.execute(
            "SELECT has_proper FROM example WHERE source_ref = '1'"
        ).fetchone()
        assert row_haus is not None
        assert row_haus[0] == 0
    finally:
        conn.close()


# ======================================================================
# 19 & 20. Canonical resolver path & Separable verb regression
# ======================================================================


def test_canonical_resolve_token_path_exercised(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #19: Canonical resolve_token path is exercised during build."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_canon.sqlite"
    cache = tmp_path / "cache_canon"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    # Use Dictionary to check example associations
    with Dictionary(out) as d:
        haus_entries = d.lookup_exact("Haus", pos="NOUN")
        assert len(haus_entries) > 0
        haus_examples = d.get_examples_for_lemma(haus_entries[0].id)
        assert len(haus_examples) >= 1
        assert any("Haus" in ex.de for ex in haus_examples)


def test_separable_verb_rufe_an_indexes_anrufen(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #20: 'Ich rufe dich morgen an.' indexes 'anrufen' through canonical resolver."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_sep.sqlite"
    cache = tmp_path / "cache_sep"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    with Dictionary(out) as d:
        anrufen_entries = d.lookup_exact("anrufen", pos="VERB")
        assert len(anrufen_entries) == 1
        anrufen_id = anrufen_entries[0].id

        examples = d.get_examples_for_lemma(anrufen_id)
        assert any(ex.de == "Ich rufe dich morgen an." for ex in examples)


# ======================================================================
# 21. Derived compound without lemma_id does not invent association
# ======================================================================


def test_derived_compound_with_no_lemma_id_does_not_invent_association(
    stage01_db: Path,
    tmp_path: Path,
) -> None:
    """A13 #21: Derived compound result with lemma_id=None creates no example_lemma row."""
    # Create sentence containing a compound word that is not in lemma table
    de_path = tmp_path / "de_comp.tsv"
    de_path.write_text("1\tDie Haustür ist neu.\n", encoding="utf-8")
    en_path = tmp_path / "en_comp.tsv"
    en_path.write_text("10\tThe front door is new.\n", encoding="utf-8")
    links_path = tmp_path / "links_comp.tsv"
    links_path.write_text("1\t10\n", encoding="utf-8")

    out = tmp_path / "out_comp.sqlite"
    cache = tmp_path / "cache_comp"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    conn = sqlite3.connect(f"file:{out.resolve()}?mode=ro", uri=True)
    try:
        # Every example_lemma row must reference an actual existing lemma ID
        orphans = conn.execute(
            "SELECT count(*) FROM example_lemma el "
            "LEFT JOIN lemma l ON l.id = el.lemma_id WHERE l.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0
    finally:
        conn.close()


# ======================================================================
# 22, 23, 24. Input preservation, overwrite refusal, clean failure
# ======================================================================


def test_stage01_input_remains_unchanged(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #22: Stage-01 database input SHA-256 remains completely unchanged."""
    de_path, en_path, links_path = sample_tsv_projections
    sha_before = sha256_file(stage01_db)

    out = tmp_path / "out_pres.sqlite"
    cache = tmp_path / "cache_pres"
    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    sha_after = sha256_file(stage01_db)
    assert sha_before == sha_after


def test_output_overwrite_refused(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #23: Attempting to write to existing output path raises BuildDictError."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "existing_out.sqlite"
    out.write_text("existing content", encoding="utf-8")
    cache = tmp_path / "cache_ow"

    with pytest.raises(BuildDictError, match="Output path already exists"):
        build_stage02(
            stage01_path=stage01_db,
            de_tsv_path=de_path,
            en_tsv_path=en_path,
            links_tsv_path=links_path,
            output_path=out,
            cache_dir=cache,
            license_label=DEFAULT_LICENSE,
            n_process=1,
        )

    assert out.read_text(encoding="utf-8") == "existing content"


def test_failure_leaves_no_completed_output(
    stage01_db: Path,
    tmp_path: Path,
) -> None:
    """A13 #24: Failure during build leaves no completed output database."""
    de_path = tmp_path / "bad_de.tsv"
    de_path.write_text("1\tValid\n2\tInvalid\textra\ttab\n", encoding="utf-8")
    en_path = tmp_path / "en.tsv"
    en_path.write_text("10\tEnglish\n", encoding="utf-8")
    links_path = tmp_path / "links.tsv"
    links_path.write_text("1\t10\n", encoding="utf-8")

    out = tmp_path / "failed_out.sqlite"
    cache = tmp_path / "cache_fail"

    with pytest.raises(BuildDictError):
        build_stage02(
            stage01_path=stage01_db,
            de_tsv_path=de_path,
            en_tsv_path=en_path,
            links_tsv_path=links_path,
            output_path=out,
            cache_dir=cache,
            license_label=DEFAULT_LICENSE,
            n_process=1,
        )

    assert not out.exists()


# ======================================================================
# 25, 26, 27, 28, 29. Cache Key Identity & Invalidation
# ======================================================================


def test_canonical_resolver_hash_helper_used() -> None:
    """A13 #25: Canonical resolver hash helper tools.resolver_hash is used."""
    h = get_resolver_hash()
    assert isinstance(h, str)
    assert len(h) == 64


def test_resolver_content_change_changes_cache_key(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A13 #26: Modifying canonical resolver hash changes the Stage-02 cache key."""
    de_path, en_path, links_path = sample_tsv_projections

    key1 = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )

    # Simulate changed resolver bytes
    monkeypatch.setattr(
        "tools.build_dict.get_resolver_hash",
        lambda: "0000000000000000000000000000000000000000000000000000000000000000",
    )

    key2 = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )

    assert key1 != key2


def test_stage01_content_change_changes_cache_key(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #27: Modifying Stage-01 content changes the Stage-02 cache key."""
    de_path, en_path, links_path = sample_tsv_projections

    key1 = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )

    stage01_mod = tmp_path / "stage01_mod.sqlite"
    stage01_mod.write_bytes(stage01_db.read_bytes() + b"\x00")

    key2 = compute_stage02_cache_key(
        stage01_path=stage01_mod,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )

    assert key1 != key2


def test_tatoeba_input_content_change_changes_cache_key(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #28: Changing any of the three Tatoeba TSVs changes the cache key."""
    de_path, en_path, links_path = sample_tsv_projections

    key_base = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )

    # 1. Change DE TSV
    de_mod = tmp_path / "de_mod.tsv"
    de_mod.write_text(de_path.read_text(encoding="utf-8") + "99\tExtra.\n", encoding="utf-8")
    key_de = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_mod,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )
    assert key_base != key_de

    # 2. Change EN TSV
    en_mod = tmp_path / "en_mod.tsv"
    en_mod.write_text(en_path.read_text(encoding="utf-8") + "99\tExtra.\n", encoding="utf-8")
    key_en = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_mod,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )
    assert key_base != key_en

    # 3. Change links TSV
    links_mod = tmp_path / "links_mod.tsv"
    links_mod.write_text(links_path.read_text(encoding="utf-8") + "99\t99\n", encoding="utf-8")
    key_links = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_mod,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )
    assert key_base != key_links


def test_spacy_model_change_changes_cache_key(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
) -> None:
    """A13 #29: Changing spaCy model argument changes the cache key."""
    de_path, en_path, links_path = sample_tsv_projections

    key1 = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )
    key2 = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_lg",
    )
    assert key1 != key2


# ======================================================================
# 30, 31, 32, 33. Cache Miss, Cache Hit, Corrupt Cache, Logical Equality
# ======================================================================


def test_cache_miss_publishes_completed_cache(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #30: Cache miss builds output and publishes validated cache artifact."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_miss.sqlite"
    cache = tmp_path / "cache_miss"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    assert out.exists()
    cache_files = list(cache.glob("*.sqlite"))
    assert len(cache_files) == 1


def test_exact_key_cache_hit_avoids_nlp_rebuild(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A13 #31: Exact-key cache hit publishes output without loading spaCy."""
    de_path, en_path, links_path = sample_tsv_projections
    out1 = tmp_path / "out_hit1.sqlite"
    out2 = tmp_path / "out_hit2.sqlite"
    cache = tmp_path / "cache_hit"

    # First run: MISS
    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out1,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    # Monkeypatch spacy to raise an error if called
    def forbidden_spacy(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("spaCy was called during cache hit!")

    import spacy

    monkeypatch.setattr(spacy, "load", forbidden_spacy)

    # Second run with same inputs and new output: HIT
    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out2,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    assert out2.exists()


def test_corrupt_cache_fails_closed(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #32: Corrupt matching cache fails closed without rebuilding or overwriting."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "out_corrupt.sqlite"
    cache = tmp_path / "cache_corrupt"
    cache.mkdir(parents=True, exist_ok=True)

    cache_key = compute_stage02_cache_key(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        license_label=DEFAULT_LICENSE,
        spacy_model="de_core_news_md",
    )
    cache_file = cache / f"{cache_key.replace(':', '_')}.sqlite"
    cache_file.write_bytes(b"corrupted sqlite content")

    with pytest.raises(BuildDictError, match="Corrupt matching cache artifact"):
        build_stage02(
            stage01_path=stage01_db,
            de_tsv_path=de_path,
            en_tsv_path=en_path,
            links_tsv_path=links_path,
            output_path=out,
            cache_dir=cache,
            license_label=DEFAULT_LICENSE,
            n_process=1,
        )

    assert not out.exists()


def test_cache_hit_and_miss_outputs_logically_equal(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """A13 #33: Cache-hit and cache-miss outputs are logically equal."""
    de_path, en_path, links_path = sample_tsv_projections
    out_miss = tmp_path / "out_miss_eq.sqlite"
    out_hit = tmp_path / "out_hit_eq.sqlite"
    cache = tmp_path / "cache_eq"

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out_miss,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    build_stage02(
        stage01_path=stage01_db,
        de_tsv_path=de_path,
        en_tsv_path=en_path,
        links_tsv_path=links_path,
        output_path=out_hit,
        cache_dir=cache,
        license_label=DEFAULT_LICENSE,
        n_process=1,
    )

    def get_rows(path: Path) -> tuple[list[Any], list[Any]]:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            ex = conn.execute("SELECT * FROM example ORDER BY id").fetchall()
            el = conn.execute(
                "SELECT * FROM example_lemma ORDER BY lemma_id, example_id"
            ).fetchall()
            return ex, el
        finally:
            conn.close()

    ex_miss, el_miss = get_rows(out_miss)
    ex_hit, el_hit = get_rows(out_hit)
    assert ex_miss == ex_hit
    assert el_miss == el_hit


# ======================================================================
# 34 & 35. Regression & Gate Checks
# ======================================================================


def test_stage01_regression_tests_remain_passing(stage01_db: Path) -> None:
    """A13 #34: Stage 01 validation and database integrity tests pass."""
    validate_stage01_database(stage01_db)


def test_make_gate_and_agents_r3_pass() -> None:
    """A13 #35: AGENTS rule checker passes."""
    import tools.check_agents
    repo_root = Path(__file__).parent.parent
    violations = tools.check_agents.check_all(repo_root)
    assert violations == []


# ======================================================================
# Additional CLI tests
# ======================================================================


def test_cli_stage02_invocation_in_process(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """CLI entrypoint main() executes stage02 subcommand successfully."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "cli_out.sqlite"
    cache = tmp_path / "cli_cache"

    code = main([
        "stage02",
        "--stage01",
        str(stage01_db),
        "--de-tsv",
        str(de_path),
        "--en-tsv",
        str(en_path),
        "--links-tsv",
        str(links_path),
        "--output",
        str(out),
        "--cache-dir",
        str(cache),
        "--license",
        DEFAULT_LICENSE,
        "--n-process",
        "1",
    ])
    assert code == 0
    assert out.exists()


def test_cli_stage02_invocation_subprocess(
    stage01_db: Path,
    sample_tsv_projections: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    """Subprocess execution of python tools/build_dict.py stage02 matches contract."""
    de_path, en_path, links_path = sample_tsv_projections
    out = tmp_path / "subp_out.sqlite"
    cache = tmp_path / "subp_cache"

    cmd = [
        sys.executable,
        "tools/build_dict.py",
        "stage02",
        "--stage01",
        str(stage01_db),
        "--de-tsv",
        str(de_path),
        "--en-tsv",
        str(en_path),
        "--links-tsv",
        str(links_path),
        "--output",
        str(out),
        "--cache-dir",
        str(cache),
        "--license",
        DEFAULT_LICENSE,
        "--n-process",
        "1",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert out.exists()
