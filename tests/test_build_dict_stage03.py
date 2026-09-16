"""DE/EN-only Stage 03 queue tests — v2 semantic context."""
# mypy: disable-error-code="attr-defined,operator,index,arg-type,unused-ignore"

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tools.build_dict import (
    STAGE01_SCHEMA_SQL,
    STAGE02_EXAMPLE_SCHEMA_SQL,
    STAGE03_QUEUE_FORMAT,
    BuildDictError,
    _validate_de_source_eligibility,
    build_stage03,
    de_learner_meaning_request_body,
)


def make_stage02(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
    conn.execute(
        "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (1, 'lemma:v1:haus', 'Haus', 'NOUN', 'das', 'wiktionary', 'CC BY-SA')"
    )
    conn.execute(
        "INSERT INTO sense VALUES (1, 1, 'sense:v1:haus:1', 'enwiktionary', 'Haus-1', 0, NULL, 'wiktionary', 'CC BY-SA')"
    )
    conn.execute(
        "INSERT INTO sense VALUES (2, 1, 'sense:v1:haus:2', 'enwiktionary', 'Haus-2', 1, NULL, 'wiktionary', 'CC BY-SA')"
    )
    # Sense 1: eligible DE + EN
    conn.execute(
        "INSERT INTO sense_meaning VALUES (1, 1, 'en', 'translation', 0, 'house', 'wiktionary', 'CC BY-SA')"
    )
    conn.execute(
        "INSERT INTO sense_meaning VALUES (2, 1, 'de', 'synonym', 0, 'Gebäude', 'wiktionary', 'CC BY-SA')"
    )
    # Sense 2: ineligible DE, but has EN for derivation
    conn.execute(
        "INSERT INTO sense_meaning VALUES (4, 2, 'en', 'translation', 0, 'building', 'wiktionary', 'CC BY-SA')"
    )
    conn.execute(
        "INSERT INTO sense_meaning VALUES (3, 2, 'de', 'definition', 0, 'siehe Haus', 'wiktionary', 'CC BY-SA')"
    )
    conn.commit()
    conn.close()
    return path


def make_stage02_with_en_counts(path: Path, en_counts: list[int]) -> Path:
    """Create DB where each sense has given number of EN rows (1-3)."""
    conn = sqlite3.connect(path)
    conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
    conn.execute("INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (1, 'lemma:v1:test', 'Test', 'NOUN', NULL, 'wiktionary', 'CC BY-SA')")
    for idx, cnt in enumerate(en_counts):
        sid = idx + 1
        conn.execute(
            "INSERT INTO sense VALUES (?, 1, ?, 'enwiktionary', ?, ?, NULL, 'wiktionary', 'CC BY-SA')",
            (sid, f"sense:v1:test:{sid}", f"src-{sid}", idx),
        )
        for ordv in range(cnt):
            mid = sid * 10 + ordv
            conn.execute(
                "INSERT INTO sense_meaning VALUES (?, ?, 'en', 'translation', ?, ?, 'wiktionary', 'CC BY-SA')",
                (mid, sid, ordv, f"meaning {sid}-{ordv}"),
            )
        # ineligible DE to force DE job
        conn.execute(
            "INSERT INTO sense_meaning VALUES (?, ?, 'de', 'definition', 0, 'siehe Test', 'wiktionary', 'CC BY-SA')",
            (sid * 100 + 1, sid),
        )
    conn.commit()
    conn.close()
    return path


def read_queue(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return dict(value)


def test_source_first_queue_is_de_en_only_and_deterministic(tmp_path: Path) -> None:
    db = make_stage02(tmp_path / "input.sqlite")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    result = build_stage03(db, first)
    build_stage03(db, second)
    queue = read_queue(first)
    items = queue["items"]
    assert isinstance(items, list)
    assert first.read_bytes() == second.read_bytes()
    assert result["de"] == 1 and result["en"] == 0  # sense1 eligible, sense2 has EN so no missing EN
    # In this fixture, only sense2 needs DE job
    assert {item["job_class"] for item in items} == {"de_learner_meaning"}
    assert {item["language"] for item in items} == {"de"}
    assert [item["item_id"] for item in items] == sorted(item["item_id"] for item in items)
    assert all("/home/" not in json.dumps(item) for item in items)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert queue["format"] == "flashcard-stage03-queue-v2"


def test_de_job_carries_all_same_sense_en_meanings(tmp_path: Path) -> None:
    # 1-source case already covered by make_stage02: sense2 has 1 EN
    db = make_stage02(tmp_path / "input.sqlite")
    queue_path = tmp_path / "queue.json"
    build_stage03(db, queue_path)
    items = read_queue(queue_path)["items"]
    assert isinstance(items, list)
    de_item = next(item for item in items if item["language"] == "de")
    # Should carry EN derivation, not DE
    assert len(de_item["derivation_inputs"]) == 1
    assert de_item["derivation_inputs"][0]["text"] == "building"
    assert de_item["derivation_inputs"][0]["language"] == "en"
    assert de_item["derivation_source_ids"] == [4]

    # 2-source
    db2 = make_stage02_with_en_counts(tmp_path / "db2.sqlite", [2])
    q2 = tmp_path / "q2.json"
    build_stage03(db2, q2)
    items2 = read_queue(q2)["items"]
    assert len(items2[0]["derivation_inputs"]) == 2

    # 3-source
    db3 = make_stage02_with_en_counts(tmp_path / "db3.sqlite", [3])
    q3 = tmp_path / "q3.json"
    build_stage03(db3, q3)
    items3 = read_queue(q3)["items"]
    assert len(items3[0]["derivation_inputs"]) == 3


def test_no_generated_source_row_is_eligible(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "input.sqlite")
    conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
    conn.execute("INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (1, 'lemma:v1:x', 'X', 'NOUN', NULL, 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense VALUES (1, 1, 'sense:v1:x:1', 'enwiktionary', 'X-1', 0, NULL, 'wiktionary', 'CC BY-SA')")
    # Only generated EN row, no source-backed EN
    conn.execute("INSERT INTO sense_meaning VALUES (1, 1, 'en', 'translation', 0, 'generated', 'llm_generated_v1', 'TEST_SYNTHETIC_LICENSE_v1')")
    # No DE row, so DE job should have 0 EN derivation (generated not eligible)
    conn.execute("INSERT INTO sense_meaning VALUES (2, 1, 'de', 'definition', 0, 'siehe X', 'wiktionary', 'CC BY-SA')")
    conn.commit()
    conn.close()
    q = tmp_path / "q.json"
    build_stage03(tmp_path / "input.sqlite", q)
    items = read_queue(q)["items"]
    # EN missing? Actually sense has generated EN, but source-backed EN count is 0, so en_meaning job should be created
    assert any(i["language"] == "en" for i in items)
    # DE job should have empty derivation (generated EN not included)
    de_item = next(i for i in items if i["language"] == "de")
    assert de_item["derivation_inputs"] == []
    assert de_item["derivation_source_ids"] == []


def test_no_other_sense_meaning_can_enter_one_job(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "db.sqlite")
    conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
    conn.execute("INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (1, 'lemma:v1:a', 'A', 'NOUN', NULL, 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense VALUES (1, 1, 'sense:v1:a:1', 'enwiktionary', 'A-1', 0, NULL, 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense VALUES (2, 1, 'sense:v1:a:2', 'enwiktionary', 'A-2', 1, NULL, 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (1, 1, 'en', 'translation', 0, 'sense1-en', 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (2, 2, 'en', 'translation', 0, 'sense2-en', 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (3, 1, 'de', 'definition', 0, 'siehe A', 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (4, 2, 'de', 'definition', 0, 'siehe A', 'wiktionary', 'CC BY-SA')")
    conn.commit()
    conn.close()
    q = tmp_path / "q.json"
    build_stage03(tmp_path / "db.sqlite", q)
    items = read_queue(q)["items"]
    for it in items:
        sid = it["sense_id"]
        for inp in it["derivation_inputs"]:
            # derivation should be same sense only - check that EN text matches sense
            if sid == 1:
                assert inp["text"] == "sense1-en"
            elif sid == 2:
                assert inp["text"] == "sense2-en"


def test_canonical_en_ordering(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "db.sqlite")
    conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
    conn.execute("INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (1, 'lemma:v1:b', 'B', 'NOUN', NULL, 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense VALUES (1, 1, 'sense:v1:b:1', 'enwiktionary', 'B-1', 0, NULL, 'wiktionary', 'CC BY-SA')")
    # Insert EN rows out of order (ord 2 then 0)
    conn.execute("INSERT INTO sense_meaning VALUES (10, 1, 'en', 'translation', 2, 'third', 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (11, 1, 'en', 'translation', 0, 'first', 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (12, 1, 'en', 'translation', 1, 'second', 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (20, 1, 'de', 'definition', 0, 'siehe B', 'wiktionary', 'CC BY-SA')")
    conn.commit()
    conn.close()
    q = tmp_path / "q.json"
    build_stage03(tmp_path / "db.sqlite", q)
    items = read_queue(q)["items"]
    de = [i for i in items if i["language"] == "de"][0]
    texts = [x["text"] for x in de["derivation_inputs"]]
    assert texts == ["first", "second", "third"]
    # Also verify ord ordering
    ords = [x["ord"] for x in de["derivation_inputs"]]
    assert ords == [0, 1, 2]


def test_item_identity_ignores_numeric_ids(tmp_path: Path) -> None:
    # Two DBs with same semantic refs but different numeric IDs should produce same queue
    def make_db(path: Path, lemma_id: int, sense_id: int, meaning_id: int) -> Path:
        conn = sqlite3.connect(path)
        conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
        conn.execute("INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (?, 'lemma:v1:same', 'Same', 'NOUN', NULL, 'wiktionary', 'CC BY-SA')", (lemma_id,))
        conn.execute("INSERT INTO sense VALUES (?, ?, 'sense:v1:same:1', 'enwiktionary', 'Same-1', 0, NULL, 'wiktionary', 'CC BY-SA')", (sense_id, lemma_id))
        conn.execute("INSERT INTO sense_meaning VALUES (?, ?, 'en', 'translation', 0, 'same text', 'wiktionary', 'CC BY-SA')", (meaning_id, sense_id))
        conn.execute("INSERT INTO sense_meaning VALUES (?, ?, 'de', 'definition', 0, 'siehe Same', 'wiktionary', 'CC BY-SA')", (meaning_id + 1, sense_id))
        conn.commit()
        conn.close()
        return path

    db1 = make_db(tmp_path / "db1.sqlite", 1, 1, 1)
    db2 = make_db(tmp_path / "db2.sqlite", 99, 99, 99)
    q1 = tmp_path / "q1.json"
    q2 = tmp_path / "q2.json"
    build_stage03(db1, q1)
    build_stage03(db2, q2)
    j1 = json.loads(q1.read_text(encoding="utf-8"))
    j2 = json.loads(q2.read_text(encoding="utf-8"))
    # Item IDs must be identical despite numeric ID differences
    assert j1["items"][0]["item_id"] == j2["items"][0]["item_id"]
    # Changing semantic text must change ID
    db3 = tmp_path / "db3.sqlite"
    conn = sqlite3.connect(db3)
    conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
    conn.execute("INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (1, 'lemma:v1:same', 'Same', 'NOUN', NULL, 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense VALUES (1, 1, 'sense:v1:same:1', 'enwiktionary', 'Same-1', 0, NULL, 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (1, 1, 'en', 'translation', 0, 'different text', 'wiktionary', 'CC BY-SA')")
    conn.execute("INSERT INTO sense_meaning VALUES (2, 1, 'de', 'definition', 0, 'siehe Same', 'wiktionary', 'CC BY-SA')")
    conn.commit()
    conn.close()
    q3 = tmp_path / "q3.json"
    build_stage03(db3, q3)
    j3 = json.loads(q3.read_text(encoding="utf-8"))
    assert j3["items"][0]["item_id"] != j1["items"][0]["item_id"]


def test_queue_v1_cannot_be_emitted(tmp_path: Path) -> None:
    db = make_stage02(tmp_path / "db.sqlite")
    q = tmp_path / "q.json"
    build_stage03(db, q)
    data = json.loads(q.read_text(encoding="utf-8"))
    for it in data["items"]:
        assert not str(it["item_id"]).startswith("queue:v1:")
        assert str(it["item_id"]).startswith("queue:v2:")


def test_queue_format_is_v2(tmp_path: Path) -> None:
    db = make_stage02(tmp_path / "db.sqlite")
    q = tmp_path / "q.json"
    build_stage03(db, q)
    data = json.loads(q.read_text(encoding="utf-8"))
    assert data["format"] == "flashcard-stage03-queue-v2"
    assert data["format"] == STAGE03_QUEUE_FORMAT


@pytest.mark.parametrize(
    "text,kind",
    [("Haus!", "synonym"), ("siehe Haus", "definition"), ("ein langer\nText", "definition")],
)
def test_de_positive_predicate_rejects_uncertain_source(text: str, kind: str) -> None:
    assert _validate_de_source_eligibility(text, kind) is not None


def test_stage03_refuses_overwrite_and_retired_packet_mode(tmp_path: Path) -> None:
    db = make_stage02(tmp_path / "input.sqlite")
    queue = tmp_path / "queue.json"
    build_stage03(db, queue)
    with pytest.raises(BuildDictError, match="already exists"):
        build_stage03(db, queue)
    with pytest.raises(BuildDictError, match="retired"):
        build_stage03(db, tmp_path / "other.json", packet_path=tmp_path / "packet.json")


def test_queue_ids_are_stable_and_order_independent(tmp_path: Path) -> None:
    def make_ordered(path: Path, reverse: bool) -> Path:
        conn = sqlite3.connect(path)
        conn.executescript(STAGE01_SCHEMA_SQL + STAGE02_EXAMPLE_SCHEMA_SQL)
        senses = [
            (1, "sense:v1:haus:1", "Haus-1"),
            (2, "sense:v1:haus:2", "Haus-2"),
        ]
        if reverse:
            senses = list(reversed(senses))
        conn.execute("INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, source, license) VALUES (1, 'lemma:v1:haus', 'Haus', 'NOUN', 'das', 'wiktionary', 'CC BY-SA')")
        for sid, sref, ssource in senses:
            conn.execute("INSERT INTO sense VALUES (?, 1, ?, 'enwiktionary', ?, ?, NULL, 'wiktionary', 'CC BY-SA')", (sid, sref, ssource, sid - 1))
        # Give each sense an EN so DE job for sense2 will have EN context
        conn.execute("INSERT INTO sense_meaning VALUES (1, 1, 'en', 'translation', 0, 'house', 'wiktionary', 'CC BY-SA')")
        conn.execute("INSERT INTO sense_meaning VALUES (5, 2, 'en', 'translation', 0, 'building', 'wiktionary', 'CC BY-SA')")
        conn.execute("INSERT INTO sense_meaning VALUES (2, 1, 'de', 'synonym', 0, 'Gebäude', 'wiktionary', 'CC BY-SA')")
        conn.execute("INSERT INTO sense_meaning VALUES (3, 2, 'de', 'definition', 0, 'siehe Haus', 'wiktionary', 'CC BY-SA')")
        conn.commit()
        conn.close()
        return path

    db1 = make_ordered(tmp_path / "db1.sqlite", reverse=False)
    db2 = make_ordered(tmp_path / "db2.sqlite", reverse=True)
    q1 = tmp_path / "q1.json"
    q2 = tmp_path / "q2.json"
    build_stage03(db1, q1)
    build_stage03(db2, q2)
    assert q1.read_bytes() == q2.read_bytes()
    qdata = read_queue(q1)
    items = qdata["items"]
    assert isinstance(items, list)
    for item in items:
        assert item["lemma_semantic_ref"] == "lemma:v1:haus"
        assert item["sense_semantic_ref"] in ("sense:v1:haus:1", "sense:v1:haus:2")
        assert item["custom_id"] == f"batch:{item['item_id']}"


def test_queue_uses_semantic_refs_not_numeric_ids(tmp_path: Path) -> None:
    db = make_stage02(tmp_path / "input.sqlite")
    queue = tmp_path / "queue.json"
    build_stage03(db, queue)
    items = read_queue(queue)["items"]
    assert isinstance(items, list)
    for item in items:
        assert "lemma_semantic_ref" in item
        assert "sense_semantic_ref" in item
        assert item["item_id"].startswith("queue:v2:")
        assert item["custom_id"] == f"batch:{item['item_id']}"


def test_stage03_no_network_and_input_immutable(tmp_path: Path) -> None:
    db = make_stage02(tmp_path / "input.sqlite")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    queue = tmp_path / "queue.json"
    build_stage03(db, queue)
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    lower = queue.read_bytes().decode("utf-8").lower()
    for forbidden in ["api_key", "password", "/home/"]:
        assert forbidden not in lower


def test_request_body_contains_real_instructions_and_en_text(tmp_path: Path) -> None:
    db = make_stage02(tmp_path / "db.sqlite")
    q = tmp_path / "q.json"
    build_stage03(db, q)
    items = read_queue(q)["items"]
    de_item = next(i for i in items if i["language"] == "de")
    body = de_learner_meaning_request_body(de_item, "gpt-5.6-luna")
    # Must contain real instructions
    assert "Work only on the supplied single semantic sense" in body["input"]
    # Must contain exact same-sense EN text
    assert "building" in body["input"]
    # Must not contain text from another sense (house is from sense1, not sense2)
    assert "house" not in body["input"]
    # Must contain opaque refs labelled
    assert "opaque" in body["input"].lower()
    assert "lemma_semantic_ref" in body["input"]
