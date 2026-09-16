"""Evidence test suite for display-time card rendering and tri-state noun plural.

Covers T1–T9 requirements per brief section A4 and tasks/slice-7.md:
- T1: Front face rendering (noun article+gender, POS, IPA present/absent, non-noun).
- T2: Back face composition (front content, selected languages de/en, order de then en).
- T3: User-meaning precedence marking (user block wins and is marked user-authored).
- T4: Tri-state noun plural (known form -> Plural: die <form>, plural_none=1 -> kein Plural,
      unknown -> omitted, non-noun -> omitted).
- T5: Grammar metadata (conditional rendering of separable, aux, principal parts,
      genitive, governs, reflexive, gradation).
- T6: Examples (de+en, de-only, empty examples=[] clean rendering).
- T7: Derived compound decomposition (D46 all-or-none, head-last ordering, user override).
- T8: Purity and determinism (equal inputs -> equal outputs, immutability, plain values).
- T9: Language domain (non-{de, en} raises ValueError).
"""

from __future__ import annotations

import copy

import pytest

from app.dictionary import ExampleEntry, LemmaEntry
from app.render import (
    AudioTrigger,
    CardRenderInput,
    DerivedComponent,
    MeaningBlock,
    RenderExample,
    RenderLemmaData,
    render_back_face,
    render_card,
    render_front_face,
    render_plural,
    validate_selected_languages,
)


def _sample_noun_lemma() -> RenderLemmaData:
    return RenderLemmaData(
        lemma="Haus",
        pos="NOUN",
        gender="das",
        plural="Häuser",
        plural_none=0,
        genitive_sg="Hauses",
        ipa="/haʊ̯s/",
    )


def _sample_verb_lemma() -> RenderLemmaData:
    return RenderLemmaData(
        lemma="anrufen",
        pos="VERB",
        aux="haben",
        separable=1,
        particle="an",
        reflexive=0,
        praesens_3sg="ruft an",
        praeteritum_3sg="rief an",
        partizip_ii="angerufen",
        governs='["Akkusativ"]',
        ipa="/ˈanˌʁuːfn̩/",
    )


# ==============================================================================
# T1 — Front face rendering
# ==============================================================================


def test_t1_front_face_noun_with_article_gender_ipa() -> None:
    """T1: Noun with article+gender, POS, and IPA present renders correctly."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de", "en"),
        audio_trigger=AudioTrigger(available=True, lemma="Haus", token="audio-123"),
    )
    front = render_front_face(card_input)

    assert front.headword == "Haus"
    assert front.display_headword == "das Haus"
    assert front.article == "das"
    assert front.gender == "das"
    assert front.pos == "NOUN"
    assert front.ipa == "/haʊ̯s/"
    assert front.audio_trigger.available is True
    assert front.audio_trigger.token == "audio-123"
    assert "das Haus" in front.text
    assert "NOUN" in front.text
    assert "/haʊ̯s/" in front.text


def test_t1_front_face_ipa_absent() -> None:
    """T1: IPA-absent case renders cleanly without error or None placeholder."""
    lemma = RenderLemmaData(
        lemma="Tisch",
        pos="NOUN",
        gender="der",
        plural="Tische",
        ipa=None,
    )
    card_input = CardRenderInput(
        lemma=lemma,
        selected_languages=("de",),
    )
    front = render_front_face(card_input)

    assert front.headword == "Tisch"
    assert front.display_headword == "der Tisch"
    assert front.article == "der"
    assert front.gender == "der"
    assert front.ipa is None
    assert "None" not in front.text
    assert front.text == "der Tisch\nNOUN"


def test_t1_front_face_non_noun_has_no_article() -> None:
    """T1: Non-noun headword has no article rendered."""
    verb = _sample_verb_lemma()
    card_input = CardRenderInput(
        lemma=verb,
        selected_languages=("de", "en"),
    )
    front = render_front_face(card_input)

    assert front.headword == "anrufen"
    assert front.display_headword == "anrufen"
    assert front.article is None
    assert front.pos == "VERB"
    assert "der anrufen" not in front.text
    assert "das anrufen" not in front.text
    assert "die anrufen" not in front.text
    assert front.text == "anrufen\nVERB • /ˈanˌʁuːfn̩/"


def test_t1_front_face_from_lemma_entry() -> None:
    """T1: Direct LemmaEntry from app.dictionary is accepted and rendered."""
    entry = LemmaEntry(
        id=1,
        lemma="Katze",
        pos="NOUN",
        gender="die",
        plural="Katzen",
        plural_none=0,
        ipa="/ˈkat͡sə/",
    )
    card_input = CardRenderInput(
        lemma=entry,
        selected_languages=("de",),
    )
    front = render_front_face(card_input)

    assert front.headword == "Katze"
    assert front.display_headword == "die Katze"
    assert front.article == "die"
    assert front.pos == "NOUN"
    assert front.ipa == "/ˈkat͡sə/"


# ==============================================================================
# T2 — Back face composition and language selection
# ==============================================================================


def test_t2_back_face_composition_selected_languages_de_only() -> None:
    """T2: When only {de} is selected, only de section renders, en is absent."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de",),
        meanings=(
            MeaningBlock(language="de", origin="dictionary", texts=("Gebäude zum Wohnen",)),
            MeaningBlock(language="en", origin="dictionary", texts=("house", "building")),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.meanings) == 1
    assert back.meanings[0].language == "de"
    assert back.meanings[0].lines == ("Gebäude zum Wohnen",)
    assert "Deutsch:" in back.text
    assert "Gebäude zum Wohnen" in back.text
    assert "English:" not in back.text
    assert "house" not in back.text


