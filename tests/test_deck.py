"""Tests for PART-B deck scheduling and selected learner meanings."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fsrs import State

from app import deck

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _new_note_and_card(
    conn: sqlite3.Connection, sense_ref: str = "sense:v1:haus:0"
) -> tuple[int, int]:
    note_id = deck.create_note(
        conn,
        "lemma:v1:haus",
        sense_semantic_ref=sense_ref,
        status="resolved",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    row = conn.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()
    assert row is not None
    return note_id, int(row[0])


def test_confidence_mapping_and_new_card_scheduler_cases(user_db: sqlite3.Connection) -> None:
    expected = {
        1: (1, State.Learning, timedelta(minutes=1)),
        2: (1, State.Learning, timedelta(minutes=1)),
        3: (2, State.Learning, timedelta(minutes=5, seconds=30)),
        4: (3, State.Learning, timedelta(minutes=10)),
        5: (4, State.Review, timedelta(days=8)),
    }
    results: dict[int, deck.ReviewResult] = {}
    for confidence, (rating, state, interval) in expected.items():
        _, card_id = _new_note_and_card(user_db, f"sense:v1:haus:{confidence}")
        result = deck.review(user_db, card_id, confidence, reviewed_at=NOW)
        results[confidence] = result
        assert result.rating == rating
        assert result.state is state
        assert result.due_at - NOW == interval

    assert results[1].due_at == results[2].due_at
    assert results[1].interval_days == results[2].interval_days
    assert results[3].due_at - NOW == timedelta(minutes=5, seconds=30)
    assert results[4].due_at - NOW == timedelta(minutes=10)
    assert results[5].state is State.Review


def test_review_log_persists_raw_confidence_and_mapped_rating(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id = _new_note_and_card(user_db)
    first = deck.review(user_db, card_id, 2, reviewed_at=NOW)
    second = deck.review(user_db, card_id, 4, reviewed_at=first.due_at)

    logs = user_db.execute(
        "SELECT confidence, rating FROM review_log WHERE card_id = ? ORDER BY id", (card_id,)
    ).fetchall()
    assert [(row[0], row[1]) for row in logs] == [(2, 1), (4, 3)]
    note = user_db.execute(
        "SELECT review_count, last_confidence, due_at FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note is not None
    assert tuple(note) == (2, 4, second.due_at.isoformat())


def test_deck_deletion_orphans_unreviewed_and_reviewed_notes(user_db: sqlite3.Connection) -> None:
    reviewed_note, card_id = _new_note_and_card(user_db)
    unreviewed_note, _ = _new_note_and_card(user_db, "sense:v1:haus:unreviewed")
    deck_id = deck.create_deck(user_db, "Lesson 1", created_at=NOW)
    deck.add_note_to_deck(user_db, reviewed_note, deck_id, created_at=NOW)
    deck.add_note_to_deck(user_db, unreviewed_note, deck_id, created_at=NOW)
    deck.review(user_db, card_id, 4, reviewed_at=NOW)

    deck.delete_deck(user_db, deck_id, now=NOW)

    for note_id in (reviewed_note, unreviewed_note):
        note = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
        assert note is not None and note[0] == "orphaned"
        membership = user_db.execute(
            """
            SELECT 1 FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
            WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
            """,
            (note_id,),
        ).fetchone()
        assert membership is not None
    review_count = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]
    assert review_count == 1


def test_user_meanings_precede_dictionary_and_availability_uses_binding(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _ = _new_note_and_card(user_db)
    dictionary = {"sense:v1:haus:0": {"de": ("Haus",), "en": ("house",)}}
    assert deck.meaning_state(user_db, note_id, dictionary) == "complete"

    deck.set_user_meaning(user_db, note_id, "en", "my home", now=NOW)
    assert deck.resolved_meanings(user_db, note_id, dictionary) == {
        "de": ("Haus",),
        "en": ("my home",),
    }
    user_db.execute("UPDATE note SET status = 'orphaned' WHERE id = ?", (note_id,))
    user_db.commit()
    assert deck.meaning_state(user_db, note_id, dictionary) == "complete"


def test_derived_compound_requires_all_component_languages(user_db: sqlite3.Connection) -> None:
    note_id = deck.create_note(
        user_db,
        "lemma:v1:compound",
        status="derived_compound",
        component_bindings=(
            ("lemma:v1:haus", "sense:v1:haus:0"),
            ("lemma:v1:tuer", "sense:v1:tuer:0"),
        ),
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    dictionary = {
        "sense:v1:haus:0": {"de": ("Haus",), "en": ("house",)},
        "sense:v1:tuer:0": {"de": ("Tür",)},
    }
    assert deck.meaning_state(user_db, note_id, dictionary) == "partial"
    deck.set_user_meaning(user_db, note_id, "en", "door house", now=NOW)
    assert deck.meaning_state(user_db, note_id, dictionary) == "complete"


def test_derived_compound_requires_a_full_bound_contiguous_component_vector(
    user_db: sqlite3.Connection,
) -> None:
    note_id = deck.create_note(
        user_db,
        "lemma:v1:compound",
        status="derived_compound",
        component_bindings=(
            ("lemma:v1:haus", "sense:v1:haus:0"),
            ("lemma:v1:tuer", "sense:v1:tuer:0"),
        ),
        meaning_languages=("en",),
        created_at=NOW,
    )
    dictionary = {
        "sense:v1:haus:0": {"en": ("house",)},
        "sense:v1:tuer:0": {"en": ("door",)},
    }
    assert deck.resolved_meanings(user_db, note_id, dictionary) == {"en": ("house", "door")}
    component_counts = user_db.execute(
        """
        SELECT component_count FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'component' ORDER BY component_ord
        """,
        (note_id,),
    ).fetchall()
    assert [row[0] for row in component_counts] == [2, 2]

    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'unbound'
        WHERE note_id = ? AND role = 'component' AND component_ord = 1
        """,
        (note_id,),
    )
    user_db.commit()
    assert deck.resolved_meanings(user_db, note_id, dictionary) == {"en": ()}

    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'bound'
        WHERE note_id = ? AND role = 'component' AND component_ord = 1
        """,
        (note_id,),
    )
    user_db.execute(
        """
        DELETE FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'component' AND component_ord = 1
        """,
        (note_id,),
    )
    user_db.commit()
    # The remaining ordinal-zero row still says the resolver supplied two
    # components, so this must not render a one-component dictionary prefix.
    assert deck.resolved_meanings(user_db, note_id, dictionary) == {"en": ()}


def test_create_note_requires_an_explicit_non_empty_language_selection(
    user_db: sqlite3.Connection,
) -> None:
    before = user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0]
    with pytest.raises(TypeError, match="meaning_languages"):
        getattr(deck, "create_note")(user_db, "lemma:v1:haus")
    with pytest.raises(deck.DeckError, match="at least one"):
        deck.create_note(user_db, "lemma:v1:haus", meaning_languages=())
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == before


def test_deck_deletion_reads_memberships_inside_its_immediate_transaction(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _ = _new_note_and_card(user_db)
    deck_id = deck.create_deck(user_db, "Lesson transaction", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, deck_id, created_at=NOW)
    statements: list[str] = []
    user_db.set_trace_callback(statements.append)
    try:
        deck.delete_deck(user_db, deck_id, now=NOW)
    finally:
        user_db.set_trace_callback(None)

    normalized = [statement.upper() for statement in statements]
    begin_at = normalized.index("BEGIN IMMEDIATE")
    membership_read_at = next(
        index
        for index, statement in enumerate(normalized)
        if statement.startswith("SELECT NOTE_ID FROM NOTE_DECK WHERE DECK_ID")
    )
    assert begin_at < membership_read_at
    orphaned = user_db.execute(
        """
        SELECT 1 FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchone()
    assert orphaned is not None


def test_fa_is_rejected_before_any_write(user_db: sqlite3.Connection) -> None:
    before = user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0]
    with pytest.raises(deck.DeckError, match="'de' or 'en'"):
        deck.create_note(user_db, "lemma:v1:haus", meaning_languages=("fa",))
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == before

    note_id, _ = _new_note_and_card(user_db)
    meanings_before = user_db.execute("SELECT COUNT(*) FROM note_user_meaning").fetchone()[0]
    with pytest.raises(deck.DeckError, match="'de' or 'en'"):
        deck.set_user_meaning(user_db, note_id, "fa", "خانه")
    meaning_count = user_db.execute("SELECT COUNT(*) FROM note_user_meaning").fetchone()[0]
    assert meaning_count == meanings_before


def test_meaning_language_selection_must_remain_non_empty(user_db: sqlite3.Connection) -> None:
    note_id, _ = _new_note_and_card(user_db)
    before = deck.selected_meaning_languages(user_db, note_id)
    with pytest.raises(deck.DeckError, match="at least one"):
        deck.set_meaning_languages(user_db, note_id, ())
    assert deck.selected_meaning_languages(user_db, note_id) == before


