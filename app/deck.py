"""PART-B deck persistence, FSRS reviews, and learner-meaning selection.

This module owns only the mutable user database. Dictionary meaning data is an
already-validated value supplied by the caller; this module never opens the
dictionary database (AGENTS R9).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, cast

from fsrs import Card, Rating, Scheduler, State

from app.dictionary import DictionaryAsset, DictionaryAssetError, validate_candidate_dictionary
from app.provider import DictionaryProvider
from app.selection import MAX_CARD_EXAMPLES, filter_candidates, rank_senses

DictionaryMeanings: TypeAlias = Mapping[str, object]
ComponentBinding: TypeAlias = tuple[str, str]

ORPHANED_DECK_NAME: str = "Orphaned"


class DeckError(ValueError):
    """Raised when a deck-layer request violates its data contract."""


class DeckNotFoundError(DeckError):
    """Raised when a deck row is missing for a deck-scoped operation."""


class NoteNotFoundError(DeckError):
    """Raised when a note row is missing for a deck-scoped operation."""


class DeckMembershipNotFoundError(DeckError):
    """Raised when a note_deck membership row is missing."""


class OrphanedDeckProtectedError(DeckError):
    """Raised when an operation would mutate the protected ``Orphaned`` deck."""


class NoteNotOrphanedError(DeckError):
    """Raised when an operation requires ``note.status == 'orphaned'``."""


class InconsistentOrphanStateError(DeckError):
    """Raised when an orphan does not satisfy the durable safe-restoration shape.

    The orphan invariant is exactly::

        note.status == 'orphaned'
        AND exactly one note_deck membership
        AND that membership points to the protected 'Orphaned' deck.

    Any deviation — multiple memberships, no memberships, a normal deck
    membership alongside an Orphaned membership, or an Orphaned status with
    no Orphaned membership — is a structurally inconsistent persistent
    state and M5 refuses to restore rather than silently repair it.
    """


class DeckConflictError(DeckError):
    """Raised when a deck name uniqueness constraint conflicts."""


class FolderNotFoundError(DeckError):
    """Raised when a folder row is missing for a folder-scoped operation."""


class FolderConflictError(DeckError):
    """Raised when a folder name uniqueness constraint conflicts."""


class DictionaryRuntimeError(DeckError):
    """Raised when dictionary runtime operations fail."""


class DictionaryClosedError(DictionaryRuntimeError):
    """Raised when an operation is attempted on a closed dictionary runtime."""


class LegacyDuplicateConflictError(DeckError):
    """Raised when a capture matches an ambiguous legacy NULL-key group.

    The group holds two or more pre-M2 rows with the same exact semantic
    identity. M2 never chooses a winner and never creates another row, so
    the capture fails closed for later M3 user-visible management.
    """

    def __init__(self, dictionary_key: str, note_ids: Sequence[int]) -> None:
        super().__init__(
            f"capture matches {len(tuple(note_ids))} legacy duplicate notes "
            f"for identity {dictionary_key!r}; refusing to create another"
        )
        self.dictionary_key = dictionary_key
        self.note_ids = tuple(note_ids)


class SelectedSenseConflictError(DeckError):
    """Raised when another note already owns the requested selected-sense key.

    The selected-sense edit never merges two notes; it refuses the
    rebind whenever another note already owns the target identity.
    """

    def __init__(self, dictionary_key: str, owner_note_id: int) -> None:
        super().__init__(
            f"selected sense key {dictionary_key!r} is already owned by "
            f"note {owner_note_id}; refusing to overwrite"
        )
        self.dictionary_key = dictionary_key
        self.owner_note_id = owner_note_id


class PromotionGatesFailedError(DeckError):
    """Raised when M2 promotion gates reject a ``needs_gloss`` rebind.

    M6 must not weaken M2 merely to let a never-bound stub be promoted:
    the same predicates that guard M2 promotion also gate the
    ``needs_gloss`` -> ``resolved`` transition here.
    """


class InconsistentNoteIdentityError(DeckError):
    """Raised when a non-orphaned note has a malformed durable identity.

    M6 refuses to silently repair malformed semantic identity. The
    conditions that raise this error are exactly the same direct-binding
    structural defects M5's :class:`InconsistentOrphanStateError`
    treats as malformed: missing ``lemma_semantic_ref``, missing
    ``sense_semantic_ref`` for a resolved note, no direct binding or
    multiple direct bindings, a non-NULL ``component_count`` on a
    direct binding, a non-zero ``component_ord`` on a direct binding,
    a direct binding whose lemma/sense ref disagrees with the note's
    durable refs, or a ``dictionary_key`` whose computed identity
    disagrees with the binding-derived key. M6 uses its own
    narrowly-named exception so it does not collide with M5's
    orphan-only contract.
    """


class UnsupportedSelectedSenseEditError(DeckError):
    """Raised when a note's status refuses the M6 selected-sense edit.

    ``derived_compound``, ``orphaned``, and ``resolved -> needs_gloss``
    are not M6 transitions; the contract refuses them with HTTP 409
    ``unsupported_selected_sense_edit``.
    """


@dataclass(frozen=True, slots=True)
class ReadingSnapshot:
    """Inert immutable value snapshot holding only copied values from one generation."""

    asset_token: str
    lemma_ids: Mapping[str, int]
    sense_ids: Mapping[str, tuple[int, int]]
    lemma_identity_fingerprints: Mapping[str, str]
    sense_identity_fingerprints: Mapping[str, str]
    bindings: Mapping[tuple[int, str, int], tuple[int | None, int | None]]


@dataclass
class _Generation:
    """Generation descriptor tracking asset handle and lease pins."""

    generation_id: int
    asset: DictionaryAsset
    pins: int = 0
    retired: bool = False
    closed: bool = False


def _is_same_file(p1: Path, p2: Path) -> bool:
    try:
        return os.path.samefile(p1, p2)
    except OSError:
        return False


@dataclass(frozen=True)
class ReviewResult:
    """The persisted result of a confidence-based FSRS review."""

    card_id: int
    confidence: int
    rating: int
    due_at: datetime
    interval_days: float
    state: State
    stability: float | None
    difficulty: float | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _parse_timestamp(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


def _last_insert_id(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite INSERT did not return a row id")
    return int(cursor.lastrowid)


def _validate_language(language: str) -> None:
    if language not in ("de", "en"):
        raise DeckError("meaning language must be 'de' or 'en'")


def _validate_languages(languages: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(languages)
    if not selected:
        raise DeckError("at least one meaning language is required")
    if len(set(selected)) != len(selected):
        raise DeckError("meaning languages must not contain duplicates")
    for language in selected:
        _validate_language(language)
    return selected


def confidence_to_rating(confidence: int) -> Rating:
    """Apply the sole ADR-0003 D28 confidence-to-FSRS mapping."""
    match confidence:
        case 1 | 2:
            return Rating.Again
        case 3:
            return Rating.Hard
        case 4:
            return Rating.Good
        case 5:
            return Rating.Easy
        case _:
            raise DeckError("confidence must be an integer from 1 through 5")


def _scheduler() -> Scheduler:
    """Return the pinned v1 scheduler without module-level mutable state."""
    return Scheduler(
        learning_steps=(timedelta(minutes=1), timedelta(minutes=10)),
        enable_fuzzing=False,
    )


def _transaction_context(conn: sqlite3.Connection, manage: bool) -> Any:
    return conn if manage else nullcontext()


# ---------------------------------------------------------------------------
# M2 — duplicate-safe note identity (note.dictionary_key).
#
# The key is a pure function of durable D47 semantic references only. It
# never uses numeric lemma/sense IDs, deck IDs, selected languages, user
# meanings, card state, or headword strings, and component vectors are
# ORDERED (never sorted, never case-folded).
# ---------------------------------------------------------------------------

#: Key-scheme version embedded in every M2 identity key.
M2_IDENTITY_SCHEME_VERSION = "v1"

#: Outcome of the shared :func:`find_or_create_note` domain operation.
NoteIdentityOutcome: TypeAlias = Literal["created", "reused", "promoted"]


@dataclass(frozen=True, slots=True)
class NoteIdentityResult:
    """Authoritative result of :func:`find_or_create_note`."""

    note_id: int
    card_id: int
    outcome: NoteIdentityOutcome


def resolved_dictionary_key(sense_semantic_ref: str) -> str:
    """Return the canonical key for a resolved sense identity."""
    if not sense_semantic_ref.strip():
        raise DeckError("sense semantic reference must not be blank")
    return f"resolved:{M2_IDENTITY_SCHEME_VERSION}:{sense_semantic_ref}"


def needs_gloss_dictionary_key(lemma_semantic_ref: str) -> str:
    """Return the canonical key for a never-bound lemma identity."""
    if not lemma_semantic_ref.strip():
        raise DeckError("lemma semantic reference must not be blank")
    return f"needs_gloss:{M2_IDENTITY_SCHEME_VERSION}:{lemma_semantic_ref}"


def derived_dictionary_key(component_bindings: Sequence[ComponentBinding]) -> str:
    """Return the canonical key for an ORDERED derived-compound vector."""
    components = tuple(component_bindings)
    if not components:
        raise DeckError("derived compounds require component bindings")
    for lemma_ref, sense_ref in components:
        if not lemma_ref.strip() or not sense_ref.strip():
            raise DeckError("component bindings require non-blank semantic references")
    payload = json.dumps(
        [[lemma_ref, sense_ref] for lemma_ref, sense_ref in components],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"derived:{M2_IDENTITY_SCHEME_VERSION}:{digest}"


def target_dictionary_key(
    status: str,
    lemma_semantic_ref: str,
    sense_semantic_ref: str | None = None,
    component_bindings: Sequence[ComponentBinding] = (),
) -> str:
    """Return the canonical identity key for a requested note identity.

    ``orphaned`` is a lifecycle/membership outcome, never a capture status,
    but its identity still follows durable bindings when it must be
    computed (migration, diagnostics).
    """
    if status == "resolved":
        if sense_semantic_ref is None or not sense_semantic_ref.strip():
            raise DeckError("resolved notes require a sense semantic reference")
        return resolved_dictionary_key(sense_semantic_ref)
    if status == "derived_compound":
        return derived_dictionary_key(component_bindings)
    if status == "needs_gloss":
        return needs_gloss_dictionary_key(lemma_semantic_ref)
    if status == "orphaned":
        components = tuple(component_bindings)
        if components:
            return derived_dictionary_key(components)
        if sense_semantic_ref is not None and sense_semantic_ref.strip():
            return resolved_dictionary_key(sense_semantic_ref)
        return needs_gloss_dictionary_key(lemma_semantic_ref)
    raise DeckError("unknown note status")


def _candidate_key_for_stored_note(
    conn: sqlite3.Connection,
    note_id: int,
    lemma_semantic_ref: str,
) -> str:
    """Compute the exact identity candidate for one stored note row.

    Identity follows durable ``note_dictionary_binding`` rows, not
    ``note.status``: a note demoted to ``needs_gloss`` by dictionary
    activation retains its previous binding identity. Unbound/bound state
    is presentation; the refs are identity.
    """
    comp_rows = conn.execute(
        """
        SELECT component_ord, lemma_semantic_ref, sense_semantic_ref
        FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'component'
        ORDER BY component_ord ASC
        """,
        (note_id,),
    ).fetchall()
    direct_rows = conn.execute(
        """
        SELECT component_ord, lemma_semantic_ref, sense_semantic_ref
        FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'direct'
        ORDER BY component_ord ASC
        """,
        (note_id,),
    ).fetchall()
    if comp_rows and not direct_rows:
        if [int(row[0]) for row in comp_rows] != list(range(len(comp_rows))):
            return needs_gloss_dictionary_key(lemma_semantic_ref)
        return derived_dictionary_key(
            [(str(row[1]), str(row[2])) for row in comp_rows]
        )
    if direct_rows and not comp_rows:
        return resolved_dictionary_key(str(direct_rows[0][2]))
    if not direct_rows and not comp_rows:
        return needs_gloss_dictionary_key(lemma_semantic_ref)
    return needs_gloss_dictionary_key(lemma_semantic_ref)


def create_deck(
    conn: sqlite3.Connection,
    name: str,
    *,
    created_at: datetime | None = None,
    _manage_transaction: bool = True,
) -> int:
    """Create a user deck and return its primary key."""
    if not name.strip():
        raise DeckError("deck name must not be blank")
    with _transaction_context(conn, _manage_transaction):
        cursor = conn.execute(
            "INSERT INTO deck (name, created_at) VALUES (?, ?)",
            (name.strip(), _timestamp(_as_utc(created_at))),
        )
    return _last_insert_id(cursor)


def create_note(
    conn: sqlite3.Connection,
    lemma_semantic_ref: str,
    *,
    sense_semantic_ref: str | None = None,
    status: str = "needs_gloss",
    component_bindings: Sequence[ComponentBinding] = (),
    meaning_languages: Sequence[str],
    created_at: datetime | None = None,
    _manage_transaction: bool = True,
) -> int:
    """Create a note, card, D47 bindings, and a non-empty language selection."""
    if not lemma_semantic_ref.strip():
        raise DeckError("lemma semantic reference must not be blank")
    if status not in ("resolved", "needs_gloss", "derived_compound", "orphaned"):
        raise DeckError("unknown note status")
    selected = _validate_languages(meaning_languages)
    components = tuple(component_bindings)
    if status == "derived_compound" and not components:
        raise DeckError("derived compounds require component bindings")
    if status == "resolved" and not sense_semantic_ref:
        raise DeckError("resolved notes require a sense semantic reference")
    for lemma_ref, sense_ref in components:
        if not lemma_ref.strip() or not sense_ref.strip():
            raise DeckError("component bindings require non-blank semantic references")

    dictionary_key = target_dictionary_key(
        status, lemma_semantic_ref, sense_semantic_ref, components
    )
    now_text = _timestamp(_as_utc(created_at))
    with _transaction_context(conn, _manage_transaction):
        cursor = conn.execute(
            """
            INSERT INTO note (
                lemma_semantic_ref, sense_semantic_ref, status, dictionary_key,
                created_at, due_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (lemma_semantic_ref, sense_semantic_ref, status, dictionary_key, now_text, now_text),
        )
        note_id = _last_insert_id(cursor)
        conn.execute(
            "INSERT INTO card (note_id, state, step, due_at) VALUES (?, ?, ?, ?)",
            (note_id, int(State.Learning), None, now_text),
        )
        conn.executemany(
            "INSERT INTO note_meaning_lang (note_id, lang) VALUES (?, ?)",
            ((note_id, language) for language in selected),
        )
        if status == "resolved" and sense_semantic_ref is not None:
            conn.execute(
                """
                INSERT INTO note_dictionary_binding (
                    note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
                    binding_status, last_relinked_at
                ) VALUES (?, 'direct', 0, ?, ?, 'bound', ?)
                """,
                (note_id, lemma_semantic_ref, sense_semantic_ref, now_text),
            )
        elif status == "derived_compound":
            conn.executemany(
                """
                INSERT INTO note_dictionary_binding (
                    note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
                    binding_status, component_count, last_relinked_at
                ) VALUES (?, 'component', ?, ?, ?, 'bound', ?, ?)
                """,
                (
                    (note_id, ordinal, lemma_ref, sense_ref, len(components), now_text)
                    for ordinal, (lemma_ref, sense_ref) in enumerate(components)
                ),
            )
    return note_id


def add_note_to_deck(
    conn: sqlite3.Connection,
    note_id: int,
    deck_id: int,
    *,
    created_at: datetime | None = None,
    _manage_transaction: bool = True,
) -> None:
    """Add a note to a deck without duplicating an existing membership."""
    with _transaction_context(conn, _manage_transaction):
        conn.execute(
            """
            INSERT OR IGNORE INTO note_deck (note_id, deck_id, created_at)
            VALUES (?, ?, ?)
            """,
            (note_id, deck_id, _timestamp(_as_utc(created_at))),
        )


def _card_id_for_note_or_raise(conn: sqlite3.Connection, note_id: int) -> int:
    row = conn.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()
    if row is None:
        raise DeckError(f"note {note_id} has no card")
    return int(row[0])


def _apply_capture_overrides(
    conn: sqlite3.Connection,
    note_id: int,
    *,
    meaning_languages: Sequence[str] | None,
    user_meanings: Mapping[str, str | None] | None,
) -> None:
    """Apply caller-supplied languages/meanings inside an open transaction."""
    if meaning_languages is not None:
        set_meaning_languages(conn, note_id, meaning_languages, _manage_transaction=False)
    if user_meanings is not None:
        for language, text in user_meanings.items():
            _validate_language(language)
            if text is not None:
                if not text.strip():
                    raise DeckError("user meaning must not be blank")
                set_user_meaning(
                    conn, note_id, language, text.strip(), _manage_transaction=False
                )
            else:
                delete_user_meaning(conn, note_id, language, _manage_transaction=False)


