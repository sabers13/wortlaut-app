"""Tests for tools/gate2_coverage.py (Gate 2 real-textbook coverage measurement)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from app.dictionary import Dictionary
from tools.gate2_coverage import (
    DECISION_CONTINUE,
    DECISION_GOVERNANCE_REDESIGN,
    DECISION_REMEDY_REQUIRED,
    Gate2CoverageError,
    evaluate_coverage,
    main,
    normalize_entry,
    parse_and_validate_word_list,
    run_gate2_coverage,
)


def _make_word_file(path: Path, words: list[str]) -> Path:
    """Helper to write word list to a file."""
    path.write_text("\n".join(words) + "\n", encoding="utf-8")
    return path


# --- Input Validation Tests ---


def test_200_words_accepted(tmp_path: Path) -> None:
    """Exactly 200 unique non-blank words are accepted."""
    words = [f"word_{i}" for i in range(200)]
    f = _make_word_file(tmp_path / "words200.txt", words)
    parsed = parse_and_validate_word_list(f)
    assert len(parsed) == 200
    assert parsed == words


def test_300_words_accepted(tmp_path: Path) -> None:
    """Exactly 300 unique non-blank words are accepted."""
    words = [f"word_{i}" for i in range(300)]
    f = _make_word_file(tmp_path / "words300.txt", words)
    parsed = parse_and_validate_word_list(f)
    assert len(parsed) == 300
    assert parsed == words


def test_199_words_rejected(tmp_path: Path) -> None:
    """199 words is below the minimum required 200 and is rejected."""
    words = [f"word_{i}" for i in range(199)]
    f = _make_word_file(tmp_path / "words199.txt", words)
    with pytest.raises(Gate2CoverageError, match="between 200 and 300 inclusive, got 199"):
        parse_and_validate_word_list(f)


def test_301_words_rejected(tmp_path: Path) -> None:
    """301 words is above the maximum allowed 300 and is rejected."""
    words = [f"word_{i}" for i in range(301)]
    f = _make_word_file(tmp_path / "words301.txt", words)
    with pytest.raises(Gate2CoverageError, match="between 200 and 300 inclusive, got 301"):
        parse_and_validate_word_list(f)


def test_blank_lines_rejected(tmp_path: Path) -> None:
    """Blank or whitespace-only lines are rejected."""
    words = [f"word_{i}" for i in range(199)] + ["   ", "word_200"]
    f = _make_word_file(tmp_path / "words_blank.txt", words)
    with pytest.raises(Gate2CoverageError, match="Blank or whitespace-only line"):
        parse_and_validate_word_list(f)


def test_normalized_duplicate_rejected(tmp_path: Path) -> None:
    """Duplicate normalized entries are rejected as hard errors."""
    words = [f"word_{i}" for i in range(199)] + ["  word_0  "]
    f = _make_word_file(tmp_path / "words_dup.txt", words)
    with pytest.raises(Gate2CoverageError, match="Duplicate normalized entry"):
        parse_and_validate_word_list(f)


# --- Baseline Normalization & Article / Gender Behavior Tests ---


def test_normalize_entry_article_and_gender_hints() -> None:
    """Article prefixes are stripped and passed as gender hints."""
    # der <term>
    term, gender = normalize_entry("der Tisch")
    assert term == "Tisch"
    assert gender == "der"

    # die <term>
    term, gender = normalize_entry("die Katze")
    assert term == "Katze"
    assert gender == "die"

    # das <term>
    term, gender = normalize_entry("das Haus")
    assert term == "Haus"
    assert gender == "das"

    # Bare words
    term, gender = normalize_entry("gehen")
    assert term == "gehen"
    assert gender is None

    # Empty remainder or no space
    term, gender = normalize_entry("der")
    assert term == "der"
    assert gender is None

    term, gender = normalize_entry("der-die-das")
    assert term == "der-die-das"
    assert gender is None


def test_gender_disambiguation_in_resolution(create_test_db: Callable[[], Path]) -> None:
    """Gender hints disambiguate homographs like der See vs die See."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # "der See" -> resolves to masculine lemma
        res_der = evaluate_coverage(d, ["der See"] + [f"dummy_{i}" for i in range(199)])
        assert res_der.hits == 1

        # "die See" -> resolves to feminine lemma
        res_die = evaluate_coverage(d, ["die See"] + [f"dummy_{i}" for i in range(199)])
        assert res_die.hits == 1


