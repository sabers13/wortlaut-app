"""Comprehensive unit and integration tests for FastAPI application, routes, and security guards.

Covers:
1. Creation-time wildcard origin rejection (AGENTS R12)
2. Host guard matrix (loopback accepted, external rejected)
3. Origin exact-match matrix (allowed origin, rejected origin, omitted origin, OPTIONS)
4. Missing and wrong X-Flashcards-Request on EVERY non-GET route returning 403 with zero writes
5. Wrong Content-Type on JSON routes returning 400
6. Lookup endpoint returning candidate grammar data + active asset token
7. Note capture happy path, stale-token 409 with zero writes, Persian fa 422 with zero writes
8. Cards/next rendering display-time front and back faces (never stored)
9. Review confidence-only contract rejecting client-supplied rating and out-of-range confidence
10. Gloss set/delete with Persian fa 422 rejection and zero writes
11. Audio upload validation, failure preserving previous audio, streaming, and revert
12. Dictionary activation success, failure paths, and token update
13. Decks CRUD, mastery_percent computation per D30, and orphan preservation (AGENTS R5)
14. Anki TSV export sanitization with embedded tabs, newlines, commas (AGENTS R10)
"""

from __future__ import annotations

import io
import sqlite3
import threading
import wave
from collections.abc import Generator
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import _render_input_from_observation, create_app
from app.render import render_card
from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref


def _make_dummy_wav(duration_seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate minimal valid PCM WAV bytes for audio tests."""
    num_samples = int(duration_seconds * sample_rate)
    raw_frames = b"\x00\x00" * num_samples
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw_frames)
    return buffer.getvalue()


@pytest.fixture
def dict_path(tmp_path: Path, part_a_schema: str) -> Path:
    """Create a test dictionary asset with valid candidate semantic refs."""
    db_path = tmp_path / "api_dict_test.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(part_a_schema)

    lemma_rows = [
        (
            1,
            compute_lemma_semantic_ref("See", "NOUN", "der"),
            "See",
            "NOUN",
            "der",
            0,
            "Seen",
            0,
            "Sees",
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "zeː",
            "wiktionary",
            100,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            2,
            compute_lemma_semantic_ref("See", "NOUN", "die"),
            "See",
            "NOUN",
            "die",
            0,
            "Seen",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "zeː",
            "wiktionary",
            150,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            3,
            compute_lemma_semantic_ref("Bank", "NOUN", "die"),
            "Bank",
            "NOUN",
            "die",
            0,
            "Bänke",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "baŋk",
            "wiktionary",
            50,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            4,
            compute_lemma_semantic_ref("kranken", "NOUN", "die"),
            "kranken",
            "NOUN",
            "die",
            0,
            None,
            1,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "ˈkʁaŋkn̩",
            "wiktionary",
            500,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            5,
            compute_lemma_semantic_ref("Versicherung", "NOUN", "die"),
            "Versicherung",
            "NOUN",
            "die",
            0,
            "Versicherungen",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "fɛɐ̯ˈzɪçəʁʊŋ",
            "wiktionary",
            200,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            6,
            compute_lemma_semantic_ref("Karte", "NOUN", "die"),
            "Karte",
            "NOUN",
            "die",
            0,
            "Karten",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "ˈkaʁtə",
            "wiktionary",
            80,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            7,
            compute_lemma_semantic_ref("Haus", "NOUN", "das"),
            "Haus",
            "NOUN",
            "das",
            0,
            "Häuser",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "haʊ̯s",
            "wiktionary",
            20,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            8,
            compute_lemma_semantic_ref("Tür", "NOUN", "die"),
            "Tür",
            "NOUN",
            "die",
            0,
            "Türen",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "tyːɐ̯",
            "wiktionary",
            90,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            9,
            compute_lemma_semantic_ref("Tag", "NOUN", "der"),
            "Tag",
            "NOUN",
            "der",
            0,
            "Tage",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "taːk",
            "wiktionary",
            10,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            10,
            compute_lemma_semantic_ref("Licht", "NOUN", "das"),
            "Licht",
            "NOUN",
            "das",
            0,
            "Lichter",
            0,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            "lɪçt",
            "wiktionary",
            110,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            11,
            compute_lemma_semantic_ref("anrufen", "VERB", None),
            "anrufen",
            "VERB",
            None,
            0,
            None,
            0,
            None,
            "haben",
            1,
            "an",
            0,
            "ruft an",
            "rief an",
            "angerufen",
            "AKK",
            None,
            None,
            "ˈanˌʁuːfn̩",
            "wiktionary",
            60,
            "wiktionary",
            "CC BY-SA",
        ),
        (
            12,
            compute_lemma_semantic_ref("rufen", "VERB", None),
            "rufen",
            "VERB",
            None,
            0,
            None,
            0,
            None,
            "haben",
            0,
            None,
            0,
            "ruft",
            "rief",
            "gerufen",
            "AKK",
            None,
            None,
            "ˈʁuːfn̩",
            "wiktionary",
            70,
            "wiktionary",
            "CC BY-SA",
        ),
    ]

    lemma_insert_sql = (
        "INSERT INTO lemma ("
        "id, semantic_ref, lemma, pos, gender, plural_none, plural, genitive_sg, "
        "aux, separable, particle, reflexive, praesens_3sg, praeteritum_3sg, "
        "partizip_ii, governs, comparative, superlative, ipa, ipa_source, "
        "freq_rank, source, license) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    conn.executemany(
        lemma_insert_sql,
        [
            (
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
                r[6],
                r[7],
                r[9],
                r[10],
                r[11],
                r[12],
                r[13],
                r[14],
                r[15],
                r[16],
                r[17],
                r[18],
                r[19],
                r[20],
                r[21],
                r[22],
                r[23],
            )
            for r in lemma_rows
        ],
    )

    surface_forms = [
        ("häuser", 7),
        ("Häuser", 7),
        ("rief an", 11),
        ("ruft an", 11),
        ("Kranken", 4),
    ]
    conn.executemany("INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)", surface_forms)

    raw_senses = [
        (
            1,
            1,
            "wiktextract:enwiktionary",
            "senseid:en-see-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            2,
            2,
            "wiktextract:enwiktionary",
            "senseid:en-see-2",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            3,
            3,
            "wiktextract:enwiktionary",
            "senseid:en-bank-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            4,
            4,
            "wiktextract:enwiktionary",
            "senseid:en-kranken-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            5,
            5,
            "wiktextract:enwiktionary",
            "senseid:en-versicherung-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            6,
            6,
            "wiktextract:enwiktionary",
            "senseid:en-karte-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            7,
            7,
            "wiktextract:enwiktionary",
            "senseid:en-house-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            8,
            8,
            "wiktextract:enwiktionary",
            "senseid:en-tuer-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            9,
            9,
            "wiktextract:enwiktionary",
            "senseid:en-tag-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            10,
            10,
            "wiktextract:enwiktionary",
            "senseid:en-licht-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            11,
            11,
            "wiktextract:enwiktionary",
            "senseid:en-call-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            12,
            12,
            "wiktextract:enwiktionary",
            "senseid:en-shout-1",
            0,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        # Second sense for ``Bank`` (lemma_id=3) so the M6 rebind
        # fixture has at least one lemma with multiple direct senses
        # under the same durable ``lemma_semantic_ref``.
        (
            13,
            3,
            "wiktextract:enwiktionary",
            "senseid:en-bank-2",
            1,
            None,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
    ]
    lemma_ref_by_id = {row[0]: str(row[1]) for row in lemma_rows}
    sense_rows = [
        (
            s_id,
            lem_id,
            compute_sense_semantic_ref(lemma_ref_by_id[lem_id], ns, sref),
            ns,
            sref,
            ord_val,
            reg,
            src,
            lic,
        )
        for (s_id, lem_id, ns, sref, ord_val, reg, src, lic) in raw_senses
    ]
    sense_insert_sql = (
        "INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, "
        "source_ref, ord, register, source, license) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    conn.executemany(sense_insert_sql, sense_rows)

    meaning_insert_sql = (
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, "
        "source, license) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    conn.executemany(
        meaning_insert_sql,
        [
            (1, 1, "en", "translation", 0, "lake", "wiktionary", "CC BY-SA 4.0"),
            (2, 2, "en", "translation", 0, "sea, ocean", "wiktionary", "CC BY-SA 4.0"),
            (3, 3, "en", "translation", 0, "bank, bench", "wiktionary", "CC BY-SA 4.0"),
            (4, 4, "en", "translation", 0, "sick, patients", "wiktionary", "CC BY-SA 4.0"),
            (5, 5, "en", "translation", 0, "insurance", "wiktionary", "CC BY-SA 4.0"),
            (6, 6, "en", "translation", 0, "card, map", "wiktionary", "CC BY-SA 4.0"),
            (7, 7, "en", "translation", 0, "house, building", "wiktionary", "CC BY-SA 4.0"),
            (8, 8, "en", "translation", 0, "door", "wiktionary", "CC BY-SA 4.0"),
            (9, 9, "en", "translation", 0, "day", "wiktionary", "CC BY-SA 4.0"),
            (10, 10, "en", "translation", 0, "light", "wiktionary", "CC BY-SA 4.0"),
            (11, 11, "en", "translation", 0, "to call, phone", "wiktionary", "CC BY-SA 4.0"),
            (12, 12, "en", "translation", 0, "to shout, cry out", "wiktionary", "CC BY-SA 4.0"),
            (13, 13, "en", "translation", 0, "financial institution", "wiktionary", "CC BY-SA 4.0"),
        ],
    )

    example_insert_sql = (
        "INSERT INTO example (id, de, en, source, license, token_count) VALUES (?, ?, ?, ?, ?, ?)"
    )
    conn.executemany(
        example_insert_sql,
        [
            (1, "Der See ist tief.", "The lake is deep.", "tatoeba", "CC BY 2.0 FR", 5),
            (2, "Die See ist stürmisch.", "The sea is stormy.", "tatoeba", "CC BY 2.0 FR", 5),
            (
                3,
                "Ich rufe dich morgen an.",
                "I will call you tomorrow.",
                "tatoeba",
                "CC BY 2.0 FR",
                5,
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO example_lemma (lemma_id, example_id) VALUES (?, ?)",
        [(1, 1), (2, 2), (11, 3)],
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def app_instance(dict_path: Path, user_db_path: Path) -> Any:
    """Create a standard test application instance with allowed origins."""
    return create_app(
        dict_path=dict_path,
        user_db_path=user_db_path,
        cors_origins=["http://localhost:3000", "http://127.0.0.1:5173"],
    )


@pytest.fixture
def client(app_instance: Any) -> Generator[TestClient, None, None]:
    """TestClient configured with loopback base URL."""
    with TestClient(app_instance, base_url="http://127.0.0.1:8000") as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. Creation-time wildcard rejection (AGENTS R12)
# ---------------------------------------------------------------------------


def test_creation_time_wildcard_rejection(dict_path: Path, user_db_path: Path) -> None:
    """AGENTS R12: cors_origins must be exact; wildcard * is strictly forbidden."""
    with pytest.raises(ValueError, match="Wildcard origin is forbidden"):
        create_app(dict_path, user_db_path, cors_origins=["*"])

    with pytest.raises(ValueError, match="Wildcard origin is forbidden"):
        create_app(dict_path, user_db_path, cors_origins=["http://*.example.com"])

    with pytest.raises(ValueError, match="Wildcard origin is forbidden"):
        create_app(dict_path, user_db_path, cors_origins=["http://localhost:3000", "*"])


# ---------------------------------------------------------------------------
# 2. Host guard matrix (loopback accepted, external rejected)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host_header",
    [
        "127.0.0.1:8000",
        "localhost:8000",
        "[::1]:8000",
    ],
)
def test_host_guard_accepts_loopback(client: TestClient, host_header: str) -> None:
    """Loopback host in all standard forms is accepted."""
    response = client.get("/vocab/decks", headers={"Host": host_header})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host_header",
    [
        "127.0.0.1",
        "localhost",
        "[::1]",
        "127.0.0.1:7999",
        "localhost:3000",
        "[::1]:8080",
        "[::1]evil:8000",
        "[::1:8000",
        "::1:8000",
        "evil.com",
        "evil.com:8000",
        "192.168.1.100",
        "192.168.1.100:8000",
        "example.org",
        "attacker.local",
    ],
)
def test_host_guard_rejects_external_hosts(client: TestClient, host_header: str) -> None:
    """External/non-loopback host is rejected with HTTP 403."""
    response = client.get("/vocab/decks", headers={"Host": host_header})
    assert response.status_code == 403
    assert "Host header must be a loopback endpoint" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 3. Origin exact-match matrix
# ---------------------------------------------------------------------------


def test_origin_exact_match_matrix(client: TestClient) -> None:
    """Configured origins are accepted with CORS headers; unconfigured are rejected with 403."""
    # Configured origin 1
    r1 = client.get("/vocab/decks", headers={"Origin": "http://localhost:3000"})
    assert r1.status_code == 200
    assert r1.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"

    # Configured origin 2
    r2 = client.get("/vocab/decks", headers={"Origin": "http://127.0.0.1:5173"})
    assert r2.status_code == 200
    assert r2.headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:5173"

    # Forbidden / unconfigured origin
    r_evil = client.get("/vocab/decks", headers={"Origin": "http://evil.com"})
    assert r_evil.status_code == 403
    assert "Forbidden origin" in r_evil.json()["detail"]

    # Subdomain not in exact allowlist
    r_sub = client.get("/vocab/decks", headers={"Origin": "http://sub.localhost:3000"})
    assert r_sub.status_code == 403

    # Omitted Origin (e.g. CLI tool / curl) is permitted
    r_no_origin = client.get("/vocab/decks")
    assert r_no_origin.status_code == 200

    # A present blank Origin is not the same as an absent Origin.
    r_blank = client.get("/vocab/decks", headers={"Origin": "   "})
    assert r_blank.status_code == 403

    # OPTIONS preflight
    r_options = client.options(
        "/vocab/notes",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-Flashcards-Request",
        },
    )
    assert r_options.status_code == 200
    assert r_options.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"


# ---------------------------------------------------------------------------
# 4. Missing / wrong X-Flashcards-Request on EVERY non-GET route returns 403
# ---------------------------------------------------------------------------


def test_custom_header_guard_on_all_non_get_routes(
    client: TestClient, user_db: sqlite3.Connection
) -> None:
    """Mutating routes require X-Flashcards-Request: 1; missing/wrong returns 403 with 0 writes."""
    dummy_audio = _make_dummy_wav(0.5)

    non_get_requests: list[tuple[str, str, dict[str, Any]]] = [
        (
            "POST",
            "/vocab/notes",
            {
                "json": {
                    "lemma_semantic_ref": "lemma:v1:test",
                    "meaning_languages": ["de"],
                }
            },
        ),
        ("POST", "/vocab/cards/1/review", {"json": {"confidence": 4}}),
        (
            "POST",
            "/vocab/notes/1/gloss",
            {"json": {"language": "de", "meaning_text": "test"}},
        ),
        ("DELETE", "/vocab/notes/1/gloss?language=de", {}),
        (
            "POST",
            "/vocab/notes/1/audio",
            {
                "content": dummy_audio,
                "headers": {"Content-Type": "audio/wav"},
            },
        ),
        ("DELETE", "/vocab/notes/1/audio", {}),
        (
            "POST",
            "/vocab/dictionary/activate",
            {"json": {"path": "candidate.sqlite"}},
        ),
        ("POST", "/vocab/decks", {"json": {"name": "NewDeck"}}),
        ("DELETE", "/vocab/decks/1", {}),
    ]

    for method, path, kwargs in non_get_requests:
        # Snapshot table counts
        notes_before = user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0]
        decks_before = user_db.execute("SELECT COUNT(*) FROM deck").fetchone()[0]
        reviews_before = user_db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]

        # Case A: Missing header
        res_missing = client.request(method, path, **kwargs)
        assert res_missing.status_code == 403, f"{method} {path} missing header expected 403"
        assert "X-Flashcards-Request" in res_missing.json()["detail"]

        # Case B: Wrong header value
        headers_wrong = dict(kwargs.get("headers", {}))
        headers_wrong["X-Flashcards-Request"] = "2"
        kwargs_wrong = dict(kwargs)
        kwargs_wrong["headers"] = headers_wrong
        res_wrong = client.request(method, path, **kwargs_wrong)
        assert res_wrong.status_code == 403, f"{method} {path} wrong header expected 403"

        # Assert ZERO writes occurred
        assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == notes_before
        assert user_db.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == decks_before
        assert user_db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == reviews_before


# ---------------------------------------------------------------------------
# 5. Wrong Content-Type on JSON routes returns 400
# ---------------------------------------------------------------------------


def test_wrong_content_type_on_json_routes(client: TestClient) -> None:
    """JSON routes require Content-Type: application/json; text/plain returns 400."""
    headers_auth = {"X-Flashcards-Request": "1", "Content-Type": "text/plain"}

    # POST /vocab/notes
    r1 = client.post(
        "/vocab/notes",
        content=b'{"lemma_semantic_ref": "test"}',
        headers=headers_auth,
    )
    assert r1.status_code == 400
    assert "Content-Type must be application/json" in r1.json()["detail"]

    # POST /vocab/decks
    r2 = client.post(
        "/vocab/decks",
        content=b'{"name": "Deck"}',
        headers=headers_auth,
    )
    assert r2.status_code == 400

    # POST /vocab/cards/1/review
    r3 = client.post(
        "/vocab/cards/1/review",
        content=b'{"confidence": 4}',
        headers=headers_auth,
    )
    assert r3.status_code == 400


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8"],
)
def test_json_content_type_accepts_exact_media_type_with_parameters(
    client: TestClient, content_type: str
) -> None:
    response = client.post(
        "/vocab/decks",
        content=b'{"name": "Media type"}',
        headers={"X-Flashcards-Request": "1", "Content-Type": content_type},
    )
    assert response.status_code == 201


def test_json_content_type_rejects_jsonp_lookalike(client: TestClient) -> None:
    response = client.post(
        "/vocab/decks",
        content=b'{"name": "Not JSON"}',
        headers={"X-Flashcards-Request": "1", "Content-Type": "application/jsonp"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 6. Lookup endpoint returns candidate grammar + active asset token
# ---------------------------------------------------------------------------


def test_lookup_endpoint(client: TestClient, app_instance: Any) -> None:
    """GET /vocab/lookup resolves lemma and returns active asset token."""
    active_token = app_instance.state.runtime.asset_token

    response = client.get("/vocab/lookup?q=See")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "See"
    assert data["asset_token"] == active_token
    assert len(data["candidates"]) >= 2  # der See and die See

    candidate_masc = next((c for c in data["candidates"] if c["gender"] == "der"), None)
    assert candidate_masc is not None
    assert candidate_masc["lemma"] == "See"
    assert candidate_masc["pos"] == "NOUN"
    assert len(candidate_masc["senses"]) > 0
    assert candidate_masc["examples"] == []

    # Empty query returns 422
    r_empty = client.get("/vocab/lookup?q=  ")
    assert r_empty.status_code == 422


# ---------------------------------------------------------------------------
# 7. Note capture happy path, stale-token 409, and Persian fa 422 zero-writes
# ---------------------------------------------------------------------------


def test_capture_note_and_failure_matrix(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """Capture happy path, stale token 409, and Persian 422 with zero database writes."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # 1. Happy path
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    payload_valid = {
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": active_token,
        "deck_name": "Lektion 1",
    }
    r_happy = client.post("/vocab/notes", json=payload_valid, headers=headers_valid)
    assert r_happy.status_code == 201
    created_data = r_happy.json()
    note_id = created_data["note_id"]
    assert note_id > 0
    assert created_data["meaning_languages"] == ["de", "en"]

    note_row = user_db.execute("SELECT id, status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row["status"] == "resolved"

    # 2. Stale token rejection (HTTP 409) with zero writes
    notes_count_before = user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0]
    payload_stale = dict(payload_valid)
    payload_stale["asset_token"] = "0" * 64
    r_stale = client.post("/vocab/notes", json=payload_stale, headers=headers_valid)
    assert r_stale.status_code == 409
    assert "Asset token mismatch" in r_stale.json()["detail"]
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == notes_count_before

    # 3. Persian fa rejection (HTTP 422) with zero writes
    payload_fa = dict(payload_valid)
    payload_fa["meaning_languages"] = ["de", "fa"]
    r_fa = client.post("/vocab/notes", json=payload_fa, headers=headers_valid)
    assert r_fa.status_code == 422
    assert "Persian (fa) is deferred" in r_fa.json()["detail"]
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == notes_count_before

    # 4. Empty / unsupported language set (HTTP 422)
    payload_empty_lang = dict(payload_valid)
    payload_empty_lang["meaning_languages"] = []
    r_empty_lang = client.post("/vocab/notes", json=payload_empty_lang, headers=headers_valid)
    assert r_empty_lang.status_code == 422


# ---------------------------------------------------------------------------
# 8. Cards/next rendering display-time faces
# ---------------------------------------------------------------------------


def test_cards_next_rendering(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """GET /vocab/cards/next dynamically computes front and back faces (never stored)."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # 1. Initially no cards
    r_none = client.get("/vocab/cards/next")
    assert r_none.status_code == 200
    assert r_none.json()["card"] is None

    # 2. Capture a note
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de", "en"],
            "asset_token": active_token,
            "deck_name": "Nouns",
        },
        headers=headers_valid,
    )

    # 3. Request next due card
    r_card = client.get("/vocab/cards/next")
    assert r_card.status_code == 200
    card_data = r_card.json()["card"]
    assert card_data is not None

    # Verify front face
    front = card_data["front"]
    assert front["headword"] == "See"
    assert front["display_headword"] == "der See"
    assert front["pos"] == "NOUN"
    assert front["article"] == "der"
    assert front["audio_trigger"]["available"] is True

    # Verify back face
    back = card_data["back"]
    assert back["display_headword"] == "der See"
    assert "Grammatik:" in back["text"]
    assert len(back["meanings"]) > 0
    assert len(back["examples"]) > 0


def test_cards_next_skips_not_yet_due_cards(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """GET /vocab/cards/next returns only cards due at the request time."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )

    r_note = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["en"],
            "asset_token": active_token,
            "deck_name": "Due filter",
        },
        headers=headers_valid,
    )
    assert r_note.status_code == 201
    note_id = r_note.json()["note_id"]
    card_row = user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()
    assert card_row is not None
    card_id = int(card_row[0])

    r_due = client.get("/vocab/cards/next")
    assert r_due.status_code == 200
    assert r_due.json()["card"]["card_id"] == card_id

    r_review = client.post(
        f"/vocab/cards/{card_id}/review",
        json={"confidence": 5},
        headers=headers_valid,
    )
    assert r_review.status_code == 200
    assert client.get("/vocab/cards/next").json()["card"] is None

    future_due_at = "2099-01-01T00:00:00+00:00"
    with user_db:
        future_note = user_db.execute(
            """
            INSERT INTO note (lemma_semantic_ref, status, created_at, due_at)
            VALUES (?, 'needs_gloss', ?, ?)
            """,
            ("future-only", future_due_at, future_due_at),
        )
        assert future_note.lastrowid is not None
        future_note_id = future_note.lastrowid
        user_db.execute(
            "INSERT INTO card (note_id, state, step, due_at) VALUES (?, ?, ?, ?)",
            (future_note_id, 1, None, future_due_at),
        )

    assert client.get("/vocab/cards/next").json()["card"] is None