def _validate_capture_request(
    lemma_semantic_ref: str,
    sense_semantic_ref: str | None,
    status: str,
    components: tuple[ComponentBinding, ...],
    meaning_languages: Sequence[str] | None,
    user_meanings: Mapping[str, str | None] | None,
) -> tuple[str, ...] | None:
    """Validate one capture request; return validated languages (or None)."""
    if not lemma_semantic_ref.strip():
        raise DeckError("lemma semantic reference must not be blank")
    if status == "orphaned":
        raise DeckError(
            "orphaned is a lifecycle/membership outcome, not a valid capture status"
        )
    if status not in ("resolved", "needs_gloss", "derived_compound"):
        raise DeckError("unknown note status")
    if status == "resolved" and (
        sense_semantic_ref is None or not sense_semantic_ref.strip()
    ):
        raise DeckError("resolved notes require a sense semantic reference")
    if status == "derived_compound" and not components:
        raise DeckError("derived compounds require component bindings")
    for lemma_ref, sense_ref in components:
        if not lemma_ref.strip() or not sense_ref.strip():
            raise DeckError("component bindings require non-blank semantic references")
    selected: tuple[str, ...] | None = None
    if meaning_languages is not None:
        selected = _validate_languages(meaning_languages)
    if user_meanings is not None:
        for language, text in user_meanings.items():
            _validate_language(language)
            if text is not None and not text.strip():
                raise DeckError("user meaning must not be blank")
    return selected


def _matching_null_key_note_ids(
    conn: sqlite3.Connection, target_key: str
) -> list[int]:
    """Return NULL-key rows whose exact candidate identity equals target."""
    matching: list[int] = []
    null_rows = conn.execute(
        "SELECT id, lemma_semantic_ref FROM note WHERE dictionary_key IS NULL"
    ).fetchall()
    for row in null_rows:
        note_id = int(row[0])
        try:
            candidate = _candidate_key_for_stored_note(conn, note_id, str(row[1]))
        except DeckError:
            continue
        if candidate == target_key:
            matching.append(note_id)
    return matching


def _reuse_keyed_or_fail_closed(
    conn: sqlite3.Connection,
    target_key: str,
    *,
    meaning_languages: Sequence[str] | None,
    user_meanings: Mapping[str, str | None] | None,
) -> NoteIdentityResult:
    """UNIQUE-race backstop: re-query the authoritative keyed row."""
    keyed = conn.execute(
        "SELECT id FROM note WHERE dictionary_key = ?", (target_key,)
    ).fetchall()
    if len(keyed) == 1:
        note_id = int(keyed[0][0])
        _apply_capture_overrides(
            conn, note_id, meaning_languages=meaning_languages, user_meanings=user_meanings
        )
        return NoteIdentityResult(note_id, _card_id_for_note_or_raise(conn, note_id), "reused")
    raise DeckError(f"note identity write conflict for {target_key!r}; failing closed")


def _promotable_stub_id(
    conn: sqlite3.Connection,
    lemma_semantic_ref: str,
    user_meanings: Mapping[str, str | None] | None,
) -> int | None:
    """Return the stub eligible for needs_gloss promotion, or None.

    Promotion is permitted only when there is exactly ONE never-bound
    ``needs_gloss`` candidate for the lemma and every conservative
    predicate holds: zero reviews, zero ``review_log`` rows, an
    unambiguous stub identity, and no conflicting incoming user meaning.
    """
    stubs = conn.execute(
        """
        SELECT n.id FROM note n
        WHERE n.status = 'needs_gloss' AND n.lemma_semantic_ref = ?
          AND NOT EXISTS (
            SELECT 1 FROM note_dictionary_binding b WHERE b.note_id = n.id
          )
        """,
        (lemma_semantic_ref,),
    ).fetchall()
    if len(stubs) != 1:
        return None
    stub_id = int(stubs[0][0])
    count_row = conn.execute(
        "SELECT review_count FROM note WHERE id = ?", (stub_id,)
    ).fetchone()
    if count_row is None or int(count_row[0]) != 0:
        return None
    card_row = conn.execute(
        "SELECT id FROM card WHERE note_id = ?", (stub_id,)
    ).fetchone()
    if card_row is None:
        return None
    log_row = conn.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (int(card_row[0]),)
    ).fetchone()
    if log_row is None or int(log_row[0]) != 0:
        return None
    stub_key = needs_gloss_dictionary_key(lemma_semantic_ref)
    keyed_same = conn.execute(
        "SELECT COUNT(*) FROM note WHERE dictionary_key = ?", (stub_key,)
    ).fetchone()
    sharing = int(keyed_same[0]) if keyed_same is not None else 0
    sharing += len(_matching_null_key_note_ids(conn, stub_key))
    if sharing != 1:
        return None
    if user_meanings:
        existing = {
            str(row[0]): str(row[1])
            for row in conn.execute(
                "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?",
                (stub_id,),
            ).fetchall()
        }
        for language, text in user_meanings.items():
            if (
                text is not None
                and language in existing
                and existing[language] != text.strip()
            ):
                return None
    return stub_id


def _promote_stub_to_target(
    conn: sqlite3.Connection,
    note_id: int,
    *,
    status: str,
    lemma_semantic_ref: str,
    sense_semantic_ref: str | None,
    components: tuple[ComponentBinding, ...],
    dictionary_key: str,
) -> None:
    """Rewrite only the semantic-resolution fields of a promoted stub.

    Identity metadata (note/card ids, FSRS fields, scheduling fields,
    memberships, languages, user meanings, custom pronunciation,
    ``created_at``) is preserved; only status/refs/key plus the required
    D47 binding rows change.
    """
    now_text = _timestamp(_utc_now())
    if status == "resolved":
        assert sense_semantic_ref is not None and sense_semantic_ref.strip()
        conn.execute(
            """
            UPDATE note SET status = 'resolved', sense_semantic_ref = ?,
                            dictionary_key = ?
            WHERE id = ?
            """,
            (sense_semantic_ref, dictionary_key, note_id),
        )
        conn.execute(
            """
            INSERT INTO note_dictionary_binding (
                note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
                binding_status, last_relinked_at
            ) VALUES (?, 'direct', 0, ?, ?, 'bound', ?)
            """,
            (note_id, lemma_semantic_ref, sense_semantic_ref, now_text),
        )
    else:
        assert status == "derived_compound" and components
        conn.execute(
            """
            UPDATE note SET status = 'derived_compound', sense_semantic_ref = NULL,
                            dictionary_key = ?
            WHERE id = ?
            """,
            (dictionary_key, note_id),
        )
        conn.executemany(
            """
            INSERT INTO note_dictionary_binding (
                note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
                binding_status, component_count, last_relinked_at
            ) VALUES (?, 'component', ?, ?, ?, 'bound', ?, ?)
            """,
            (
                (note_id, ordinal, lemma_ref, sense_ref, len(components), now_text)
                for ordinal, (lemma_ref, sense_ref) in enumerate(components)
            ),
        )


def find_or_create_note(
    conn: sqlite3.Connection,
    lemma_semantic_ref: str,
    *,
    sense_semantic_ref: str | None = None,
    status: str = "needs_gloss",
    component_bindings: Sequence[ComponentBinding] = (),
    meaning_languages: Sequence[str] | None = None,
    user_meanings: Mapping[str, str | None] | None = None,
    apply_languages_on_reuse: bool = True,
    _manage_transaction: bool = True,
) -> NoteIdentityResult:
    """Create, reuse, claim, or promote exactly one note for an identity.

    The single authoritative domain operation for every product
    note-creation path. The caller supplies already-validated stable refs
    (dictionary identity is never resolved here) and owns the surrounding
    ``BEGIN IMMEDIATE`` transaction when ``_manage_transaction`` is False.
    Deck membership is the caller's responsibility via
    :func:`add_note_to_deck`.
    """
    components = tuple(component_bindings)
    selected = _validate_capture_request(
        lemma_semantic_ref,
        sense_semantic_ref,
        status,
        components,
        meaning_languages,
        user_meanings,
    )
    target_key = target_dictionary_key(
        status, lemma_semantic_ref, sense_semantic_ref, components
    )
    reuse_languages = selected if apply_languages_on_reuse else None

    with _transaction_context(conn, _manage_transaction):
        keyed = conn.execute(
            "SELECT id FROM note WHERE dictionary_key = ?", (target_key,)
        ).fetchall()
        if len(keyed) > 1:
            raise DeckError(f"duplicate keyed identity rows for {target_key!r}")
        if keyed:
            note_id = int(keyed[0][0])
            _apply_capture_overrides(
                conn,
                note_id,
                meaning_languages=reuse_languages,
                user_meanings=user_meanings,
            )
            return NoteIdentityResult(
                note_id, _card_id_for_note_or_raise(conn, note_id), "reused"
            )

        matching = _matching_null_key_note_ids(conn, target_key)
        if len(matching) >= 2:
            raise LegacyDuplicateConflictError(target_key, matching)
        if len(matching) == 1:
            note_id = matching[0]
            claimed = conn.execute(
                "UPDATE note SET dictionary_key = ? WHERE id = ? AND dictionary_key IS NULL",
                (target_key, note_id),
            )
            if claimed.rowcount != 1:
                return _reuse_keyed_or_fail_closed(
                    conn,
                    target_key,
                    meaning_languages=reuse_languages,
                    user_meanings=user_meanings,
                )
            _apply_capture_overrides(
                conn,
                note_id,
                meaning_languages=reuse_languages,
                user_meanings=user_meanings,
            )
            return NoteIdentityResult(
                note_id, _card_id_for_note_or_raise(conn, note_id), "reused"
            )

        if status in ("resolved", "derived_compound"):
            stub_id = _promotable_stub_id(conn, lemma_semantic_ref, user_meanings)
            if stub_id is not None:
                try:
                    _promote_stub_to_target(
                        conn,
                        stub_id,
                        status=status,
                        lemma_semantic_ref=lemma_semantic_ref,
                        sense_semantic_ref=sense_semantic_ref,
                        components=components,
                        dictionary_key=target_key,
                    )
                except sqlite3.IntegrityError:
                    return _reuse_keyed_or_fail_closed(
                        conn,
                        target_key,
                        meaning_languages=None,
                        user_meanings=user_meanings,
                    )
                _apply_capture_overrides(
                    conn, stub_id, meaning_languages=None, user_meanings=user_meanings
                )
                return NoteIdentityResult(
                    stub_id, _card_id_for_note_or_raise(conn, stub_id), "promoted"
                )

        initial_languages = selected if selected is not None else ("de", "en")
        try:
            note_id = create_note(
                conn,
                lemma_semantic_ref,
                sense_semantic_ref=sense_semantic_ref,
                status=status,
                component_bindings=components,
                meaning_languages=initial_languages,
                _manage_transaction=False,
            )
        except sqlite3.IntegrityError:
            return _reuse_keyed_or_fail_closed(
                conn,
                target_key,
                meaning_languages=reuse_languages,
                user_meanings=user_meanings,
            )
        if user_meanings is not None:
            for language, text in user_meanings.items():
                if text is not None:
                    set_user_meaning(
                        conn, note_id, language, text.strip(), _manage_transaction=False
                    )
        return NoteIdentityResult(
            note_id, _card_id_for_note_or_raise(conn, note_id), "created"
        )


@dataclass(frozen=True, slots=True)
class LegacyDuplicateGroup:
    """One ambiguous NULL-key candidate group for later M3 management."""

    candidate_key: str
    kind: str
    note_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LegacyIdentityDiagnostic:
    """Read-only M2 migration report for tests and later M3 work."""

    total_notes: int
    duplicate_resolved_groups: int
    duplicate_needs_gloss_groups: int
    duplicate_derived_groups: int
    cross_status_groups: int
    groups_with_review_history: int
    groups_with_conflicting_user_meanings: int
    groups_with_multiple_custom_audio: int
    null_key_note_count: int
    duplicate_groups: tuple[LegacyDuplicateGroup, ...]


def _diagnostic_kind(candidate_key: str) -> str:
    if candidate_key.startswith(f"resolved:{M2_IDENTITY_SCHEME_VERSION}:"):
        return "resolved"
    if candidate_key.startswith(f"needs_gloss:{M2_IDENTITY_SCHEME_VERSION}:"):
        return "needs_gloss"
    if candidate_key.startswith(f"derived:{M2_IDENTITY_SCHEME_VERSION}:"):
        return "derived"
    return "unknown"


def legacy_identity_diagnostic(conn: sqlite3.Connection) -> LegacyIdentityDiagnostic:
    """Report legacy NULL-key duplicate state without mutating the database.

    SELECT-only: no writes, no dictionary access, no telemetry.
    """
    total_row = conn.execute("SELECT COUNT(*) FROM note").fetchone()
    total_notes = int(total_row[0]) if total_row is not None else 0
    null_rows = conn.execute(
        "SELECT id, lemma_semantic_ref FROM note WHERE dictionary_key IS NULL"
    ).fetchall()
    null_key_note_count = len(null_rows)

    by_candidate: dict[str, list[int]] = {}
    lemma_of: dict[int, str] = {}
    for row in null_rows:
        note_id = int(row[0])
        lemma_ref = str(row[1])
        lemma_of[note_id] = lemma_ref
        try:
            candidate = _candidate_key_for_stored_note(conn, note_id, lemma_ref)
        except DeckError:
            continue
        by_candidate.setdefault(candidate, []).append(note_id)

    duplicate_groups = tuple(
        LegacyDuplicateGroup(
            candidate_key=candidate,
            kind=_diagnostic_kind(candidate),
            note_ids=tuple(sorted(note_ids)),
        )
        for candidate, note_ids in sorted(by_candidate.items())
        if len(note_ids) >= 2
    )

    member_ids = sorted({nid for group in duplicate_groups for nid in group.note_ids})
    review_counts = (
        {
            int(row[0]): int(row[1])
            for row in conn.execute("SELECT id, review_count FROM note").fetchall()
        }
        if member_ids
        else {}
    )
    card_of_note = (
        {
            int(row[0]): int(row[1])
            for row in conn.execute("SELECT note_id, id FROM card").fetchall()
        }
        if member_ids
        else {}
    )
    cards_with_history = (
        {
            int(row[0])
            for row in conn.execute(
                "SELECT DISTINCT card_id FROM review_log"
            ).fetchall()
        }
        if member_ids
        else set()
    )
    meanings: dict[int, dict[str, str]] = {}
    if member_ids:
        meaning_rows = conn.execute(
            "SELECT note_id, lang, meaning_text FROM note_user_meaning"
        ).fetchall()
        for row in meaning_rows:
            if int(row[0]) in review_counts:
                meanings.setdefault(int(row[0]), {})[str(row[1])] = str(row[2])
    audio_notes = (
        {
            int(row[0])
            for row in conn.execute("SELECT note_id FROM custom_pronunciation").fetchall()
        }
        if member_ids
        else set()
    )

    groups_with_review_history = 0
    groups_with_conflicting_user_meanings = 0
    groups_with_multiple_custom_audio = 0
    for group in duplicate_groups:
        if any(
            review_counts.get(nid, 0) > 0
            or card_of_note.get(nid) in cards_with_history
            for nid in group.note_ids
        ):
            groups_with_review_history += 1
        meaning_maps = {
            tuple(sorted(meanings.get(nid, {}).items())) for nid in group.note_ids
        }
        if len(meaning_maps) > 1:
            groups_with_conflicting_user_meanings += 1
        if sum(1 for nid in group.note_ids if nid in audio_notes) >= 2:
            groups_with_multiple_custom_audio += 1

    by_lemma: dict[str, set[str]] = {}
    for row in null_rows:
        note_id = int(row[0])
        try:
            candidate = _candidate_key_for_stored_note(conn, note_id, str(row[1]))
        except DeckError:
            continue
        by_lemma.setdefault(str(row[1]), set()).add(candidate)
    cross_status_groups = sum(1 for candidates in by_lemma.values() if len(candidates) >= 2)

    return LegacyIdentityDiagnostic(
        total_notes=total_notes,
        duplicate_resolved_groups=sum(1 for g in duplicate_groups if g.kind == "resolved"),
        duplicate_needs_gloss_groups=sum(
            1 for g in duplicate_groups if g.kind == "needs_gloss"
        ),
        duplicate_derived_groups=sum(1 for g in duplicate_groups if g.kind == "derived"),
        cross_status_groups=cross_status_groups,
        groups_with_review_history=groups_with_review_history,
        groups_with_conflicting_user_meanings=groups_with_conflicting_user_meanings,
        groups_with_multiple_custom_audio=groups_with_multiple_custom_audio,
        null_key_note_count=null_key_note_count,
        duplicate_groups=duplicate_groups,
    )


