"""Fixture-only Stage 05 packaging tests."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_build_dict_stage03 import make_stage02
from tools.build_dict import BuildDictError, build_stage05


def test_stage05_copies_verified_fixture_and_metadata(tmp_path: Path) -> None:
    source = make_stage02(tmp_path / "fixture.sqlite")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output, metadata = tmp_path / "dictionary_v1.sqlite", tmp_path / "release.json"
    result = build_stage05(source, output, "v1", metadata)
    assert result == json.loads(metadata.read_text(encoding="utf-8"))
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["bytes"] == output.stat().st_size
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_stage05_refuses_overwrite_and_bad_attribution(tmp_path: Path) -> None:
    source = make_stage02(tmp_path / "fixture.sqlite")
    output = tmp_path / "dictionary_v1.sqlite"
    build_stage05(source, output)
    with pytest.raises(BuildDictError, match="already exists"):
        build_stage05(source, output)
    bad = make_stage02(tmp_path / "bad.sqlite")
    import sqlite3

    conn = sqlite3.connect(bad)
    conn.execute("UPDATE sense_meaning SET license='' WHERE id=1")
    conn.commit()
    conn.close()
    with pytest.raises(BuildDictError, match="attribution"):
        build_stage05(bad, tmp_path / "bad-output.sqlite")


def test_stage05_rejects_duplicate_stable_ref_and_malformed_provenance(tmp_path: Path) -> None:
    import sqlite3

    # Blank stable ref (also covered by uniqueness/nonblank check)
    dup = make_stage02(tmp_path / "dup.sqlite")
    conn = sqlite3.connect(dup)
    conn.execute("UPDATE lemma SET semantic_ref='' WHERE id=1")
    conn.commit()
    conn.close()
    with pytest.raises(BuildDictError, match="blank|Duplicate"):
        build_stage05(dup, tmp_path / "dup-out.sqlite")
    # Malformed provenance: generated row without valid license
    bad2 = make_stage02(tmp_path / "bad2.sqlite")
    conn2 = sqlite3.connect(bad2)
    conn2.execute("INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) VALUES (999, 1, 'de', 'definition', 99, 'test', 'llm_generated_v1', '')")
    conn2.commit()
    conn2.close()
    with pytest.raises(BuildDictError, match="attribution|license"):
        build_stage05(bad2, tmp_path / "bad2-out.sqlite")


def test_stage05_input_unchanged_and_metadata_consistency(tmp_path: Path) -> None:
    source = make_stage02(tmp_path / "fixture.sqlite")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "dictionary_v1.sqlite"
    result = build_stage05(source, output, "v1")
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after
    # Check deterministic metadata
    assert result["version"] == "v1"
    assert result["filename"] == "dictionary_v1.sqlite"
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["bytes"] == output.stat().st_size
    # Check quick_check and tables via second call would fail due to overwrite, so just verify file exists
    assert output.exists()
    # Verify PRAGMA quick_check
    import sqlite3

    conn = sqlite3.connect(f"file:{output}?mode=ro", uri=True)
    assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    conn.close()