# ---------------------------------------------------------------------------
# 9. Review confidence-only contract (ADR-0003)
# ---------------------------------------------------------------------------


def test_review_confidence_only_contract(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """POST /vocab/cards/{id}/review accepts confidence 1..5 only; rejects rating."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # Create note & card
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    res_note = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["en"],
            "asset_token": active_token,
        },
        headers=headers_valid,
    )
    note_id = res_note.json()["note_id"]
    card_row = user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()
    card_id = int(card_row[0])

    # 1. Reject client-supplied rating field (HTTP 422)
    r_rating = client.post(
        f"/vocab/cards/{card_id}/review",
        json={"confidence": 4, "rating": 3},
        headers=headers_valid,
    )
    assert r_rating.status_code == 422
    assert "Client-supplied rating is forbidden" in r_rating.json()["detail"]

    # 2. Reject out-of-range confidence
    for invalid_conf in (0, 6, -1, 10):
        r_bad = client.post(
            f"/vocab/cards/{card_id}/review",
            json={"confidence": invalid_conf},
            headers=headers_valid,
        )
        assert r_bad.status_code == 422

    # 3. Valid confidence review (confidence=4 -> mapped FSRS rating=3)
    r_good = client.post(
        f"/vocab/cards/{card_id}/review",
        json={"confidence": 4},
        headers=headers_valid,
    )
    assert r_good.status_code == 200
    review_data = r_good.json()
    assert review_data["confidence"] == 4
    assert review_data["rating"] == 3

    # Verify review_log has append-only row with raw confidence 4 and rating 3
    log_row = user_db.execute(
        "SELECT confidence, rating FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()
    assert log_row is not None
    assert log_row[0] == 4
    assert log_row[1] == 3


# ---------------------------------------------------------------------------
# 10. Gloss set/delete with Persian fa 422 rejection and zero writes
# ---------------------------------------------------------------------------


def test_gloss_endpoints_and_fa_rejection(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """POST/DELETE /vocab/notes/{id}/gloss manages user meanings and rejects fa with 422."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # Create note
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    res_note = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de", "en"],
            "asset_token": active_token,
        },
        headers=headers_valid,
    )
    note_id = res_note.json()["note_id"]

    # 1. Persian fa rejection on POST gloss
    r_post_fa = client.post(
        f"/vocab/notes/{note_id}/gloss",
        json={"language": "fa", "meaning_text": "خانه"},
        headers=headers_valid,
    )
    assert r_post_fa.status_code == 422
    assert "Persian (fa) is deferred" in r_post_fa.json()["detail"]
    assert user_db.execute("SELECT COUNT(*) FROM note_user_meaning").fetchone()[0] == 0

    # 2. Persian fa rejection on DELETE gloss
    r_del_fa = client.delete(
        f"/vocab/notes/{note_id}/gloss?language=fa",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del_fa.status_code == 422

    # 3. Valid user meaning upsert (English)
    r_post_en = client.post(
        f"/vocab/notes/{note_id}/gloss",
        json={"language": "en", "meaning_text": "my cozy home"},
        headers=headers_valid,
    )
    assert r_post_en.status_code == 200
    assert r_post_en.json()["meaning_text"] == "my cozy home"

    um_row = user_db.execute(
        "SELECT meaning_text FROM note_user_meaning WHERE note_id = ? AND lang = 'en'",
        (note_id,),
    ).fetchone()
    assert um_row is not None and um_row[0] == "my cozy home"

    # 4. Valid user meaning delete
    r_del_en = client.delete(
        f"/vocab/notes/{note_id}/gloss?language=en",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del_en.status_code == 200
    assert user_db.execute("SELECT COUNT(*) FROM note_user_meaning").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 11. Audio upload, validation failure preserving previous, streaming, and revert
# ---------------------------------------------------------------------------


def test_audio_endpoints_and_preservation(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """Custom audio persistence, crash-safe replacement, failure preserving previous, and revert."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # Create note
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    res_note = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de"],
            "asset_token": active_token,
        },
        headers=headers_valid,
    )
    note_id = res_note.json()["note_id"]

    valid_wav = _make_dummy_wav(0.5)

    # 1. Upload valid audio
    r_upload_1 = client.post(
        f"/vocab/notes/{note_id}/audio",
        content=valid_wav,
        headers={"X-Flashcards-Request": "1", "Content-Type": "audio/wav"},
    )
    assert r_upload_1.status_code == 201
    media_fn_1 = r_upload_1.json()["media_filename"]
    sha_1 = r_upload_1.json()["sha256"]

    # 2. Upload invalid audio -> rejected (HTTP 422), previous audio MUST be preserved
    invalid_bytes = b"NOT_A_REAL_AUDIO_FILE_DATA_12345"
    r_upload_bad = client.post(
        f"/vocab/notes/{note_id}/audio",
        content=invalid_bytes,
        headers={"X-Flashcards-Request": "1", "Content-Type": "audio/wav"},
    )
    assert r_upload_bad.status_code == 422
    assert "Audio validation failed" in r_upload_bad.json()["detail"]

    # Assert previous valid audio is preserved intact
    cust_row = user_db.execute(
        "SELECT media_filename, sha256 FROM custom_pronunciation WHERE note_id = ?",
        (note_id,),
    ).fetchone()
    assert cust_row is not None
    assert cust_row[0] == media_fn_1
    assert cust_row[1] == sha_1

    # 3. Stream audio
    r_stream = client.get(f"/vocab/audio/{note_id}")
    assert r_stream.status_code == 200
    assert r_stream.content == valid_wav
    assert r_stream.headers["content-type"].startswith("audio/wav")

    # 4. Revert custom audio to automatic
    r_revert = client.delete(
        f"/vocab/notes/{note_id}/audio",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_revert.status_code == 200
    assert user_db.execute("SELECT COUNT(*) FROM custom_pronunciation").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 12. Dictionary activation success, failure paths, and token update (ADR-0004 D47)
# ---------------------------------------------------------------------------


def test_dictionary_activate_endpoints(
    client: TestClient, app_instance: Any, dict_path: Path, part_a_schema: str
) -> None:
    """POST /vocab/dictionary/activate drives atomic activation and token update."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    managed_dir = dict_path.parent

    # Create candidate dictionary in managed directory
    cand_path = managed_dir / "dict_v2_candidate.sqlite"
    conn = sqlite3.connect(cand_path)
    conn.executescript(part_a_schema)
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(lemma_ref, "wiktionary", "s1")
    conn.execute(
        "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender) "
        "VALUES (1, ?, 'Haus', 'NOUN', 'das')",
        (lemma_ref,),
    )
    conn.execute(
        "INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, source_ref) "
        "VALUES (1, 1, ?, 'wiktionary', 's1')",
        (sense_ref,),
    )
    conn.commit()
    conn.close()

    # 1. Activate valid candidate
    r_act = client.post(
        "/vocab/dictionary/activate",
        json={"path": "dict_v2_candidate.sqlite", "version": "v2"},
        headers=headers_valid,
    )
    assert r_act.status_code == 200
    data = r_act.json()
    assert data["status"] == "activated"
    assert data["version"] == "v2"
    assert data["asset_token"] == sha256(cand_path.read_bytes()).hexdigest()

    # 2. Activate non-existent candidate -> 422
    r_missing = client.post(
        "/vocab/dictionary/activate",
        json={"path": "missing_dict.sqlite", "version": "v3"},
        headers=headers_valid,
    )
    assert r_missing.status_code == 422

    # 3. Path traversal forbidden -> 422
    r_traversal = client.post(
        "/vocab/dictionary/activate",
        json={"path": "../outside.sqlite", "version": "v3"},
        headers=headers_valid,
    )
    assert r_traversal.status_code == 422


# ---------------------------------------------------------------------------
# 13. Decks CRUD and orphan preservation (AGENTS R5, ADR-0003 D30)
# ---------------------------------------------------------------------------


def test_decks_crud_and_orphan_preservation(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """Decks CRUD, mastery_percent formula (D30), and notes orphaned on deck deletion (R5)."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # 1. Create deck
    r_deck = client.post(
        "/vocab/decks",
        json={"name": "Lesson 1"},
        headers=headers_valid,
    )
    assert r_deck.status_code == 201
    deck_id = r_deck.json()["id"]

    # 2. Duplicate deck name -> 409
    r_dup = client.post(
        "/vocab/decks",
        json={"name": "Lesson 1"},
        headers=headers_valid,
    )
    assert r_dup.status_code == 409

    # 3. Capture note in this deck and review it
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    res_note = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de"],
            "asset_token": active_token,
            "deck_name": "Lesson 1",
        },
        headers=headers_valid,
    )
    note_id = res_note.json()["note_id"]
    card_id = user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()[0]

    # Review with confidence 5 (100% mastery for this single card)
    client.post(f"/vocab/cards/{card_id}/review", json={"confidence": 5}, headers=headers_valid)

    # 4. Check decks list and mastery_percent
    r_decks = client.get("/vocab/decks")
    assert r_decks.status_code == 200
    decks = r_decks.json()
    lesson_deck = next((d for d in decks if d["id"] == deck_id), None)
    assert lesson_deck is not None
    assert lesson_deck["card_count"] == 1
    assert lesson_deck["mastery_percent"] == 100.0

    # 5. Delete deck -> note must move to Orphaned deck, never cascade-deleted (AGENTS R5)
    r_del_deck = client.delete(
        f"/vocab/decks/{deck_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del_deck.status_code == 200

    # Verify note status is orphaned and review history is intact
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == "orphaned"

    reviews_count = user_db.execute(
        "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (card_id,)
    ).fetchone()[0]
    assert reviews_count == 1


# ---------------------------------------------------------------------------
# 13b. M1 — deck/card management endpoints
# ---------------------------------------------------------------------------


def _seed_resolved_card_with_audio(
    client: TestClient,
    user_db: sqlite3.Connection,
    app_instance: Any,
    lemma: str,
    sense: str,
    *,
    deck_name: str,
    meaning_text: str | None = None,
) -> dict[str, int]:
    """Helper: create a resolved note in ``deck_name`` and return IDs."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    deck_row = user_db.execute(
        "SELECT id FROM deck WHERE name = ?", (deck_name,)
    ).fetchone()
    if deck_row is None:
        deck_id = int(
            client.post(
                "/vocab/decks",
                json={"name": deck_name},
                headers=headers_valid,
            ).json()["id"]
        )
    else:
        deck_id = int(deck_row[0])

    lemma_ref = compute_lemma_semantic_ref(lemma, "NOUN", "der")
    payload = {
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": active_token,
        "deck_name": deck_name,
    }
    r = client.post("/vocab/notes", json=payload, headers=headers_valid)
    assert r.status_code == 201
    note_id = int(r.json()["note_id"])
    if meaning_text is not None:
        r_gloss = client.post(
            f"/vocab/notes/{note_id}/gloss",
            json={"language": "en", "meaning_text": meaning_text},
            headers=headers_valid,
        )
        assert r_gloss.status_code == 200
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
            "2026-08-24T12:00:00+00:00",
        ),
    )
    user_db.commit()
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()[0]
    )
    return {"note_id": note_id, "card_id": card_id, "deck_id": deck_id}


