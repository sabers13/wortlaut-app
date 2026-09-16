"""Pure unit tests for app/resolve.py resolution ladder and compound splitter (D46)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import app.resolve
from app.resolve import (
    FUGENELEMENTE,
    SVP_DEP,
    Ref,
    SenseRecord,
    TokenLike,
    generate_candidates,
    resolve_token,
    resolve_word,
    split_compound,
)
from tests.conftest import InMemoryLookupOracle


@dataclass
class TokenDouble(TokenLike):
    """Test double implementing TokenLike structural protocol."""

    _text: str
    _lemma: str
    _pos: str
    _dep: str = ""
    _head: TokenLike | None = None
    _children: list[TokenLike] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self._text

    @property
    def lemma_(self) -> str:
        return self._lemma

    @property
    def pos_(self) -> str:
        return self._pos

    @property
    def dep_(self) -> str:
        return self._dep

    @property
    def head(self) -> TokenLike:
        return self._head if self._head is not None else self

    @property
    def children(self) -> Iterable[TokenLike]:
        return self._children


def test_pure_no_io_imports() -> None:
    """app/resolve.py must not import sqlite3, app.dictionary, or file openers (AGENTS R2/B1)."""
    assert "sqlite3" not in dir(app.resolve)
    resolve_source = app.resolve.__file__
    assert resolve_source is not None
    with open(resolve_source, encoding="utf-8") as f:
        code = f.read()
    assert "sqlite3" not in code
    assert "app.dictionary" not in code
    assert "open(" not in code


def test_svp_dep_constant() -> None:
    """Acceptance B3: SVP_DEP is a single module-level constant equal to 'svp'."""
    assert SVP_DEP == "svp"


def test_fugenelemente_tuple() -> None:
    """Acceptance B2: Hardcoded exceptionless Fugenelemente."""
    assert set(FUGENELEMENTE) == {"s", "es", "n", "en", "er", "e", "ns"}


# --- Step 1: Exact Lookup and Gender Disambiguation ---


def test_exact_lookup_unambiguous(populated_oracle: InMemoryLookupOracle) -> None:
    """Ladder Step 1: Exact lookup resolves unambiguous lemma with status='resolved'."""
    res = resolve_word("Bank", populated_oracle)
    assert len(res) == 1
    assert res[0] == Ref(
        lemma="Bank",
        pos="NOUN",
        gender="die",
        status="resolved",
        lemma_id=3,
    )


def test_exact_lookup_gender_disambiguation_with_gender(
    populated_oracle: InMemoryLookupOracle,
) -> None:
    """Ladder Step 1: Disambiguates der See vs die See when gender is supplied."""
    res_der = resolve_word("See", populated_oracle, pos="NOUN", gender="der")
    assert len(res_der) == 1
    assert res_der[0].lemma == "See"
    assert res_der[0].gender == "der"
    assert res_der[0].lemma_id == 1
    assert res_der[0].status == "resolved"

    res_die = resolve_word("See", populated_oracle, pos="NOUN", gender="die")
    assert len(res_die) == 1
    assert res_die[0].lemma == "See"
    assert res_die[0].gender == "die"
    assert res_die[0].lemma_id == 2
    assert res_die[0].status == "resolved"


def test_exact_lookup_gender_disambiguation_without_gender(
    populated_oracle: InMemoryLookupOracle,
) -> None:
    """Ladder Step 1: Returns all matching lemmas when gender is not specified."""
    res = resolve_word("See", populated_oracle, pos="NOUN")
    assert len(res) == 2
    genders = {r.gender for r in res}
    assert genders == {"der", "die"}
    ids = {r.lemma_id for r in res}
    assert ids == {1, 2}
    assert all(r.status == "resolved" for r in res)


# --- Step 2: Surface Form ---


def test_surface_form_lookup(populated_oracle: InMemoryLookupOracle) -> None:
    """Ladder Step 2: Inflected surface form 'Häuser' resolves to lemma 'Haus'."""
    res = resolve_word("Häuser", populated_oracle)
    assert len(res) == 1
    assert res[0].lemma == "Haus"
    assert res[0].pos == "NOUN"
    assert res[0].gender == "das"
    assert res[0].status == "resolved"
    assert res[0].lemma_id == 7


def test_surface_form_separable_inflection(populated_oracle: InMemoryLookupOracle) -> None:
    """Ladder Step 2: Multi-word separable inflections ('rief an') resolve to infinitive."""
    res = resolve_word("rief an", populated_oracle)
    assert len(res) == 1
    assert res[0].lemma == "anrufen"
    assert res[0].pos == "VERB"
    assert res[0].status == "resolved"
    assert res[0].lemma_id == 11


def test_surface_form_wrong_pos_only_continues_to_fallback() -> None:
    """A surface row with another POS cannot satisfy a requested POS."""
    oracle = InMemoryLookupOracle()
    wrong = oracle.add_lemma("etwas", "NOUN", "das", lemma_id=101)
    oracle.add_surface_form("fremd", wrong)

    resolved = resolve_word("fremd", oracle, pos="AUX")

    assert resolved == [
        Ref(
            lemma="fremd",
            pos="AUX",
            status="needs_gloss",
            lemma_id=None,
        )
    ]


def test_surface_form_mixed_pos_returns_only_requested_pos() -> None:
    """Surface-form resolution strictly contains numeric hits to the requested POS."""
    oracle = InMemoryLookupOracle()
    noun = oracle.add_lemma("Haus", "NOUN", "das", lemma_id=102)
    aux = oracle.add_lemma("haben", "AUX", lemma_id=103)
    oracle.add_surface_form("hat", noun)
    oracle.add_surface_form("hat", aux)

    resolved = resolve_word("hat", oracle, pos="AUX")

    assert [(ref.lemma_id, ref.pos) for ref in resolved] == [(103, "AUX")]


def test_surface_form_punctuation_cannot_return_other_pos_ids() -> None:
    """Punctuation with only unrelated surface rows falls through without IDs."""
    oracle = InMemoryLookupOracle()
    noun = oracle.add_lemma("Frage", "NOUN", "die", lemma_id=104)
    oracle.add_surface_form("?", noun)

    resolved = resolve_word("?", oracle, pos="PUNCT")

    assert all(ref.lemma_id is None for ref in resolved)
    assert resolved[0].status == "needs_gloss"


# --- Step 3: Compound Splitter with D46 Component Bindings ---


def test_adr_verified_compound_split(populated_oracle: InMemoryLookupOracle) -> None:
    """Acceptance B2 / ADR-0001 §10 / ADR-0004 D46 verified case:

    Krankenversicherungskarte -> ['kranken', 'versicherung', 'karte']
    inherited gender: 'die' (from Karte)
    status: 'derived_compound'
    component_bindings: ordered (kranken -> versicherung -> karte)
    """
    res = resolve_word("Krankenversicherungskarte", populated_oracle)
    assert len(res) == 1
    compound_ref = res[0]
    assert compound_ref.lemma == "Krankenversicherungskarte"
    assert compound_ref.pos == "NOUN"
    assert compound_ref.gender == "die"
    assert compound_ref.status == "derived_compound"
    assert compound_ref.lemma_id is None
    assert compound_ref.components == ["kranken", "versicherung", "karte"]
    assert compound_ref.head_lemma == "Karte"
    assert compound_ref.component_bindings is not None
    assert len(compound_ref.component_bindings) == 3

    b0, b1, b2 = compound_ref.component_bindings
    assert b0.lemma == "kranken"
    assert b0.pos == "NOUN"
    assert b0.gender == "die"
    assert b0.lemma_id == 4
    assert b0.lemma_ref.startswith("lemma:v1:")
    assert b0.sense_ref.startswith("sense:v1:")

    assert b1.lemma == "Versicherung"
    assert b1.pos == "NOUN"
    assert b1.gender == "die"
    assert b1.lemma_id == 5

    assert b2.lemma == "Karte"
    assert b2.pos == "NOUN"
    assert b2.gender == "die"
    assert b2.lemma_id == 6


def test_compound_split_two_parts(populated_oracle: InMemoryLookupOracle) -> None:
    """Ladder Step 3: Two-component compound noun (Haustür -> Haus + Tür)."""
    res = resolve_word("Haustür", populated_oracle)
    assert len(res) == 1
    assert res[0].status == "derived_compound"
    assert res[0].components == ["haus", "tür"]
    assert res[0].gender == "die"  # Inherited from Tür
    assert res[0].head_lemma == "Tür"
    assert res[0].component_bindings is not None
    assert len(res[0].component_bindings) == 2
    assert res[0].component_bindings[0].lemma == "Haus"
    assert res[0].component_bindings[1].lemma == "Tür"


def test_compound_split_with_fuge_es(populated_oracle: InMemoryLookupOracle) -> None:
    """Ladder Step 3: Compound with 'es' Fuge (Tageslicht -> tag + es + licht)."""
    res = resolve_word("Tageslicht", populated_oracle)
    assert len(res) == 1
    assert res[0].status == "derived_compound"
    assert res[0].components == ["tag", "licht"]
    assert res[0].gender == "das"  # Inherited from Licht
    assert res[0].head_lemma == "Licht"
    assert res[0].component_bindings is not None
    assert len(res[0].component_bindings) == 2
    assert res[0].component_bindings[0].lemma == "Tag"
    assert res[0].component_bindings[1].lemma == "Licht"


def test_split_compound_direct_function(populated_oracle: InMemoryLookupOracle) -> None:
    """Direct invocation of split_compound helper."""
    split = split_compound("Krankenversicherungskarte", populated_oracle)
    assert split is not None
    assert split.components == ["kranken", "versicherung", "karte"]
    assert split.head.lemma == "Karte"
    assert split.head.gender == "die"
    assert len(split.component_bindings) == 3


def test_split_compound_short_word_returns_none(populated_oracle: InMemoryLookupOracle) -> None:
    """Words shorter than 3 letters cannot be compounds."""
    assert split_compound("ab", populated_oracle) is None
    assert split_compound("", populated_oracle) is None


# --- Step 4: Stub Fallback ---


def test_stub_fallback_unknown_word(populated_oracle: InMemoryLookupOracle) -> None:
    """Ladder Step 4: Unknown word falls through to status='needs_gloss' stub."""
    res = resolve_word("UnbekanntesWortXYZ", populated_oracle)
    assert len(res) == 1
    assert res[0] == Ref(
        lemma="UnbekanntesWortXYZ",
        pos="UNKNOWN",
        gender=None,
        status="needs_gloss",
        lemma_id=None,
        components=None,
        head_lemma=None,
        component_bindings=None,
    )


def test_stub_fallback_preserves_pos_and_gender(populated_oracle: InMemoryLookupOracle) -> None:
    """Ladder Step 4: Stub fallback preserves supplied pos and gender."""
    res = resolve_word("Fantasiereise", populated_oracle, pos="NOUN", gender="die")
    assert len(res) == 1
    assert res[0] == Ref(
        lemma="Fantasiereise",
        pos="NOUN",
        gender="die",
        status="needs_gloss",
        lemma_id=None,
        components=None,
        head_lemma=None,
        component_bindings=None,
    )


# --- Token Resolution & Separable Verbs ---


def test_resolve_token_separable_verb_parent(populated_oracle: InMemoryLookupOracle) -> None:
    """Resolving verb token with an attached 'svp' child combines particle + verb."""
    # Sentence: "Ich rufe dich morgen an."
    tok_particle = TokenDouble(_text="an", _lemma="an", _pos="ADP", _dep="svp")
    tok_verb = TokenDouble(
        _text="rufe",
        _lemma="rufen",
        _pos="VERB",
        _dep="ROOT",
        _children=[tok_particle],
    )
    tok_particle._head = tok_verb

    res = resolve_token(tok_verb, populated_oracle)
    assert len(res) == 1
    assert res[0].lemma == "anrufen"
    assert res[0].pos == "VERB"
    assert res[0].status == "resolved"
    assert res[0].lemma_id == 11


def test_resolve_token_separable_particle_child(populated_oracle: InMemoryLookupOracle) -> None:
    """Resolving particle token with dep='svp' resolves to head verb's combined lemma."""
    tok_verb = TokenDouble(_text="rufe", _lemma="rufen", _pos="VERB", _dep="ROOT")
    tok_particle = TokenDouble(_text="an", _lemma="an", _pos="ADP", _dep=SVP_DEP, _head=tok_verb)

    res = resolve_token(tok_particle, populated_oracle)
    assert len(res) == 1
    assert res[0].lemma == "anrufen"
    assert res[0].pos == "VERB"
    assert res[0].status == "resolved"
    assert res[0].lemma_id == 11


