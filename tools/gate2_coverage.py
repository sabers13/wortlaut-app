"""Measurement CLI for real-textbook Stage-01 dictionary coverage (Gate 2).

Implements ADR-0002 §6 order 5 / ADR-0001 §13 Gate 2:
Measures resolution coverage of 200–300 vocabulary headwords from a real
German-textbook unit against a read-only Stage-01 dictionary asset.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Sequence

if __package__ in (None, ""):
    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

from app.dictionary import Dictionary  # noqa: E402
from app.resolve import Ref, resolve_word  # noqa: E402

DECISION_GOVERNANCE_REDESIGN: Final[str] = "GOVERNANCE_REDESIGN_REQUIRED"
DECISION_REMEDY_REQUIRED: Final[str] = "REMEDY_REQUIRED"
DECISION_CONTINUE: Final[str] = "CONTINUE"

_SPLIT_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?:\s+|-+)")


class Gate2CoverageError(Exception):
    """Base exception for Gate 2 coverage measurement errors."""


@dataclass(frozen=True)
class CoverageResult:
    """Deterministic coverage measurement outcome."""

    total: int
    hits: int
    misses_count: int
    misses: list[str]
    coverage_ratio: float
    display_percentage: str
    decision: str


def parse_and_validate_word_list(words_path: Path | str) -> list[str]:
    """Parse and validate UTF-8 textbook headword list.

    Enforces:
    - UTF-8 decoding;
    - Rejection of blank or whitespace-only lines;
    - Total count between 200 and 300 inclusive;
    - Rejection of duplicate normalized entries.
    """
    p = Path(words_path)
    if not p.is_file():
        raise Gate2CoverageError(f"Word list file not found: {p}")

    try:
        raw_text = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise Gate2CoverageError(f"Word list file is not valid UTF-8: {e}") from e

    raw_lines = raw_text.splitlines()
    if not raw_lines:
        raise Gate2CoverageError("Word list file is empty")

    normalized: list[str] = []
    for line_no, raw_line in enumerate(raw_lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            raise Gate2CoverageError(
                f"Blank or whitespace-only line at line {line_no} in {p}"
            )
        normalized.append(stripped)

    count = len(normalized)
    if not (200 <= count <= 300):
        raise Gate2CoverageError(
            f"Word count must be between 200 and 300 inclusive, got {count}"
        )

    seen: set[str] = set()
    for entry in normalized:
        if entry in seen:
            raise Gate2CoverageError(
                f"Duplicate normalized entry found in word list: {entry!r}"
            )
        seen.add(entry)

    return normalized


def normalize_entry(entry: str) -> tuple[str, str | None]:
    """Normalize a textbook entry into a search term and optional gender hint.

    Baseline rule (C3):
    If an entry has the exact form 'der <term>', 'die <term>', or 'das <term>'
    with a non-empty remainder, evaluate <term> with that article passed as gender hint.
    Otherwise evaluate the complete stripped entry unchanged with no gender hint.
    """
    stripped = entry.strip()
    parts = stripped.split(" ", 1)
    if len(parts) == 2 and parts[0] in ("der", "die", "das") and parts[1].strip():
        return parts[1].strip(), parts[0]
    return stripped, None


def _refs_are_hit(refs: Sequence[Ref]) -> bool:
    """Return True iff at least one returned ref has status resolved or derived_compound."""
    return any(r.status in ("resolved", "derived_compound") for r in refs)


def _split_lexical_pieces(term: str) -> list[str]:
    """Split a term into lexical pieces on whitespace and ASCII hyphens."""
    return [p for p in _SPLIT_PATTERN.split(term) if p]


def _lexical_pieces_resolve(term: str, dictionary: Dictionary) -> bool:
    """Resolve individual lexical pieces independently.

    Requires at least 2 non-empty pieces.
    Succeeds iff EVERY piece resolves (status 'resolved' or 'derived_compound').
    No gender hint is propagated.
    """
    pieces = _split_lexical_pieces(term)
    if len(pieces) < 2:
        return False
    for piece in pieces:
        refs = resolve_word(piece, dictionary)
        if not _refs_are_hit(refs):
            return False
    return True


def evaluate_coverage(
    dictionary: Dictionary,
    words: Sequence[str],
    *,
    lexical_split_remedy: bool = False,
) -> CoverageResult:
    """Evaluate resolution coverage across a sequence of normalized words.

    An entry is a hit iff:
    1. Whole-term resolution produces at least one reference with status
       'resolved' or 'derived_compound'.
    2. Or, if lexical_split_remedy is enabled and whole-term resolution missed,
       the term splits on whitespace or ASCII hyphens into at least 2 non-empty
       pieces, and every individual piece resolves through resolve_word.

    Misses retain exact input order.
    Threshold decision uses integer arithmetic (C5).
    """
    total = len(words)
    hits = 0
    misses: list[str] = []

    for entry in words:
        term, gender_hint = normalize_entry(entry)
        refs = resolve_word(term, dictionary, gender=gender_hint)
        if _refs_are_hit(refs):
            hits += 1
        elif lexical_split_remedy and _lexical_pieces_resolve(term, dictionary):
            hits += 1
        else:
            misses.append(entry)

    # Integer arithmetic for threshold decision
    if 100 * hits < 85 * total:
        decision = DECISION_GOVERNANCE_REDESIGN
    elif 100 * hits < 95 * total:
        decision = DECISION_REMEDY_REQUIRED
    else:
        decision = DECISION_CONTINUE

    ratio = hits / total if total > 0 else 0.0
    display_pct = f"{ratio * 100:.2f}%"

    return CoverageResult(
        total=total,
        hits=hits,
        misses_count=len(misses),
        misses=misses,
        coverage_ratio=ratio,
        display_percentage=display_pct,
        decision=decision,
    )


def run_gate2_coverage(
    dictionary_path: Path | str,
    words_path: Path | str,
    misses_out_path: Path | str,
    *,
    lexical_split_remedy: bool = False,
) -> dict[str, Any]:
    """Execute Gate 2 coverage measurement and record misses output fail-closed."""
    dict_p = Path(dictionary_path)
    if not dict_p.is_file():
        raise Gate2CoverageError(f"Dictionary database not found: {dict_p}")

    misses_p = Path(misses_out_path)
    if misses_p.exists():
        raise Gate2CoverageError(
            f"Misses output path already exists (refusing overwrite): {misses_p}"
        )

    words = parse_and_validate_word_list(words_path)

    with Dictionary(dict_p) as dictionary:
        result = evaluate_coverage(
            dictionary,
            words,
            lexical_split_remedy=lexical_split_remedy,
        )

    # Fail-closed atomic write of misses output
    misses_p.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        dir=misses_p.parent,
        prefix=f".{misses_p.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_p = Path(temp_file.name)
    temp_file.close()

    try:
        misses_content = "".join(f"{m}\n" for m in result.misses)
        temp_p.write_text(misses_content, encoding="utf-8")

        if misses_p.exists():
            raise Gate2CoverageError(
                f"Misses output path already exists before publish: {misses_p}"
            )

        temp_p.replace(misses_p)
    except Exception:
        if temp_p.exists():
            temp_p.unlink(missing_ok=True)
        raise

    return {
        "total": result.total,
        "hits": result.hits,
        "misses": result.misses_count,
        "coverage_ratio": result.coverage_ratio,
        "display_percentage": result.display_percentage,
        "display_coverage": result.display_percentage,
        "misses_output": str(misses_p),
        "misses_output_path": str(misses_p),
        "decision": result.decision,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for Gate 2 coverage measurement tool."""
    parser = argparse.ArgumentParser(
        description="Gate 2: Measure Stage-01 dictionary coverage against textbook vocabulary"
    )
    parser.add_argument(
        "--dictionary",
        type=Path,
        required=True,
        help="Path to read-only Stage-01 SQLite dictionary database",
    )
    parser.add_argument(
        "--words",
        type=Path,
        required=True,
        help="Path to UTF-8 textbook headword list (200–300 unique entries)",
    )
    parser.add_argument(
        "--misses-out",
        type=Path,
        required=True,
        help="Path to target output file for unglossed misses (must not exist)",
    )
    parser.add_argument(
        "--lexical-split-remedy",
        action="store_true",
        default=False,
        help=(
            "Enable one-time lexical-piece splitting remedy for multi-word "
            "expressions and hyphenated compounds"
        ),
    )

    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))

    try:
        outcome = run_gate2_coverage(
            dictionary_path=args.dictionary,
            words_path=args.words,
            misses_out_path=args.misses_out,
            lexical_split_remedy=args.lexical_split_remedy,
        )
        sys.stdout.write(json.dumps(outcome, indent=2) + "\n")
        return 0
    except Exception as e:
        sys.stderr.write(f"Error during Gate 2 coverage measurement: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