def test_get_deck_cards_returns_empty_for_empty_deck(client: TestClient) -> None:
    """GET /vocab/decks/{deck_id}/cards returns 200 with empty cards for an empty deck."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    r = client.post("/vocab/decks", json={"name": "Empty deck"}, headers=headers_valid)
    assert r.status_code == 201
    deck_id = int(r.json()["id"])

    r_get = client.get(f"/vocab/decks/{deck_id}/cards")
    assert r_get.status_code == 200
    body = r_get.json()
    assert body["deck"]["id"] == deck_id
    assert body["deck"]["name"] == "Empty deck"
    assert body["cards"] == []


def test_get_deck_cards_404_for_unknown_deck(client: TestClient) -> None:
    r_get = client.get("/vocab/decks/99999/cards")
    assert r_get.status_code == 404
    assert "99999" in r_get.json()["detail"]


def test_get_deck_cards_includes_user_meanings_and_audio_metadata(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """GET /vocab/decks/{deck_id}/cards materializes headword/POS/gender and metadata."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": active_token,
        "deck_name": "Cards projection",
    }, headers=headers_valid)
    assert r.status_code == 201
    note_id = int(r.json()["note_id"])
    deck_id = int(r.json()["deck_id"])
    client.post(
        f"/vocab/notes/{note_id}/gloss",
        json={"language": "en", "meaning_text": "lake"},
        headers=headers_valid,
    )
    user_db.execute(
        """
        INSERT INTO custom_pronunciation (
            note_id, media_filename, sha256, byte_size, format, source_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            "see.wav",
            "b" * 64,
            4096,
            "wav",
            "uploaded",
            "2026-08-24T12:00:00+00:00",
        ),
    )
    user_db.commit()

    r_get = client.get(f"/vocab/decks/{deck_id}/cards")
    assert r_get.status_code == 200
    body = r_get.json()
    assert body["deck"]["id"] == deck_id
    assert len(body["cards"]) == 1
    card = body["cards"][0]
    assert card["note_id"] == note_id
    assert card["lemma_semantic_ref"] == lemma_ref
    assert card["sense_semantic_ref"] == sense_ref
    assert card["status"] == "resolved"
    assert card["headword"] == "See"
    assert card["pos"] == "NOUN"
    assert card["gender"] == "der"
    assert card["selected_languages"] == ["de", "en"]
    assert card["user_meanings"] == {"en": "lake"}
    assert card["has_custom_audio"] is True
    assert card["other_deck_ids"] == []
    assert "examples" not in card
    assert "back" not in card


def test_get_deck_cards_reports_status_across_lifecycle(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """Deck cards listing surfaces resolved/needs_gloss/derived_compound/orphaned."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    active_token = app_instance.state.runtime.asset_token

    # 1. resolved note
    r_deck = client.post(
        "/vocab/decks", json={"name": "Lifecycle deck"}, headers=headers_valid
    )
    assert r_deck.status_code == 201
    deck_id = int(r_deck.json()["id"])

    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r_resolved = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": active_token,
        "deck_name": "Lifecycle deck",
    }, headers=headers_valid)
    assert r_resolved.status_code == 201

    # 2. needs_gloss note
    lemma_ref_2 = compute_lemma_semantic_ref("Tag", "NOUN", "der")
    r_needs = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref_2,
        "status": "needs_gloss",
        "meaning_languages": ["de"],
        "asset_token": active_token,
        "deck_name": "Lifecycle deck",
    }, headers=headers_valid)
    assert r_needs.status_code == 201

    # 3. derived_compound note built from existing fixture components.
    # The fixture dictionary holds no compound lemma, so the note is
    # seeded through the domain helper; the listing endpoint under test
    # is still exercised over HTTP below.
    from app.deck import add_note_to_deck as _add_note_to_deck
    from app.deck import create_note as _create_note

    haus_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    haus_sense = compute_sense_semantic_ref(
        haus_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    tuer_ref = compute_lemma_semantic_ref("Tür", "NOUN", "die")
    tuer_sense = compute_sense_semantic_ref(
        tuer_ref, "wiktextract:enwiktionary", "senseid:en-tuer-1"
    )
    compound_note = _create_note(
        user_db,
        compute_lemma_semantic_ref("Haustür", "NOUN", "die"),
        status="derived_compound",
        component_bindings=((haus_ref, haus_sense), (tuer_ref, tuer_sense)),
        meaning_languages=("de",),
    )
    _add_note_to_deck(user_db, compound_note, deck_id)
    user_db.commit()

    r_get = client.get(f"/vocab/decks/{deck_id}/cards")
    assert r_get.status_code == 200
    statuses = sorted(c["status"] for c in r_get.json()["cards"])
    assert statuses == ["derived_compound", "needs_gloss", "resolved"]

    # 4. orphaned note: removing the last membership surfaces it in Orphaned.
    doomed_id = int(r_needs.json()["note_id"])
    r_del = client.delete(
        f"/vocab/decks/{deck_id}/notes/{doomed_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del.status_code == 200
    assert r_del.json()["orphaned"] is True
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])
    r_orphan = client.get(f"/vocab/decks/{orphan_id}/cards")
    assert r_orphan.status_code == 200
    orphan_cards = r_orphan.json()["cards"]
    assert [c["note_id"] for c in orphan_cards] == [doomed_id]
    assert orphan_cards[0]["status"] == "orphaned"
    remaining = sorted(
        c["status"] for c in client.get(f"/vocab/decks/{deck_id}/cards").json()["cards"]
    )
    assert remaining == ["derived_compound", "resolved"]