def test_resolve_token_non_separable_verb(populated_oracle: InMemoryLookupOracle) -> None:
    """Resolving plain verb without separable particle resolves to plain lemma."""
    # Sentence: "Ich rufe laut."
    tok_verb = TokenDouble(_text="rufe", _lemma="rufen", _pos="VERB", _dep="ROOT")
    res = resolve_token(tok_verb, populated_oracle)
    assert len(res) == 1
    assert res[0].lemma == "rufen"
    assert res[0].pos == "VERB"
    assert res[0].status == "resolved"
    assert res[0].lemma_id == 12


def test_generate_candidates_surface_scan(populated_oracle: InMemoryLookupOracle) -> None:
    """Candidate generation per ADR-0001 §4 scans sentence and filters by oracle."""
    tok_ich = TokenDouble(_text="Ich", _lemma="ich", _pos="PRON")
    tok_verb = TokenDouble(_text="rufe", _lemma="rufen", _pos="VERB")
    tok_dich = TokenDouble(_text="dich", _lemma="du", _pos="PRON")
    tok_morgen = TokenDouble(_text="morgen", _lemma="morgen", _pos="ADV")
    tok_an = TokenDouble(_text="an", _lemma="an", _pos="ADP")

    sentence = [tok_ich, tok_verb, tok_dich, tok_morgen, tok_an]

    candidates = generate_candidates(tok_verb, sentence, populated_oracle)
    lemmas = [c.lemma for c in candidates]
    assert "rufen" in lemmas
    assert "anrufen" in lemmas


