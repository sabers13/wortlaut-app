"""Pytest fixtures and configuration for flashcard tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.resolve import LemmaRecord, LookupProtocol, SenseRecord
from tools.build_dict import compute_lemma_semantic_ref


@pytest.fixture
def user_db_path(tmp_path: Path) -> Path:
    """Create an isolated PART-B user database from the reference schema."""
    schema = (Path(__file__).parents[1] / "reference" / "schema.sql").read_text()
    _, marker, part_b = schema.partition("-- PART B")
    assert marker, "reference schema must contain a PART B section"
    path = tmp_path / "user_test.sqlite"
    conn = sqlite3.connect(path)
    try:
        conn.executescript("-- PART B" + part_b)
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()
    return path


@pytest.fixture
def user_db(user_db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open the isolated user database with foreign-key enforcement enabled."""
    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


PART_A_SCHEMA_SQL = """
-- Numeric IDs (lemma.id, sense.id, etc.) are local per-asset keys only.
-- Durable cross-version identity is defined by semantic_ref.
CREATE TABLE IF NOT EXISTS lemma (
  id            INTEGER PRIMARY KEY,
  semantic_ref  TEXT NOT NULL UNIQUE,
  lemma         TEXT NOT NULL,
  pos           TEXT NOT NULL,
  gender        TEXT,
  plural        TEXT,
  plural_none   INTEGER NOT NULL DEFAULT 0 CHECK (plural_none IN (0,1)),
  genitive_sg   TEXT,
  aux           TEXT,
  separable     INTEGER DEFAULT 0,
  particle      TEXT,
  reflexive     INTEGER DEFAULT 0,
  praesens_3sg  TEXT,
  praeteritum_3sg TEXT,
  partizip_ii   TEXT,
  governs       TEXT,
  comparative   TEXT,
  superlative   TEXT,
  ipa           TEXT,
  ipa_source    TEXT,
  freq_rank     INTEGER,
  source        TEXT,
  license       TEXT,
  CHECK (plural_none = 0 OR plural IS NULL),
  UNIQUE(lemma, pos, gender)
);
CREATE INDEX IF NOT EXISTS ix_lemma_lookup ON lemma(lemma, pos);

CREATE TABLE IF NOT EXISTS surface_form (
  form      TEXT NOT NULL,
  lemma_id  INTEGER NOT NULL REFERENCES lemma(id),
  PRIMARY KEY (form, lemma_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS sense (
  id                INTEGER PRIMARY KEY,
  lemma_id          INTEGER NOT NULL REFERENCES lemma(id),
  semantic_ref      TEXT NOT NULL UNIQUE,
  source_namespace  TEXT NOT NULL,
  source_ref        TEXT NOT NULL,
  ord               INTEGER NOT NULL DEFAULT 0,
  register          TEXT,
  source            TEXT,
  license           TEXT
);
CREATE INDEX IF NOT EXISTS ix_sense_lemma ON sense(lemma_id, ord);

CREATE TABLE IF NOT EXISTS sense_meaning (
  id        INTEGER PRIMARY KEY,
  sense_id  INTEGER NOT NULL REFERENCES sense(id) ON DELETE CASCADE,
  language  TEXT NOT NULL,
  kind      TEXT NOT NULL CHECK (kind IN ('definition', 'synonym', 'translation')),
  ord       INTEGER NOT NULL DEFAULT 0,
  text      TEXT NOT NULL,
  source    TEXT NOT NULL,
  license   TEXT NOT NULL,
  UNIQUE(sense_id, language, kind, ord)
);
CREATE INDEX IF NOT EXISTS ix_sense_meaning ON sense_meaning(sense_id, language, ord);

CREATE TABLE IF NOT EXISTS sense_meaning_derivation (
  generated_meaning_id INTEGER NOT NULL
      REFERENCES sense_meaning(id) ON DELETE CASCADE,
  source_meaning_id INTEGER NOT NULL
      REFERENCES sense_meaning(id) ON DELETE RESTRICT,
  PRIMARY KEY (generated_meaning_id, source_meaning_id),
  CHECK (generated_meaning_id <> source_meaning_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS example (
  id           INTEGER PRIMARY KEY,
  de           TEXT NOT NULL,
  en           TEXT,
  source       TEXT,
  source_ref   TEXT,
  license      TEXT,
  token_count  INTEGER,
  has_proper   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS example_lemma (
  lemma_id   INTEGER NOT NULL REFERENCES lemma(id),
  example_id INTEGER NOT NULL REFERENCES example(id),
  PRIMARY KEY (lemma_id, example_id)
) WITHOUT ROWID;
"""