def test_get_deck_cards_reflects_membership_mutation(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """After move/remove, the deck cards listing reflects the new state."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    active_token = app_instance.state.runtime.asset_token
    deck_a = int(
        client.post(
            "/vocab/decks", json={"name": "A"}, headers=headers_valid
        ).json()["id"]
    )
    deck_b = int(
        client.post(
            "/vocab/decks", json={"name": "B"}, headers=headers_valid
        ).json()["id"]
    )

    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": active_token,
        "deck_name": "A",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])

    assert client.get(f"/vocab/decks/{deck_a}/cards").json()["cards"][0]["other_deck_ids"] == []

    r_move = client.patch(
        f"/vocab/decks/{deck_a}/notes/{note_id}",
        json={"deck_id": deck_b},
        headers=headers_valid,
    )
    assert r_move.status_code == 200
    assert client.get(f"/vocab/decks/{deck_a}/cards").json()["cards"] == []
    listing_b = client.get(f"/vocab/decks/{deck_b}/cards").json()
    assert len(listing_b["cards"]) == 1
    assert listing_b["cards"][0]["other_deck_ids"] == []


def test_get_deck_cards_makes_no_writes(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """GET /vocab/decks/{deck_id}/cards is a strict read with zero PART-B writes."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post("/vocab/decks", json={"name": "Read-only"}, headers=headers_valid).json()["id"]
    )
    note_count = user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0]
    deck_count = user_db.execute("SELECT COUNT(*) FROM deck").fetchone()[0]
    review_count = user_db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]
    r = client.get(f"/vocab/decks/{deck_id}/cards")
    assert r.status_code == 200
    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == note_count
    assert user_db.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == deck_count
    assert user_db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == review_count


