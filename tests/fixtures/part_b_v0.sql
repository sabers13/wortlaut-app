-- M2 test fixture: exact pre-M2 PART-B schema at main c1455b0d833190afe9c02e3dfcd9b0eff0358f34.
--
-- This is the PART-B section of reference/schema.sql BEFORE the M2
-- duplicate-identity unit: no identity key column on note, no partial
-- unique identity index, PRAGMA user_version = 0.
--
-- Tests build version-0 databases from THIS file only. Never generate old
-- DBs by editing the current schema after creation. No PART-A tables here.
-- PART B — per user, mutable. This script is applied to a separate user DB.
-- ============================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS deck (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL
);

-- Resolver status and computed meaning availability are deliberately separate.
-- Durable dictionary identity is semantic references, never asset-local IDs.
CREATE TABLE IF NOT EXISTS note (
  id                  INTEGER PRIMARY KEY,
  lemma_semantic_ref  TEXT NOT NULL,
  sense_semantic_ref  TEXT,
  status              TEXT NOT NULL
                      CHECK (status IN ('resolved', 'needs_gloss',
                                        'derived_compound', 'orphaned')),
  created_at          TEXT NOT NULL,
  due_at              TEXT NOT NULL,
  interval_days       REAL NOT NULL DEFAULT 0,
  ease_factor         REAL NOT NULL DEFAULT 2.5,
  review_count        INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
  last_confidence     INTEGER CHECK (last_confidence BETWEEN 1 AND 5)
);
CREATE INDEX IF NOT EXISTS ix_note_due ON note(due_at);

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

-- Pre-M2 databases report schema version 0.
PRAGMA user_version = 0;
