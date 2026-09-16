"""Seeds a 5-word dictionary and exercises the whole pipeline.

No spaCy, no network. Run: python3 reference/smoke_test.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import deck  # noqa: E402
from app.dictionary import Dictionary  # noqa: E402
from app.render import (  # noqa: E402
    CardRenderInput,
    DerivedComponent,
    MeaningBlock,
    RenderedCard,
    RenderExample,
    RenderLemmaData,
    render_card,
)
from app.resolve import split_compound  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_TEXT = SCHEMA_PATH.read_text(encoding="utf-8")

part_a_raw, _, part_b_raw = SCHEMA_TEXT.partition("-- PART B")
PART_A_SQL = part_a_raw
PART_B_SQL = "-- PART B" + part_b_raw


def seed_dict(path: str = "/tmp/dict.sqlite") -> str:
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript(PART_A_SQL)
    rows = [
        # id, semantic_ref, lemma, pos, gender, plural_none, plural, genitive_sg,
        # aux, separable, particle, reflexive, praesens_3sg, praeteritum_3sg,
        # partizip_ii, governs, comparative, superlative, ipa, ipa_source, freq_rank,
        # source, license
        (
            1,
            "lemma:v1:haus_noun_das",
            "Haus",
            "NOUN",
            "das",
            0,
            "die Häuser",
            "des Hauses",
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            "[]",
            None,
            None,
            "haʊ̯s",
            "wiktionary",
            10,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            2,
            "lemma:v1:karte_noun_die",
            "Karte",
            "NOUN",
            "die",
            0,
            "die Karten",
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            "[]",
            None,
            None,
            "ˈkaʁtə",
            "wiktionary",
            20,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            3,
            "lemma:v1:versicherung_noun_die",
            "Versicherung",
            "NOUN",
            "die",
            0,
            "die Versicherungen",
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            "[]",
            None,
            None,
            None,
            "wiktionary",
            30,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            4,
            "lemma:v1:kranken_noun_die",
            "kranken",
            "NOUN",
            "die",
            0,
            None,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            "[]",
            None,
            None,
            None,
            "wiktionary",
            40,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            5,
            "lemma:v1:anrufen_verb",
            "anrufen",
            "VERB",
            None,
            0,
            None,
            None,
            "haben",
            1,
            "an",
            0,
            "ruft an",
            "rief an",
            "angerufen",
            '["Akkusativ"]',
            None,
            None,
            "ˈanˌʁuːfn̩",
            "wiktionary",
            50,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
        (
            6,
            "lemma:v1:gross_adj",
            "groß",
            "ADJ",
            None,
            0,
            None,
            None,
            None,
            0,
            None,
            0,
            None,
            None,
            None,
            "[]",
            "größer",
            "größten",
            "ɡʁoːs",
            "wiktionary",
            60,
            "wiktionary",
            "CC BY-SA 4.0",
        ),
    ]
    for r in rows:
        db.execute(
            """
            INSERT INTO lemma (
                id, semantic_ref, lemma, pos, gender, plural_none, plural, genitive_sg,
                aux, separable, particle, reflexive, praesens_3sg, praeteritum_3sg,
                partizip_ii, governs, comparative, superlative, ipa, ipa_source,
                freq_rank, source, license
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            r,
        )

    # Surface forms
    db.execute("INSERT INTO surface_form (form, lemma_id) VALUES ('Häuser', 1)")
    db.execute("INSERT INTO surface_form (form, lemma_id) VALUES ('häuser', 1)")
    db.execute("INSERT INTO surface_form (form, lemma_id) VALUES ('ruft an', 5)")
    db.execute("INSERT INTO surface_form (form, lemma_id) VALUES ('rief an', 5)")

    # Senses and sense_meaning
    senses_data = [
        (1, 1, "sense:v1:haus_0", "wiktextract", "h1", 0, "house; building", "Gebäude zum Wohnen"),
        (2, 1, "sense:v1:haus_1", "wiktextract", "h2", 1, "household, family line", None),
        (3, 2, "sense:v1:karte_0", "wiktextract", "k1", 0, "card; map; ticket", "Karte"),
        (4, 3, "sense:v1:versicherung_0", "wiktextract", "v1", 0, "insurance", "Versicherung"),
        (5, 4, "sense:v1:kranken_0", "wiktextract", "kr1", 0, "sick, patients", "Kranken"),
        (6, 5, "sense:v1:anrufen_0", "wiktextract", "a1", 0, "to call, to phone", "anrufen"),
        (7, 6, "sense:v1:gross_0", "wiktextract", "g1", 0, "big, large, tall", "groß"),
    ]
    for sid, lid, sref, ns, ssrc, ord_val, gloss_en, gloss_de in senses_data:
        db.execute(
            """
            INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, source_ref,
                               ord, source, license)
            VALUES (?, ?, ?, ?, ?, ?, 'wiktionary', 'CC-BY-SA-3.0')
            """,
            (sid, lid, sref, ns, ssrc, ord_val),
        )
        if gloss_en:
            db.execute(
                """
                INSERT INTO sense_meaning (sense_id, language, kind, ord, text, source, license)
                VALUES (?, 'en', 'definition', 0, ?, 'wiktionary', 'CC-BY-SA-3.0')
                """,
                (sid, gloss_en),
            )
        if gloss_de:
            db.execute(
                """
                INSERT INTO sense_meaning (sense_id, language, kind, ord, text, source, license)
                VALUES (?, 'de', 'definition', 0, ?, 'wiktionary', 'CC-BY-SA-3.0')
                """,
                (sid, gloss_de),
            )

    # Example linked to anrufen
    db.execute(
        """
        INSERT INTO example (
            id, de, en, source, source_ref, license, token_count, has_proper
        ) VALUES (
            1, 'Ruf mich morgen an!', 'Call me tomorrow!', 'tatoeba', '12345',
            'CC-BY-2.0', 5, 0
        )
        """
    )
    db.execute("INSERT INTO example_lemma (lemma_id, example_id) VALUES (5, 1)")

    db.commit()
    db.close()
    return path