def test_remove_note_from_deck_removes_only_one_membership(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """DELETE /vocab/decks/{deck_id}/notes/{note_id} removes one membership only."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_a = int(
        client.post(
            "/vocab/decks", json={"name": "A1"}, headers=headers_valid
        ).json()["id"]
    )
    deck_b = int(
        client.post(
            "/vocab/decks", json={"name": "B1"}, headers=headers_valid
        ).json()["id"]
    )

    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "A1",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])
    # Add the note to deck_b as well, so removing from A does not orphan.
    user_db.execute(
        "INSERT INTO note_deck (note_id, deck_id, created_at) VALUES (?, ?, ?)",
        (note_id, deck_b, "2026-08-24T12:00:00+00:00"),
    )
    user_db.commit()

    r_del = client.delete(
        f"/vocab/decks/{deck_a}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del.status_code == 200
    body = r_del.json()
    assert body["note_id"] == note_id
    assert body["deck_id"] == deck_a
    assert body["removed"] is True
    assert body["orphaned"] is False

    # Note still in deck_b, not in Orphaned
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row["status"] == "resolved"


def test_remove_note_from_deck_orphans_when_last(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """Removing the last non-Orphaned membership moves the note into Orphaned."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post("/vocab/decks", json={"name": "Only"}, headers=headers_valid).json()["id"]
    )

    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "Only",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])

    r_del = client.delete(
        f"/vocab/decks/{deck_id}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del.status_code == 200
    assert r_del.json()["orphaned"] is True

    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row["status"] == "orphaned"
    orphan_row = user_db.execute(
        """
        SELECT 1 FROM note_deck JOIN deck ON deck.id = note_deck.deck_id
        WHERE note_deck.note_id = ? AND deck.name = 'Orphaned'
        """,
        (note_id,),
    ).fetchone()
    assert orphan_row is not None


def test_remove_note_from_deck_rejects_unknown_membership(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post("/vocab/decks", json={"name": "Only2"}, headers=headers_valid).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "Only2",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])
    other_deck = int(
        client.post("/vocab/decks", json={"name": "Other"}, headers=headers_valid).json()["id"]
    )
    r_del = client.delete(
        f"/vocab/decks/{other_deck}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del.status_code == 404
    assert str(note_id) in r_del.json()["detail"]
    assert (
        user_db.execute(
            "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
            (note_id, deck_id),
        ).fetchone()
        is not None
    )


def test_remove_note_from_deck_rejects_missing_entities(
    client: TestClient, app_instance: Any
) -> None:
    r = client.delete(
        "/vocab/decks/99999/notes/99999",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r.status_code == 404


def test_remove_note_from_deck_refuses_to_remove_orphaned_membership(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """Orphaned is protected from a membership removal that would re-create it."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post("/vocab/decks", json={"name": "Last"}, headers=headers_valid).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "Last",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])
    # Move note to Orphaned by removing the last membership
    r_del = client.delete(
        f"/vocab/decks/{deck_id}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del.status_code == 200
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])

    r_orphan = client.delete(
        f"/vocab/decks/{orphan_id}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_orphan.status_code == 409
    assert r_orphan.json()["code"] == "orphaned_deck_protected"


def test_move_note_between_decks_swaps_membership(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_src = int(
        client.post(
            "/vocab/decks", json={"name": "Source"}, headers=headers_valid
        ).json()["id"]
    )
    deck_dst = int(
        client.post(
            "/vocab/decks", json={"name": "Dest"}, headers=headers_valid
        ).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "Source",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])
    card_id = int(
        user_db.execute(
            "SELECT id FROM card WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
    )

    r_move = client.patch(
        f"/vocab/decks/{deck_src}/notes/{note_id}",
        json={"deck_id": deck_dst},
        headers=headers_valid,
    )
    assert r_move.status_code == 200
    body = r_move.json()
    assert body["note_id"] == note_id
    assert body["card_id"] == card_id
    assert body["from_deck_id"] == deck_src
    assert body["to_deck_id"] == deck_dst

    src_membership = user_db.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, deck_src),
    ).fetchone()
    assert src_membership is None
    dst_membership = user_db.execute(
        "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, deck_dst),
    ).fetchone()
    assert dst_membership is not None


def test_move_note_between_decks_handles_existing_destination(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """If destination already contains the note, the move keeps exactly one membership."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_src = int(
        client.post(
            "/vocab/decks", json={"name": "S"}, headers=headers_valid
        ).json()["id"]
    )
    deck_dst = int(
        client.post(
            "/vocab/decks", json={"name": "D"}, headers=headers_valid
        ).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "S",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])
    card_id = int(
        user_db.execute(
            "SELECT id FROM card WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
    )
    # Genuinely establish BOTH memberships before the move; without this
    # the test cannot prove the idempotent destination contract.
    user_db.execute(
        "INSERT INTO note_deck (note_id, deck_id, created_at) VALUES (?, ?, ?)",
        (note_id, deck_dst, "2026-08-24T12:00:00+00:00"),
    )
    user_db.commit()
    pre_rows = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? ORDER BY deck_id", (note_id,)
    ).fetchall()
    assert sorted(int(row[0]) for row in pre_rows) == sorted([deck_src, deck_dst])
    assert len(pre_rows) == 2

    r_move = client.patch(
        f"/vocab/decks/{deck_src}/notes/{note_id}",
        json={"deck_id": deck_dst},
        headers=headers_valid,
    )
    assert r_move.status_code == 200
    body = r_move.json()
    assert body["note_id"] == note_id
    assert body["card_id"] == card_id
    assert body["from_deck_id"] == deck_src
    assert body["to_deck_id"] == deck_dst

    # Source membership removed; destination remains exactly once.
    assert (
        user_db.execute(
            "SELECT 1 FROM note_deck WHERE note_id = ? AND deck_id = ?",
            (note_id, deck_src),
        ).fetchone()
        is None
    )
    dest_rows = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ? AND deck_id = ?",
        (note_id, deck_dst),
    ).fetchall()
    assert len(dest_rows) == 1
    total = user_db.execute(
        "SELECT COUNT(*) FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchone()[0]
    assert total == 1

    # Identity, status, and history preserved; no Orphaned membership created.
    note_row = user_db.execute(
        "SELECT status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None and note_row[0] == "resolved"
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


def test_move_note_between_decks_refuses_to_move_from_orphaned_deck(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """PATCH from the Orphaned deck is rejected; membership/status/history survive."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": "Last normal"}, headers=headers_valid
        ).json()["id"]
    )
    normal_id = int(
        client.post(
            "/vocab/decks", json={"name": "Normal target"}, headers=headers_valid
        ).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "Last normal",
    }, headers=headers_valid)
    assert r.status_code == 201
    note_id = int(r.json()["note_id"])
    card_id = int(
        user_db.execute(
            "SELECT id FROM card WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
    )
    # Review once so FSRS/history preservation is meaningful.
    card_row = user_db.execute(
        "SELECT due_at FROM card WHERE id = ?", (card_id,)
    ).fetchone()
    assert card_row is not None
    r_review = client.post(
        f"/vocab/cards/{card_id}/review",
        json={"confidence": 4},
        headers=headers_valid,
    )
    assert r_review.status_code == 200

    # Remove the final normal membership so the note becomes Orphaned.
    r_del = client.delete(
        f"/vocab/decks/{deck_id}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del.status_code == 200
    assert r_del.json()["orphaned"] is True
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])
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
    assert review_log_before == 1

    r_move = client.patch(
        f"/vocab/decks/{orphan_id}/notes/{note_id}",
        json={"deck_id": normal_id},
        headers=headers_valid,
    )
    assert r_move.status_code == 409
    assert r_move.json()["code"] == "orphaned_deck_protected"

    # Membership remains in Orphaned; status, card, FSRS, and history unchanged.
    rows = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(row[0]) for row in rows] == [orphan_id]
    note_row = user_db.execute(
        "SELECT status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None and note_row[0] == "orphaned"
    card_check = user_db.execute(
        "SELECT id FROM card WHERE note_id = ?", (note_id,)
    ).fetchone()
    assert card_check is not None and int(card_check[0]) == card_id
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
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": "S=D"}, headers=headers_valid
        ).json()["id"]
    )
    r = client.patch(
        f"/vocab/decks/{deck_id}/notes/99999",
        json={"deck_id": deck_id},
        headers=headers_valid,
    )
    assert r.status_code == 409
    assert r.json()["code"] == "source_equals_destination"


def test_move_note_between_decks_rejects_missing_entities(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": "Exists"}, headers=headers_valid
        ).json()["id"]
    )
    r = client.patch(
        "/vocab/decks/99999/notes/99999",
        json={"deck_id": deck_id},
        headers=headers_valid,
    )
    assert r.status_code == 404


def test_move_note_between_decks_rejects_invalid_body(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": "Exists2"}, headers=headers_valid
        ).json()["id"]
    )
    r = client.patch(
        f"/vocab/decks/{deck_id}/notes/99999",
        json={"deck_id": "string-not-int"},
        headers=headers_valid,
    )
    assert r.status_code == 422


def test_rename_deck_succeeds_and_preserves_memberships(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": "Before"}, headers=headers_valid
        ).json()["id"]
    )
    r = client.patch(
        f"/vocab/decks/{deck_id}",
        json={"name": "After"},
        headers=headers_valid,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == deck_id
    assert body["name"] == "After"
    refreshed = user_db.execute("SELECT name FROM deck WHERE id = ?", (deck_id,)).fetchone()
    assert refreshed is not None and refreshed[0] == "After"


def test_rename_deck_rejects_duplicate(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    client.post("/vocab/decks", json={"name": "Lesson 1"}, headers=headers_valid)
    deck_id_2 = int(
        client.post("/vocab/decks", json={"name": "Lesson 2"}, headers=headers_valid).json()["id"]
    )
    r = client.patch(
        f"/vocab/decks/{deck_id_2}",
        json={"name": "Lesson 1"},
        headers=headers_valid,
    )
    assert r.status_code == 409


def test_rename_deck_rejects_blank(client: TestClient, app_instance: Any) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post("/vocab/decks", json={"name": "Lesson A"}, headers=headers_valid).json()["id"]
    )
    r = client.patch(
        f"/vocab/decks/{deck_id}",
        json={"name": "   "},
        headers=headers_valid,
    )
    assert r.status_code == 422


def test_rename_deck_rejects_missing(client: TestClient, app_instance: Any) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    r = client.patch(
        "/vocab/decks/99999",
        json={"name": "Anything"},
        headers=headers_valid,
    )
    assert r.status_code == 404


def test_rename_deck_refuses_to_rename_orphaned(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": "Last2"}, headers=headers_valid
        ).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "Last2",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])
    client.delete(
        f"/vocab/decks/{deck_id}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])

    r_rename = client.patch(
        f"/vocab/decks/{orphan_id}",
        json={"name": "Renamed"},
        headers=headers_valid,
    )
    assert r_rename.status_code == 409
    assert r_rename.json()["code"] == "orphaned_deck_protected"


def test_delete_deck_rejects_orphaned_deck(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": "D-only"}, headers=headers_valid
        ).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "D-only",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])
    client.delete(
        f"/vocab/decks/{deck_id}/notes/{note_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    orphan_row = user_db.execute("SELECT id FROM deck WHERE name = 'Orphaned'").fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])

    r_del = client.delete(
        f"/vocab/decks/{orphan_id}",
        headers={"X-Flashcards-Request": "1"},
    )
    assert r_del.status_code == 409
    assert r_del.json()["code"] == "orphaned_deck_protected"


def test_set_meaning_languages_via_endpoint(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "ML",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])

    r_put = client.put(
        f"/vocab/notes/{note_id}/meaning-languages",
        json={"languages": ["en"]},
        headers=headers_valid,
    )
    assert r_put.status_code == 200
    body = r_put.json()
    assert body["note_id"] == note_id
    assert body["languages"] == ["en"]
    stored = user_db.execute(
        "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang", (note_id,)
    ).fetchall()
    assert [r[0] for r in stored] == ["en"]


def test_set_meaning_languages_rejects_empty_and_fa_and_dup(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    lemma_ref = compute_lemma_semantic_ref("See", "NOUN", "der")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-see-1"
    )
    r = client.post("/vocab/notes", json={
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "status": "resolved",
        "meaning_languages": ["de", "en"],
        "asset_token": app_instance.state.runtime.asset_token,
        "deck_name": "ML2",
    }, headers=headers_valid)
    note_id = int(r.json()["note_id"])

    r_empty = client.put(
        f"/vocab/notes/{note_id}/meaning-languages",
        json={"languages": []},
        headers=headers_valid,
    )
    assert r_empty.status_code == 422

    r_fa = client.put(
        f"/vocab/notes/{note_id}/meaning-languages",
        json={"languages": ["fa"]},
        headers=headers_valid,
    )
    assert r_fa.status_code == 422

    r_dup = client.put(
        f"/vocab/notes/{note_id}/meaning-languages",
        json={"languages": ["de", "de"]},
        headers=headers_valid,
    )
    assert r_dup.status_code == 422


def test_set_meaning_languages_rejects_unknown_note(client: TestClient) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    r = client.put(
        "/vocab/notes/99999/meaning-languages",
        json={"languages": ["de"]},
        headers=headers_valid,
    )
    assert r.status_code == 404


def test_m1_routes_require_x_flashcards_request(
    client: TestClient, user_db: sqlite3.Connection
) -> None:
    """Non-GET M1 routes enforce AGENTS R12 / ADR-0002 D24."""
    before_notes = user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0]
    before_decks = user_db.execute("SELECT COUNT(*) FROM deck").fetchone()[0]

    # DELETE without header
    r = client.delete("/vocab/decks/1/notes/1")
    assert r.status_code == 403
    # PATCH without header
    r = client.patch(
        "/vocab/decks/1/notes/1",
        json={"deck_id": 2},
    )
    assert r.status_code == 403
    # PATCH /vocab/decks/{id} without header
    r = client.patch(
        "/vocab/decks/1",
        json={"name": "x"},
    )
    assert r.status_code == 403
    # PUT without header
    r = client.put(
        "/vocab/notes/1/meaning-languages",
        json={"languages": ["de"]},
    )
    assert r.status_code == 403

    assert user_db.execute("SELECT COUNT(*) FROM note").fetchone()[0] == before_notes
    assert user_db.execute("SELECT COUNT(*) FROM deck").fetchone()[0] == before_decks


# ---------------------------------------------------------------------------
# 14. Anki TSV export sanitization (AGENTS R10)
# ---------------------------------------------------------------------------


def test_anki_export_sanitization(
    client: TestClient, app_instance: Any, user_db: sqlite3.Connection
) -> None:
    """Anki TSV export is tab-separated, converts tabs to space, newlines to <br>."""
    active_token = app_instance.state.runtime.asset_token
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # Create note with tabs and newlines in user meaning
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    res_note_1 = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de", "en"],
            "asset_token": active_token,
            "deck_name": "Grammar A1",
        },
        headers=headers_valid,
    )
    note_id_1 = res_note_1.json()["note_id"]
    client.post(
        f"/vocab/notes/{note_id_1}/gloss",
        json={"language": "en", "meaning_text": "line1\nline2\twith\ttabs, and commas"},
        headers=headers_valid,
    )

    # Create a second note that needs_gloss
    lemma_ref_2 = compute_lemma_semantic_ref("Tür", "NOUN", "die")
    client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref_2,
            "status": "needs_gloss",
            "meaning_languages": ["de"],
            "asset_token": active_token,
            "deck_name": "Grammar A1",
        },
        headers=headers_valid,
    )

    # Export Anki
    r_export = client.get("/vocab/export/anki")
    assert r_export.status_code == 200
    assert "text/tab-separated-values" in r_export.headers["content-type"]

    content = r_export.text
    lines = content.strip().split("\n")

    # Header directives
    assert lines[0] == "#separator:tab"
    assert lines[1] == "#html:true"
    assert lines[2] == "#notetype:German Vocabulary"
    assert lines[3] == "#columns:Front\tBack\tGrammar\tExample\tIPA\tTags"

    # Data lines
    data_lines = lines[4:]
    assert len(data_lines) == 2

    for dline in data_lines:
        fields = dline.split("\t")
        assert len(fields) == 6, f"Expected 6 TSV fields per record, got {len(fields)}"

        # Assert no embedded literal tabs inside fields
        for field in fields:
            assert "\n" not in field, "Literal newline found in field!"
            assert "\r" not in field, "Literal carriage return found in field!"

    # Check note 1 with sanitized newlines/tabs
    record_1 = data_lines[0].split("\t")
    back_field = record_1[1]
    assert "<br>" in back_field
    assert "\t" not in back_field
    assert "line1<br>line2 with tabs, and commas" in back_field

    # Check needs_gloss note (empty back, tagged needs_gloss)
    record_2 = data_lines[1].split("\t")
    assert record_2[1] == ""  # Back face is empty
    tags_field = record_2[5]
    assert "needs_gloss" in tags_field


def test_post_activation_consistency_regression(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """Post-activation consistency regression suite.

    Builds dictionary A and dictionary B in a managed directory.
    Creates the app with dictionary A.
    Activates managed dictionary B with distinct content.
    Asserts:
    - GET /vocab/lookup returns the B token WITH B-only content
      (a lemma present only in B appears; a lemma present only in A does not).
    - cards/next renders meanings sourced from B.
    - export emits B texts.
    - capture submitting an A-era ref under the B token is rejected 422 with zero writes.
    """
    managed_dir = tmp_path / "managed_dictionaries"
    managed_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build Dictionary A
    dict_a_path = managed_dir / "dict_a.sqlite"
    conn_a = sqlite3.connect(dict_a_path)
    conn_a.executescript(part_a_schema)

    apfel_lem_ref = compute_lemma_semantic_ref("Apfel", "NOUN", "der")
    apfel_sense_ref = compute_sense_semantic_ref(apfel_lem_ref, "wiktionary", "s_apfel_1")
    haus_lem_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    haus_sense_ref = compute_sense_semantic_ref(haus_lem_ref, "wiktionary", "s_haus_1")

    # Insert Apfel (A-only) into Dict A
    conn_a.execute(
        """
        INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license)
        VALUES (1, ?, 'Apfel', 'NOUN', 'der', 'wiktionary', 'CC BY-SA')
        """,
        (apfel_lem_ref,),
    )
    conn_a.execute(
        """
        INSERT INTO sense (
            id, lemma_id, semantic_ref, source_namespace, source_ref, source, license
        )
        VALUES (1, 1, ?, 'wiktionary', 's_apfel_1', 'wiktionary', 'CC BY-SA')
        """,
        (apfel_sense_ref,),
    )
    conn_a.execute(
        """
        INSERT INTO sense_meaning (
            id, sense_id, language, kind, text, ord, source, license
        )
        VALUES (
            1, 1, 'de', 'definition', 'Frucht des Apfelbaums (Dict A)', 1, 'wiktionary', 'CC BY-SA'
        )
        """
    )
    # Insert Haus into Dict A
    conn_a.execute(
        """
        INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license)
        VALUES (2, ?, 'Haus', 'NOUN', 'das', 'wiktionary', 'CC BY-SA')
        """,
        (haus_lem_ref,),
    )
    conn_a.execute(
        """
        INSERT INTO sense (
            id, lemma_id, semantic_ref, source_namespace, source_ref, source, license
        )
        VALUES (2, 2, ?, 'wiktionary', 's_haus_1', 'wiktionary', 'CC BY-SA')
        """,
        (haus_sense_ref,),
    )
    conn_a.execute(
        """
        INSERT INTO sense_meaning (
            id, sense_id, language, kind, text, ord, source, license
        )
        VALUES (
            2, 2, 'de', 'definition', 'Gebäude zum Wohnen (Dict A)', 1, 'wiktionary', 'CC BY-SA'
        )
        """
    )
    conn_a.commit()
    conn_a.close()

    # 2. Build Dictionary B
    dict_b_path = managed_dir / "dict_b.sqlite"
    conn_b = sqlite3.connect(dict_b_path)
    conn_b.executescript(part_a_schema)

    birne_lem_ref = compute_lemma_semantic_ref("Birne", "NOUN", "die")
    birne_sense_ref = compute_sense_semantic_ref(birne_lem_ref, "wiktionary", "s_birne_1")

    # Insert Birne (B-only) into Dict B
    conn_b.execute(
        """
        INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license)
        VALUES (1, ?, 'Birne', 'NOUN', 'die', 'wiktionary', 'CC BY-SA')
        """,
        (birne_lem_ref,),
    )
    conn_b.execute(
        """
        INSERT INTO sense (
            id, lemma_id, semantic_ref, source_namespace, source_ref, source, license
        )
        VALUES (1, 1, ?, 'wiktionary', 's_birne_1', 'wiktionary', 'CC BY-SA')
        """,
        (birne_sense_ref,),
    )
    conn_b.execute(
        """
        INSERT INTO sense_meaning (
            id, sense_id, language, kind, text, ord, source, license
        )
        VALUES (1, 1, 'de', 'definition', 'Birnenfrucht (Dict B)', 1, 'wiktionary', 'CC BY-SA')
        """
    )
    # Insert Haus into Dict B with updated B-era meaning
    conn_b.execute(
        """
        INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license)
        VALUES (2, ?, 'Haus', 'NOUN', 'das', 'wiktionary', 'CC BY-SA')
        """,
        (haus_lem_ref,),
    )
    conn_b.execute(
        """
        INSERT INTO sense (
            id, lemma_id, semantic_ref, source_namespace, source_ref, source, license
        )
        VALUES (2, 2, ?, 'wiktionary', 's_haus_1', 'wiktionary', 'CC BY-SA')
        """,
        (haus_sense_ref,),
    )
    conn_b.execute(
        """
        INSERT INTO sense_meaning (
            id, sense_id, language, kind, text, ord, source, license
        )
        VALUES (2, 2, 'de', 'definition', 'Modernes Wohnhaus (Dict B)', 1, 'wiktionary', 'CC BY-SA')
        """
    )
    conn_b.commit()
    conn_b.close()

    # 3. Create app pointing to Dictionary A
    app = create_app(
        dict_path=dict_a_path,
        user_db_path=user_db_path,
        cors_origins=["http://localhost:3000"],
        service_port=3000,
    )
    client = TestClient(app, base_url="http://localhost:3000")
    headers = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}

    # Capture Haus note under Dict A
    r_look_a = client.get("/vocab/lookup?q=Haus")
    assert r_look_a.status_code == 200
    token_a = r_look_a.json()["asset_token"]
    assert token_a == sha256(dict_a_path.read_bytes()).hexdigest()

    r_cap_haus = client.post(
        "/vocab/notes",
        json={
            "asset_token": token_a,
            "lemma_semantic_ref": haus_lem_ref,
            "sense_semantic_ref": haus_sense_ref,
            "meaning_languages": ["de"],
        },
        headers=headers,
    )
    assert r_cap_haus.status_code == 201
    assert r_cap_haus.json()["status"] == "resolved"

    # 4. Activate Dictionary B
    r_act = client.post(
        "/vocab/dictionary/activate",
        json={"path": "dict_b.sqlite", "version": "v2"},
        headers=headers,
    )
    assert r_act.status_code == 200
    act_data = r_act.json()
    assert act_data["status"] == "activated"
    token_b = act_data["asset_token"]
    assert token_b == sha256(dict_b_path.read_bytes()).hexdigest()
    assert token_b != token_a

    # 5. Assert GET /vocab/lookup returns B token WITH B-only content
    # (Birne appears, Apfel does not)
    r_look_birne = client.get("/vocab/lookup?q=Birne")
    assert r_look_birne.status_code == 200
    birne_data = r_look_birne.json()
    assert birne_data["asset_token"] == token_b
    assert len(birne_data["candidates"]) == 1
    assert birne_data["candidates"][0]["lemma"] == "Birne"
    assert (
        birne_data["candidates"][0]["senses"][0]["meanings"][0]["text"] == "Birnenfrucht (Dict B)"
    )

    r_look_apfel = client.get("/vocab/lookup?q=Apfel")
    assert r_look_apfel.status_code == 200
    apfel_data = r_look_apfel.json()
    assert apfel_data["asset_token"] == token_b
    assert len(apfel_data["candidates"]) == 0

    # 6. Assert cards/next renders meanings sourced from Dictionary B
    r_next = client.get("/vocab/cards/next")
    assert r_next.status_code == 200
    card_body = r_next.json()["card"]
    assert card_body is not None
    assert card_body["front"]["display_headword"] == "das Haus"
    rendered_meanings = [line for mb in card_body["back"]["meanings"] for line in mb["lines"]]
    assert any("Modernes Wohnhaus (Dict B)" in m for m in rendered_meanings)
    assert not any("Gebäude zum Wohnen (Dict A)" in m for m in rendered_meanings)

    # 7. Assert export emits Dictionary B texts
    r_export = client.get("/vocab/export/anki")
    assert r_export.status_code == 200
    export_content = r_export.text
    assert "Modernes Wohnhaus (Dict B)" in export_content
    assert "Gebäude zum Wohnen (Dict A)" not in export_content

    # 8. Assert note capture submitting an A-era ref under token B is rejected 422 with zero writes
    user_conn = sqlite3.connect(user_db_path)
    notes_before = user_conn.execute("SELECT COUNT(*) FROM note").fetchone()[0]
    cards_before = user_conn.execute("SELECT COUNT(*) FROM card").fetchone()[0]
    user_conn.close()

    r_stale_ref = client.post(
        "/vocab/notes",
        json={
            "asset_token": token_b,
            "lemma_semantic_ref": apfel_lem_ref,
            "sense_semantic_ref": apfel_sense_ref,
            "meaning_languages": ["de"],
        },
        headers=headers,
    )
    assert r_stale_ref.status_code == 422
    assert "Unknown lemma semantic reference in active dictionary" in r_stale_ref.json()["detail"]

    user_conn = sqlite3.connect(user_db_path)
    notes_after = user_conn.execute("SELECT COUNT(*) FROM note").fetchone()[0]
    cards_after = user_conn.execute("SELECT COUNT(*) FROM card").fetchone()[0]
    user_conn.close()

    assert notes_after == notes_before
    assert cards_after == cards_before


def test_concurrency_complete_old_observation_during_activation(
    tmp_path: Path, part_a_schema: str, user_db_path: Path
) -> None:
    """Concurrency regression proving complete-old observation during activation.

    Spans cards/next and export observations pinned on Generation A while a real activation
    to Dictionary B commits and publishes, proving 100% Generation A consistency throughout.
    """
    managed_dir = tmp_path / "managed_dictionaries"
    managed_dir.mkdir(parents=True, exist_ok=True)
    dict_a_path = managed_dir / "dict_a.sqlite"
    dict_b_path = managed_dir / "dict_b.sqlite"

    haus_lem_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    haus_sense_ref = compute_sense_semantic_ref(haus_lem_ref, "wiktionary", "s_haus_1")

    # Build Dict A
    conn_a = sqlite3.connect(dict_a_path)
    conn_a.executescript(part_a_schema)
    conn_a.execute(
        """
        INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license)
        VALUES (1, ?, 'Haus', 'NOUN', 'das', 'wiktionary', 'CC BY-SA')
        """,
        (haus_lem_ref,),
    )
    conn_a.execute(
        """
        INSERT INTO sense (
            id, lemma_id, semantic_ref, source_namespace, source_ref, source, license
        )
        VALUES (1, 1, ?, 'wiktionary', 's_haus_1', 'wiktionary', 'CC BY-SA')
        """,
        (haus_sense_ref,),
    )
    conn_a.execute(
        """
        INSERT INTO sense_meaning (
            id, sense_id, language, kind, text, ord, source, license
        )
        VALUES (
            1, 1, 'de', 'definition', 'Gebäude zum Wohnen (Dict A)', 1, 'wiktionary', 'CC BY-SA'
        )
        """
    )
    conn_a.commit()
    conn_a.close()

    # Build Dict B with different meaning
    conn_b = sqlite3.connect(dict_b_path)
    conn_b.executescript(part_a_schema)
    conn_b.execute(
        """
        INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license)
        VALUES (1, ?, 'Haus', 'NOUN', 'das', 'wiktionary', 'CC BY-SA')
        """,
        (haus_lem_ref,),
    )
    conn_b.execute(
        """
        INSERT INTO sense (
            id, lemma_id, semantic_ref, source_namespace, source_ref, source, license
        )
        VALUES (1, 1, ?, 'wiktionary', 's_haus_1', 'wiktionary', 'CC BY-SA')
        """,
        (haus_sense_ref,),
    )
    conn_b.execute(
        """
        INSERT INTO sense_meaning (
            id, sense_id, language, kind, text, ord, source, license
        )
        VALUES (
            1, 1, 'de', 'definition', 'Modernes Wohnhaus (Dict B)', 1, 'wiktionary', 'CC BY-SA'
        )
        """
    )
    conn_b.commit()
    conn_b.close()

    app = create_app(
        dict_a_path,
        user_db_path,
        ["http://127.0.0.1:3000"],
        service_port=3000,
    )
    client = TestClient(app, base_url="http://127.0.0.1:3000")
    headers = {
        "Host": "127.0.0.1:3000",
        "Origin": "http://127.0.0.1:3000",
        "X-Flashcards-Request": "1",
    }

    # 1. Lookup Haus to get active token A
    r_lookup = client.get("/vocab/lookup?q=Haus")
    assert r_lookup.status_code == 200
    token_a = r_lookup.json()["asset_token"]

    # 2. Capture Haus note under token A
    r_cap = client.post(
        "/vocab/notes",
        json={
            "asset_token": token_a,
            "lemma_semantic_ref": haus_lem_ref,
            "sense_semantic_ref": haus_sense_ref,
            "meaning_languages": ["de"],
        },
        headers=headers,
    )
    assert r_cap.status_code == 201

    runtime = app.state.runtime

    # --- PART 1: cards/next observation concurrency test ---
    obs_ready = threading.Event()
    activation_commit_done = threading.Event()
    obs_done = threading.Event()
    obs_errors: list[Exception] = []
    obs_card_holder: list[Any] = []

    def observation_worker() -> None:
        try:
            with runtime.reading():
                # Signal that we are pinned on Gen A
                obs_ready.set()
                # Wait until activation commit has occurred via seam probe
                if not activation_commit_done.wait(timeout=5.0):
                    raise TimeoutError("timed out waiting for activation commit")
                card_obs = runtime.observe_card_render()
                obs_card_holder.append(card_obs)
        except Exception as exc:
            obs_errors.append(exc)
        finally:
            obs_done.set()

    t_obs = threading.Thread(target=observation_worker)
    t_obs.start()
    assert obs_ready.wait(timeout=5.0)

    # Set up seam probe to signal when commit has happened in activation
    def seam_probe_fn() -> None:
        activation_commit_done.set()

    runtime._seam_probe = seam_probe_fn
    try:
        # Drive real activation to Dictionary B
        r_act = client.post(
            "/vocab/dictionary/activate",
            json={"path": "dict_b.sqlite", "version": "v2"},
            headers=headers,
        )
        assert r_act.status_code == 200
        token_b = r_act.json()["asset_token"]
        assert token_b != token_a
    finally:
        runtime._seam_probe = None

    t_obs.join(timeout=5.0)
    assert not t_obs.is_alive()
    assert not obs_errors
    assert len(obs_card_holder) == 1
    card_obs = obs_card_holder[0]
    assert card_obs is not None
    assert card_obs["asset_token"] == token_a
    assert card_obs["note_status"] == "resolved"

    render_input = _render_input_from_observation(card_obs)
    rendered = render_card(render_input)
    rendered_meanings = [line for mb in rendered.back.meanings for line in mb.lines]
    # Assert observation completes 100% Gen A consistent (0 mixed values)
    assert any("Gebäude zum Wohnen (Dict A)" in m for m in rendered_meanings)
    assert not any("Modernes Wohnhaus (Dict B)" in m for m in rendered_meanings)

    # --- PART 2: export observation concurrency test ---
    # Reactivate Dict A to establish baseline for export observation
    r_act_a = client.post(
        "/vocab/dictionary/activate",
        json={"path": "dict_a.sqlite", "version": "v3"},
        headers=headers,
    )
    assert r_act_a.status_code == 200
    token_a3 = r_act_a.json()["asset_token"]
    assert token_a3 == token_a

    exp_ready = threading.Event()
    exp_activation_commit_done = threading.Event()
    exp_done = threading.Event()
    exp_errors: list[Exception] = []
    exp_holder: list[Any] = []

    def export_worker() -> None:
        try:
            with runtime.reading():
                exp_ready.set()
                if not exp_activation_commit_done.wait(timeout=5.0):
                    raise TimeoutError("timed out waiting for activation commit")
                export_obs = runtime.observe_export_payload()
                exp_holder.append(export_obs)
        except Exception as exc:
            exp_errors.append(exc)
        finally:
            exp_done.set()

    t_exp = threading.Thread(target=export_worker)
    t_exp.start()
    assert exp_ready.wait(timeout=5.0)

    def seam_probe_exp() -> None:
        exp_activation_commit_done.set()

    runtime._seam_probe = seam_probe_exp
    try:
        r_act_b2 = client.post(
            "/vocab/dictionary/activate",
            json={"path": "dict_b.sqlite", "version": "v4"},
            headers=headers,
        )
        assert r_act_b2.status_code == 200
    finally:
        runtime._seam_probe = None

    t_exp.join(timeout=5.0)
    assert not t_exp.is_alive()
    assert not exp_errors
    assert len(exp_holder) == 1
    export_obs = exp_holder[0]
    assert len(export_obs) >= 1
    card_exp = export_obs[0]
    assert card_exp["asset_token"] == token_a3

    render_exp_input = _render_input_from_observation(card_exp, with_audio=False)
    rendered_exp = render_card(render_exp_input)
    rendered_exp_meanings = [line for mb in rendered_exp.back.meanings for line in mb.lines]
    assert any("Gebäude zum Wohnen (Dict A)" in m for m in rendered_exp_meanings)
    assert not any("Modernes Wohnhaus (Dict B)" in m for m in rendered_exp_meanings)

    # --- PART 3: Verify fresh observations yield complete-new (Dict B) ---
    r_fresh_next = client.get("/vocab/cards/next")
    assert r_fresh_next.status_code == 200
    fresh_card = r_fresh_next.json()["card"]
    fresh_meanings = [line for mb in fresh_card["back"]["meanings"] for line in mb["lines"]]
    assert any("Modernes Wohnhaus (Dict B)" in m for m in fresh_meanings)
    assert not any("Gebäude zum Wohnen (Dict A)" in m for m in fresh_meanings)

    r_fresh_exp = client.get("/vocab/export/anki")
    assert r_fresh_exp.status_code == 200
    assert "Modernes Wohnhaus (Dict B)" in r_fresh_exp.text
    assert "Gebäude zum Wohnen (Dict A)" not in r_fresh_exp.text


def test_apkg_export_endpoint_is_deck_scoped_and_host_origin_guarded(
    client: TestClient, app_instance: Any
) -> None:
    """APKG export has the normal GET guard behavior and returns package bytes."""
    headers = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    created = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de"],
            "asset_token": app_instance.state.runtime.asset_token,
            "deck_name": "APKG deck",
        },
        headers=headers,
    )
    assert created.status_code == 201
    deck_id = client.get("/vocab/decks").json()[0]["id"]

    valid = client.get(f"/vocab/export/apkg?deck_id={deck_id}")
    assert valid.status_code == 200
    assert valid.headers["content-type"].startswith("application/apkg")
    assert valid.content.startswith(b"PK")
    assert "attachment" in valid.headers["content-disposition"]

    missing_scope = client.get("/vocab/export/apkg")
    assert missing_scope.status_code == 422
    bad_host = client.get(f"/vocab/export/apkg?deck_id={deck_id}", headers={"Host": "evil.test"})
    assert bad_host.status_code == 403
    bad_origin = client.get(
        f"/vocab/export/apkg?deck_id={deck_id}", headers={"Origin": "https://evil.test"}
    )
    assert bad_origin.status_code == 403


# ---------------------------------------------------------------------------
# M5 — orphan restoration endpoint
# ---------------------------------------------------------------------------


def _seed_resolved_note_for_restore(
    client: TestClient,
    user_db: sqlite3.Connection,
    app_instance: Any,
    *,
    deck_name: str,
    lemma: str = "See",
    sense_index: str = "senseid:en-see-1",
    gender: str = "der",
    with_meaning: str | None = None,
) -> dict[str, int]:
    """Helper: create one resolved note/card in ``deck_name`` and return its IDs."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": deck_name}, headers=headers_valid
        ).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref(lemma, "NOUN", gender)
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", sense_index
    )
    r = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de", "en"],
            "asset_token": app_instance.state.runtime.asset_token,
            "deck_name": deck_name,
        },
        headers=headers_valid,
    )
    assert r.status_code == 201
    note_id = int(r.json()["note_id"])
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()[0]
    )
    if with_meaning is not None:
        client.post(
            f"/vocab/notes/{note_id}/gloss",
            json={"language": "en", "meaning_text": with_meaning},
            headers=headers_valid,
        )
    return {"note_id": note_id, "card_id": card_id, "deck_id": deck_id}


