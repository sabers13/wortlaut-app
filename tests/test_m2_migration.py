"""M2 duplicate-safe note identity + PART-B v0 -> v1 migration tests.

Covers the M2 maintenance unit: canonical key constructors, shared
find_or_create_note reuse/promotion/refusal, safe versioned migration
(no legacy auto-merge), API unification, concurrency, Offline/Online
parity, and the read-only legacy diagnostic.

All databases are temporary fixture copies. The owner's real production
database is never inspected or mutated here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Generator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deck
from app.api import create_app
from app.dictionary_session import OnlineSessionInfo
from app.provider import (
    CandidateLookup,
    CompoundComponent,
    DictionaryEntry,
    DictionaryProvider,
    ExampleRecord,
    LemmaEntry,
    LemmaHit,
    MeaningRow,
    SenseEntry,
    SenseHit,
)
from app.standalone import StandaloneError, ensure_user_db
from tools.build_dict import compute_lemma_semantic_ref
from tools.check_agents import check_m2_identity

REPO_ROOT = Path(__file__).resolve().parent.parent
V0_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "part_b_v0.sql"

AUTH_HEADERS = {
    "Host": "127.0.0.1:8000",
    "Origin": "http://127.0.0.1:8000",
    "X-Flashcards-Request": "1",
    "Content-Type": "application/json",
}

NOW = "2026-01-01T00:00:00+00:00"

HAUS_LEMMA = compute_lemma_semantic_ref("Haus", "NOUN", "das")
TUER_LEMMA = compute_lemma_semantic_ref("Tür", "NOUN", "die")
TAG_LEMMA = compute_lemma_semantic_ref("Tag", "NOUN", "der")
KARTE_LEMMA = compute_lemma_semantic_ref("Karte", "NOUN", "die")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _make_v0_db(tmp_path: Path, name: str = "user_v0.sqlite") -> Path:
    """Build a version-0 PART-B database from the frozen pre-M2 fixture."""
    target = tmp_path / name
    conn = sqlite3.connect(target)
    try:
        conn.executescript(V0_FIXTURE.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()
    return target


def _v0_add_note(
    conn: sqlite3.Connection,
    note_id: int,
    lemma_ref: str,
    *,
    sense_ref: str | None = None,
    status: str = "needs_gloss",
    review_count: int = 0,
    last_confidence: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO note (
            id, lemma_semantic_ref, sense_semantic_ref, status,
            created_at, due_at, review_count, last_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (note_id, lemma_ref, sense_ref, status, NOW, NOW, review_count, last_confidence),
    )
    conn.execute(
        "INSERT INTO card (id, note_id, state, step, due_at) VALUES (?, ?, 0, NULL, ?)",
        (note_id, note_id, NOW),
    )
    conn.execute(
        "INSERT INTO note_meaning_lang (note_id, lang) VALUES (?, 'de'), (?, 'en')",
        (note_id, note_id),
    )


def _v0_add_direct_binding(
    conn: sqlite3.Connection,
    note_id: int,
    lemma_ref: str,
    sense_ref: str,
    *,
    binding_status: str = "bound",
) -> None:
    conn.execute(
        """
        INSERT INTO note_dictionary_binding (
            note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
            binding_status, last_relinked_at
        ) VALUES (?, 'direct', 0, ?, ?, ?, ?)
        """,
        (note_id, lemma_ref, sense_ref, binding_status, NOW),
    )


def _v0_add_component_bindings(
    conn: sqlite3.Connection,
    note_id: int,
    components: Sequence[tuple[str, str]],
    *,
    binding_status: str = "bound",
) -> None:
    conn.executemany(
        """
        INSERT INTO note_dictionary_binding (
            note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
            binding_status, component_count, last_relinked_at
        ) VALUES (?, 'component', ?, ?, ?, ?, ?, ?)
        """,
        (
            (note_id, ordinal, lemma_ref, sense_ref, binding_status, len(components), NOW)
            for ordinal, (lemma_ref, sense_ref) in enumerate(components)
        ),
    )


def _v0_add_membership(
    conn: sqlite3.Connection, note_id: int, deck_name: str = "Deck"
) -> int:
    row = conn.execute("SELECT id FROM deck WHERE name = ?", (deck_name,)).fetchone()
    if row is None:
        cursor = conn.execute(
            "INSERT INTO deck (name, created_at) VALUES (?, ?)", (deck_name, NOW)
        )
        deck_id = int(cursor.lastrowid or 0)
    else:
        deck_id = int(row[0])
    conn.execute(
        "INSERT OR IGNORE INTO note_deck (note_id, deck_id, created_at) VALUES (?, ?, ?)",
        (note_id, deck_id, NOW),
    )
    return deck_id


def _v0_add_user_meaning(
    conn: sqlite3.Connection, note_id: int, lang: str, text: str
) -> None:
    conn.execute(
        """
        INSERT INTO note_user_meaning (note_id, lang, meaning_text, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (note_id, lang, text, NOW, NOW),
    )


def _v0_add_audio(conn: sqlite3.Connection, note_id: int, filename: str) -> None:
    conn.execute(
        """
        INSERT INTO custom_pronunciation (
            note_id, media_filename, sha256, byte_size, format, source_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (note_id, filename, "a" * 64, 100, "wav", "uploaded", NOW),
    )


def _v0_add_review(conn: sqlite3.Connection, card_id: int, confidence: int = 4) -> None:
    conn.execute(
        """
        INSERT INTO review_log (
            card_id, confidence, rating, scheduled_days, elapsed_days, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (card_id, confidence, 3, 1.0, 1.0, NOW),
    )


def _backups_beside(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".pre-m2-v0-*.bak"))


def _recompute_sense_refs(dict_path: Path) -> None:
    """Normalize conftest fixture sense refs to canonical computed refs."""
    from tools.build_dict import compute_sense_semantic_ref

    conn_dict = sqlite3.connect(dict_path)
    try:
        senses = conn_dict.execute(
            "SELECT s.id, l.semantic_ref, s.source_namespace, s.source_ref "
            "FROM sense s JOIN lemma l ON s.lemma_id = l.id"
        ).fetchall()
        for sid, lref, ns, sref in senses:
            sem_ref = compute_sense_semantic_ref(str(lref), str(ns), str(sref))
            conn_dict.execute(
                "UPDATE sense SET semantic_ref = ? WHERE id = ?", (sem_ref, sid)
            )
        conn_dict.commit()
    finally:
        conn_dict.close()


def _note_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM note").fetchone()
    assert row is not None
    return int(row[0])


def _dict_refs(dict_path: Path, headword: str) -> tuple[str, str]:
    """Return (lemma_ref, first sense_ref) for a headword in a test dictionary."""
    conn = sqlite3.connect(dict_path)
    try:
        lemma_ref = str(
            conn.execute(
                "SELECT semantic_ref FROM lemma WHERE lemma = ?", (headword,)
            ).fetchone()[0]
        )
        sense_ref = str(
            conn.execute(
                """
                SELECT s.semantic_ref FROM sense s
                JOIN lemma l ON s.lemma_id = l.id
                WHERE l.lemma = ? ORDER BY s.ord ASC LIMIT 1
                """,
                (headword,),
            ).fetchone()[0]
        )
    finally:
        conn.close()
    return lemma_ref, sense_ref