# --- Hit Classifications Tests ---


def test_exact_hit_classification(create_test_db: Callable[[], Path]) -> None:
    """Exact lemma match in dictionary is classified as a hit."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # "das Haus" and "Tür" are exact lemmas in test db
        words = ["das Haus", "Tür"] + [f"miss_{i}" for i in range(198)]
        res = evaluate_coverage(d, words)
        assert res.hits == 2
        assert res.misses_count == 198


def test_surface_form_hit_classification(create_test_db: Callable[[], Path]) -> None:
    """Inflected surface form match in dictionary is classified as a hit."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # "Häuser" is a surface form for "Haus" in test db
        words = ["Häuser"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words)
        assert res.hits == 1
        assert "Häuser" not in res.misses


def test_derived_compound_hit_with_d46_bindings(create_test_db: Callable[[], Path]) -> None:
    """Compound word decomposable into known lemmas with D46 bindings is classified as a hit."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # "Haustür" splits into "Haus" and "Tür", both have source senses/bindings
        words = ["Haustür"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words)
        assert res.hits == 1
        assert "Haustür" not in res.misses


def test_miss_classification(create_test_db: Callable[[], Path]) -> None:
    """Unknown word is classified as a miss and retained in misses list."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["unbekannteswortxyz"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words)
        assert res.hits == 0
        assert res.misses_count == 200
        assert res.misses[0] == "unbekannteswortxyz"


def test_deterministic_miss_ordering(create_test_db: Callable[[], Path]) -> None:
    """Misses preserve exact input order."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Hits at indices 1 and 3; misses at 0, 2, 4..199
        words = ["miss_alpha", "das Haus", "miss_beta", "Tür", "miss_gamma"] + [
            f"miss_tail_{i}" for i in range(195)
        ]
        res = evaluate_coverage(d, words)
        assert res.hits == 2
        assert res.misses[:3] == ["miss_alpha", "miss_beta", "miss_gamma"]


# --- Integer Arithmetic Threshold Boundary Tests ---


def test_exact_85_percent_threshold_boundary(create_test_db: Callable[[], Path]) -> None:
    """Integer arithmetic strictly tests the 85% boundary with no rounding drift."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Total = 200: 85% is exactly 170 hits.
        # 169 hits (84.5%): 100 * 169 = 16900 < 85 * 200 (17000) -> GOVERNANCE_REDESIGN_REQUIRED
        words_169 = ["das Haus"] * 169 + [f"miss_{i}" for i in range(31)]
        res_169 = evaluate_coverage(d, words_169)
        assert res_169.hits == 169
        assert res_169.decision == DECISION_GOVERNANCE_REDESIGN

        # 170 hits (85.0%): 100 * 170 = 17000 == 85 * 200 (17000) -> REMEDY_REQUIRED
        words_170 = ["das Haus"] * 170 + [f"miss_{i}" for i in range(30)]
        res_170 = evaluate_coverage(d, words_170)
        assert res_170.hits == 170
        assert res_170.decision == DECISION_REMEDY_REQUIRED

        # Total = 201: 85 * 201 = 17085.
        # 170 hits: 100 * 170 = 17000 < 17085 -> GOVERNANCE_REDESIGN_REQUIRED
        # (170 / 201 = 84.577% which would round to 85% if rounded to integer)
        words_201_170 = ["das Haus"] * 170 + [f"miss_{i}" for i in range(31)]
        res_201_170 = evaluate_coverage(d, words_201_170)
        assert res_201_170.total == 201
        assert res_201_170.hits == 170
        assert res_201_170.decision == DECISION_GOVERNANCE_REDESIGN

        # 171 hits: 100 * 171 = 17100 >= 17085 -> REMEDY_REQUIRED
        words_201_171 = ["das Haus"] * 171 + [f"miss_{i}" for i in range(30)]
        res_201_171 = evaluate_coverage(d, words_201_171)
        assert res_201_171.total == 201
        assert res_201_171.hits == 171
        assert res_201_171.decision == DECISION_REMEDY_REQUIRED