def _orphan_deck_id(conn: sqlite3.Connection, timestamp: str) -> int:
    row = conn.execute("SELECT id FROM deck WHERE name = ?", (ORPHANED_DECK_NAME,)).fetchone()
    if row is not None:
        return int(row[0])
    return _last_insert_id(
        conn.execute(
            "INSERT INTO deck (name, created_at) VALUES (?, ?)",
            (ORPHANED_DECK_NAME, timestamp),
        )
    )


def _orphan_deck_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Return the protected ``Orphaned`` deck row, or ``None`` if absent."""
    return cast(
        sqlite3.Row | None,
        conn.execute(
            "SELECT id, name, created_at FROM deck WHERE name = ?",
            (ORPHANED_DECK_NAME,),
        ).fetchone(),
    )


# ---------------------------------------------------------------------------
# M5 — orphan restoration: status derivation + transactional recovery.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestoreOrphanedNoteResult:
    """Authoritative result of :func:`restore_orphaned_note`."""

    note_id: int
    card_id: int
    from_deck_id: int
    to_deck_id: int
    previous_status: str
    restored_status: str


_RESTORED_STATUSES: frozenset[str] = frozenset(
    {"resolved", "needs_gloss", "derived_compound"}
)


def _derive_restored_status(
    conn: sqlite3.Connection,
    note_id: int,
    *,
    dictionary_key: str | None,
    lemma_semantic_ref: str,
    sense_semantic_ref: str | None,
) -> str:
    """Derive the correct CURRENT non-Orphaned ``note.status`` from durable bindings.

    The helper distinguishes *semantic unavailability* — a structurally
    valid binding that the active dictionary simply no longer recognises
    — from *structurally corrupt persistent state* — direct/component
    mixing, malformed ordinals, inconsistent ``component_count``,
    multiple direct rows, or non-NULL ``dictionary_key`` that disagrees
    with the binding-derived identity. The latter is never silently
    re-mapped to ``needs_gloss``; it raises :class:`InconsistentOrphanStateError`.
    """
    direct_rows = conn.execute(
        """
        SELECT component_ord, lemma_semantic_ref, sense_semantic_ref, binding_status,
               component_count
        FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'direct'
        ORDER BY component_ord ASC
        """,
        (note_id,),
    ).fetchall()
    component_rows = conn.execute(
        """
        SELECT component_ord, lemma_semantic_ref, sense_semantic_ref, binding_status,
               component_count
        FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'component'
        ORDER BY component_ord ASC
        """,
        (note_id,),
    ).fetchall()

    has_direct = bool(direct_rows)
    has_components = bool(component_rows)

    if has_direct and has_components:
        raise InconsistentOrphanStateError(
            f"note {note_id} has both direct and component bindings; orphan state is malformed"
        )
    if not has_direct and not has_components:
        restored_status = "needs_gloss"
    elif has_direct:
        if len(direct_rows) != 1:
            raise InconsistentOrphanStateError(
                f"note {note_id} has {len(direct_rows)} direct binding rows; expected exactly 1"
            )
        row = direct_rows[0]
        if int(row["component_ord"]) != 0:
            raise InconsistentOrphanStateError(
                f"note {note_id} direct binding has malformed ordinal {int(row['component_ord'])}"
            )
        if row["component_count"] is not None:
            raise InconsistentOrphanStateError(
                f"note {note_id} direct binding has non-NULL component_count"
            )
        direct_sense_ref = str(row["sense_semantic_ref"])
        direct_lemma_ref = str(row["lemma_semantic_ref"])
        if sense_semantic_ref is None or direct_sense_ref != sense_semantic_ref:
            raise InconsistentOrphanStateError(
                f"note {note_id} direct binding sense_ref disagrees with durable note identity"
            )
        if direct_lemma_ref != lemma_semantic_ref:
            raise InconsistentOrphanStateError(
                f"note {note_id} direct binding lemma_ref disagrees with durable note identity"
            )
        if str(row["binding_status"]) == "bound":
            restored_status = "resolved"
        else:
            restored_status = "needs_gloss"
    else:
        # has_components == True and has_direct == False by the mixed-state guard above
        ordinals = [int(row["component_ord"]) for row in component_rows]
        counts = [row["component_count"] for row in component_rows]
        if any(
            (not isinstance(c, int) or isinstance(c, bool) or c <= 0)
            for c in counts
        ):
            raise InconsistentOrphanStateError(
                f"note {note_id} component binding has malformed component_count"
            )
        first_count = counts[0]
        if any(c != first_count for c in counts):
            raise InconsistentOrphanStateError(
                f"note {note_id} component bindings disagree on component_count"
            )
        expected_count = int(first_count)
        if len(component_rows) != expected_count:
            raise InconsistentOrphanStateError(
                f"note {note_id} component vector length {len(component_rows)} "
                f"disagrees with declared component_count {expected_count}"
            )
        if ordinals != list(range(expected_count)):
            raise InconsistentOrphanStateError(
                f"note {note_id} component ordinals {ordinals} are not "
                f"contiguous 0..{expected_count - 1}"
            )
        statuses = [str(row["binding_status"]) for row in component_rows]
        if all(status == "bound" for status in statuses):
            restored_status = "derived_compound"
        else:
            restored_status = "needs_gloss"

    if dictionary_key is not None:
        try:
            candidate = _candidate_key_for_stored_note(conn, note_id, lemma_semantic_ref)
        except DeckError as exc:
            raise InconsistentOrphanStateError(
                f"note {note_id} durable dictionary_key cannot be reconciled with bindings: {exc}"
            ) from exc
        if candidate != dictionary_key:
            raise InconsistentOrphanStateError(
                f"note {note_id} dictionary_key disagrees with binding-derived identity"
            )

    if restored_status not in _RESTORED_STATUSES:
        raise InconsistentOrphanStateError(
            f"note {note_id} derived status {restored_status!r} is not a valid restored status"
        )
    return restored_status


def restore_orphaned_note(
    conn: sqlite3.Connection,
    note_id: int,
    destination_deck_id: int,
    *,
    now: datetime | None = None,
) -> RestoreOrphanedNoteResult:
    """Atomically recover one orphaned note into a normal deck.

    The operation owns one ``BEGIN IMMEDIATE`` transaction and is the
    sole mutating path for ``note.status`` when the source membership is
    the protected ``Orphaned`` deck. It only acts on the canonical valid
    orphan invariant:

        note.status == 'orphaned'
        AND exactly one note_deck membership
        AND that membership deck == protected 'Orphaned' deck

    Any deviation raises :class:`InconsistentOrphanStateError` and writes
    nothing. Allowed writes are exactly one destination ``note_deck``
    INSERT, one ``note.status`` UPDATE, and one Orphaned ``note_deck``
    DELETE. No other table is touched: ``note.id``, ``note.dictionary_key``,
    ``note.created_at``, the durable semantic refs, every ``note_dictionary_binding``
    row, the card row, the FSRS state, ``review_log``, language selections,
    user meanings, and custom pronunciation are preserved verbatim.

    Repeating a successful restore returns :class:`NoteNotOrphanedError`
    (the note is no longer orphaned); this is intentional and strictly
    one-shot.
    """
    timestamp = _timestamp(_as_utc(now))
    try:
        conn.execute("BEGIN IMMEDIATE")

        dest_row = conn.execute(
            "SELECT id, name FROM deck WHERE id = ?", (destination_deck_id,)
        ).fetchone()
        if dest_row is None:
            raise DeckNotFoundError(f"deck {destination_deck_id} not found")
        if str(dest_row[1]) == ORPHANED_DECK_NAME:
            raise OrphanedDeckProtectedError(
                f"deck {destination_deck_id} is the protected '{ORPHANED_DECK_NAME}' fallback deck"
            )

        note_row = conn.execute(
            """
            SELECT id, lemma_semantic_ref, sense_semantic_ref, status, dictionary_key
            FROM note WHERE id = ?
            """,
            (note_id,),
        ).fetchone()
        if note_row is None:
            raise NoteNotFoundError(f"note {note_id} not found")
        if str(note_row["status"]) != "orphaned":
            raise NoteNotOrphanedError(
                f"note {note_id} is not orphaned (status={note_row['status']!r})"
            )

        orphan_row = _orphan_deck_row(conn)
        if orphan_row is None:
            raise InconsistentOrphanStateError(
                "protected 'Orphaned' deck row is missing from persistent state"
            )
        orphan_deck_id = int(orphan_row["id"])

        membership_rows = conn.execute(
            "SELECT deck_id FROM note_deck WHERE note_id = ? ORDER BY deck_id ASC",
            (note_id,),
        ).fetchall()
        membership_deck_ids = [int(row[0]) for row in membership_rows]
        if (
            len(membership_deck_ids) != 1
            or membership_deck_ids[0] != orphan_deck_id
        ):
            raise InconsistentOrphanStateError(
                f"note {note_id} orphan invariant violated: memberships={membership_deck_ids}"
            )

        restored_status = _derive_restored_status(
            conn,
            note_id,
            dictionary_key=(
                str(note_row["dictionary_key"])
                if note_row["dictionary_key"] is not None
                else None
            ),
            lemma_semantic_ref=str(note_row["lemma_semantic_ref"]),
            sense_semantic_ref=(
                str(note_row["sense_semantic_ref"])
                if note_row["sense_semantic_ref"] is not None
                else None
            ),
        )

        card_row = conn.execute(
            "SELECT id FROM card WHERE note_id = ?", (note_id,)
        ).fetchone()
        if card_row is None:
            raise InconsistentOrphanStateError(
                f"note {note_id} has no card row; persistent state is malformed"
            )
        card_id = int(card_row[0])

        previous_status = "orphaned"

        insert_cursor = conn.execute(
            """
            INSERT INTO note_deck (note_id, deck_id, created_at)
            VALUES (?, ?, ?)
            """,
            (note_id, destination_deck_id, timestamp),
        )
        if insert_cursor.rowcount != 1:
            raise InconsistentOrphanStateError(
                f"destination membership insert for note {note_id} "
                f"produced rowcount={insert_cursor.rowcount}"
            )

        update_cursor = conn.execute(
            "UPDATE note SET status = ? WHERE id = ?",
            (restored_status, note_id),
        )
        if update_cursor.rowcount != 1:
            raise InconsistentOrphanStateError(
                f"note {note_id} status update produced rowcount={update_cursor.rowcount}"
            )

        delete_cursor = conn.execute(
            """
            DELETE FROM note_deck
            WHERE note_id = ? AND deck_id = ?
            """,
            (note_id, orphan_deck_id),
        )
        if delete_cursor.rowcount != 1:
            raise InconsistentOrphanStateError(
                f"orphaned membership delete for note {note_id} "
                f"produced rowcount={delete_cursor.rowcount}"
            )

        verify_rows = conn.execute(
            "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
        ).fetchall()
        if [int(row[0]) for row in verify_rows] != [int(destination_deck_id)]:
            raise InconsistentOrphanStateError(
                f"note {note_id} post-restore membership invariant failed: "
                f"{[int(r[0]) for r in verify_rows]}"
            )
        verify_status = conn.execute(
            "SELECT status FROM note WHERE id = ?", (note_id,)
        ).fetchone()
        if (
            verify_status is None
            or str(verify_status["status"]) != restored_status
        ):
            raise InconsistentOrphanStateError(
                f"note {note_id} post-restore status invariant failed"
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return RestoreOrphanedNoteResult(
        note_id=int(note_id),
        card_id=card_id,
        from_deck_id=int(orphan_deck_id),
        to_deck_id=int(destination_deck_id),
        previous_status=previous_status,
        restored_status=restored_status,
    )


def delete_deck(conn: sqlite3.Connection, deck_id: int, *, now: datetime | None = None) -> None:
    """Remove a deck and place every newly membership-less note in Orphaned.

    A note is never deleted here. Review history therefore survives, and does
    not decide whether an otherwise orphaned note gets its required membership.

    Refuses to delete the protected ``Orphaned`` deck; doing so would
    allow a later ``remove_note_from_deck`` to silently re-create it,
    hiding the integrity hole this contract closes.
    """
    timestamp = _timestamp(_as_utc(now))
    try:
        conn.execute("BEGIN IMMEDIATE")
        target_row = conn.execute(
            "SELECT id, name FROM deck WHERE id = ?", (deck_id,)
        ).fetchone()
        if target_row is None:
            raise DeckNotFoundError(f"deck {deck_id} not found")
        if str(target_row[1]) == ORPHANED_DECK_NAME:
            raise OrphanedDeckProtectedError(
                f"deck {deck_id} is the protected '{ORPHANED_DECK_NAME}' fallback deck"
            )
        note_rows = conn.execute(
            "SELECT note_id FROM note_deck WHERE deck_id = ?", (deck_id,)
        ).fetchall()
        conn.execute("DELETE FROM deck WHERE id = ?", (deck_id,))
        newly_orphaned = [
            int(row[0])
            for row in note_rows
            if conn.execute(
                "SELECT 1 FROM note_deck WHERE note_id = ? LIMIT 1", (int(row[0]),)
            ).fetchone()
            is None
        ]
        if newly_orphaned:
            orphan_id = _orphan_deck_id(conn, timestamp)
            for note_id in newly_orphaned:
                conn.execute("UPDATE note SET status = 'orphaned' WHERE id = ?", (note_id,))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO note_deck (note_id, deck_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (note_id, orphan_id, timestamp),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# M6 — selected-sense editing (D47 rebind under stable semantic refs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeSelectedSenseResult:
    """Authoritative result of :func:`change_note_selected_sense`."""

    note_id: int
    card_id: int
    lemma_semantic_ref: str
    previous_sense_semantic_ref: str | None
    sense_semantic_ref: str
    dictionary_key: str
    status: str


def _validate_resolved_direct_identity(
    conn: sqlite3.Connection,
    note_id: int,
    *,
    note_lemma_ref: str,
    note_sense_ref: str,
) -> tuple[str, str]:
    """Return the (lemma_ref, sense_ref) of the direct binding for a resolved note.

    Raise :class:`InconsistentNoteIdentityError` on any structural defect
    that would mean the persisted bindings disagree with the note's
    durable refs. Used by M6 to refuse to silently repair malformed
    semantic identity.
    """
    direct_rows = conn.execute(
        """
        SELECT component_ord, lemma_semantic_ref, sense_semantic_ref, binding_status,
               component_count
        FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'direct'
        ORDER BY component_ord ASC
        """,
        (note_id,),
    ).fetchall()
    component_rows = conn.execute(
        """
        SELECT 1 FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'component'
        LIMIT 1
        """,
        (note_id,),
    ).fetchall()
    if component_rows:
        raise InconsistentNoteIdentityError(
            f"note {note_id} is resolved but has component bindings; identity is malformed"
        )
    if len(direct_rows) != 1:
        raise InconsistentNoteIdentityError(
            f"note {note_id} has {len(direct_rows)} direct binding rows; expected exactly 1"
        )
    row = direct_rows[0]
    if int(row["component_ord"]) != 0:
        raise InconsistentNoteIdentityError(
            f"note {note_id} direct binding has malformed ordinal {int(row['component_ord'])}"
        )
    if row["component_count"] is not None:
        raise InconsistentNoteIdentityError(
            f"note {note_id} direct binding has non-NULL component_count"
        )
    direct_lemma_ref = str(row["lemma_semantic_ref"])
    direct_sense_ref = str(row["sense_semantic_ref"])
    if direct_lemma_ref != note_lemma_ref:
        raise InconsistentNoteIdentityError(
            f"note {note_id} direct binding lemma_ref disagrees with note durable lemma"
        )
    if direct_sense_ref != note_sense_ref:
        raise InconsistentNoteIdentityError(
            f"note {note_id} direct binding sense_ref disagrees with note durable sense"
        )
    # The contract does not require ``binding_status == 'bound'``: a
    # legacy NULL-key row may carry an ``unbound`` direct binding whose
    # durable refs nevertheless agree with the note. M6 refuses only
    # structurally inconsistent identity, not dictionary coverage.
    return direct_lemma_ref, direct_sense_ref


def _validate_durable_dictionary_key(
    conn: sqlite3.Connection,
    note_id: int,
    *,
    lemma_ref: str,
    dictionary_key: str | None,
) -> None:
    """Refuse a non-NULL ``dictionary_key`` that disagrees with the bindings."""
    if dictionary_key is None:
        return
    candidate = _candidate_key_for_stored_note(conn, note_id, lemma_ref)
    if candidate != dictionary_key:
        raise InconsistentNoteIdentityError(
            f"note {note_id} dictionary_key disagrees with binding-derived identity"
        )


def change_note_selected_sense(
    conn: sqlite3.Connection,
    note_id: int,
    target_sense_semantic_ref: str,
) -> ChangeSelectedSenseResult:
    """Atomically rebind an existing note to a different selected sense.

    The operation owns one ``BEGIN IMMEDIATE`` transaction and never
    merges two notes. Supported transitions are exactly::

        resolved A    -> resolved B
        needs_gloss   -> resolved B     (only when M2 promotion gates accept it)

    ``derived_compound``, ``orphaned``, and ``resolved -> needs_gloss``
    are rejected as ``unsupported_selected_sense_edit``. The cross-lemma
    case (a different durable ``lemma_semantic_ref``) is the API
    layer's responsibility: this function expects
    ``target_sense_semantic_ref`` to belong to the note's own lemma.

    Successful rebind preserves ``note.id``, ``note.lemma_semantic_ref``,
    ``note.created_at``, every ``card`` row and FSRS field, every
    ``review_log`` row, every ``note_deck`` row, every ``note_meaning_lang``
    row, every ``note_user_meaning`` row, every ``folder`` /
    ``deck.folder_id`` row, ``active_dictionary_metadata``, and
    ``custom_pronunciation``. Only the selected-sense identity fields
    change: ``note.sense_semantic_ref``, ``note.dictionary_key``,
    ``note.status`` (only when ``needs_gloss -> resolved``), and the
    single ``note_dictionary_binding`` direct row.
    """
    if not target_sense_semantic_ref.strip():
        raise DeckError("target sense semantic reference must not be blank")

    timestamp = _timestamp(_utc_now())
    try:
        conn.execute("BEGIN IMMEDIATE")

        note_row = conn.execute(
            """
            SELECT id, lemma_semantic_ref, sense_semantic_ref, status, dictionary_key
            FROM note WHERE id = ?
            """,
            (note_id,),
        ).fetchone()
        if note_row is None:
            raise NoteNotFoundError(f"note {note_id} not found")

        note_lemma_ref = str(note_row["lemma_semantic_ref"])
        previous_sense_ref = (
            str(note_row["sense_semantic_ref"])
            if note_row["sense_semantic_ref"] is not None
            else None
        )
        existing_dictionary_key = (
            str(note_row["dictionary_key"])
            if note_row["dictionary_key"] is not None
            else None
        )
        current_status = str(note_row["status"])

        if current_status in ("derived_compound", "orphaned"):
            raise UnsupportedSelectedSenseEditError(
                f"note {note_id} status {current_status!r} does not support selected-sense editing"
            )
        if current_status not in ("resolved", "needs_gloss"):
            raise UnsupportedSelectedSenseEditError(
                f"note {note_id} status {current_status!r} is not editable"
            )

        target_dictionary_key = resolved_dictionary_key(target_sense_semantic_ref)

        if current_status == "resolved":
            _validate_resolved_direct_identity(
                conn,
                note_id,
                note_lemma_ref=note_lemma_ref,
                note_sense_ref=(
                    previous_sense_ref
                    if previous_sense_ref is not None
                    else ""
                ),
            )
            _validate_durable_dictionary_key(
                conn,
                note_id,
                lemma_ref=note_lemma_ref,
                dictionary_key=existing_dictionary_key,
            )

        # Keyed-owner collision: another note already owns the target key.
        keyed_rows = conn.execute(
            "SELECT id FROM note WHERE dictionary_key = ?", (target_dictionary_key,)
        ).fetchall()
        keyed_other = [
            int(row[0]) for row in keyed_rows if int(row[0]) != note_id
        ]
        if keyed_other:
            raise SelectedSenseConflictError(
                target_dictionary_key, int(keyed_other[0])
            )

        # Legacy NULL-key collision: another row's candidate identity
        # computes to the target key. The edited note itself MAY acquire
        # the target key as part of a successful rebind — that case is
        # not a conflict and is handled below.
        legacy_matches = [
            nid
            for nid in _matching_null_key_note_ids(conn, target_dictionary_key)
            if nid != note_id
        ]
        if legacy_matches:
            raise LegacyDuplicateConflictError(
                target_dictionary_key, legacy_matches
            )

        # Same-sense no-op: identity is structurally coherent and the
        # requested target is already the selected sense. Idempotent
        # success without rewriting any row, without touching
        # ``last_relinked_at``, and without ``created_at``.
        if (
            current_status == "resolved"
            and previous_sense_ref == target_sense_semantic_ref
        ):
            card_id = int(
                conn.execute(
                    "SELECT id FROM card WHERE note_id = ?", (note_id,)
                ).fetchone()[0]
            )
            conn.commit()
            return ChangeSelectedSenseResult(
                note_id=int(note_id),
                card_id=card_id,
                lemma_semantic_ref=note_lemma_ref,
                previous_sense_semantic_ref=previous_sense_ref,
                sense_semantic_ref=target_sense_semantic_ref,
                dictionary_key=(
                    existing_dictionary_key
                    if existing_dictionary_key is not None
                    else target_dictionary_key
                ),
                status="resolved",
            )

        if current_status == "resolved":
            # Remove the old direct binding and write the new one inside
            # the same transaction. ``note.sense_semantic_ref`` and
            # ``note.dictionary_key`` are rewritten in lock-step so the
            # persisted identity stays coherent: there is never a row
            # where the note identifies sense B but the binding
            # identifies sense A.
            conn.execute(
                """
                DELETE FROM note_dictionary_binding
                WHERE note_id = ? AND role = 'direct' AND component_ord = 0
                """,
                (note_id,),
            )
            conn.execute(
                """
                INSERT INTO note_dictionary_binding (
                    note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
                    binding_status, last_relinked_at
                ) VALUES (?, 'direct', 0, ?, ?, 'bound', ?)
                """,
                (note_id, note_lemma_ref, target_sense_semantic_ref, timestamp),
            )
            try:
                conn.execute(
                    """
                    UPDATE note
                    SET sense_semantic_ref = ?, dictionary_key = ?
                    WHERE id = ?
                    """,
                    (target_sense_semantic_ref, target_dictionary_key, note_id),
                )
            except sqlite3.IntegrityError:
                # UNIQUE backstop fired after we passed the in-memory
                # collision checks: another note captured the target
                # identity between the check and the write (resolved ->
                # resolved race). Re-query and surface a clean conflict
                # so the caller never sees a raw ``sqlite3.IntegrityError``
                # (and the API never maps it to HTTP 500). When no other
                # note owns the target key the failure is unrelated and
                # the original error is re-raised unchanged.
                owner_row = conn.execute(
                    "SELECT id FROM note WHERE dictionary_key = ?",
                    (target_dictionary_key,),
                ).fetchone()
                if owner_row is not None and int(owner_row[0]) != note_id:
                    raise SelectedSenseConflictError(
                        target_dictionary_key, int(owner_row[0])
                    )
                raise
        else:
            # ``needs_gloss -> resolved``: the same M2 promotion
            # contract that guards new captures also gates M6. We do
            # not weaken M2 merely to let the rebind succeed.
            promotable = _promotable_stub_id(conn, note_lemma_ref, None)
            if promotable is None or promotable != note_id:
                raise PromotionGatesFailedError(
                    f"note {note_id} is not a promotable never-bound "
                    f"needs_gloss stub for lemma {note_lemma_ref!r}"
                )
            try:
                _promote_stub_to_target(
                    conn,
                    note_id,
                    status="resolved",
                    lemma_semantic_ref=note_lemma_ref,
                    sense_semantic_ref=target_sense_semantic_ref,
                    components=(),
                    dictionary_key=target_dictionary_key,
                )
            except sqlite3.IntegrityError:
                # UNIQUE backstop fired after we passed the in-memory
                # collision checks: the dictionary already has another
                # note whose keyed identity was inserted between the
                # check and the write. Re-query and surface a clean
                # conflict so the caller never sees a raw
                # ``sqlite3.IntegrityError``.
                owner_row = conn.execute(
                    "SELECT id FROM note WHERE dictionary_key = ?",
                    (target_dictionary_key,),
                ).fetchone()
                owner_id = int(owner_row[0]) if owner_row is not None else note_id
                raise SelectedSenseConflictError(
                    target_dictionary_key, owner_id
                )

        # Postconditions: exactly one coherent direct binding for the
        # note, no component bindings, and (when ``dictionary_key`` is
        # required to agree) the persisted key still matches.
        post_direct = conn.execute(
            """
            SELECT component_ord, lemma_semantic_ref, sense_semantic_ref,
                   binding_status, component_count
            FROM note_dictionary_binding
            WHERE note_id = ? AND role = 'direct'
            ORDER BY component_ord ASC
            """,
            (note_id,),
        ).fetchall()
        post_component = conn.execute(
            "SELECT 1 FROM note_dictionary_binding WHERE note_id = ? AND role = 'component'",
            (note_id,),
        ).fetchall()
        if len(post_direct) != 1 or int(post_direct[0]["component_ord"]) != 0:
            raise InconsistentNoteIdentityError(
                f"note {note_id} post-rebind direct binding invariant failed"
            )
        if post_direct[0]["component_count"] is not None:
            raise InconsistentNoteIdentityError(
                f"note {note_id} post-rebind direct binding has non-NULL component_count"
            )
        if str(post_direct[0]["binding_status"]) != "bound":
            raise InconsistentNoteIdentityError(
                f"note {note_id} post-rebind direct binding is not 'bound'"
            )
        if post_component:
            raise InconsistentNoteIdentityError(
                f"note {note_id} post-rebind unexpectedly has component bindings"
            )
        if (
            str(post_direct[0]["lemma_semantic_ref"]) != note_lemma_ref
            or str(post_direct[0]["sense_semantic_ref"]) != target_sense_semantic_ref
        ):
            raise InconsistentNoteIdentityError(
                f"note {note_id} post-rebind binding identity disagrees with target"
            )

        card_row = conn.execute(
            "SELECT id FROM card WHERE note_id = ?", (note_id,)
        ).fetchone()
        if card_row is None:
            raise InconsistentNoteIdentityError(
                f"note {note_id} post-rebind has no card row"
            )
        card_id = int(card_row[0])

        post_status = conn.execute(
            "SELECT status FROM note WHERE id = ?", (note_id,)
        ).fetchone()
        if post_status is None or str(post_status["status"]) != "resolved":
            raise InconsistentNoteIdentityError(
                f"note {note_id} post-rebind status is not 'resolved'"
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return ChangeSelectedSenseResult(
        note_id=int(note_id),
        card_id=card_id,
        lemma_semantic_ref=note_lemma_ref,
        previous_sense_semantic_ref=previous_sense_ref,
        sense_semantic_ref=target_sense_semantic_ref,
        dictionary_key=target_dictionary_key,
        status="resolved",
    )


# ---------------------------------------------------------------------------
# M1 — deck/card management contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeckIdentity:
    """Authoritative deck identity projection for management endpoints."""

    id: int
    name: str
    created_at: str
    folder_id: int | None


@dataclass(frozen=True, slots=True)
class FolderIdentity:
    """Authoritative folder identity projection for management endpoints."""

    id: int
    name: str
    created_at: str


@dataclass(frozen=True, slots=True)
class DeckCardRow:
    """One membership row in :func:`list_deck_cards`.

    Dictionary-derived ``headword`` / ``pos`` / ``gender`` fields default
    to safe PART-B-only fallbacks here; the API layer fills them in
    through the active provider session.
    """

    card_id: int
    note_id: int
    lemma_semantic_ref: str
    sense_semantic_ref: str | None
    status: str
    due_at: str
    state: int
    review_count: int
    last_confidence: int | None
    selected_languages: tuple[str, ...]
    user_meanings: Mapping[str, str]
    has_custom_audio: bool
    other_deck_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DeckCardsListing:
    """Result of :func:`list_deck_cards`: deck identity plus membership rows."""

    deck: DeckIdentity
    cards: tuple[DeckCardRow, ...]


def _deck_row_or_raise(conn: sqlite3.Connection, deck_id: int) -> DeckIdentity:
    row = conn.execute(
        "SELECT id, name, created_at, folder_id FROM deck WHERE id = ?", (deck_id,)
    ).fetchone()
    if row is None:
        raise DeckNotFoundError(f"deck {deck_id} not found")
    return DeckIdentity(
        id=int(row[0]),
        name=str(row[1]),
        created_at=str(row[2]),
        folder_id=int(row[3]) if row[3] is not None else None,
    )


def _folder_row_or_raise(conn: sqlite3.Connection, folder_id: int) -> FolderIdentity:
    row = conn.execute(
        "SELECT id, name, created_at FROM folder WHERE id = ?", (folder_id,)
    ).fetchone()
    if row is None:
        raise FolderNotFoundError(f"folder {folder_id} not found")
    return FolderIdentity(id=int(row[0]), name=str(row[1]), created_at=str(row[2]))


# ---------------------------------------------------------------------------
# M4 — folder persistence and deck assignment
# ---------------------------------------------------------------------------


def list_folders(conn: sqlite3.Connection) -> list[FolderIdentity]:
    """Return every folder identity in deterministic ``id`` order."""
    rows = conn.execute(
        "SELECT id, name, created_at FROM folder ORDER BY id ASC"
    ).fetchall()
    return [
        FolderIdentity(id=int(row[0]), name=str(row[1]), created_at=str(row[2]))
        for row in rows
    ]


def create_folder(
    conn: sqlite3.Connection,
    name: str,
    *,
    created_at: datetime | None = None,
    _manage_transaction: bool = True,
) -> FolderIdentity:
    """Create a folder and return its authoritative identity.

    ``name`` is trimmed; a blank name is rejected. Case is preserved and
    exact SQLite ``UNIQUE`` semantics apply, so a duplicate name raises
    :class:`FolderConflictError`.
    """
    cleaned = name.strip()
    if not cleaned:
        raise DeckError("folder name must not be blank")
    now_text = _timestamp(_as_utc(created_at))
    with _transaction_context(conn, _manage_transaction):
        try:
            cursor = conn.execute(
                "INSERT INTO folder (name, created_at) VALUES (?, ?)",
                (cleaned, now_text),
            )
        except sqlite3.IntegrityError as exc:
            raise FolderConflictError(
                f"folder name {cleaned!r} already exists"
            ) from exc
        folder_id = _last_insert_id(cursor)
        return _folder_row_or_raise(conn, folder_id)


def rename_folder(
    conn: sqlite3.Connection,
    folder_id: int,
    new_name: str,
    *,
    _manage_transaction: bool = True,
) -> FolderIdentity:
    """Rename ``folder_id`` preserving its id and ``created_at``.

    Raises :class:`FolderNotFoundError` for a missing folder, rejects a
    blank name, and raises :class:`FolderConflictError` on a duplicate.
    """
    cleaned = new_name.strip()
    if not cleaned:
        raise DeckError("folder name must not be blank")
    with _transaction_context(conn, _manage_transaction):
        _folder_row_or_raise(conn, folder_id)
        try:
            conn.execute(
                "UPDATE folder SET name = ? WHERE id = ?", (cleaned, folder_id)
            )
        except sqlite3.IntegrityError as exc:
            raise FolderConflictError(
                f"folder name {cleaned!r} already exists"
            ) from exc
        return _folder_row_or_raise(conn, folder_id)


def delete_folder(
    conn: sqlite3.Connection,
    folder_id: int,
    *,
    _manage_transaction: bool = True,
) -> None:
    """Delete a folder after explicitly unassigning every member deck.

    Deck rows are never deleted: ``deck.folder_id`` is set to NULL first,
    then the folder row is removed. The FK ``ON DELETE SET NULL`` remains as
    a structural backstop. Raises :class:`FolderNotFoundError` when missing.
    """
    if not _manage_transaction:
        _delete_folder_rows(conn, folder_id)
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        _delete_folder_rows(conn, folder_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _delete_folder_rows(conn: sqlite3.Connection, folder_id: int) -> None:
    _folder_row_or_raise(conn, folder_id)
    conn.execute("UPDATE deck SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
    conn.execute("DELETE FROM folder WHERE id = ?", (folder_id,))


def assign_deck_folder(
    conn: sqlite3.Connection,
    deck_id: int,
    folder_id: int | None,
    *,
    _manage_transaction: bool = True,
) -> DeckIdentity:
    """Assign or clear a deck's folder, returning the authoritative identity.

    ``folder_id=None`` unassigns the deck. A non-NULL folder must exist.
    The protected ``Orphaned`` deck is a recovery system deck and is never
    assignable; the attempt raises :class:`OrphanedDeckProtectedError` and
    leaves its ``folder_id`` NULL. No membership, note, card, review,
    scheduling, or dictionary-binding state is touched.
    """
    with _transaction_context(conn, _manage_transaction):
        deck_row = conn.execute(
            "SELECT id, name FROM deck WHERE id = ?", (deck_id,)
        ).fetchone()
        if deck_row is None:
            raise DeckNotFoundError(f"deck {deck_id} not found")
        if str(deck_row[1]) == ORPHANED_DECK_NAME:
            raise OrphanedDeckProtectedError(
                f"deck {deck_id} is the protected '{ORPHANED_DECK_NAME}' fallback deck"
            )
        if folder_id is not None:
            _folder_row_or_raise(conn, folder_id)
        conn.execute(
            "UPDATE deck SET folder_id = ? WHERE id = ?", (folder_id, deck_id)
        )
        return _deck_row_or_raise(conn, deck_id)


def _note_row_or_raise(conn: sqlite3.Connection, note_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT id FROM note WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise NoteNotFoundError(f"note {note_id} not found")
    return cast(sqlite3.Row, row)


def _membership_row_or_raise(
    conn: sqlite3.Connection, note_id: int, deck_id: int
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT note_id FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, deck_id),
    ).fetchone()
    if row is None:
        raise DeckMembershipNotFoundError(
            f"note {note_id} has no membership in deck {deck_id}"
        )
    return cast(sqlite3.Row, row)


def list_deck_cards(conn: sqlite3.Connection, deck_id: int) -> DeckCardsListing:
    """Return a MANAGEMENT projection of every membership in ``deck_id``.

    This is a strict PART-B read; it must not mutate scheduling, trigger
    reviews, re-resolve vocabulary, or write to PART-B. Dictionary
    ``headword``/``pos``/``gender`` are filled in by the API layer
    through the active provider session; this helper leaves the three
    fields absent so the contract stays explicit about where dictionary
    materialization happens.
    """
    deck = _deck_row_or_raise(conn, deck_id)
    rows = conn.execute(
        """
        SELECT c.id AS card_id, c.note_id, c.due_at, c.state,
               n.lemma_semantic_ref, n.sense_semantic_ref, n.status,
               n.review_count, n.last_confidence
        FROM note_deck nd
        JOIN card c ON c.note_id = nd.note_id
        JOIN note n ON n.id = nd.note_id
        WHERE nd.deck_id = ?
        ORDER BY c.id ASC
        """,
        (deck_id,),
    ).fetchall()

    cards: list[DeckCardRow] = []
    for row in rows:
        note_id = int(row["note_id"])
        lang_rows = conn.execute(
            "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang",
            (note_id,),
        ).fetchall()
        selected_langs = tuple(str(r[0]) for r in lang_rows) if lang_rows else ("de", "en")

        user_rows = conn.execute(
            "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?",
            (note_id,),
        ).fetchall()
        user_meanings = MappingProxyType({str(r[0]): str(r[1]) for r in user_rows})

        custom_row = conn.execute(
            "SELECT 1 FROM custom_pronunciation WHERE note_id = ?", (note_id,)
        ).fetchone()
        has_custom_audio = custom_row is not None

        other_rows = conn.execute(
            "SELECT deck_id FROM note_deck WHERE note_id = ? AND deck_id <> ? ORDER BY deck_id",
            (note_id, deck_id),
        ).fetchall()
        other_deck_ids = tuple(int(r[0]) for r in other_rows)

        cards.append(
            DeckCardRow(
                card_id=int(row["card_id"]),
                note_id=note_id,
                lemma_semantic_ref=str(row["lemma_semantic_ref"]),
                sense_semantic_ref=(
                    str(row["sense_semantic_ref"]) if row["sense_semantic_ref"] else None
                ),
                status=str(row["status"]),
                due_at=str(row["due_at"]),
                state=int(row["state"]),
                review_count=int(row["review_count"]),
                last_confidence=(
                    int(row["last_confidence"]) if row["last_confidence"] is not None else None
                ),
                selected_languages=selected_langs,
                user_meanings=user_meanings,
                has_custom_audio=has_custom_audio,
                other_deck_ids=other_deck_ids,
            )
        )
    return DeckCardsListing(deck=deck, cards=tuple(cards))


def remove_note_from_deck(
    conn: sqlite3.Connection,
    deck_id: int,
    note_id: int,
    *,
    now: datetime | None = None,
    _manage_transaction: bool = True,
) -> tuple[bool, bool]:
    """Remove one ``note_deck`` membership, preserving all other state.

    Returns ``(removed, orphaned)``:

    * ``removed`` is ``True`` exactly when the requested membership was
      present and has been deleted; it is ``False`` when the membership
      did not exist (caller should map to 404 / 409).
    * ``orphaned`` is ``True`` exactly when the last non-Orphaned
      membership was removed and the note was therefore placed in the
      protected ``Orphaned`` deck with ``status='orphaned'``.

    Refuses to remove a membership from the protected ``Orphaned``
    deck; doing so would re-create the very membership that the
    safety deck is supposed to hold. Raises
    :class:`OrphanedDeckProtectedError` so callers can map to 409.
    """
    timestamp = _timestamp(_as_utc(now))
    with _transaction_context(conn, _manage_transaction):
        deck_row = conn.execute(
            "SELECT id, name FROM deck WHERE id = ?", (deck_id,)
        ).fetchone()
        if deck_row is None:
            raise DeckNotFoundError(f"deck {deck_id} not found")
        if str(deck_row[1]) == ORPHANED_DECK_NAME:
            raise OrphanedDeckProtectedError(
                f"deck {deck_id} is the protected '{ORPHANED_DECK_NAME}' fallback deck"
            )
        _note_row_or_raise(conn, note_id)
        membership = conn.execute(
            "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
            (note_id, deck_id),
        ).fetchone()
        if membership is None:
            return (False, False)

        conn.execute(
            "DELETE FROM note_deck WHERE note_id = ? AND deck_id = ?",
            (note_id, deck_id),
        )

        remaining = conn.execute(
            "SELECT 1 FROM note_deck WHERE note_id = ? LIMIT 1", (note_id,)
        ).fetchone()
        if remaining is not None:
            return (True, False)

        orphan_id = _orphan_deck_id(conn, timestamp)
        conn.execute(
            "UPDATE note SET status = 'orphaned' WHERE id = ?", (note_id,)
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO note_deck (note_id, deck_id, created_at)
            VALUES (?, ?, ?)
            """,
            (note_id, orphan_id, timestamp),
        )
        return (True, True)