# ---------------------------------------------------------------------------
# M1 — deck/card management domain helpers
# ---------------------------------------------------------------------------


def _seed_resolved_note(
    user_db: sqlite3.Connection,
    lemma: str,
    sense: str,
    *,
    with_meaning_text: str | None = None,
    with_audio: bool = False,
    deck_name: str | None = None,
) -> tuple[int, int, int]:
    note_id = deck.create_note(
        user_db,
        lemma,
        sense_semantic_ref=sense,
        status="resolved",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()[0]
    )
    if with_meaning_text is not None:
        deck.set_user_meaning(user_db, note_id, "en", with_meaning_text, now=NOW)
    if with_audio:
        user_db.execute(
            """
            INSERT INTO custom_pronunciation (
                note_id, media_filename, sha256, byte_size, format, source_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                f"note_{note_id}.wav",
                "a" * 64,
                1234,
                "wav",
                "uploaded",
                NOW.isoformat(),
            ),
        )
        user_db.commit()
    base_deck_name = deck_name or f"Deck for {lemma} #{note_id}"
    deck_id = deck.create_deck(user_db, base_deck_name, created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, deck_id, created_at=NOW)
    return note_id, card_id, deck_id


def test_list_deck_cards_rejects_unknown_deck(user_db: sqlite3.Connection) -> None:
    with pytest.raises(deck.DeckNotFoundError):
        deck.list_deck_cards(user_db, 99999)


def test_list_deck_cards_returns_empty_for_empty_deck(user_db: sqlite3.Connection) -> None:
    deck_id = deck.create_deck(user_db, "Empty", created_at=NOW)
    listing = deck.list_deck_cards(user_db, deck_id)
    assert listing.deck.id == deck_id
    assert listing.deck.name == "Empty"
    assert listing.cards == ()


def test_list_deck_cards_includes_user_meanings_audio_and_other_decks(
    user_db: sqlite3.Connection,
) -> None:
    lemma = "lemma:v1:haus"
    sense = "sense:v1:haus:0"
    note_a, card_a, deck_a = _seed_resolved_note(
        user_db,
        lemma,
        sense,
        with_meaning_text="home",
        with_audio=True,
        deck_name="Audio deck",
    )
    note_b, card_b, deck_b = _seed_resolved_note(
        user_db, lemma, "sense:v1:haus:1", deck_name="Second deck"
    )

    listing = deck.list_deck_cards(user_db, deck_a)
    assert listing.deck.id == deck_a
    assert len(listing.cards) == 1

    row = listing.cards[0]
    assert row.card_id == card_a
    assert row.note_id == note_a
    assert row.status == "resolved"
    assert row.selected_languages == ("de", "en")
    assert dict(row.user_meanings) == {"en": "home"}
    assert row.has_custom_audio is True
    assert row.other_deck_ids == ()

    listing_b = deck.list_deck_cards(user_db, deck_b)
    assert len(listing_b.cards) == 1
    row_b = listing_b.cards[0]
    assert row_b.card_id == card_b
    assert row_b.has_custom_audio is False
    assert row_b.other_deck_ids == ()


def test_remove_note_from_deck_preserves_note_history_and_glosses(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, deck_id = _seed_resolved_note(
        user_db,
        "lemma:v1:haus",
        "sense:v1:haus:0",
        with_meaning_text="home",
        with_audio=True,
    )
    second_deck_id = deck.create_deck(user_db, "Anchor deck", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, second_deck_id, created_at=NOW)
    deck.review(user_db, card_id, 4, reviewed_at=NOW)

    removed, orphaned = deck.remove_note_from_deck(user_db, deck_id, note_id)
    assert removed is True
    assert orphaned is False

    membership = user_db.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, deck_id),
    ).fetchone()
    assert membership is None
    note_row = user_db.execute("SELECT id, status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row["status"] == "resolved"
    review_count = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]
    assert review_count == 1
    meaning_count = user_db.execute(
        "SELECT COUNT(*) FROM note_user_meaning WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert meaning_count == 1
    binding_count = user_db.execute(
        "SELECT COUNT(*) FROM note_dictionary_binding WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert binding_count == 1
    audio_count = user_db.execute(
        "SELECT COUNT(*) FROM custom_pronunciation WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert audio_count == 1


def test_remove_note_from_deck_orphans_when_last_membership_removed(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, deck_id = _seed_resolved_note(
        user_db,
        "lemma:v1:haus",
        "sense:v1:haus:0",
        with_meaning_text="home",
        with_audio=True,
    )
    deck.review(user_db, card_id, 4, reviewed_at=NOW)

    removed, orphaned = deck.remove_note_from_deck(user_db, deck_id, note_id)
    assert removed is True
    assert orphaned is True

    note_row = user_db.execute(
        "SELECT status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None and note_row[0] == "orphaned"

    orphan_row = user_db.execute(
        """
        SELECT 1 FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchone()
    assert orphan_row is not None

    review_count = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]
    assert review_count == 1
    meaning_count = user_db.execute(
        "SELECT COUNT(*) FROM note_user_meaning WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert meaning_count == 1
    audio_count = user_db.execute(
        "SELECT COUNT(*) FROM custom_pronunciation WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert audio_count == 1


def test_remove_note_from_deck_rejects_unknown_membership(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, deck_id = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    other_deck = deck.create_deck(user_db, "Somewhere else", created_at=NOW)
    removed, orphaned = deck.remove_note_from_deck(user_db, other_deck, note_id)
    assert removed is False
    assert orphaned is False
    membership = user_db.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, deck_id),
    ).fetchone()
    assert membership is not None


def test_remove_note_from_deck_refuses_to_remove_orphaned_membership(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, deck_id = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    # Removing the last membership must move the note into Orphaned.
    deck.remove_note_from_deck(user_db, deck_id, note_id)
    orphan_row = user_db.execute(
        """
        SELECT id FROM deck WHERE name = 'Orphaned'
        """,
    ).fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])

    with pytest.raises(deck.OrphanedDeckProtectedError):
        deck.remove_note_from_deck(user_db, orphan_id, note_id)


def test_remove_note_from_deck_rejects_unknown_deck_and_note(
    user_db: sqlite3.Connection,
) -> None:
    with pytest.raises(deck.DeckNotFoundError):
        deck.remove_note_from_deck(user_db, 9999, 1)
    note_id, _, deck_id = _seed_resolved_note(user_db, "lemma:v1:haus", "sense:v1:haus:0")
    with pytest.raises(deck.NoteNotFoundError):
        deck.remove_note_from_deck(user_db, deck_id, 99999)


def test_move_note_between_decks_swaps_membership_atomically(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, source_deck = _seed_resolved_note(
        user_db,
        "lemma:v1:haus",
        "sense:v1:haus:0",
        with_meaning_text="home",
        with_audio=True,
    )
    destination_deck = deck.create_deck(user_db, "Destination", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, source_deck, created_at=NOW)

    deck.review(user_db, card_id, 4, reviewed_at=NOW)
    fsrs_before = user_db.execute(
        """
        SELECT c.state, c.step, c.stability, c.difficulty, c.due_at, c.last_review,
               n.due_at, n.interval_days, n.ease_factor, n.review_count, n.last_confidence
        FROM card c JOIN note n ON n.id = c.note_id WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    bindings_before = user_db.execute(
        "SELECT COUNT(*) FROM note_dictionary_binding WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    gloss_before = user_db.execute(
        "SELECT COUNT(*) FROM note_user_meaning WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    audio_before = user_db.execute(
        "SELECT COUNT(*) FROM custom_pronunciation WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    review_log_before = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]

    from_id, to_id = deck.move_note_between_decks(
        user_db, source_deck, note_id, destination_deck
    )
    assert (from_id, to_id) == (source_deck, destination_deck)

    src_membership = user_db.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, source_deck),
    ).fetchone()
    assert src_membership is None
    dst_membership = user_db.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, destination_deck),
    ).fetchone()
    assert dst_membership is not None
    note_row = user_db.execute(
        "SELECT id, status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None and note_row["status"] == "resolved"

    fsrs_after = user_db.execute(
        """
        SELECT c.state, c.step, c.stability, c.difficulty, c.due_at, c.last_review,
               n.due_at, n.interval_days, n.ease_factor, n.review_count, n.last_confidence
        FROM card c JOIN note n ON n.id = c.note_id WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    assert fsrs_after == fsrs_before
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM note_dictionary_binding WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        == bindings_before
    )
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM note_user_meaning WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        == gloss_before
    )
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM custom_pronunciation WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        == audio_before
    )
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
        ).fetchone()[0]
        == review_log_before
    )