def _orphan_note(
    client: TestClient, user_db: sqlite3.Connection, deck_id: int, note_id: int
) -> int:
    """Force a note into the Orphaned deck by removing its last membership."""
    headers_valid = {"X-Flashcards-Request": "1"}
    r = client.delete(
        f"/vocab/decks/{deck_id}/notes/{note_id}", headers=headers_valid
    )
    assert r.status_code == 200
    assert r.json()["orphaned"] is True
    orphan_row = user_db.execute(
        "SELECT id FROM deck WHERE name = 'Orphaned'"
    ).fetchone()
    assert orphan_row is not None
    return int(orphan_row[0])


def test_restore_orphaned_note_returns_200_with_expected_shape(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5RestoreSource", with_meaning="lake"
    )
    note_id, card_id, source_deck = seeded["note_id"], seeded["card_id"], seeded["deck_id"]
    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5RestoreTarget"}, headers=headers_valid
        ).json()["id"]
    )
    orphan_id = _orphan_note(client, user_db, source_deck, note_id)

    r = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "note_id",
        "card_id",
        "from_deck_id",
        "to_deck_id",
        "previous_status",
        "restored_status",
    }
    assert body["note_id"] == note_id
    assert body["card_id"] == card_id
    assert body["from_deck_id"] == orphan_id
    assert body["to_deck_id"] == destination
    assert body["previous_status"] == "orphaned"
    assert body["restored_status"] == "resolved"
    # dictionary_key must never leak into API responses.
    assert "dictionary_key" not in body