# --- D46 Deterministic Selection & Candidate Ordering Tests (A12 / A15 #28-#30) ---


def test_d46_preceding_component_selection_order() -> None:
    """Acceptance A12 / A15 #28: Preceding component candidate set ordering.

    1. freq_rank ascending, NULL last
    2. pos ascending
    3. gender ascending, NULL last
    4. lemma.semantic_ref lexical ascending
    """
    oracle = InMemoryLookupOracle()
    # Add multiple candidates for same prefix 'test'
    # Candidate A: freq_rank=200, pos="NOUN", gender="die", sem_ref="lemma:v1:b"
    oracle.add_lemma("test", "NOUN", "die", lemma_id=1, semantic_ref="lemma:v1:b", freq_rank=200)
    # Candidate B: freq_rank=100, pos="NOUN", gender="der", sem_ref="lemma:v1:c" (wins on freq_rank)
    oracle.add_lemma("test", "NOUN", "der", lemma_id=2, semantic_ref="lemma:v1:c", freq_rank=100)
    # Head: 'haus'
    oracle.add_lemma("haus", "NOUN", "das", lemma_id=3, semantic_ref="lemma:v1:h", freq_rank=50)

    split = split_compound("testhaus", oracle)
    assert split is not None
    assert len(split.component_bindings) == 2
    # Preceding component selected candidate B (id=2, freq_rank=100)
    assert split.component_bindings[0].lemma_id == 2
    assert split.component_bindings[0].freq_rank == 100


