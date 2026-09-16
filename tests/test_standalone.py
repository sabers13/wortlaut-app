"""Tests for app/standalone.py — XDG paths, user DB bootstrap, and the
standalone app factory.

The tests build tiny synthetic dictionaries from the reference
schema, write them to a temporary directory, and assert that the
standalone bootstrap creates and reuses the user database
deterministically.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from app.standalone import (
    StandaloneError,
    StandalonePaths,
    build_standalone_app,
    default_data_dir,
    ensure_user_db,
    resolve_standalone_paths,
    verify_dictionary_asset,
)
from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "reference" / "schema.sql"


def _part_a_sql() -> str:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    part_a, marker, _ = text.partition("-- PART B")
    assert marker, "schema.sql missing the PART B section"
    return part_a


def _part_b_sql() -> str:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    _, marker, part_b = text.partition("-- PART B")
    assert marker
    return "-- PART B" + part_b


@pytest.fixture
def part_a_sql() -> str:
    return _part_a_sql()


@pytest.fixture
def part_b_sql() -> str:
    return _part_b_sql()


@pytest.fixture
def synthetic_dict(tmp_path: Path, part_a_sql: str) -> Path:
    """Create a minimal valid PART-A dictionary asset."""
    db_path = tmp_path / "dictionary.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(part_a_sql)
        # One lemma + sense + EN meaning + DE example
        lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
        sense_ref = compute_sense_semantic_ref(
            lemma_ref, "wiktextract:enwiktionary", "senseid:en-haus-1"
        )
        conn.execute(
            """
            INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, plural,
                plural_none, source, license)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'wiktionary', 'CC BY-SA')
            """,
            (1, lemma_ref, "Haus", "NOUN", "das", "die Häuser"),
        )
        conn.execute(
            """
            INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace,
                source_ref, ord, source, license)
            VALUES (?, ?, ?, ?, ?, 0, 'wiktionary', 'CC BY-SA')
            """,
            (1, 1, sense_ref, "wiktextract:enwiktionary", "senseid:en-haus-1"),
        )
        conn.execute(
            """
            INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text,
                source, license)
            VALUES (?, ?, 'en', 'translation', 0, 'house',
                'wiktionary', 'CC BY-SA')
            """,
            (1, 1),
        )
        conn.execute(
            """
            INSERT INTO example (id, de, en, source, license, token_count)
            VALUES (1, 'Das Haus ist gross.', 'The house is big.',
                'tatoeba', 'CC BY 2.0 FR', 5)
            """
        )
        conn.execute(
            "INSERT INTO example_lemma (lemma_id, example_id) VALUES (1, 1)"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_default_data_dir_uses_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    result = default_data_dir()
    assert result == tmp_path / "xdg" / "flashcard"


def test_default_data_dir_falls_back_to_home_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/test-user")
    assert default_data_dir() == Path("/home/test-user/.local/share/flashcard")


def test_default_data_dir_fails_closed_without_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(StandaloneError, match="HOME is not set"):
        default_data_dir()


def test_resolve_standalone_paths_creates_dirs(tmp_path: Path) -> None:
    paths = resolve_standalone_paths(data_dir=tmp_path / "data")
    assert paths.data_dir == tmp_path / "data"
    assert paths.dictionary_dir.is_dir()
    assert paths.dictionary_path == paths.dictionary_dir / "dictionary.sqlite"
    assert paths.user_db_path == tmp_path / "data" / "flashcards.sqlite"
    assert paths.media_dir.is_dir()
    assert paths.cache_dir.is_dir()


def test_resolve_standalone_paths_respects_overrides(tmp_path: Path) -> None:
    custom_dict = tmp_path / "my_dict.sqlite"
    custom_user = tmp_path / "my_user.sqlite"
    paths = resolve_standalone_paths(
        data_dir=tmp_path / "data",
        dict_path=custom_dict,
        user_db_path=custom_user,
    )
    assert paths.dictionary_path == custom_dict
    assert paths.user_db_path == custom_user


def test_ensure_user_db_creates_part_b_on_first_run(
    tmp_path: Path, part_b_sql: str
) -> None:
    target = tmp_path / "fresh.sqlite"
    assert not target.exists()
    ensure_user_db(target)
    assert target.is_file()
    # Verify PART-B tables exist
    conn = sqlite3.connect(target)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    for required in (
        "deck",
        "note",
        "card",
        "review_log",
        "note_deck",
        "note_meaning_lang",
        "note_user_meaning",
        "note_dictionary_binding",
        "active_dictionary_metadata",
        "custom_pronunciation",
    ):
        assert required in names, f"PART-B table missing: {required}"


def test_ensure_user_db_is_idempotent_on_second_run(
    tmp_path: Path, part_b_sql: str
) -> None:
    target = tmp_path / "twice.sqlite"
    ensure_user_db(target)
    first_size = target.stat().st_size
    # Insert user data so the second call must not destroy it.
    conn = sqlite3.connect(target)
    try:
        conn.execute(
            "INSERT INTO deck (name, created_at) VALUES (?, ?)",
            ("Existing", "2025-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    ensure_user_db(target)
    assert target.stat().st_size >= first_size
    conn = sqlite3.connect(target)
    try:
        deck_row = conn.execute(
            "SELECT name FROM deck WHERE name = 'Existing'"
        ).fetchone()
    finally:
        conn.close()
    assert deck_row is not None, "Second ensure_user_db must not destroy user data"


def test_ensure_user_db_enables_wal_journal_mode(tmp_path: Path) -> None:
    target = tmp_path / "wal.sqlite"
    ensure_user_db(target)
    conn = sqlite3.connect(target)
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    finally:
        conn.close()
    assert mode is not None and str(mode[0]).lower() == "wal"


def test_verify_dictionary_asset_returns_sha256(synthetic_dict: Path) -> None:
    digest = verify_dictionary_asset(synthetic_dict)
    assert len(digest) == 64
    assert all(ch in "0123456789abcdef" for ch in digest)


def test_verify_dictionary_asset_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no.sqlite"
    with pytest.raises(StandaloneError, match="not found"):
        verify_dictionary_asset(missing)


def test_verify_dictionary_asset_rejects_corrupt_bytes(
    tmp_path: Path, part_a_sql: str
) -> None:
    db = tmp_path / "broken.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(part_a_sql)
    finally:
        conn.close()
    # Truncate to a non-SQLite prefix
    db.write_bytes(b"NOT A SQLITE FILE")
    with pytest.raises(StandaloneError):
        verify_dictionary_asset(db)


def test_build_standalone_app_creates_user_db_and_returns_app(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    app = build_standalone_app(
        data_dir=tmp_path / "data",
        dict_path=synthetic_dict,
        user_db_path=tmp_path / "data" / "user.sqlite",
        cors_origins=("http://127.0.0.1:8000", "http://localhost:8000"),
    )
    assert app is not None
    # The user DB must now exist.
    user_db = tmp_path / "data" / "user.sqlite"
    assert user_db.is_file()
    # media + cache dirs must exist.
    assert (tmp_path / "data" / "media").is_dir()
    assert (tmp_path / "data" / "cache").is_dir()


def test_build_standalone_app_rejects_wildcard_cors(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    with pytest.raises(ValueError, match="Wildcard origin is forbidden"):
        build_standalone_app(
            data_dir=tmp_path / "data",
            dict_path=synthetic_dict,
            cors_origins=("*",),
        )


def test_build_standalone_app_fails_closed_on_missing_dict(tmp_path: Path) -> None:
    with pytest.raises(StandaloneError, match="dictionary asset is missing"):
        build_standalone_app(
            data_dir=tmp_path / "data",
            dict_path=tmp_path / "data" / "no.sqlite",
        )


def test_build_standalone_app_reuses_existing_user_data(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    user_db = tmp_path / "data" / "user.sqlite"
    user_db.parent.mkdir(parents=True, exist_ok=True)
    ensure_user_db(user_db)
    conn = sqlite3.connect(user_db)
    try:
        conn.execute(
            "INSERT INTO deck (name, created_at) VALUES (?, ?)",
            ("Preserved", "2025-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    build_standalone_app(
        data_dir=tmp_path / "data",
        dict_path=synthetic_dict,
        user_db_path=user_db,
    )
    conn = sqlite3.connect(user_db)
    try:
        rows = conn.execute("SELECT name FROM deck").fetchall()
    finally:
        conn.close()
    assert any(row[0] == "Preserved" for row in rows)


def test_build_standalone_app_default_cors_is_loopback_only(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """The default CORS allowlist must include both loopback forms only."""
    app = build_standalone_app(
        data_dir=tmp_path / "data",
        dict_path=synthetic_dict,
    )
    cors_origins = app.state.cors_origins
    assert "http://127.0.0.1:8000" in cors_origins
    assert "http://localhost:8000" in cors_origins
    assert "*" not in cors_origins


def test_standalone_main_prints_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    synthetic_dict: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "app.standalone",
            "--data-dir",
            str(tmp_path / "data"),
            "--dict-path",
            str(synthetic_dict),
        ],
    )
    # Lazy import to keep the CLI module import surface small.
    from app import standalone as standalone_module

    rc = standalone_module.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "user_db_path=" in out
    assert "dictionary_path=" in out


def test_standalone_paths_dataclass_is_frozen() -> None:
    paths = StandalonePaths(
        data_dir=Path("/tmp"),
        dictionary_dir=Path("/tmp/d"),
        dictionary_path=Path("/tmp/d/x.sqlite"),
        user_db_path=Path("/tmp/u.sqlite"),
        media_dir=Path("/tmp/m"),
        cache_dir=Path("/tmp/c"),
    )
    with pytest.raises(Exception):
        paths.data_dir = Path("/other")  # type: ignore[misc]