def move_note_between_decks(
    conn: sqlite3.Connection,
    source_deck_id: int,
    note_id: int,
    destination_deck_id: int,
    *,
    now: datetime | None = None,
    _manage_transaction: bool = True,
) -> tuple[int, int]:
    """Atomic membership swap: source membership replaced by destination.

    Returns ``(source_deck_id, destination_deck_id)``. The function
    inserts the destination membership FIRST and removes the source
    membership SECOND; this guarantees the note is never membership-less
    inside the transaction. If the destination membership already
    exists, the source is removed and no duplicate is created.

    Raises:
      * :class:`DeckNotFoundError` if either deck is missing.
      * :class:`NoteNotFoundError` if the note is missing.
      * :class:`DeckMembershipNotFoundError` if the source membership
        is missing.
      * :class:`DeckConflictError` if ``source_deck_id == destination_deck_id``.
      * :class:`OrphanedDeckProtectedError` if the source deck is the
        protected ``Orphaned`` deck. An orphaned note carries
        ``status='orphaned'``; moving it out while preserving status
        would leave a normal membership on an orphaned note, and M1
        deliberately performs no automatic status restoration.
    """
    if source_deck_id == destination_deck_id:
        raise DeckConflictError(
            f"source deck {source_deck_id} and destination deck {destination_deck_id} "
            "are the same; no-op moves are rejected"
        )
    timestamp = _timestamp(_as_utc(now))
    with _transaction_context(conn, _manage_transaction):
        source_row = conn.execute(
            "SELECT id, name FROM deck WHERE id = ?", (source_deck_id,)
        ).fetchone()
        if source_row is None:
            raise DeckNotFoundError(f"deck {source_deck_id} not found")
        if str(source_row[1]) == ORPHANED_DECK_NAME:
            raise OrphanedDeckProtectedError(
                f"deck {source_deck_id} is the protected '{ORPHANED_DECK_NAME}' fallback deck"
            )
        dest_row = conn.execute(
            "SELECT 1 FROM deck WHERE id = ?", (destination_deck_id,)
        ).fetchone()
        if dest_row is None:
            raise DeckNotFoundError(f"deck {destination_deck_id} not found")
        _note_row_or_raise(conn, note_id)
        _membership_row_or_raise(conn, note_id, source_deck_id)

        conn.execute(
            """
            INSERT OR IGNORE INTO note_deck (note_id, deck_id, created_at)
            VALUES (?, ?, ?)
            """,
            (note_id, destination_deck_id, timestamp),
        )
        conn.execute(
            "DELETE FROM note_deck WHERE note_id = ? AND deck_id = ?",
            (note_id, source_deck_id),
        )
        return (source_deck_id, destination_deck_id)