def test_exact_95_percent_threshold_boundary(create_test_db: Callable[[], Path]) -> None:
    """Integer arithmetic strictly tests the 95% boundary with no rounding drift."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Total = 200: 95% is exactly 190 hits.
        # 189 hits (94.5%): 100 * 189 = 18900 < 95 * 200 (19000) -> REMEDY_REQUIRED
        words_189 = ["das Haus"] * 189 + [f"miss_{i}" for i in range(11)]
        res_189 = evaluate_coverage(d, words_189)
        assert res_189.hits == 189
        assert res_189.decision == DECISION_REMEDY_REQUIRED

        # 190 hits (95.0%): 100 * 190 = 19000 == 95 * 200 (19000) -> CONTINUE
        words_190 = ["das Haus"] * 190 + [f"miss_{i}" for i in range(10)]
        res_190 = evaluate_coverage(d, words_190)
        assert res_190.hits == 190
        assert res_190.decision == DECISION_CONTINUE

        # Total = 201: 95 * 201 = 19095.
        # 190 hits: 100 * 190 = 19000 < 19095 -> REMEDY_REQUIRED
        # (190 / 201 = 94.527% which would round to 95% if rounded to integer)
        words_201_190 = ["das Haus"] * 190 + [f"miss_{i}" for i in range(11)]
        res_201_190 = evaluate_coverage(d, words_201_190)
        assert res_201_190.total == 201
        assert res_201_190.hits == 190
        assert res_201_190.decision == DECISION_REMEDY_REQUIRED

        # 191 hits: 100 * 191 = 19100 >= 19095 -> CONTINUE
        words_201_191 = ["das Haus"] * 191 + [f"miss_{i}" for i in range(10)]
        res_201_191 = evaluate_coverage(d, words_201_191)
        assert res_201_191.total == 201
        assert res_201_191.hits == 191
        assert res_201_191.decision == DECISION_CONTINUE


# --- Output Safety and CLI Tests ---


def test_misses_output_collision_refuses_overwrite(
    create_test_db: Callable[[], Path], tmp_path: Path
) -> None:
    """Tool refuses to overwrite an existing misses output file."""
    db_path = create_test_db()
    words = [f"word_{i}" for i in range(200)]
    words_file = _make_word_file(tmp_path / "words.txt", words)
    misses_file = tmp_path / "misses.txt"
    misses_file.write_text("existing content", encoding="utf-8")

    with pytest.raises(Gate2CoverageError, match="already exists"):
        run_gate2_coverage(db_path, words_file, misses_file)

    # Existing content preserved
    assert misses_file.read_text(encoding="utf-8") == "existing content"


def test_run_gate2_coverage_end_to_end(
    create_test_db: Callable[[], Path], tmp_path: Path
) -> None:
    """Full execution produces valid JSON report and atomic misses file."""
    db_path = create_test_db()
    # 2 hits ("das Haus", "die Tür"), 198 misses
    words = ["das Haus", "die Tür"] + [f"unknown_{i}" for i in range(198)]
    words_file = _make_word_file(tmp_path / "words.txt", words)
    misses_file = tmp_path / "misses.txt"

    res = run_gate2_coverage(db_path, words_file, misses_file)

    assert res["total"] == 200
    assert res["hits"] == 2
    assert res["misses"] == 198
    assert res["coverage_ratio"] == 0.01
    assert res["display_percentage"] == "1.00%"
    assert res["decision"] == DECISION_GOVERNANCE_REDESIGN
    assert Path(res["misses_output"]).is_file()

    written_misses = misses_file.read_text(encoding="utf-8").splitlines()
    assert len(written_misses) == 198
    assert written_misses[0] == "unknown_0"
    assert written_misses[-1] == "unknown_197"


def test_cli_main_success_and_failure(
    create_test_db: Callable[[], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI main entrypoint outputs valid JSON on success and exits 1 on failure."""
    db_path = create_test_db()
    words = [f"w_{i}" for i in range(200)]
    words_file = _make_word_file(tmp_path / "words.txt", words)
    misses_file = tmp_path / "out_misses.txt"

    # Success
    code = main([
        "--dictionary", str(db_path),
        "--words", str(words_file),
        "--misses-out", str(misses_file),
    ])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["total"] == 200
    assert data["misses"] == 200

    # Failure: output already exists
    code_fail = main([
        "--dictionary", str(db_path),
        "--words", str(words_file),
        "--misses-out", str(misses_file),
    ])
    assert code_fail == 1
    captured_fail = capsys.readouterr()
    assert "Error during Gate 2 coverage measurement" in captured_fail.err