def seed_user_db(path: str = "/tmp/u1.sqlite") -> sqlite3.Connection:
    if os.path.exists(path):
        os.remove(path)
    udb = sqlite3.connect(path, isolation_level=None)
    udb.row_factory = sqlite3.Row
    udb.execute("PRAGMA foreign_keys = ON")
    udb.executescript(PART_B_SQL)
    return udb


def box(card: RenderedCard, label: str) -> None:
    face = card.front if label == "FRONT" else card.back
    print(f"  ┌─ {label} " + "─" * max(0, 26 - len(label)) + "┐")
    if hasattr(face, "headword") and face.headword:
        print(f"  │ {face.headword[:26]:<26} │")
    if hasattr(face, "ipa") and face.ipa:
        print(f"  │ [{face.ipa}]{'':<{max(0, 24 - len(face.ipa))}} │")
    if hasattr(face, "text") and face.text:
        print(f"  │ {face.text[:26]:<26} │")
    if hasattr(face, "meanings"):
        for mb in face.meanings:
            for line in mb.lines:
                print(f"  │ • {line[:24]:<24} │")
    if hasattr(face, "examples"):
        for ex in face.examples:
            print(f"  │ {ex.de[:26]:<26} │")
            if ex.en:
                print(f"  │ {ex.en[:26]:<26} │")
    print("  └" + "─" * 28 + "┘")


def main() -> None:
    dict_file = seed_dict()
    d = Dictionary(dict_file)

    print("=== 1. exact hit: noun ===")
    matches = d.lookup_exact("Haus", pos="NOUN", gender="das")
    assert len(matches) == 1
    lem = matches[0]
    render_inp = CardRenderInput(
        lemma=RenderLemmaData.from_lemma_entry(lem),
        selected_languages=("de", "en"),
        meanings=(
            MeaningBlock(language="de", origin="dictionary", texts=("Gebäude zum Wohnen",)),
            MeaningBlock(language="en", origin="dictionary", texts=("house; building",)),
        ),
    )
    rendered = render_card(render_inp)
    box(rendered, "FRONT")
    box(rendered, "BACK")

    print("\n=== 2. exact hit: separable verb ===")
    v_matches = d.lookup_exact("anrufen", pos="VERB")
    assert len(v_matches) == 1
    v_lem = v_matches[0]
    v_render = CardRenderInput(
        lemma=RenderLemmaData.from_lemma_entry(v_lem),
        selected_languages=("en",),
        meanings=(
            MeaningBlock(language="en", origin="dictionary", texts=("to call, to phone",)),
        ),
        examples=(
            RenderExample(de="Ruf mich morgen an!", en="Call me tomorrow!"),
        ),
    )
    v_rendered = render_card(v_render)
    box(v_rendered, "FRONT")
    box(v_rendered, "BACK")

    print("\n=== 3. compound fallback (no dict entry) ===")
    split_res = split_compound("Krankenversicherungskarte", d)
    assert split_res is not None
    print("  split:", split_res.components)
    comp_render = CardRenderInput(
        lemma=RenderLemmaData(lemma="Krankenversicherungskarte", pos="NOUN", gender="die"),
        selected_languages=("en",),
        meanings=(
            MeaningBlock(
                language="en",
                origin="derived_component",
                components=(
                    DerivedComponent(lemma="kranken", text="sick, patients"),
                    DerivedComponent(lemma="versicherung", text="insurance"),
                    DerivedComponent(lemma="karte", text="card; map; ticket"),
                ),
            ),
        ),
    )
    comp_rendered = render_card(comp_render)
    box(comp_rendered, "BACK")

    print("\n=== 4. unresolved -> needs_gloss, card still created ===")
    stub_refs = d.resolve("Feierabend", pos="NOUN", gender="der")
    assert stub_refs[0].status == "needs_gloss"
    stub_render = CardRenderInput(
        lemma=RenderLemmaData(lemma="Feierabend", pos="NOUN", gender="der"),
        selected_languages=("en",),
        meanings=(),
    )
    stub_rendered = render_card(stub_render)
    box(stub_rendered, "BACK")

    print("\n=== 5. deck write + FSRS ===")
    udb = seed_user_db()
    deck_id = deck.create_deck(udb, "Lektion 1")
    nid = deck.create_note(
        udb,
        lemma_semantic_ref="lemma:v1:feierabend_noun_der",
        status="needs_gloss",
        meaning_languages=("en",),
    )
    deck.add_note_to_deck(udb, nid, deck_id)
    print(f"  note {nid} created")

    card_row = udb.execute("SELECT id FROM card WHERE note_id=?", (nid,)).fetchone()
    assert card_row is not None
    cid = int(card_row[0])

    r_good = deck.review(udb, cid, 4)
    print("  due after Good:", r_good.due_at.isoformat()[:19])
    r_again = deck.review(udb, cid, 1)
    print("  due after Again:", r_again.due_at.isoformat()[:19])

    deck.set_user_meaning(udb, nid, "en", "end of the workday")
    rev_count = udb.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]
    print("  reviews logged:", rev_count)
    assert rev_count == 2

    r_meanings = deck.resolved_meanings(udb, nid, None)
    print("  note meanings now:", r_meanings)
    assert r_meanings == {"en": ("end of the workday",)}

    udb.close()
    d.close()
    print("\nOK")


if __name__ == "__main__":
    main()