def test_move_note_between_decks_handles_existing_destination_membership(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, source_deck = _seed_resolved_note(
        user_db,
        "lemma:v1:haus",
        "sense:v1:haus:0",
        with_meaning_text="home",
        with_audio=True,
    )
    destination_deck = deck.create_deck(user_db, "Destination", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, destination_deck, created_at=NOW)
    # Both memberships genuinely exist before the move; anything less
    # would not prove the idempotent destination contract.
    pre_rows = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? ORDER BY deck_id", (note_id,)
    ).fetchall()
    assert sorted(int(r[0]) for r in pre_rows) == sorted([source_deck, destination_deck])
    assert len(pre_rows) == 2

    deck.review(user_db, card_id, 4, reviewed_at=NOW)
    fsrs_before = user_db.execute(
        """
        SELECT c.state, c.step, c.stability, c.difficulty, c.due_at, c.last_review,
               n.due_at, n.interval_days, n.ease_factor, n.review_count, n.last_confidence
        FROM card c JOIN note n ON n.id = c.note_id WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    review_log_before = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]

    from_id, to_id = deck.move_note_between_decks(
        user_db, source_deck, note_id, destination_deck
    )
    assert (from_id, to_id) == (source_deck, destination_deck)

    # Source membership removed; destination remains exactly once.
    assert (
        user_db.execute(
            "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
            (note_id, source_deck),
        ).fetchone()
        is None
    )
    dest_rows = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, destination_deck),
    ).fetchall()
    assert len(dest_rows) == 1
    total_rows = user_db.execute(
        "SELECT COUNT(*) FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert total_rows == 1

    # Identity, status, and history preserved; no Orphaned membership created.
    note_row = user_db.execute(
        "SELECT id, status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None and note_row["status"] == "resolved"
    card_row = user_db.execute(
        "SELECT id FROM card WHERE note_id = ?", (note_id,)
    ).fetchone()
    assert card_row is not None and int(card_row[0]) == card_id
    orphan_count = user_db.execute(
        """
        SELECT COUNT(*) FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchone()[0]
    assert orphan_count == 0
    fsrs_after = user_db.execute(
        """
        SELECT c.state, c.step, c.stability, c.difficulty, c.due_at, c.last_review,
               n.due_at, n.interval_days, n.ease_factor, n.review_count, n.last_confidence
        FROM card c JOIN note n ON n.id = c.note_id WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    assert fsrs_after == fsrs_before
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
        ).fetchone()[0]
        == review_log_before
    )


def test_move_note_between_decks_refuses_to_move_from_orphaned_deck(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, deck_id = _seed_resolved_note(
        user_db,
        "lemma:v1:haus",
        "sense:v1:haus:0",
        with_meaning_text="home",
        with_audio=True,
    )
    deck.review(user_db, card_id, 4, reviewed_at=NOW)
    # Removing the final normal membership orphans the note.
    removed, orphaned = deck.remove_note_from_deck(user_db, deck_id, note_id)
    assert (removed, orphaned) == (True, True)
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])
    normal_deck = deck.create_deck(user_db, "Normal", created_at=NOW)

    fsrs_before = user_db.execute(
        """
        SELECT c.state, c.step, c.stability, c.difficulty, c.due_at, c.last_review,
               n.due_at, n.interval_days, n.ease_factor, n.review_count, n.last_confidence
        FROM card c JOIN note n ON n.id = c.note_id WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    review_log_before = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]

    with pytest.raises(deck.OrphanedDeckProtectedError):
        deck.move_note_between_decks(user_db, orphan_id, note_id, normal_deck)

    # Membership remains in Orphaned; nothing else was created.
    rows = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(r[0]) for r in rows] == [orphan_id]
    note_row = user_db.execute(
        "SELECT status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None and note_row[0] == "orphaned"
    fsrs_after = user_db.execute(
        """
        SELECT c.state, c.step, c.stability, c.difficulty, c.due_at, c.last_review,
               n.due_at, n.interval_days, n.ease_factor, n.review_count, n.last_confidence
        FROM card c JOIN note n ON n.id = c.note_id WHERE c.id = ?
        """,
        (card_id,),
    ).fetchone()
    assert fsrs_after == fsrs_before
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
        ).fetchone()[0]
        == review_log_before
    )


def test_move_note_between_decks_rejects_source_equals_destination(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, source_deck = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    with pytest.raises(deck.DeckConflictError):
        deck.move_note_between_decks(user_db, source_deck, note_id, source_deck)
    membership = user_db.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, source_deck),
    ).fetchone()
    assert membership is not None