@dataclass
class InMemoryLookupOracle(LookupProtocol):
    """In-memory pure test double implementing LookupProtocol."""

    lemmas: list[LemmaRecord] = field(default_factory=list)
    surface_forms: dict[str, list[LemmaRecord]] = field(default_factory=dict)
    senses: dict[int, list[SenseRecord]] = field(default_factory=dict)
    _sense_id_counter: int = 1

    def add_lemma(
        self,
        lemma: str,
        pos: str,
        gender: str | None = None,
        lemma_id: int | None = None,
        semantic_ref: str | None = None,
        freq_rank: int | None = None,
    ) -> LemmaRecord:
        lid = lemma_id if lemma_id is not None else len(self.lemmas) + 1
        sem_ref = (
            semantic_ref
            if semantic_ref is not None
            else compute_lemma_semantic_ref(lemma, pos, gender)
        )
        record = LemmaRecord(
            id=lid,
            lemma=lemma,
            pos=pos,
            gender=gender,
            semantic_ref=sem_ref,
            freq_rank=freq_rank,
        )
        self.lemmas.append(record)

        # Default source sense if not already populated for this lemma
        if lid not in self.senses:
            sid = self._sense_id_counter
            self._sense_id_counter += 1
            default_sense_ref = f"sense:v1:{sem_ref.removeprefix('lemma:v1:')}_0"
            self.senses[lid] = [
                SenseRecord(
                    id=sid,
                    lemma_id=lid,
                    ord=0,
                    semantic_ref=default_sense_ref,
                )
            ]

        return record

    def add_sense(
        self,
        lemma_id: int,
        ord: int = 0,
        semantic_ref: str | None = None,
        sense_id: int | None = None,
    ) -> SenseRecord:
        sid = sense_id if sense_id is not None else self._sense_id_counter
        self._sense_id_counter += 1
        sem_ref = semantic_ref or f"sense:v1:custom_{lemma_id}_{ord}_{sid}"
        rec = SenseRecord(id=sid, lemma_id=lemma_id, ord=ord, semantic_ref=sem_ref)
        self.senses.setdefault(lemma_id, []).append(rec)
        return rec

    def add_surface_form(self, form: str, record: LemmaRecord) -> None:
        entries = self.surface_forms.setdefault(form.lower(), [])
        if record not in entries:
            entries.append(record)

    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[LemmaRecord]:
        matches: list[LemmaRecord] = []
        target = lemma.strip().lower()
        for rec in self.lemmas:
            if rec.lemma.lower() == target:
                if pos is not None and rec.pos != pos:
                    continue
                if gender is not None and rec.gender != gender:
                    continue
                matches.append(rec)
        return matches

    def lookup_surface_form(self, form: str) -> Sequence[LemmaRecord]:
        target = form.strip().lower()
        return self.surface_forms.get(target, [])

    def lookup_senses(self, lemma_id: int) -> Sequence[SenseRecord]:
        return self.senses.get(lemma_id, [])


@pytest.fixture
def part_a_schema() -> str:
    """Return PART A schema SQL string."""
    return PART_A_SCHEMA_SQL


@pytest.fixture
def empty_oracle() -> InMemoryLookupOracle:
    """Return an empty pure in-memory lookup oracle."""
    return InMemoryLookupOracle()


@pytest.fixture
def populated_oracle() -> InMemoryLookupOracle:
    """Return an in-memory lookup oracle populated with standard test vocabulary."""
    oracle = InMemoryLookupOracle()
    # Nouns with gender disambiguation
    oracle.add_lemma("See", "NOUN", "der", lemma_id=1, freq_rank=100)  # der See (lake)
    oracle.add_lemma("See", "NOUN", "die", lemma_id=2, freq_rank=150)  # die See (sea)
    oracle.add_lemma("Bank", "NOUN", "die", lemma_id=3, freq_rank=50)  # die Bank

    # Compound test vocabulary (ADR-0001 §10 verified case)
    l_kranken = oracle.add_lemma("kranken", "NOUN", "die", lemma_id=4, freq_rank=500)
    oracle.add_lemma("Versicherung", "NOUN", "die", lemma_id=5, freq_rank=200)
    oracle.add_lemma("Karte", "NOUN", "die", lemma_id=6, freq_rank=80)
    oracle.add_lemma("Haus", "NOUN", "das", lemma_id=7, freq_rank=20)
    oracle.add_lemma("Tür", "NOUN", "die", lemma_id=8, freq_rank=90)
    oracle.add_lemma("Tag", "NOUN", "der", lemma_id=9, freq_rank=10)
    oracle.add_lemma("Licht", "NOUN", "das", lemma_id=10, freq_rank=110)

    # Verbs and separable verbs
    l_anrufen = oracle.add_lemma("anrufen", "VERB", None, lemma_id=11, freq_rank=60)
    oracle.add_lemma("rufen", "VERB", None, lemma_id=12, freq_rank=70)

    # Surface forms
    oracle.add_surface_form("häuser", oracle.lemmas[6])  # Haus
    oracle.add_surface_form("Häuser", oracle.lemmas[6])
    oracle.add_surface_form("rief an", l_anrufen)
    oracle.add_surface_form("ruft an", l_anrufen)
    oracle.add_surface_form("Kranken", l_kranken)

    return oracle


