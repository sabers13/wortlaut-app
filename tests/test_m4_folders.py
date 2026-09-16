"""M4 folder persistence domain and backend API tests.

Covers the folder domain operations in ``app.deck`` and the M4 folder
backend API contracts. No dictionary asset or network access is required:
folder operations are pure PART-B user-database behaviour.

All databases are temporary fixtures. The owner's real production database
is never inspected or mutated here.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import deck
from app.api import create_app

NOW = "2026-01-01T00:00:00+00:00"

AUTH_HEADERS = {
    "X-Flashcards-Request": "1",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def folder_app(user_db_path: Path) -> Any:
    """An app with no dictionary bound; folder endpoints need only PART-B."""
    return create_app(
        user_db_path=user_db_path,
        cors_origins=["http://127.0.0.1:5173"],
    )


@pytest.fixture
def folder_client(folder_app: Any) -> Generator[TestClient, None, None]:
    with TestClient(folder_app, base_url="http://127.0.0.1:8000") as client:
        yield client


# ---------------------------------------------------------------------------
# Domain operations
# ---------------------------------------------------------------------------


def test_create_folder_and_deterministic_list(user_db: sqlite3.Connection) -> None:
    first = deck.create_folder(user_db, "Grammar")
    second = deck.create_folder(user_db, "Vocab")
    assert (first.id, first.name) == (1, "Grammar")
    assert second.id == 2
    assert deck.list_folders(user_db) == [first, second]


def test_create_folder_trims_and_preserves_case(user_db: sqlite3.Connection) -> None:
    folder = deck.create_folder(user_db, "  Grammar  ")
    assert folder.name == "Grammar"


def test_create_folder_rejects_blank(user_db: sqlite3.Connection) -> None:
    with pytest.raises(deck.DeckError):
        deck.create_folder(user_db, "   ")
    assert deck.list_folders(user_db) == []


def test_create_folder_duplicate_exact_name_conflicts(
    user_db: sqlite3.Connection,
) -> None:
    deck.create_folder(user_db, "Grammar")
    with pytest.raises(deck.FolderConflictError):
        deck.create_folder(user_db, "Grammar")
    assert len(deck.list_folders(user_db)) == 1


def test_create_folder_case_sensitive_names_are_distinct(
    user_db: sqlite3.Connection,
) -> None:
    deck.create_folder(user_db, "grammar")
    deck.create_folder(user_db, "Grammar")
    assert [f.name for f in deck.list_folders(user_db)] == ["grammar", "Grammar"]


def test_rename_folder_preserves_id_and_created_at(
    user_db: sqlite3.Connection,
) -> None:
    folder = deck.create_folder(user_db, "Old", created_at=datetime.fromisoformat(NOW))
    renamed = deck.rename_folder(user_db, folder.id, "New")
    assert renamed.id == folder.id
    assert renamed.created_at == folder.created_at
    assert renamed.name == "New"


def test_rename_folder_rejects_blank_and_missing_and_duplicate(
    user_db: sqlite3.Connection,
) -> None:
    folder = deck.create_folder(user_db, "One")
    other = deck.create_folder(user_db, "Two")
    with pytest.raises(deck.DeckError):
        deck.rename_folder(user_db, folder.id, "  ")
    with pytest.raises(deck.FolderNotFoundError):
        deck.rename_folder(user_db, 99999, "Nope")
    with pytest.raises(deck.FolderConflictError):
        deck.rename_folder(user_db, other.id, "One")


def test_delete_folder_missing_raises(user_db: sqlite3.Connection) -> None:
    with pytest.raises(deck.FolderNotFoundError):
        deck.delete_folder(user_db, 99999)


def test_delete_empty_folder(user_db: sqlite3.Connection) -> None:
    folder = deck.create_folder(user_db, "Empty")
    deck.delete_folder(user_db, folder.id)
    assert deck.list_folders(user_db) == []


def test_delete_assigned_folder_unassigns_but_never_deletes_decks(
    user_db: sqlite3.Connection,
) -> None:
    folder = deck.create_folder(user_db, "Group")
    first = deck.create_deck(user_db, "Deck A")
    second = deck.create_deck(user_db, "Deck B")
    deck.assign_deck_folder(user_db, first, folder.id)
    deck.assign_deck_folder(user_db, second, folder.id)

    deck.delete_folder(user_db, folder.id)

    assert deck.list_folders(user_db) == []
    assert deck._deck_row_or_raise(user_db, first).folder_id is None
    assert deck._deck_row_or_raise(user_db, second).folder_id is None
    names = {
        str(row[0]) for row in user_db.execute("SELECT name FROM deck").fetchall()
    }
    assert {"Deck A", "Deck B"} <= names


def test_assign_and_reassign_and_unassign_folder(user_db: sqlite3.Connection) -> None:
    first = deck.create_folder(user_db, "First")
    second = deck.create_folder(user_db, "Second")
    deck_id = deck.create_deck(user_db, "Deck")

    assigned = deck.assign_deck_folder(user_db, deck_id, first.id)
    assert assigned.folder_id == first.id

    reassigned = deck.assign_deck_folder(user_db, deck_id, second.id)
    assert reassigned.folder_id == second.id

    cleared = deck.assign_deck_folder(user_db, deck_id, None)
    assert cleared.folder_id is None


def test_assign_folder_missing_deck_and_missing_folder(
    user_db: sqlite3.Connection,
) -> None:
    deck_id = deck.create_deck(user_db, "Deck")
    folder = deck.create_folder(user_db, "Folder")
    with pytest.raises(deck.DeckNotFoundError):
        deck.assign_deck_folder(user_db, 99999, folder.id)
    with pytest.raises(deck.FolderNotFoundError):
        deck.assign_deck_folder(user_db, deck_id, 99999)


def test_deck_rename_coexists_with_folder_assignment(
    user_db: sqlite3.Connection,
) -> None:
    folder = deck.create_folder(user_db, "Group")
    deck_id = deck.create_deck(user_db, "Before")
    deck.assign_deck_folder(user_db, deck_id, folder.id)

    renamed = deck.rename_deck(user_db, deck_id, "After")

    assert renamed.name == "After"
    assert renamed.folder_id == folder.id


def test_deleting_a_deck_leaves_its_folder_alive(
    user_db: sqlite3.Connection,
) -> None:
    folder = deck.create_folder(user_db, "Group")
    deck_id = deck.create_deck(user_db, "Doomed")
    deck.assign_deck_folder(user_db, deck_id, folder.id)

    deck.delete_deck(user_db, deck_id)

    assert deck.list_folders(user_db) == [folder]
    assert user_db.execute(
        "SELECT COUNT(*) FROM deck WHERE id = ?", (deck_id,)
    ).fetchone()[0] == 0


def test_orphaned_deck_is_not_assignable_and_stays_null(
    user_db: sqlite3.Connection,
) -> None:
    folder = deck.create_folder(user_db, "Group")
    user_db.execute(
        "INSERT INTO deck (name, created_at) VALUES (?, ?)",
        (deck.ORPHANED_DECK_NAME, NOW),
    )
    user_db.commit()
    orphan_row = user_db.execute(
        "SELECT id FROM deck WHERE name = ?", (deck.ORPHANED_DECK_NAME,)
    ).fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])

    with pytest.raises(deck.OrphanedDeckProtectedError):
        deck.assign_deck_folder(user_db, orphan_id, folder.id)

    folder_id = user_db.execute(
        "SELECT folder_id FROM deck WHERE id = ?", (orphan_id,)
    ).fetchone()[0]
    assert folder_id is None


def test_concurrent_duplicate_folder_create_yields_one_row(
    user_db_path: Path,
) -> None:
    """Concurrent same-name creates converge on exactly one folder row."""
    barrier = threading.Barrier(4)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = sqlite3.connect(user_db_path, timeout=10)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            barrier.wait()
            try:
                deck.create_folder(conn, "Concurrent")
            except deck.FolderConflictError:
                with lock:
                    outcomes.append("conflict")
            except sqlite3.OperationalError:
                with lock:
                    outcomes.append("contended")
            else:
                with lock:
                    outcomes.append("created")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    conn = sqlite3.connect(user_db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM folder WHERE name = 'Concurrent'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1
    assert outcomes.count("created") == 1


# ---------------------------------------------------------------------------
# API contracts
# ---------------------------------------------------------------------------


def test_get_folders_empty(folder_client: TestClient) -> None:
    response = folder_client.get("/vocab/folders")
    assert response.status_code == 200
    assert response.json() == []


def test_post_folder_creates_and_lists(folder_client: TestClient) -> None:
    response = folder_client.post(
        "/vocab/folders", json={"name": "Grammar"}, headers=AUTH_HEADERS
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["name"] == "Grammar"
    assert isinstance(body["created_at"], str)
    assert folder_client.get("/vocab/folders").json() == [body]


def test_post_folder_trims_name(folder_client: TestClient) -> None:
    response = folder_client.post(
        "/vocab/folders", json={"name": "  Grammar  "}, headers=AUTH_HEADERS
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Grammar"


@pytest.mark.parametrize("payload", [{"name": "   "}, {"name": 5}, {}])
def test_post_folder_invalid_body_is_422(
    folder_client: TestClient, payload: dict[str, Any]
) -> None:
    response = folder_client.post("/vocab/folders", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_post_folder_duplicate_is_409_with_stable_code(
    folder_client: TestClient,
) -> None:
    folder_client.post("/vocab/folders", json={"name": "Grammar"}, headers=AUTH_HEADERS)
    response = folder_client.post(
        "/vocab/folders", json={"name": "Grammar"}, headers=AUTH_HEADERS
    )
    assert response.status_code == 409
    assert response.json()["code"] == "folder_name_conflict"


def test_patch_folder_renames_preserving_identity(folder_client: TestClient) -> None:
    created = folder_client.post(
        "/vocab/folders", json={"name": "Old"}, headers=AUTH_HEADERS
    ).json()
    response = folder_client.patch(
        f"/vocab/folders/{created['id']}",
        json={"name": "New"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["created_at"] == created["created_at"]
    assert body["name"] == "New"


def test_patch_folder_errors(folder_client: TestClient) -> None:
    first = folder_client.post(
        "/vocab/folders", json={"name": "One"}, headers=AUTH_HEADERS
    ).json()
    second = folder_client.post(
        "/vocab/folders", json={"name": "Two"}, headers=AUTH_HEADERS
    ).json()

    missing = folder_client.patch(
        "/vocab/folders/99999", json={"name": "X"}, headers=AUTH_HEADERS
    )
    assert missing.status_code == 404

    blank = folder_client.patch(
        f"/vocab/folders/{first['id']}", json={"name": "  "}, headers=AUTH_HEADERS
    )
    assert blank.status_code == 422

    not_string = folder_client.patch(
        f"/vocab/folders/{first['id']}", json={"name": 3}, headers=AUTH_HEADERS
    )
    assert not_string.status_code == 422

    duplicate = folder_client.patch(
        f"/vocab/folders/{second['id']}", json={"name": "One"}, headers=AUTH_HEADERS
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "folder_name_conflict"


def test_delete_folder_returns_deleted_and_preserves_deck(
    folder_client: TestClient, user_db: sqlite3.Connection
) -> None:
    folder = folder_client.post(
        "/vocab/folders", json={"name": "Group"}, headers=AUTH_HEADERS
    ).json()
    deck_id = int(
        folder_client.post(
            "/vocab/decks", json={"name": "Deck"}, headers=AUTH_HEADERS
        ).json()["id"]
    )
    assigned = folder_client.put(
        f"/vocab/decks/{deck_id}/folder",
        json={"folder_id": folder["id"]},
        headers=AUTH_HEADERS,
    )
    assert assigned.status_code == 200
    assert assigned.json()["folder_id"] == folder["id"]

    deleted = folder_client.delete(
        f"/vocab/folders/{folder['id']}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"id": folder["id"], "deleted": True}

    assert folder_client.get("/vocab/folders").json() == []
    decks = folder_client.get("/vocab/decks").json()
    listed = next(d for d in decks if d["id"] == deck_id)
    assert listed["folder_id"] is None
    assert user_db.execute(
        "SELECT COUNT(*) FROM deck WHERE id = ?", (deck_id,)
    ).fetchone()[0] == 1


def test_delete_missing_folder_is_404(folder_client: TestClient) -> None:
    response = folder_client.delete(
        "/vocab/folders/99999", headers={"X-Flashcards-Request": "1"}
    )
    assert response.status_code == 404


def test_put_deck_folder_assigns_and_returns_authoritative_deck(
    folder_client: TestClient, user_db_path: Path
) -> None:
    folder = folder_client.post(
        "/vocab/folders", json={"name": "Group"}, headers=AUTH_HEADERS
    ).json()
    deck_id = int(
        folder_client.post(
            "/vocab/decks", json={"name": "Deck"}, headers=AUTH_HEADERS
        ).json()["id"]
    )

    response = folder_client.put(
        f"/vocab/decks/{deck_id}/folder",
        json={"folder_id": folder["id"]},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == deck_id
    assert body["name"] == "Deck"
    assert body["folder_id"] == folder["id"]

    # Persists across a fresh connection/request.
    conn = sqlite3.connect(user_db_path)
    try:
        assert conn.execute(
            "SELECT folder_id FROM deck WHERE id = ?", (deck_id,)
        ).fetchone()[0] == folder["id"]
    finally:
        conn.close()
    listed = next(
        d for d in folder_client.get("/vocab/decks").json() if d["id"] == deck_id
    )
    assert listed["folder_id"] == folder["id"]

    # Unassign.
    cleared = folder_client.put(
        f"/vocab/decks/{deck_id}/folder",
        json={"folder_id": None},
        headers=AUTH_HEADERS,
    )
    assert cleared.status_code == 200
    assert cleared.json()["folder_id"] is None


def test_put_deck_folder_missing_deck_is_404(folder_client: TestClient) -> None:
    folder = folder_client.post(
        "/vocab/folders", json={"name": "Group"}, headers=AUTH_HEADERS
    ).json()
    response = folder_client.put(
        "/vocab/decks/99999/folder",
        json={"folder_id": folder["id"]},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404


def test_put_deck_folder_missing_folder_is_404_with_code(
    folder_client: TestClient,
) -> None:
    deck_id = int(
        folder_client.post(
            "/vocab/decks", json={"name": "Deck"}, headers=AUTH_HEADERS
        ).json()["id"]
    )
    response = folder_client.put(
        f"/vocab/decks/{deck_id}/folder",
        json={"folder_id": 99999},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "folder_not_found"


def test_put_deck_folder_orphaned_is_409(
    folder_client: TestClient, user_db: sqlite3.Connection
) -> None:
    folder = folder_client.post(
        "/vocab/folders", json={"name": "Group"}, headers=AUTH_HEADERS
    ).json()
    user_db.execute(
        "INSERT INTO deck (name, created_at) VALUES (?, ?)",
        (deck.ORPHANED_DECK_NAME, NOW),
    )
    user_db.commit()
    orphan_id = int(
        user_db.execute(
            "SELECT id FROM deck WHERE name = ?", (deck.ORPHANED_DECK_NAME,)
        ).fetchone()[0]
    )

    response = folder_client.put(
        f"/vocab/decks/{orphan_id}/folder",
        json={"folder_id": folder["id"]},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "orphaned_deck_protected"
    assert user_db.execute(
        "SELECT folder_id FROM deck WHERE id = ?", (orphan_id,)
    ).fetchone()[0] is None


@pytest.mark.parametrize("folder_id", ["not-an-int", True, 1.5])
def test_put_deck_folder_wrong_type_is_422(
    folder_client: TestClient, folder_id: Any
) -> None:
    deck_id = int(
        folder_client.post(
            "/vocab/decks", json={"name": "Deck"}, headers=AUTH_HEADERS
        ).json()["id"]
    )
    response = folder_client.put(
        f"/vocab/decks/{deck_id}/folder",
        json={"folder_id": folder_id},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422


def test_put_deck_folder_requires_folder_id_key(folder_client: TestClient) -> None:
    deck_id = int(
        folder_client.post(
            "/vocab/decks", json={"name": "Deck"}, headers=AUTH_HEADERS
        ).json()["id"]
    )
    response = folder_client.put(
        f"/vocab/decks/{deck_id}/folder", json={}, headers=AUTH_HEADERS
    )
    assert response.status_code == 422


def test_put_deck_folder_null_unassigns(folder_client: TestClient) -> None:
    deck_id = int(
        folder_client.post(
            "/vocab/decks", json={"name": "Deck"}, headers=AUTH_HEADERS
        ).json()["id"]
    )
    response = folder_client.put(
        f"/vocab/decks/{deck_id}/folder",
        json={"folder_id": None},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["folder_id"] is None


def test_put_deck_folder_malformed_json_is_400(folder_client: TestClient) -> None:
    response = folder_client.put(
        "/vocab/decks/1/folder",
        content=b"not json",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "valid JSON" in response.json()["detail"]


def test_get_decks_includes_folder_id(folder_client: TestClient) -> None:
    folder = folder_client.post(
        "/vocab/folders", json={"name": "Group"}, headers=AUTH_HEADERS
    ).json()
    deck_id = int(
        folder_client.post(
            "/vocab/decks", json={"name": "Deck"}, headers=AUTH_HEADERS
        ).json()["id"]
    )
    unassigned = next(
        d for d in folder_client.get("/vocab/decks").json() if d["id"] == deck_id
    )
    assert unassigned["folder_id"] is None

    folder_client.put(
        f"/vocab/decks/{deck_id}/folder",
        json={"folder_id": folder["id"]},
        headers=AUTH_HEADERS,
    )
    assigned = next(
        d for d in folder_client.get("/vocab/decks").json() if d["id"] == deck_id
    )
    assert assigned["folder_id"] == folder["id"]
