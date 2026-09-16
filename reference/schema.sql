-- ============================================================
-- PART A — shared, derived, ships as a read-only asset.
-- Built offline by tools/build_dict.py. No user data here.
-- Numeric IDs (lemma.id, sense.id, etc.) are local per-asset keys only.
-- Durable cross-version identity is defined by semantic_ref.
-- ============================================================

CREATE TABLE IF NOT EXISTS lemma (
  id            INTEGER PRIMARY KEY,
  semantic_ref  TEXT NOT NULL UNIQUE,
  lemma         TEXT NOT NULL,
  pos           TEXT NOT NULL,          -- NOUN | VERB | ADJ | ADV | PREP ...
  gender        TEXT,                   -- der | die | das
  plural        TEXT,
  plural_none   INTEGER NOT NULL DEFAULT 0 CHECK (plural_none IN (0,1)),
  genitive_sg   TEXT,
  aux           TEXT,                   -- haben | sein
  separable     INTEGER DEFAULT 0,
  particle      TEXT,
  reflexive     INTEGER DEFAULT 0,
  praesens_3sg  TEXT,
  praeteritum_3sg TEXT,
  partizip_ii   TEXT,
  governs       TEXT,                   -- JSON: ["Akkusativ"] or ["für+Akkusativ"]
  comparative   TEXT,
  superlative   TEXT,
  ipa           TEXT,
  ipa_source    TEXT,                   -- wiktionary | espeak
  freq_rank     INTEGER,
  source        TEXT,                   -- wiktionary | llm_generated_v1 | contributed
  license       TEXT,
  CHECK (plural_none = 0 OR plural IS NULL),
  UNIQUE(lemma, pos, gender)            -- der See / die See
);
CREATE INDEX IF NOT EXISTS ix_lemma_lookup ON lemma(lemma, pos);

CREATE TABLE IF NOT EXISTS surface_form (
  form      TEXT NOT NULL,              -- "Häuser", "rief an", "ruft"
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
  source       TEXT,                    -- tatoeba | wiktionary_de
  source_ref   TEXT,                    -- upstream id, for re-diffing dumps
  license      TEXT,
  token_count  INTEGER,
  has_proper   INTEGER DEFAULT 0
);

-- inverted index: lemma -> example. Built with the SAME resolver
-- used at highlight time, so "anrufen" matches "ruft ... an".
CREATE TABLE IF NOT EXISTS example_lemma (
  lemma_id   INTEGER NOT NULL REFERENCES lemma(id),
  example_id INTEGER NOT NULL REFERENCES example(id),
  PRIMARY KEY (lemma_id, example_id)
) WITHOUT ROWID;

-- ============================================================
-- PART B — per user, mutable. This script is applied to a separate user DB.
-- ============================================================

PRAGMA foreign_keys = ON;