def test_gate2_coverage_direct_script_subprocess(
    create_test_db: Callable[[], Path], tmp_path: Path
) -> None:
    """Direct-script execution via subprocess works without PYTHONPATH."""
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "tools" / "gate2_coverage.py"
    db_path = create_test_db()

    # Exactly 200 unique non-blank words (2 hits, 198 misses)
    words = ["das Haus", "die Tür"] + [f"unknown_sub_{i}" for i in range(198)]
    words_file = _make_word_file(tmp_path / "sub_words.txt", words)
    misses_file = tmp_path / "sub_misses.txt"
    assert not misses_file.exists()

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--dictionary",
            str(db_path),
            "--words",
            str(words_file),
            "--misses-out",
            str(misses_file),
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, f"Subprocess failed with stderr: {proc.stderr}"
    data = json.loads(proc.stdout)
    assert data["total"] == 200
    assert data["hits"] == 2
    assert data["misses"] == 198
    assert data["decision"] == DECISION_GOVERNANCE_REDESIGN

    assert misses_file.is_file()
    written_misses = misses_file.read_text(encoding="utf-8").splitlines()
    assert len(written_misses) == 198
    assert written_misses[0] == "unknown_sub_0"
    assert written_misses[-1] == "unknown_sub_197"


def test_gate2_coverage_subprocess_startup_validation() -> None:
    """Invoking script with invalid/missing arguments reaches CLI parser without import error."""
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "tools" / "gate2_coverage.py"

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert "ModuleNotFoundError" not in combined
    assert "No module named 'app'" not in combined


# --- One-Time Lexical Split Remedy Tests ---


def test_remedy_absent_whitespace_phrase_remains_miss(
    create_test_db: Callable[[], Path],
) -> None:
    """Without remedy flag, a multi-word phrase remains a miss as in baseline."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["Haus Tür"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=False)
        assert res.hits == 0
        assert res.misses_count == 200
        assert res.misses[0] == "Haus Tür"


def test_remedy_enabled_whitespace_phrase_all_pieces_resolve_becomes_hit(
    create_test_db: Callable[[], Path],
) -> None:
    """With remedy enabled, a multi-word phrase whose pieces all resolve becomes a hit."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["Haus Tür"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 1
        assert res.misses_count == 199
        assert "Haus Tür" not in res.misses


def test_whole_entry_hit_remains_hit_without_remedy_consultation(
    create_test_db: Callable[[], Path],
) -> None:
    """An entry that is a whole-term hit succeeds immediately with or without remedy."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["das Haus"] + [f"miss_{i}" for i in range(199)]
        res_baseline = evaluate_coverage(d, words, lexical_split_remedy=False)
        res_remedy = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res_baseline.hits == 1
        assert res_remedy.hits == 1


def test_remedy_enabled_one_failed_piece_entire_entry_remains_miss(
    create_test_db: Callable[[], Path],
) -> None:
    """If one whitespace piece fails resolution, the entire original entry remains a miss."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["Haus unbekannteswortxyz"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 0
        assert res.misses_count == 200
        assert res.misses[0] == "Haus unbekannteswortxyz"


def test_remedy_enabled_ascii_hyphen_phrase_recovers(
    create_test_db: Callable[[], Path],
) -> None:
    """With remedy enabled, an ASCII hyphenated compound whose pieces resolve becomes a hit."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["Haus-Tür"] + [f"miss_{i}" for i in range(199)]
        res_baseline = evaluate_coverage(d, words, lexical_split_remedy=False)
        assert res_baseline.hits == 0
        res_remedy = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res_remedy.hits == 1
        assert "Haus-Tür" not in res_remedy.misses


def test_remedy_enabled_single_token_miss_remains_miss(
    create_test_db: Callable[[], Path],
) -> None:
    """A single-token unknown word remains a miss in remedy mode."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["unbekannteswortxyz"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 0
        assert res.misses_count == 200
        assert res.misses[0] == "unbekannteswortxyz"


