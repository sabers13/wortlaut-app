"""Standalone runtime bootstrap, XDG path resolution, and user-database init.

Provides the small boundary that makes ``create_app`` usable from a single
launcher without any user-supplied path arguments:

* ``default_data_dir`` — XDG-compliant per-user data directory.
* ``ensure_user_db`` — Idempotent PART-B schema initialisation that never
  touches an existing user database (AGENTS R9 / dictionary separation).
* ``build_standalone_app`` — Convenience wrapper that computes XDG-style
  paths, ensures the user database, and constructs the FastAPI app with
  the loopback-only CORS allowlist required by AGENTS R12. Dictionary
  validation is delegated to ``DictionaryRuntime`` so the ~945 MB asset
  is streamed through SHA-256 / schema validation exactly once.

Nothing here writes to the dictionary file (AGENTS R9) and nothing here
adds a runtime LLM dependency (AGENTS R1).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT_DEFAULT: Path = Path(__file__).resolve().parent.parent
SCHEMA_FILENAME: str = "schema.sql"

#: Current explicit PART-B user-database schema version (M4).
PART_B_SCHEMA_VERSION: int = 2

#: Schema version of every pre-M2 user database.
PRE_M2_PART_B_VERSION: int = 0

#: Schema version of every pre-M4 (post-M2) user database.
PRE_M4_PART_B_VERSION: int = 1

#: Core PART-B tables that must exist before a v0 -> v1 migration may run.
_M2_REQUIRED_TABLES: tuple[str, ...] = (
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
)

#: Pre-M2 ``note`` columns the migration reads to compute identity keys.
_M2_REQUIRED_NOTE_COLUMNS: tuple[str, ...] = (
    "id",
    "lemma_semantic_ref",
    "sense_semantic_ref",
    "status",
)


class StandaloneError(ValueError):
    """Raised when the standalone bootstrap cannot produce a usable state."""


@dataclass(frozen=True)
class StandalonePaths:
    """Resolved per-user data layout for the standalone launch."""

    data_dir: Path
    dictionary_dir: Path
    dictionary_path: Path
    user_db_path: Path
    media_dir: Path
    cache_dir: Path


def default_data_dir() -> Path:
    """Return the per-user data directory following XDG conventions on Linux.

    ``$XDG_DATA_HOME`` is honoured when set; otherwise
    ``$HOME/.local/share/flashcard`` is used. A leading tilde in the
    environment value is also expanded.
    """
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(raw)) / "flashcard"
    home = os.environ.get("HOME", "").strip()
    if not home:
        raise StandaloneError("HOME is not set; cannot resolve default data directory")
    return Path(home) / ".local" / "share" / "flashcard"


def resolve_standalone_paths(
    *,
    data_dir: Path | str | None = None,
    dict_path: Path | str | None = None,
    user_db_path: Path | str | None = None,
    media_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
) -> StandalonePaths:
    """Compute the standalone paths, falling back to per-user defaults.

    Every optional override wins over its derived default. The returned
    paths are absolute and resolved; the user data directory is created
    on demand so callers do not have to mkdir before constructing the
    app.
    """
    base = Path(data_dir).resolve() if data_dir is not None else default_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    dictionary_dir = base / "dictionary"
    dictionary_dir.mkdir(parents=True, exist_ok=True)
    if dict_path is None:
        resolved_dict = dictionary_dir / "dictionary.sqlite"
    else:
        resolved_dict = Path(dict_path).resolve()
    if user_db_path is None:
        resolved_user_db = base / "flashcards.sqlite"
    else:
        resolved_user_db = Path(user_db_path).resolve()
    resolved_user_db.parent.mkdir(parents=True, exist_ok=True)
    resolved_media = (
        Path(media_dir).resolve() if media_dir is not None else base / "media"
    )
    resolved_media.mkdir(parents=True, exist_ok=True)
    resolved_cache = (
        Path(cache_dir).resolve() if cache_dir is not None else base / "cache"
    )
    resolved_cache.mkdir(parents=True, exist_ok=True)
    return StandalonePaths(
        data_dir=base,
        dictionary_dir=dictionary_dir,
        dictionary_path=resolved_dict,
        user_db_path=resolved_user_db,
        media_dir=resolved_media,
        cache_dir=resolved_cache,
    )


def _read_part_b_schema() -> str:
    """Return the PART-B schema section, sourced from ``reference/schema.sql``.

    Imported lazily so the file path is resolved relative to the repo
    root even when the package is installed elsewhere; if the schema
    cannot be located the bootstrap fails closed rather than silently
    building a divergent schema (ADR-0001 / AGENTS R6).
    """
    candidates: list[Path] = []
    here = Path(__file__).resolve().parent
    candidates.append(here.parent / "reference" / SCHEMA_FILENAME)
    candidates.append(Path.cwd() / "reference" / SCHEMA_FILENAME)
    for candidate in candidates:
        if candidate.is_file():
            schema_text = candidate.read_text(encoding="utf-8")
            _, marker, part_b = schema_text.partition("-- PART B")
            if not marker:
                raise StandaloneError(
                    f"reference schema at {candidate} is missing the PART B section"
                )
            return "-- PART B" + part_b
    raise StandaloneError(
        "reference/schema.sql not found; cannot bootstrap user database"
    )


def ensure_user_db(user_db_path: Path | str) -> Path:
    """Idempotently create or transactionally migrate the PART-B user database.

    The dictionary file is never opened or modified (AGENTS R9). A fresh
    database is initialised with PART-B tables only at
    :data:`PART_B_SCHEMA_VERSION`, with foreign keys enabled and the WAL
    journal mode set, and no migration backup is created for it.

    An existing database reports its explicit PART-B schema version via
    ``PRAGMA user_version``:

    * version 2: returned untouched — no migration write, no backup;
    * version 1 (pre-M4): one SQLite-safe backup is created beside the
      database first (never overwriting an existing file); then the
      version-1 -> version-2 folder migration runs inside one
      ``BEGIN IMMEDIATE`` transaction. Any failure rolls the transaction
      back so the database logical state is transactionally unchanged, and
      startup fails closed reporting the backup path;
    * version 0 (pre-M2): one backup is created, then the pending sequence
      version-0 -> version-1 -> version-2 runs inside a single
      ``BEGIN IMMEDIATE`` transaction and commits exactly once. Any failure
      anywhere in the sequence rolls the whole sequence back, so a
      pre-M2 database never ends in an intermediate committed version-1
      state;
    * newer than supported: fails closed — the database requires a newer
      Wortlaut version and is never downgraded.
    """
    target = Path(user_db_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        _create_fresh_user_db(target)
        return target
    version = _part_b_user_version(target)
    if version > PART_B_SCHEMA_VERSION:
        raise StandaloneError(
            "database requires a newer Wortlaut version "
            f"(user_version={version}); this build supports PART-B schema "
            f"version {PART_B_SCHEMA_VERSION}"
        )
    if version == PART_B_SCHEMA_VERSION:
        return target
    if version not in (PRE_M2_PART_B_VERSION, PRE_M4_PART_B_VERSION):
        raise StandaloneError(
            f"unsupported PART-B schema version user_version={version}; failing closed"
        )
    backup_path = _backup_user_db_before_migration(target, start_version=version)
    try:
        _migrate_user_db(target, start_version=version)
    except Exception as exc:
        raise StandaloneError(
            f"PART-B v{version} -> v{PART_B_SCHEMA_VERSION} migration failed; "
            "database logical state transactionally unchanged; "
            f"pre-migration backup at {backup_path}: {exc}"
        ) from exc
    return target


def _create_fresh_user_db(target: Path) -> None:
    """Create a new PART-B database directly at the current schema version."""
    schema_sql = _read_part_b_schema()
    conn = sqlite3.connect(target)
    try:
        conn.executescript(schema_sql)
        conn.execute("PRAGMA foreign_keys = ON")
        wal_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if wal_row is None or str(wal_row[0]).lower() != "wal":
            raise StandaloneError(
                "failed to establish WAL journal mode on the new user database"
            )
        conn.commit()
    finally:
        conn.close()


def _part_b_user_version(target: Path) -> int:
    """Return the explicit PART-B schema version of an existing database."""
    conn = sqlite3.connect(target)
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
    finally:
        conn.close()
    if row is None:
        raise StandaloneError(f"cannot read PART-B schema version of {target}")
    return int(row[0])


def _backup_user_db_before_migration(target: Path, *, start_version: int) -> Path:
    """Copy the user database beside itself using the SQLite backup API.

    The backup name is timestamped and never overwrites an existing file.
    The label encodes the starting schema version so the backup is
    self-describing: ``pre-m2-v0`` for a pre-M2 database and ``pre-m4-v1``
    for a pre-M4 database. Any failure stops startup before any schema
    mutation.
    """
    if start_version == PRE_M2_PART_B_VERSION:
        label = "pre-m2-v0"
    elif start_version == PRE_M4_PART_B_VERSION:
        label = "pre-m4-v1"
    else:
        raise StandaloneError(
            f"cannot choose a backup label for PART-B user_version={start_version}; "
            "refusing to migrate"
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = target.parent / f"{target.name}.{label}-{stamp}.bak"
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = target.parent / f"{target.name}.{label}-{stamp}-{counter}.bak"
    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    try:
        src = sqlite3.connect(target)
        dst = sqlite3.connect(candidate)
        src.backup(dst)
    except Exception as exc:
        raise StandaloneError(
            f"cannot create pre-migration backup of {target}: {exc}"
        ) from exc
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        if dst is not None:
            try:
                dst.close()
            except Exception:
                pass
    if not candidate.is_file():
        raise StandaloneError(
            f"pre-migration backup was not created for {target}; refusing to migrate"
        )
    return candidate


def _validate_pre_m2_part_b(conn: sqlite3.Connection) -> None:
    """Fail closed unless the expected pre-M2 PART-B core tables exist."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for required in _M2_REQUIRED_TABLES:
        if required not in tables:
            raise StandaloneError(
                f"pre-M2 PART-B validation failed: missing table {required!r}"
            )
    note_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(note)").fetchall()
    }
    for required in _M2_REQUIRED_NOTE_COLUMNS:
        if required not in note_columns:
            raise StandaloneError(
                f"pre-M2 PART-B validation failed: note is missing column {required!r}"
            )