@pytest.fixture
def create_test_db(tmp_path: Path) -> Callable[..., Path]:
    """Factory fixture to create and populate a temporary PART A SQLite database."""

    def _factory(populate: bool = True) -> Path:
        db_path = tmp_path / "dict_test.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(PART_A_SCHEMA_SQL)

        if populate:
            # Insert test lemmas
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
                    None,
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
                    None,
                    None,
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
                    None,
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
                    None,
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
                    "Hauses",
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
                    None,
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
                    None,
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
                    None,
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
                    None,
                    1,
                    "an",
                    0,
                    "ruft an",
                    "rief an",
                    "angerufen",
                    None,
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
                    None,
                    0,
                    None,
                    0,
                    "ruft",
                    "rief",
                    "gerufen",
                    None,
                    None,
                    None,
                    "ˈʁuːfn̩",
                    "wiktionary",
                    70,
                    "wiktionary",
                    "CC BY-SA",
                ),
            ]
            conn.executemany(
                """
                INSERT INTO lemma (
                    id, semantic_ref, lemma, pos, gender, plural_none, plural, genitive_sg,
                    aux, separable, particle, reflexive, praesens_3sg, praeteritum_3sg,
                    partizip_ii, governs, comparative, superlative, ipa, ipa_source,
                    freq_rank, source, license
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
                        r[9], r[10], r[11], r[12], r[13], r[14],
                        r[15], r[16], r[17], r[18], r[19], r[20],
                        r[21], r[22], r[23],
                    )
                    for r in lemma_rows
                ],
            )

            # Insert surface forms
            conn.executemany(
                "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
                [
                    ("Häuser", 7),
                    ("häuser", 7),
                    ("rief an", 11),
                    ("ruft an", 11),
                    ("ruft", 12),
                ],
            )

            # Insert senses
            sense_insert_sql = (
                "INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, "
                "source_ref, ord, register, source, license) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            conn.executemany(
                sense_insert_sql,
                [
                    (
                        1,
                        1,
                        "sense:v1:see_der_0",
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
                        "sense:v1:see_die_0",
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
                        "sense:v1:bank_0",
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
                        "sense:v1:kranken_0",
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
                        "sense:v1:versicherung_0",
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
                        "sense:v1:karte_0",
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
                        "sense:v1:haus_0",
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
                        "sense:v1:tuer_0",
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
                        "sense:v1:tag_0",
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
                        "sense:v1:licht_0",
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
                        "sense:v1:anrufen_0",
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
                        "sense:v1:rufen_0",
                        "wiktextract:enwiktionary",
                        "senseid:en-shout-1",
                        0,
                        None,
                        "wiktionary",
                        "CC BY-SA 4.0",
                    ),
                ],
            )

            # Insert sense_meaning
            meaning_insert_sql = (
                "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, "
                "source, license) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
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
                    (
                        11,
                        11,
                        "en",
                        "translation",
                        0,
                        "to call, phone",
                        "wiktionary",
                        "CC BY-SA 4.0",
                    ),
                    (
                        12,
                        12,
                        "en",
                        "translation",
                        0,
                        "to shout, cry out",
                        "wiktionary",
                        "CC BY-SA 4.0",
                    ),
                ],
            )

            # Insert examples
            example_insert_sql = (
                "INSERT INTO example (id, de, en, source, license, token_count) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            conn.executemany(
                example_insert_sql,
                [
                    (1, "Der See ist tief.", "The lake is deep.", "tatoeba", "CC BY 2.0 FR", 5),
                    (
                        2,
                        "Die See ist stürmisch.",
                        "The sea is stormy.",
                        "tatoeba",
                        "CC BY 2.0 FR",
                        5,
                    ),
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

            # Insert example_lemma mappings
            conn.executemany(
                "INSERT INTO example_lemma (lemma_id, example_id) VALUES (?, ?)",
                [
                    (1, 1),
                    (2, 2),
                    (11, 3),
                ],
            )

            conn.commit()

        conn.close()
        return db_path

    return _factory