def rename_deck(
    conn: sqlite3.Connection,
    deck_id: int,
    new_name: str,
    *,
    _manage_transaction: bool = True,
) -> DeckIdentity:
    """Rename ``deck_id`` to ``new_name`` and return the authoritative identity.

    Refuses to rename the protected ``Orphaned`` deck. ``new_name``
    must be non-blank after stripping; duplicate names raise
    :class:`DeckConflictError`.
    """
    cleaned = new_name.strip()
    if not cleaned:
        raise DeckError("deck name must not be blank")
    with _transaction_context(conn, _manage_transaction):
        row = conn.execute(
            "SELECT id, name FROM deck WHERE id = ?", (deck_id,)
        ).fetchone()
        if row is None:
            raise DeckNotFoundError(f"deck {deck_id} not found")
        if str(row[1]) == ORPHANED_DECK_NAME:
            raise OrphanedDeckProtectedError(
                f"deck {deck_id} is the protected '{ORPHANED_DECK_NAME}' fallback deck"
            )
        try:
            conn.execute(
                "UPDATE deck SET name = ? WHERE id = ?", (cleaned, deck_id)
            )
        except sqlite3.IntegrityError as exc:
            raise DeckConflictError(
                f"deck name {cleaned!r} already exists"
            ) from exc
        refreshed = _deck_row_or_raise(conn, deck_id)
        return refreshed


def set_meaning_languages(
    conn: sqlite3.Connection,
    note_id: int,
    languages: Sequence[str],
    *,
    _manage_transaction: bool = True,
) -> None:
    """Replace a note's display language set after validating it in full.

    Raises :class:`NoteNotFoundError` if ``note_id`` does not exist.
    """
    selected = _validate_languages(languages)
    with _transaction_context(conn, _manage_transaction):
        note_row = conn.execute("SELECT 1 FROM note WHERE id = ?", (note_id,)).fetchone()
        if note_row is None:
            raise NoteNotFoundError(f"note {note_id} not found")
        conn.execute("DELETE FROM note_meaning_lang WHERE note_id = ?", (note_id,))
        conn.executemany(
            "INSERT INTO note_meaning_lang (note_id, lang) VALUES (?, ?)",
            ((note_id, language) for language in selected),
        )


def set_user_meaning(
    conn: sqlite3.Connection,
    note_id: int,
    language: str,
    meaning_text: str,
    *,
    now: datetime | None = None,
    _manage_transaction: bool = True,
) -> None:
    """Upsert a language-specific note-local user meaning."""
    _validate_language(language)
    if not meaning_text.strip():
        raise DeckError("user meaning must not be blank")
    timestamp = _timestamp(_as_utc(now))
    with _transaction_context(conn, _manage_transaction):
        conn.execute(
            """
            INSERT INTO note_user_meaning (
                note_id, lang, meaning_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(note_id, lang) DO UPDATE SET
                meaning_text = excluded.meaning_text,
                updated_at = excluded.updated_at
            """,
            (note_id, language, meaning_text, timestamp, timestamp),
        )


def delete_user_meaning(
    conn: sqlite3.Connection,
    note_id: int,
    language: str,
    *,
    _manage_transaction: bool = True,
) -> None:
    """Remove one note-local user meaning without changing selected languages."""
    _validate_language(language)
    with _transaction_context(conn, _manage_transaction):
        conn.execute(
            "DELETE FROM note_user_meaning WHERE note_id = ? AND lang = ?",
            (note_id, language),
        )