def _validate_v1_part_b(conn: sqlite3.Connection) -> None:
    """Fail closed unless the expected v1 (post-M2, pre-M4) schema is present.

    A version-1 database must be a faithful M2 database: the core tables and
    ``note.dictionary_key`` with its partial UNIQUE index. A v1 database that
    already contains an M4 ``folder`` table, ``deck.folder_id`` column, or
    ``ix_deck_folder`` index is malformed/ambiguous and fails closed rather
    than being silently assumed valid.
    """
    _validate_pre_m2_part_b(conn)
    note_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(note)").fetchall()
    }
    if "dictionary_key" not in note_columns:
        raise StandaloneError(
            "v1 PART-B validation failed: note is missing column 'dictionary_key'"
        )
    index = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'index' AND name = 'ux_note_dictionary_key'"
    ).fetchone()
    if (
        index is None
        or index[0] is None
        or "WHERE dictionary_key IS NOT NULL" not in str(index[0])
    ):
        raise StandaloneError(
            "v1 PART-B validation failed: missing partial UNIQUE "
            "ux_note_dictionary_key index"
        )
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "folder" in tables:
        raise StandaloneError(
            "v1 PART-B validation failed: unexpected 'folder' table present"
        )
    deck_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(deck)").fetchall()
    }
    if "folder_id" in deck_columns:
        raise StandaloneError(
            "v1 PART-B validation failed: unexpected 'deck.folder_id' present"
        )
    artifact = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'ix_deck_folder'"
    ).fetchone()
    if artifact is not None:
        raise StandaloneError(
            "v1 PART-B validation failed: unexpected 'ix_deck_folder' index present"
        )