def test_remedy_requires_at_least_two_pieces(
    create_test_db: Callable[[], Path],
) -> None:
    """Lexical splitting requires at least two non-empty pieces."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Punctuation-wrapped single token that produces only 1 non-empty piece
        words = ["-unbekannt-"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 0
        assert res.misses[0] == "-unbekannt-"


def test_remedy_interior_article_must_resolve_not_dropped(
    create_test_db: Callable[[], Path],
) -> None:
    """Interior articles are treated as ordinary pieces and must resolve; they are not dropped."""
    import sqlite3

    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # In base test db, 'der' is not in lemma table -> fails because interior 'der'
        # is not dropped
        words = ["Haus der Tür"] + [f"miss_{i}" for i in range(199)]
        res_fail = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res_fail.hits == 0
        assert "Haus der Tür" in res_fail.misses

    # Insert 'der' lemma into DB
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO lemma (id, semantic_ref, lemma, pos, source, license)
        VALUES (99, 'lemma:v1:der_art', 'der', 'DET', 'wiktionary', 'CC BY-SA')
        """
    )
    conn.execute(
        """
        INSERT INTO sense (
            id, lemma_id, semantic_ref, source_namespace, source_ref, ord, source, license
        )
        VALUES (99, 99, 'sense:v1:der_art_0', 'wiktextract', 's99', 0, 'wiktionary', 'CC BY-SA')
        """
    )
    conn.commit()
    conn.close()

    # Now that 'der' resolves, the 3-piece phrase succeeds
    with Dictionary(db_path) as d:
        res_ok = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res_ok.hits == 1
        assert "Haus der Tür" not in res_ok.misses


