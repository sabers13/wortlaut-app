"""M4 folder-persistence migration tests: PART-B v0 -> v2 and v1 -> v2.

Covers the M4 maintenance unit's deterministic migration contract:

* fresh v2 bootstrap (folder table, deck.folder_id, FK action, index, no backup);
* v1 -> v2 with exactly one pre-m4-v1 backup and full logical preservation;
* v0 -> v2 in one atomic pending sequence with one pre-m2-v0 backup and no
  intermediate committed v1 state;
* rollback of the whole sequence on failure anywhere;
* future-version fail-closed, idempotency, and malformed v1 artifacts.

All databases are temporary fixture copies. The owner's real production
database is never inspected or mutated here. The dictionary asset is never
opened.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import standalone as standalone_module
from app.standalone import StandaloneError, ensure_user_db
from tools.build_dict import compute_lemma_semantic_ref

REPO_ROOT = Path(__file__).resolve().parent.parent
V0_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "part_b_v0.sql"
V1_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "part_b_v1.sql"

NOW = "2026-01-01T00:00:00+00:00"
HAUS_LEMMA = compute_lemma_semantic_ref("Haus", "NOUN", "das")
HAUS_SENSE = "sense:v1:haus_das_0"

#: Explicit column projections kept free of the migration-added deck.folder_id
#: so a v1 snapshot can be compared with the post-migration v2 snapshot.
_SNAPSHOT_COLUMNS: dict[str, tuple[str, ...]] = {
    "deck": ("id", "name", "created_at"),
    "note": (
        "id",
        "lemma_semantic_ref",
        "sense_semantic_ref",
        "status",
        "dictionary_key",
        "created_at",
        "due_at",
        "interval_days",
        "ease_factor",
        "review_count",
        "last_confidence",
    ),
    "card": (
        "id",
        "note_id",
        "state",
        "step",
        "stability",
        "difficulty",
        "due_at",
        "last_review",
    ),
    "review_log": (
        "id",
        "card_id",
        "confidence",
        "rating",
        "scheduled_days",
        "elapsed_days",
        "reviewed_at",
    ),
    "note_deck": ("note_id", "deck_id", "created_at"),
    "note_meaning_lang": ("note_id", "lang"),
    "note_user_meaning": ("note_id", "lang", "meaning_text", "created_at", "updated_at"),
    "note_dictionary_binding": (
        "note_id",
        "role",
        "component_ord",
        "lemma_semantic_ref",
        "sense_semantic_ref",
        "cached_lemma_id",
        "cached_sense_id",
        "binding_status",
        "component_count",
        "last_relinked_at",
    ),
    "active_dictionary_metadata": (
        "singleton",
        "active_version",
        "active_filename",
        "active_sha256",
        "activated_at",
    ),
    "custom_pronunciation": (
        "note_id",
        "media_filename",
        "sha256",
        "byte_size",
        "format",
        "source_type",
        "created_at",
    ),
}


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _make_db_from_fixture(tmp_path: Path, fixture: Path, name: str) -> Path:
    target = tmp_path / name
    conn = sqlite3.connect(target)
    try:
        conn.executescript(fixture.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()
    return target


def _make_v0_db(tmp_path: Path, name: str = "user_v0.sqlite") -> Path:
    return _make_db_from_fixture(tmp_path, V0_FIXTURE, name)


def _make_v1_db(tmp_path: Path, name: str = "user_v1.sqlite") -> Path:
    return _make_db_from_fixture(tmp_path, V1_FIXTURE, name)


def _backups(path: Path, label: str) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.{label}-*.bak"))


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    snap: dict[str, list[tuple[object, ...]]] = {}
    for table, cols in _SNAPSHOT_COLUMNS.items():
        collist = ", ".join(cols)
        rows = conn.execute(
            f"SELECT {collist} FROM {table} ORDER BY {cols[0]}"
        ).fetchall()
        snap[table] = [tuple(row) for row in rows]
    return snap


def _seed_v1_dataset(conn: sqlite3.Connection) -> None:
    """Seed every PART-B table so v1 -> v2 preservation is fully checked."""
    conn.execute(
        "INSERT INTO deck (id, name, created_at) VALUES (?, ?, ?)",
        (1, "Lesson", "2025-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO deck (id, name, created_at) VALUES (?, ?, ?)",
        (2, "Orphaned", "2025-01-02T00:00:00+00:00"),
    )
    conn.execute(
        """
        INSERT INTO note (
            id, lemma_semantic_ref, sense_semantic_ref, status, dictionary_key,
            created_at, due_at, interval_days, ease_factor, review_count,
            last_confidence
        ) VALUES (?, ?, ?, 'resolved', ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, HAUS_LEMMA, HAUS_SENSE, f"resolved:v1:{HAUS_SENSE}", NOW, NOW, 3.5, 2.7, 2, 4),
    )
    conn.execute(
        "INSERT INTO card (id, note_id, state, step, stability, difficulty, due_at, "
        "last_review) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1, 1, 2, None, 12.5, 5.25, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO review_log (id, card_id, confidence, rating, scheduled_days, "
        "elapsed_days, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 1, 4, 3, 1.0, 2.0, NOW),
    )
    conn.execute(
        "INSERT INTO note_deck (note_id, deck_id, created_at) VALUES (?, ?, ?)",
        (1, 1, NOW),
    )
    conn.executemany(
        "INSERT INTO note_meaning_lang (note_id, lang) VALUES (?, ?)",
        [(1, "de"), (1, "en")],
    )
    conn.execute(
        "INSERT INTO note_user_meaning (note_id, lang, meaning_text, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?)",
        (1, "en", "house", NOW, NOW),
    )
    conn.execute(
        """
        INSERT INTO note_dictionary_binding (
            note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
            binding_status, last_relinked_at
        ) VALUES (1, 'direct', 0, ?, ?, 'bound', ?)
        """,
        (HAUS_LEMMA, HAUS_SENSE, NOW),
    )
    conn.execute(
        "INSERT INTO active_dictionary_metadata (singleton, active_version, "
        "active_filename, active_sha256, activated_at) VALUES (1, 'v1', ?, ?, ?)",
        ("dictionary.sqlite", "a" * 64, NOW),
    )
    conn.execute(
        "INSERT INTO custom_pronunciation (note_id, media_filename, sha256, "
        "byte_size, format, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "haus.wav", "b" * 64, 100, "wav", "uploaded", NOW),
    )