-- M4 folders: a flat, user-defined grouping of decks. Deck membership is a
-- single nullable FK on deck; assignments have exactly one authoritative
-- representation (deck.folder_id). No nesting and no many-to-many membership.
CREATE TABLE IF NOT EXISTS folder (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL,
  folder_id   INTEGER REFERENCES folder(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_deck_folder ON deck(folder_id);

-- Resolver status and computed meaning availability are deliberately separate.
-- Durable dictionary identity is semantic references, never asset-local IDs.
-- M2: note.dictionary_key is the single duplicate-safe logical identity.
-- It is NULL exactly when legacy identity is ambiguous / quarantined /
-- not safely canonicalized; NULL never means invalid or deletable.
CREATE TABLE IF NOT EXISTS note (
  id                  INTEGER PRIMARY KEY,
  lemma_semantic_ref  TEXT NOT NULL,
  sense_semantic_ref  TEXT,
  status              TEXT NOT NULL
                      CHECK (status IN ('resolved', 'needs_gloss',
                                        'derived_compound', 'orphaned')),
  dictionary_key      TEXT,
  created_at          TEXT NOT NULL,
  due_at              TEXT NOT NULL,
  interval_days       REAL NOT NULL DEFAULT 0,
  ease_factor         REAL NOT NULL DEFAULT 2.5,
  review_count        INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
  last_confidence     INTEGER CHECK (last_confidence BETWEEN 1 AND 5)
);
CREATE INDEX IF NOT EXISTS ix_note_due ON note(due_at);

-- M2 duplicate-safe identity backstop: at most one keyed note per logical
-- identity. Legacy ambiguous rows stay NULL and are therefore exempt.
CREATE UNIQUE INDEX IF NOT EXISTS ux_note_dictionary_key
ON note(dictionary_key)
WHERE dictionary_key IS NOT NULL;

-- Card faces are rendered from structured state at read time and are never stored.
CREATE TABLE IF NOT EXISTS card (
  id          INTEGER PRIMARY KEY,
  note_id     INTEGER NOT NULL UNIQUE REFERENCES note(id) ON DELETE RESTRICT,
  state       INTEGER NOT NULL,
  step        INTEGER,
  stability   REAL,
  difficulty  REAL,
  due_at      TEXT NOT NULL,
  last_review TEXT
);
CREATE INDEX IF NOT EXISTS ix_card_due ON card(due_at);

-- Append-only: application code only inserts these rows. RESTRICT preserves history.
CREATE TABLE IF NOT EXISTS review_log (
  id             INTEGER PRIMARY KEY,
  card_id        INTEGER NOT NULL REFERENCES card(id) ON DELETE RESTRICT,
  confidence     INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
  rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
  scheduled_days REAL NOT NULL,
  elapsed_days   REAL NOT NULL,
  reviewed_at    TEXT NOT NULL
);

-- Deleting a deck deletes memberships, not notes. The deck layer puts a note
-- whose last membership disappeared into the Orphaned deck.
CREATE TABLE IF NOT EXISTS note_deck (
  note_id     INTEGER NOT NULL REFERENCES note(id) ON DELETE RESTRICT,
  deck_id     INTEGER NOT NULL REFERENCES deck(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (note_id, deck_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS note_meaning_lang (
  note_id  INTEGER NOT NULL REFERENCES note(id) ON DELETE CASCADE,
  lang     TEXT NOT NULL CHECK (lang IN ('de', 'en')),
  PRIMARY KEY (note_id, lang)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS note_user_meaning (
  note_id      INTEGER NOT NULL REFERENCES note(id) ON DELETE CASCADE,
  lang         TEXT NOT NULL CHECK (lang IN ('de', 'en')),
  meaning_text TEXT NOT NULL CHECK (length(trim(meaning_text)) > 0),
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (note_id, lang)
) WITHOUT ROWID;

-- One direct row binds a resolved note. Ordered component rows bind a derived
-- compound. Semantic refs are durable; cached numeric IDs are current-asset only.
CREATE TABLE IF NOT EXISTS note_dictionary_binding (
  note_id             INTEGER NOT NULL REFERENCES note(id) ON DELETE CASCADE,
  role                TEXT NOT NULL CHECK (role IN ('direct', 'component')),
  component_ord       INTEGER NOT NULL DEFAULT 0 CHECK (component_ord >= 0),
  lemma_semantic_ref  TEXT NOT NULL,
  sense_semantic_ref  TEXT NOT NULL,
  cached_lemma_id     INTEGER,
  cached_sense_id     INTEGER,
  binding_status      TEXT NOT NULL
                       CHECK (binding_status IN ('bound', 'unbound', 'ambiguous')),
  -- Every component row records the resolver-declared vector length.  This is
  -- deliberately independent of the rows still present at render/relink time:
  -- D46 must reject a truncated prefix rather than render it as a compound.
  component_count     INTEGER CHECK (
                        (role = 'component' AND component_count IS NOT NULL
                         AND component_count > 0)
                        OR (role = 'direct' AND component_count IS NULL)
                      ),
  last_relinked_at    TEXT,
  PRIMARY KEY (note_id, role, component_ord)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS active_dictionary_metadata (
  singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
  active_version  TEXT NOT NULL,
  active_filename TEXT NOT NULL,
  active_sha256   TEXT NOT NULL,
  activated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_pronunciation (
  note_id        INTEGER PRIMARY KEY REFERENCES note(id) ON DELETE CASCADE,
  media_filename TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  byte_size      INTEGER NOT NULL CHECK (byte_size >= 0),
  format         TEXT NOT NULL,
  source_type    TEXT NOT NULL CHECK (source_type IN ('recorded', 'uploaded')),
  created_at     TEXT NOT NULL
);

-- M4 PART-B schema version. Fresh databases are created at version 2.
-- Pre-M4 databases report version 1; pre-M2 databases report version 0.
-- Both are transactionally migrated by ensure_user_db; anything newer fails
-- closed (newer Wortlaut required).
PRAGMA user_version = 2;
