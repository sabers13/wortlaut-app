"""Pure and deterministic example sentence ranking for German flashcards.

Implements ADR-0001 §11 and ADR-0002 §5 pure scoring algorithm:
- Target length near nine tokens
- Penalties for unknown and rare unknown lemmas (i+1)
- Penalties for proper nouns (has_proper)
- Penalties for untranslated examples
- Small bonus for question sentences
- Known vocabulary = deck lemmas ∪ known_lemmas (when known_lemmas supplied by value,
  otherwise deck lemmas)
- Pure function layer: no I/O, no network, no module mutable state.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

TARGET_TOKEN_COUNT: Final[int] = 9
LENGTH_PENALTY_WEIGHT: Final[float] = 2.0
UNTRANSLATED_PENALTY: Final[float] = 25.0
PROPER_NOUN_PENALTY: Final[float] = 15.0
UNKNOWN_LEMMA_PENALTY: Final[float] = 10.0
RARE_UNKNOWN_PENALTY: Final[float] = 10.0
QUESTION_BONUS: Final[float] = 5.0
RARE_FREQ_THRESHOLD: Final[int] = 5000


@dataclass(frozen=True, slots=True)
class ExampleScore:
    """Breakdown of deterministic score computation for an example sentence."""

    total_score: float
    token_count: int
    length_penalty: float
    untranslated_penalty: float
    proper_noun_penalty: float
    unknown_lemma_penalty: float
    question_bonus: float


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_de_text(example: Any) -> str:
    de = _get_field(example, "de")
    if de is not None:
        return str(de).strip()
    text = _get_field(example, "text")
    if text is not None:
        return str(text).strip()
    return ""


def _extract_en_text(example: Any) -> str | None:
    en = _get_field(example, "en")
    if en is not None and str(en).strip():
        return str(en).strip()
    translation = _get_field(example, "translation")
    if translation is not None and str(translation).strip():
        return str(translation).strip()
    return None


def _estimate_token_count(de: str) -> int:
    # Split on whitespace after normalizing punctuation
    words = re.findall(r"\b[\w'-]+\b", de, flags=re.UNICODE)
    return len(words) if words else len(de.split())


def compute_known_lemmas(
    *,
    deck_lemmas: Iterable[str] | None = None,
    known_lemmas: Iterable[str] | None = None,
) -> frozenset[str]:
    """Compute the active known lemma set per ADR-0002 §5.

    known = deck lemmas ∪ known_lemmas when supplied by value, otherwise deck lemmas.
    """
    deck_set = {str(lem).strip().lower() for lem in deck_lemmas} if deck_lemmas else set()
    if known_lemmas is not None:
        known_set = {str(lem).strip().lower() for lem in known_lemmas}
        return frozenset(deck_set | known_set)
    return frozenset(deck_set)


def score_example(
    example: Any,
    *,
    known_lemmas: frozenset[str] | set[str] | Sequence[str] | None = None,
    deck_lemmas: Iterable[str] | None = None,
    lemma_freq_ranks: Mapping[str, int] | None = None,
) -> ExampleScore:
    """Calculate deterministic score for a single example sentence.

    Higher score = better candidate.
    """
    de_text = _extract_de_text(example)
    en_text = _extract_en_text(example)

    # 1. Token count & length penalty
    raw_tc = _get_field(example, "token_count")
    if raw_tc is not None and isinstance(raw_tc, int) and raw_tc > 0:
        token_count = raw_tc
    else:
        token_count = _estimate_token_count(de_text)

    length_diff = abs(token_count - TARGET_TOKEN_COUNT)
    length_penalty = length_diff * LENGTH_PENALTY_WEIGHT

    # 2. Untranslated penalty
    untranslated_penalty = UNTRANSLATED_PENALTY if not en_text else 0.0

    # 3. Proper noun penalty
    has_proper = _get_field(example, "has_proper", 0)
    proper_noun_penalty = PROPER_NOUN_PENALTY if bool(has_proper) else 0.0

    # 4. Unknown lemmas penalty
    if isinstance(known_lemmas, frozenset):
        known_set = known_lemmas
    elif known_lemmas is not None or deck_lemmas is not None:
        known_set = compute_known_lemmas(deck_lemmas=deck_lemmas, known_lemmas=known_lemmas)
    else:
        known_set = frozenset()

    example_lemmas_raw = _get_field(example, "lemmas") or _get_field(example, "example_lemmas")
    unknown_lemma_penalty = 0.0

    if example_lemmas_raw:
        for lem in example_lemmas_raw:
            clean_lem = str(lem).strip().lower()
            if clean_lem and clean_lem not in known_set:
                unknown_lemma_penalty += UNKNOWN_LEMMA_PENALTY
                if lemma_freq_ranks is not None:
                    rank = lemma_freq_ranks.get(clean_lem)
                    if rank is None or rank > RARE_FREQ_THRESHOLD:
                        unknown_lemma_penalty += RARE_UNKNOWN_PENALTY

    # 5. Question bonus
    question_bonus = QUESTION_BONUS if "?" in de_text else 0.0

    total_score = (
        100.0
        - length_penalty
        - untranslated_penalty
        - proper_noun_penalty
        - unknown_lemma_penalty
        + question_bonus
    )

    return ExampleScore(
        total_score=round(total_score, 4),
        token_count=token_count,
        length_penalty=round(length_penalty, 4),
        untranslated_penalty=round(untranslated_penalty, 4),
        proper_noun_penalty=round(proper_noun_penalty, 4),
        unknown_lemma_penalty=round(unknown_lemma_penalty, 4),
        question_bonus=round(question_bonus, 4),
    )


def rank_examples(
    examples: Sequence[Any],
    *,
    known_lemmas: Iterable[str] | None = None,
    deck_lemmas: Iterable[str] | None = None,
    lemma_freq_ranks: Mapping[str, int] | None = None,
) -> list[Any]:
    """Deterministically rank example sentences from best to worst.

    Ties broken by example text (de).
    """
    if not examples:
        return []

    known_set = compute_known_lemmas(deck_lemmas=deck_lemmas, known_lemmas=known_lemmas)

    scored: list[tuple[float, str, Any]] = []
    for ex in examples:
        score_info = score_example(
            ex,
            known_lemmas=known_set,
            lemma_freq_ranks=lemma_freq_ranks,
        )
        de_text = _extract_de_text(ex)
        scored.append((score_info.total_score, de_text, ex))

    # Sort descending by score, ascending by de text
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored]


def select_primary_example(
    examples: Sequence[Any],
    *,
    known_lemmas: Iterable[str] | None = None,
    deck_lemmas: Iterable[str] | None = None,
    lemma_freq_ranks: Mapping[str, int] | None = None,
) -> Any | None:
    """Return the single top-ranked example sentence, or None if empty."""
    ranked = rank_examples(
        examples,
        known_lemmas=known_lemmas,
        deck_lemmas=deck_lemmas,
        lemma_freq_ranks=lemma_freq_ranks,
    )
    return ranked[0] if ranked else None