def _seed_v0_note(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO note (
            id, lemma_semantic_ref, sense_semantic_ref, status,
            created_at, due_at, review_count, last_confidence
        ) VALUES (1, ?, ?, 'resolved', ?, ?, 0, NULL)
        """,
        (HAUS_LEMMA, HAUS_SENSE, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO card (id, note_id, state, step, due_at) VALUES (1, 1, 0, NULL, ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO note_dictionary_binding (note_id, role, component_ord, "
        "lemma_semantic_ref, sense_semantic_ref, binding_status, last_relinked_at) "
        "VALUES (1, 'direct', 0, ?, ?, 'bound', ?)",
        (HAUS_LEMMA, HAUS_SENSE, NOW),
    )


# ---------------------------------------------------------------------------
# Fresh v2
# ---------------------------------------------------------------------------


def test_fresh_v2_schema_present_and_no_backup(tmp_path: Path) -> None:
    target = tmp_path / "fresh.sqlite"
    ensure_user_db(target)
    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        names = _table_names(conn)
        assert "folder" in names
        folder_sql = str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'folder'"
            ).fetchone()[0]
        )
        assert "UNIQUE" in folder_sql.upper()
        assert "folder_id" in _column_names(conn, "deck")
        notnull = {
            str(row[1]): int(row[3])
            for row in conn.execute("PRAGMA table_info(deck)").fetchall()
        }
        assert notnull["folder_id"] == 0  # nullable
        fk_rows = conn.execute("PRAGMA foreign_key_list(deck)").fetchall()
        matching = [
            row for row in fk_rows if row[2] == "folder" and row[3] == "folder_id"
        ]
        assert len(matching) == 1
        assert matching[0][4] == "id"  # FK target
        assert matching[0][6] == "SET NULL"  # ON DELETE action
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_deck_folder'"
        ).fetchone()
        assert index is not None
    finally:
        conn.close()
    assert _backups(target, "pre-m4-v1") == []
    assert _backups(target, "pre-m2-v0") == []


# ---------------------------------------------------------------------------
# v1 -> v2
# ---------------------------------------------------------------------------


def test_v1_to_v2_backs_up_and_preserves_all_state(tmp_path: Path) -> None:
    target = _make_v1_db(tmp_path)
    conn = _open(target)
    _seed_v1_dataset(conn)
    conn.commit()
    before = _snapshot(conn)
    conn.close()

    ensure_user_db(target)

    backups = _backups(target, "pre-m4-v1")
    assert len(backups) == 1
    backup_conn = sqlite3.connect(backups[0])
    try:
        assert backup_conn.execute("PRAGMA user_version").fetchone()[0] == 1
        backup_names = {
            str(row[0])
            for row in backup_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "folder" not in backup_names
        assert "folder_id" not in {
            str(row[1]) for row in backup_conn.execute("PRAGMA table_info(deck)").fetchall()
        }
    finally:
        backup_conn.close()

    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert _snapshot(conn) == before
        assert [row[0] for row in conn.execute("SELECT folder_id FROM deck ORDER BY id")] == [
            None,
            None,
        ]
        assert "folder" in _table_names(conn)
        assert "folder_id" in _column_names(conn, "deck")
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_deck_folder'"
        ).fetchone()
        assert index is not None
    finally:
        conn.close()
    assert _backups(target, "pre-m2-v0") == []


def test_v1_to_v2_preserves_deck_row_values(tmp_path: Path) -> None:
    target = _make_v1_db(tmp_path)
    conn = _open(target)
    _seed_v1_dataset(conn)
    conn.commit()
    conn.close()

    ensure_user_db(target)

    conn = _open(target)
    try:
        rows = conn.execute(
            "SELECT id, name, created_at, folder_id FROM deck ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (1, "Lesson", "2025-01-01T00:00:00+00:00", None),
            (2, "Orphaned", "2025-01-02T00:00:00+00:00", None),
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# v0 -> v2
# ---------------------------------------------------------------------------


def test_v0_to_v2_single_backup_applies_both_steps(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _seed_v0_note(conn)
    conn.commit()
    conn.close()

    ensure_user_db(target)

    assert len(_backups(target, "pre-m2-v0")) == 1
    assert _backups(target, "pre-m4-v1") == []

    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        key = conn.execute("SELECT dictionary_key FROM note WHERE id = 1").fetchone()[0]
        assert key == f"resolved:v1:{HAUS_SENSE}"
        assert "folder" in _table_names(conn)
        assert "folder_id" in _column_names(conn, "deck")
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_deck_folder'"
        ).fetchone()
        assert index is not None
        assert [row[0] for row in conn.execute("SELECT folder_id FROM deck")] == []
    finally:
        conn.close()


def test_v0_backup_predates_all_migration_writes(tmp_path: Path) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _seed_v0_note(conn)
    conn.commit()
    conn.close()

    ensure_user_db(target)

    backup = _backups(target, "pre-m2-v0")[0]
    backup_conn = sqlite3.connect(backup)
    try:
        assert backup_conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert "dictionary_key" not in {
            str(row[1]) for row in backup_conn.execute("PRAGMA table_info(note)").fetchall()
        }
        names = {
            str(row[0])
            for row in backup_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "folder" not in names
    finally:
        backup_conn.close()


# ---------------------------------------------------------------------------
# Failure injection / rollback
# ---------------------------------------------------------------------------


def test_v1_to_v2_failure_rolls_back_whole_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _make_v1_db(tmp_path)
    conn = _open(target)
    _seed_v1_dataset(conn)
    conn.commit()
    before = _snapshot(conn)
    conn.close()

    def failing_step(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE folder (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "ALTER TABLE deck ADD COLUMN folder_id INTEGER REFERENCES folder(id) "
            "ON DELETE SET NULL"
        )
        raise RuntimeError("injected v1 -> v2 failure")

    monkeypatch.setattr(standalone_module, "_apply_v1_to_v2", failing_step)

    with pytest.raises(StandaloneError, match="migration failed"):
        ensure_user_db(target)

    assert len(_backups(target, "pre-m4-v1")) == 1
    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "folder" not in _table_names(conn)
        assert "folder_id" not in _column_names(conn, "deck")
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_deck_folder'"
        ).fetchone()
        assert index is None
        assert _snapshot(conn) == before
    finally:
        conn.close()


def test_v0_to_v2_second_step_failure_rolls_back_first_step_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _make_v0_db(tmp_path)
    conn = _open(target)
    _seed_v0_note(conn)
    conn.commit()
    conn.close()

    def failing_step(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE folder (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
            "created_at TEXT NOT NULL)"
        )
        raise RuntimeError("injected second-step failure")

    monkeypatch.setattr(standalone_module, "_apply_v1_to_v2", failing_step)

    with pytest.raises(StandaloneError, match="migration failed"):
        ensure_user_db(target)

    assert len(_backups(target, "pre-m2-v0")) == 1
    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        # The v0 -> v1 step (note.dictionary_key + partial UNIQUE index) also rolled back.
        assert "dictionary_key" not in _column_names(conn, "note")
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'ux_note_dictionary_key'"
        ).fetchone()
        assert index is None
        assert "folder" not in _table_names(conn)
        assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Future / idempotency / malformed
# ---------------------------------------------------------------------------


def test_future_version_fails_closed(tmp_path: Path) -> None:
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

    conn = sqlite3.connect(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        conn.close()


def test_second_startup_on_v2_performs_no_write_or_backup(tmp_path: Path) -> None:
    target = tmp_path / "steady.sqlite"
    ensure_user_db(target)
    conn = _open(target)
    conn.execute(
        "INSERT INTO folder (name, created_at) VALUES (?, ?)", ("Kept", NOW)
    )
    conn.commit()
    conn.close()
    before_stat = target.stat()

    ensure_user_db(target)

    assert _backups(target, "pre-m4-v1") == []
    assert _backups(target, "pre-m2-v0") == []
    assert target.stat().st_mtime_ns == before_stat.st_mtime_ns
    conn = _open(target)
    try:
        assert conn.execute("SELECT name FROM folder WHERE name = 'Kept'").fetchone()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


@pytest.mark.parametrize(
    "artifact_ddl",
    [
        "CREATE TABLE folder (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
        "created_at TEXT NOT NULL)",
        "ALTER TABLE deck ADD COLUMN folder_id INTEGER",
        "CREATE INDEX ix_deck_folder ON deck(name)",
    ],
)
def test_malformed_v1_artifacts_fail_closed(
    tmp_path: Path, artifact_ddl: str
) -> None:
    target = _make_v1_db(tmp_path)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(artifact_ddl)
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(StandaloneError, match="migration failed"):
        ensure_user_db(target)

    assert len(_backups(target, "pre-m4-v1")) == 1
    conn = _open(target)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()