@pytest.fixture
def offline_app(
    tmp_path: Path, create_test_db: Callable[..., Path]
) -> Generator[tuple[TestClient, Path, Path], None, None]:
    """Offline product app on a fresh v1 user DB (via ensure_user_db)."""
    dict_path = create_test_db(populate=True)
    _recompute_sense_refs(dict_path)
    user_db_path = tmp_path / "offline_user.sqlite"
    ensure_user_db(user_db_path)
    app = create_app(
        dict_path=dict_path,
        user_db_path=user_db_path,
        cors_origins=["http://127.0.0.1:8000"],
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client, dict_path, user_db_path


# ---------------------------------------------------------------------------
# 1. Fixture integrity
# ---------------------------------------------------------------------------


def test_v0_fixture_is_the_frozen_pre_m2_schema() -> None:
    text = V0_FIXTURE.read_text(encoding="utf-8")
    assert "ux_note_dictionary_key" not in text
    assert "ADD COLUMN" not in text
    assert "user_version = 0" in text
    assert "user_version = 1" not in text
    assert "CREATE TABLE IF NOT EXISTS note_dictionary_binding" in text
    assert "CREATE TABLE IF NOT EXISTS review_log" in text


def test_current_schema_carries_m2_identity() -> None:
    text = (REPO_ROOT / "reference" / "schema.sql").read_text(encoding="utf-8")
    assert "dictionary_key" in text
    assert "ux_note_dictionary_key" in text
    assert "PRAGMA user_version = 2;" in text


def test_m2_executable_invariant_passes() -> None:
    assert check_m2_identity(REPO_ROOT) == []


# ---------------------------------------------------------------------------
# 2. Key constructors
# ---------------------------------------------------------------------------


def test_resolved_key_uses_sense_ref_verbatim() -> None:
    sense = "sense:v1:haus_das_0"
    assert deck.resolved_dictionary_key(sense) == f"resolved:v1:{sense}"


def test_needs_gloss_key_uses_lemma_ref_only() -> None:
    assert deck.needs_gloss_dictionary_key(HAUS_LEMMA) == f"needs_gloss:v1:{HAUS_LEMMA}"


def test_derived_key_digest_is_ordered_and_deterministic() -> None:
    components = [(HAUS_LEMMA, "sense:v1:haus_0"), (TUER_LEMMA, "sense:v1:tuer_0")]
    payload = json.dumps(
        [[lemma, sense] for lemma, sense in components],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    expected = f"derived:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
    assert deck.derived_dictionary_key(components) == expected
    assert deck.derived_dictionary_key(list(reversed(components))) != expected


def test_same_refs_produce_same_keys() -> None:
    assert deck.resolved_dictionary_key("sense:v1:x") == deck.resolved_dictionary_key(
        "sense:v1:x"
    )
    assert deck.needs_gloss_dictionary_key("lemma:v1:x") == deck.needs_gloss_dictionary_key(
        "lemma:v1:x"
    )
    vector = [(HAUS_LEMMA, "sense:v1:a"), (TUER_LEMMA, "sense:v1:b")]
    assert deck.derived_dictionary_key(vector) == deck.derived_dictionary_key(vector)


def test_key_constructors_reject_blank_refs() -> None:
    with pytest.raises(deck.DeckError):
        deck.resolved_dictionary_key("  ")
    with pytest.raises(deck.DeckError):
        deck.needs_gloss_dictionary_key("")
    with pytest.raises(deck.DeckError):
        deck.derived_dictionary_key([])
    with pytest.raises(deck.DeckError):
        deck.derived_dictionary_key([(HAUS_LEMMA, "  ")])


# ---------------------------------------------------------------------------
# 3. Fresh DB behavior
# ---------------------------------------------------------------------------


def test_fresh_db_is_version_2_with_index_and_no_backup(tmp_path: Path) -> None:
    target = tmp_path / "fresh.sqlite"
    ensure_user_db(target)
    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "dictionary_key" in [
            row[1] for row in conn.execute("PRAGMA table_info(note)").fetchall()
        ]
        index = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_note_dictionary_key'"
        ).fetchone()
        assert index is not None and "WHERE dictionary_key IS NOT NULL" in str(index[0])
    finally:
        conn.close()
    assert _backups_beside(target) == []


def test_second_startup_performs_no_migration_write(tmp_path: Path) -> None:
    target = tmp_path / "steady.sqlite"
    ensure_user_db(target)
    conn = _open(target)
    conn.execute(
        "INSERT INTO deck (name, created_at) VALUES (?, ?)", ("Kept", NOW)
    )
    conn.commit()
    conn.close()
    before_stat = target.stat()
    ensure_user_db(target)
    assert _backups_beside(target) == []
    assert target.stat().st_mtime_ns == before_stat.st_mtime_ns
    conn = _open(target)
    try:
        assert conn.execute("SELECT name FROM deck WHERE name = 'Kept'").fetchone()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_newer_database_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "future.sqlite"
    ensure_user_db(target)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(StandaloneError, match="newer Wortlaut"):
        ensure_user_db(target)


# ---------------------------------------------------------------------------
# 4. Clean v0 -> v1 migration
# ---------------------------------------------------------------------------


def test_clean_v0_migration_backs_up_and_keys_singletons(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    sense_haus = "sense:v1:haus_das_0"
    sense_tuer = "sense:v1:tuer_die_0"
    _v0_add_note(conn, 1, HAUS_LEMMA, sense_ref=sense_haus, status="resolved")
    _v0_add_direct_binding(conn, 1, HAUS_LEMMA, sense_haus)
    _v0_add_note(conn, 2, TAG_LEMMA, status="needs_gloss")
    _v0_add_note(
        conn, 3, HAUS_LEMMA, status="derived_compound", sense_ref=None
    )
    _v0_add_component_bindings(
        conn, 3, [(HAUS_LEMMA, sense_haus), (TUER_LEMMA, sense_tuer)]
    )
    _v0_add_membership(conn, 1, "Lesson")
    conn.commit()
    conn.close()

    ensure_user_db(target)

    backups = _backups_beside(target)
    assert len(backups) == 1
    backup_conn = sqlite3.connect(backups[0])
    try:
        assert backup_conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "dictionary_key" not in [
            row[1] for row in backup_conn.execute("PRAGMA table_info(note)").fetchall()
        ]
    finally:
        backup_conn.close()

    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        keys = {
            int(row[0]): row[1]
            for row in conn.execute("SELECT id, dictionary_key FROM note").fetchall()
        }
        assert keys[1] == f"resolved:v1:{sense_haus}"
        assert keys[2] == f"needs_gloss:v1:{TAG_LEMMA}"
        assert keys[3] == deck.derived_dictionary_key(
            [(HAUS_LEMMA, sense_haus), (TUER_LEMMA, sense_tuer)]
        )
        assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 1
    finally:
        conn.close()


def test_migration_demoted_note_keeps_binding_identity(tmp_path: Path) -> None:
    """A needs_gloss note with a durable direct binding keeps the resolved key."""
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    sense_haus = "sense:v1:haus_das_0"
    _v0_add_note(conn, 1, HAUS_LEMMA, sense_ref=sense_haus, status="needs_gloss")
    _v0_add_direct_binding(
        conn, 1, HAUS_LEMMA, sense_haus, binding_status="unbound"
    )
    conn.commit()
    conn.close()

    ensure_user_db(target)

    conn = _open(target)
    try:
        key = conn.execute("SELECT dictionary_key FROM note WHERE id = 1").fetchone()[0]
        assert key == f"resolved:v1:{sense_haus}"
        status = conn.execute("SELECT status FROM note WHERE id = 1").fetchone()[0]
        assert status == "needs_gloss"
    finally:
        conn.close()


def test_migration_orphaned_note_keeps_binding_identity(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    sense_haus = "sense:v1:haus_das_0"
    _v0_add_note(conn, 1, HAUS_LEMMA, sense_ref=sense_haus, status="orphaned")
    _v0_add_direct_binding(conn, 1, HAUS_LEMMA, sense_haus)
    _v0_add_membership(conn, 1, "Orphaned")
    conn.commit()
    conn.close()

    ensure_user_db(target)

    conn = _open(target)
    try:
        row = conn.execute(
            "SELECT status, dictionary_key FROM note WHERE id = 1"
        ).fetchone()
        assert row[0] == "orphaned"
        assert row[1] == f"resolved:v1:{sense_haus}"
        assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Duplicate groups are preserved, never merged
# ---------------------------------------------------------------------------


def _seed_duplicate_pair(
    conn: sqlite3.Connection,
    first_id: int,
    second_id: int,
    lemma_ref: str,
    sense_ref: str,
) -> None:
    _v0_add_note(
        conn,
        first_id,
        lemma_ref,
        sense_ref=sense_ref,
        status="resolved",
        review_count=1,
        last_confidence=4,
    )
    _v0_add_direct_binding(conn, first_id, lemma_ref, sense_ref)
    _v0_add_note(conn, second_id, lemma_ref, sense_ref=sense_ref, status="resolved")
    _v0_add_direct_binding(conn, second_id, lemma_ref, sense_ref)
    _v0_add_membership(conn, first_id, "Lesson")
    _v0_add_membership(conn, second_id, "Lesson")
    _v0_add_user_meaning(conn, first_id, "en", "first meaning")
    _v0_add_user_meaning(conn, second_id, "en", "second meaning")
    _v0_add_audio(conn, first_id, "first.wav")
    _v0_add_audio(conn, second_id, "second.wav")
    _v0_add_review(conn, first_id, confidence=4)


def test_duplicate_resolved_group_survives_migration(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    sense = "sense:v1:see_der_0"
    lemma = compute_lemma_semantic_ref("See", "NOUN", "der")
    _seed_duplicate_pair(conn, 1, 2, lemma, sense)
    conn.commit()
    conn.close()

    ensure_user_db(target)

    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        rows = conn.execute(
            "SELECT id, dictionary_key, status, review_count FROM note ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] is None and rows[1][1] is None
        assert (rows[0][2], rows[1][2]) == ("resolved", "resolved")
        assert (rows[0][3], rows[1][3]) == (1, 0)
        assert conn.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 1
        log = conn.execute(
            "SELECT card_id, confidence, rating FROM review_log"
        ).fetchone()
        assert tuple(log) == (1, 4, 3)
        meanings = {
            int(row[0]): str(row[1])
            for row in conn.execute(
                "SELECT note_id, meaning_text FROM note_user_meaning"
            ).fetchall()
        }
        assert meanings == {1: "first meaning", 2: "second meaning"}
        audio = sorted(
            str(row[0])
            for row in conn.execute(
                "SELECT media_filename FROM custom_pronunciation ORDER BY note_id"
            ).fetchall()
        )
        assert audio == ["first.wav", "second.wav"]
        bindings = conn.execute(
            "SELECT COUNT(*) FROM note_dictionary_binding"
        ).fetchone()[0]
        assert bindings == 2
    finally:
        conn.close()


def test_duplicate_needs_gloss_group_stays_null(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _v0_add_note(conn, 1, TAG_LEMMA, status="needs_gloss")
    _v0_add_note(conn, 2, TAG_LEMMA, status="needs_gloss")
    _v0_add_membership(conn, 1, "Lesson")
    _v0_add_membership(conn, 2, "Lesson")
    conn.commit()
    conn.close()

    ensure_user_db(target)

    conn = _open(target)
    try:
        rows = conn.execute(
            "SELECT id, dictionary_key FROM note ORDER BY id"
        ).fetchall()
        assert [row[1] for row in rows] == [None, None]
        assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 2
    finally:
        conn.close()


def test_duplicate_derived_group_stays_null(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    sense_haus = "sense:v1:haus_das_0"
    sense_tuer = "sense:v1:tuer_die_0"
    vector = [(HAUS_LEMMA, sense_haus), (TUER_LEMMA, sense_tuer)]
    _v0_add_note(conn, 1, HAUS_LEMMA, status="derived_compound")
    _v0_add_component_bindings(conn, 1, vector)
    _v0_add_note(conn, 2, HAUS_LEMMA, status="derived_compound")
    _v0_add_component_bindings(conn, 2, vector)
    conn.commit()
    conn.close()

    ensure_user_db(target)

    conn = _open(target)
    try:
        rows = conn.execute(
            "SELECT id, dictionary_key FROM note ORDER BY id"
        ).fetchall()
        assert [row[1] for row in rows] == [None, None]
        assert conn.execute(
            "SELECT COUNT(*) FROM note_dictionary_binding"
        ).fetchone()[0] == 4
    finally:
        conn.close()


def test_cross_status_rows_are_not_collapsed(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    sense_tag = "sense:v1:tag_der_0"
    _v0_add_note(conn, 1, TAG_LEMMA, status="needs_gloss")
    _v0_add_note(conn, 2, TAG_LEMMA, sense_ref=sense_tag, status="resolved")
    _v0_add_direct_binding(conn, 2, TAG_LEMMA, sense_tag)
    conn.commit()
    conn.close()

    ensure_user_db(target)

    conn = _open(target)
    try:
        rows = {
            int(row[0]): (str(row[1]), row[2])
            for row in conn.execute(
                "SELECT id, status, dictionary_key FROM note"
            ).fetchall()
        }
        assert rows[1] == ("needs_gloss", f"needs_gloss:v1:{TAG_LEMMA}")
        assert rows[2] == ("resolved", f"resolved:v1:{sense_tag}")
    finally:
        conn.close()


def test_migration_idempotency_creates_no_second_backup(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _v0_add_note(conn, 1, TAG_LEMMA, status="needs_gloss")
    conn.commit()
    conn.close()

    ensure_user_db(target)
    assert len(_backups_beside(target)) == 1
    conn = _open(target)
    try:
        keys_before = conn.execute(
            "SELECT id, dictionary_key FROM note ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    ensure_user_db(target)

    assert len(_backups_beside(target)) == 1
    conn = _open(target)
    try:
        keys_after = conn.execute(
            "SELECT id, dictionary_key FROM note ORDER BY id"
        ).fetchall()
        assert keys_after == keys_before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_migration_failure_rolls_back_and_reports_backup(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _v0_add_note(conn, 1, TAG_LEMMA, status="needs_gloss")
    conn.execute("CREATE TABLE ux_note_dictionary_key (poison TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(StandaloneError, match="backup"):
        ensure_user_db(target)

    backups = _backups_beside(target)
    assert len(backups) == 1
    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "dictionary_key" not in [
            row[1] for row in conn.execute("PRAGMA table_info(note)").fetchall()
        ]
        index = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'ux_note_dictionary_key'"
        ).fetchone()
        assert index is None
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Domain reuse
# ---------------------------------------------------------------------------


def _v1_conn(tmp_path: Path, name: str = "domain.sqlite") -> sqlite3.Connection:
    target = tmp_path / name
    ensure_user_db(target)
    return _open(target)


def test_reuse_same_resolved_identity(user_db: sqlite3.Connection) -> None:
    first = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        sense_semantic_ref="sense:v1:haus_0",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert first.outcome == "created"
    second = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        sense_semantic_ref="sense:v1:haus_0",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert second.outcome == "reused"
    assert (second.note_id, second.card_id) == (first.note_id, first.card_id)
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    assert user_db.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 1


def test_reuse_needs_gloss_and_derived_and_second_deck(
    user_db: sqlite3.Connection,
) -> None:
    gloss = deck.find_or_create_note(
        user_db, TAG_LEMMA, status="needs_gloss", meaning_languages=("en",)
    )
    gloss_again = deck.find_or_create_note(
        user_db, TAG_LEMMA, status="needs_gloss", meaning_languages=("en",)
    )
    assert gloss_again.outcome == "reused"
    assert gloss_again.note_id == gloss.note_id

    vector = [(HAUS_LEMMA, "sense:v1:a"), (TUER_LEMMA, "sense:v1:b")]
    derived = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        status="derived_compound",
        component_bindings=vector,
        meaning_languages=("de", "en"),
    )
    derived_again = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        status="derived_compound",
        component_bindings=vector,
        meaning_languages=("de", "en"),
    )
    assert derived_again.outcome == "reused"
    assert derived_again.note_id == derived.note_id

    deck_a = deck.create_deck(user_db, "A")
    deck_b = deck.create_deck(user_db, "B")
    deck.add_note_to_deck(user_db, gloss.note_id, deck_a)
    deck.add_note_to_deck(user_db, gloss.note_id, deck_b)
    memberships = user_db.execute(
        "SELECT COUNT(*) FROM note_deck WHERE note_id = ?", (gloss.note_id,)
    ).fetchone()[0]
    assert memberships == 2


def test_different_senses_same_lemma_stay_distinct(
    user_db: sqlite3.Connection,
) -> None:
    first = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        sense_semantic_ref="sense:v1:haus_0",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    second = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        sense_semantic_ref="sense:v1:haus_1",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert second.outcome == "created"
    assert second.note_id != first.note_id


def test_orphaned_capture_is_rejected(user_db: sqlite3.Connection) -> None:
    with pytest.raises(deck.DeckError, match="orphaned"):
        deck.find_or_create_note(
            user_db,
            HAUS_LEMMA,
            sense_semantic_ref="sense:v1:haus_0",
            status="orphaned",
            meaning_languages=("de", "en"),
        )
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 7. Promotion
# ---------------------------------------------------------------------------


def _promotable_setup(
    user_db: sqlite3.Connection, lemma_ref: str = HAUS_LEMMA
) -> tuple[int, int, str, str]:
    stub = deck.find_or_create_note(
        user_db, lemma_ref, status="needs_gloss", meaning_languages=("de", "en")
    )
    assert stub.outcome == "created"
    note_before = user_db.execute(
        "SELECT due_at, interval_days, ease_factor, created_at FROM note WHERE id = ?",
        (stub.note_id,),
    ).fetchone()
    assert note_before is not None
    deck.set_user_meaning(user_db, stub.note_id, "en", "stub meaning")
    user_db.execute(
        """
        INSERT INTO custom_pronunciation (
            note_id, media_filename, sha256, byte_size, format, source_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (stub.note_id, "stub.wav", "c" * 64, 10, "wav", "recorded", NOW),
    )
    user_db.commit()
    return stub.note_id, stub.card_id, str(note_before[0]), str(note_before[3])


def test_promotion_needs_gloss_to_resolved(user_db: sqlite3.Connection) -> None:
    note_id, card_id, due_before, created_before = _promotable_setup(user_db)
    deck_id = deck.create_deck(user_db, "Lesson")
    deck.add_note_to_deck(user_db, note_id, deck_id)
    user_db.commit()

    sense = "sense:v1:haus_promoted"
    result = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        sense_semantic_ref=sense,
        status="resolved",
        meaning_languages=("de", "en"),
        user_meanings={"en": "stub meaning"},
    )
    assert result.outcome == "promoted"
    assert result.note_id == note_id
    assert result.card_id == card_id

    row = user_db.execute(
        "SELECT status, sense_semantic_ref, dictionary_key, due_at, created_at,"
        " interval_days, ease_factor, review_count FROM note WHERE id = ?",
        (note_id,),
    ).fetchone()
    assert row is not None
    assert (
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
        float(row[5]),
        float(row[6]),
        int(row[7]),
    ) == (
        "resolved",
        sense,
        f"resolved:v1:{sense}",
        due_before,
        created_before,
        0.0,
        2.5,
        0,
    )
    binding = user_db.execute(
        """
        SELECT role, lemma_semantic_ref, sense_semantic_ref, binding_status
        FROM note_dictionary_binding WHERE note_id = ?
        """,
        (note_id,),
    ).fetchall()
    assert len(binding) == 1
    assert (str(binding[0][0]), str(binding[0][1]), str(binding[0][2])) == (
        "direct",
        HAUS_LEMMA,
        sense,
    )
    assert str(binding[0][3]) == "bound"
    assert deck.selected_meaning_languages(user_db, note_id) == ("de", "en")
    meaning = user_db.execute(
        "SELECT meaning_text FROM note_user_meaning WHERE note_id = ? AND lang = 'en'",
        (note_id,),
    ).fetchone()
    assert meaning is not None and str(meaning[0]) == "stub meaning"
    audio = user_db.execute(
        "SELECT media_filename FROM custom_pronunciation WHERE note_id = ?",
        (note_id,),
    ).fetchone()
    assert audio is not None and str(audio[0]) == "stub.wav"
    assert user_db.execute(
        "SELECT COUNT(*) FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchone()[0] == 1


def test_promotion_needs_gloss_to_derived(user_db: sqlite3.Connection) -> None:
    note_id, card_id, _, _ = _promotable_setup(user_db)
    vector = [(HAUS_LEMMA, "sense:v1:da"), (TUER_LEMMA, "sense:v1:db")]
    result = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        status="derived_compound",
        component_bindings=vector,
        meaning_languages=("de", "en"),
    )
    assert result.outcome == "promoted"
    assert (result.note_id, result.card_id) == (note_id, card_id)
    row = user_db.execute(
        "SELECT status, sense_semantic_ref, dictionary_key FROM note WHERE id = ?",
        (note_id,),
    ).fetchone()
    assert row is not None
    assert str(row[0]) == "derived_compound"
    assert row[1] is None
    assert str(row[2]) == deck.derived_dictionary_key(vector)
    bindings = user_db.execute(
        """
        SELECT component_ord, lemma_semantic_ref, sense_semantic_ref, component_count
        FROM note_dictionary_binding WHERE note_id = ? ORDER BY component_ord
        """,
        (note_id,),
    ).fetchall()
    assert [(int(r[0]), str(r[1]), str(r[2]), int(r[3])) for r in bindings] == [
        (0, HAUS_LEMMA, "sense:v1:da", 2),
        (1, TUER_LEMMA, "sense:v1:db", 2),
    ]


def _refuse_setup_reviewed(user_db: sqlite3.Connection) -> int:
    stub = deck.find_or_create_note(
        user_db, TAG_LEMMA, status="needs_gloss", meaning_languages=("de", "en")
    )
    deck.review(user_db, stub.card_id, 4)
    return stub.note_id


def test_promotion_refused_for_reviewed_stub(user_db: sqlite3.Connection) -> None:
    stub_id = _refuse_setup_reviewed(user_db)
    before = user_db.execute(
        "SELECT status, review_count, dictionary_key FROM note WHERE id = ?",
        (stub_id,),
    ).fetchone()
    result = deck.find_or_create_note(
        user_db,
        TAG_LEMMA,
        sense_semantic_ref="sense:v1:tag_new",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert result.outcome == "created"
    assert result.note_id != stub_id
    after = user_db.execute(
        "SELECT status, review_count, dictionary_key FROM note WHERE id = ?",
        (stub_id,),
    ).fetchone()
    assert tuple(after) == tuple(before)
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 2


def test_promotion_refused_for_stub_with_review_log(
    user_db: sqlite3.Connection,
) -> None:
    stub = deck.find_or_create_note(
        user_db, TAG_LEMMA, status="needs_gloss", meaning_languages=("de", "en")
    )
    user_db.execute(
        """
        INSERT INTO review_log (
            card_id, confidence, rating, scheduled_days, elapsed_days, reviewed_at
        ) VALUES (?, 4, 3, 0.0, 0.0, ?)
        """,
        (stub.card_id, NOW),
    )
    user_db.commit()
    result = deck.find_or_create_note(
        user_db,
        TAG_LEMMA,
        sense_semantic_ref="sense:v1:tag_new",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert result.outcome == "created"
    assert result.note_id != stub.note_id


def test_promotion_refused_for_bound_demoted_stub(
    user_db: sqlite3.Connection,
) -> None:
    stub = deck.find_or_create_note(
        user_db,
        TAG_LEMMA,
        sense_semantic_ref="sense:v1:tag_old",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    user_db.execute("UPDATE note SET status = 'needs_gloss' WHERE id = ?", (stub.note_id,))
    user_db.execute(
        "UPDATE note_dictionary_binding SET binding_status = 'unbound' WHERE note_id = ?",
        (stub.note_id,),
    )
    user_db.commit()
    result = deck.find_or_create_note(
        user_db,
        TAG_LEMMA,
        sense_semantic_ref="sense:v1:tag_fresh",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert result.outcome == "created"
    assert result.note_id != stub.note_id
    status = user_db.execute(
        "SELECT status FROM note WHERE id = ?", (stub.note_id,)
    ).fetchone()
    assert status is not None and str(status[0]) == "needs_gloss"


def test_promotion_refused_for_ambiguous_stubs(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _v0_add_note(conn, 1, TAG_LEMMA, status="needs_gloss")
    _v0_add_note(conn, 2, TAG_LEMMA, status="needs_gloss")
    conn.commit()
    conn.close()
    ensure_user_db(target)
    conn = _open(target)
    try:
        result = deck.find_or_create_note(
            conn,
            TAG_LEMMA,
            sense_semantic_ref="sense:v1:tag_new",
            status="resolved",
            meaning_languages=("de", "en"),
        )
        assert result.outcome == "created"
        assert _note_count(conn) == 3
    finally:
        conn.close()


def test_promotion_refused_for_conflicting_user_meaning(
    user_db: sqlite3.Connection,
) -> None:
    stub = deck.find_or_create_note(
        user_db, TAG_LEMMA, status="needs_gloss", meaning_languages=("de", "en")
    )
    deck.set_user_meaning(user_db, stub.note_id, "en", "stub text")
    result = deck.find_or_create_note(
        user_db,
        TAG_LEMMA,
        sense_semantic_ref="sense:v1:tag_new",
        status="resolved",
        meaning_languages=("de", "en"),
        user_meanings={"en": "different text"},
    )
    assert result.outcome == "created"
    kept = user_db.execute(
        "SELECT meaning_text FROM note_user_meaning WHERE note_id = ? AND lang = 'en'",
        (stub.note_id,),
    ).fetchone()
    assert kept is not None and str(kept[0]) == "stub text"


def test_promotion_skipped_when_target_already_keyed(
    user_db: sqlite3.Connection,
) -> None:
    keyed = deck.find_or_create_note(
        user_db,
        TAG_LEMMA,
        sense_semantic_ref="sense:v1:tag_keyed",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert keyed.outcome == "created"
    stub = deck.find_or_create_note(
        user_db, TAG_LEMMA, status="needs_gloss", meaning_languages=("de", "en")
    )
    assert stub.outcome == "created"
    again = deck.find_or_create_note(
        user_db,
        TAG_LEMMA,
        sense_semantic_ref="sense:v1:tag_keyed",
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert again.outcome == "reused"
    assert again.note_id == keyed.note_id
    status = user_db.execute(
        "SELECT status FROM note WHERE id = ?", (stub.note_id,)
    ).fetchone()
    assert status is not None and str(status[0]) == "needs_gloss"


# ---------------------------------------------------------------------------
# 8. Legacy NULL-key behavior on new writes
# ---------------------------------------------------------------------------


def _seed_legacy_null_singleton(
    conn: sqlite3.Connection, note_id: int, lemma_ref: str, sense_ref: str
) -> None:
    conn.execute(
        """
        INSERT INTO note (
            id, lemma_semantic_ref, sense_semantic_ref, status, dictionary_key,
            created_at, due_at
        ) VALUES (?, ?, ?, 'resolved', NULL, ?, ?)
        """,
        (note_id, lemma_ref, sense_ref, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO card (id, note_id, state, step, due_at) VALUES (?, ?, 0, NULL, ?)",
        (note_id, note_id, NOW),
    )
    conn.execute(
        "INSERT INTO note_meaning_lang (note_id, lang) VALUES (?, 'de'), (?, 'en')",
        (note_id, note_id),
    )
    conn.execute(
        """
        INSERT INTO note_dictionary_binding (
            note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
            binding_status, last_relinked_at
        ) VALUES (?, 'direct', 0, ?, ?, 'bound', ?)
        """,
        (note_id, lemma_ref, sense_ref, NOW),
    )
    conn.commit()


def test_single_legacy_null_row_is_claimed_and_reused(
    user_db: sqlite3.Connection,
) -> None:
    sense = "sense:v1:legacy_single"
    _seed_legacy_null_singleton(user_db, 1, HAUS_LEMMA, sense)
    result = deck.find_or_create_note(
        user_db,
        HAUS_LEMMA,
        sense_semantic_ref=sense,
        status="resolved",
        meaning_languages=("de", "en"),
    )
    assert result.outcome == "reused"
    assert result.note_id == 1
    assert _note_count(user_db) == 1
    key = user_db.execute("SELECT dictionary_key FROM note WHERE id = 1").fetchone()
    assert key is not None and str(key[0]) == f"resolved:v1:{sense}"


def test_legacy_duplicate_group_raises_conflict_without_writes(
    user_db: sqlite3.Connection,
) -> None:
    sense = "sense:v1:legacy_dup"
    lemma = compute_lemma_semantic_ref("Bank", "NOUN", "die")
    _seed_legacy_null_singleton(user_db, 1, lemma, sense)
    _seed_legacy_null_singleton(user_db, 2, lemma, sense)
    deck_id = deck.create_deck(user_db, "Lesson")
    user_db.commit()
    with pytest.raises(deck.LegacyDuplicateConflictError):
        deck.find_or_create_note(
            user_db,
            lemma,
            sense_semantic_ref=sense,
            status="resolved",
            meaning_languages=("de", "en"),
        )
    assert _note_count(user_db) == 2
    assert user_db.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 2
    assert user_db.execute(
        "SELECT COUNT(*) FROM note_deck WHERE deck_id = ?", (deck_id,)
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 9. Read-only diagnostic
# ---------------------------------------------------------------------------


def test_diagnostic_reports_groups_and_mutates_nothing(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    sense = "sense:v1:diag_dup"
    lemma = compute_lemma_semantic_ref("Bank", "NOUN", "die")
    _seed_duplicate_pair(conn, 1, 2, lemma, sense)
    _v0_add_note(conn, 3, TAG_LEMMA, status="needs_gloss")
    conn.commit()
    conn.close()
    ensure_user_db(target)
    conn = _open(target)
    before = (
        conn.execute("SELECT COUNT(*) FROM note").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM card").fetchone()[0],
        conn.execute(
            "SELECT COUNT(*) FROM note WHERE dictionary_key IS NULL"
        ).fetchone()[0],
    )
    report = deck.legacy_identity_diagnostic(conn)
    after = (
        conn.execute("SELECT COUNT(*) FROM note").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM card").fetchone()[0],
        conn.execute(
            "SELECT COUNT(*) FROM note WHERE dictionary_key IS NULL"
        ).fetchone()[0],
    )
    version_after = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert before == after == (3, 3, 2)
    assert version_after == 2
    assert report.total_notes == 3
    assert report.null_key_note_count == 2
    assert report.duplicate_resolved_groups == 1
    assert report.duplicate_needs_gloss_groups == 0
    assert report.duplicate_derived_groups == 0
    assert report.cross_status_groups == 0
    assert report.groups_with_review_history == 1
    assert report.groups_with_conflicting_user_meanings == 1
    assert report.groups_with_multiple_custom_audio == 1
    assert len(report.duplicate_groups) == 1
    assert report.duplicate_groups[0].note_ids == (1, 2)


def test_diagnostic_cross_status_group(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _v0_add_note(conn, 1, TAG_LEMMA, status="needs_gloss")
    _v0_add_note(conn, 2, TAG_LEMMA, status="needs_gloss")
    _v0_add_note(conn, 3, TAG_LEMMA, sense_ref="sense:v1:tag_x", status="resolved")
    _v0_add_direct_binding(conn, 3, TAG_LEMMA, "sense:v1:tag_x")
    _v0_add_note(conn, 4, TAG_LEMMA, sense_ref="sense:v1:tag_x", status="resolved")
    _v0_add_direct_binding(conn, 4, TAG_LEMMA, "sense:v1:tag_x")
    conn.commit()
    conn.close()
    ensure_user_db(target)
    conn = _open(target)
    try:
        report = deck.legacy_identity_diagnostic(conn)
    finally:
        conn.close()
    assert report.duplicate_needs_gloss_groups == 1
    assert report.duplicate_resolved_groups == 1
    assert report.cross_status_groups == 1


# ---------------------------------------------------------------------------
# 10. API unification (Offline)
# ---------------------------------------------------------------------------


def test_notes_created_then_reused_with_outcome(
    offline_app: tuple[TestClient, Path, Path],
) -> None:
    client, dict_path, _ = offline_app
    lemma_ref, sense_ref = _dict_refs(dict_path, "Haus")
    token = client.get("/vocab/lookup?q=Haus").json()["asset_token"]
    payload = {
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": token,
        "deck_name": "Deck",
    }
    first = client.post("/vocab/notes", json=payload, headers=AUTH_HEADERS)
    assert first.status_code == 201
    assert first.json()["outcome"] == "created"
    note_id = int(first.json()["note_id"])

    second = client.post("/vocab/notes", json=payload, headers=AUTH_HEADERS)
    assert second.status_code == 201
    body = second.json()
    assert body["outcome"] == "reused"
    assert int(body["note_id"]) == note_id

    conn = sqlite3.connect(offline_app[2])
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 1
    finally:
        conn.close()


def test_notes_reject_orphaned_capture(
    offline_app: tuple[TestClient, Path, Path],
) -> None:
    client, dict_path, user_db = offline_app
    lemma_ref, sense_ref = _dict_refs(dict_path, "Haus")
    token = client.get("/vocab/lookup?q=Haus").json()["asset_token"]
    resp = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "orphaned",
            "meaning_languages": ["de", "en"],
            "asset_token": token,
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422
    conn = sqlite3.connect(user_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    finally:
        conn.close()


def test_cards_reuse_identity_with_outcome(
    offline_app: tuple[TestClient, Path, Path],
) -> None:
    client, dict_path, user_db = offline_app
    lemma_ref, sense_ref = _dict_refs(dict_path, "Haus")
    token = client.get("/vocab/lookup?q=Haus").json()["asset_token"]
    selection = {
        "asset_token": token,
        "deck": "Deck A",
        "selections": [{"ref": lemma_ref, "sense_ref": sense_ref}],
    }
    first = client.post("/vocab/cards", json=selection, headers=AUTH_HEADERS)
    assert first.status_code == 201
    assert first.json()["notes"][0]["created"] is True
    assert first.json()["notes"][0]["outcome"] == "created"
    note_id = int(first.json()["notes"][0]["note_id"])

    selection["deck"] = "Deck B"
    second = client.post("/vocab/cards", json=selection, headers=AUTH_HEADERS)
    assert second.status_code == 201
    assert second.json()["notes"][0]["created"] is False
    assert second.json()["notes"][0]["outcome"] == "reused"
    assert int(second.json()["notes"][0]["note_id"]) == note_id

    conn = sqlite3.connect(user_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 2
    finally:
        conn.close()


def test_csv_reuse_and_promotion_counts(
    offline_app: tuple[TestClient, Path, Path],
) -> None:
    client, dict_path, user_db = offline_app
    lemma_ref, _ = _dict_refs(dict_path, "Haus")
    token = client.get("/vocab/lookup?q=Haus").json()["asset_token"]
    stub = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "status": "needs_gloss",
            "meaning_languages": ["de", "en"],
            "asset_token": token,
            "deck_name": "Stub deck",
        },
        headers=AUTH_HEADERS,
    )
    assert stub.status_code == 201
    stub_id = int(stub.json()["note_id"])

    first = client.post(
        "/vocab/import/csv",
        json={"csv_text": "Haus", "deck_name": "CSV deck"},
        headers=AUTH_HEADERS,
    )
    assert first.status_code == 201
    body = first.json()
    assert body["notes_promoted"] == 1
    assert body["notes_created"] == 0
    assert body["notes_reused"] == 0

    conn = sqlite3.connect(user_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
        status = conn.execute(
            "SELECT status FROM note WHERE id = ?", (stub_id,)
        ).fetchone()
        assert status is not None and str(status[0]) == "resolved"
    finally:
        conn.close()

    second = client.post(
        "/vocab/import/csv",
        json={"csv_text": "Haus", "deck_name": "CSV deck"},
        headers=AUTH_HEADERS,
    )
    assert second.status_code == 201
    assert second.json()["notes_reused"] == 1
    assert second.json()["notes_promoted"] == 0


def test_csv_derived_reuse_regression(
    offline_app: tuple[TestClient, Path, Path],
) -> None:
    client, _, user_db = offline_app
    first = client.post(
        "/vocab/import/csv",
        json={"csv_text": "Haustür", "deck_name": "CSV deck"},
        headers=AUTH_HEADERS,
    )
    assert first.status_code == 201
    assert first.json()["notes_created"] == 1
    conn = sqlite3.connect(user_db)
    try:
        status = conn.execute("SELECT status FROM note").fetchone()
        assert status is not None and str(status[0]) == "derived_compound"
    finally:
        conn.close()
    second = client.post(
        "/vocab/import/csv",
        json={"csv_text": "Haustür", "deck_name": "CSV deck"},
        headers=AUTH_HEADERS,
    )
    assert second.status_code == 201
    assert second.json()["notes_reused"] == 1
    assert second.json()["notes_created"] == 0
    conn = sqlite3.connect(user_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    finally:
        conn.close()


def test_csv_unknown_word_reuse(offline_app: tuple[TestClient, Path, Path]) -> None:
    client, _, user_db = offline_app
    payload = {"csv_text": "UnbekanntesWortXyz", "deck_name": "CSV deck"}
    first = client.post("/vocab/import/csv", json=payload, headers=AUTH_HEADERS)
    assert first.status_code == 201
    assert first.json()["notes_created"] == 1
    second = client.post("/vocab/import/csv", json=payload, headers=AUTH_HEADERS)
    assert second.status_code == 201
    assert second.json()["notes_reused"] == 1
    conn = sqlite3.connect(user_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    finally:
        conn.close()


def test_notes_then_csv_share_one_note(
    offline_app: tuple[TestClient, Path, Path],
) -> None:
    client, dict_path, user_db = offline_app
    lemma_ref, sense_ref = _dict_refs(dict_path, "Karte")
    token = client.get("/vocab/lookup?q=Karte").json()["asset_token"]
    created = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de", "en"],
            "asset_token": token,
            "deck_name": "Notes deck",
        },
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 201
    imported = client.post(
        "/vocab/import/csv",
        json={"csv_text": "Karte", "deck_name": "CSV deck"},
        headers=AUTH_HEADERS,
    )
    assert imported.status_code == 201
    assert imported.json()["notes_reused"] == 1
    conn = sqlite3.connect(user_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 2
    finally:
        conn.close()


def test_csv_then_notes_share_one_note(
    offline_app: tuple[TestClient, Path, Path],
) -> None:
    client, dict_path, user_db = offline_app
    imported = client.post(
        "/vocab/import/csv",
        json={"csv_text": "Karte", "deck_name": "CSV deck"},
        headers=AUTH_HEADERS,
    )
    assert imported.status_code == 201
    lemma_ref, sense_ref = _dict_refs(dict_path, "Karte")
    token = client.get("/vocab/lookup?q=Karte").json()["asset_token"]
    created = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de", "en"],
            "asset_token": token,
            "deck_name": "Notes deck",
        },
        headers=AUTH_HEADERS,
    )
    assert created.status_code == 201
    assert created.json()["outcome"] == "reused"
    conn = sqlite3.connect(user_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    finally:
        conn.close()


def test_unsafe_legacy_group_returns_409_without_third_note(
    tmp_path: Path, create_test_db: Callable[..., Path]
) -> None:
    dict_path = create_test_db(populate=True)
    _recompute_sense_refs(dict_path)
    user_db_path = tmp_path / "legacy_user.sqlite"
    ensure_user_db(user_db_path)
    conn = _open(user_db_path)
    lemma_ref, sense_ref = _dict_refs(dict_path, "Haus")
    _seed_legacy_null_singleton(conn, 1, lemma_ref, sense_ref)
    _seed_legacy_null_singleton(conn, 2, lemma_ref, sense_ref)
    conn.close()

    app = create_app(
        dict_path=dict_path,
        user_db_path=user_db_path,
        cors_origins=["http://127.0.0.1:8000"],
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        token = client.get("/vocab/lookup?q=Haus").json()["asset_token"]
        resp = client.post(
            "/vocab/notes",
            json={
                "lemma_semantic_ref": lemma_ref,
                "sense_semantic_ref": sense_ref,
                "status": "resolved",
                "meaning_languages": ["de", "en"],
                "asset_token": token,
                "deck_name": "Attacker deck",
            },
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "legacy_duplicate_conflict"

    conn = _open(user_db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 11. Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_create_resolves_to_single_identity(tmp_path: Path) -> None:
    target = tmp_path / "concurrent.sqlite"
    ensure_user_db(target)
    sense = "sense:v1:concurrent_0"
    barrier = threading.Barrier(2)
    results: list[deck.NoteIdentityResult] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            barrier.wait(timeout=10)
            result = deck.find_or_create_note(
                conn,
                HAUS_LEMMA,
                sense_semantic_ref=sense,
                status="resolved",
                meaning_languages=("de", "en"),
            )
            with lock:
                results.append(result)
        except BaseException as exc:
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert len(results) == 2
    assert results[0].note_id == results[1].note_id
    assert sorted(r.outcome for r in results) == ["created", "reused"]
    conn = _open(target)
    try:
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 1
        key = conn.execute("SELECT dictionary_key FROM note").fetchone()
        assert key is not None and str(key[0]) == f"resolved:v1:{sense}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 12. Offline / Online parity with a deterministic Online fixture
# ---------------------------------------------------------------------------


class _DeterministicOnlineProvider(DictionaryProvider):
    """Deterministic in-memory Online fixture: no network, fixed refs."""

    def __init__(self, lemma_ref: str, sense_ref: str) -> None:
        self._lemma_ref = lemma_ref
        self._sense_ref = sense_ref

    @property
    def asset_token(self) -> str:
        return "online-fixture-token-m2"

    def _lemma_entry(self) -> LemmaEntry:
        return LemmaEntry(
            lemma_id=701,
            semantic_ref=self._lemma_ref,
            lemma="Haus",
            pos="NOUN",
            gender="das",
            freq_rank=20,
            plural="Häuser",
            plural_none=0,
            genitive_sg="Hauses",
            aux=None,
            separable=0,
            particle=None,
            reflexive=0,
            praesens_3sg=None,
            praeteritum_3sg=None,
            partizip_ii=None,
            governs=None,
            comparative=None,
            superlative=None,
            ipa="haʊ̯s",
            source="wiktionary",
            license="CC BY-SA",
        )

    def _sense_entry(self) -> SenseEntry:
        return SenseEntry(
            sense_id=7011,
            lemma_id=701,
            semantic_ref=self._sense_ref,
            source_namespace="wiktextract:enwiktionary",
            source_ref="senseid:en-house-1",
            ord=0,
            register=None,
            source="wiktionary",
            license="CC BY-SA 4.0",
        )

    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[LemmaHit]:
        if lemma.strip().lower() != "haus":
            return []
        return [
            LemmaHit(
                lemma_id=701,
                lemma="Haus",
                pos="NOUN",
                gender="das",
                semantic_ref=self._lemma_ref,
                freq_rank=20,
            )
        ]

    def lookup_surface_form(self, form: str) -> Sequence[LemmaHit]:
        return []

    def lookup_senses(self, lemma_id: int) -> Sequence[SenseHit]:
        if lemma_id != 701:
            return []
        return [
            SenseHit(sense_id=7011, lemma_id=701, ord=0, semantic_ref=self._sense_ref)
        ]

    def lemma_for_ref(self, lemma_semantic_ref: str) -> LemmaEntry | None:
        if lemma_semantic_ref == self._lemma_ref:
            return self._lemma_entry()
        return None

    def lemma_for_id(self, lemma_id: int) -> LemmaEntry | None:
        if lemma_id == 701:
            return self._lemma_entry()
        return None

    def senses_for_lemma(self, lemma_id: int) -> Sequence[SenseEntry]:
        if lemma_id == 701:
            return [self._sense_entry()]
        return []

    def senses_for_ref(self, lemma_semantic_ref: str) -> Sequence[SenseEntry]:
        if lemma_semantic_ref == self._lemma_ref:
            return [self._sense_entry()]
        return []

    def meanings_for_lemma(self, lemma_id: int) -> Sequence[MeaningRow]:
        return []

    def meanings_for_sense(self, sense_id: int) -> Sequence[MeaningRow]:
        return []

    def examples_for_lemma(
        self, lemma_id: int, *, limit: int | None = None
    ) -> Sequence[ExampleRecord]:
        return []

    def surface_forms_for_lemma(self, lemma_id: int) -> Sequence[str]:
        return []

    def entry_for_ref(
        self,
        lemma_semantic_ref: str,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> DictionaryEntry | None:
        return None

    def entry_for_id(
        self,
        lemma_id: int,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> DictionaryEntry | None:
        return None

    def candidate_lookup(self, query: str) -> Sequence[CandidateLookup]:
        return []

    def sense_route(self, sense_ref: str) -> tuple[str, str] | None:
        if sense_ref == self._sense_ref:
            return (self._lemma_ref, self._sense_ref)
        return None

    def compound_components(
        self, component_refs: Sequence[tuple[str, str]]
    ) -> tuple[CompoundComponent, ...]:
        return ()


def test_online_and_offline_produce_identical_identity(
    offline_app: tuple[TestClient, Path, Path], tmp_path: Path
) -> None:
    offline_client, dict_path, _ = offline_app
    lemma_ref, sense_ref = _dict_refs(dict_path, "Haus")
    expected_key = deck.resolved_dictionary_key(sense_ref)

    provider = _DeterministicOnlineProvider(lemma_ref, sense_ref)
    online_user_db = tmp_path / "online_user.sqlite"
    ensure_user_db(online_user_db)
    info = OnlineSessionInfo(
        dataset_token="m2-online-fixture",
        asset_token=provider.asset_token,
        cache_dir=str(tmp_path / "online-cache"),
    )
    online_app = create_app(
        dict_path=None,
        user_db_path=online_user_db,
        cors_origins=["http://127.0.0.1:8000"],
        online_provider=provider,
        online_session_info=info,
        managed_dictionary_dir=tmp_path / "dictionary-slot",
    )
    payload = {
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": provider.asset_token,
        "deck_name": "Deck",
    }
    with TestClient(online_app, base_url="http://127.0.0.1:8000") as online_client:
        first = online_client.post("/vocab/notes", json=payload, headers=AUTH_HEADERS)
        assert first.status_code == 201
        assert first.json()["outcome"] == "created"
        second = online_client.post("/vocab/notes", json=payload, headers=AUTH_HEADERS)
        assert second.status_code == 201
        assert second.json()["outcome"] == "reused"
        assert int(second.json()["note_id"]) == int(first.json()["note_id"])

    conn = sqlite3.connect(online_user_db)
    try:
        key = conn.execute("SELECT dictionary_key FROM note").fetchone()
        assert key is not None and str(key[0]) == expected_key
    finally:
        conn.close()

    offline_token = offline_client.get("/vocab/lookup?q=Haus").json()["asset_token"]
    offline_payload = dict(payload, asset_token=offline_token)
    created = offline_client.post(
        "/vocab/notes", json=offline_payload, headers=AUTH_HEADERS
    )
    assert created.status_code == 201
    conn = sqlite3.connect(offline_app[2])
    try:
        offline_key = conn.execute("SELECT dictionary_key FROM note").fetchone()
        assert offline_key is not None and str(offline_key[0]) == expected_key
    finally:
        conn.close()


def test_fake_provider_satisfies_abstract_contract() -> None:
    provider = _DeterministicOnlineProvider("lemma:v1:x", "sense:v1:x")
    assert isinstance(provider, DictionaryProvider)
    assert provider.asset_token == "online-fixture-token-m2"
