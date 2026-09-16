"""Real-model resolver tests locking ADR-0001 §13 Gate 1 cases."""

from __future__ import annotations

import pytest
import spacy
from spacy.language import Language

from app.resolve import resolve_token
from tests.conftest import InMemoryLookupOracle

CASES = [
    ("Ich rufe dich morgen an.", "rufe", "anrufen"),
    ("Der Zug kommt um acht an.", "kommt", "ankommen"),
    ("Ich rufe dich morgen an.", "an", "anrufen"),
    ("Ich rufe laut.", "rufe", "rufen"),
    ("Sie interessiert sich für Musik.", "interessiert", "interessieren"),
]


@pytest.fixture(scope="module")
def nlp() -> Language:
    """Load the real de_core_news_md spaCy pipeline."""
    return spacy.load("de_core_news_md")


@pytest.fixture(scope="module")
def gate_oracle() -> InMemoryLookupOracle:
    """In-memory oracle populated for the ADR-0001 §13 CASES."""
    oracle = InMemoryLookupOracle()
    oracle.add_lemma("anrufen", "VERB", lemma_id=1)
    oracle.add_lemma("ankommen", "VERB", lemma_id=2)
    oracle.add_lemma("rufen", "VERB", lemma_id=3)
    oracle.add_lemma("interessieren", "VERB", lemma_id=4)
    return oracle


@pytest.mark.parametrize("sent_text,token_text,expected_lemma", CASES)
def test_gate1_cases(
    nlp: Language,
    gate_oracle: InMemoryLookupOracle,
    sent_text: str,
    token_text: str,
    expected_lemma: str,
) -> None:
    """Lock ADR-0001 §13 Gate 1 real-model resolution cases."""
    doc = nlp(sent_text)
    target_token = next((t for t in doc if t.text == token_text), None)
    assert target_token is not None, f"Token {token_text!r} not found in {sent_text!r}"

    results = resolve_token(target_token, gate_oracle)
    assert len(results) >= 1
    assert results[0].lemma == expected_lemma
    assert results[0].status == "resolved"