def _apply_v0_to_v1(conn: sqlite3.Connection) -> None:
    """Apply the M2 version-0 -> version-1 step inside the caller's transaction.

    Adds ``note.dictionary_key``, assigns the candidate key to unambiguous
    singleton notes, leaves every ambiguous/duplicate group NULL (no merge,
    no delete, no rewrite of cards, ``review_log``, meanings, audio, or
    memberships), creates the partial UNIQUE index, and stamps version 1.
    Never stamps the mutable current-version constant. Never inspects or
    modifies the dictionary.
    """
    from app.deck import _candidate_key_for_stored_note  # noqa: PLC0415

    _validate_pre_m2_part_b(conn)
    conn.execute("ALTER TABLE note ADD COLUMN dictionary_key TEXT")
    note_rows = conn.execute(
        "SELECT id, lemma_semantic_ref FROM note"
    ).fetchall()
    candidates: dict[int, str] = {}
    for row in note_rows:
        note_id = int(row[0])
        try:
            candidates[note_id] = _candidate_key_for_stored_note(
                conn, note_id, str(row[1])
            )
        except Exception as exc:
            raise StandaloneError(
                "pre-M2 PART-B migration failed while computing "
                f"identity for note {note_id}: {exc}"
            ) from exc
    occurrences: dict[str, int] = {}
    for candidate in candidates.values():
        occurrences[candidate] = occurrences.get(candidate, 0) + 1
    for note_id, candidate in candidates.items():
        if occurrences[candidate] == 1:
            conn.execute(
                "UPDATE note SET dictionary_key = ? WHERE id = ?",
                (candidate, note_id),
            )
    conn.execute(
        "CREATE UNIQUE INDEX ux_note_dictionary_key "
        "ON note(dictionary_key) WHERE dictionary_key IS NOT NULL"
    )
    conn.execute("PRAGMA user_version = 1")