def test_t2_back_face_composition_selected_languages_en_only() -> None:
    """T2: When only {en} is selected, only en section renders, de is absent."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("en",),
        meanings=(
            MeaningBlock(language="de", origin="dictionary", texts=("Gebäude zum Wohnen",)),
            MeaningBlock(language="en", origin="dictionary", texts=("house",)),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.meanings) == 1
    assert back.meanings[0].language == "en"
    assert back.meanings[0].lines == ("house",)
    assert "English:" in back.text
    assert "house" in back.text
    assert "Deutsch:" not in back.text
    assert "Gebäude zum Wohnen" not in back.text


def test_t2_back_face_composition_selected_languages_de_and_en_order() -> None:
    """T2: When {de, en} is selected, order is deterministically de then en."""
    # Pass ("en", "de") in reverse selection order to assert deterministic de-then-en ordering
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("en", "de"),
        meanings=(
            MeaningBlock(language="en", origin="dictionary", texts=("house",)),
            MeaningBlock(language="de", origin="dictionary", texts=("Wohngebäude",)),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.meanings) == 2
    assert back.meanings[0].language == "de"
    assert back.meanings[1].language == "en"
    assert back.meanings[0].lines == ("Wohngebäude",)
    assert back.meanings[1].lines == ("house",)

    de_pos = back.text.index("Deutsch:")
    en_pos = back.text.index("English:")
    assert de_pos < en_pos


def test_t2_back_face_unselected_language_absent_entirely() -> None:
    """T2: Unselected language contributes no section, no heading, no placeholder."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de",),
        meanings=(),
    )
    back = render_back_face(card_input)

    assert len(back.meanings) == 0
    assert "English:" not in back.text
    assert "Deutsch:" not in back.text


# ==============================================================================
# T3 — User-authored meaning precedence and marking
# ==============================================================================