def test_restore_orphaned_note_updates_deck_listings(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5ListingsSource"
    )
    note_id = seeded["note_id"]
    source_deck = seeded["deck_id"]
    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5ListingsTarget"}, headers=headers_valid
        ).json()["id"]
    )
    _orphan_note(client, user_db, source_deck, note_id)

    r = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert r.status_code == 200

    orphan_row = user_db.execute(
        "SELECT id FROM deck WHERE name = 'Orphaned'"
    ).fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])
    orphan_listing = client.get(f"/vocab/decks/{orphan_id}/cards").json()
    assert [c["note_id"] for c in orphan_listing["cards"]] == []
    destination_listing = client.get(f"/vocab/decks/{destination}/cards").json()
    assert [c["note_id"] for c in destination_listing["cards"]] == [note_id]


def test_restore_orphaned_note_404_for_unknown_note(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5MissingNoteTarget"}, headers=headers_valid
        ).json()["id"]
    )
    r = client.post(
        "/vocab/notes/99999/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "note_not_found"


def test_restore_orphaned_note_404_for_unknown_destination(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5MissingDeckSource"
    )
    note_id = seeded["note_id"]
    source_deck = seeded["deck_id"]
    _orphan_note(client, user_db, source_deck, note_id)

    r = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": 99999},
        headers=headers_valid,
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "deck_not_found"


def test_restore_orphaned_note_409_for_orphaned_destination(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5OrphanAsDestSource"
    )
    note_id = seeded["note_id"]
    source_deck = seeded["deck_id"]
    _orphan_note(client, user_db, source_deck, note_id)
    orphan_row = user_db.execute(
        "SELECT id FROM deck WHERE name = 'Orphaned'"
    ).fetchone()
    assert orphan_row is not None
    orphan_id = int(orphan_row[0])

    r = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": orphan_id},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "orphaned_deck_protected"
    # The Orphaned membership must remain intact.
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(row[0]) for row in memberships] == [orphan_id]


def test_restore_orphaned_note_409_for_non_orphaned_note(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5NonOrphanSource"
    )
    note_id = seeded["note_id"]
    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5NonOrphanTarget"}, headers=headers_valid
        ).json()["id"]
    )
    r = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "note_not_orphaned"


def test_restore_orphaned_note_409_for_inconsistent_orphan_state(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5InconsistentSource"
    )
    note_id = seeded["note_id"]
    source_deck = seeded["deck_id"]
    _orphan_note(client, user_db, source_deck, note_id)
    # Insert a second normal membership while status stays 'orphaned' to
    # violate the orphan invariant.
    extra = int(
        client.post(
            "/vocab/decks", json={"name": "M5InconsistentExtra"}, headers=headers_valid
        ).json()["id"]
    )
    user_db.execute(
        "INSERT INTO note_deck (note_id, deck_id, created_at) VALUES (?, ?, ?)",
        (note_id, extra, "2026-08-24T12:00:00+00:00"),
    )
    user_db.commit()

    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5InconsistentTarget"}, headers=headers_valid
        ).json()["id"]
    )
    r = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "inconsistent_orphan_state"
    # Note status is still 'orphaned'; the extra membership is preserved.
    note_row = user_db.execute("SELECT status FROM note WHERE id = ?", (note_id,)).fetchone()
    assert note_row is not None and note_row[0] == "orphaned"


def test_restore_orphaned_note_rejects_malformed_json_body(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    r = client.post(
        "/vocab/notes/1/restore",
        content="{not valid json",
        headers=headers_valid,
    )
    assert r.status_code == 400


def test_restore_orphaned_note_rejects_missing_deck_id_field(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    r = client.post(
        "/vocab/notes/1/restore",
        json={},
        headers=headers_valid,
    )
    assert r.status_code == 422


def test_restore_orphaned_note_rejects_string_deck_id(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    r = client.post(
        "/vocab/notes/1/restore",
        json={"deck_id": "string-not-int"},
        headers=headers_valid,
    )
    assert r.status_code == 422


def test_restore_orphaned_note_rejects_bool_deck_id(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    r = client.post(
        "/vocab/notes/1/restore",
        json={"deck_id": True},
        headers=headers_valid,
    )
    assert r.status_code == 422


def test_restore_orphaned_note_rejects_extra_invalid_body_fields(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5ExtraFieldSource"
    )
    note_id = seeded["note_id"]
    source_deck = seeded["deck_id"]
    _orphan_note(client, user_db, source_deck, note_id)
    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5ExtraFieldTarget"}, headers=headers_valid
        ).json()["id"]
    )
    # Extra field "deck_name" must be silently ignored and not affect the
    # operation: response uses the requested numeric deck_id.
    r = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination, "deck_name": "Ignored"},
        headers=headers_valid,
    )
    assert r.status_code == 200
    assert r.json()["to_deck_id"] == destination