def test_d46_source_sense_selection_order() -> None:
    """Acceptance A12 / A15 #29: Source sense selection ordering.

    1. lowest sense.ord
    2. lexical sense.semantic_ref
    """
    oracle = InMemoryLookupOracle()
    oracle.add_lemma("test", "NOUN", "das", lemma_id=1, semantic_ref="lemma:v1:t", freq_rank=10)
    # Clear default sense and add multiple senses
    oracle.senses[1] = [
        SenseRecord(id=10, lemma_id=1, ord=2, semantic_ref="sense:v1:ord2"),
        SenseRecord(id=11, lemma_id=1, ord=0, semantic_ref="sense:v1:ord0_z"),
        # ord=0 with lexical sem_ref "sense:v1:ord0_a" wins
        SenseRecord(id=12, lemma_id=1, ord=0, semantic_ref="sense:v1:ord0_a"),
    ]
    oracle.add_lemma("haus", "NOUN", "das", lemma_id=2, semantic_ref="lemma:v1:h", freq_rank=10)

    split = split_compound("testhaus", oracle)
    assert split is not None
    assert split.component_bindings[0].sense_id == 12
    assert split.component_bindings[0].sense_ref == "sense:v1:ord0_a"


def test_compound_split_fails_closed_when_binding_incomplete() -> None:
    """Acceptance A12 / A15 #30: Incomplete binding fails closed to stub."""
    oracle = InMemoryLookupOracle()
    # Lemma without semantic_ref
    oracle.add_lemma("test", "NOUN", "das", lemma_id=1, semantic_ref="", freq_rank=10)
    oracle.add_lemma("haus", "NOUN", "das", lemma_id=2, semantic_ref="lemma:v1:h", freq_rank=10)

    # Incomplete binding -> split_compound returns None -> resolve_word returns needs_gloss stub
    split = split_compound("testhaus", oracle)
    assert split is None

    res = resolve_word("testhaus", oracle)
    assert len(res) == 1
    assert res[0].status == "needs_gloss"