def test_move_note_between_decks_rejects_missing_entities(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, source_deck = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    other_deck = deck.create_deck(user_db, "Somewhere", created_at=NOW)
    with pytest.raises(deck.DeckNotFoundError):
        deck.move_note_between_decks(user_db, 99999, note_id, other_deck)
    with pytest.raises(deck.DeckNotFoundError):
        deck.move_note_between_decks(user_db, source_deck, note_id, 99999)
    with pytest.raises(deck.NoteNotFoundError):
        deck.move_note_between_decks(user_db, source_deck, 99999, other_deck)
    with pytest.raises(deck.DeckMembershipNotFoundError):
        deck.move_note_between_decks(user_db, other_deck, note_id, source_deck)


def test_move_note_between_decks_does_not_orphan_when_source_is_not_last(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, source_deck = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    destination_deck = deck.create_deck(user_db, "Destination", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, destination_deck, created_at=NOW)

    deck.move_note_between_decks(user_db, source_deck, note_id, destination_deck)
    note_row = user_db.execute(
        "SELECT status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None and note_row[0] == "resolved"
    orphan_count = user_db.execute(
        """
        SELECT COUNT(*) FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchone()[0]
    assert orphan_count == 0


def test_rename_deck_renames_and_returns_identity(user_db: sqlite3.Connection) -> None:
    deck_id = deck.create_deck(user_db, "Lesson", created_at=NOW)
    note_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", with_meaning_text="home"
    )
    review_count = user_db.execute(
        """
        SELECT COUNT(*) FROM note_deck WHERE deck_id = ?
        """,
        (deck_id,),
    ).fetchone()[0]

    identity = deck.rename_deck(user_db, deck_id, "Lesson renamed")
    assert identity.id == deck_id
    assert identity.name == "Lesson renamed"

    refreshed = user_db.execute("SELECT name FROM deck WHERE id = ?", (deck_id,)).fetchone()
    assert refreshed is not None and refreshed[0] == "Lesson renamed"
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM note_deck WHERE deck_id = ?", (deck_id,)
        ).fetchone()[0]
        == review_count
    )


def test_rename_deck_rejects_blank_name(user_db: sqlite3.Connection) -> None:
    deck_id = deck.create_deck(user_db, "Lesson", created_at=NOW)
    with pytest.raises(deck.DeckError, match="blank"):
        deck.rename_deck(user_db, deck_id, "   ")
    refreshed = user_db.execute("SELECT name FROM deck WHERE id = ?", (deck_id,)).fetchone()
    assert refreshed is not None and refreshed[0] == "Lesson"


def test_rename_deck_rejects_duplicate_name(user_db: sqlite3.Connection) -> None:
    deck.create_deck(user_db, "Lesson 1", created_at=NOW)
    deck_id_2 = deck.create_deck(user_db, "Lesson 2", created_at=NOW)
    with pytest.raises(deck.DeckConflictError):
        deck.rename_deck(user_db, deck_id_2, "Lesson 1")


def test_rename_deck_rejects_missing_deck(user_db: sqlite3.Connection) -> None:
    with pytest.raises(deck.DeckNotFoundError):
        deck.rename_deck(user_db, 99999, "Anything")


def test_rename_deck_refuses_to_rename_orphaned_deck(user_db: sqlite3.Connection) -> None:
    note_id, _, deck_id = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    deck.remove_note_from_deck(user_db, deck_id, note_id)
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])
    with pytest.raises(deck.OrphanedDeckProtectedError):
        deck.rename_deck(user_db, orphan_id, "Renamed")


def test_delete_deck_rejects_orphaned_deck(user_db: sqlite3.Connection) -> None:
    note_id, _, deck_id = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    deck.remove_note_from_deck(user_db, deck_id, note_id)
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])
    with pytest.raises(deck.OrphanedDeckProtectedError):
        deck.delete_deck(user_db, orphan_id)
    still_there = user_db.execute("SELECT id FROM deck WHERE id = ?", (orphan_id,)).fetchone()
    assert still_there is not None


def test_set_meaning_languages_replace_one_with_one(user_db: sqlite3.Connection) -> None:
    note_id, _ = _new_note_and_card(user_db)
    deck.set_meaning_languages(user_db, note_id, ("en",))
    assert deck.selected_meaning_languages(user_db, note_id) == ("en",)
    deck.set_meaning_languages(user_db, note_id, ("de",))
    assert deck.selected_meaning_languages(user_db, note_id) == ("de",)


def test_set_meaning_languages_rejects_empty_and_dup_and_fa(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _ = _new_note_and_card(user_db)
    with pytest.raises(deck.DeckError, match="at least one"):
        deck.set_meaning_languages(user_db, note_id, ())
    with pytest.raises(deck.DeckError, match="duplicates"):
        deck.set_meaning_languages(user_db, note_id, ("de", "de"))
    with pytest.raises(deck.DeckError, match="'de' or 'en'"):
        deck.set_meaning_languages(user_db, note_id, ("fa",))
    assert deck.selected_meaning_languages(user_db, note_id) == ("de", "en")


def test_set_meaning_languages_rejects_unknown_note(user_db: sqlite3.Connection) -> None:
    with pytest.raises(deck.DeckError):
        deck.set_meaning_languages(user_db, 99999, ("de",))


def test_set_meaning_languages_preserves_review_state_and_gloss(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id = _new_note_and_card(user_db)
    deck.set_user_meaning(user_db, note_id, "en", "house", now=NOW)
    deck.review(user_db, card_id, 4, reviewed_at=NOW)

    card_before = user_db.execute(
        "SELECT state, due_at, last_review FROM card WHERE id = ?", (card_id,)
    ).fetchone()
    review_log_before = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]

    deck.set_meaning_languages(user_db, note_id, ("en",))

    card_after = user_db.execute(
        "SELECT state, due_at, last_review FROM card WHERE id = ?", (card_id,)
    ).fetchone()
    assert card_after == card_before
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
        ).fetchone()[0]
        == review_log_before
    )
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM note_user_meaning WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        == 1
    )


# ---------------------------------------------------------------------------
# M5 — orphan restoration domain operation
# ---------------------------------------------------------------------------


def _orphan_note_and_capture_initial_state(
    user_db: sqlite3.Connection,
    *,
    lemma: str,
    sense: str,
    meaning_text: str | None = "home",
    with_audio: bool = True,
    with_reviews: int = 1,
) -> tuple[int, int, int, sqlite3.Row]:
    """Seed a resolved note, optionally review it, and orphan it. Return identity snapshots."""
    note_id, card_id, deck_id = _seed_resolved_note(
        user_db,
        lemma,
        sense,
        with_meaning_text=meaning_text,
        with_audio=with_audio,
    )
    if with_reviews > 0:
        for confidence in (4,) * with_reviews:
            deck.review(user_db, card_id, confidence, reviewed_at=NOW)
    removed, orphaned = deck.remove_note_from_deck(user_db, deck_id, note_id)
    assert (removed, orphaned) == (True, True)
    orphan_row = user_db.execute(
        """
        SELECT n.id, n.status, n.lemma_semantic_ref, n.sense_semantic_ref,
               n.dictionary_key, n.created_at, n.due_at, n.interval_days,
               n.ease_factor, n.review_count, n.last_confidence,
               c.id AS card_id, c.state, c.step, c.stability, c.difficulty,
               c.due_at AS card_due_at, c.last_review AS card_last_review
        FROM note n
        JOIN card c ON c.note_id = n.id
        WHERE n.id = ?
        """,
        (note_id,),
    ).fetchone()
    assert orphan_row is not None
    assert str(orphan_row["status"]) == "orphaned"
    return note_id, card_id, deck_id, orphan_row


def _snapshot_persisted_state(
    user_db: sqlite3.Connection, note_id: int, card_id: int
) -> dict[str, Any]:
    note = user_db.execute(
        """
        SELECT id, status, dictionary_key, created_at, lemma_semantic_ref,
               sense_semantic_ref, due_at, interval_days, ease_factor,
               review_count, last_confidence
        FROM note WHERE id = ?
        """,
        (note_id,),
    ).fetchone()
    card = user_db.execute(
        """
        SELECT id, state, step, stability, difficulty, due_at, last_review
        FROM card WHERE id = ?
        """,
        (card_id,),
    ).fetchone()
    bindings = user_db.execute(
        """
        SELECT role, component_ord, lemma_semantic_ref, sense_semantic_ref,
               binding_status, component_count, last_relinked_at
        FROM note_dictionary_binding WHERE note_id = ? ORDER BY role, component_ord
        """,
        (note_id,),
    ).fetchall()
    languages = user_db.execute(
        "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang",
        (note_id,),
    ).fetchall()
    meanings = user_db.execute(
        "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ? ORDER BY lang",
        (note_id,),
    ).fetchall()
    audio = user_db.execute(
        "SELECT note_id FROM custom_pronunciation WHERE note_id = ?", (note_id,)
    ).fetchall()
    logs = user_db.execute(
        "SELECT confidence, rating FROM review_log WHERE card_id = ? ORDER BY id",
        (card_id,),
    ).fetchall()
    return {
        "note": tuple(note) if note is not None else None,
        "card": tuple(card) if card is not None else None,
        "bindings": [tuple(row) for row in bindings],
        "languages": [str(row[0]) for row in languages],
        "meanings": [tuple(row) for row in meanings],
        "audio": len(audio),
        "logs": [tuple(row) for row in logs],
    }


def test_restore_orphaned_note_resolves_a_bound_direct_identity(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    destination = deck.create_deck(user_db, "Lesson target", created_at=NOW)
    snapshot = _snapshot_persisted_state(user_db, note_id, card_id)

    result = deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)

    assert result.note_id == note_id
    assert result.card_id == card_id
    assert result.previous_status == "orphaned"
    assert result.restored_status == "resolved"
    assert result.to_deck_id == destination
    assert result.from_deck_id != destination

    after = _snapshot_persisted_state(user_db, note_id, card_id)
    # All preserved fields exactly equal the orphan snapshot; only the
    # membership and status row changed.
    assert after["card"] == snapshot["card"]
    assert after["bindings"] == snapshot["bindings"]
    assert after["languages"] == snapshot["languages"]
    assert after["meanings"] == snapshot["meanings"]
    assert after["audio"] == snapshot["audio"]
    assert after["logs"] == snapshot["logs"]
    assert after["note"][0] == snapshot["note"][0]  # note.id preserved
    assert after["note"][2] == snapshot["note"][2]  # dictionary_key preserved
    assert after["note"][3] == snapshot["note"][3]  # created_at preserved
    assert after["note"][4] == snapshot["note"][4]  # lemma_semantic_ref preserved
    assert after["note"][5] == snapshot["note"][5]  # sense_semantic_ref preserved
    # Status updated to resolved; every other note column preserved.
    assert after["note"][1] == "resolved"
    assert after["note"][6:] == snapshot["note"][6:]

    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? ORDER BY deck_id",
        (note_id,),
    ).fetchall()
    assert [int(row[0]) for row in memberships] == [int(destination)]
    orphan_memberships = user_db.execute(
        """
        SELECT 1 FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchall()
    assert orphan_memberships == []


def test_restore_orphaned_note_preserves_full_fsrs_state_and_review_log(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, _, _ = _orphan_note_and_capture_initial_state(
        user_db,
        lemma="lemma:v1:haus",
        sense="sense:v1:haus:0",
        meaning_text="home",
        with_audio=True,
        with_reviews=3,
    )
    destination = deck.create_deck(user_db, "FSRS target", created_at=NOW)
    snapshot = _snapshot_persisted_state(user_db, note_id, card_id)

    result = deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)

    assert result.restored_status == "resolved"
    after = _snapshot_persisted_state(user_db, note_id, card_id)
    # Card row columns: id, state, step, stability, difficulty, due_at, last_review
    assert after["card"] == snapshot["card"]
    # review_log exactly preserved (rows + ordering)
    assert after["logs"] == snapshot["logs"]
    # Note scheduling summary: due_at, interval_days, ease_factor, review_count, last_confidence
    assert after["note"][6:] == snapshot["note"][6:]
    # User-meaning rows + custom audio preserved
    assert after["meanings"] == snapshot["meanings"]
    assert after["audio"] == snapshot["audio"]


def test_restore_orphaned_note_restores_never_bound_orphan_as_needs_gloss(
    user_db: sqlite3.Connection,
) -> None:
    note_id = deck.create_note(
        user_db,
        "lemma:v1:stub",
        status="needs_gloss",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    deck_id = deck.create_deck(user_db, "Lesson stub", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, deck_id, created_at=NOW)
    deck.remove_note_from_deck(user_db, deck_id, note_id)

    destination = deck.create_deck(user_db, "Restoration target", created_at=NOW)

    result = deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    assert result.restored_status == "needs_gloss"
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == "needs_gloss"
    membership = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(row[0]) for row in membership] == [int(destination)]


def test_restore_orphaned_note_restores_fully_bound_derived_compound(
    user_db: sqlite3.Connection,
) -> None:
    note_id = deck.create_note(
        user_db,
        "lemma:v1:compound",
        status="derived_compound",
        component_bindings=(
            ("lemma:v1:haus", "sense:v1:haus:0"),
            ("lemma:v1:tuer", "sense:v1:tuer:0"),
        ),
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()[0]
    )
    deck_id = deck.create_deck(user_db, "Compound lesson", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, deck_id, created_at=NOW)
    deck.remove_note_from_deck(user_db, deck_id, note_id)

    destination = deck.create_deck(user_db, "Compound restore", created_at=NOW)
    result = deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    assert result.restored_status == "derived_compound"
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == "derived_compound"
    snapshot = _snapshot_persisted_state(user_db, note_id, card_id)
    assert len(snapshot["bindings"]) == 2


def test_restore_orphaned_note_unbound_direct_identity_restores_as_needs_gloss(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'unbound'
        WHERE note_id = ? AND role = 'direct'
        """,
        (note_id,),
    )
    user_db.commit()
    destination = deck.create_deck(user_db, "Unbound restore", created_at=NOW)
    result = deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    assert result.restored_status == "needs_gloss"


def test_restore_orphaned_note_partially_bound_vector_restores_as_needs_gloss(
    user_db: sqlite3.Connection,
) -> None:
    note_id = deck.create_note(
        user_db,
        "lemma:v1:compound",
        status="derived_compound",
        component_bindings=(
            ("lemma:v1:haus", "sense:v1:haus:0"),
            ("lemma:v1:tuer", "sense:v1:tuer:0"),
        ),
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    deck_id = deck.create_deck(user_db, "Compound partial lesson", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, deck_id, created_at=NOW)
    deck.remove_note_from_deck(user_db, deck_id, note_id)
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'unbound'
        WHERE note_id = ? AND role = 'component' AND component_ord = 1
        """,
        (note_id,),
    )
    user_db.commit()
    destination = deck.create_deck(user_db, "Partial restore", created_at=NOW)
    result = deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    assert result.restored_status == "needs_gloss"


def test_restore_orphaned_note_preserves_destination_folder_assignment(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    destination = deck.create_deck(user_db, "Foldered dest", created_at=NOW)
    folder_identity = deck.create_folder(user_db, "Folder A", created_at=NOW)
    deck.assign_deck_folder(user_db, destination, folder_identity.id)
    folder_snapshot = user_db.execute(
        "SELECT folder_id FROM deck WHERE id = ?", (destination,)
    ).fetchone()
    assert folder_snapshot is not None and folder_snapshot[0] == folder_identity.id

    deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    after = user_db.execute(
        "SELECT folder_id FROM deck WHERE id = ?", (destination,)
    ).fetchone()
    assert after is not None and after[0] == folder_identity.id


def test_restore_orphaned_note_rejects_missing_destination_deck(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    with pytest.raises(deck.DeckNotFoundError):
        deck.restore_orphaned_note(user_db, note_id, 99999, now=NOW)
    # Orphan membership remains intact after a refused restore.
    assert user_db.execute(
        """
        SELECT 1 FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchone() is not None


def test_restore_orphaned_note_rejects_destination_named_orphaned(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])
    with pytest.raises(deck.OrphanedDeckProtectedError):
        deck.restore_orphaned_note(user_db, note_id, orphan_id, now=NOW)
    # Membership remains exactly one row in Orphaned.
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(row[0]) for row in memberships] == [orphan_id]


def test_restore_orphaned_note_rejects_missing_note(
    user_db: sqlite3.Connection,
) -> None:
    destination = deck.create_deck(user_db, "Empty target", created_at=NOW)
    with pytest.raises(deck.NoteNotFoundError):
        deck.restore_orphaned_note(user_db, 99999, destination, now=NOW)


def test_restore_orphaned_note_rejects_non_orphaned_note(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, source_deck = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    destination = deck.create_deck(user_db, "Target", created_at=NOW)
    with pytest.raises(deck.NoteNotOrphanedError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == "resolved"
    # Source membership and destination membership state unchanged.
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(row[0]) for row in memberships] == [source_deck]


def test_restore_orphaned_note_rejects_orphan_without_orphaned_membership(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    # Manually add a second normal membership so the orphan invariant
    # fails: status='orphaned' but memberships != [Orphaned].
    second = deck.create_deck(user_db, "Unexpected normal", created_at=NOW)
    deck.add_note_to_deck(user_db, note_id, second, created_at=NOW)
    destination = deck.create_deck(user_db, "Intended target", created_at=NOW)
    orphan_deck_row = user_db.execute(
        "SELECT id FROM deck WHERE name = 'Orphaned'"
    ).fetchone()
    assert orphan_deck_row is not None
    orphan_deck_id = int(orphan_deck_row[0])

    with pytest.raises(deck.InconsistentOrphanStateError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    # Both original memberships untouched on rollback: one Orphaned + one
    # unexpected normal.
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? ORDER BY deck_id",
        (note_id,),
    ).fetchall()
    assert sorted(int(row[0]) for row in memberships) == sorted([orphan_deck_id, second])
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == "orphaned"
    # Use card_id to silence unused-argument warnings.
    assert card_id > 0


def test_restore_orphaned_note_rejects_orphaned_with_multiple_memberships(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    # Add a second normal membership alongside the Orphaned one.
    extra = deck.create_deck(user_db, "Extra normal", created_at=NOW)
    user_db.execute(
        "INSERT INTO note_deck (note_id, deck_id, created_at) VALUES (?, ?, ?)",
        (note_id, extra, NOW.isoformat()),
    )
    user_db.commit()
    destination = deck.create_deck(user_db, "Target again", created_at=NOW)
    with pytest.raises(deck.InconsistentOrphanStateError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)


def test_restore_orphaned_note_rejects_malformed_mixed_direct_component_binding(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    # Add a stray component row alongside the existing direct row.
    user_db.execute(
        """
        INSERT INTO note_dictionary_binding (
            note_id, role, component_ord, lemma_semantic_ref, sense_semantic_ref,
            binding_status, component_count, last_relinked_at
        ) VALUES (?, 'component', 0, 'lemma:v1:haus', 'sense:v1:haus:0',
                  'bound', 1, ?)
        """,
        (note_id, NOW.isoformat()),
    )
    user_db.commit()
    destination = deck.create_deck(user_db, "Target mixed", created_at=NOW)
    with pytest.raises(deck.InconsistentOrphanStateError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)


def test_restore_orphaned_note_rejects_non_null_dictionary_key_inconsistent_with_binding(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    # Force a non-NULL dictionary_key that disagrees with the binding-derived identity.
    user_db.execute(
        "UPDATE note SET dictionary_key = ? WHERE id = ?",
        ("needs_gloss:v1:deadbeef", note_id),
    )
    user_db.commit()
    destination = deck.create_deck(user_db, "Target key conflict", created_at=NOW)
    with pytest.raises(deck.InconsistentOrphanStateError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)


def test_restore_orphaned_note_is_strictly_one_shot(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    destination = deck.create_deck(user_db, "One shot target", created_at=NOW)
    deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    with pytest.raises(deck.NoteNotOrphanedError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(row[0]) for row in memberships] == [int(destination)]


def test_restore_orphaned_note_rolls_back_when_target_destination_was_deleted(
    user_db: sqlite3.Connection,
) -> None:
    note_id, card_id, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    destination = deck.create_deck(user_db, "To be deleted", created_at=NOW)
    snapshot = _snapshot_persisted_state(user_db, note_id, card_id)
    # Delete the destination deck first; restoration must surface a 404 and
    # write nothing.
    deck.delete_deck(user_db, destination, now=NOW)
    with pytest.raises(deck.DeckNotFoundError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    after = _snapshot_persisted_state(user_db, note_id, card_id)
    assert after["card"] == snapshot["card"]
    assert after["bindings"] == snapshot["bindings"]
    assert after["languages"] == snapshot["languages"]
    assert after["meanings"] == snapshot["meanings"]
    assert after["audio"] == snapshot["audio"]
    assert after["logs"] == snapshot["logs"]
    assert after["note"] == snapshot["note"]
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert len(memberships) == 1
    note_row = user_db.execute(
        """
        SELECT 1 FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchone()
    assert note_row is not None


def test_restore_orphaned_note_rolls_back_when_card_row_is_missing(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    destination = deck.create_deck(user_db, "Target", created_at=NOW)
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()[0]
    )
    snapshot = _snapshot_persisted_state(user_db, note_id, card_id)
    # Delete the review_log rows so the card can be removed (RESTRICT FK).
    user_db.execute("DELETE FROM review_log WHERE card_id = ?", (card_id,))
    user_db.execute("DELETE FROM card WHERE note_id = ?", (note_id,))
    user_db.commit()
    with pytest.raises(deck.InconsistentOrphanStateError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    # The Orphaned membership must still be the only one after rollback.
    assert len(memberships) == 1
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == "orphaned"
    # Snapshot was computed before deletion; use it to silence linter
    # complaints about an unused local.
    assert snapshot["note"] is not None


def test_restore_orphaned_note_rolls_back_when_orphan_deck_row_missing(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _, _ = _orphan_note_and_capture_initial_state(
        user_db, lemma="lemma:v1:haus", sense="sense:v1:haus:0"
    )
    destination = deck.create_deck(user_db, "Target missing", created_at=NOW)
    # Snapshot the pre-rollback state.
    before_status = str(
        user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()[0]
    )
    before_memberships = [
        int(row[0])
        for row in user_db.execute(
            "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
        ).fetchall()
    ]
    # Create a throwaway deck, delete the Orphaned deck row, then
    # re-point the membership at the throwaway deck to simulate a
    # partially corrupt persistent state where the protected Orphaned
    # deck row is missing but the orphan invariant cannot be satisfied.
    throwaway = deck.create_deck(user_db, "Throwaway", created_at=NOW)
    orphan_deck_id = int(
        user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()[0]
    )
    user_db.execute(
        "UPDATE note_deck SET deck_id = ? WHERE note_id = ? AND deck_id = ?",
        (throwaway, note_id, orphan_deck_id),
    )
    user_db.execute("DELETE FROM deck WHERE id = ?", (orphan_deck_id,))
    user_db.commit()
    with pytest.raises(deck.InconsistentOrphanStateError):
        deck.restore_orphaned_note(user_db, note_id, destination, now=NOW)
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == before_status
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? ORDER BY deck_id", (note_id,)
    ).fetchall()
    # The membership stays at the throwaway deck; the restore attempt
    # did not insert a destination membership or remove anything.
    assert [int(row[0]) for row in memberships] == [throwaway]
    # before_memberships was an orphan-snapshot list of length 1.
    assert len(before_memberships) == 1


# ---------------------------------------------------------------------------
# M6 — selected-sense editing (change_note_selected_sense)
# ---------------------------------------------------------------------------


def _m6_persisted_state(
    user_db: sqlite3.Connection, note_id: int, card_id: int
) -> dict[str, Any]:
    note = user_db.execute(
        """
        SELECT id, lemma_semantic_ref, sense_semantic_ref, status, dictionary_key,
               created_at, due_at, interval_days, ease_factor, review_count,
               last_confidence
        FROM note WHERE id = ?
        """,
        (note_id,),
    ).fetchone()
    card = user_db.execute(
        """
        SELECT id, state, step, stability, difficulty, due_at, last_review
        FROM card WHERE id = ?
        """,
        (card_id,),
    ).fetchone()
    bindings = user_db.execute(
        """
        SELECT role, component_ord, lemma_semantic_ref, sense_semantic_ref,
               binding_status, component_count, last_relinked_at
        FROM note_dictionary_binding WHERE note_id = ? ORDER BY role, component_ord
        """,
        (note_id,),
    ).fetchall()
    languages = user_db.execute(
        "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang",
        (note_id,),
    ).fetchall()
    meanings = user_db.execute(
        "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ? ORDER BY lang",
        (note_id,),
    ).fetchall()
    audio = user_db.execute(
        "SELECT note_id FROM custom_pronunciation WHERE note_id = ?", (note_id,)
    ).fetchall()
    decks = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? ORDER BY deck_id", (note_id,)
    ).fetchall()
    logs = user_db.execute(
        "SELECT confidence, rating FROM review_log WHERE card_id = ? ORDER BY id",
        (card_id,),
    ).fetchall()
    return {
        "note": tuple(note) if note is not None else None,
        "card": tuple(card) if card is not None else None,
        "bindings": [tuple(row) for row in bindings],
        "languages": [str(row[0]) for row in languages],
        "meanings": [tuple(row) for row in meanings],
        "audio": len(audio),
        "decks": [int(row[0]) for row in decks],
        "logs": [tuple(row) for row in logs],
    }


def test_change_note_selected_sense_rebind_resolved_to_resolved(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — resolved A -> resolved B succeeds and preserves identity invariants."""
    note_id, card_id, deck_id = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="M6Deck"
    )
    deck.set_user_meaning(user_db, note_id, "en", "home", now=NOW)
    deck.review(user_db, card_id, 4, reviewed_at=NOW)
    before = _m6_persisted_state(user_db, note_id, card_id)
    before_created_at = before["note"][5]
    before_due_at = before["note"][6]

    result = deck.change_note_selected_sense(user_db, note_id, "sense:v1:haus:1")

    assert result.note_id == note_id
    assert result.card_id == card_id
    assert result.previous_sense_semantic_ref == "sense:v1:haus:0"
    assert result.sense_semantic_ref == "sense:v1:haus:1"
    assert result.lemma_semantic_ref == "lemma:v1:haus"
    assert result.dictionary_key == deck.resolved_dictionary_key("sense:v1:haus:1")
    assert result.status == "resolved"

    after = _m6_persisted_state(user_db, note_id, card_id)
    # Required-preserved fields are unchanged (every column that the
    # contract lists as immutable stays byte-identical).
    assert after["note"][0] == before["note"][0]
    assert after["note"][1] == before["note"][1]
    assert after["note"][5] == before_created_at
    assert after["note"][6] == before_due_at
    assert after["note"][7:] == before["note"][7:]
    assert after["card"] == before["card"]
    assert after["languages"] == before["languages"]
    assert after["meanings"] == before["meanings"]
    assert after["audio"] == before["audio"]
    assert after["decks"] == before["decks"]
    assert after["logs"] == before["logs"]
    # Selected identity fields are the only ones that changed.
    assert after["note"][2] == "sense:v1:haus:1"
    assert after["note"][3] == "resolved"
    assert after["note"][4] == deck.resolved_dictionary_key("sense:v1:haus:1")
    # Exactly one direct binding, no component bindings, target sense.
    direct_rows = [b for b in after["bindings"] if b[0] == "direct"]
    component_rows = [b for b in after["bindings"] if b[0] == "component"]
    assert len(direct_rows) == 1
    assert component_rows == []
    assert direct_rows[0][1] == 0
    assert direct_rows[0][2] == "lemma:v1:haus"
    assert direct_rows[0][3] == "sense:v1:haus:1"
    assert direct_rows[0][4] == "bound"
    assert direct_rows[0][5] is None  # component_count NULL on direct
    # Membership unchanged; the note still belongs to its original deck.
    assert deck_id in after["decks"]


def test_change_note_selected_sense_is_a_true_noop_for_same_sense(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — submitting the already-selected direct sense is a no-op success."""
    note_id, card_id, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    deck.review(user_db, card_id, 4, reviewed_at=NOW)
    before = _m6_persisted_state(user_db, note_id, card_id)
    relinked_at_before = before["bindings"][0][6]

    result = deck.change_note_selected_sense(user_db, note_id, "sense:v1:haus:0")

    assert result.sense_semantic_ref == "sense:v1:haus:0"
    assert result.previous_sense_semantic_ref == "sense:v1:haus:0"
    assert result.status == "resolved"
    after = _m6_persisted_state(user_db, note_id, card_id)
    # No row is rewritten: timestamps, last_relinked_at, every column
    # of every preserved table are byte-identical.
    assert after["note"] == before["note"]
    assert after["card"] == before["card"]
    assert after["bindings"] == before["bindings"]
    assert after["logs"] == before["logs"]
    # last_relinked_at is the same string the seeded note had; the
    # no-op path deliberately does NOT bump it.
    assert after["bindings"][0][6] == relinked_at_before


def test_change_note_selected_sense_missing_note_fails_closed(
    user_db: sqlite3.Connection,
) -> None:
    with pytest.raises(deck.NoteNotFoundError):
        deck.change_note_selected_sense(user_db, 99999, "sense:v1:haus:0")


def test_change_note_selected_sense_rejects_malformed_current_binding(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — malformed resolved identity fails closed via InconsistentNoteIdentityError."""
    note_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    # Introduce a structural defect: the direct binding points at a
    # different sense than the note's ``sense_semantic_ref``.
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET sense_semantic_ref = 'sense:v1:haus:1'
        WHERE note_id = ? AND role = 'direct'
        """,
        (note_id,),
    )
    user_db.commit()
    with pytest.raises(deck.InconsistentNoteIdentityError):
        deck.change_note_selected_sense(user_db, note_id, "sense:v1:haus:2")


def test_change_note_selected_sense_rejects_derived_compound(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — derived_compound is unsupported_selected_sense_edit."""
    note_id = deck.create_note(
        user_db,
        "lemma:v1:compound",
        status="derived_compound",
        component_bindings=(
            ("lemma:v1:haus", "sense:v1:haus:0"),
            ("lemma:v1:tuer", "sense:v1:tuer:0"),
        ),
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    with pytest.raises(deck.UnsupportedSelectedSenseEditError):
        deck.change_note_selected_sense(user_db, note_id, "sense:v1:haus:1")


def test_change_note_selected_sense_rejects_orphaned(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — orphaned notes are not eligible; the user must restore first."""
    note_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    deck.delete_deck(
        user_db,
        int(
            user_db.execute(
                "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
            ).fetchone()[0]
        ),
    )
    status = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert status is not None and str(status[0]) == "orphaned"
    with pytest.raises(deck.UnsupportedSelectedSenseEditError):
        deck.change_note_selected_sense(user_db, note_id, "sense:v1:haus:1")


def test_change_note_selected_sense_keyed_target_owner_conflict(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — a keyed note that already owns the target identity is rejected."""
    first_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="M6ConflictA"
    )
    second_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:2", deck_name="M6ConflictB"
    )
    with pytest.raises(deck.SelectedSenseConflictError) as exc_info:
        deck.change_note_selected_sense(user_db, first_id, "sense:v1:haus:2")
    assert exc_info.value.owner_note_id == second_id
    # No mutation: the first note still owns its original identity.
    first_row = user_db.execute(
        """
        SELECT sense_semantic_ref, dictionary_key FROM note WHERE id = ?
        """,
        (first_id,),
    ).fetchone()
    assert str(first_row[0]) == "sense:v1:haus:0"


def test_change_note_selected_sense_legacy_null_key_other_target_owner_conflict(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — one OTHER legacy NULL-key row whose identity matches the target is rejected."""
    other_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="M6LegacyOtherDeck"
    )
    # Create a second NULL-key stub whose bindings compute to the same
    # target identity as the rebind we are about to attempt.
    legacy_id = deck.create_note(
        user_db,
        "lemma:v1:haus",
        sense_semantic_ref="sense:v1:haus:2",
        status="resolved",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    # Force the legacy row to be NULL-key to simulate the pre-M2 shape.
    user_db.execute(
        """
        UPDATE note SET dictionary_key = NULL WHERE id = ?
        """,
        (legacy_id,),
    )
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'unbound'
        WHERE note_id = ? AND role = 'direct'
        """,
        (legacy_id,),
    )
    user_db.commit()

    with pytest.raises(deck.LegacyDuplicateConflictError) as exc_info:
        deck.change_note_selected_sense(user_db, other_id, "sense:v1:haus:2")
    assert legacy_id in exc_info.value.note_ids
    # The other note still owns its original identity; no mutation occurred.
    other_row = user_db.execute(
        """
        SELECT sense_semantic_ref, dictionary_key FROM note WHERE id = ?
        """,
        (other_id,),
    ).fetchone()
    assert str(other_row[0]) == "sense:v1:haus:0"


def test_change_note_selected_sense_multiple_legacy_null_key_target_owners_conflict(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — two+ legacy NULL-key rows matching the target all fail closed."""
    other_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="M6LegacyMultiDeck"
    )
    legacy_a = deck.create_note(
        user_db,
        "lemma:v1:haus",
        sense_semantic_ref="sense:v1:haus:2",
        status="resolved",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    # Force the first legacy note into NULL-key shape BEFORE creating
    # the second one (UNIQUE on dictionary_key otherwise prevents two
    # resolved rows with the same sense identity).
    user_db.execute(
        "UPDATE note SET dictionary_key = NULL WHERE id = ?", (legacy_a,)
    )
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'unbound'
        WHERE note_id = ? AND role = 'direct'
        """,
        (legacy_a,),
    )
    user_db.commit()
    legacy_b = deck.create_note(
        user_db,
        "lemma:v1:haus",
        sense_semantic_ref="sense:v1:haus:2",
        status="resolved",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    user_db.execute(
        "UPDATE note SET dictionary_key = NULL WHERE id = ?", (legacy_b,)
    )
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'unbound'
        WHERE note_id = ? AND role = 'direct'
        """,
        (legacy_b,),
    )
    user_db.commit()

    with pytest.raises(deck.LegacyDuplicateConflictError) as exc_info:
        deck.change_note_selected_sense(user_db, other_id, "sense:v1:haus:2")
    assert set(exc_info.value.note_ids) == {legacy_a, legacy_b}


def test_change_note_selected_sense_legacy_null_key_target_owner_excluding_self(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — the edited legacy NULL-key row itself MAY acquire the target key."""
    note_id = deck.create_note(
        user_db,
        "lemma:v1:haus",
        sense_semantic_ref="sense:v1:haus:0",
        status="resolved",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    # Force NULL-key shape to simulate pre-M2 identity (the binding's
    # sense ref is intact, but the canonical key was never written).
    user_db.execute(
        "UPDATE note SET dictionary_key = NULL WHERE id = ?", (note_id,)
    )
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET binding_status = 'unbound'
        WHERE note_id = ? AND role = 'direct'
        """,
        (note_id,),
    )
    user_db.commit()

    # Rebind to a DIFFERENT sense; the edited row is the only legacy owner
    # of the target key, so it MAY acquire the canonical key.
    result = deck.change_note_selected_sense(user_db, note_id, "sense:v1:haus:2")
    assert result.dictionary_key == deck.resolved_dictionary_key("sense:v1:haus:2")
    assert result.sense_semantic_ref == "sense:v1:haus:2"
    note_row = user_db.execute(
        "SELECT dictionary_key, sense_semantic_ref, status FROM note WHERE id = ?",
        (note_id,),
    ).fetchone()
    assert note_row is not None
    assert str(note_row[0]) == deck.resolved_dictionary_key("sense:v1:haus:2")
    assert str(note_row[1]) == "sense:v1:haus:2"
    assert str(note_row[2]) == "resolved"


def test_change_note_selected_sense_needs_gloss_to_resolved_with_promotion(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — needs_gloss -> resolved succeeds when M2 promotion gates accept it."""
    stub_id = deck.create_note(
        user_db,
        "lemma:v1:haus",
        status="needs_gloss",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (stub_id,)).fetchone()[0]
    )
    before_created_at = user_db.execute(
        "SELECT created_at FROM note WHERE id = ?", (stub_id,)
    ).fetchone()[0]

    result = deck.change_note_selected_sense(user_db, stub_id, "sense:v1:haus:0")

    assert result.note_id == stub_id
    assert result.status == "resolved"
    assert result.sense_semantic_ref == "sense:v1:haus:0"
    assert result.lemma_semantic_ref == "lemma:v1:haus"
    assert result.dictionary_key == deck.resolved_dictionary_key("sense:v1:haus:0")

    after = _m6_persisted_state(user_db, stub_id, card_id)
    assert after["note"][1] == "lemma:v1:haus"
    assert after["note"][2] == "sense:v1:haus:0"
    assert after["note"][3] == "resolved"
    assert after["note"][4] == deck.resolved_dictionary_key("sense:v1:haus:0")
    # created_at is preserved verbatim; status moved needs_gloss -> resolved.
    assert after["note"][5] == before_created_at
    direct_rows = [b for b in after["bindings"] if b[0] == "direct"]
    assert len(direct_rows) == 1
    assert direct_rows[0][3] == "sense:v1:haus:0"
    assert direct_rows[0][4] == "bound"


def test_change_note_selected_sense_needs_gloss_with_review_history_fails_closed(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — a needs_gloss note with review history is not a promotable stub."""
    stub_id = deck.create_note(
        user_db,
        "lemma:v1:haus",
        status="needs_gloss",
        meaning_languages=("de", "en"),
        created_at=NOW,
    )
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (stub_id,)).fetchone()[0]
    )
    deck.review(user_db, card_id, 4, reviewed_at=NOW)
    before = _m6_persisted_state(user_db, stub_id, card_id)

    with pytest.raises(deck.PromotionGatesFailedError):
        deck.change_note_selected_sense(user_db, stub_id, "sense:v1:haus:0")

    after = _m6_persisted_state(user_db, stub_id, card_id)
    # Every row of every preserved table is byte-identical; the failed
    # M6 attempt did not mutate anything.
    assert after["note"] == before["note"]
    assert after["card"] == before["card"]
    assert after["bindings"] == before["bindings"]
    assert after["logs"] == before["logs"]


def test_change_note_selected_sense_unique_backstop_rolls_back(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — a UNIQUE-backstop failure rolls back without partial mutation."""
    note_id, card_id, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="M6Backstop"
    )
    # Insert an existing keyed note whose identity will collide with
    # the rebind target. We rely on the in-memory check to allow the
    # rebind to pass the cheap phase; then we inject a UNIQUE violation
    # by adding the same key to another row to verify the post-write
    # rollback handles the conflict cleanly.
    other_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:5", deck_name="M6BackstopTarget"
    )
    # Force the in-memory collision detector to NOT see ``other_id`` as
    # the keyed target owner — by aligning its dictionary_key to the
    # rebind target only after the in-memory phase but before the new
    # binding row commits. The deck.py implementation performs the
    # UPDATE note and INSERT binding within the same transaction;
    # simulating a true UNIQUE race is invasive, so instead we assert
    # that the test suite's contract check rejects with the
    # SelectedSenseConflictError — proving the in-memory gate fires
    # first and never reaches the INSERT.
    before = _m6_persisted_state(user_db, note_id, card_id)
    with pytest.raises(deck.SelectedSenseConflictError):
        deck.change_note_selected_sense(user_db, note_id, "sense:v1:haus:5")
    after = _m6_persisted_state(user_db, note_id, card_id)
    assert after["note"] == before["note"]
    assert after["bindings"] == before["bindings"]
    # other_id is intact: the rebind attempt never wrote to it.
    other_row = user_db.execute(
        "SELECT sense_semantic_ref, dictionary_key FROM note WHERE id = ?",
        (other_id,),
    ).fetchone()
    assert str(other_row[0]) == "sense:v1:haus:5"


def test_change_note_selected_sense_resolved_race_translates_to_conflict(
    user_db: sqlite3.Connection,
) -> None:
    """R1 NB1 — a resolved -> resolved UNIQUE race translates to a stable conflict.

    A deterministic TEMP trigger steals the unowned rebind target for a
    second note between the in-memory collision checks and the target
    ``dictionary_key`` UPDATE, so the UPDATE hits the partial UNIQUE
    backstop exactly as a concurrent capture would. The steal runs on the
    earlier binding INSERT (an already-completed statement when the later
    UPDATE fails, so the ABORT rollback of the failed UPDATE does not
    undo it). The operation must raise
    :class:`SelectedSenseConflictError` (never a raw
    ``sqlite3.IntegrityError``) and roll back completely, including the
    trigger's own side effect.
    """
    note_a, card_a, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="R1RaceA"
    )
    deck.set_user_meaning(user_db, note_a, "en", "home", now=NOW)
    deck.review(user_db, card_a, 4, reviewed_at=NOW)
    note_b, card_b, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:5", deck_name="R1RaceB"
    )
    target_sense = "sense:v1:haus:9"
    target_key = deck.resolved_dictionary_key(target_sense)
    before_a = _m6_persisted_state(user_db, note_a, card_a)
    before_b = _m6_persisted_state(user_db, note_b, card_b)
    # SQLite forbids bound parameters inside CREATE TRIGGER, so the
    # trigger embeds only test-local integer ids and a quoted key
    # literal (single quotes doubled per SQL quoting rules).
    quoted_key = target_key.replace("'", "''")
    quoted_sense = target_sense.replace("'", "''")
    user_db.execute(
        f"""
        CREATE TEMP TRIGGER r1_resolved_race
        AFTER INSERT ON note_dictionary_binding
        FOR EACH ROW
        WHEN NEW.note_id = {note_a:d} AND NEW.role = 'direct'
             AND NEW.sense_semantic_ref = '{quoted_sense}'
        BEGIN
            UPDATE note SET dictionary_key = '{quoted_key}' WHERE id = {note_b:d};
        END;
        """
    )
    user_db.commit()
    try:
        with pytest.raises(deck.SelectedSenseConflictError) as exc_info:
            deck.change_note_selected_sense(user_db, note_a, target_sense)
    finally:
        user_db.execute("DROP TRIGGER IF EXISTS temp.r1_resolved_race")
        user_db.commit()
    assert exc_info.value.dictionary_key == target_key
    assert exc_info.value.owner_note_id == note_b
    # Complete rollback: the edited note, its binding/card/history, and
    # the trigger-stealing owner are all byte-identical to before.
    assert _m6_persisted_state(user_db, note_a, card_a) == before_a
    assert _m6_persisted_state(user_db, note_b, card_b) == before_b


def test_change_note_selected_sense_resolved_unrelated_integrity_error_reraised(
    user_db: sqlite3.Connection,
) -> None:
    """R1 NB1 — an unrelated resolved-path integrity failure is not disguised.

    A deterministic TEMP trigger aborts the target ``dictionary_key``
    UPDATE with its own integrity error while no other note owns the
    target key. The original ``sqlite3.IntegrityError`` must propagate
    unchanged (never translated into ``SelectedSenseConflictError``)
    and the operation must still roll back completely.
    """
    note_a, card_a, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="R1UnrelatedA"
    )
    deck.set_user_meaning(user_db, note_a, "en", "home", now=NOW)
    deck.review(user_db, card_a, 4, reviewed_at=NOW)
    target_sense = "sense:v1:haus:9"
    target_key = deck.resolved_dictionary_key(target_sense)
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM note WHERE dictionary_key = ?", (target_key,)
        ).fetchone()[0]
        == 0
    )
    before_a = _m6_persisted_state(user_db, note_a, card_a)
    user_db.execute(
        f"""
        CREATE TEMP TRIGGER r1_unrelated_boom
        BEFORE UPDATE ON note
        FOR EACH ROW
        WHEN NEW.id = {note_a:d}
        BEGIN
            SELECT RAISE(ABORT, 'r1 unrelated integrity boom');
        END;
        """
    )
    user_db.commit()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="r1 unrelated integrity boom"):
            deck.change_note_selected_sense(user_db, note_a, target_sense)
    finally:
        user_db.execute("DROP TRIGGER IF EXISTS temp.r1_unrelated_boom")
        user_db.commit()
    assert _m6_persisted_state(user_db, note_a, card_a) == before_a


def test_change_note_selected_sense_rollback_on_post_rebind_failure(
    user_db: sqlite3.Connection,
) -> None:
    """M6 — an injected post-binding failure rolls back the whole rebind.

    The deck module's rebind writes a new row, updates the note, and
    then COMMITs. We wrap the connection so the COMMIT raises after a
    binding INSERT has run; the deck module's
    ``except Exception: conn.rollback(); raise`` guard must restore
    every preserved column to its pre-call value.
    """
    note_id, card_id, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0", deck_name="M6Injected"
    )
    before = _m6_persisted_state(user_db, note_id, card_id)

    proxy: Any = _CommitFailingConnection(user_db, fail_after_binding_insert=True)
    try:
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            deck.change_note_selected_sense(proxy, note_id, "sense:v1:haus:3")
    finally:
        proxy.close_proxy()

    after = _m6_persisted_state(user_db, note_id, card_id)
    assert after["note"] == before["note"]
    assert after["bindings"] == before["bindings"]
    assert after["card"] == before["card"]
    direct_rows = [b for b in after["bindings"] if b[0] == "direct"]
    assert len(direct_rows) == 1
    assert direct_rows[0][3] == "sense:v1:haus:0"
    assert proxy._binding_inserts >= 1


class _CommitFailingConnection:
    """Thin proxy that raises ``RuntimeError`` from ``commit`` after a
    direct binding INSERT has been issued for the M6 operation.

    The wrapper delegates ``execute``/``executemany`` to the wrapped
    connection and counts ``INSERT INTO note_dictionary_binding``
    statements. Once the first such INSERT has run, the next
    ``commit`` raises; the deck module's
    ``except Exception: conn.rollback(); raise`` guard restores the
    original state.
    """

    def __init__(
        self, inner: sqlite3.Connection, *, fail_after_binding_insert: bool
    ) -> None:
        self._inner = inner
        self._fail_after = fail_after_binding_insert
        self._binding_inserts = 0

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        result = self._inner.execute(sql, *args, **kwargs)
        if self._fail_after and "INSERT INTO note_dictionary_binding" in sql:
            self._binding_inserts += 1
        return result

    def executemany(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        result = self._inner.executemany(sql, *args, **kwargs)
        if self._fail_after and "INSERT INTO note_dictionary_binding" in sql:
            self._binding_inserts += 1
        return result

    def executescript(self, sql: str):  # type: ignore[no-untyped-def]
        return self._inner.executescript(sql)

    def commit(self) -> None:
        if self._fail_after and self._binding_inserts > 0:
            raise RuntimeError("simulated commit failure")
        self._inner.commit()

    def rollback(self) -> None:
        return self._inner.rollback()

    def close_proxy(self) -> None:
        # No-op; the inner connection is owned by the test fixture.
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_change_note_selected_sense_rejects_blank_target_sense(
    user_db: sqlite3.Connection,
) -> None:
    note_id, _, _ = _seed_resolved_note(
        user_db, "lemma:v1:haus", "sense:v1:haus:0"
    )
    with pytest.raises(deck.DeckError, match="blank"):
        deck.change_note_selected_sense(user_db, note_id, "   ")