def test_t3_user_meaning_precedence_and_marking() -> None:
    """T3: User-authored meaning wins over dictionary meaning and is visibly marked."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de", "en"),
        meanings=(
            MeaningBlock(language="de", origin="dictionary", texts=("Wiktionary Definition",)),
            MeaningBlock(language="de", origin="user", texts=("Mein eigenes Haus-Glossar",)),
            MeaningBlock(language="en", origin="dictionary", texts=("dwelling",)),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.meanings) == 2
    # de meaning is user-authored
    de_block = back.meanings[0]
    assert de_block.language == "de"
    assert de_block.origin == "user"
    assert de_block.is_user_authored is True
    assert "user-authored" in de_block.heading
    assert de_block.lines == ("Mein eigenes Haus-Glossar",)
    assert "Wiktionary Definition" not in back.text

    # en meaning is dictionary
    en_block = back.meanings[1]
    assert en_block.language == "en"
    assert en_block.origin == "dictionary"
    assert en_block.is_user_authored is False
    assert en_block.lines == ("dwelling",)


# ==============================================================================
# T4 — Tri-state noun plural
# ==============================================================================


def test_t4_tri_state_plural_known_form() -> None:
    """T4 State 1: Known plural form renders 'Plural: die <form>' prominently."""
    plural_result = render_plural("NOUN", "Häuser", 0)
    assert plural_result == "Plural: die Häuser"

    # Also test with existing "die " prefix in plural data
    plural_with_die = render_plural("NOUN", "die Häuser", 0)
    assert plural_with_die == "Plural: die Häuser"

    lemma = RenderLemmaData(
        lemma="Haus",
        pos="NOUN",
        gender="das",
        plural="Häuser",
        plural_none=0,
    )
    card_input = CardRenderInput(
        lemma=lemma,
        selected_languages=("de",),
    )
    back = render_back_face(card_input)
    assert back.plural == "Plural: die Häuser"
    assert "Plural: die Häuser" in back.text


def test_t4_tri_state_plural_none_singular_only() -> None:
    """T4 State 2: plural_none=1 renders 'kein Plural'."""
    plural_result = render_plural("NOUN", None, 1)
    assert plural_result == "kein Plural"

    card_input = CardRenderInput(
        lemma=RenderLemmaData(lemma="Durst", pos="NOUN", gender="der", plural=None, plural_none=1),
        selected_languages=("de",),
    )
    back = render_back_face(card_input)
    assert back.plural == "kein Plural"
    assert "kein Plural" in back.text


def test_t4_tri_state_plural_unknown_load_bearing_omitted() -> None:
    """T4 State 3 (LOAD-BEARING): Missing/unknown plural data renders NOTHING about plural.

    Must NEVER be presented as 'kein Plural'. Data-driven only.
    """
    plural_result = render_plural("NOUN", None, 0)
    assert plural_result is None

    lemma = RenderLemmaData(
        lemma="Unbekannt",
        pos="NOUN",
        gender="das",
        plural=None,
        plural_none=0,
    )
    card_input = CardRenderInput(
        lemma=lemma,
        selected_languages=("de",),
    )
    back = render_back_face(card_input)
    assert back.plural is None
    assert "Plural" not in back.text
    assert "kein Plural" not in back.text


def test_t4_tri_state_plural_non_noun_omitted() -> None:
    """T4: Non-nouns render no plural line regardless of fields."""
    plural_verb = render_plural("VERB", "irrelevant", 1)
    assert plural_verb is None

    card_input = CardRenderInput(
        lemma=_sample_verb_lemma(),
        selected_languages=("de",),
    )
    back = render_back_face(card_input)
    assert back.plural is None
    assert "Plural" not in back.text
    assert "kein Plural" not in back.text


# ==============================================================================
# T5 — Grammar metadata
# ==============================================================================


def test_t5_grammar_metadata_all_conditional_fields() -> None:
    """T5: Each conditional grammar field renders when present."""
    verb = RenderLemmaData(
        lemma="aufstehen",
        pos="VERB",
        aux="sein",
        separable=1,
        particle="auf",
        reflexive=0,
        praesens_3sg="steht auf",
        praeteritum_3sg="stand auf",
        partizip_ii="aufgestanden",
        governs='["für+Akkusativ"]',
    )
    card_input = CardRenderInput(
        lemma=verb,
        selected_languages=("de",),
    )
    back = render_back_face(card_input)
    grammar = back.grammar

    assert grammar.pos == "VERB"
    assert grammar.aux == "sein"
    assert grammar.separable is True
    assert grammar.particle == "auf"
    assert grammar.praesens_3sg == "steht auf"
    assert grammar.praeteritum_3sg == "stand auf"
    assert grammar.partizip_ii == "aufgestanden"
    assert grammar.principal_parts == ("steht auf", "stand auf", "aufgestanden")
    assert grammar.governs == "für+Akkusativ"

    assert "Hilfsverb: sein" in back.text
    assert "Trennbar (Präfix: auf)" in back.text
    assert "3. P. Sing.: steht auf" in back.text
    assert "Prät.: stand auf" in back.text
    assert "Part. II: aufgestanden" in back.text
    assert "Rektion: für+Akkusativ" in back.text

    # Test adjective gradation and reflexive
    adj = RenderLemmaData(
        lemma="schnell",
        pos="ADJ",
        comparative="schneller",
        superlative="am schnellsten",
        reflexive=1,
    )
    adj_input = CardRenderInput(lemma=adj, selected_languages=("de",))
    adj_back = render_back_face(adj_input)
    assert adj_back.grammar.comparative == "schneller"
    assert adj_back.grammar.superlative == "am schnellsten"
    assert adj_back.grammar.reflexive is True
    assert "Steigerung: Komparativ: schneller, Superlativ: am schnellsten" in adj_back.text
    assert "reflexiv" in adj_back.text


def test_t5_grammar_metadata_absent_fields_contribute_nothing() -> None:
    """T5: Absent fields contribute nothing (no empty lines, no None strings)."""
    minimal = RenderLemmaData(
        lemma="hier",
        pos="ADV",
    )
    card_input = CardRenderInput(lemma=minimal, selected_languages=("de",))
    back = render_back_face(card_input)
    grammar = back.grammar

    assert grammar.aux is None
    assert grammar.particle is None
    assert grammar.separable is False
    assert grammar.reflexive is False
    assert grammar.principal_parts == ()
    assert grammar.governs is None
    assert grammar.comparative is None
    assert grammar.superlative is None
    assert grammar.genitive_sg is None
    assert grammar.plural is None

    assert "None" not in back.text
    assert "Hilfsverb" not in back.text
    assert "Trennbar" not in back.text
    assert "Stammformen" not in back.text
    assert "Rektion" not in back.text
    assert "Steigerung" not in back.text


# ==============================================================================
# T6 — Example sentences
# ==============================================================================


def test_t6_examples_de_and_en() -> None:
    """T6: German text plus English translation renders cleanly."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de", "en"),
        examples=(
            RenderExample(de="Wir bauen ein Haus.", en="We are building a house."),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.examples) == 1
    assert back.examples[0].de == "Wir bauen ein Haus."
    assert back.examples[0].en == "We are building a house."
    assert "Beispiele:" in back.text
    assert "• Wir bauen ein Haus." in back.text
    assert "  We are building a house." in back.text