def test_remedy_initial_article_normalized_before_splitting(
    create_test_db: Callable[[], Path],
) -> None:
    """Initial der/die/das article is normalized first before lexical splitting."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["das Haus-Tür"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 1
        assert "das Haus-Tür" not in res.misses


def test_remedy_gender_hint_not_propagated_to_pieces(
    create_test_db: Callable[[], Path],
) -> None:
    """Gender hint from the initial article is NOT passed to lexical pieces."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # "der Haus-Tür": "Haus" is neuter (das), "Tür" is feminine (die).
        # Initial article "der" would cause "Haus" to fail if propagated as gender hint.
        words = ["der Haus-Tür"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 1
        assert "der Haus-Tür" not in res.misses


def test_remedy_resolved_and_derived_compound_pieces_succeed(
    create_test_db: Callable[[], Path],
) -> None:
    """Pieces with status 'resolved' or 'derived_compound' count as successful."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # 'Haustür' is a derived compound; 'Tag' is exact lemma (resolved)
        words = ["Haustür Tag"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 1
        assert "Haustür Tag" not in res.misses

        # Surface form piece ('Häuser') + exact lemma ('Tür') -> both 'resolved'
        words_surface = ["Häuser Tür"] + [f"miss_{i}" for i in range(199)]
        res_surface = evaluate_coverage(d, words_surface, lexical_split_remedy=True)
        assert res_surface.hits == 1


def test_remedy_needs_gloss_piece_fails_expression(
    create_test_db: Callable[[], Path],
) -> None:
    """A piece with status 'needs_gloss' causes the whole expression to fail."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["Haustür unbekannteswortxyz"] + [f"miss_{i}" for i in range(199)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 0
        assert "Haustür unbekannteswortxyz" in res.misses


def test_remedy_slash_and_dot_punctuation_not_split(
    create_test_db: Callable[[], Path],
) -> None:
    """Slash and dot punctuation are not split in remedy mode."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        words = ["Haus/Tür", "Haus.Tür"] + [f"miss_{i}" for i in range(198)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 0
        assert res.misses[:2] == ["Haus/Tür", "Haus.Tür"]


def test_remedy_unicode_dash_not_split(
    create_test_db: Callable[[], Path],
) -> None:
    """Unicode dash characters (en-dash, em-dash) are not treated as ASCII hyphens."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # \u2013 (en-dash), \u2014 (em-dash)
        words = ["Haus\u2013Tür", "Haus\u2014Tür"] + [f"miss_{i}" for i in range(198)]
        res = evaluate_coverage(d, words, lexical_split_remedy=True)
        assert res.hits == 0
        assert res.misses[:2] == ["Haus\u2013Tür", "Haus\u2014Tür"]


def test_remedy_threshold_arithmetic_unchanged(
    create_test_db: Callable[[], Path],
) -> None:
    """Integer arithmetic threshold boundaries remain identical under remedy mode."""
    db_path = create_test_db()
    with Dictionary(db_path) as d:
        # Total = 200: 169 hits -> GOVERNANCE_REDESIGN_REQUIRED
        words_169 = ["das Haus"] * 169 + [f"miss_{i}" for i in range(31)]
        res_169 = evaluate_coverage(d, words_169, lexical_split_remedy=True)
        assert res_169.decision == DECISION_GOVERNANCE_REDESIGN

        # 170 hits -> REMEDY_REQUIRED
        words_170 = ["das Haus"] * 170 + [f"miss_{i}" for i in range(30)]
        res_170 = evaluate_coverage(d, words_170, lexical_split_remedy=True)
        assert res_170.decision == DECISION_REMEDY_REQUIRED

        # 189 hits -> REMEDY_REQUIRED
        words_189 = ["das Haus"] * 189 + [f"miss_{i}" for i in range(11)]
        res_189 = evaluate_coverage(d, words_189, lexical_split_remedy=True)
        assert res_189.decision == DECISION_REMEDY_REQUIRED

        # 190 hits -> CONTINUE
        words_190 = ["das Haus"] * 190 + [f"miss_{i}" for i in range(10)]
        res_190 = evaluate_coverage(d, words_190, lexical_split_remedy=True)
        assert res_190.decision == DECISION_CONTINUE


def test_remedy_misses_output_collision_refuses_overwrite(
    create_test_db: Callable[[], Path], tmp_path: Path
) -> None:
    """Remedy execution still refuses to overwrite an existing misses output file."""
    db_path = create_test_db()
    words = [f"word_{i}" for i in range(200)]
    words_file = _make_word_file(tmp_path / "words.txt", words)
    misses_file = tmp_path / "misses.txt"
    misses_file.write_text("existing content", encoding="utf-8")

    with pytest.raises(Gate2CoverageError, match="already exists"):
        run_gate2_coverage(
            db_path, words_file, misses_file, lexical_split_remedy=True
        )


def test_gate2_coverage_direct_script_subprocess_remedy_flag(
    create_test_db: Callable[[], Path], tmp_path: Path
) -> None:
    """Direct-script execution with --lexical-split-remedy works without PYTHONPATH."""
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "tools" / "gate2_coverage.py"
    db_path = create_test_db()

    # 2 whole-entry hits ("das Haus", "die Tür"), 1 remedy hit ("Haus-Tür"), 197 misses
    words = ["das Haus", "die Tür", "Haus-Tür"] + [
        f"unknown_sub_{i}" for i in range(197)
    ]
    words_file = _make_word_file(tmp_path / "sub_words_remedy.txt", words)
    misses_file = tmp_path / "sub_misses_remedy.txt"
    assert not misses_file.exists()

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    proc = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--dictionary",
            str(db_path),
            "--words",
            str(words_file),
            "--misses-out",
            str(misses_file),
            "--lexical-split-remedy",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, f"Subprocess failed with stderr: {proc.stderr}"
    data = json.loads(proc.stdout)
    assert data["total"] == 200
    assert data["hits"] == 3
    assert data["misses"] == 197
    assert data["decision"] == DECISION_GOVERNANCE_REDESIGN

    assert misses_file.is_file()
    written_misses = misses_file.read_text(encoding="utf-8").splitlines()
    assert len(written_misses) == 197
    assert written_misses[0] == "unknown_sub_0"
    assert written_misses[-1] == "unknown_sub_196"
    assert "Haus-Tür" not in written_misses