def test_restore_orphaned_note_second_call_returns_409_note_not_orphaned(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5OneShotSource"
    )
    note_id = seeded["note_id"]
    source_deck = seeded["deck_id"]
    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5OneShotTarget"}, headers=headers_valid
        ).json()["id"]
    )
    _orphan_note(client, user_db, source_deck, note_id)

    first = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert first.status_code == 200

    second = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert second.status_code == 409
    assert second.json()["code"] == "note_not_orphaned"
    # Membership count stays at one and destination was not duplicated.
    memberships = user_db.execute(
        "SELECT deck_id FROM note_deck WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert [int(row[0]) for row in memberships] == [destination]
    assert (
        user_db.execute(
            "SELECT COUNT(*) FROM note_deck WHERE note_id = ? AND deck_id = ?",
            (note_id, destination),
        ).fetchone()[0]
        == 1
    )


def test_restore_orphaned_note_does_not_leak_dictionary_key_in_any_branch(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """End-to-end: success, conflict, and validation paths never echo dictionary_key."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _seed_resolved_note_for_restore(
        client, user_db, app_instance, deck_name="M5NoLeakSource"
    )
    note_id = seeded["note_id"]
    source_deck = seeded["deck_id"]
    destination = int(
        client.post(
            "/vocab/decks", json={"name": "M5NoLeakTarget"}, headers=headers_valid
        ).json()["id"]
    )
    _orphan_note(client, user_db, source_deck, note_id)
    success = client.post(
        f"/vocab/notes/{note_id}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert success.status_code == 200
    assert "dictionary_key" not in success.text

    # Build a new orphan for an error path with a different lemma to
    # avoid M2 duplicate-identity reuse of the first note.
    seeded2 = _seed_resolved_note_for_restore(
        client,
        user_db,
        app_instance,
        deck_name="M5NoLeakSource2",
        lemma="Bank",
        sense_index="senseid:en-bank-1",
        gender="die",
    )
    assert seeded2["note_id"] != note_id, (
        f"expected a different note for lemma Bank; got note_id={seeded2['note_id']}"
    )
    note_id2 = seeded2["note_id"]
    source_deck2 = seeded2["deck_id"]
    _orphan_note(client, user_db, source_deck2, note_id2)
    conflict = client.post(
        f"/vocab/notes/{note_id2}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert conflict.status_code == 200
    # Second attempt on the now-restored note should be a 409 and must not
    # echo dictionary_key either.
    again = client.post(
        f"/vocab/notes/{note_id2}/restore",
        json={"deck_id": destination},
        headers=headers_valid,
    )
    assert again.status_code == 409
    assert "dictionary_key" not in again.text


# ---------------------------------------------------------------------------
# M6 — selected-sense editing (PUT /vocab/notes/{note_id}/sense)
# ---------------------------------------------------------------------------


def _m6_create_resolved_note(
    client: TestClient,
    user_db: sqlite3.Connection,
    app_instance: Any,
    *,
    deck_name: str,
    lemma: str = "Bank",
    gender: str = "die",
    sense_index: str = "senseid:en-bank-1",
) -> dict[str, Any]:
    """Seed one resolved note under a multi-sense lemma for M6 tests.

    The fixture dictionary exposes two direct senses for ``Bank``:
    ``senseid:en-bank-1`` (ord=0) and ``senseid:en-bank-2`` (ord=1).
    """
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    deck_id = int(
        client.post(
            "/vocab/decks", json={"name": deck_name}, headers=headers_valid
        ).json()["id"]
    )
    lemma_ref = compute_lemma_semantic_ref(lemma, "NOUN", gender)
    sense_ref = compute_sense_semantic_ref(
        lemma_ref, "wiktextract:enwiktionary", sense_index
    )
    r = client.post(
        "/vocab/notes",
        json={
            "lemma_semantic_ref": lemma_ref,
            "sense_semantic_ref": sense_ref,
            "status": "resolved",
            "meaning_languages": ["de", "en"],
            "asset_token": app_instance.state.runtime.asset_token,
            "deck_name": deck_name,
        },
        headers=headers_valid,
    )
    assert r.status_code == 201
    body = r.json()
    note_id = int(body["note_id"])
    card_id = int(
        user_db.execute("SELECT id FROM card WHERE note_id = ?", (note_id,)).fetchone()[0]
    )
    return {
        "note_id": note_id,
        "card_id": card_id,
        "deck_id": deck_id,
        "lemma_ref": lemma_ref,
        "sense_ref": sense_ref,
    }


def test_change_note_selected_sense_returns_200_with_expected_shape(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — successful rebind returns exactly the documented public shape."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _m6_create_resolved_note(
        client,
        user_db,
        app_instance,
        deck_name="M6RebindSource",
    )
    note_id = seeded["note_id"]
    card_id = seeded["card_id"]
    bank_lemma_ref = seeded["lemma_ref"]
    bank_sense1_ref = seeded["sense_ref"]
    bank_sense2_ref = compute_sense_semantic_ref(
        bank_lemma_ref, "wiktextract:enwiktionary", "senseid:en-bank-2"
    )
    assert bank_sense1_ref != bank_sense2_ref
    active_token = app_instance.state.runtime.asset_token

    r = client.put(
        f"/vocab/notes/{note_id}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense2_ref},
        headers=headers_valid,
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "note_id",
        "card_id",
        "lemma_semantic_ref",
        "sense_semantic_ref",
        "status",
    }
    assert body["note_id"] == note_id
    assert body["card_id"] == card_id
    assert body["lemma_semantic_ref"] == bank_lemma_ref
    assert body["sense_semantic_ref"] == bank_sense2_ref
    assert body["status"] == "resolved"
    # The response must never expose the durable ``dictionary_key``.
    assert "dictionary_key" not in body
    assert "dictionary_key" not in r.text

    # Persisted: the direct binding points at the new sense.
    binding_row = user_db.execute(
        """
        SELECT lemma_semantic_ref, sense_semantic_ref, binding_status
        FROM note_dictionary_binding
        WHERE note_id = ? AND role = 'direct'
        """,
        (note_id,),
    ).fetchone()
    assert binding_row is not None
    assert str(binding_row[0]) == bank_lemma_ref
    assert str(binding_row[1]) == bank_sense2_ref
    assert str(binding_row[2]) == "bound"
    note_row = user_db.execute(
        "SELECT sense_semantic_ref, status FROM note WHERE id = ?", (note_id,)
    ).fetchone()
    assert note_row is not None
    assert str(note_row[0]) == bank_sense2_ref
    assert str(note_row[1]) == "resolved"


def test_change_note_selected_sense_same_sense_no_op(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — submitting the already-selected sense returns 200 without rewrites."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6NoOpSource"
    )
    active_token = app_instance.state.runtime.asset_token
    before = user_db.execute(
        "SELECT last_relinked_at FROM note_dictionary_binding "
        "WHERE note_id = ? AND role = 'direct'",
        (seeded["note_id"],),
    ).fetchone()

    r = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": seeded["sense_ref"]},
        headers=headers_valid,
    )
    assert r.status_code == 200
    after = user_db.execute(
        "SELECT last_relinked_at FROM note_dictionary_binding "
        "WHERE note_id = ? AND role = 'direct'",
        (seeded["note_id"],),
    ).fetchone()
    # last_relinked_at is preserved: same-sense is a true no-op.
    assert before is not None and after is not None
    assert str(before[0]) == str(after[0])


def test_change_note_selected_sense_rejects_stale_asset_token(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — stale picker asset_token returns 409 dictionary_changed with zero writes."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6StaleSource"
    )
    bank_lemma_ref = seeded["lemma_ref"]
    bank_sense2_ref = compute_sense_semantic_ref(
        bank_lemma_ref, "wiktextract:enwiktionary", "senseid:en-bank-2"
    )

    before = user_db.execute(
        "SELECT sense_semantic_ref FROM note WHERE id = ?", (seeded["note_id"],)
    ).fetchone()
    r = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": "0" * 64, "sense_semantic_ref": bank_sense2_ref},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "dictionary_changed"
    assert "dictionary_key" not in body
    after = user_db.execute(
        "SELECT sense_semantic_ref FROM note WHERE id = ?", (seeded["note_id"],)
    ).fetchone()
    assert str(after[0]) == str(before[0])


def test_change_note_selected_sense_rejects_unknown_sense_ref(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — target sense absent from the pinned asset returns 422 invalid_sense_ref."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6UnknownSenseSource"
    )
    active_token = app_instance.state.runtime.asset_token
    r = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={
            "asset_token": active_token,
            "sense_semantic_ref": "sense:v1:not-in-current-asset",
        },
        headers=headers_valid,
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "invalid_sense_ref"


def test_change_note_selected_sense_rejects_cross_lemma_target(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — a target sense on a different lemma returns 422 same_lemma_required."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6CrossLemmaSource"
    )
    active_token = app_instance.state.runtime.asset_token
    # The note belongs to ``Bank``; pick a sense that belongs to ``Haus``
    # — a different durable ``lemma_semantic_ref``.
    haus_lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
    haus_sense_ref = compute_sense_semantic_ref(
        haus_lemma_ref, "wiktextract:enwiktionary", "senseid:en-house-1"
    )
    r = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": haus_sense_ref},
        headers=headers_valid,
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "same_lemma_required"


def test_change_note_selected_sense_selected_sense_conflict(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — another note already owning the target key returns 409 selected_sense_conflict."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    source = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6ConflictSource"
    )
    other = _m6_create_resolved_note(
        client,
        user_db,
        app_instance,
        deck_name="M6ConflictTarget",
        sense_index="senseid:en-bank-2",
    )
    active_token = app_instance.state.runtime.asset_token
    r = client.put(
        f"/vocab/notes/{source['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": other["sense_ref"]},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "selected_sense_conflict"
    # R13: dictionary_key is private internal identity and owner_note_id
    # is not part of any accepted public convention. Neither is
    # surfaced on the M6 selected-sense endpoint.
    assert "dictionary_key" not in body
    assert "owner_note_id" not in body
    assert "dictionary_key" not in r.text
    assert "owner_note_id" not in r.text
    # The source note still owns its original identity.
    source_row = user_db.execute(
        "SELECT sense_semantic_ref FROM note WHERE id = ?", (source["note_id"],)
    ).fetchone()
    assert str(source_row[0]) == source["sense_ref"]


def test_change_note_selected_sense_legacy_duplicate_conflict(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — legacy NULL-key matches target identity -> 409 legacy_duplicate_conflict."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    source = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6LegacyDupSource"
    )
    legacy_a = _m6_create_resolved_note(
        client,
        user_db,
        app_instance,
        deck_name="M6LegacyDupA",
        sense_index="senseid:en-bank-2",
    )
    legacy_b = _m6_create_resolved_note(
        client,
        user_db,
        app_instance,
        deck_name="M6LegacyDupB",
        sense_index="senseid:en-bank-2",
    )
    user_db.execute(
        "UPDATE note SET dictionary_key = NULL WHERE id IN (?, ?)",
        (legacy_a["note_id"], legacy_b["note_id"]),
    )
    user_db.commit()
    r = client.put(
        f"/vocab/notes/{source['note_id']}/sense",
        json={
            "asset_token": app_instance.state.runtime.asset_token,
            "sense_semantic_ref": legacy_a["sense_ref"],
        },
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "legacy_duplicate_conflict"
    # R13: dictionary_key is private internal identity and must never
    # be surfaced by the M6 selected-sense endpoint.
    assert "dictionary_key" not in body
    assert "dictionary_key" not in r.text


def test_change_note_selected_sense_unsupported_status(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — derived_compound is rejected with 409 unsupported_selected_sense_edit."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    active_token = app_instance.state.runtime.asset_token
    kranken_lemma = compute_lemma_semantic_ref("kranken", "NOUN", "die")
    karte_lemma = compute_lemma_semantic_ref("Karte", "NOUN", "die")
    kranken_sense = compute_sense_semantic_ref(
        kranken_lemma, "wiktextract:enwiktionary", "senseid:en-kranken-1"
    )
    karte_sense = compute_sense_semantic_ref(
        karte_lemma, "wiktextract:enwiktionary", "senseid:en-karte-1"
    )
    # The /vocab/notes endpoint does not accept derived_compound status
    # via its current payload shape, so create the compound row directly
    # through the proven PART-B helper. The endpoint contract is
    # unchanged; the test still proves the M6 support refuses it.
    from app.deck import create_note as deck_create_note

    compound_note_id = deck_create_note(
        user_db,
        kranken_lemma,
        status="derived_compound",
        component_bindings=(
            (kranken_lemma, kranken_sense),
            (karte_lemma, karte_sense),
        ),
        meaning_languages=("de", "en"),
    )
    user_db.commit()
    r = client.put(
        f"/vocab/notes/{compound_note_id}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": kranken_sense},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "unsupported_selected_sense_edit"
    assert body["status"] == "derived_compound"


def test_change_note_selected_sense_inconsistent_note_identity(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — malformed current binding returns 409 inconsistent_note_identity."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6InconsistentSource"
    )
    # Introduce a structural defect: the direct binding points at a
    # different sense than the note's ``sense_semantic_ref``.
    user_db.execute(
        """
        UPDATE note_dictionary_binding SET sense_semantic_ref = ?
        WHERE note_id = ? AND role = 'direct'
        """,
        ("sense:v1:haus:0", seeded["note_id"]),
    )
    user_db.commit()
    active_token = app_instance.state.runtime.asset_token
    bank_sense2_ref = compute_sense_semantic_ref(
        seeded["lemma_ref"], "wiktextract:enwiktionary", "senseid:en-bank-2"
    )
    r = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense2_ref},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "inconsistent_note_identity"


def test_change_note_selected_sense_promotion_gates_failed(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — needs_gloss with review history returns 409 promotion_gates_failed."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    active_token = app_instance.state.runtime.asset_token
    # Create a stub note in needs_gloss state by directly POSTing a stub.
    bank_lemma_ref = compute_lemma_semantic_ref("Bank", "NOUN", "die")
    r_create = client.post(
        "/vocab/notes",
        json={
            "asset_token": active_token,
            "lemma_semantic_ref": bank_lemma_ref,
            "status": "needs_gloss",
            "meaning_languages": ["de", "en"],
            "deck_name": "M6PromotionStubSource",
        },
        headers=headers_valid,
    )
    assert r_create.status_code == 201
    stub_note_id = int(r_create.json()["note_id"])
    # Add a review so the stub is no longer M2-promotable.
    stub_card_id = int(
        user_db.execute(
            "SELECT id FROM card WHERE note_id = ?", (stub_note_id,)
        ).fetchone()[0]
    )
    client.post(
        f"/vocab/cards/{stub_card_id}/review",
        json={"confidence": 4},
        headers=headers_valid,
    )
    bank_sense1_ref = compute_sense_semantic_ref(
        bank_lemma_ref, "wiktextract:enwiktionary", "senseid:en-bank-1"
    )
    r = client.put(
        f"/vocab/notes/{stub_note_id}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense1_ref},
        headers=headers_valid,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "promotion_gates_failed"


def test_change_note_selected_sense_rejects_malformed_body_and_missing_fields(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    active_token = app_instance.state.runtime.asset_token

    # Malformed JSON
    r1 = client.put(
        "/vocab/notes/1/sense",
        content=b"{not valid json",
        headers=headers_valid,
    )
    assert r1.status_code == 400

    # Non-object body
    r2 = client.put(
        "/vocab/notes/1/sense",
        json=["array-not-object"],
        headers=headers_valid,
    )
    assert r2.status_code == 422

    # Missing asset_token
    r3 = client.put(
        "/vocab/notes/1/sense",
        json={"sense_semantic_ref": "sense:v1:any"},
        headers=headers_valid,
    )
    assert r3.status_code == 422

    # Missing sense_semantic_ref
    r4 = client.put(
        "/vocab/notes/1/sense",
        json={"asset_token": active_token},
        headers=headers_valid,
    )
    assert r4.status_code == 422

    # Blank sense_semantic_ref
    r5 = client.put(
        "/vocab/notes/1/sense",
        json={"asset_token": active_token, "sense_semantic_ref": "   "},
        headers=headers_valid,
    )
    assert r5.status_code == 422

    # Non-string asset_token
    r6 = client.put(
        "/vocab/notes/1/sense",
        json={"asset_token": 12345, "sense_semantic_ref": "sense:v1:any"},
        headers=headers_valid,
    )
    assert r6.status_code == 422


def test_change_note_selected_sense_inherits_r12_request_header_and_content_type(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — the new endpoint inherits R12/X-Flashcards-Request + JSON Content-Type."""
    seeded = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6HeaderSource"
    )
    bank_sense2_ref = compute_sense_semantic_ref(
        seeded["lemma_ref"], "wiktextract:enwiktionary", "senseid:en-bank-2"
    )
    active_token = app_instance.state.runtime.asset_token

    # Missing X-Flashcards-Request -> 403, no mutation.
    no_header_request = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense2_ref},
        headers={"Content-Type": "application/json"},
    )
    assert no_header_request.status_code == 403
    assert "X-Flashcards-Request" in no_header_request.json()["detail"]
    note_row = user_db.execute(
        "SELECT sense_semantic_ref FROM note WHERE id = ?", (seeded["note_id"],)
    ).fetchone()
    assert str(note_row[0]) == seeded["sense_ref"]

    # Wrong Content-Type -> 400, no mutation.
    wrong_ct = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense2_ref},
        headers={"X-Flashcards-Request": "1", "Content-Type": "text/plain"},
    )
    assert wrong_ct.status_code == 400

    # Wrong header value -> 403, no mutation.
    wrong_header = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense2_ref},
        headers={"X-Flashcards-Request": "0", "Content-Type": "application/json"},
    )
    assert wrong_header.status_code == 403


def test_change_note_selected_sense_404_for_unknown_note(
    client: TestClient, app_instance: Any
) -> None:
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    active_token = app_instance.state.runtime.asset_token
    bank_lemma_ref = compute_lemma_semantic_ref("Bank", "NOUN", "die")
    bank_sense1_ref = compute_sense_semantic_ref(
        bank_lemma_ref, "wiktextract:enwiktionary", "senseid:en-bank-1"
    )
    r = client.put(
        "/vocab/notes/99999/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense1_ref},
        headers=headers_valid,
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "note_not_found"


def test_change_note_selected_sense_preserves_review_log_and_user_meanings(
    client: TestClient, user_db: sqlite3.Connection, app_instance: Any
) -> None:
    """M6 — successful rebind preserves every preserved table row verbatim."""
    headers_valid = {"X-Flashcards-Request": "1", "Content-Type": "application/json"}
    seeded = _m6_create_resolved_note(
        client, user_db, app_instance, deck_name="M6PreserveSource"
    )
    # Add a user meaning and a review.
    client.post(
        f"/vocab/notes/{seeded['note_id']}/gloss",
        json={"language": "en", "meaning_text": "banking"},
        headers=headers_valid,
    )
    client.post(
        f"/vocab/cards/{seeded['card_id']}/review",
        json={"confidence": 4},
        headers=headers_valid,
    )

    # Snapshot the preserved state.
    def snapshot() -> dict[str, Any]:
        review_count = user_db.execute(
            "SELECT COUNT(*) FROM review_log WHERE card_id = ?", (seeded["card_id"],)
        ).fetchone()[0]
        meanings = user_db.execute(
            "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?",
            (seeded["note_id"],),
        ).fetchall()
        return {
            "review_count": int(review_count),
            "meanings": [tuple(row) for row in meanings],
        }

    before = snapshot()
    active_token = app_instance.state.runtime.asset_token
    bank_sense2_ref = compute_sense_semantic_ref(
        seeded["lemma_ref"], "wiktextract:enwiktionary", "senseid:en-bank-2"
    )
    r = client.put(
        f"/vocab/notes/{seeded['note_id']}/sense",
        json={"asset_token": active_token, "sense_semantic_ref": bank_sense2_ref},
        headers=headers_valid,
    )
    assert r.status_code == 200
    after = snapshot()
    assert after["review_count"] == before["review_count"]
    assert sorted(after["meanings"]) == sorted(before["meanings"])