def test_t6_examples_de_only() -> None:
    """T6: German-only example (en=None) renders cleanly."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de",),
        examples=(
            RenderExample(de="Das ist ein großes Haus.", en=None),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.examples) == 1
    assert back.examples[0].de == "Das ist ein großes Haus."
    assert back.examples[0].en is None
    assert "Beispiele:" in back.text
    assert "• Das ist ein großes Haus." in back.text
    assert "None" not in back.text


def test_t6_examples_empty_list_clean() -> None:
    """T6: examples=[] renders cleanly with no error and no empty example section."""
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de",),
        examples=(),
    )
    back = render_back_face(card_input)

    assert len(back.examples) == 0
    assert "Beispiele:" not in back.text


def test_t6_examples_from_example_entry() -> None:
    """T6: Direct ExampleEntry from app.dictionary is accepted and rendered."""
    entry = ExampleEntry(
        id=1,
        de="Er schläft.",
        en="He is sleeping.",
    )
    card_input = CardRenderInput(
        lemma=_sample_noun_lemma(),
        selected_languages=("de", "en"),
        examples=(entry,),
    )
    back = render_back_face(card_input)

    assert len(back.examples) == 1
    assert back.examples[0].de == "Er schläft."
    assert back.examples[0].en == "He is sleeping."


# ==============================================================================
# T7 — Derived compound decomposition (ADR-0004 D46)
# ==============================================================================


def test_t7_derived_compound_all_components_present_head_last() -> None:
    """T7: All components present renders ordered decomposition with head last."""
    components = (
        DerivedComponent(lemma="Flug", text="flight"),
        DerivedComponent(lemma="Hafen", text="port"),
    )
    card_input = CardRenderInput(
        lemma=RenderLemmaData(lemma="Flughafen", pos="NOUN", gender="der", plural="Flughäfen"),
        selected_languages=("en",),
        meanings=(
            MeaningBlock(
                language="en",
                origin="derived_component",
                components=components,
            ),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.meanings) == 1
    mb = back.meanings[0]
    assert mb.language == "en"
    assert mb.origin == "derived_component"
    assert mb.lines == ("Flug: flight", "Hafen: port")
    assert mb.decomposition == components
    assert "English:" in back.text
    assert "Flug: flight" in back.text
    assert "Hafen: port" in back.text


def test_t7_derived_compound_missing_component_suppresses_language_block() -> None:
    """T7: If ANY component lacks localized text in L, NO dictionary block is rendered for L.

    Meanwhile, another fully-covered language still renders its decomposition.
    """
    card_input = CardRenderInput(
        lemma=RenderLemmaData(lemma="Flughafen", pos="NOUN", gender="der", plural="Flughäfen"),
        selected_languages=("de", "en"),
        meanings=(
            # de is missing text for Hafen
            MeaningBlock(
                language="de",
                origin="derived_component",
                components=(
                    DerivedComponent(lemma="Flug", text="das Fliegen"),
                    DerivedComponent(lemma="Hafen", text=None),  # MISSING
                ),
            ),
            # en has text for all components
            MeaningBlock(
                language="en",
                origin="derived_component",
                components=(
                    DerivedComponent(lemma="Flug", text="flight"),
                    DerivedComponent(lemma="Hafen", text="port"),
                ),
            ),
        ),
    )
    back = render_back_face(card_input)

    # de is suppressed entirely (all-or-none rule)
    # en is fully rendered
    assert len(back.meanings) == 1
    assert back.meanings[0].language == "en"
    assert "Deutsch:" not in back.text
    assert "das Fliegen" not in back.text
    assert "English:" in back.text
    assert "Flug: flight" in back.text
    assert "Hafen: port" in back.text


def test_t7_derived_compound_user_meaning_overrides_whole_block() -> None:
    """T7: Note-local user meaning overrides the entire compound block for that language."""
    card_input = CardRenderInput(
        lemma=RenderLemmaData(lemma="Flughafen", pos="NOUN", gender="der", plural="Flughäfen"),
        selected_languages=("de", "en"),
        meanings=(
            MeaningBlock(
                language="de",
                origin="user",
                texts=("Flughafen für Passagierflugzeuge",),
            ),
            MeaningBlock(
                language="de",
                origin="derived_component",
                components=(
                    DerivedComponent(lemma="Flug", text="Flug"),
                    DerivedComponent(lemma="Hafen", text="Hafen"),
                ),
            ),
            MeaningBlock(
                language="en",
                origin="derived_component",
                components=(
                    DerivedComponent(lemma="Flug", text="flight"),
                    DerivedComponent(lemma="Hafen", text="port"),
                ),
            ),
        ),
    )
    back = render_back_face(card_input)

    assert len(back.meanings) == 2
    de_mb = back.meanings[0]
    assert de_mb.language == "de"
    assert de_mb.origin == "user"
    assert de_mb.is_user_authored is True
    assert de_mb.lines == ("Flughafen für Passagierflugzeuge",)
    assert "Flughafen für Passagierflugzeuge" in back.text

    en_mb = back.meanings[1]
    assert en_mb.language == "en"
    assert en_mb.origin == "derived_component"
    assert en_mb.lines == ("Flug: flight", "Hafen: port")


def test_t7_derived_compound_user_meaning_de_with_incomplete_en_components() -> None:
    """T7: User meaning for de renders marked user block while incomplete en components render
    nothing.
    """
    card_input = CardRenderInput(
        lemma=RenderLemmaData(lemma="Flughafen", pos="NOUN", gender="der", plural="Flughäfen"),
        selected_languages=("de", "en"),
        meanings=(
            MeaningBlock(
                language="de",
                origin="user",
                texts=("Passagierflughafen",),
            ),
            MeaningBlock(
                language="de",
                origin="derived_component",
                components=(
                    DerivedComponent(lemma="Flug", text="Flug"),
                    DerivedComponent(lemma="Hafen", text="Hafen"),
                ),
            ),
            MeaningBlock(
                language="en",
                origin="derived_component",
                components=(
                    DerivedComponent(lemma="Flug", text="flight"),
                    DerivedComponent(lemma="Hafen", text=None),
                ),
            ),
        ),
    )
    back = render_back_face(card_input)

    # de renders the marked user block
    assert len(back.meanings) == 1
    de_mb = back.meanings[0]
    assert de_mb.language == "de"
    assert de_mb.origin == "user"
    assert de_mb.is_user_authored is True
    assert "user-authored" in de_mb.heading
    assert de_mb.lines == ("Passagierflughafen",)
    assert "Deutsch (user-authored):" in back.text
    assert "Passagierflughafen" in back.text

    # en renders no dictionary block and no partial component composition appears anywhere
    assert "English:" not in back.text
    assert "flight" not in back.text
    assert "Flug: flight" not in back.text
    assert "Flug:" not in back.text
    assert not any(mb.language == "en" for mb in back.meanings)


# ==============================================================================
# T8 — Purity and determinism
# ==============================================================================


def test_t8_purity_and_determinism() -> None:
    """T8: Repeated calls with equal inputs yield equal outputs; inputs are not mutated."""
    lemma = _sample_noun_lemma()
    meanings = (
        MeaningBlock(language="de", origin="dictionary", texts=("Gebäude",)),
        MeaningBlock(language="en", origin="dictionary", texts=("house",)),
    )
    examples = (
        RenderExample(de="Wir bauen ein Haus.", en="We build a house."),
    )
    card_input = CardRenderInput(
        lemma=lemma,
        selected_languages=("de", "en"),
        meanings=meanings,
        examples=examples,
    )

    card_input_copy = copy.deepcopy(card_input)

    # Render multiple times
    res1 = render_card(card_input)
    res2 = render_card(card_input)
    res3 = render_card(card_input_copy)

    # Assert deterministic equality
    assert res1 == res2
    assert res1 == res3
    assert res1.front.text == res2.front.text
    assert res1.back.text == res2.back.text

    # Assert inputs were not mutated
    assert card_input == card_input_copy
    assert card_input.lemma == lemma
    assert card_input.meanings == meanings
    assert card_input.examples == examples


# ==============================================================================
# T9 — Language domain validation
# ==============================================================================


def test_t9_language_domain_unsupported_language_raises_value_error() -> None:
    """T9: Unsupported language (e.g. 'fr', 'es') in selected set raises ValueError."""
    with pytest.raises(ValueError, match="unsupported meaning language"):
        validate_selected_languages(("de", "fr"))

    with pytest.raises(ValueError, match="unsupported meaning language"):
        render_back_face(
            CardRenderInput(
                lemma=_sample_noun_lemma(),
                selected_languages=("de", "fr"),
            )
        )


def test_t9_language_domain_empty_selected_languages_raises_value_error() -> None:
    """T9: Empty selected language set raises ValueError."""
    with pytest.raises(ValueError, match="non-empty subset"):
        validate_selected_languages(())

    with pytest.raises(ValueError, match="non-empty subset"):
        render_back_face(
            CardRenderInput(
                lemma=_sample_noun_lemma(),
                selected_languages=(),
            )
        )


def test_t9_language_domain_persian_fa_raises_value_error() -> None:
    """T9: Persian 'fa' is rejected with ValueError under ADR-0007 D80."""
    with pytest.raises(ValueError, match="unsupported meaning language 'fa'"):
        validate_selected_languages(("fa",))

    with pytest.raises(ValueError, match="unsupported meaning language 'fa'"):
        render_back_face(
            CardRenderInput(
                lemma=_sample_noun_lemma(),
                selected_languages=("fa",),
            )
        )

    # Also rejected if passed in meaning blocks
    with pytest.raises(ValueError, match="unsupported meaning block language 'fa'"):
        render_back_face(
            CardRenderInput(
                lemma=_sample_noun_lemma(),
                selected_languages=("de",),
                meanings=(
                    MeaningBlock(language="fa", origin="dictionary", texts=("خانه",)),
                ),
            )
        )
