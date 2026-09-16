"""Tests for ADR-0002 §5 stateless two-stage capture and CSV import endpoints."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deck
from app.api import create_app
from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref

AUTH_HEADERS = {
    "Host": "127.0.0.1:8000",
    "Origin": "http://127.0.0.1:8000",
    "X-Flashcards-Request": "1",
    "Content-Type": "application/json",
}


@pytest.fixture
def test_setup(
    tmp_path: Path,
    create_test_db: Callable[..., Path],
) -> tuple[Path, Path]:
    dict_path = create_test_db(populate=True)
    conn_dict = sqlite3.connect(dict_path)
    try:
        senses = conn_dict.execute(
            "SELECT s.id, l.semantic_ref, s.source_namespace, s.source_ref "
            "FROM sense s JOIN lemma l ON s.lemma_id = l.id"
        ).fetchall()
        for sid, lref, ns, sref in senses:
            sem_ref = compute_sense_semantic_ref(lref, ns, sref)
            conn_dict.execute("UPDATE sense SET semantic_ref = ? WHERE id = ?", (sem_ref, sid))
        conn_dict.commit()
    finally:
        conn_dict.close()

    user_db_path = tmp_path / "user.sqlite"
    conn = sqlite3.connect(user_db_path)
    schema_path = Path(__file__).resolve().parent.parent / "reference" / "schema.sql"
    _, _, part_b = schema_path.read_text(encoding="utf-8").partition("-- PART B")
    conn.executescript("-- PART B" + part_b)
    conn.commit()
    conn.close()
    return dict_path, user_db_path


@pytest.fixture
def app_instance(test_setup: tuple[Path, Path]) -> Generator[TestClient, None, None]:
    dict_path, user_db_path = test_setup
    app = create_app(
        dict_path=dict_path,
        user_db_path=user_db_path,
        cors_origins=["http://127.0.0.1:8000"],
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client(app_instance: TestClient) -> TestClient:
    return app_instance


@pytest.fixture
def user_db(test_setup: tuple[Path, Path]) -> Path:
    return test_setup[1]


# ===========================================================================
# 1. POST /vocab/highlight tests
# ===========================================================================


def test_highlight_sentence_span_bounds_validation(client: TestClient, user_db: Path) -> None:
    """Validate sentence_text and selected_span bounds validation on /vocab/highlight."""
    # 1. Missing sentence_text
    resp = client.post(
        "/vocab/highlight",
        json={"selected_span": {"start": 0, "end": 4}, "lesson_label": "L1"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422

    # 2. Invalid span types
    resp = client.post(
        "/vocab/highlight",
        json={
            "sentence_text": "Das ist ein Haus.",
            "selected_span": {"start": "0", "end": 4},
            "lesson_label": "L1",
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422

    # 3. Out-of-bounds span (end > len)
    resp = client.post(
        "/vocab/highlight",
        json={
            "sentence_text": "Das ist ein Haus.",
            "selected_span": {"start": 0, "end": 50},
            "lesson_label": "L1",
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422

    # 4. Inverted span (start > end)
    resp = client.post(
        "/vocab/highlight",
        json={
            "sentence_text": "Das ist ein Haus.",
            "selected_span": {"start": 10, "end": 5},
            "lesson_label": "L1",
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422

    # 5. Missing / blank lesson_label
    resp = client.post(
        "/vocab/highlight",
        json={
            "sentence_text": "Das ist ein Haus.",
            "selected_span": {"start": 12, "end": 16},
            "lesson_label": "   ",
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422

    # Zero writes on failure
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    conn.close()


def test_highlight_success_and_candidate_resolution(client: TestClient, user_db: Path) -> None:
    """Happy path: resolve highlight to candidate list with context and zero writes."""
    sent = "Das ist ein schönes Haus in der Stadt."
    # Span for "Haus" (index 20..24)
    start = sent.index("Haus")
    end = start + len("Haus")

    resp = client.post(
        "/vocab/highlight",
        json={
            "sentence_text": sent,
            "selected_span": {"start": start, "end": end},
            "lesson_label": "Lektion 04",
            "lesson_id": "lek_04",
            "known_lemmas": ["das", "sein", "ein"],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "asset_token" in data
    assert "candidates" in data
    assert len(data["candidates"]) >= 1

    cand = data["candidates"][0]
    assert cand["lemma"] == "Haus"
    assert cand["pos"] == "NOUN"
    assert cand["gender"] == "das"
    assert cand["ref"] == compute_lemma_semantic_ref("Haus", "NOUN", "das")
    assert len(cand["senses"]) >= 1

    ctx = data["capture_context"]
    assert ctx["sentence_text"] == sent
    assert ctx["selected_span"] == {"start": start, "end": end}
    assert ctx["lesson_label"] == "Lektion 04"
    assert ctx["provenance"]["lesson_id"] == "lek_04"

    # Zero writes to user DB
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 0
    conn.close()


def test_highlight_preserves_multi_gender_candidate_identity(client: TestClient) -> None:
    """Resolved highlight candidates retain their POS and gender identity."""
    sent = "Der See ist tief."
    start = sent.index("See")
    end = start + len("See")

    resp = client.post(
        "/vocab/highlight",
        json={
            "sentence_text": sent,
            "selected_span": {"start": start, "end": end},
            "lesson_label": "Lektion 04",
        },
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 200
    candidates = resp.json()["candidates"]
    assert len(candidates) == 2
    assert len({candidate["lemma_semantic_ref"] for candidate in candidates}) == 2
    assert {candidate["gender"] for candidate in candidates} == {"der", "die"}
    assert {candidate["senses"][0]["gloss"] for candidate in candidates} == {
        "lake",
        "sea, ocean",
    }


# ===========================================================================
# 2. POST /vocab/cards tests
# ===========================================================================


def test_cards_stale_asset_token_rejection(client: TestClient, user_db: Path) -> None:
    """Blocker 3: Reject stale asset token with HTTP 409 conflict and zero writes."""
    hl_resp = client.get("/vocab/lookup?q=Haus", headers={"Host": "127.0.0.1:8000"})
    cand = hl_resp.json()["candidates"][0]
    haus_lem_ref = cand.get("ref") or cand.get("lemma_semantic_ref")
    haus_sense_ref = cand["senses"][0]["sense_semantic_ref"]

    resp = client.post(
        "/vocab/cards",
        json={
            "asset_token": "stale_token_1234567890",
            "deck": "Lektion 1",
            "selections": [
                {
                    "ref": haus_lem_ref,
                    "sense_ref": haus_sense_ref,
                }
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Asset token mismatch; dictionary has changed"

    # Zero writes
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 0
    conn.close()


def test_cards_unrelated_sense_lemma_rejection(client: TestClient, user_db: Path) -> None:
    """Blocker 2: Reject sense that does not belong to the submitted lemma (HTTP 422)."""
    hl_resp = client.get("/vocab/lookup?q=Haus", headers={"Host": "127.0.0.1:8000"})
    h_data = hl_resp.json()
    active_token = h_data["asset_token"]
    h_cand = h_data["candidates"][0]
    haus_lem_ref = h_cand.get("ref") or h_cand.get("lemma_semantic_ref")

    k_resp = client.get("/vocab/lookup?q=Karte", headers={"Host": "127.0.0.1:8000"})
    karte_sense_ref = k_resp.json()["candidates"][0]["senses"][0]["sense_semantic_ref"]

    # Submit Haus lemma with Karte sense
    resp = client.post(
        "/vocab/cards",
        json={
            "asset_token": active_token,
            "deck": "Lektion 1",
            "selections": [
                {
                    "ref": haus_lem_ref,
                    "sense_ref": karte_sense_ref,
                }
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422
    assert "does not belong to lemma" in resp.json()["detail"]

    # Zero writes
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    conn.close()


def test_cards_duplicate_selections_rejection(client: TestClient, user_db: Path) -> None:
    """Reject duplicate same-identity selections in a single request."""
    hl_resp = client.get("/vocab/lookup?q=Haus", headers={"Host": "127.0.0.1:8000"})
    h_data = hl_resp.json()
    active_token = h_data["asset_token"]
    cand = h_data["candidates"][0]
    haus_lem_ref = cand.get("ref") or cand.get("lemma_semantic_ref")
    haus_sense_ref = cand["senses"][0]["sense_semantic_ref"]

    resp = client.post(
        "/vocab/cards",
        json={
            "asset_token": active_token,
            "deck": "Lektion 1",
            "selections": [
                {"ref": haus_lem_ref, "sense_ref": haus_sense_ref},
                {"ref": haus_lem_ref, "sense_ref": haus_sense_ref},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422
    assert "Duplicate same-identity" in resp.json()["detail"]

    # Zero writes
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    conn.close()


def test_cards_atomic_rollback_on_failure(client: TestClient, user_db: Path) -> None:
    """Blocker 1: Verify transaction is genuinely atomic with rollback on failure."""
    hl_resp = client.get("/vocab/lookup?q=Haus", headers={"Host": "127.0.0.1:8000"})
    h_data = hl_resp.json()
    active_token = h_data["asset_token"]
    h_cand = h_data["candidates"][0]
    haus_lem_ref = h_cand.get("ref") or h_cand.get("lemma_semantic_ref")
    haus_sense_ref = h_cand["senses"][0]["sense_semantic_ref"]

    k_resp = client.get("/vocab/lookup?q=Karte", headers={"Host": "127.0.0.1:8000"})
    k_cand = k_resp.json()["candidates"][0]
    karte_lem_ref = k_cand.get("ref") or k_cand.get("lemma_semantic_ref")

    # Invalid sense for second item: pass haus_sense_ref for karte_lem_ref
    resp = client.post(
        "/vocab/cards",
        json={
            "asset_token": active_token,
            "deck": "Lektion 1",
            "selections": [
                {"ref": haus_lem_ref, "sense_ref": haus_sense_ref},
                {"ref": karte_lem_ref, "sense_ref": haus_sense_ref},
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 422

    # Zero writes for BOTH items
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 0
    conn.close()


def test_cards_creation_and_reuse_with_overrides(client: TestClient, user_db: Path) -> None:
    """Happy path: create note, reuse note, apply user meanings and overrides."""
    hl_resp = client.get("/vocab/lookup?q=Haus", headers={"Host": "127.0.0.1:8000"})
    h_data = hl_resp.json()
    active_token = h_data["asset_token"]
    h_cand = h_data["candidates"][0]
    haus_lem_ref = h_cand.get("ref") or h_cand.get("lemma_semantic_ref")
    haus_sense_ref = h_cand["senses"][0]["sense_semantic_ref"]

    # 1. Create note in Deck A with user meaning
    resp = client.post(
        "/vocab/cards",
        json={
            "asset_token": active_token,
            "deck": "Deck A",
            "selections": [
                {
                    "ref": haus_lem_ref,
                    "sense_ref": haus_sense_ref,
                    "overrides": {
                        "meaning_langs": ["en"],
                        "user_meanings": {"en": "my special house"},
                    },
                }
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["notes"]) == 1
    assert data["notes"][0]["created"] is True
    note_id = data["notes"][0]["note_id"]

    # Verify DB state
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 1
    um_row = conn.execute(
        "SELECT meaning_text FROM note_user_meaning WHERE note_id = ? AND lang = 'en'",
        (note_id,),
    ).fetchone()
    assert um_row[0] == "my special house"
    conn.close()

    # 2. Reuse note in Deck B, update user meaning
    resp2 = client.post(
        "/vocab/cards",
        json={
            "asset_token": active_token,
            "deck": "Deck B",
            "selections": [
                {
                    "ref": haus_lem_ref,
                    "sense_ref": haus_sense_ref,
                    "overrides": {
                        "user_meanings": {"en": "updated house meaning"},
                    },
                }
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert len(data2["notes"]) == 1
    assert data2["notes"][0]["created"] is False  # Reused!
    assert data2["notes"][0]["note_id"] == note_id

    # Verify DB state: still 1 note, 2 decks, 2 memberships
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 2
    um_row2 = conn.execute(
        "SELECT meaning_text FROM note_user_meaning WHERE note_id = ? AND lang = 'en'",
        (note_id,),
    ).fetchone()
    assert um_row2[0] == "updated house meaning"
    conn.close()


# ===========================================================================
# 3. POST /vocab/import/csv tests
# ===========================================================================


def test_import_csv_happy_path_and_atomic_rollback(client: TestClient, user_db: Path) -> None:
    """Test CSV import happy path and atomic rollback on error."""
    # 1. Invalid meaning language -> atomic rollback
    resp_fail = client.post(
        "/vocab/import/csv",
        json={
            "csv_text": "Haus\nKarte",
            "deck_name": "CSV Deck",
            "meaning_languages": ["fa"],
        },
        headers=AUTH_HEADERS,
    )
    assert resp_fail.status_code == 422

    # Verify zero writes
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 0
    conn.close()

    # 2. Valid CSV import
    csv_payload = "Haus\nKarte\nUnbekanntesWort"
    resp_ok = client.post(
        "/vocab/import/csv",
        json={
            "csv_text": csv_payload,
            "deck_name": "CSV Deck",
            "meaning_languages": ["en"],
        },
        headers=AUTH_HEADERS,
    )
    assert resp_ok.status_code == 201
    data = resp_ok.json()
    assert data["notes_created"] == 3
    assert data["total_words"] == 3

    # Verify DB state
    conn = sqlite3.connect(user_db)
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM note_deck").fetchone()[0] == 3
    conn.close()


# ===========================================================================
# 4. Direct Standalone Helper Tests
# ===========================================================================


def test_standalone_deck_mutations_manage_transactions(user_db: Path) -> None:
    """Ensure direct callers of deck mutating functions commit standalone transactions."""
    conn = sqlite3.connect(user_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # 1. create_deck commits standalone
    deck_id = deck.create_deck(conn, "Standalone Deck")
    assert deck_id > 0

    # Reopen connection to verify persisted commit
    conn2 = sqlite3.connect(user_db)
    row = conn2.execute("SELECT name FROM deck WHERE id = ?", (deck_id,)).fetchone()
    assert row is not None and row[0] == "Standalone Deck"

    # 2. create_note commits standalone
    note_id = deck.create_note(
        conn,
        lemma_semantic_ref="lemma:v1:test_lemma",
        meaning_languages=["en"],
    )
    assert note_id > 0
    row_n = conn2.execute("SELECT id FROM note WHERE id = ?", (note_id,)).fetchone()
    assert row_n is not None and row_n[0] == note_id

    # 3. add_note_to_deck commits standalone
    deck.add_note_to_deck(conn, note_id, deck_id)
    row_nd = conn2.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?", (note_id, deck_id)
    ).fetchone()
    assert row_nd is not None

    # 4. set_user_meaning and set_meaning_languages commit standalone
    deck.set_user_meaning(conn, note_id, "en", "my direct meaning")
    row_um = conn2.execute(
        "SELECT meaning_text FROM note_user_meaning WHERE note_id = ? AND lang = 'en'", (note_id,)
    ).fetchone()
    assert row_um is not None and row_um[0] == "my direct meaning"

    deck.set_meaning_languages(conn, note_id, ["de", "en"])
    langs = [
        r[0]
        for r in conn2.execute(
            "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang", (note_id,)
        ).fetchall()
    ]
    assert langs == ["de", "en"]

    # 5. delete_user_meaning commits standalone
    deck.delete_user_meaning(conn, note_id, "en")
    row_del = conn2.execute(
        "SELECT 1 FROM note_user_meaning WHERE note_id = ? AND lang = 'en'", (note_id,)
    ).fetchone()
    assert row_del is None

    conn.close()
    conn2.close()