def selected_meaning_languages(conn: sqlite3.Connection, note_id: int) -> tuple[str, ...]:
    """Return a note's selected display languages in deterministic order."""
    rows = conn.execute(
        "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang", (note_id,)
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _texts(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, Sequence):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _dictionary_texts(
    dictionary_meanings: DictionaryMeanings | None, sense_ref: str, language: str
) -> tuple[str, ...]:
    """Read either a ref-keyed mapping or the small language-keyed test adapter."""
    if dictionary_meanings is None:
        return ()
    by_ref = dictionary_meanings.get(sense_ref)
    if isinstance(by_ref, Mapping):
        return _texts(by_ref.get(language))
    return _texts(dictionary_meanings.get(language))


def _valid_bindings(
    conn: sqlite3.Connection, note_id: int, role: str
) -> tuple[tuple[int, str], ...]:
    rows = conn.execute(
        """
        SELECT component_ord, sense_semantic_ref, binding_status, component_count
        FROM note_dictionary_binding
        WHERE note_id = ? AND role = ?
        ORDER BY component_ord
        """,
        (note_id, role),
    ).fetchall()
    bindings = tuple((int(row[0]), str(row[1]), str(row[2]), row[3]) for row in rows)
    if role == "direct":
        if len(bindings) != 1 or bindings[0][0] != 0 or bindings[0][2] != "bound":
            return ()
        return ((bindings[0][0], bindings[0][1]),)

    # Revalidate the complete persisted D46 component vector before inspecting
    # whether any entry is bound.  The resolver-declared count prevents a
    # deleted trailing row from becoming a plausible, contiguous prefix.
    if not bindings:
        return ()
    expected_count = bindings[0][3]
    if (
        not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count <= 0
        or any(component_count != expected_count for _, _, _, component_count in bindings)
        or len(bindings) != expected_count
        or tuple(ordinal for ordinal, _, _, _ in bindings) != tuple(range(expected_count))
        or any(status != "bound" for _, _, status, _ in bindings)
    ):
        return ()
    return tuple((ordinal, sense_ref) for ordinal, sense_ref, _, _ in bindings)


def resolved_meanings(
    conn: sqlite3.Connection,
    note_id: int,
    dictionary_meanings: DictionaryMeanings | None = None,
) -> dict[str, tuple[str, ...]]:
    """Return selected meanings, with note-local text taking precedence.

    Availability uses validated D47 bindings matching note.status (M6).
    """
    row = conn.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise DeckError("unknown note")
    status = str(row[0])
    selected = selected_meaning_languages(conn, note_id)
    user_rows = conn.execute(
        "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?", (note_id,)
    ).fetchall()
    user_meanings = {str(row[0]): str(row[1]) for row in user_rows}

    if status == "derived_compound":
        direct: tuple[tuple[int, str], ...] = ()
        components = _valid_bindings(conn, note_id, "component")
    elif status == "resolved":
        direct = _valid_bindings(conn, note_id, "direct")
        components = ()
    elif status == "orphaned":
        direct = _valid_bindings(conn, note_id, "direct")
        components = () if direct else _valid_bindings(conn, note_id, "component")
    else:
        direct = ()
        components = ()

    result: dict[str, tuple[str, ...]] = {}
    for language in selected:
        if language in user_meanings:
            result[language] = (user_meanings[language],)
        elif direct:
            result[language] = _dictionary_texts(dictionary_meanings, direct[0][1], language)
        elif components:
            component_texts = [
                _dictionary_texts(dictionary_meanings, sense_ref, language)
                for _, sense_ref in components
            ]
            result[language] = (
                tuple(texts[0] for texts in component_texts) if all(component_texts) else ()
            )
        else:
            result[language] = ()
    return result


def meaning_state(
    conn: sqlite3.Connection,
    note_id: int,
    dictionary_meanings: DictionaryMeanings | None = None,
) -> str:
    """Compute ADR-0004 D43 availability for the selected language set."""
    meanings = resolved_meanings(conn, note_id, dictionary_meanings)
    available = sum(bool(texts) for texts in meanings.values())
    if available == 0:
        return "none"
    if available == len(meanings):
        return "complete"
    return "partial"


def _card_from_row(row: sqlite3.Row) -> Card:
    return Card(
        card_id=int(row["id"]),
        state=State(int(row["state"])),
        step=int(row["step"]) if row["step"] is not None else None,
        stability=float(row["stability"]) if row["stability"] is not None else None,
        difficulty=float(row["difficulty"]) if row["difficulty"] is not None else None,
        due=_parse_timestamp(str(row["due_at"])),
        last_review=(
            _parse_timestamp(str(row["last_review"])) if row["last_review"] is not None else None
        ),
    )


def review(
    conn: sqlite3.Connection,
    card_id: int,
    confidence: int,
    *,
    reviewed_at: datetime | None = None,
) -> ReviewResult:
    """Atomically schedule a card and append its raw confidence plus FSRS grade."""
    rating = confidence_to_rating(confidence)
    reviewed = _as_utc(reviewed_at)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT card.id, card.note_id, card.state, card.step, card.stability,
                   card.difficulty, card.due_at, card.last_review
            FROM card WHERE card.id = ?
            """,
            (card_id,),
        ).fetchone()
        if row is None:
            raise DeckError("unknown card")
        card = _card_from_row(cast(sqlite3.Row, row))
        previous_review = card.last_review
        scheduled_days = max(
            (card.due - (previous_review or reviewed)).total_seconds() / 86400, 0.0
        )
        elapsed_days = (
            max((reviewed - previous_review).total_seconds() / 86400, 0.0)
            if previous_review is not None
            else 0.0
        )
        updated, _ = _scheduler().review_card(card, rating, reviewed)
        interval_days = max((updated.due - reviewed).total_seconds() / 86400, 0.0)
        ease_factor = 2.5 if updated.difficulty is None else 11.0 - updated.difficulty
        conn.execute(
            """
            INSERT INTO review_log (
                card_id, confidence, rating, scheduled_days, elapsed_days, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (card_id, confidence, int(rating), scheduled_days, elapsed_days, _timestamp(reviewed)),
        )
        conn.execute(
            """
            UPDATE card
            SET state = ?, step = ?, stability = ?, difficulty = ?, due_at = ?, last_review = ?
            WHERE id = ?
            """,
            (
                int(updated.state),
                updated.step,
                updated.stability,
                updated.difficulty,
                _timestamp(updated.due),
                _timestamp(reviewed),
                card_id,
            ),
        )
        conn.execute(
            """
            UPDATE note
            SET due_at = ?, interval_days = ?, ease_factor = ?,
                review_count = review_count + 1, last_confidence = ?
            WHERE id = ?
            """,
            (_timestamp(updated.due), interval_days, ease_factor, confidence, int(row["note_id"])),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ReviewResult(
        card_id=card_id,
        confidence=confidence,
        rating=int(rating),
        due_at=updated.due,
        interval_days=interval_days,
        state=updated.state,
        stability=updated.stability,
        difficulty=updated.difficulty,
    )


class DictionaryRuntime:
    """Manages active dictionary asset lifecycle, atomic relinking, and read pins."""

    def __init__(
        self,
        dict_path: Path | str,
        user_db_path: Path | str,
        *,
        expected_sha256: str | None = None,
        expected_version: str = "v1",
    ) -> None:
        if not isinstance(dict_path, (str, Path)) or isinstance(dict_path, bool):
            raise TypeError("dict_path must be a str or Path")
        if not isinstance(user_db_path, (str, Path)) or isinstance(user_db_path, bool):
            raise TypeError("user_db_path must be a str or Path")
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str) or len(expected_sha256) != 64
        ):
            raise ValueError("expected_sha256 must be a 64-character SHA-256 string")
        if not isinstance(expected_version, str) or not expected_version.strip():
            raise ValueError("expected_version must be a non-blank string")

        self._user_db_path = Path(user_db_path).resolve()
        initial_dict_path = Path(dict_path)
        self._managed_dir = initial_dict_path.resolve().parent

        if not self._user_db_path.exists():
            raise DeckError(f"user database file not found: {self._user_db_path}")

        # Check underlying-file identity for initial dict_path
        if initial_dict_path.exists() and _is_same_file(initial_dict_path, self._user_db_path):
            raise DictionaryRuntimeError("dictionary path is the user database file")

        # WAL establishment on user database
        self._establish_wal()

        self._lock = threading.Lock()
        self._activation_lock = threading.Lock()
        self._thread_local = threading.local()
        self._closed = False
        self._generation_counter = 0
        self._seam_probe: Callable[[], None] | None = None
        self._pre_commit_probe: Callable[[], None] | None = None
        self._writer_close_hook: Callable[[], None] | None = None
        self._rollback_failure_hook: Callable[[], None] | None = None

        # Check active_dictionary_metadata
        self._init_active_generation(
            initial_dict_path,
            expected_sha256=expected_sha256.lower() if expected_sha256 is not None else None,
            expected_version=expected_version.strip(),
        )

    @property
    def managed_dir(self) -> Path:
        """Return the managed dictionary directory."""
        return self._managed_dir

    @property
    def asset_token(self) -> str:
        """Return the current active dictionary asset token (SHA-256)."""
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            return self._current_generation.asset.asset_token

    @property
    def lemma_ids(self) -> Mapping[str, int]:
        """Return the durable ``lemma_ref -> lemma_id`` map for the active asset."""
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            return self._current_generation.asset.lemma_ids

    @property
    def sense_ids(self) -> Mapping[str, tuple[int, int]]:
        """Return the durable ``sense_ref -> (sense_id, lemma_id)`` map for the active asset."""
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            return self._current_generation.asset.sense_ids

    def provider(self) -> DictionaryProvider:
        """Return a Slice-11 ``DictionaryProvider`` adapter for the current asset.

        Slice 12: ``app/api.py`` migrated served-product dictionary reads
        off raw ``asset.connection`` access onto the abstract provider
        contract. The adapter wraps the existing validated asset (and
        therefore reuses the runtime's read pin semantics) so the
        provider code path remains consistent with the activation /
        relink rules already enforced by ``DictionaryRuntime``.
        """
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            gen = self._current_generation
            cached = getattr(gen, "_provider_view", None)
            if cached is not None:
                return cached  # type: ignore[no-any-return]
            from app.provider_local import LocalDictionaryProvider

            view = LocalDictionaryProvider(gen.asset.path, asset=gen.asset)
            try:
                gen._provider_view = view  # type: ignore[attr-defined]
            except Exception:
                pass
            return view

    @property
    def current_generation_id(self) -> int:
        """Return the monotonic generation identifier of the current asset."""
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            return self._current_generation.generation_id

    @property
    def is_closed(self) -> bool:
        """Return whether the runtime is closed."""
        with self._lock:
            return self._closed

    def _establish_wal(self) -> None:
        try:
            conn = sqlite3.connect(self._user_db_path)
            try:
                cur = conn.execute("PRAGMA journal_mode=WAL")
                row = cur.fetchone()
                if row is None or str(row[0]).lower() != "wal":
                    raise DeckError("failed to establish WAL journal mode on user database")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise DeckError(f"failed to configure WAL on user database: {exc}") from exc

    def _init_active_generation(
        self,
        initial_dict_path: Path,
        *,
        expected_sha256: str | None,
        expected_version: str,
    ) -> None:
        conn = sqlite3.connect(self._user_db_path)
        try:
            row = conn.execute(
                "SELECT active_version, active_filename, active_sha256, activated_at "
                "FROM active_dictionary_metadata WHERE singleton = 1"
            ).fetchone()
        finally:
            conn.close()

        # Canonical launcher startup supplies the selected manifest identity.
        # Validate the configured pathname into one immutable snapshot before
        # consulting durable metadata; that snapshot is the only asset this
        # runtime can publish.  This closes both the pathname race after the
        # lightweight launcher precheck and the stale-metadata bypass.
        if expected_sha256 is not None:
            if not initial_dict_path.is_file():
                raise DictionaryRuntimeError(
                    f"initial dictionary file not found: {initial_dict_path}"
                )
            resolved = initial_dict_path.resolve()
            if resolved.parent != self._managed_dir:
                raise DictionaryRuntimeError("initial dictionary is outside managed directory")
            if _is_same_file(resolved, self._user_db_path):
                raise DictionaryRuntimeError("initial dictionary is the user database file")
            try:
                asset = validate_candidate_dictionary(resolved)
            except Exception as exc:
                raise DictionaryRuntimeError(
                    f"failed to validate initial dictionary: {exc}"
                ) from exc
            if asset.sha256 != expected_sha256:
                asset.close()
                raise DictionaryRuntimeError(
                    "initial dictionary SHA-256 does not match the selected manifest"
                )

            metadata_matches = (
                row is not None and str(row[1]) == resolved.name and str(row[2]) == asset.sha256
            )
            if not metadata_matches or str(row[0]) != expected_version:
                now_text = _timestamp(_utc_now())
                conn = sqlite3.connect(self._user_db_path)
                try:
                    conn.execute("PRAGMA foreign_keys = ON")
                    conn.execute("BEGIN IMMEDIATE")
                    if not metadata_matches:
                        self._relink_part_b(conn, asset, now_text)
                    conn.execute(
                        """
                        INSERT INTO active_dictionary_metadata (
                            singleton, active_version, active_filename, active_sha256, activated_at
                        ) VALUES (1, ?, ?, ?, ?)
                        ON CONFLICT(singleton) DO UPDATE SET
                            active_version = excluded.active_version,
                            active_filename = excluded.active_filename,
                            active_sha256 = excluded.active_sha256,
                            activated_at = excluded.activated_at
                        """,
                        (expected_version, resolved.name, asset.sha256, now_text),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    asset.close()
                    raise
                finally:
                    conn.close()

            self._generation_counter = 1
            self._current_generation = _Generation(
                generation_id=1,
                asset=asset,
                pins=0,
                retired=False,
                closed=False,
            )
            return

        if row is not None:
            active_filename, active_sha256 = str(row[1]), str(row[2])
            if "/" in active_filename or "\\" in active_filename or ".." in active_filename:
                raise DictionaryRuntimeError(
                    f"invalid active_filename in metadata: {active_filename}"
                )

            recovery_target = self._managed_dir / active_filename
            if not recovery_target.is_file():
                raise DictionaryRuntimeError(
                    f"recovery target dictionary file not found: {recovery_target}"
                )

            if _is_same_file(recovery_target, self._user_db_path):
                raise DictionaryRuntimeError("recovery target dictionary is the user database file")

            try:
                asset = validate_candidate_dictionary(recovery_target)
            except Exception as exc:
                raise DictionaryRuntimeError(f"failed to validate recovery target: {exc}") from exc

            if asset.sha256 != active_sha256:
                asset.close()
                raise DictionaryRuntimeError(
                    "recovery target SHA-256 does not match active_dictionary_metadata"
                )

            self._generation_counter = 1
            self._current_generation = _Generation(
                generation_id=1,
                asset=asset,
                pins=0,
                retired=False,
                closed=False,
            )
        else:
            if not initial_dict_path.is_file():
                raise DictionaryRuntimeError(
                    f"initial dictionary file not found: {initial_dict_path}"
                )

            resolved = initial_dict_path.resolve()
            if resolved.parent != self._managed_dir:
                raise DictionaryRuntimeError("initial dictionary is outside managed directory")

            if _is_same_file(resolved, self._user_db_path):
                raise DictionaryRuntimeError("initial dictionary is the user database file")

            try:
                asset = validate_candidate_dictionary(resolved)
            except Exception as exc:
                raise DictionaryRuntimeError(
                    f"failed to validate initial dictionary: {exc}"
                ) from exc

            now_text = _timestamp(_utc_now())
            conn = sqlite3.connect(self._user_db_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("BEGIN IMMEDIATE")
                self._relink_part_b(conn, asset, now_text)
                conn.execute(
                    """
                    INSERT INTO active_dictionary_metadata (
                        singleton, active_version, active_filename, active_sha256, activated_at
                    ) VALUES (1, 'v1', ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        active_version = excluded.active_version,
                        active_filename = excluded.active_filename,
                        active_sha256 = excluded.active_sha256,
                        activated_at = excluded.activated_at
                    """,
                    (resolved.name, asset.sha256, now_text),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                asset.close()
                raise
            finally:
                conn.close()

            self._generation_counter = 1
            self._current_generation = _Generation(
                generation_id=1,
                asset=asset,
                pins=0,
                retired=False,
                closed=False,
            )

    @contextmanager
    def reading(self) -> Generator[ReadingSnapshot, None, None]:
        """Yield an inert immutable value snapshot under an atomic read pin."""
        reader_conn: sqlite3.Connection | None = None
        gen: _Generation | None = None
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")

            try:
                reader_conn = sqlite3.connect(
                    f"{self._user_db_path.as_uri()}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                )
                reader_conn.row_factory = sqlite3.Row
                reader_conn.execute("BEGIN DEFERRED")

                rows = reader_conn.execute(
                    """
                    SELECT note_id, role, component_ord, cached_lemma_id, cached_sense_id
                    FROM note_dictionary_binding
                    """
                ).fetchall()
                bindings_dict = {
                    (int(r[0]), str(r[1]), int(r[2])): (
                        int(r[3]) if r[3] is not None else None,
                        int(r[4]) if r[4] is not None else None,
                    )
                    for r in rows
                }

                gen = self._current_generation
                asset_token = str(gen.asset.asset_token)
                lemma_ids = MappingProxyType(dict(gen.asset.lemma_ids))
                sense_ids = MappingProxyType(dict(gen.asset.sense_ids))
                lemma_fps = MappingProxyType(dict(gen.asset.lemma_identity_fingerprints))
                sense_fps = MappingProxyType(dict(gen.asset.sense_identity_fingerprints))
                bindings = MappingProxyType(bindings_dict)

                snapshot = ReadingSnapshot(
                    asset_token=asset_token,
                    lemma_ids=lemma_ids,
                    sense_ids=sense_ids,
                    lemma_identity_fingerprints=lemma_fps,
                    sense_identity_fingerprints=sense_fps,
                    bindings=bindings,
                )

                gen.pins += 1
                depth = getattr(self._thread_local, "depth", 0)
                self._thread_local.depth = depth + 1
                prev_reader_conn = getattr(self._thread_local, "reader_conn", None)
                prev_gen = getattr(self._thread_local, "pinned_generation", None)
                self._thread_local.reader_conn = reader_conn
                self._thread_local.pinned_generation = gen
            except Exception:
                if reader_conn is not None:
                    try:
                        reader_conn.rollback()
                    except Exception:
                        pass
                    try:
                        reader_conn.close()
                    except Exception:
                        pass
                raise

        try:
            yield snapshot
        finally:
            if reader_conn is not None:
                try:
                    reader_conn.rollback()
                except Exception:
                    pass
                try:
                    reader_conn.close()
                except Exception:
                    pass

            self._thread_local.reader_conn = prev_reader_conn
            self._thread_local.pinned_generation = prev_gen

            if gen is not None:
                with self._lock:
                    depth = getattr(self._thread_local, "depth", 0)
                    self._thread_local.depth = max(0, depth - 1)
                    gen.pins = max(0, gen.pins - 1)
                    if gen.retired and gen.pins == 0 and not gen.closed:
                        gen.closed = True
                        try:
                            gen.asset.close()
                        except Exception:
                            pass

    def _materialize_lemma_under_gen(
        self,
        conn: sqlite3.Connection,
        lemma_semantic_ref: str,
    ) -> tuple[
        MappingProxyType[str, object] | None,
        tuple[MappingProxyType[str, object], ...],
        tuple[MappingProxyType[str, object], ...],
        tuple[MappingProxyType[str, object], ...],
    ]:
        lem_cur = conn.execute(
            """
            SELECT id, semantic_ref, lemma, pos, gender, plural, plural_none,
                   genitive_sg, aux, separable, particle, reflexive, praesens_3sg,
                   praeteritum_3sg, partizip_ii, governs, comparative, superlative,
                   ipa, ipa_source, freq_rank, source, license
            FROM lemma WHERE semantic_ref = ?
            """,
            (lemma_semantic_ref,),
        )
        lem = lem_cur.fetchone()
        if lem is None:
            return (None, (), (), ())

        lem_id = int(lem["id"])

        s_cur = conn.execute(
            """
            SELECT id, lemma_id, semantic_ref, source_namespace, source_ref, ord,
                   register, source, license
            FROM sense WHERE lemma_id = ?
            ORDER BY ord ASC, semantic_ref ASC, id ASC
            """,
            (lem_id,),
        )
        sense_rows = s_cur.fetchall()

        m_cur = conn.execute(
            """
            SELECT sm.id, sm.sense_id, sm.language, sm.kind, sm.ord, sm.text,
                   sm.source, sm.license
            FROM sense_meaning sm
            JOIN sense s ON sm.sense_id = s.id
            WHERE s.lemma_id = ?
            ORDER BY sm.language ASC, sm.kind ASC, sm.ord ASC, sm.id ASC
            """,
            (lem_id,),
        )
        meaning_rows = m_cur.fetchall()

        e_cur = conn.execute(
            """
            SELECT e.id, e.de, e.en, e.source, e.source_ref, e.license,
                   e.token_count, e.has_proper
            FROM example_lemma el
            JOIN example e ON el.example_id = e.id
            WHERE el.lemma_id = ?
            ORDER BY e.id ASC
            LIMIT ?
            """,
            (lem_id, MAX_CARD_EXAMPLES),
        )
        example_rows = e_cur.fetchall()

        lemma_map = MappingProxyType(
            {
                "id": lem_id,
                "semantic_ref": str(lem["semantic_ref"]),
                "lemma": str(lem["lemma"]),
                "pos": str(lem["pos"]),
                "gender": str(lem["gender"]) if lem["gender"] is not None else None,
                "plural": str(lem["plural"]) if lem["plural"] is not None else None,
                "plural_none": int(lem["plural_none"]),
                "genitive_sg": (
                    str(lem["genitive_sg"]) if lem["genitive_sg"] is not None else None
                ),
                "aux": str(lem["aux"]) if lem["aux"] is not None else None,
                "separable": int(lem["separable"]),
                "particle": str(lem["particle"]) if lem["particle"] is not None else None,
                "reflexive": int(lem["reflexive"]),
                "praesens_3sg": (
                    str(lem["praesens_3sg"]) if lem["praesens_3sg"] is not None else None
                ),
                "praeteritum_3sg": (
                    str(lem["praeteritum_3sg"]) if lem["praeteritum_3sg"] is not None else None
                ),
                "partizip_ii": (
                    str(lem["partizip_ii"]) if lem["partizip_ii"] is not None else None
                ),
                "governs": str(lem["governs"]) if lem["governs"] is not None else None,
                "comparative": (
                    str(lem["comparative"]) if lem["comparative"] is not None else None
                ),
                "superlative": (
                    str(lem["superlative"]) if lem["superlative"] is not None else None
                ),
                "ipa": str(lem["ipa"]) if lem["ipa"] is not None else None,
            }
        )
        senses_tuple = tuple(
            MappingProxyType(
                {
                    "id": int(s["id"]),
                    "semantic_ref": str(s["semantic_ref"]),
                    "source_namespace": str(s["source_namespace"]),
                    "source_ref": str(s["source_ref"]),
                    "ord": int(s["ord"]),
                    "register": str(s["register"]) if s["register"] is not None else None,
                }
            )
            for s in sense_rows
        )
        meanings_tuple = tuple(
            MappingProxyType(
                {
                    "id": int(m["id"]),
                    "sense_id": int(m["sense_id"]),
                    "language": str(m["language"]),
                    "kind": str(m["kind"]),
                    "ord": int(m["ord"]),
                    "text": str(m["text"]),
                }
            )
            for m in meaning_rows
        )
        examples_tuple = tuple(
            MappingProxyType(
                {
                    "id": int(ex["id"]),
                    "de": str(ex["de"]),
                    "en": str(ex["en"]) if ex["en"] is not None else None,
                }
            )
            for ex in example_rows
        )
        return (lemma_map, senses_tuple, meanings_tuple, examples_tuple)

    def _materialize_components_under_gen(
        self,
        conn: sqlite3.Connection,
        comp_rows: Sequence[sqlite3.Row] | Sequence[Mapping[str, str]],
    ) -> tuple[MappingProxyType[str, object], ...]:
        components: list[MappingProxyType[str, object]] = []
        for cr in comp_rows:
            c_lem_ref = str(cr["lemma_semantic_ref"])
            c_sense_ref = str(cr["sense_semantic_ref"])
            lem_cur = conn.execute(
                "SELECT lemma FROM lemma WHERE semantic_ref = ?", (c_lem_ref,)
            ).fetchone()
            lemma_text = str(lem_cur[0]) if lem_cur is not None else c_lem_ref.split(":")[-1]

            meanings_by_lang: dict[str, str] = {}
            for lang in ("de", "en"):
                m_cur = conn.execute(
                    """
                    SELECT sm.text FROM sense_meaning sm
                    JOIN sense s ON s.id = sm.sense_id
                    WHERE s.semantic_ref = ? AND sm.language = ?
                    ORDER BY sm.ord ASC LIMIT 1
                    """,
                    (c_sense_ref, lang),
                ).fetchone()
                if m_cur is not None:
                    meanings_by_lang[lang] = str(m_cur[0])

            components.append(
                MappingProxyType(
                    {
                        "lemma_ref": c_lem_ref,
                        "sense_ref": c_sense_ref,
                        "lemma": lemma_text,
                        "meanings": MappingProxyType(meanings_by_lang),
                    }
                )
            )
        return tuple(components)

    def _observe_card_render_internal(
        self,
        reader_conn: sqlite3.Connection,
        gen: _Generation,
        *,
        card_id: int | None = None,
        deck_id: int | None = None,
    ) -> MappingProxyType[str, object] | None:
        if card_id is not None:
            row = reader_conn.execute(
                """
                SELECT c.id AS card_id, c.note_id, c.due_at, c.state,
                       c.stability, c.difficulty,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       n.status AS note_status
                FROM card c
                JOIN note n ON n.id = c.note_id
                WHERE c.id = ?
                """,
                (card_id,),
            ).fetchone()
        elif deck_id is not None:
            row = reader_conn.execute(
                """
                SELECT c.id AS card_id, c.note_id, c.due_at, c.state,
                       c.stability, c.difficulty,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       n.status AS note_status
                FROM card c
                JOIN note n ON n.id = c.note_id
                JOIN note_deck nd ON nd.note_id = n.id
                WHERE nd.deck_id = ? AND c.due_at <= ?
                ORDER BY c.due_at ASC, c.id ASC
                LIMIT 1
                """,
                (deck_id, _timestamp(_utc_now())),
            ).fetchone()
        else:
            row = reader_conn.execute(
                """
                SELECT c.id AS card_id, c.note_id, c.due_at, c.state,
                       c.stability, c.difficulty,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       n.status AS note_status
                FROM card c
                JOIN note n ON n.id = c.note_id
                WHERE c.due_at <= ?
                ORDER BY c.due_at ASC, c.id ASC
                LIMIT 1
                """,
                (_timestamp(_utc_now()),),
            ).fetchone()

        if row is None:
            return None

        c_id = int(row["card_id"])
        n_id = int(row["note_id"])
        due_at = str(row["due_at"])
        state = int(row["state"])
        stability = float(row["stability"]) if row["stability"] is not None else None
        difficulty = float(row["difficulty"]) if row["difficulty"] is not None else None
        lemma_ref = str(row["lemma_semantic_ref"])
        sense_ref = str(row["sense_semantic_ref"]) if row["sense_semantic_ref"] else None
        note_status = str(row["note_status"])

        lang_rows = reader_conn.execute(
            "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang",
            (n_id,),
        ).fetchall()
        selected_langs = tuple(str(r[0]) for r in lang_rows) if lang_rows else ("de", "en")

        user_meanings_rows = reader_conn.execute(
            "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?",
            (n_id,),
        ).fetchall()
        user_meanings_dict = MappingProxyType({str(r[0]): str(r[1]) for r in user_meanings_rows})

        custom_row = reader_conn.execute(
            "SELECT 1 FROM custom_pronunciation WHERE note_id = ?",
            (n_id,),
        ).fetchone()
        has_custom_audio = custom_row is not None

        comp_rows: list[sqlite3.Row] = []
        if note_status == "derived_compound":
            comp_rows = reader_conn.execute(
                """
                SELECT component_ord, lemma_semantic_ref, sense_semantic_ref
                FROM note_dictionary_binding
                WHERE note_id = ? AND role = 'component'
                ORDER BY component_ord ASC
                """,
                (n_id,),
            ).fetchall()

        dict_conn = gen.asset.connection
        components = (
            self._materialize_components_under_gen(dict_conn, comp_rows) if comp_rows else ()
        )

        lemma_map, senses_tuple, meanings_tuple, examples_tuple = self._materialize_lemma_under_gen(
            dict_conn, lemma_ref
        )

        payload_dict: dict[str, object] = {
            "card_id": c_id,
            "note_id": n_id,
            "due_at": due_at,
            "state": state,
            "stability": stability,
            "difficulty": difficulty,
            "note_status": note_status,
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "asset_token": str(gen.asset.asset_token),
            "selected_languages": selected_langs,
            "user_meanings": user_meanings_dict,
            "has_custom_audio": has_custom_audio,
            "components": components,
            "lemma": lemma_map,
            "senses": senses_tuple,
            "meanings": meanings_tuple,
            "examples": examples_tuple,
        }
        return MappingProxyType(payload_dict)

    def _observe_export_payload_internal(
        self,
        reader_conn: sqlite3.Connection,
        gen: _Generation,
        *,
        deck_id: int | None = None,
    ) -> tuple[MappingProxyType[str, object], ...]:
        if deck_id is not None:
            rows = reader_conn.execute(
                """
                SELECT c.id AS card_id, n.id AS note_id, n.status,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       GROUP_CONCAT(DISTINCT d.name) AS deck_names
                FROM card c
                JOIN note n ON n.id = c.note_id
                JOIN note_deck nd ON nd.note_id = n.id
                JOIN deck d ON d.id = nd.deck_id
                WHERE d.id = ?
                GROUP BY c.id, n.id, n.status, n.lemma_semantic_ref,
                         n.sense_semantic_ref
                ORDER BY c.id ASC
                """,
                (deck_id,),
            ).fetchall()
        else:
            rows = reader_conn.execute(
                """
                SELECT c.id AS card_id, n.id AS note_id, n.status,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       GROUP_CONCAT(DISTINCT d.name) AS deck_names
                FROM card c
                JOIN note n ON n.id = c.note_id
                LEFT JOIN note_deck nd ON nd.note_id = n.id
                LEFT JOIN deck d ON d.id = nd.deck_id
                GROUP BY c.id, n.id, n.status, n.lemma_semantic_ref,
                         n.sense_semantic_ref
                ORDER BY c.id ASC
                """
            ).fetchall()

        dict_conn = gen.asset.connection
        token = str(gen.asset.asset_token)
        export_items: list[MappingProxyType[str, object]] = []

        for row in rows:
            c_id = int(row["card_id"])
            n_id = int(row["note_id"])
            note_status = str(row["status"])
            lemma_ref = str(row["lemma_semantic_ref"])
            sense_ref = str(row["sense_semantic_ref"]) if row["sense_semantic_ref"] else None
            deck_names_str = str(row["deck_names"]) if row["deck_names"] else ""

            lang_rows = reader_conn.execute(
                "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang",
                (n_id,),
            ).fetchall()
            selected_langs = tuple(str(r[0]) for r in lang_rows) if lang_rows else ("de", "en")

            user_meanings_rows = reader_conn.execute(
                "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?",
                (n_id,),
            ).fetchall()
            user_meanings_dict = MappingProxyType(
                {str(r[0]): str(r[1]) for r in user_meanings_rows}
            )

            custom_row = reader_conn.execute(
                "SELECT 1 FROM custom_pronunciation WHERE note_id = ?",
                (n_id,),
            ).fetchone()
            has_custom_audio = custom_row is not None

            comp_rows: list[sqlite3.Row] = []
            if note_status == "derived_compound":
                comp_rows = reader_conn.execute(
                    """
                    SELECT component_ord, lemma_semantic_ref, sense_semantic_ref
                    FROM note_dictionary_binding
                    WHERE note_id = ? AND role = 'component'
                    ORDER BY component_ord ASC
                    """,
                    (n_id,),
                ).fetchall()

            components = (
                self._materialize_components_under_gen(dict_conn, comp_rows) if comp_rows else ()
            )

            lemma_map, senses_tuple, meanings_tuple, examples_tuple = (
                self._materialize_lemma_under_gen(dict_conn, lemma_ref)
            )

            card_dict: dict[str, object] = {
                "card_id": c_id,
                "note_id": n_id,
                "due_at": "",
                "state": 0,
                "stability": None,
                "difficulty": None,
                "note_status": note_status,
                "lemma_semantic_ref": lemma_ref,
                "sense_semantic_ref": sense_ref,
                "deck_names": deck_names_str,
                "asset_token": token,
                "selected_languages": selected_langs,
                "user_meanings": user_meanings_dict,
                "has_custom_audio": has_custom_audio,
                "components": components,
                "lemma": lemma_map,
                "senses": senses_tuple,
                "meanings": meanings_tuple,
                "examples": examples_tuple,
            }
            export_items.append(MappingProxyType(card_dict))

        return tuple(export_items)

    def observe_card_render(
        self,
        card_id: int | None = None,
        *,
        deck_id: int | None = None,
    ) -> MappingProxyType[str, object] | None:
        """Observe next or specified card render payload inside a single read pin."""
        active_reader_conn = getattr(self._thread_local, "reader_conn", None)
        active_gen = getattr(self._thread_local, "pinned_generation", None)

        if active_reader_conn is not None and active_gen is not None:
            return self._observe_card_render_internal(
                active_reader_conn, active_gen, card_id=card_id, deck_id=deck_id
            )

        with self.reading():
            reader_conn = getattr(self._thread_local, "reader_conn", None)
            gen = getattr(self._thread_local, "pinned_generation", None)
            if reader_conn is None or gen is None:
                raise DictionaryRuntimeError("reader connection not available")
            return self._observe_card_render_internal(
                reader_conn, gen, card_id=card_id, deck_id=deck_id
            )

    def observe_export_payload(
        self,
        deck_id: int | None = None,
    ) -> tuple[MappingProxyType[str, object], ...]:
        """Observe export card payloads inside a single read pin."""
        active_reader_conn = getattr(self._thread_local, "reader_conn", None)
        active_gen = getattr(self._thread_local, "pinned_generation", None)

        if active_reader_conn is not None and active_gen is not None:
            return self._observe_export_payload_internal(
                active_reader_conn, active_gen, deck_id=deck_id
            )

        with self.reading():
            reader_conn = getattr(self._thread_local, "reader_conn", None)
            gen = getattr(self._thread_local, "pinned_generation", None)
            if reader_conn is None or gen is None:
                raise DictionaryRuntimeError("reader connection not available")
            return self._observe_export_payload_internal(reader_conn, gen, deck_id=deck_id)

    def materialize_lookup(
        self, query: str
    ) -> tuple[str, tuple[MappingProxyType[str, object], ...]]:
        """Look up exact and surface lemmas, returning (asset_token, candidate_entries)."""
        clean_q = query.strip()
        if not clean_q:
            return ("", ())

        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            gen = self._current_generation
            token = gen.asset.asset_token
            conn = gen.asset.connection

            # 1. Exact lemmas
            cur = conn.execute(
                """
                SELECT id, semantic_ref, lemma, pos, gender, plural, plural_none,
                       genitive_sg, aux, separable, particle, reflexive, praesens_3sg,
                       praeteritum_3sg, partizip_ii, governs, comparative, superlative,
                       ipa, ipa_source, freq_rank, source, license
                FROM lemma
                WHERE (lemma = ? OR lower(lemma) = ?)
                ORDER BY freq_rank ASC NULLS LAST, pos ASC, gender ASC NULLS LAST, semantic_ref ASC
                """,
                (clean_q, clean_q.lower()),
            )
            exact_rows = cur.fetchall()

            # 2. Surface lemmas (if no exact lemmas)
            surface_rows: list[sqlite3.Row] = []
            if not exact_rows:
                cur = conn.execute(
                    """
                    SELECT l.id, l.semantic_ref, l.lemma, l.pos, l.gender, l.plural,
                           l.plural_none, l.genitive_sg, l.aux, l.separable, l.particle,
                           l.reflexive, l.praesens_3sg, l.praeteritum_3sg, l.partizip_ii,
                           l.governs, l.comparative, l.superlative, l.ipa, l.ipa_source,
                           l.freq_rank, l.source, l.license
                    FROM surface_form sf
                    JOIN lemma l ON sf.lemma_id = l.id
                    WHERE (sf.form = ? OR lower(sf.form) = ?)
                    ORDER BY l.freq_rank ASC NULLS LAST, l.pos ASC, l.gender ASC NULLS LAST,
                             l.semantic_ref ASC
                    """,
                    (clean_q, clean_q.lower()),
                )
                seen_ids: set[int] = set()
                for r in cur.fetchall():
                    lid = int(r["id"])
                    if lid not in seen_ids:
                        seen_ids.add(lid)
                        surface_rows.append(r)

            all_lemma_rows = exact_rows if exact_rows else surface_rows
            if not all_lemma_rows:
                return (token, ())

            candidates: list[MappingProxyType[str, object]] = []
            for lem in all_lemma_rows:
                lem_id = int(lem["id"])

                # Senses
                s_cur = conn.execute(
                    """
                    SELECT id, lemma_id, semantic_ref, source_namespace, source_ref, ord,
                           register, source, license
                    FROM sense WHERE lemma_id = ?
                    ORDER BY ord ASC, semantic_ref ASC, id ASC
                    """,
                    (lem_id,),
                )
                sense_rows = s_cur.fetchall()

                # Meanings
                m_cur = conn.execute(
                    """
                    SELECT sm.id, sm.sense_id, sm.language, sm.kind, sm.ord, sm.text,
                           sm.source, sm.license
                    FROM sense_meaning sm
                    JOIN sense s ON sm.sense_id = s.id
                    WHERE s.lemma_id = ?
                    ORDER BY sm.language ASC, sm.kind ASC, sm.ord ASC, sm.id ASC
                    """,
                    (lem_id,),
                )
                meaning_rows = m_cur.fetchall()

                senses_data: list[MappingProxyType[str, object]] = []
                for s in sense_rows:
                    sid = int(s["id"])
                    meanings_data = tuple(
                        MappingProxyType(
                            {
                                "language": str(m["language"]),
                                "kind": str(m["kind"]),
                                "text": str(m["text"]),
                                "ord": int(m["ord"]),
                            }
                        )
                        for m in meaning_rows
                        if int(m["sense_id"]) == sid
                    )
                    senses_data.append(
                        MappingProxyType(
                            {
                                "sense_id": sid,
                                "sense_semantic_ref": str(s["semantic_ref"]),
                                "source_namespace": str(s["source_namespace"]),
                                "source_ref": str(s["source_ref"]),
                                "ord": int(s["ord"]),
                                "register": str(s["register"])
                                if s["register"] is not None
                                else None,
                                "meanings": meanings_data,
                            }
                        )
                    )
                ranked_senses_data = tuple(rank_senses(senses_data))

                candidates.append(
                    MappingProxyType(
                        {
                            "lemma_id": lem_id,
                            "lemma_semantic_ref": str(lem["semantic_ref"]),
                            "lemma": str(lem["lemma"]),
                            "pos": str(lem["pos"]),
                            "gender": str(lem["gender"]) if lem["gender"] is not None else None,
                            "plural": str(lem["plural"]) if lem["plural"] is not None else None,
                            "plural_none": int(lem["plural_none"]),
                            "genitive_sg": (
                                str(lem["genitive_sg"]) if lem["genitive_sg"] is not None else None
                            ),
                            "aux": str(lem["aux"]) if lem["aux"] is not None else None,
                            "separable": int(lem["separable"]),
                            "particle": str(lem["particle"])
                            if lem["particle"] is not None
                            else None,
                            "reflexive": int(lem["reflexive"]),
                            "praesens_3sg": (
                                str(lem["praesens_3sg"])
                                if lem["praesens_3sg"] is not None
                                else None
                            ),
                            "praeteritum_3sg": (
                                str(lem["praeteritum_3sg"])
                                if lem["praeteritum_3sg"] is not None
                                else None
                            ),
                            "partizip_ii": (
                                str(lem["partizip_ii"]) if lem["partizip_ii"] is not None else None
                            ),
                            "governs": str(lem["governs"]) if lem["governs"] is not None else None,
                            "comparative": (
                                str(lem["comparative"]) if lem["comparative"] is not None else None
                            ),
                            "superlative": (
                                str(lem["superlative"]) if lem["superlative"] is not None else None
                            ),
                            "ipa": str(lem["ipa"]) if lem["ipa"] is not None else None,
                            "senses": ranked_senses_data,
                            "examples": (),
                        }
                    )
                )

            return (token, tuple(filter_candidates(candidates)))

    def materialize_card_render_payload(
        self, lemma_semantic_ref: str
    ) -> MappingProxyType[str, object] | None:
        """Materialize immutable lemma data, senses, meanings, and examples for card rendering."""
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            gen = self._current_generation
            conn = gen.asset.connection
            lemma_map, senses_tuple, meanings_tuple, examples_tuple = (
                self._materialize_lemma_under_gen(conn, lemma_semantic_ref)
            )
            if lemma_map is None:
                return None
            return MappingProxyType(
                {
                    "lemma": lemma_map,
                    "senses": senses_tuple,
                    "meanings": meanings_tuple,
                    "examples": examples_tuple,
                }
            )

    def materialize_compound_components(
        self, component_refs: Sequence[tuple[str, str]]
    ) -> tuple[MappingProxyType[str, object], ...]:
        """Materialize immutable compound component lemma texts and meanings for languages."""
        with self._lock:
            if self._closed:
                raise DictionaryClosedError("runtime is closed")
            gen = self._current_generation
            conn = gen.asset.connection
            comp_rows = [
                {"lemma_semantic_ref": cr[0], "sense_semantic_ref": cr[1]} for cr in component_refs
            ]
            return self._materialize_components_under_gen(conn, comp_rows)

    def activate_dictionary(
        self,
        path: Path | str,
        *,
        version: str = "v1",
        activated_at: datetime | None = None,
    ) -> None:
        """Atomically validate candidate dictionary, relink PART-B, and swap generations."""
        # Phase (1): same-thread reentrancy refusal
        if getattr(self._thread_local, "depth", 0) > 0:
            raise DictionaryRuntimeError("same-thread reentrancy is forbidden")

        # Phase (2): acquire _activation_lock
        with self._activation_lock:
            # Phase (3): closed check under runtime lock
            with self._lock:
                if self._closed:
                    raise DictionaryClosedError("runtime is closed")

            # Phase (4): argument / type validation
            if not isinstance(path, (str, Path)) or isinstance(path, bool):
                raise TypeError(f"path must be a str or Path, got {type(path).__name__}")
            if isinstance(path, str) and not path.strip():
                raise ValueError("candidate dictionary path must not be blank")
            if not isinstance(version, str) or not version.strip():
                raise ValueError("version must be a non-blank string")

            raw_str = str(path)
            normalized_parts = raw_str.replace("\\", "/").split("/")
            if ".." in normalized_parts:
                raise DictionaryAssetError(
                    "path traversal is forbidden in candidate dictionary path"
                )

            candidate_path = Path(path)
            if candidate_path.is_absolute():
                resolved_cand = candidate_path.resolve()
            else:
                resolved_cand = (self._managed_dir / candidate_path).resolve()

            # Phase (5): managed-path validation
            if resolved_cand.parent != self._managed_dir:
                raise DictionaryAssetError(
                    f"candidate dictionary must reside in managed directory: {resolved_cand}"
                )

            if "/" in resolved_cand.name or "\\" in resolved_cand.name:
                raise DictionaryAssetError("candidate dictionary filename contains separators")

            if not resolved_cand.is_file():
                raise DictionaryAssetError(f"candidate dictionary file not found: {resolved_cand}")

            if _is_same_file(resolved_cand, self._user_db_path):
                raise DictionaryAssetError("candidate dictionary is the user database file")

            # Phase (6): candidate validation
            candidate_asset: DictionaryAsset | None = None
            write_conn: sqlite3.Connection | None = None
            committed = False

            try:
                candidate_asset = validate_candidate_dictionary(resolved_cand)

                # Phase (7): BEGIN IMMEDIATE on dedicated write connection, relink, upsert metadata
                now_text = _timestamp(_as_utc(activated_at))
                write_conn = sqlite3.connect(self._user_db_path, check_same_thread=False)
                write_conn.execute("PRAGMA foreign_keys = ON")
                write_conn.execute("BEGIN IMMEDIATE")

                self._relink_part_b(write_conn, candidate_asset, now_text)

                write_conn.execute(
                    """
                    INSERT INTO active_dictionary_metadata (
                        singleton, active_version, active_filename, active_sha256, activated_at
                    ) VALUES (1, ?, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        active_version = excluded.active_version,
                        active_filename = excluded.active_filename,
                        active_sha256 = excluded.active_sha256,
                        activated_at = excluded.activated_at
                    """,
                    (version.strip(), resolved_cand.name, candidate_asset.sha256, now_text),
                )

                if self._pre_commit_probe is not None:
                    self._pre_commit_probe()

                # Phase (8): [runtime lock: defensive closed recheck, commit, seam probe, publish]
                seam_exc: BaseException | None = None
                with self._lock:
                    if self._closed:
                        raise DictionaryClosedError("runtime is closed")

                    write_conn.commit()
                    committed = True

                    if self._seam_probe is not None:
                        try:
                            self._seam_probe()
                        except BaseException as exc:
                            seam_exc = exc

                    old_generation = self._current_generation
                    old_generation.retired = True
                    if old_generation.pins == 0 and not old_generation.closed:
                        old_generation.closed = True
                        try:
                            old_generation.asset.close()
                        except Exception:
                            pass

                    self._generation_counter += 1
                    self._current_generation = _Generation(
                        generation_id=self._generation_counter,
                        asset=candidate_asset,
                        pins=0,
                        retired=False,
                        closed=False,
                    )

                if seam_exc is not None:
                    raise seam_exc

            finally:
                if not committed:
                    if write_conn is not None:
                        try:
                            if self._rollback_failure_hook is not None:
                                self._rollback_failure_hook()
                            write_conn.rollback()
                        except Exception:
                            pass
                        try:
                            write_conn.close()
                        except Exception:
                            pass
                    if candidate_asset is not None:
                        try:
                            candidate_asset.close()
                        except Exception:
                            pass
                else:
                    if write_conn is not None:
                        try:
                            if self._writer_close_hook is not None:
                                self._writer_close_hook()
                            write_conn.close()
                        except Exception:
                            pass

    def _relink_part_b(
        self, conn: sqlite3.Connection, asset: DictionaryAsset, now_text: str
    ) -> None:
        notes = conn.execute("SELECT id, status FROM note").fetchall()
        note_status_map = {int(r[0]): str(r[1]) for r in notes}

        binding_rows = conn.execute(
            """
            SELECT note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
                   cached_lemma_id, cached_sense_id, binding_status, component_count
            FROM note_dictionary_binding
            ORDER BY note_id, role, component_ord
            """
        ).fetchall()

        direct_bindings: dict[int, list[sqlite3.Row]] = {}
        component_bindings: dict[int, list[sqlite3.Row]] = {}
        for r in binding_rows:
            nid = int(r[0])
            role = str(r[1])
            if role == "direct":
                direct_bindings.setdefault(nid, []).append(r)
            elif role == "component":
                component_bindings.setdefault(nid, []).append(r)

        for note_id, current_status in note_status_map.items():
            direct_rows = direct_bindings.get(note_id, [])
            comp_rows = component_bindings.get(note_id, [])

            if direct_rows:
                for r in direct_rows:
                    ord_val = int(r[2])
                    sense_ref = str(r[4])
                    target_sense = asset.sense_ids.get(sense_ref)
                    if target_sense is not None:
                        sense_id, lemma_id = target_sense
                        conn.execute(
                            """
                            UPDATE note_dictionary_binding
                            SET cached_lemma_id = ?, cached_sense_id = ?, binding_status = 'bound',
                                last_relinked_at = ?
                            WHERE note_id = ? AND role = 'direct' AND component_ord = ?
                            """,
                            (lemma_id, sense_id, now_text, note_id, ord_val),
                        )
                        if current_status in ("resolved", "derived_compound", "needs_gloss"):
                            conn.execute(
                                "UPDATE note SET status = 'resolved' WHERE id = ?", (note_id,)
                            )
                    else:
                        conn.execute(
                            """
                            UPDATE note_dictionary_binding
                            SET cached_lemma_id = NULL, cached_sense_id = NULL,
                                binding_status = 'unbound', last_relinked_at = ?
                            WHERE note_id = ? AND role = 'direct' AND component_ord = ?
                            """,
                            (now_text, note_id, ord_val),
                        )
                        if current_status in ("resolved", "derived_compound", "needs_gloss"):
                            conn.execute(
                                "UPDATE note SET status = 'needs_gloss' WHERE id = ?", (note_id,)
                            )

            if comp_rows:
                expected_count = comp_rows[0][8]
                is_valid_vector = (
                    isinstance(expected_count, int)
                    and not isinstance(expected_count, bool)
                    and expected_count > 0
                    and len(comp_rows) == expected_count
                    and [r[2] for r in comp_rows] == list(range(expected_count))
                    and all(r[8] == expected_count for r in comp_rows)
                )

                if not is_valid_vector:
                    for r in comp_rows:
                        ord_val = int(r[2])
                        conn.execute(
                            """
                            UPDATE note_dictionary_binding
                            SET cached_lemma_id = NULL, cached_sense_id = NULL,
                                binding_status = 'ambiguous', last_relinked_at = ?
                            WHERE note_id = ? AND role = 'component' AND component_ord = ?
                            """,
                            (now_text, note_id, ord_val),
                        )
                    if current_status in ("resolved", "derived_compound", "needs_gloss"):
                        conn.execute(
                            "UPDATE note SET status = 'needs_gloss' WHERE id = ?", (note_id,)
                        )
                else:
                    all_found = True
                    matched_components: list[tuple[int, int, int]] = []
                    for r in comp_rows:
                        ord_val = int(r[2])
                        sense_ref = str(r[4])
                        target_sense = asset.sense_ids.get(sense_ref)
                        if target_sense is None:
                            all_found = False
                            break
                        matched_components.append((ord_val, target_sense[1], target_sense[0]))

                    if all_found:
                        for ord_val, lemma_id, sense_id in matched_components:
                            conn.execute(
                                """
                                UPDATE note_dictionary_binding
                                SET cached_lemma_id = ?, cached_sense_id = ?,
                                    binding_status = 'bound', last_relinked_at = ?
                                WHERE note_id = ? AND role = 'component' AND component_ord = ?
                                """,
                                (lemma_id, sense_id, now_text, note_id, ord_val),
                            )
                        if current_status in ("resolved", "derived_compound", "needs_gloss"):
                            conn.execute(
                                "UPDATE note SET status = 'derived_compound' WHERE id = ?",
                                (note_id,),
                            )
                    else:
                        for r in comp_rows:
                            ord_val = int(r[2])
                            conn.execute(
                                """
                                UPDATE note_dictionary_binding
                                SET cached_lemma_id = NULL, cached_sense_id = NULL,
                                    binding_status = 'unbound', last_relinked_at = ?
                                WHERE note_id = ? AND role = 'component' AND component_ord = ?
                                """,
                                (now_text, note_id, ord_val),
                            )
                        if current_status in ("resolved", "derived_compound", "needs_gloss"):
                            conn.execute(
                                "UPDATE note SET status = 'needs_gloss' WHERE id = ?", (note_id,)
                            )

    def close(self) -> None:
        """Idempotently close the runtime, retiring and releasing unpinned assets."""
        # Phase (1): same-thread reentrancy refusal
        if getattr(self._thread_local, "depth", 0) > 0:
            raise DictionaryRuntimeError("same-thread reentrancy is forbidden")

        with self._activation_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                old_generation = self._current_generation
                old_generation.retired = True
                if old_generation.pins == 0 and not old_generation.closed:
                    old_generation.closed = True
                    try:
                        old_generation.asset.close()
                    except Exception:
                        pass

    def __enter__(self) -> DictionaryRuntime:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        self.close()