def _apply_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Apply the M4 version-1 -> version-2 folder step in the caller's transaction.

    Creates the ``folder`` table, adds the nullable ``deck.folder_id``
    reference with ``ON DELETE SET NULL``, and creates ``ix_deck_folder``.
    Every existing deck ends with ``folder_id IS NULL`` and no existing row
    id/name/timestamp changes. Migration-step DDL deliberately omits
    ``IF NOT EXISTS`` so a malformed/ambiguous v1 database fails closed
    instead of being silently assumed valid.
    """
    _validate_v1_part_b(conn)
    conn.execute(
        """
        CREATE TABLE folder (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        ALTER TABLE deck
        ADD COLUMN folder_id INTEGER
        REFERENCES folder(id)
        ON DELETE SET NULL
        """
    )
    conn.execute("CREATE INDEX ix_deck_folder ON deck(folder_id)")
    conn.execute("PRAGMA user_version = 2")


def _migrate_user_db(target: Path, *, start_version: int) -> None:
    """Run the pending version-targeted migration sequence in one transaction.

    The sequence applies every step from ``start_version`` up to
    :data:`PART_B_SCHEMA_VERSION` without committing in between, so a
    version-0 database never ends in an intermediate committed version-1
    state. Any failure rolls the entire sequence back, leaving the logical
    database at the version it had when startup began.
    """
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if start_version == PRE_M2_PART_B_VERSION:
                _apply_v0_to_v1(conn)
            if start_version <= PRE_M4_PART_B_VERSION:
                _apply_v1_to_v2(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def verify_dictionary_asset(dict_path: Path | str) -> str:
    """Validate an existing dictionary file against the standalone PART-A contract.

    Returns the SHA-256 fingerprint of the dictionary bytes. The
    validation opens the database read-only via the candidate validator
    so a corrupt PART-A schema or a tampered-with file fails closed
    before the FastAPI app tries to read it.

    Kept for tests and other callers that need an explicit fail-closed
    check, but the standalone launcher no longer invokes it: full-file
    validation is delegated to ``DictionaryRuntime`` so the asset is
    streamed through SHA-256 / schema validation exactly once at
    activation (Repair G).
    """
    target = Path(dict_path).resolve()
    if not target.is_file():
        raise StandaloneError(f"dictionary file not found: {target}")
    try:
        raw_bytes = target.read_bytes()
    except OSError as exc:
        raise StandaloneError(f"dictionary file cannot be read: {target}") from exc
    digest = hashlib.sha256(raw_bytes).hexdigest()
    from app.dictionary import (  # noqa: PLC0415
        DictionaryAssetError,
        validate_candidate_dictionary,
    )

    try:
        asset = validate_candidate_dictionary(target)
    except DictionaryAssetError as exc:
        raise StandaloneError(
            f"dictionary PART-A validation failed: {exc}"
        ) from exc
    try:
        if asset.sha256 != digest:
            raise StandaloneError(
                "dictionary SHA-256 mismatch between read and validated snapshot"
            )
    finally:
        asset.close()
    return digest


def build_standalone_app(
    *,
    data_dir: Path | str | None = None,
    dict_path: Path | str | None = None,
    user_db_path: Path | str | None = None,
    media_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    cors_origins: Sequence[str] | None = None,
    port: int | None = None,
    tts_remote_url: str | None = None,
    expected_dictionary_sha256: str | None = None,
    expected_dictionary_version: str = "v1",
) -> Any:
    """Construct a FastAPI app using the standalone XDG path layout.

    The user database is initialised on demand; the dictionary file is
    not modified and not created (the dictionary is a read-only
    distributable asset, AGENTS R9 / ADR-0001). Full-file dictionary
    validation is delegated to ``DictionaryRuntime`` so the asset is
    streamed and SHA-256-checked exactly once at activation. The CORS
    allowlist defaults to the loopback endpoints used by the bundled
    frontend; when ``port`` is provided, both the ``127.0.0.1`` and
    ``localhost`` origins are constructed for that port so the bundled
    browser frontend's same-origin ``Origin`` header is accepted at
    non-default ports (Repair C).
    """
    paths = resolve_standalone_paths(
        data_dir=data_dir,
        dict_path=dict_path,
        user_db_path=user_db_path,
        media_dir=media_dir,
        cache_dir=cache_dir,
    )
    ensure_user_db(paths.user_db_path)
    if not paths.dictionary_path.is_file():
        raise StandaloneError(
            "dictionary asset is missing; place the verified dictionary.sqlite at "
            f"{paths.dictionary_path} or pass --dict-path"
        )
    port_value = 8000 if port is None else int(port)
    if cors_origins is None:
        cors_origins = (
            f"http://127.0.0.1:{port_value}",
            f"http://localhost:{port_value}",
        )
    from app.api import create_app  # noqa: PLC0415

    return create_app(
        dict_path=paths.dictionary_path,
        user_db_path=paths.user_db_path,
        cors_origins=cors_origins,
        service_port=port_value,
        tts_remote_url=tts_remote_url,
        media_dir=paths.media_dir,
        cache_dir=paths.cache_dir,
        expected_dictionary_sha256=expected_dictionary_sha256,
        expected_dictionary_version=expected_dictionary_version,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Tiny CLI used by the launcher to print the resolved standalone paths."""
    args = list(argv) if argv is not None else sys.argv[1:]
    data_dir_arg: str | None = None
    dict_arg: str | None = None
    user_arg: str | None = None
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--data-dir" and i + 1 < len(args):
            data_dir_arg = args[i + 1]
            i += 2
            continue
        if token == "--dict-path" and i + 1 < len(args):
            dict_arg = args[i + 1]
            i += 2
            continue
        if token == "--user-db" and i + 1 < len(args):
            user_arg = args[i + 1]
            i += 2
            continue
        if token in {"-h", "--help"}:
            sys.stdout.write(
                "Usage: python -m app.standalone [--data-dir DIR] [--dict-path PATH] "
                "[--user-db PATH]\n"
            )
            return 0
        sys.stderr.write(f"unknown argument: {token}\n")
        return 2
    paths = resolve_standalone_paths(
        data_dir=Path(data_dir_arg) if data_dir_arg else None,
        dict_path=Path(dict_arg) if dict_arg else None,
        user_db_path=Path(user_arg) if user_arg else None,
    )
    ensure_user_db(paths.user_db_path)
    sys.stdout.write(f"data_dir={paths.data_dir}\n")
    sys.stdout.write(f"dictionary_path={paths.dictionary_path}\n")
    sys.stdout.write(f"user_db_path={paths.user_db_path}\n")
    sys.stdout.write(f"media_dir={paths.media_dir}\n")
    sys.stdout.write(f"cache_dir={paths.cache_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
