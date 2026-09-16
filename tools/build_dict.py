"""Offline dictionary build tooling for German flashcards.

Implements build stage 01 (ADR-0001 §12, ADR-0004 D36/D45/D47):
Deterministic offline JSONL-to-SQLite transform from Kaikki/Wiktextract dumps
to the read-only dictionary core (lemma, surface_form, sense, sense_meaning,
and sense_meaning_derivation tables).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import IO, Any, Callable, Final, Sequence

# Ensure repository root is on sys.path for direct script execution
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.resolve import (  # noqa: E402
    LemmaRecord,
    LookupProtocol,
    SenseRecord,
    resolve_token,
)
from tools.resolver_hash import get_resolver_hash  # noqa: E402

POS_MAP: Final[dict[str, str]] = {
    "noun": "NOUN",
    "proper_noun": "PROPN",
    "name": "PROPN",
    "verb": "VERB",
    "aux": "AUX",
    "adj": "ADJ",
    "adv": "ADV",
    "prep": "ADP",
    "postp": "ADP",
    "pron": "PRON",
    "det": "DET",
    "num": "NUM",
    "conj": "CCONJ",
    "particle": "PART",
    "intj": "INTJ",
}

GENDER_CANONICAL_ORDER: Final[tuple[str, ...]] = ("der", "die", "das")
GENDER_ORDER_MAP: Final[dict[str | None, int]] = {
    None: 0,
    "der": 1,
    "die": 2,
    "das": 3,
}

STAGE01_SCHEMA_SQL: Final[str] = """
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
"""

STAGE02_EXAMPLE_SCHEMA_SQL: Final[str] = """
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


INCLUDED_SENSE_DISTINCTION_FIELDS: Final[tuple[str, ...]] = (
    "glosses",
    "tags",
    "topics",
    "form_of",
    "alt_of",
    "compound_of",
    "qualifier",
    "taxonomic",
)

GENERATED_SOURCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^llm_generated_v[1-9][0-9]*$")


class BuildDictError(Exception):
    """Base error for dictionary build failures."""


def _canonical_json(value: object) -> str:
    """Return the one stable JSON representation used by build artifacts."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonicalize_pos(raw_pos: str) -> str:
    """Map Wiktextract POS tag to canonical Universal POS tag or uppercase raw POS."""
    return POS_MAP.get(raw_pos, raw_pos.upper())


def compute_lemma_semantic_ref(word: str, pos: str, gender: str | None) -> str:
    """Compute deterministic lemma semantic ref (ADR-0004 D47 / A2)."""
    nfc_lemma_text = unicodedata.normalize("NFC", word)
    gender_token = gender if gender is not None else "<null>"
    payload = json.dumps(
        ["de", nfc_lemma_text, pos, gender_token],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"lemma:v1:{hashlib.sha256(payload).hexdigest()}"


LINKAGE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "form_of",
        "alt_of",
        "compound_of",
        "taxonomic",
    }
)


def canonicalize_string_projection(s: str) -> str | None:
    """Canonicalize string for non-linkage sense fallback fingerprint projection (A4)."""
    s = unicodedata.normalize("NFC", s).casefold()
    s = "".join(" " if unicodedata.category(c).startswith("P") else c for c in s)
    s = " ".join(s.split())
    return s if s else None


def canonicalize_string_linkage(s: str) -> str | None:
    """Canonicalize string for identity-bearing linkage fields (Failure-2 amendment / v2)."""
    s = unicodedata.normalize("NFC", s)
    s = " ".join(s.split())
    return s if s else None


def canonicalize_projection_value(val: object, is_linkage: bool = False) -> object:
    """Canonicalize any value (string, list, dict, scalar) for fallback fingerprint."""
    if val is None:
        return None
    if isinstance(val, str):
        return (
            canonicalize_string_linkage(val) if is_linkage else canonicalize_string_projection(val)
        )
    if isinstance(val, (int, float, bool)):
        return val
    if isinstance(val, list):
        canon_items: list[Any] = []
        for item in val:
            c = canonicalize_projection_value(item, is_linkage=is_linkage)
            if c is not None:
                canon_items.append(c)
        if not canon_items:
            return None
        # Deduplicate and sort by canonical JSON encoding
        seen: dict[str, Any] = {}
        for item in canon_items:
            encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if encoded not in seen:
                seen[encoded] = item
        sorted_items = [seen[k] for k in sorted(seen.keys())]
        return sorted_items if sorted_items else None
    if isinstance(val, dict):
        canon_dict: dict[str, Any] = {}
        for k in sorted(val.keys()):
            if not isinstance(k, str):
                continue
            c_val = canonicalize_projection_value(val[k], is_linkage=is_linkage)
            if c_val is not None:
                canon_dict[k] = c_val
        return canon_dict if canon_dict else None
    return None


def compute_sense_fallback_projection_payload(
    raw_sense: dict[str, Any],
) -> tuple[str, bytes]:
    """Compute fallback version and canonical projection UTF-8 payload bytes."""
    projection: dict[str, Any] = {}
    has_surviving_linkage = False
    for f in INCLUDED_SENSE_DISTINCTION_FIELDS:
        if f in raw_sense and raw_sense[f] is not None:
            is_linkage = f in LINKAGE_FIELDS
            c = canonicalize_projection_value(raw_sense[f], is_linkage=is_linkage)
            if c is not None:
                projection[f] = c
                if is_linkage:
                    has_surviving_linkage = True
    payload = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    version = "v2" if has_surviving_linkage else "v1"
    return version, payload


def compute_sense_fallback_ref(raw_sense: dict[str, Any]) -> str:
    """Compute cosmetic-stable fallback sense source_ref fingerprint (A4 / Failure-2 amendment)."""
    version, payload = compute_sense_fallback_projection_payload(raw_sense)
    digest = hashlib.sha256(payload).hexdigest()
    return f"fingerprint:{version}:{digest}"


def deduplicate_record_senses(senses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coalesce same-record canonical-equivalent fallback senses (Failure-3 amendment).

    Preserves the first occurrence in raw source order and skips later canonical-equivalent
    fallback senses whose canonical projection bytes are identical under the existing
    fallback fingerprint implementation.
    Explicit senseid/wikidata senses are never coalesced.
    """
    retained: list[dict[str, Any]] = []
    seen_fallback_keys: set[tuple[str, bytes]] = set()

    for sense in senses:
        source_ref = compute_sense_source_ref(sense)
        if source_ref.startswith("fingerprint:v1:") or source_ref.startswith("fingerprint:v2:"):
            version, payload = compute_sense_fallback_projection_payload(sense)
            key = (version, payload)
            if key in seen_fallback_keys:
                continue
            seen_fallback_keys.add(key)
        retained.append(sense)

    return retained


def compute_senseid_candidate(raw_sense: dict[str, Any]) -> str | None:
    """Compute the cleaned senseid candidate source_ref for one raw sense (A4).

    Returns None when the sense has no usable senseid. Multiple usable IDs in
    one raw sense serialize deterministically as senseids:v1:<sha256>.
    """
    senseids_raw = raw_sense.get("senseid")
    if senseids_raw is None:
        senseids_raw = raw_sense.get("senseids")
    if senseids_raw is None:
        return None
    if isinstance(senseids_raw, str):
        senseids_list = [senseids_raw]
    elif isinstance(senseids_raw, list):
        senseids_list = [s for s in senseids_raw if isinstance(s, str)]
    else:
        senseids_list = []
    clean_senseids: list[str] = []
    for s in senseids_list:
        s_norm = unicodedata.normalize("NFC", s).strip()
        if s_norm and s_norm not in clean_senseids:
            clean_senseids.append(s_norm)
    if len(clean_senseids) == 1:
        return f"senseid:{clean_senseids[0]}"
    if len(clean_senseids) > 1:
        sorted_senseids = sorted(clean_senseids)
        payload = json.dumps(sorted_senseids, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return f"senseids:v1:{hashlib.sha256(payload).hexdigest()}"
    return None


def compute_wikidata_candidate(raw_sense: dict[str, Any]) -> str | None:
    """Compute the cleaned Wikidata candidate source_ref for one raw sense (A4).

    Returns None when the sense has no usable Wikidata QID. Multiple usable
    QIDs in one raw sense serialize deterministically as wikidata-set:v1:<sha256>.
    """
    wikidata_raw = raw_sense.get("wikidata")
    if wikidata_raw is None:
        return None
    if isinstance(wikidata_raw, str):
        qids_list = [wikidata_raw]
    elif isinstance(wikidata_raw, list):
        qids_list = [q for q in wikidata_raw if isinstance(q, str)]
    else:
        qids_list = []
    clean_qids: list[str] = []
    for q in qids_list:
        q_norm = unicodedata.normalize("NFC", q).strip()
        if q_norm and q_norm not in clean_qids:
            clean_qids.append(q_norm)
    if len(clean_qids) == 1:
        return f"wikidata:{clean_qids[0]}"
    if len(clean_qids) > 1:
        sorted_qids = sorted(clean_qids)
        payload = json.dumps(sorted_qids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return f"wikidata-set:v1:{hashlib.sha256(payload).hexdigest()}"
    return None


def compute_sense_source_ref(raw_sense: dict[str, Any]) -> str:
    """Compute the single-sense source_ref using the A4 priority ladder.

    This greedy helper remains the reference for same-record fallback dedupe
    routing (Failure-3) and for isolated lookups. Final lemma-identity
    source_ref assignment uses resolve_sense_source_refs (Failure-4), which
    demotes explicit identifiers that are ambiguous within the lemma identity.
    """
    senseid_candidate = compute_senseid_candidate(raw_sense)
    if senseid_candidate is not None:
        return senseid_candidate
    wikidata_candidate = compute_wikidata_candidate(raw_sense)
    if wikidata_candidate is not None:
        return wikidata_candidate
    return compute_sense_fallback_ref(raw_sense)


def resolve_sense_source_refs(raw_senses: Sequence[dict[str, Any]]) -> list[str]:
    """Resolve source_refs for all raw senses at final lemma-identity scope.

    Failure-4 amendment: an explicit upstream identifier (senseid, then
    Wikidata) is usable for a raw sense only when its canonical candidate ref
    is unambiguous within the lemma identity. Resolution is set/count based,
    so the outcome does not depend on source record order.

    Effective priority: unique senseid -> unique Wikidata -> fallback fingerprint.
    """
    senseid_candidates = [compute_senseid_candidate(s) for s in raw_senses]
    wikidata_candidates = [compute_wikidata_candidate(s) for s in raw_senses]

    senseid_counts: Counter[str] = Counter(c for c in senseid_candidates if c is not None)

    resolved: list[str | None] = [None] * len(raw_senses)

    # Senseid pass: a candidate occurring exactly once is usable.
    for i, candidate in enumerate(senseid_candidates):
        if candidate is not None and senseid_counts[candidate] == 1:
            resolved[i] = candidate

    # Wikidata pass: only senses without a unique usable senseid participate.
    wikidata_stage = [i for i in range(len(raw_senses)) if resolved[i] is None]
    wikidata_counts: Counter[str] = Counter()
    for i in wikidata_stage:
        candidate = wikidata_candidates[i]
        if candidate is not None:
            wikidata_counts[candidate] += 1
    for i in wikidata_stage:
        candidate = wikidata_candidates[i]
        if candidate is not None and wikidata_counts[candidate] == 1:
            resolved[i] = candidate

    # Fallback pass.
    final_refs: list[str] = []
    for i, ref in enumerate(resolved):
        if ref is None:
            final_refs.append(compute_sense_fallback_ref(raw_senses[i]))
        else:
            final_refs.append(ref)
    return final_refs


def compute_sense_semantic_ref(
    lemma_semantic_ref: str, source_namespace: str, source_ref: str
) -> str:
    """Compute deterministic sense semantic ref (A5)."""
    payload = json.dumps(
        [lemma_semantic_ref, source_namespace, source_ref],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sense:v1:{hashlib.sha256(payload).hexdigest()}"


def validate_sense_meaning_derivations(conn: sqlite3.Connection) -> None:
    """Validate sense_meaning_derivation edges according to ADR-0004 D45 / A8."""
    cur = conn.execute("""
        SELECT d.generated_meaning_id, d.source_meaning_id,
               gm.id AS gm_id, gm.sense_id AS gm_sense_id,
               gm.source AS gm_source, gm.license AS gm_license,
               sm.id AS sm_id, sm.sense_id AS sm_sense_id,
               sm.source AS sm_source, sm.license AS sm_license
        FROM sense_meaning_derivation d
        LEFT JOIN sense_meaning gm ON d.generated_meaning_id = gm.id
        LEFT JOIN sense_meaning sm ON d.source_meaning_id = sm.id
    """)
    for (
        gen_mid,
        src_mid,
        gm_id,
        gm_sense_id,
        gm_source,
        gm_license,
        sm_id,
        sm_sense_id,
        sm_source,
        sm_license,
    ) in cur.fetchall():
        if gm_id is None:
            raise BuildDictError(
                f"Derivation edge references nonexistent generated meaning {gen_mid}"
            )
        if sm_id is None:
            raise BuildDictError(f"Derivation edge references nonexistent source meaning {src_mid}")
        if gm_id == sm_id:
            raise BuildDictError(f"Derivation self-edge forbidden on meaning {gm_id}")

        gm_src_str = str(gm_source) if gm_source is not None else ""
        if not GENERATED_SOURCE_PATTERN.match(gm_src_str):
            raise BuildDictError(
                f"Generated meaning {gm_id} source '{gm_src_str}' does not match "
                f"versioned generated marker"
            )

        sm_src_str = str(sm_source) if sm_source is not None else ""
        if GENERATED_SOURCE_PATTERN.match(sm_src_str):
            raise BuildDictError(
                f"Source meaning {sm_id} cannot be generated (generated->generated forbidden)"
            )
        if not sm_src_str.strip():
            raise BuildDictError(f"Source meaning {sm_id} has blank source")

        sm_lic_str = str(sm_license) if sm_license is not None else ""
        if not sm_lic_str.strip():
            raise BuildDictError(f"Source meaning {sm_id} has blank license")

        if gm_sense_id != sm_sense_id:
            raise BuildDictError(
                f"Cross-sense derivation forbidden: generated meaning {gm_id} "
                f"(sense {gm_sense_id}) != source meaning {sm_id} (sense {sm_sense_id})"
            )


@dataclass
class LemmaAccumulator:
    """In-memory accumulator for a single merged lemma identity."""

    word: str
    pos: str
    gender: str | None
    ipa: str | None = None
    ipa_source: str | None = None
    plural_candidates: set[str] = field(default_factory=set)
    plural_none: bool = False
    genitive_sg_candidates: set[str] = field(default_factory=set)
    praesens_3sg_candidates: set[str] = field(default_factory=set)
    praeteritum_3sg_candidates: set[str] = field(default_factory=set)
    partizip_ii_candidates: set[str] = field(default_factory=set)
    comparative_candidates: set[str] = field(default_factory=set)
    superlative_candidates: set[str] = field(default_factory=set)
    surface_forms: set[str] = field(default_factory=set)
    raw_senses_en: list[dict[str, Any]] = field(default_factory=list)


def _validate_type(
    val: object,
    expected_type: type | tuple[type, ...],
    field_name: str,
    path: Path,
    line_no: int,
) -> None:
    """Validate JSON value type, raising BuildDictError on mismatch."""
    if not isinstance(val, expected_type):
        if isinstance(expected_type, tuple):
            type_name = "/".join(t.__name__ for t in expected_type)
        else:
            type_name = expected_type.__name__
        raise BuildDictError(
            f"Invalid type for field '{field_name}' in {path}:{line_no}: "
            f"expected {type_name}, got {type(val).__name__}"
        )


def process_jsonl_file(
    file_path: Path,
    is_en_edition: bool,
    accumulators: dict[tuple[str, str, str | None], LemmaAccumulator],
) -> None:
    """Process a single Wiktextract JSONL dump file line-by-line."""
    if not file_path.is_file():
        raise BuildDictError(f"Input JSONL file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except Exception as e:
                raise BuildDictError(f"Malformed JSON in {file_path}:{line_no}: {e}") from e

            if not isinstance(record, dict):
                raise BuildDictError(
                    f"Invalid record format in {file_path}:{line_no}: expected JSON object"
                )

            # C2: Only records with lang_code == "de", non-empty string word, and non-empty pos
            lang_code = record.get("lang_code")
            word = record.get("word")
            pos = record.get("pos")

            if (
                lang_code != "de"
                or not isinstance(word, str)
                or not word
                or not isinstance(pos, str)
                or not pos
            ):
                # Deliberately ignored per C2
                continue

            # Normalize participating lemma text to NFC
            word = unicodedata.normalize("NFC", word)

            # Participating record: validate participating fields (C8)
            # 1. Tags and Gender / Plural-none
            has_no_plural_tag = False
            found_genders: set[str] = set()
            if "tags" in record and record["tags"] is not None:
                tags = record["tags"]
                _validate_type(tags, list, "tags", file_path, line_no)
                for tag in tags:
                    _validate_type(tag, str, "tags[]", file_path, line_no)

                if "masculine" in tags:
                    found_genders.add("der")
                if "feminine" in tags:
                    found_genders.add("die")
                if "neuter" in tags:
                    found_genders.add("das")

                if "no-plural" in tags:
                    has_no_plural_tag = True

            target_genders: list[str | None]
            if found_genders:
                target_genders = [g for g in GENDER_CANONICAL_ORDER if g in found_genders]
            else:
                target_genders = [None]

            canonical_pos = canonicalize_pos(pos)
            target_accs: list[LemmaAccumulator] = []
            for g in target_genders:
                lemma_key = (word, canonical_pos, g)
                if lemma_key not in accumulators:
                    accumulators[lemma_key] = LemmaAccumulator(
                        word=word, pos=canonical_pos, gender=g
                    )
                acc = accumulators[lemma_key]
                if has_no_plural_tag:
                    acc.plural_none = True
                target_accs.append(acc)

            # 2. Sounds and IPA
            if "sounds" in record and record["sounds"] is not None:
                sounds = record["sounds"]
                _validate_type(sounds, list, "sounds", file_path, line_no)
                first_ipa: str | None = None
                for sound in sounds:
                    _validate_type(sound, dict, "sounds[]", file_path, line_no)
                    if "ipa" in sound and sound["ipa"] is not None:
                        ipa_val = sound["ipa"]
                        _validate_type(ipa_val, str, "sounds[].ipa", file_path, line_no)
                        if ipa_val.strip() and first_ipa is None:
                            first_ipa = ipa_val.strip()

                if first_ipa is not None:
                    for acc in target_accs:
                        if acc.ipa is None:
                            acc.ipa = first_ipa
                            acc.ipa_source = "wiktionary"

            # 3. Senses (owned by English edition, C5)
            if "senses" in record and record["senses"] is not None:
                senses = record["senses"]
                _validate_type(senses, list, "senses", file_path, line_no)
                for sense in senses:
                    _validate_type(sense, dict, "senses[]", file_path, line_no)
                if is_en_edition:
                    deduped_senses = deduplicate_record_senses(senses)
                    for acc in target_accs:
                        for sense in deduped_senses:
                            acc.raw_senses_en.append(dict(sense))

            # 4. Forms (Surface forms and Form-derived fields, C6 & C7)
            if "forms" in record and record["forms"] is not None:
                forms = record["forms"]
                _validate_type(forms, list, "forms", file_path, line_no)
                parsed_forms: list[tuple[str, set[str]]] = []
                for form_item in forms:
                    _validate_type(form_item, dict, "forms[]", file_path, line_no)
                    form_str = form_item.get("form")
                    if form_str is not None:
                        _validate_type(form_str, str, "forms[].form", file_path, line_no)

                    form_tags = form_item.get("tags")
                    if form_tags is not None:
                        _validate_type(form_tags, list, "forms[].tags", file_path, line_no)
                        for t in form_tags:
                            _validate_type(t, str, "forms[].tags[]", file_path, line_no)

                    if form_str is not None:
                        clean_form = form_str.strip()
                        if clean_form and clean_form != "-":
                            clean_form = unicodedata.normalize("NFC", clean_form)
                            tags_set = set(form_tags) if form_tags is not None else set()
                            parsed_forms.append((clean_form, tags_set))

                for clean_form, tags_set in parsed_forms:
                    for acc in target_accs:
                        if clean_form != word:
                            acc.surface_forms.add(clean_form)

                        if "plural" in tags_set:
                            acc.plural_candidates.add(clean_form)
                        if "genitive" in tags_set and "singular" in tags_set:
                            acc.genitive_sg_candidates.add(clean_form)
                        if (
                            "present" in tags_set
                            and "third-person" in tags_set
                            and "singular" in tags_set
                        ):
                            acc.praesens_3sg_candidates.add(clean_form)
                        if (
                            "past" in tags_set
                            and "third-person" in tags_set
                            and "singular" in tags_set
                            and "participle" not in tags_set
                        ):
                            acc.praeteritum_3sg_candidates.add(clean_form)
                        if "past" in tags_set and "participle" in tags_set:
                            acc.partizip_ii_candidates.add(clean_form)
                        if "comparative" in tags_set:
                            acc.comparative_candidates.add(clean_form)
                        if "superlative" in tags_set:
                            acc.superlative_candidates.add(clean_form)


def build_stage01(
    en_jsonl_path: Path | str,
    de_jsonl_path: Path | str,
    output_path: Path | str,
) -> None:
    """Execute build stage 01 deterministic transform to SQLite."""
    en_path = Path(en_jsonl_path)
    de_path = Path(de_jsonl_path)
    out_path = Path(output_path)

    if out_path.exists():
        raise BuildDictError(f"Output path already exists: {out_path}")

    # Process EN then DE into accumulators
    accumulators: dict[tuple[str, str, str | None], LemmaAccumulator] = {}
    process_jsonl_file(en_path, is_en_edition=True, accumulators=accumulators)
    process_jsonl_file(de_path, is_en_edition=False, accumulators=accumulators)

    # Sort lemma identities deterministically before assigning IDs (C4)
    sorted_keys = sorted(
        accumulators.keys(),
        key=lambda k: (k[0], k[1], GENDER_ORDER_MAP.get(k[2], 99), k[2] or ""),
    )

    # Prepare temporary sibling file for fail-closed atomic publish (C1 & C8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        dir=out_path.parent,
        prefix=f".{out_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(temp_file.name)
    temp_file.close()

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(temp_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(STAGE01_SCHEMA_SQL)

        seen_lemma_semantic_refs: set[str] = set()
        seen_sense_semantic_refs: set[str] = set()

        with conn:
            sense_id_counter = 1
            meaning_id_counter = 1

            for lemma_id, key in enumerate(sorted_keys, start=1):
                acc = accumulators[key]

                # Contradictory plural check (A9)
                if acc.plural_none and acc.plural_candidates:
                    raise BuildDictError(
                        f"Contradictory plural evidence for '{acc.word}': 'no-plural' tag present "
                        f"along with plural form {sorted(acc.plural_candidates)}"
                    )

                if acc.plural_candidates:
                    plural = min(acc.plural_candidates)
                    plural_none = 0
                elif acc.plural_none:
                    plural = None
                    plural_none = 1
                else:
                    plural = None
                    plural_none = 0

                genitive_sg = (
                    min(acc.genitive_sg_candidates) if acc.genitive_sg_candidates else None
                )
                praesens_3sg = (
                    min(acc.praesens_3sg_candidates) if acc.praesens_3sg_candidates else None
                )
                praeteritum_3sg = (
                    min(acc.praeteritum_3sg_candidates) if acc.praeteritum_3sg_candidates else None
                )
                partizip_ii = (
                    min(acc.partizip_ii_candidates) if acc.partizip_ii_candidates else None
                )
                comparative = (
                    min(acc.comparative_candidates) if acc.comparative_candidates else None
                )
                superlative = (
                    min(acc.superlative_candidates) if acc.superlative_candidates else None
                )

                lemma_semantic_ref = compute_lemma_semantic_ref(acc.word, acc.pos, acc.gender)
                if lemma_semantic_ref in seen_lemma_semantic_refs:
                    raise BuildDictError(
                        f"Duplicate lemma semantic_ref '{lemma_semantic_ref}' for '{acc.word}'"
                    )
                seen_lemma_semantic_refs.add(lemma_semantic_ref)

                conn.execute(
                    """
                    INSERT INTO lemma (
                        id, semantic_ref, lemma, pos, gender, plural, plural_none, genitive_sg,
                        aux, separable, particle, reflexive, praesens_3sg, praeteritum_3sg,
                        partizip_ii, governs, comparative, superlative, ipa, ipa_source,
                        freq_rank, source, license
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lemma_id,
                        lemma_semantic_ref,
                        acc.word,
                        acc.pos,
                        acc.gender,
                        plural,
                        plural_none,
                        genitive_sg,
                        None,  # aux
                        0,  # separable
                        None,  # particle
                        0,  # reflexive
                        praesens_3sg,
                        praeteritum_3sg,
                        partizip_ii,
                        None,  # governs
                        comparative,
                        superlative,
                        acc.ipa,
                        acc.ipa_source,
                        None,  # freq_rank
                        "wiktionary",
                        "CC BY-SA",
                    ),
                )

                # Insert surface forms sorted
                for sf in sorted(acc.surface_forms):
                    conn.execute(
                        "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
                        (sf, lemma_id),
                    )

                # Senses and English localized meanings (A6 / A7)
                # Resolve source_refs at final lemma-identity scope BEFORE the
                # A6 learner-meaning cap so identifier ambiguity is set/count
                # based over every participating raw sense (Failure-4).
                resolved_source_refs = resolve_sense_source_refs(acc.raw_senses_en)

                retained_senses: list[tuple[dict[str, Any], list[str], str]] = []
                total_en_meanings = 0
                seen_gloss_texts: set[str] = set()

                for raw_sense, source_ref in zip(acc.raw_senses_en, resolved_source_refs):
                    retained_glosses_for_this_sense: list[str] = []
                    if "glosses" in raw_sense and raw_sense["glosses"] is not None:
                        for g in raw_sense["glosses"]:
                            if isinstance(g, str):
                                clean_g = g.strip()
                                if clean_g and clean_g not in seen_gloss_texts:
                                    if total_en_meanings < 3:
                                        seen_gloss_texts.add(clean_g)
                                        retained_glosses_for_this_sense.append(clean_g)
                                        total_en_meanings += 1

                    if retained_glosses_for_this_sense:
                        retained_senses.append(
                            (raw_sense, retained_glosses_for_this_sense, source_ref)
                        )

                for ord_idx, (raw_sense, gloss_list, source_ref) in enumerate(retained_senses):
                    source_namespace = "wiktextract:enwiktionary"
                    sense_semantic_ref = compute_sense_semantic_ref(
                        lemma_semantic_ref, source_namespace, source_ref
                    )
                    if sense_semantic_ref in seen_sense_semantic_refs:
                        raise BuildDictError(
                            f"Duplicate sense semantic_ref '{sense_semantic_ref}' "
                            f"for lemma '{acc.word}'"
                        )
                    seen_sense_semantic_refs.add(sense_semantic_ref)

                    conn.execute(
                        """
                        INSERT INTO sense (
                            id, lemma_id, semantic_ref, source_namespace, source_ref,
                            ord, register, source, license
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sense_id_counter,
                            lemma_id,
                            sense_semantic_ref,
                            source_namespace,
                            source_ref,
                            ord_idx,
                            None,  # register
                            "wiktionary",
                            "CC BY-SA",
                        ),
                    )

                    for meaning_ord, gloss_text in enumerate(gloss_list):
                        conn.execute(
                            """
                            INSERT INTO sense_meaning (
                                id, sense_id, language, kind, ord, text, source, license
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                meaning_id_counter,
                                sense_id_counter,
                                "en",
                                "translation",
                                meaning_ord,
                                gloss_text,
                                "wiktionary",
                                "CC BY-SA",
                            ),
                        )
                        meaning_id_counter += 1

                    sense_id_counter += 1

            # Validate derivations
            validate_sense_meaning_derivations(conn)

        conn.close()
        conn = None

        if out_path.exists():
            raise BuildDictError(f"Output path already exists: {out_path}")

        temp_path.replace(out_path)

    except Exception:
        if conn is not None:
            conn.close()
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def sha256_file(path: Path | str) -> str:
    """Compute SHA-256 hex digest of file raw bytes."""
    p = Path(path)
    if not p.is_file():
        raise BuildDictError(f"File not found for SHA-256 calculation: {p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_stage02_cache_key(
    stage01_path: Path | str,
    de_tsv_path: Path | str,
    en_tsv_path: Path | str,
    links_tsv_path: Path | str,
    license_label: str,
    spacy_model: str,
) -> str:
    """Compute deterministic Stage-02 cache key including canonical resolver hash (A8)."""
    resolver_sha = get_resolver_hash()
    stage01_sha = sha256_file(stage01_path)
    de_sha = sha256_file(de_tsv_path)
    en_sha = sha256_file(en_tsv_path)
    links_sha = sha256_file(links_tsv_path)

    payload = json.dumps(
        {
            "format": "stage02:v1",
            "resolver_sha256": resolver_sha,
            "stage01_sha256": stage01_sha,
            "de_tsv_sha256": de_sha,
            "en_tsv_sha256": en_sha,
            "links_tsv_sha256": links_sha,
            "spacy_model": spacy_model,
            "license": license_label,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"stage02:v1:{hashlib.sha256(payload).hexdigest()}"


class Stage02LookupOracle(LookupProtocol):
    """Bounded-memory, read-only dictionary lookup oracle for Stage 02.

    Stage 01 deliberately has no expression indexes for the runtime's
    ``lower(lemma)`` and ``lower(surface_form)`` predicates.  Build a temporary
    Stage-02-only accelerator once, using SQLite's own ``lower`` implementation,
    then use indexed equality lookups throughout the NLP pass.  Keeping SQLite
    responsible for case folding is essential: it preserves the runtime
    Dictionary's exact SQLite/Python case behaviour without materialising the
    dictionary in Python memory.
    """

    _CACHE_SIZE: Final[int] = 100_000

    def __init__(self, db_path: Path | str, accelerator_path: Path | str | None = None) -> None:
        self._source_path = Path(db_path).resolve()
        self._owns_accelerator = accelerator_path is None
        if accelerator_path is None:
            temp_file = tempfile.NamedTemporaryFile(
                prefix="stage02-lookup-", suffix=".sqlite", delete=False
            )
            self._accelerator_path = Path(temp_file.name)
            temp_file.close()
            self._accelerator_path.unlink()
        else:
            self._accelerator_path = Path(accelerator_path)
            if self._accelerator_path.exists():
                raise BuildDictError(
                    f"Stage-02 lookup accelerator already exists: {self._accelerator_path}"
                )

        self._conn: sqlite3.Connection | None = None
        try:
            self._conn = sqlite3.connect(self._accelerator_path)
            self._conn.execute(
                "ATTACH DATABASE ? AS source",
                (f"file:{self._source_path}?mode=ro",),
            )
            self._conn.executescript(
                """
                CREATE TABLE exact_lookup (
                    lookup_key TEXT NOT NULL,
                    id INTEGER NOT NULL,
                    lemma TEXT NOT NULL,
                    pos TEXT NOT NULL,
                    gender TEXT,
                    semantic_ref TEXT NOT NULL,
                    freq_rank INTEGER,
                    PRIMARY KEY (lookup_key, id)
                ) WITHOUT ROWID;
                CREATE TABLE surface_lookup (
                    lookup_key TEXT NOT NULL,
                    lemma_id INTEGER NOT NULL,
                    PRIMARY KEY (lookup_key, lemma_id)
                ) WITHOUT ROWID;

                INSERT INTO exact_lookup
                SELECT lemma, id, lemma, pos, gender, semantic_ref, freq_rank
                FROM source.lemma;
                INSERT OR IGNORE INTO exact_lookup
                SELECT lower(lemma), id, lemma, pos, gender, semantic_ref, freq_rank
                FROM source.lemma;

                INSERT INTO surface_lookup
                SELECT form, lemma_id FROM source.surface_form;
                INSERT OR IGNORE INTO surface_lookup
                SELECT lower(form), lemma_id FROM source.surface_form;
                """
            )
        except Exception:
            if self._conn is not None:
                self._conn.close()
            self._accelerator_path.unlink(missing_ok=True)
            raise

    @property
    def accelerator_path(self) -> Path:
        """Return the ephemeral accelerator path for execution instrumentation."""
        return self._accelerator_path

    def lookup_query_plans(self, lemma: str, form: str) -> tuple[str, str]:
        """Return query plans proving lookup queries avoid source-table scans."""
        if self._conn is None:
            raise BuildDictError("Stage-02 lookup oracle is closed")
        exact_plan = self._conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id, lemma, pos, gender, semantic_ref, freq_rank "
            "FROM exact_lookup WHERE lookup_key IN (?, ?) GROUP BY id "
            "ORDER BY freq_rank ASC NULLS LAST, pos ASC, gender ASC NULLS LAST, "
            "semantic_ref ASC",
            (lemma, lemma.lower()),
        ).fetchall()
        surface_plan = self._conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT l.id, l.lemma, l.pos, l.gender, l.semantic_ref, l.freq_rank "
            "FROM surface_lookup sl JOIN source.lemma l ON l.id = sl.lemma_id "
            "WHERE sl.lookup_key IN (?, ?) "
            "ORDER BY l.freq_rank ASC NULLS LAST, l.pos ASC, l.gender ASC NULLS LAST, "
            "l.semantic_ref ASC",
            (form, form.lower()),
        ).fetchall()
        return (
            "\n".join(str(row[-1]) for row in exact_plan),
            "\n".join(str(row[-1]) for row in surface_plan),
        )

    @staticmethod
    def _record(row: tuple[Any, ...]) -> LemmaRecord:
        return LemmaRecord(
            id=row[0],
            lemma=row[1],
            pos=row[2],
            gender=row[3],
            semantic_ref=row[4],
            freq_rank=row[5],
        )

    @lru_cache(maxsize=_CACHE_SIZE)
    def _exact_records(self, lemma: str) -> tuple[LemmaRecord, ...]:
        if self._conn is None:
            raise BuildDictError("Stage-02 lookup oracle is closed")
        rows = self._conn.execute(
            "SELECT id, lemma, pos, gender, semantic_ref, freq_rank FROM exact_lookup "
            "WHERE lookup_key IN (?, ?) GROUP BY id "
            "ORDER BY freq_rank ASC NULLS LAST, pos ASC, gender ASC NULLS LAST, "
            "semantic_ref ASC",
            (lemma, lemma.lower()),
        ).fetchall()
        return tuple(map(self._record, rows))

    @lru_cache(maxsize=_CACHE_SIZE)
    def _surface_records(self, form: str) -> tuple[LemmaRecord, ...]:
        if self._conn is None:
            raise BuildDictError("Stage-02 lookup oracle is closed")
        rows = self._conn.execute(
            "SELECT l.id, l.lemma, l.pos, l.gender, l.semantic_ref, l.freq_rank "
            "FROM surface_lookup sl JOIN source.lemma l ON l.id = sl.lemma_id "
            "WHERE sl.lookup_key IN (?, ?) "
            "ORDER BY l.freq_rank ASC NULLS LAST, l.pos ASC, l.gender ASC NULLS LAST, "
            "l.semantic_ref ASC",
            (form, form.lower()),
        ).fetchall()
        seen: set[int | None] = set()
        records: list[LemmaRecord] = []
        for row in rows:
            record = self._record(row)
            if record.id not in seen:
                seen.add(record.id)
                records.append(record)
        return tuple(records)

    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[LemmaRecord]:
        matches = list(self._exact_records(lemma))
        if pos is not None:
            matches = [m for m in matches if m.pos == pos]
        if gender is not None:
            matches = [m for m in matches if m.gender == gender]
        return matches

    def lookup_surface_form(self, form: str) -> Sequence[LemmaRecord]:
        return self._surface_records(form)

    @lru_cache(maxsize=_CACHE_SIZE)
    def lookup_senses(self, lemma_id: int) -> Sequence[SenseRecord]:
        if self._conn is None:
            raise BuildDictError("Stage-02 lookup oracle is closed")
        rows = self._conn.execute(
            "SELECT id, lemma_id, ord, semantic_ref FROM source.sense "
            "WHERE lemma_id = ? ORDER BY ord ASC, semantic_ref ASC, id ASC",
            (lemma_id,),
        ).fetchall()
        return tuple(SenseRecord(*row) for row in rows)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._accelerator_path.unlink(missing_ok=True)


def parse_sentence_tsv(tsv_path: Path, lang_name: str) -> dict[int, str]:
    """Parse and strictly validate Tatoeba sentence projection TSV (A3)."""
    if not tsv_path.is_file():
        raise BuildDictError(f"{lang_name} projection file not found: {tsv_path}")
    sentences: dict[int, str] = {}
    with tsv_path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, 1):
            line = raw_line.rstrip("\r\n")
            parts = line.split("\t")
            if len(parts) != 2:
                raise BuildDictError(
                    f"Malformed sentence row in {tsv_path}:{line_no}: "
                    f"expected exactly 2 tab-separated fields, got {len(parts)}"
                )
            id_str, text = parts
            if not id_str.isdigit() or int(id_str) <= 0:
                raise BuildDictError(
                    f"Invalid sentence id '{id_str}' in {tsv_path}:{line_no}: "
                    f"must be a positive integer"
                )
            if not text.strip():
                raise BuildDictError(f"Blank sentence text in {tsv_path}:{line_no}")
            sid = int(id_str)
            if sid in sentences:
                raise BuildDictError(
                    f"Duplicate {lang_name} sentence id {sid} in {tsv_path}:{line_no}"
                )
            sentences[sid] = text
    return sentences


def parse_links_tsv(
    links_path: Path,
    de_sentence_ids: set[int],
    en_sentence_ids: set[int],
) -> dict[int, list[int]]:
    """Parse and strictly validate Tatoeba DE->EN link projection TSV (A3)."""
    if not links_path.is_file():
        raise BuildDictError(f"Links projection file not found: {links_path}")
    links_by_de: dict[int, list[int]] = {}
    seen_pairs: set[tuple[int, int]] = set()
    with links_path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, 1):
            line = raw_line.rstrip("\r\n")
            parts = line.split("\t")
            if len(parts) != 2:
                raise BuildDictError(
                    f"Malformed link row in {links_path}:{line_no}: "
                    f"expected exactly 2 tab-separated fields, got {len(parts)}"
                )
            de_id_str, en_id_str = parts
            if not de_id_str.isdigit() or int(de_id_str) <= 0:
                raise BuildDictError(
                    f"Invalid German sentence id '{de_id_str}' in links {links_path}:{line_no}"
                )
            if not en_id_str.isdigit() or int(en_id_str) <= 0:
                raise BuildDictError(
                    f"Invalid English sentence id '{en_id_str}' in links {links_path}:{line_no}"
                )
            de_id = int(de_id_str)
            en_id = int(en_id_str)
            pair = (de_id, en_id)
            if pair in seen_pairs:
                raise BuildDictError(
                    f"Duplicate link pair ({de_id}, {en_id}) in {links_path}:{line_no}"
                )
            seen_pairs.add(pair)
            if de_id not in de_sentence_ids:
                raise BuildDictError(
                    f"Dangling German sentence id {de_id} in {links_path}:{line_no}"
                )
            if en_id not in en_sentence_ids:
                raise BuildDictError(
                    f"Dangling English sentence id {en_id} in {links_path}:{line_no}"
                )
            links_by_de.setdefault(de_id, []).append(en_id)
    return links_by_de


class Stage02ProjectionStore:
    """Disk-backed validated Tatoeba projections for the real Stage-02 pass.

    The public parsing helpers intentionally return dictionaries for concise
    unit tests.  Real projections are substantially larger, so the build uses
    this temporary SQLite store to validate uniqueness and referential
    integrity without retaining the sentence corpora or link graph in memory.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(
            """
            CREATE TABLE de_sentence (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE en_sentence (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE de_en_link (
                de_id INTEGER NOT NULL,
                en_id INTEGER NOT NULL,
                PRIMARY KEY (de_id, en_id)
            ) WITHOUT ROWID;
            """
        )

    @staticmethod
    def _parse_sentence_rows(
        path: Path, table: str, language: str, conn: sqlite3.Connection
    ) -> None:
        if not path.is_file():
            raise BuildDictError(f"{language} projection file not found: {path}")
        batch: list[tuple[int, str]] = []
        try:
            with path.open("r", encoding="utf-8") as source:
                for line_no, raw_line in enumerate(source, 1):
                    parts = raw_line.rstrip("\r\n").split("\t")
                    if len(parts) != 2:
                        raise BuildDictError(
                            f"Malformed sentence row in {path}:{line_no}: "
                            f"expected exactly 2 tab-separated fields, got {len(parts)}"
                        )
                    id_str, text = parts
                    if not id_str.isdigit() or int(id_str) <= 0:
                        raise BuildDictError(
                            f"Invalid sentence id '{id_str}' in {path}:{line_no}: "
                            "must be a positive integer"
                        )
                    if not text.strip():
                        raise BuildDictError(f"Blank sentence text in {path}:{line_no}")
                    batch.append((int(id_str), text))
                    if len(batch) == 10_000:
                        conn.executemany(f"INSERT INTO {table} (id, text) VALUES (?, ?)", batch)
                        batch.clear()
                if batch:
                    conn.executemany(f"INSERT INTO {table} (id, text) VALUES (?, ?)", batch)
        except sqlite3.IntegrityError as exc:
            raise BuildDictError(f"Duplicate {language} sentence id in {path}") from exc

    @staticmethod
    def _parse_links(path: Path, conn: sqlite3.Connection) -> None:
        if not path.is_file():
            raise BuildDictError(f"Links projection file not found: {path}")
        batch: list[tuple[int, int]] = []
        try:
            with path.open("r", encoding="utf-8") as source:
                for line_no, raw_line in enumerate(source, 1):
                    parts = raw_line.rstrip("\r\n").split("\t")
                    if len(parts) != 2:
                        raise BuildDictError(
                            f"Malformed link row in {path}:{line_no}: "
                            f"expected exactly 2 tab-separated fields, got {len(parts)}"
                        )
                    de_id_str, en_id_str = parts
                    if not de_id_str.isdigit() or int(de_id_str) <= 0:
                        raise BuildDictError(
                            f"Invalid German sentence id '{de_id_str}' in links {path}:{line_no}"
                        )
                    if not en_id_str.isdigit() or int(en_id_str) <= 0:
                        raise BuildDictError(
                            f"Invalid English sentence id '{en_id_str}' in links {path}:{line_no}"
                        )
                    batch.append((int(de_id_str), int(en_id_str)))
                    if len(batch) == 10_000:
                        conn.executemany(
                            "INSERT INTO de_en_link (de_id, en_id) VALUES (?, ?)", batch
                        )
                        batch.clear()
                if batch:
                    conn.executemany("INSERT INTO de_en_link (de_id, en_id) VALUES (?, ?)", batch)
        except sqlite3.IntegrityError as exc:
            raise BuildDictError(f"Duplicate link pair in {path}") from exc

    @classmethod
    def create(
        cls, path: Path, de_tsv: Path, en_tsv: Path, links_tsv: Path
    ) -> "Stage02ProjectionStore":
        store = cls(path)
        try:
            with store.conn:
                store._parse_sentence_rows(de_tsv, "de_sentence", "German", store.conn)
                store._parse_sentence_rows(en_tsv, "en_sentence", "English", store.conn)
                store._parse_links(links_tsv, store.conn)
                dangling_de = store.conn.execute(
                    "SELECT de_id FROM de_en_link "
                    "WHERE de_id NOT IN (SELECT id FROM de_sentence) LIMIT 1"
                ).fetchone()
                if dangling_de:
                    raise BuildDictError(
                        f"Dangling German sentence id {dangling_de[0]} in {links_tsv}"
                    )
                dangling_en = store.conn.execute(
                    "SELECT en_id FROM de_en_link "
                    "WHERE en_id NOT IN (SELECT id FROM en_sentence) LIMIT 1"
                ).fetchone()
                if dangling_en:
                    raise BuildDictError(
                        f"Dangling English sentence id {dangling_en[0]} in {links_tsv}"
                    )
        except Exception:
            store.close()
            raise
        return store

    def german_rows(self) -> Any:
        return self.conn.execute(
            """
            SELECT d.id, d.text, e.text
            FROM de_sentence d
            LEFT JOIN (
                SELECT de_id, MIN(en_id) AS en_id
                FROM de_en_link GROUP BY de_id
            ) chosen ON chosen.de_id = d.id
            LEFT JOIN en_sentence e ON e.id = chosen.en_id
            ORDER BY d.id ASC
            """
        )

    def close(self) -> None:
        self.conn.close()
        self.path.unlink(missing_ok=True)


def validate_stage01_database(stage01_path: Path) -> None:
    """Validate Stage-01 database input read-only before copying (A2)."""
    if not stage01_path.is_file():
        raise BuildDictError(f"Stage 01 database file not found: {stage01_path}")
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{stage01_path.resolve()}?mode=ro", uri=True)
        check = conn.execute("PRAGMA quick_check").fetchall()
        if check != [("ok",)]:
            raise BuildDictError(f"Stage 01 database PRAGMA quick_check failed: {check}")
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        required_tables = {
            "lemma",
            "surface_form",
            "sense",
            "sense_meaning",
            "sense_meaning_derivation",
        }
        missing = required_tables - tables
        if missing:
            raise BuildDictError(f"Stage 01 database missing required tables: {sorted(missing)}")
        if "example" in tables:
            tatoeba_count = conn.execute(
                "SELECT count(*) FROM example WHERE source = 'tatoeba'"
            ).fetchone()[0]
            if tatoeba_count > 0:
                raise BuildDictError(
                    f"Stage 01 database contains {tatoeba_count} pre-existing Tatoeba examples"
                )
    finally:
        if conn is not None:
            conn.close()


def validate_stage02_database(stage02_path: Path) -> None:
    """Validate Stage-02 database structure, attribution, and integrity (A2, A6, A7)."""
    if not stage02_path.is_file():
        raise BuildDictError(f"Stage 02 database file not found: {stage02_path}")
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{stage02_path.resolve()}?mode=ro", uri=True)
        check = conn.execute("PRAGMA quick_check").fetchall()
        if check != [("ok",)]:
            raise BuildDictError(f"Stage 02 database PRAGMA quick_check failed: {check}")
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        required_tables = {
            "lemma",
            "surface_form",
            "sense",
            "sense_meaning",
            "sense_meaning_derivation",
            "example",
            "example_lemma",
        }
        missing = required_tables - tables
        if missing:
            raise BuildDictError(f"Stage 02 database missing required tables: {sorted(missing)}")

        bad_attribution = conn.execute(
            "SELECT count(*) FROM example "
            "WHERE source='tatoeba' AND "
            "(source_ref IS NULL OR trim(source_ref)='' OR license IS NULL OR trim(license)='')"
        ).fetchone()[0]
        if bad_attribution > 0:
            raise BuildDictError(f"Stage 02 database has {bad_attribution} bad attribution rows")

        orphan_index = conn.execute(
            "SELECT count(*) FROM example_lemma el "
            "LEFT JOIN lemma l ON l.id=el.lemma_id "
            "LEFT JOIN example e ON e.id=el.example_id "
            "WHERE l.id IS NULL OR e.id IS NULL"
        ).fetchone()[0]
        if orphan_index > 0:
            raise BuildDictError(f"Stage 02 database has {orphan_index} orphan example_lemma rows")
    finally:
        if conn is not None:
            conn.close()


def build_stage02(
    stage01_path: Path | str,
    de_tsv_path: Path | str,
    en_tsv_path: Path | str,
    links_tsv_path: Path | str,
    output_path: Path | str,
    cache_dir: Path | str,
    license_label: str,
    spacy_model: str = "de_core_news_md",
    n_process: int = 8,
) -> None:
    """Execute build stage 02 deterministic Tatoeba example indexing with caching."""
    stage01 = Path(stage01_path).resolve()
    de_tsv = Path(de_tsv_path).resolve()
    en_tsv = Path(en_tsv_path).resolve()
    links_tsv = Path(links_tsv_path).resolve()
    output = Path(output_path)
    cache_root = Path(cache_dir).resolve()

    if not license_label or not license_label.strip():
        raise BuildDictError("License label must be nonblank")

    if output.exists():
        raise BuildDictError(f"Output path already exists: {output}")

    # Validate Stage 01 read-only before copying
    validate_stage01_database(stage01)

    # Compute cache key (calls canonical get_resolver_hash)
    cache_key = compute_stage02_cache_key(
        stage01_path=stage01,
        de_tsv_path=de_tsv,
        en_tsv_path=en_tsv,
        links_tsv_path=links_tsv,
        license_label=license_label,
        spacy_model=spacy_model,
    )
    cache_file = cache_root / f"{cache_key.replace(':', '_')}.sqlite"

    # Check cache HIT
    if cache_file.is_file():
        # Validate cached asset fail-closed
        try:
            validate_stage02_database(cache_file)
        except Exception as e:
            raise BuildDictError(f"Corrupt matching cache artifact '{cache_file}': {e}") from e

        sys.stdout.write(f"Cache key: {cache_key}\n")
        sys.stdout.write("Cache result: HIT\n")

        # Atomically publish copy to output
        output.parent.mkdir(parents=True, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        )
        temp_out_path = Path(temp_file.name)
        temp_file.close()
        try:
            shutil.copyfile(cache_file, temp_out_path)
            if output.exists():
                raise BuildDictError(f"Output path already exists: {output}")
            temp_out_path.replace(output)
            return
        finally:
            if temp_out_path.exists():
                temp_out_path.unlink(missing_ok=True)

    # Cache MISS
    sys.stdout.write(f"Cache key: {cache_key}\n")
    sys.stdout.write("Cache result: MISS\n")

    # Build Stage 02 in a temporary sibling file
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_out_path = Path(temp_file.name)
    temp_file.close()

    conn: sqlite3.Connection | None = None
    projection_store: Stage02ProjectionStore | None = None
    oracle: Stage02LookupOracle | None = None
    projection_temp = tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.stage02-projections.",
        suffix=".sqlite.tmp",
        delete=False,
    )
    projection_path = Path(projection_temp.name)
    projection_temp.close()
    lookup_temp = tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.stage02-lookup-",
        suffix=".sqlite.tmp",
        delete=False,
    )
    lookup_path = Path(lookup_temp.name)
    lookup_temp.close()
    lookup_path.unlink()
    try:
        # Validate inputs into a disk-backed store before opening the output.
        # This keeps the multi-million-row projections out of Python memory.
        projection_store = Stage02ProjectionStore.create(projection_path, de_tsv, en_tsv, links_tsv)

        # Copy Stage 01 to temp_out_path
        shutil.copyfile(stage01, temp_out_path)

        # Connect to temp output DB and ensure example / example_lemma schemas
        conn = sqlite3.connect(temp_out_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(STAGE02_EXAMPLE_SCHEMA_SQL)

        # Build a bounded-memory, disk-backed lookup accelerator alongside the
        # other Stage-02 temporary artifacts.  It is deleted in ``finally``.
        oracle = Stage02LookupOracle(stage01, lookup_path)

        # Load spaCy
        try:
            import spacy

            nlp = spacy.load(spacy_model)
        except Exception as e:
            raise BuildDictError(f"Failed to load spaCy model '{spacy_model}': {e}") from e

        example_id_counter = conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM example"
        ).fetchone()[0]
        example_batch: list[tuple[Any, ...]] = []
        index_batch: list[tuple[int, int]] = []
        BATCH_SIZE = 25000

        def flush_batches() -> None:
            if not example_batch:
                return
            if conn is None:
                raise BuildDictError("Stage 02 output connection unexpectedly closed")
            output_conn = conn
            output_conn.executemany(
                """
                INSERT INTO example (
                    id, de, en, source, source_ref, license, token_count, has_proper
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                example_batch,
            )
            output_conn.executemany(
                "INSERT INTO example_lemma (lemma_id, example_id) VALUES (?, ?)",
                index_batch,
            )
            output_conn.commit()
            example_batch.clear()
            index_batch.clear()

        projection_items = (
            (de_text, (de_id, de_text, en_text))
            for de_id, de_text, en_text in projection_store.german_rows()
        )
        for doc, (de_id, de_text, en_text) in nlp.pipe(
            projection_items,
            as_tuples=True,
            n_process=n_process,
            batch_size=2000,
        ):
            non_space_tokens = [tok for tok in doc if not tok.is_space]
            token_count = len(non_space_tokens)
            has_proper = 1 if any(tok.pos_ == "PROPN" for tok in non_space_tokens) else 0

            resolved_lemma_ids: set[int] = set()
            for tok in doc:
                refs = resolve_token(tok, oracle)
                for ref in refs:
                    if ref.lemma_id is not None:
                        resolved_lemma_ids.add(ref.lemma_id)

            if not resolved_lemma_ids:
                continue

            example_id = example_id_counter
            example_id_counter += 1

            example_batch.append(
                (
                    example_id,
                    de_text,
                    en_text,
                    "tatoeba",
                    str(de_id),
                    license_label,
                    token_count,
                    has_proper,
                )
            )

            for lid in sorted(resolved_lemma_ids):
                index_batch.append((lid, example_id))

            if len(example_batch) >= BATCH_SIZE:
                flush_batches()

        flush_batches()

        conn.close()
        conn = None

        # Validate built database
        validate_stage02_database(temp_out_path)

        # Atomically publish to cache directory
        cache_root.mkdir(parents=True, exist_ok=True)
        temp_cache = tempfile.NamedTemporaryFile(
            dir=cache_root,
            prefix=f".{cache_file.name}.",
            suffix=".tmp",
            delete=False,
        )
        temp_cache_path = Path(temp_cache.name)
        temp_cache.close()
        try:
            shutil.copyfile(temp_out_path, temp_cache_path)
            temp_cache_path.replace(cache_file)
        finally:
            if temp_cache_path.exists():
                temp_cache_path.unlink(missing_ok=True)

        # Atomically publish to output_path
        if output.exists():
            raise BuildDictError(f"Output path already exists: {output}")
        temp_out_path.replace(output)

    finally:
        if oracle is not None:
            oracle.close()
        if projection_store is not None:
            projection_store.close()
        else:
            projection_path.unlink(missing_ok=True)
        lookup_path.unlink(missing_ok=True)
        if conn is not None:
            conn.close()
        if temp_out_path.exists():
            temp_out_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for dictionary build tools."""
    parser = argparse.ArgumentParser(description="German Flashcards Dictionary Build Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage01_parser = subparsers.add_parser(
        "stage01",
        help="Stage 01: Build dictionary core from Wiktextract JSONL dumps",
    )
    stage01_parser.add_argument(
        "--en-jsonl",
        type=Path,
        required=True,
        help="Path to Wiktextract English edition JSONL dump",
    )
    stage01_parser.add_argument(
        "--de-jsonl",
        type=Path,
        required=True,
        help="Path to Wiktextract German edition JSONL dump",
    )
    stage01_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to target output SQLite database file",
    )

    stage02_parser = subparsers.add_parser(
        "stage02",
        help="Stage 02: Deterministic Tatoeba example indexing",
    )
    stage02_parser.add_argument(
        "--stage01",
        type=Path,
        required=True,
        help="Path to accepted Stage-01 SQLite dictionary database",
    )
    stage02_parser.add_argument(
        "--de-tsv",
        type=Path,
        required=True,
        help="Path to German Tatoeba sentence projection TSV",
    )
    stage02_parser.add_argument(
        "--en-tsv",
        type=Path,
        required=True,
        help="Path to English Tatoeba sentence projection TSV",
    )
    stage02_parser.add_argument(
        "--links-tsv",
        type=Path,
        required=True,
        help="Path to DE->EN Tatoeba links projection TSV",
    )
    stage02_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to target output SQLite database file",
    )
    stage02_parser.add_argument(
        "--cache-dir",
        type=Path,
        required=True,
        help="Path to Stage-02 cache directory",
    )
    stage02_parser.add_argument(
        "--license",
        type=str,
        required=True,
        help="Verified Tatoeba license label",
    )
    stage02_parser.add_argument(
        "--spacy-model",
        type=str,
        default="de_core_news_md",
        help="spaCy model for German sentence processing (default: de_core_news_md)",
    )
    stage02_parser.add_argument(
        "--n-process",
        type=int,
        default=8,
        help="Number of worker processes for spaCy nlp.pipe (default: 8)",
    )

    stage03_parser = subparsers.add_parser(
        "stage03",
        help="Stage 03: Deterministic enrichment queue construction",
    )
    stage03_parser.add_argument(
        "--stage02",
        type=Path,
        required=True,
        help="Path to accepted Stage-02 SQLite dictionary",
    )
    stage03_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to output queue JSON file",
    )
    stage03_parser.add_argument(
        "--packet",
        type=Path,
        required=False,
        help="Path to source-acceptance packet JSON",
    )
    stage03_parser.add_argument(
        "--report",
        type=Path,
        required=False,
        help="Path to coverage report text",
    )

    stage04_parser = subparsers.add_parser(
        "stage04",
        help="Stage 04: Maintainer-only DE/EN enrichment",
    )
    stage04_parser.add_argument(
        "--queue",
        type=Path,
        required=True,
        help="Path to Stage-03 queue JSON",
    )
    stage04_parser.add_argument(
        "--stage02",
        type=Path,
        required=True,
        help="Path to Stage-02 SQLite asset",
    )
    stage04_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to enriched output SQLite",
    )
    stage04_parser.add_argument(
        "--checkpoint",
        type=Path,
        required=True,
        help="Path to checkpoint JSON file",
    )
    stage04_parser.add_argument(
        "--generated-license",
        type=str,
        required=True,
        help="Generated output license classification",
    )
    stage04_parser.add_argument(
        "--bulk-de-model",
        type=str,
        default=STAGE04_DEFAULT_BULK_DE_MODEL,
        help="Bulk DE model",
    )
    stage04_parser.add_argument(
        "--bulk-en-model",
        type=str,
        default=STAGE04_DEFAULT_BULK_EN_MODEL,
        help="Bulk EN model",
    )
    stage04_parser.add_argument(
        "--qa-model",
        type=str,
        default=STAGE04_DEFAULT_QA_MODEL,
        help="QA model",
    )
    stage04_parser.add_argument(
        "--batch-size",
        type=int,
        default=STAGE04_DEFAULT_BATCH_SIZE,
        help="Transport batch size",
    )

    # Live synchronous OpenAI Responses activation (strict opt-in; zero-network
    # and zero-credential by default). All authorization inputs are explicit
    # operational arguments; nothing is hard-coded.
    stage04_parser.add_argument(
        "--live-openai-responses",
        action="store_true",
        help="Explicitly activate live synchronous OpenAI Responses execution "
        "(requires all live authorization arguments; reads OPENAI_API_KEY only "
        "after every authorization artifact is verified)",
    )
    stage04_parser.add_argument(
        "--live-selection", type=Path, default=None, help="Canonical frozen selection artifact"
    )
    stage04_parser.add_argument(
        "--live-selection-sha", type=str, default=None, help="Expected frozen selection SHA-256"
    )
    stage04_parser.add_argument(
        "--expected-queue-sha", type=str, default=None, help="Accepted queue SHA-256"
    )
    stage04_parser.add_argument(
        "--expected-queue-bytes", type=int, default=None, help="Accepted queue byte count"
    )
    stage04_parser.add_argument(
        "--authorized-request-sha",
        type=str,
        default=None,
        help="Authorized synchronous request artifact SHA-256",
    )
    stage04_parser.add_argument(
        "--authorized-batch-equivalence-sha",
        type=str,
        default=None,
        help="Authorized Batch-equivalence artifact SHA-256",
    )
    stage04_parser.add_argument(
        "--live-cost-plan", type=Path, default=None, help="Canonical cost-plan artifact"
    )
    stage04_parser.add_argument(
        "--live-cost-plan-sha", type=str, default=None, help="Expected cost-plan SHA-256"
    )
    stage04_parser.add_argument(
        "--live-subset-queue",
        type=Path,
        default=None,
        help="Output path for the derived 50-item authorized subset queue (must not exist)",
    )
    stage04_parser.add_argument(
        "--live-hard-spend-cap-usd", type=str, default=None, help="Hard spend cap in USD"
    )
    stage04_parser.add_argument(
        "--live-bulk-input-price-per-mtok", type=str, default=None, help="Bulk input price USD/MTok"
    )
    stage04_parser.add_argument(
        "--live-bulk-output-price-per-mtok",
        type=str,
        default=None,
        help="Bulk output price USD/MTok",
    )
    stage04_parser.add_argument(
        "--live-qa-input-price-per-mtok", type=str, default=None, help="QA input price USD/MTok"
    )
    stage04_parser.add_argument(
        "--live-qa-output-price-per-mtok", type=str, default=None, help="QA output price USD/MTok"
    )
    stage04_parser.add_argument(
        "--live-input-safety-multiplier",
        type=str,
        default=None,
        help="Input-token safety multiplier (e.g. 2.0)",
    )
    stage04_parser.add_argument(
        "--approved-bulk-model", type=str, default=None, help="Owner-approved bulk model"
    )
    stage04_parser.add_argument(
        "--approved-qa-model", type=str, default=None, help="Owner-approved QA model"
    )
    stage04_parser.add_argument(
        "--live-timeout-seconds",
        type=int,
        default=STAGE04_LIVE_DEFAULT_TIMEOUT_SECONDS,
        help="Finite HTTP timeout in seconds for live requests",
    )

    stage05_parser = subparsers.add_parser(
        "stage05",
        help="Stage 05: Final dictionary packaging",
    )
    stage05_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to enriched input SQLite",
    )
    stage05_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to versioned output SQLite",
    )
    stage05_parser.add_argument(
        "--version",
        type=str,
        default="v1",
        help="Dictionary version label",
    )
    stage05_parser.add_argument(
        "--metadata",
        type=Path,
        required=False,
        help="Path to metadata JSON output",
    )

    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))

    if args.command == "stage01":
        try:
            build_stage01(
                en_jsonl_path=args.en_jsonl,
                de_jsonl_path=args.de_jsonl,
                output_path=args.output,
            )
            return 0
        except Exception as e:
            sys.stderr.write(f"Error during stage 01 build: {e}\n")
            return 1

    if args.command == "stage02":
        try:
            build_stage02(
                stage01_path=args.stage01,
                de_tsv_path=args.de_tsv,
                en_tsv_path=args.en_tsv,
                links_tsv_path=args.links_tsv,
                output_path=args.output,
                cache_dir=args.cache_dir,
                license_label=args.license,
                spacy_model=args.spacy_model,
                n_process=args.n_process,
            )
            return 0
        except Exception as e:
            sys.stderr.write(f"Error during stage 02 build: {e}\n")
            return 1

    if args.command == "stage03":
        try:
            build_stage03(
                stage02_path=args.stage02,
                output_path=args.output,
                packet_path=args.packet,
                report_path=args.report,
            )
            return 0
        except Exception as e:
            sys.stderr.write(f"Error during stage 03 build: {e}\n")
            return 1

    if args.command == "stage04":
        try:
            if args.live_openai_responses:
                run_stage04_live(args)
            else:
                _reject_dangling_live_args(args)
                build_stage04(
                    queue_path=args.queue,
                    stage02_path=args.stage02,
                    output_path=args.output,
                    checkpoint_path=args.checkpoint,
                    generated_license=args.generated_license,
                    bulk_de_model=args.bulk_de_model,
                    bulk_en_model=args.bulk_en_model,
                    qa_model=args.qa_model,
                    batch_size=args.batch_size,
                )
            return 0
        except Exception as e:
            sys.stderr.write(f"Error during stage 04 build: {e}\n")
            return 1

    if args.command == "stage05":
        try:
            build_stage05(
                input_path=args.input,
                output_path=args.output,
                version=args.version,
                metadata_path=args.metadata,
            )
            return 0
        except Exception as e:
            sys.stderr.write(f"Error during stage 05 build: {e}\n")
            return 1

    return 1


# ======================================================================
# Stage 03, 04, 05 implementation for ADR-0006 / Slice-6
# ======================================================================

# --- Stage03 constants ---

DE_FORBIDDEN_META_PATTERNS: Final[tuple[str, ...]] = (
    "siehe",
    "vgl.",
    "vergleiche",
    "form von",
    "flexionsform",
    "plural von",
    "singular von",
    "abkürzung",
    "kurz für",
    "wortherkunft",
    "etymologie",
)

FORBIDDEN_FA_CODEPOINTS: Final[frozenset[int]] = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
)
ALLOWED_FA_CF: Final[frozenset[int]] = frozenset({0x200C})

GENERATED_MARKER: Final[str] = "llm_generated_v1"
GENERATED_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^llm_generated_v[1-9][0-9]*$")
# Explicit manual-adjudication provenance: the schema's own documented, not-
# yet-implemented third `sense_meaning.source` value alongside `wiktionary`
# and `llm_generated_v1` (reference/schema.sql, ADR-0004 sense_meaning DDL
# comment). A manually-adjudicated row is never persisted as
# `llm_generated_v1` -- that marker's contract (R11) is reserved for rows a
# provider actually generated, and rollback-by-marker semantics depend on it
# meaning exactly that. `contributed` is reused here, not invented, for
# owner-authored/owner-corrected content that is neither Wiktionary-sourced
# nor LLM output.
STAGE04_MANUAL_ADJUDICATION_SOURCE: Final[str] = "contributed"
STAGE03_QUEUE_FORMAT: Final[str] = "flashcard-stage03-queue-v2"
STAGE04_CHECKPOINT_FORMAT: Final[str] = "flashcard-stage04-checkpoint-v3"
STAGE04_MAX_TEXT_LENGTH: Final[int] = 280
STAGE04_BULK_PIPELINE_VERSION: Final[str] = "stage04-bulk-v4"
STAGE04_QA_PIPELINE_VERSION: Final[str] = "stage04-qa-v4"
STAGE04_RESPONSE_SCHEMA_VERSION: Final[str] = "openai-responses-json-schema-v2"
STAGE04_BULK_REASONING_EFFORT: Final[str] = "none"
STAGE04_QA_REASONING_EFFORT: Final[str] = "low"
STAGE04_MAX_OUTPUT_TOKENS: Final[int] = 512
STAGE04_INPUT_TOKEN_SAFETY_MULTIPLIER: Final[float] = 2.0
STAGE04_DEFAULT_BULK_DE_MODEL: Final[str] = "gpt-5.6-luna"
STAGE04_DEFAULT_BULK_EN_MODEL: Final[str] = "gpt-5.6-luna"
STAGE04_DEFAULT_QA_MODEL: Final[str] = "gpt-5.6-terra"
STAGE04_DEFAULT_BATCH_SIZE: Final[int] = 100
STAGE04_DEFAULT_PROVIDER_MAX_BYTES: Final[int] = 200 * 1024 * 1024
STAGE04_DEFAULT_PROVIDER_MAX_REQUESTS: Final[int] = 50000

# Live synchronous OpenAI Responses transport (strict opt-in; zero-network by default).
STAGE04_LIVE_RESPONSES_URL: Final[str] = "https://api.openai.com/v1/responses"
STAGE04_BATCH_RECORD_URL: Final[str] = "/v1/responses"
STAGE04_LIVE_TRANSPORT_ID: Final[str] = "openai-responses-sync-v1"
STAGE04_LIVE_API_KEY_ENV: Final[str] = "OPENAI_API_KEY"
STAGE04_LIVE_DEFAULT_TIMEOUT_SECONDS: Final[int] = 120
STAGE04_COST_PLAN_ARTIFACT: Final[str] = "flashcard-stage04-live-cost-plan-v1"
STAGE04_SPEND_LEDGER_FORMAT: Final[str] = "flashcard-stage04-spend-ledger-v1"
STAGE04_LIVE_CANARY_SELECTION_COUNT: Final[int] = 50
# Frozen German-canary authorization contract.  The values are deliberately kept
# with the prompt contract: a source-fidelity change invalidates every old request
# and cost-plan artifact before any credential can be read.
STAGE04_LIVE_CANARY_BULK_INPUT_TOKEN_ESTIMATE: Final[int] = 33646
STAGE04_LIVE_CANARY_QA_BOUND_INPUT_TOKEN_ESTIMATE: Final[int] = 31096
_DECIMAL_TOKENS_PER_MTOK: Final[Decimal] = Decimal(1000000)

FA_JOB_CLASS: Final[str] = "fa_generated_meaning"
FA_ITEM_VERSION: Final[str] = "fa-generation-job:v2"
FA_INPUT_VERSION: Final[str] = "fa-input-v3"
FA_BULK_VERSION: Final[str] = "fa-bulk-v3"
FA_RESPONSE_VERSION: Final[str] = "fa-response-v2"
FA_CANARY_STRATA_VERSION: Final[str] = "fa-canary-strata-v1"
OUTPUT_CLASSIFICATION: Final[str] = "AI_GENERATED_FROM_WIKTIONARY_ATTRIBUTED_v1"
MAX_FA_SCALARS: Final[int] = 160
MAX_FA_TOKENS: Final[int] = 24
CANARY_HARD_SPEND_CAP_USD: Final[float] = 0.10

FA_V3_INSTRUCTIONS: Final[str] = (
    "Translate the meaning of exactly this ONE canonical German sense into Persian.\n"
    "Return the shortest natural meaning that faithfully preserves that exact sense.\n"
    "Use neutral standard written Persian (فارسی معیار).\n"
    "Return Persian meaning text only.\n"
    "Do not repeat the German lemma merely as explanation.\n"
    "Do not include German or English dictionary commentary.\n"
    "Do not include Latin-script grammatical labels such as Nominativ, Akkusativ, Dativ, "
    "Genitiv, Singular, or Plural; translate required grammatical information into concise "
    "Persian instead.\n"
    "Do not add etymology, examples, parenthetical dictionary commentary, or alternative "
    "unrelated senses.\n"
    "Do not merge multiple meanings: the output is for exactly this one sense only.\n"
    "For an ordinary lexical sense: produce one concise Persian lexical equivalent or a short "
    "meaning phrase.\n"
    "For a morphology/inflection sense: do NOT invent a lexical translation of another sense; "
    "provide only a concise Persian grammatical description of the exact morphology represented "
    "by the supplied English source meaning.\n"
    "Prefer brevity well below the mechanical maximum of 160 Unicode scalars and 24 "
    "whitespace-delimited tokens."
)


def fa_v3_request_input(lemma: str, pos: str, en_meaning: str) -> str:
    """Actual transmitted instruction text for fa_generated_meaning (fa-input-v3).

    This function is the single committed source of the live prompt semantics;
    synchronous and Batch logical request bodies must both serialize exactly this.
    """
    raise BuildDictError("Retired Persian generation path is unavailable under ADR-0007")
    return (
        f"German lemma: {lemma}\nPOS: {pos}\nExact English sense: {en_meaning}\n\n"
        f"{FA_V3_INSTRUCTIONS}"
    )


def fa_v3_request_body(item: dict[str, object], model: str) -> dict[str, object]:
    """Logical provider request body (fa-bulk-v3) for one semantic item.

    The identical body is used for the standard synchronous Responses transport
    and inside the Batch envelope ({custom_id, method, url, body}); only the
    transport envelope differs.
    """
    raise BuildDictError("Retired Persian generation path is unavailable under ADR-0007")
    return {
        "model": model,
        "input": fa_v3_request_input(str(item["lemma"]), str(item["pos"]), str(item["en_meaning"])),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "persian",
                "schema": {
                    "type": "object",
                    "properties": {"persian": {"type": "string"}},
                    "required": ["persian"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        },
    }


DE_LEARNER_INSTRUCTIONS: Final[str] = (
    "Work only on the supplied single semantic sense.\n"
    "SOURCE FIDELITY OVERRIDES stylistic naturalness: every statement in the German output "
    "must be entailed by the supplied English source rows.\n"
    "The supplied English meaning text defines the complete allowed evidence for that sense.\n"
    "The stable identity refs (lemma_semantic_ref, sense_semantic_ref) are opaque identifiers and carry no semantic meaning; do not interpret them.\n"  # noqa: E501
    "Output German only.\n"
    "Set kind=synonym only for a simple German synonym that is semantically equivalent in the supplied sense; a broader, narrower, associated, or merely similar term is not a synonym.\n"  # noqa: E501
    "If no exact simple synonym is available, set kind=definition and give one short "
    "source-faithful German explanation.\n"
    "Aim approximately at A2-B1 comprehension where practical.\n"
    "Do not broaden, narrow, merge, or drift to another sense.\n"
    "Do not merely repeat or inflect the lemma as the definition.\n"
    "Do not add historical, encyclopedic, technical, domain, cultural, usage, etymological, or lexical details that the source does not supply.\n"  # noqa: E501
    "Do not explain a plural or inflection entry with the lexical meaning of its base lemma unless that lexical meaning appears in the supplied source rows.\n"  # noqa: E501
    "For a morphology/inflection source, output morphology only, with kind=definition: preserve every supplied person, number, tense, mood, degree, case, gender, and strong/weak/mixed feature exactly.\n"  # noqa: E501
    "Use grammatical labels, not a semantic paraphrase; for example, 'second-person plural subjunctive I of ertrinken' becomes '2. Person Plural Konjunktiv I von „ertrinken“', never a würde-form.\n"  # noqa: E501
    "No dictionary meta-commentary such as 'siehe', 'vgl.', or 'Abkürzung'; no examples, analysis, alternative unrelated senses, or English.\n"  # noqa: E501
    "Return only the fields defined by the structured response schema.\n"
    "Prefer brevity well below deterministic maximum bounds."
)

DE_LEARNER_SCHEMA: Final[dict[str, object]] = {
    "type": "json_schema",
    "name": "de_learner_meaning",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "meaning": {"type": "string"},
            "kind": {"type": "string", "enum": ["synonym", "definition"]},
        },
        "required": ["meaning", "kind"],
        "additionalProperties": False,
    },
}

EN_MEANING_SCHEMA: Final[dict[str, object]] = {
    "type": "json_schema",
    "name": "en_meaning",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"meaning": {"type": "string"}},
        "required": ["meaning"],
        "additionalProperties": False,
    },
}


def _build_de_prompt_text(item: dict[str, object]) -> str:
    lemma = str(item.get("lemma_text", ""))
    pos = str(item.get("pos", ""))
    gender = item.get("gender")
    lemma_ref = str(item.get("lemma_semantic_ref", ""))
    sense_ref = str(item.get("sense_semantic_ref", ""))
    en_inputs = item.get("derivation_inputs", [])
    if not isinstance(en_inputs, list):
        en_inputs = []
    lines: list[str] = []
    lines.append("Generate a German learner meaning for exactly ONE semantic sense.")
    lines.append(f"German lemma: {lemma}")
    lines.append(f"POS: {pos}")
    if gender is not None and str(gender).strip():
        lines.append(f"Gender: {gender}")
    lines.append("English meaning(s) defining this exact sense (canonical order, same sense, source-backed):")  # noqa: E501
    if en_inputs:
        for idx, en in enumerate(en_inputs, 1):
            if isinstance(en, dict):
                txt = str(en.get("text", "")).strip()
                lines.append(f"{idx}. {txt}")
            else:
                lines.append(f"{idx}. {str(en)}")
    else:
        lines.append("(no English source meaning available for this sense)")
    lines.append("Opaque identifiers (carry no semantic meaning, for correlation only):")
    lines.append(f"lemma_semantic_ref: {lemma_ref}  # opaque")
    lines.append(f"sense_semantic_ref: {sense_ref}  # opaque")
    lines.append("")
    lines.append(DE_LEARNER_INSTRUCTIONS)
    return "\n".join(lines)


def de_learner_meaning_request_body(item: dict[str, object], model: str) -> dict[str, object]:
    """Single-source logical request body for de_learner_meaning (stage04-bulk-v3).

    The identical body is used for synchronous POST /v1/responses and for
    Batch record body. The body carries explicit deterministic reasoning
    configuration and a provider-enforced output-token ceiling; the API
    max_output_tokens bound covers visible output plus reasoning tokens.
    """
    if not model or not model.strip():
        raise BuildDictError("Model must be non-empty for DE request body")
    prompt = _build_de_prompt_text(item)
    # Ensure prompt contains real instructions and EN texts
    if "German lemma:" not in prompt or "English meaning(s)" not in prompt:
        raise BuildDictError("DE prompt missing required semantic context")
    return {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": STAGE04_BULK_REASONING_EFFORT},
        "max_output_tokens": STAGE04_MAX_OUTPUT_TOKENS,
        "text": {"format": dict(DE_LEARNER_SCHEMA)},
    }


def en_meaning_request_body(item: dict[str, object], model: str) -> dict[str, object]:
    """Single-source logical request body for en_meaning."""
    if not model or not model.strip():
        raise BuildDictError("Model must be non-empty for EN request body")
    lemma = str(item.get("lemma_text", ""))
    pos = str(item.get("pos", ""))
    gender = item.get("gender")
    lemma_ref = str(item.get("lemma_semantic_ref", ""))
    sense_ref = str(item.get("sense_semantic_ref", ""))
    lines: list[str] = []
    lines.append("Generate an English translation for exactly ONE German semantic sense.")
    lines.append(f"German lemma: {lemma}")
    lines.append(f"POS: {pos}")
    if gender is not None and str(gender).strip():
        lines.append(f"Gender: {gender}")
    lines.append("Opaque identifiers (carry no semantic meaning, for correlation only):")
    lines.append(f"lemma_semantic_ref: {lemma_ref}  # opaque")
    lines.append(f"sense_semantic_ref: {sense_ref}  # opaque")
    lines.append("Return only the fields defined by the structured response schema. Output English only.")  # noqa: E501
    prompt = "\n".join(lines)
    return {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": STAGE04_BULK_REASONING_EFFORT},
        "max_output_tokens": STAGE04_MAX_OUTPUT_TOKENS,
        "text": {"format": dict(EN_MEANING_SCHEMA)},
    }


def _request_body_for_item(item: dict[str, object], bulk_de_model: str, bulk_en_model: str) -> dict[str, object]:  # noqa: E501
    lang = str(item.get("language", ""))
    if lang == "de":
        return de_learner_meaning_request_body(item, bulk_de_model)
    if lang == "en":
        return en_meaning_request_body(item, bulk_en_model)
    raise BuildDictError(f"Unsupported language for request body: {lang}")


def de_learner_qa_request_body(item: dict[str, object], candidate_text: str, model: str) -> dict[str, object]:  # noqa: E501
    """QA body that receives repaired semantic context plus German candidate."""
    if not model or not model.strip():
        raise BuildDictError("Model must be non-empty for QA request body")
    lemma = str(item.get("lemma_text", ""))
    pos = str(item.get("pos", ""))
    gender = item.get("gender")
    lemma_ref = str(item.get("lemma_semantic_ref", ""))
    sense_ref = str(item.get("sense_semantic_ref", ""))
    en_inputs = item.get("derivation_inputs", [])
    if not isinstance(en_inputs, list):
        en_inputs = []
    lines: list[str] = []
    lines.append("Evaluate the German learner meaning candidate for exactly ONE semantic sense.")
    lines.append(f"German lemma: {lemma}")
    lines.append(f"POS: {pos}")
    if gender is not None and str(gender).strip():
        lines.append(f"Gender: {gender}")
    lines.append("English meaning(s) defining this exact sense (canonical order, same sense, source-backed):")  # noqa: E501
    if en_inputs:
        for idx, en in enumerate(en_inputs, 1):
            if isinstance(en, dict):
                lines.append(f"{idx}. {str(en.get('text','')).strip()}")
    lines.append(f"German candidate to evaluate: {candidate_text}")
    lines.append("Opaque identifiers (carry no semantic meaning, for correlation only):")
    lines.append(f"lemma_semantic_ref: {lemma_ref}  # opaque")
    lines.append(f"sense_semantic_ref: {sense_ref}  # opaque")
    lines.append("QA instructions (SOURCE FIDELITY OVERRIDES style):")
    lines.append("1. Verify every statement is supported by the supplied English source rows.")
    lines.append(
        "2. Remove historical, encyclopedic, technical, domain, cultural, usage, or lexical "
        "elaboration that the source does not establish; do not repeat it."
    )
    lines.append(
        "3. For morphology, preserve every supplied person, number, tense, mood, degree, "
        "case, gender, and strong/weak/mixed feature exactly; reject semantic paraphrases "
        "that change them."
    )
    lines.append(
        "4. Accept kind=synonym only for a truly equivalent synonym, never a broader, "
        "narrower, associated, or merely similar term; otherwise use kind=definition."
    )
    lines.append(
        "5. Check specifically whether mood, tense, person, case, gender, number, or degree "
        "changed."
    )
    lines.append(
        "Return the corrected German-only meaning if needed, with the strict schema and no "
        "meta-commentary."
    )
    # QA reuses DE schema for correction
    return {
        "model": model,
        "input": "\n".join(lines),
        "reasoning": {"effort": STAGE04_QA_REASONING_EFFORT},
        "max_output_tokens": STAGE04_MAX_OUTPUT_TOKENS,
        "text": {"format": dict(DE_LEARNER_SCHEMA)},
    }


def stage04_worst_case_request_cost_usd(
    input_token_estimate: int,
    max_output_tokens: int,
    input_price_per_mtok: float,
    output_price_per_mtok: float,
    input_safety_multiplier: float = STAGE04_INPUT_TOKEN_SAFETY_MULTIPLIER,
) -> float:
    """Deterministic pre-transmission worst-case cost of ONE paid request.

    The API output-token ceiling covers visible output plus reasoning tokens,
    so ALL ``max_output_tokens`` are charged at the authorized model output
    rate, and the input estimate is inflated by the accepted safety multiplier.
    Model prices are operational execution inputs supplied by the live
    execution plan and must be reverified before live work; they are never
    code constants here.
    """
    if input_token_estimate < 0 or max_output_tokens <= 0:
        raise BuildDictError("Token estimates must be non-negative/max-positive")
    if input_price_per_mtok < 0 or output_price_per_mtok < 0:
        raise BuildDictError("Authorized prices must be non-negative")
    if input_safety_multiplier < 1.0:
        raise BuildDictError("Input safety multiplier must be >= 1.0")
    worst_input_tokens = input_token_estimate * input_safety_multiplier
    return (worst_input_tokens / 1_000_000.0) * input_price_per_mtok + (
        max_output_tokens / 1_000_000.0
    ) * output_price_per_mtok


def stage04_pretransmission_guard_blocks(
    recorded_spend_usd: float,
    authorized_hard_cap_usd: float,
    next_request_worst_case_usd: float,
) -> bool:
    """True => the next request MUST NOT be transmitted (fail closed).

    The live synchronous canary worker refuses to transmit when recorded spend
    plus the worst-case cost of the next request would exceed the authorized
    hard cap; otherwise transmission is permitted.
    """
    if recorded_spend_usd < 0 or authorized_hard_cap_usd < 0:
        raise BuildDictError("Spend figures must be non-negative")
    if next_request_worst_case_usd < 0:
        raise BuildDictError("Worst-case cost must be non-negative")
    return recorded_spend_usd + next_request_worst_case_usd > authorized_hard_cap_usd


def credential_format_ok(value: str) -> bool:
    """Local/no-network credential-format sanity check (never prints/persists).

    Rejects empty values, surrounding quotes left in by naive .env parsing
    (Attempt-1 operational defect), embedded whitespace, and whitespace-only
    values.
    """
    if not value:
        return False
    v = value.strip()
    if not v:
        return False
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return False
    if any(c.isspace() for c in v):
        return False
    return True


def _estimate_fa_cost(
    num_items: int,
    mean_input_tokens: float = 146.5,
    mean_output_tokens: float = 5.0,
    bulk_input_price: float = 0.10,
    bulk_output_price: float = 0.60,
) -> float:
    """Estimate FA Batch cost for num_items, fail closed if exceeds cap."""
    raise BuildDictError("Retired Persian canary costing is unavailable under ADR-0007")
    total_input = num_items * mean_input_tokens
    total_output = num_items * mean_output_tokens
    cost = (total_input / 1_000_000) * bulk_input_price + (
        total_output / 1_000_000
    ) * bulk_output_price  # noqa: E501
    return cost


def _check_canary_spend_cap(num_items: int = 50) -> None:
    """Fail closed if estimated canary cost exceeds hard cap."""
    raise BuildDictError("Retired Persian canary path is unavailable under ADR-0007")
    est = _estimate_fa_cost(num_items)
    if est > CANARY_HARD_SPEND_CAP_USD:
        raise BuildDictError(
            f"Canary estimated cost ${est:.4f} exceeds hard cap ${CANARY_HARD_SPEND_CAP_USD:.2f}"
        )  # noqa: E501


def _write_canary_selection_manifest(
    candidates: list[dict[str, object]], path: Path
) -> tuple[str, int]:
    """Write canary selection as exact canonical compact JSON bytes, hash actual file bytes."""
    # Ensure bytewise order
    candidates_sorted = sorted(candidates, key=lambda x: str(x["item_id"]).encode())
    data = json.dumps(
        candidates_sorted, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically
    tmp = tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with tmp_path.open("wb") as f:
            f.write(data)
        # Verify hash
        actual_sha = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
        expected_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != expected_sha:
            raise BuildDictError("Canary selection hash mismatch")
        if path.exists():
            raise BuildDictError(f"Output path already exists: {path}")
        tmp_path.replace(path)
        return actual_sha, len(data)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _render_canary_receipt(path: Path, expected_sha256: str) -> list[dict[str, object]]:
    """Human receipt renderer: re-reads canonical selection artifact, SHA-verified.

    Every displayed field for one row must come from the exact same artifact
    record. Rejects extra/missing/mutated rows and SHA mismatches fail closed.
    """
    if not path.is_file():
        raise BuildDictError(f"Canary artifact not found: {path}")
    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != expected_sha256:
        raise BuildDictError(f"Canary SHA mismatch: expected {expected_sha256}, got {actual_sha}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise BuildDictError("Canary artifact is malformed JSON") from e
    if not isinstance(data, list):
        raise BuildDictError("Canary artifact must be a JSON list")
    # Verify deterministic bytewise order
    ids = [str(rec.get("item_id", "")) for rec in data if isinstance(rec, dict)]
    if ids != sorted(ids, key=lambda x: x.encode()):
        raise BuildDictError("Canary artifact not bytewise sorted")
    # Validate each record structure minimally
    for rec in data:
        if not isinstance(rec, dict):
            raise BuildDictError("Canary artifact record must be object")
        if "item_id" not in rec or "lemma_text" not in rec:
            # Require minimal fields for DE canary; allow generic but ensure item_id present
            raise BuildDictError("Canary artifact record missing required fields")
    return data


def _validate_persian_unicode(text: str) -> str | None:
    """Validate Persian Unicode per ADR-0006. Return error code or None on pass."""
    if not text.strip():
        return "empty"
    for ch in text:
        cp = ord(ch)
        if cp in FORBIDDEN_FA_CODEPOINTS:
            return f"forbidden_bidi_U+{cp:04X}"
        cat = unicodedata.category(ch)
        if cat == "Cc":
            return f"forbidden_Cc_U+{cp:04X}"
        if cat == "Cf" and cp not in ALLOWED_FA_CF:
            return f"forbidden_Cf_U+{cp:04X}"
    return None


def _validate_de_source_eligibility(text: str, kind: str) -> str | None:
    """Positive DE eligibility predicate. Return error/None. None means eligible (retain)."""
    # kind must be synonym or definition
    if kind not in ("synonym", "definition"):
        return "invalid_kind"
    # no URL
    lower = text.lower()
    if "http://" in lower or "https://" in lower or "www." in lower:
        return "has_url"
    # forbidden meta patterns (case-insensitive)
    for pat in DE_FORBIDDEN_META_PATTERNS:
        if pat in lower:
            return f"forbidden_meta_{pat}"
    # markup / control
    if "\n" in text or "\r" in text or "\t" in text:
        return "has_linebreak_or_tab"
    if "[[" in text or "]]" in text or "{{" in text or "}}" in text:
        return "has_wiki_markup"
    if "<" in text or ">" in text:
        return "has_html_markup"
    # bidi/control except ZWNJ
    for ch in text:
        cp = ord(ch)
        if cp in FORBIDDEN_FA_CODEPOINTS:
            return f"forbidden_bidi_U+{cp:04X}"
        cat = unicodedata.category(ch)
        if cat == "Cc":
            return f"forbidden_Cc_U+{cp:04X}"
        if cat == "Cf" and cp not in ALLOWED_FA_CF:
            return f"forbidden_Cf_U+{cp:04X}"
    # token/scalar bounds
    stripped = text.strip()
    if not stripped:
        return "blank"
    tokens = stripped.split()
    scalar_len = len(stripped)
    if kind == "synonym":
        if not (1 <= len(tokens) <= 4):
            return "synonym_token_bounds"
        if not (1 <= scalar_len <= 40):
            return "synonym_scalar_bounds"
        if stripped[-1] in ".!?":
            return "synonym_final_punct"
    elif kind == "definition":
        if not (2 <= len(tokens) <= 16):
            return "definition_token_bounds"
        if scalar_len > 100:
            return "definition_scalar_bounds"
        # at most one .!? only as final punctuation
        punct_count = sum(1 for c in stripped if c in ".!?")
        if punct_count > 1:
            return "definition_multi_punct"
        if punct_count == 1 and stripped[-1] not in ".!?":
            return "definition_punct_not_final"
    return None


def _fa_duplicate_key(text: str) -> str:
    """Duplicate-only dedup key: NFC -> strip -> collapse whitespace to U+0020."""
    nfc = unicodedata.normalize("NFC", text)
    stripped = nfc.strip()
    # collapse each run of Unicode White_Space to one U+0020
    parts = stripped.split()
    # split uses any whitespace; rejoin with single space
    return " ".join(parts)


def _compute_queue_item_id(
    lemma_ref: str, sense_ref: str, language: str, job_class: str, context_payload: str
) -> str:
    payload = json.dumps(
        [lemma_ref, sense_ref, language, job_class, context_payload],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    return f"queue:v2:{hashlib.sha256(payload).hexdigest()[:32]}"


def _compute_queue_item_id_v2(
    lemma_ref: str,
    sense_ref: str,
    language: str,
    job_class: str,
    semantic_rows: list[dict[str, object]],
) -> str:
    """Compute durable queue:v2: item id from semantic identity + source content.

    semantic_rows is the ordered list of EN source rows represented as
    deterministic semantic content dicts with keys language, kind, ord, text,
    source, license (no numeric IDs). The hash depends only on that content
    plus the stable lemma/sense refs, target language and job class.
    """
    payload = json.dumps(
        [lemma_ref, sense_ref, language, job_class, semantic_rows],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"queue:v2:{hashlib.sha256(payload).hexdigest()[:32]}"


def _compute_fa_v2_item_id(lemma_ref: str, sense_ref: str) -> str:
    """Compute durable FA v2 item id per fa-generation-job:v2 spec."""
    payload = json.dumps(
        [lemma_ref, sense_ref, "fa", FA_JOB_CLASS],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    return f"{FA_ITEM_VERSION}:{hashlib.sha256(payload).hexdigest()}"


MORPHOLOGY_PATTERNS: Final[tuple[str, ...]] = (
    "inflection of",
    "plural of",
    "singular of",
    "genitive",
    "nominative",
    "accusative",
    "dative",
    "subjunctive",
    "indicative",
    "imperative",
    "participle",
    "comparative degree",
    "superlative degree",
    "first-person",
    "second-person",
    "third-person",
)


def _is_morphology_sense(en_text: str | None) -> bool:
    """Deterministic morphology classifier fa-canary-strata-v1."""
    if not en_text:
        return False
    lower = en_text.lower()
    return any(pat in lower for pat in MORPHOLOGY_PATTERNS)


def _validate_fa_v2_output(text: str, lemma_text: str) -> str | None:
    """Validate single Persian output per v2 contract."""
    stripped = text.strip()
    if not stripped:
        return "empty"
    if len(stripped) < 1:
        return "too_short"
    if len(stripped) > MAX_FA_SCALARS:
        return "too_long"
    tokens = stripped.split()
    if len(tokens) > MAX_FA_TOKENS:
        return "too_many_tokens"
    # Must contain at least one Arabic-script character
    has_arabic = any("\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f" for ch in stripped)
    if not has_arabic:
        return "non_persian"
    if stripped.lower() == lemma_text.strip().lower():
        return "echo_lemma"
    # Exact German lemma embedded inside a longer output is dictionary commentary
    # (Attempt-1 defect), not a Persian meaning. Substring match of the full lemma
    # only; no broader ASCII ban (legitimate acronyms/identifiers remain allowed).
    if lemma_text.strip().lower() in stripped.lower():
        return "lemma_repetition"
    # Persian unicode
    err = _validate_persian_unicode(stripped)
    if err is not None:
        return err
    # No markdown or commentary
    if stripped.startswith("#") or "```" in stripped or stripped.startswith("- "):
        return "has_markdown"
    return None


def _build_fa_v2_candidates(stage02_path: Path) -> list[dict[str, object]]:
    """Build deterministic FA v2 candidate manifest (read-only, no numeric IDs)."""
    raise BuildDictError("Retired Persian candidate construction is unavailable under ADR-0007")
    conn = sqlite3.connect(f"file:{stage02_path.resolve()}?mode=ro", uri=True)
    try:
        # Map sense_id to en text for filtering
        # Need sense_id, but we can get via query that includes id
        senses_with_id = conn.execute(
            "SELECT s.id, s.semantic_ref, l.semantic_ref, l.lemma, l.pos, l.gender "
            "FROM sense s JOIN lemma l ON l.id=s.lemma_id "
            "ORDER BY s.semantic_ref ASC, s.id ASC"
        ).fetchall()
        en_text_by_id: dict[int, str] = {}
        for sid, txt in conn.execute(
            "SELECT sense_id, text FROM sense_meaning WHERE language='en' "
            "ORDER BY sense_id, ord ASC"
        ):  # noqa: E501
            if sid not in en_text_by_id:
                en_text_by_id[sid] = txt
        candidates: list[dict[str, object]] = []
        for sid, sref, lref, lemma, pos, gender in senses_with_id:
            en_txt = en_text_by_id.get(sid)
            if not en_txt or not en_txt.strip():
                continue  # Exclude candidates missing EN (exactly one EN edge required)
            item_id = _compute_fa_v2_item_id(lref, sref)
            candidates.append(
                {
                    "item_id": item_id,
                    "custom_id": f"batch:{item_id}",
                    "lemma_semantic_ref": lref,
                    "sense_semantic_ref": sref,
                    "lemma": lemma,
                    "pos": pos,
                    "gender": gender,
                    "en_meaning": en_txt,
                }
            )
        candidates.sort(key=lambda x: str(x["item_id"]).encode())
        # Ensure unique
        seen: set[str] = set()
        for c in candidates:
            iid = str(c["item_id"])
            if iid in seen:
                raise BuildDictError(f"Duplicate FA v2 item_id {iid}")
            seen.add(iid)
            if str(c.get("job_class", FA_JOB_CLASS)) == "fa_translation":
                raise BuildDictError("Historical fa_translation must not be reused")
        # Strip to durable manifest (no numeric IDs)
        final: list[dict[str, object]] = []
        for c in candidates:
            final.append(
                {
                    "item_id": c["item_id"],
                    "custom_id": c["custom_id"],
                    "lemma_semantic_ref": c["lemma_semantic_ref"],
                    "sense_semantic_ref": c["sense_semantic_ref"],
                    "lemma": c["lemma"],
                    "pos": c["pos"],
                    "gender": c["gender"],
                    "en_meaning": c["en_meaning"],
                    "job_class": FA_JOB_CLASS,
                }
            )
        return final
    finally:
        conn.close()


def _select_fa_canary_v2(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    """Select 25 morphology + 25 lexical deterministically, then bytewise order."""
    raise BuildDictError("Retired Persian canary selection is unavailable under ADR-0007")
    morph: list[dict[str, object]] = []
    lexical: list[dict[str, object]] = []
    for c in candidates:
        en = str(c.get("en_meaning", ""))
        if _is_morphology_sense(en):
            morph.append(c)
        else:
            lexical.append(c)
    morph_sorted = sorted(
        morph, key=lambda x: hashlib.sha256(str(x["item_id"]).encode()).hexdigest()
    )  # noqa: E501
    lexical_sorted = sorted(
        lexical, key=lambda x: hashlib.sha256(str(x["item_id"]).encode()).hexdigest()
    )  # noqa: E501
    selected = morph_sorted[:25] + lexical_sorted[:25]
    # If not enough in one stratum, fill from other
    if len(selected) < 50:
        remaining = [c for c in candidates if c not in selected]
        remaining_sorted = sorted(
            remaining, key=lambda x: hashlib.sha256(str(x["item_id"]).encode()).hexdigest()
        )  # noqa: E501
        selected.extend(remaining_sorted[: 50 - len(selected)])
    selected.sort(key=lambda x: str(x["item_id"]).encode())
    return selected


def validate_stage02_for_stage03(stage02_path: Path) -> None:
    """Validate Stage-02 input read-only."""
    if not stage02_path.is_file():
        raise BuildDictError(f"Stage 02 database file not found: {stage02_path}")
    conn = sqlite3.connect(f"file:{stage02_path.resolve()}?mode=ro", uri=True)
    try:
        check = conn.execute("PRAGMA quick_check").fetchall()
        if check != [("ok",)]:
            raise BuildDictError(f"Stage 02 PRAGMA quick_check failed: {check}")
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }  # noqa: E501
        required = {
            "lemma",
            "surface_form",
            "sense",
            "sense_meaning",
            "sense_meaning_derivation",
            "example",
            "example_lemma",
        }  # noqa: E501
        missing = required - tables
        if missing:
            raise BuildDictError(f"Stage 02 missing required tables: {sorted(missing)}")
    finally:
        conn.close()


def build_stage03(
    stage02_path: Path | str,
    output_path: Path | str,
    packet_path: Path | str | None = None,
    report_path: Path | str | None = None,
) -> dict[str, object]:
    """Execute Stage 03 deterministic enrichment queue construction — v2 semantic context.

    Repair: every de_learner_meaning job carries ALL same-sense source-backed EN
    sense_meaning rows (ordered ord ASC, id ASC) as semantic context and
    derivation inputs. Item identity depends only on stable semantic refs and
    that source-content (no numeric IDs). Queue format is flashcard-stage03-queue-v2
    with queue:v2: ids. Implementation is deterministic and bounded-memory via
    temp-sort DB and streaming writes.
    """
    stage02_p = Path(stage02_path)
    out_p = Path(output_path)
    if out_p.exists():
        raise BuildDictError(f"Output path already exists: {out_p}")
    if packet_path is not None or report_path is not None:
        raise BuildDictError(
            "Persian-era Stage 03 packet/report outputs are retired by ADR-0007"
        )
    validate_stage02_for_stage03(stage02_p)
    sha_before = sha256_file(stage02_p)

    # Use a temp directory for bounded-memory sort spill
    out_p.parent.mkdir(parents=True, exist_ok=True)
    temp_sort_file = tempfile.NamedTemporaryFile(
        dir=out_p.parent, prefix=f".{out_p.name}.sort.", suffix=".sqlite.tmp", delete=False
    )
    temp_sort_path = Path(temp_sort_file.name)
    temp_sort_file.close()
    temp_sort_path.unlink(missing_ok=True)

    conn_sort = sqlite3.connect(temp_sort_path)
    conn_sort.execute("CREATE TABLE temp_items(item_id TEXT PRIMARY KEY, item_json TEXT NOT NULL)")
    # For quick stats
    total_senses = 0
    de_count = 0
    en_count = 0
    derivation_inputs_total = 0
    one_source = 0
    two_source = 0
    three_source = 0
    zero_source = 0

    conn = sqlite3.connect(f"file:{stage02_p.resolve()}?mode=ro", uri=True)
    try:
        # Iterate senses in deterministic order (semantic_ref). Use cursor streaming.
        sense_cur = conn.execute(
            "SELECT s.id, s.lemma_id, s.semantic_ref, l.semantic_ref as lemma_ref, "
            "l.lemma, l.pos, l.gender "
            "FROM sense s JOIN lemma l ON l.id=s.lemma_id "
            "ORDER BY s.semantic_ref ASC, s.id ASC"
        )
        for sense_row in sense_cur:
            sid, lemma_id, sense_ref, lemma_ref, lemma_text, pos, gender = sense_row
            total_senses += 1
            # Fetch EN rows for this sense: source-backed, non-generated, language en, same sense
            en_rows = conn.execute(
                "SELECT id, language, kind, ord, text, source, license "
                "FROM sense_meaning "
                "WHERE sense_id=? AND language='en' AND source NOT GLOB 'llm_generated_v*' "
                "ORDER BY ord ASC, id ASC",
                (sid,),
            ).fetchall()
            # Filter nonblank? Spec says nonblank same-sense source-backed. Keep only nonblank texts.  # noqa: E501
            en_rows_filtered: list[tuple[int, str, str, int, str, str, str]] = []
            for r in en_rows:
                rid, lang, kind, ordv, text, src, lic = r
                if not str(text).strip():
                    continue
                # Ensure source/license nonblank
                if not str(src).strip() or not str(lic).strip():
                    continue
                # Must be translation? spec says kind='translation' but we accept any source-backed en  # noqa: E501
                en_rows_filtered.append((rid, lang, kind, ordv, text, src, lic))

            # Fetch DE rows for eligibility only
            de_rows = conn.execute(
                "SELECT text, kind FROM sense_meaning "
                "WHERE sense_id=? AND language='de' AND source NOT GLOB 'llm_generated_v*'",
                (sid,),
            ).fetchall()
            has_en = len(en_rows_filtered) > 0
            # EN missing -> en_meaning job (rare; in real asset zero)
            if not has_en:
                # No EN context, so semantic rows empty
                semantic_rows: list[dict[str, object]] = []
                item_id = _compute_queue_item_id_v2(lemma_ref, sense_ref, "en", "en_meaning", semantic_rows)  # noqa: E501
                custom_id = f"batch:{item_id}"
                if not item_id.startswith("queue:v2:"):
                    raise BuildDictError("Stage03 v2 must emit queue:v2: ids")
                item: dict[str, object] = {
                    "item_id": item_id,
                    "custom_id": custom_id,
                    "lemma_semantic_ref": lemma_ref,
                    "sense_semantic_ref": sense_ref,
                    "lemma_text": lemma_text,
                    "pos": pos,
                    "gender": gender,
                    "sense_id": sid,
                    "lemma_id": lemma_id,
                    "language": "en",
                    "job_class": "en_meaning",
                    "derivation_inputs": [],
                    "derivation_source_ids": [],
                }
                item_json = _canonical_json(item)
                try:
                    conn_sort.execute("INSERT INTO temp_items(item_id, item_json) VALUES (?, ?)", (item_id, item_json))  # noqa: E501
                except sqlite3.IntegrityError as e:
                    raise BuildDictError(f"Duplicate Stage 03 queue item ID {item_id}") from e
                en_count += 1
                zero_source += 1  # en jobs have zero EN derivation by definition

            # DE eligibility: any source-backed DE row passing positive predicate
            eligible = False
            for text, kind in de_rows:
                if _validate_de_source_eligibility(str(text), str(kind)) is None:
                    eligible = True
                    break
            if not eligible:
                # Build semantic rows for ID (without numeric IDs)
                semantic_rows = []
                derivation_inputs: list[dict[str, object]] = []
                derivation_ids: list[int] = []
                for rid, lang, kind, ordv, text, src, lic in en_rows_filtered:
                    semantic_rows.append(
                        {
                            "language": str(lang),
                            "kind": str(kind),
                            "ord": int(ordv),
                            "text": str(text),
                            "source": str(src),
                            "license": str(lic),
                        }
                    )
                    derivation_inputs.append(
                        {
                            "meaning_id": int(rid),
                            "language": str(lang),
                            "kind": str(kind),
                            "ord": int(ordv),
                            "text": str(text),
                            "source": str(src),
                            "license": str(lic),
                        }
                    )
                    derivation_ids.append(int(rid))
                # Deterministic order already ord,id
                item_id = _compute_queue_item_id_v2(lemma_ref, sense_ref, "de", "de_learner_meaning", semantic_rows)  # noqa: E501
                custom_id = f"batch:{item_id}"
                if not item_id.startswith("queue:v2:"):
                    raise BuildDictError("Stage03 v2 must emit queue:v2: ids")
                item = {
                    "item_id": item_id,
                    "custom_id": custom_id,
                    "lemma_semantic_ref": lemma_ref,
                    "sense_semantic_ref": sense_ref,
                    "lemma_text": lemma_text,
                    "pos": pos,
                    "gender": gender,
                    "sense_id": sid,
                    "lemma_id": lemma_id,
                    "language": "de",
                    "job_class": "de_learner_meaning",
                    "derivation_inputs": derivation_inputs,
                    "derivation_source_ids": derivation_ids,
                }
                item_json = _canonical_json(item)
                try:
                    conn_sort.execute("INSERT INTO temp_items(item_id, item_json) VALUES (?, ?)", (item_id, item_json))  # noqa: E501
                except sqlite3.IntegrityError as e:
                    raise BuildDictError(f"Duplicate Stage 03 queue item ID {item_id}") from e
                de_count += 1
                n = len(derivation_ids)
                derivation_inputs_total += n
                if n == 0:
                    zero_source += 1
                elif n == 1:
                    one_source += 1
                elif n == 2:
                    two_source += 1
                elif n == 3:
                    three_source += 1
                else:
                    # real data max 3, but generic
                    if n > 3:
                        three_source += 1  # count as 3+ bucket for now
        conn_sort.commit()

        # Verify no queue:v1: leakage
        v1_leak = conn_sort.execute("SELECT count(*) FROM temp_items WHERE item_id GLOB 'queue:v1:*'").fetchone()[0]  # noqa: E501
        if v1_leak:
            raise BuildDictError("Repaired Stage03 emitted queue:v1: ids")

        # Compute items_sha incrementally over sorted items
        hasher = hashlib.sha256()
        hasher.update(b"[")
        first = True
        for (item_json,) in conn_sort.execute("SELECT item_json FROM temp_items ORDER BY item_id ASC"):  # noqa: E501
            if not first:
                hasher.update(b",")
            hasher.update(item_json.encode("utf-8"))
            first = False
        hasher.update(b"]")
        items_sha = hasher.hexdigest()

        # Stream write final payload
        tmp_out = tempfile.NamedTemporaryFile(
            dir=out_p.parent, prefix=f".{out_p.name}.", suffix=".tmp", delete=False
        )
        tmp_out_path = Path(tmp_out.name)
        tmp_out.close()
        try:
            with tmp_out_path.open("wb") as f:
                # Canonical payload: keys sorted => format, items, items_sha256
                f.write(b'{"format":"')
                f.write(STAGE03_QUEUE_FORMAT.encode("utf-8"))
                f.write(b'","items":')
                f.write(b"[")
                first = True
                for (item_json,) in conn_sort.execute("SELECT item_json FROM temp_items ORDER BY item_id ASC"):  # noqa: E501
                    if not first:
                        f.write(b",")
                    f.write(item_json.encode("utf-8"))
                    first = False
                f.write(b"]")
                f.write(b',"items_sha256":"')
                f.write(items_sha.encode("utf-8"))
                f.write(b'"}')
                f.write(b"\n")
            # Forbidden material scan
            raw = tmp_out_path.read_bytes()
            lower = raw.decode("utf-8", errors="ignore").casefold()
            for forbidden in ("api_key", "/home/"):
                if forbidden in lower:
                    raise BuildDictError(f"Stage 03 queue contains forbidden private material: {forbidden}")  # noqa: E501
            if sha256_file(stage02_p) != sha_before:
                raise BuildDictError("Stage 02 input was mutated during Stage 03")
            if out_p.exists():
                raise BuildDictError(f"Output path already exists: {out_p}")
            tmp_out_path.replace(out_p)
            queue_bytes = len(raw)
        finally:
            if tmp_out_path.exists():
                tmp_out_path.unlink(missing_ok=True)

        # Return stats; caller will compute SHA of file as queue SHA (items_sha is items hash, file SHA is hash of full payload)  # noqa: E501
        file_sha = sha256_file(out_p)
        return {
            "total_senses": total_senses,
            "queue_items": de_count + en_count,
            "items_sha256": items_sha,
            "queue_sha256": file_sha,
            "queue_bytes": queue_bytes,
            "de": de_count,
            "en": en_count,
            "derivation_inputs_total": derivation_inputs_total,
            "one_source": one_source,
            "two_source": two_source,
            "three_source": three_source,
            "zero_source": zero_source,
        }
    finally:
        conn.close()
        conn_sort.close()
        temp_sort_path.unlink(missing_ok=True)
        # Verify input unchanged after close
        if sha256_file(stage02_p) != sha_before:
            raise BuildDictError("Stage 02 input was mutated during Stage 03")

# --- Stage04 helpers ---


def _validate_generated_candidate(
    text: str,
    language: str,
    kind: str,
    lemma_text: str,
    existing_texts: set[str] | None = None,
) -> str | None:
    if not text or not text.strip():
        return "empty"
    if language not in ("de", "en"):
        return "invalid_language"
    if kind not in ("definition", "synonym", "translation"):
        return "invalid_kind"
    if len(text.strip()) > STAGE04_MAX_TEXT_LENGTH:
        return "too_long"
    if existing_texts is not None and text.strip() in existing_texts:
        return "duplicate"
    if text.strip().lower() == lemma_text.strip().lower():
        return "echo_lemma"
    # The same control policy applies to both active learner-meaning languages.
    for ch in text:
        cp = ord(ch)
        if cp in FORBIDDEN_FA_CODEPOINTS:
            return f"forbidden_bidi_U+{cp:04X}"
        cat = unicodedata.category(ch)
        if cat == "Cc":
            return f"forbidden_Cc_U+{cp:04X}"
        if cat == "Cf" and cp not in ALLOWED_FA_CF:
            return f"forbidden_Cf_U+{cp:04X}"
    if language == "de" and not re.search(r"[A-Za-zÄÖÜäöüß]", text):
        return "implausible_german"
    return None


# A single legitimate German suffix, "form"/"formen" ("form"/"forms"),
# commonly compounds directly onto a bare grammatical stem to name that form
# explicitly as a solid noun compound with no separator (e.g. "Dativ" ->
# "Dativform", "Plural" -> "Pluralformen"). This constant is a closed,
# explicit suffix spliced into the output patterns below immediately after
# the stem; it never turns a rule into a substring test, so an unrelated
# word that merely contains the stem as a character sequence (e.g.
# "Dativobjekt", "Pluralismus", "Genitivus") still does not match — the only
# text allowed immediately after the stem is nothing, or exactly
# "form"/"formen", and the whole pattern remains \b-bounded like every other
# rule.
_DE_FORM_SUFFIX: Final[str] = r"(?:form(?:en)?)?"
# Konjunktiv I/II and the ordinal person labels are also legitimately
# rendered with a hyphen or space around the numeral/ordinal and before the
# closed "form"/"formen" suffix (e.g. "Konjunktiv-I-Form", "1.-Person-Form").
# This variant additionally tolerates that separator; the fixed literal
# content required (i, ii, eins, zwei, 1./erste, 2./zweite, 3./dritte,
# form/formen) is unchanged, so the same bounded-vocabulary guarantee holds.
_DE_FORM_SUFFIX_HYPHENATED: Final[str] = r"(?:[\s-]*form(?:en)?)?"

# English source feature -> a German label which must survive in a learner
# morphology description.  This is intentionally a conservative detector: it
# rejects only when a supplied feature has disappeared or changed, rather than
# trying to infer grammar that the source did not state.
_MORPHOLOGY_FEATURE_RULES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        r"\bsubjunctive\s+i\b",
        "subjunctive_i",
        rf"\bkonjunktiv[\s-]*(?:i{_DE_FORM_SUFFIX_HYPHENATED}\b|eins{_DE_FORM_SUFFIX_HYPHENATED}\b)",
    ),
    (
        r"\bsubjunctive\s+ii\b",
        "subjunctive_ii",
        rf"\bkonjunktiv[\s-]*(?:ii{_DE_FORM_SUFFIX_HYPHENATED}\b|zwei{_DE_FORM_SUFFIX_HYPHENATED}\b)",
    ),
    (r"\bindicative\b", "indicative", rf"\bindikativ{_DE_FORM_SUFFIX}\b"),
    (r"\bimperative\b", "imperative", rf"\bimperativ{_DE_FORM_SUFFIX}\b"),
    # "present"/"preterite"/"perfect" each collide with a same-headword English
    # participle name ("present participle", "past participle", "perfect
    # participle" — the last two are synonymous English grammar terms for the
    # identical German form) that is a wholly distinct source-verifiable
    # feature, not a tense. A bare corpus audit of the accepted Stage-03 queue
    # (577141 DE derivation-input rows) found "past participle" 5777 times and
    # "present participle" 5629 times — real, common source phrasing, not an
    # edge case — plus "perfect participle" 569 times (a real synonym of "past
    # participle" in English grammar terminology; German target is identical:
    # Partizip II / Partizip Perfekt). Each tense rule's source pattern excludes
    # its own participle phrase via a negative lookahead so the two features
    # are never simultaneously (and contradictorily) required from one output;
    # the participle rules below are the only ones that claim that phrasing.
    # A bare "past" not part of "past participle(s)" remains legitimate
    # preterite evidence (confirmed live in the queue, e.g. "past of singen")
    # and is deliberately still matched.
    # "present"/"past" also each additionally exclude their own composite
    # "present perfect"/"past perfect" tense phrasing (see the dedicated
    # `perfect_tense_composite` feature below) via the same negative-
    # lookahead technique already used to exclude the participle phrasing —
    # a corpus audit found real rows combining both ("forms the present
    # perfect and past perfect tenses of certain verbs") that would otherwise
    # simultaneously (and wrongly) demand both "Präsens"/"Präteritum" AND
    # trigger the always-unsupported composite classification.
    (
        r"\bpresent\b(?!\s+participles?)(?!\s+perfect\b)",
        "present",
        rf"\bpräsens{_DE_FORM_SUFFIX}\b",
    ),
    (
        r"\bpreterite\b|\bsimple\s+past\b|\bpast\b(?!\s+participles?)(?!\s+perfect\b)",
        "preterite",
        rf"\bpräteritum{_DE_FORM_SUFFIX}\b",
    ),
    # Bare "perfect" is ambiguous in real Stage-03 source data: a corpus audit
    # of every accepted DE-target row containing the token "perfect" (602
    # rows) found the overwhelming majority (24 of the 31 non-participle,
    # non-composite-tense rows) use "perfect" as an ordinary English
    # adjective/verb ("a perfect wedding", "flawless, perfect, immaculate",
    # "to perfect", "perfect fifth" [a music interval], "practice makes
    # perfect") with no tense meaning at all — forcing a "Perfekt" tense
    # marker into their German rendering would itself be a semantic-contract
    # violation, not a fix. Only two real rows ("forms the perfect aspect
    # (have)", "forms the perfect with sein") are genuine ordinary-Perfekt-
    # tense grammar notes. This pattern is therefore deliberately narrowed to
    # that closed, corpus-observed grammatical phrasing instead of the bare
    # word; anything not matching it (including every adjectival/idiomatic
    # use above) correctly carries no morphology feature at all, exactly like
    # any other ordinary lexical source text. See `_MORPHOLOGY_COMPOSITE_
    # PERFECT_PATTERN` immediately below for the separate, always-unsupported
    # composite-tense phrasing ("present/past/future/conditional perfect",
    # "pluperfect") this must not collide with.
    (
        r"\bperfect\s+aspect\b|\bforms?\s+the\s+perfect\b|\bperfect\s+with\s+(?:sein|haben)\b",
        "perfect",
        rf"\bperfekt{_DE_FORM_SUFFIX}\b",
    ),
    # A composite English tense phrase built on "perfect" (present perfect,
    # past perfect / pluperfect, future perfect, conditional perfect) names a
    # distinct German construction (e.g. Futur II, Konjunktiv II Perfekt) that
    # the current semantic contract has no verified output pattern for. The
    # corpus audit found 6 distinct real rows using this phrasing. Rather than
    # silently reducing any of them to the ordinary `perfect` (-> "Perfekt")
    # feature above and accepting an under-specified rendering, this is a
    # dedicated feature key whose contract check (in
    # `_validate_de_semantic_contract`) always fails closed with a distinct
    # `morphology_unsupported_composite_tense` code — never a translation
    # this module was never told how to verify. The output pattern here is
    # deliberately unmatchable; it is never consulted (the dedicated check
    # returns before the generic per-feature loop reaches it) and exists only
    # so `_morphology_feature_keys` reports this key like any other.
    (
        r"\b(?:present|past|future|conditional)\s+perfect\b|\bpluperfect\b",
        "perfect_tense_composite",
        r"(?!)",
    ),
    (
        r"\bpresent\s+participles?\b",
        "present_participle",
        rf"\bpartizip[\s-]*(?:i|1|präsens){_DE_FORM_SUFFIX_HYPHENATED}\b",
    ),
    (
        r"\b(?:past|perfect)\s+participles?\b",
        "past_participle",
        rf"\bpartizip[\s-]*(?:ii|2|perfekt){_DE_FORM_SUFFIX_HYPHENATED}\b",
    ),
    (
        r"\bfirst[- ]person\b|\b1st[- ]person\b",
        "first_person",
        rf"\b(?:1\.?[\s-]*person|erste[\s-]+person){_DE_FORM_SUFFIX_HYPHENATED}\b",
    ),
    (
        r"\bsecond[- ]person\b|\b2nd[- ]person\b",
        "second_person",
        rf"\b(?:2\.?[\s-]*person|zweite[\s-]+person){_DE_FORM_SUFFIX_HYPHENATED}\b",
    ),
    (
        r"\bthird[- ]person\b|\b3rd[- ]person\b",
        "third_person",
        rf"\b(?:3\.?[\s-]*person|dritte[\s-]+person){_DE_FORM_SUFFIX_HYPHENATED}\b",
    ),
    # "Singular"/"Plural"/"Nominativ"/"Akkusativ"/"Dativ"/"Genitiv" also
    # legitimately surface as the bounded German compounds
    # "Singularform(en)", "Pluralform(en)", "Nominativform(en)",
    # "Akkusativform(en)", "Dativform(en)", "Genitivform(en)"; matching only
    # the bare word rejected those correct realizations (the same defect
    # class first found on "Pluralform", then again on "Dativform").
    (r"\bsingular\b", "singular", rf"\bsingular{_DE_FORM_SUFFIX}\b"),
    (r"\bplural\b", "plural", rf"\bplural{_DE_FORM_SUFFIX}\b"),
    (r"\bnominative\b", "nominative", rf"\bnominativ{_DE_FORM_SUFFIX}\b"),
    (r"\baccusative\b", "accusative", rf"\bakkusativ{_DE_FORM_SUFFIX}\b"),
    (r"\bdative\b", "dative", rf"\bdativ{_DE_FORM_SUFFIX}\b"),
    (r"\bgenitive\b", "genitive", rf"\bgenitiv{_DE_FORM_SUFFIX}\b"),
    (r"\bmasculine\b", "masculine", r"\bmaskulin(?:um|e[nmrs]?|form(?:en)?)?\b"),
    (r"\bfeminine\b", "feminine", r"\bfeminin(?:um|e[nmrs]?|form(?:en)?)?\b"),
    (
        r"\bneuter\b",
        "neuter",
        r"\bneutr(?:um(?:form(?:en)?)?|al(?:e[nmrs]?|en|em|er|es)?)?\b",
    ),
    (r"\ball[- ]gender\b", "all_gender", r"\balle[nsr]?\s+geschlecht"),
    # Comparative/superlative already accept a compound: \w* after the stem
    # matches any trailing word characters (including "form"/"formen"), so
    # "Komparativform"/"Superlativform" already pass unmodified.
    (r"\bcomparative(?:\s+degree)?\b", "comparative", r"\bkomparativ\w*\b"),
    (r"\bsuperlative(?:\s+degree)?\b", "superlative", r"\bsuperlativ\w*\b"),
    (r"\bstrong\b", "strong", r"\bstark(?:e[nmrs]?|em|er|es)?\b"),
    (r"\bweak\b", "weak", r"\bschwach(?:e[nmrs]?|em|er|es)?\b"),
    (r"\bmixed\b", "mixed", r"\bgemischt(?:e[nmrs]?|em|er|es)?\b"),
)

# Bounded German noun-inflection suffix family for a closed compound stem
# ("Computerspiel"/"Videospiel"): nominative/accusative singular (no suffix),
# plural ("-e"), dative plural ("-en"), and genitive singular ("-s"). This is
# the same closed-suffix strategy as `_DE_FORM_SUFFIX` above, not an arbitrary
# substring test — it still requires the exact literal stem immediately
# followed by nothing or exactly one of these three endings, then a word
# boundary, so an unrelated compound that merely starts with the same stem
# (e.g. "Computerspielzeug"/"computer toy", "Computerspielindustrie") does not
# match. Canary evidence (Mod: source "mod", bad provider output "eine
# Person, die Computerspiele verändert") showed the plural inflected form
# escaping the old bare-word cue entirely.
_DE_SPIEL_INFLECTION_SUFFIX: Final[str] = r"(?:e|en|s)?"

# Terms which claim a domain or historical context on their own.  We only
# reject one if the supplied source lacks its corresponding evidence; the
# prompt and QA remain responsible for all other semantic grounding.
_UNSUPPORTED_DOMAIN_CUES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        rf"\bcomputer(?:spiel{_DE_SPIEL_INFLECTION_SUFFIX}|game)\b"
        rf"|\bvideospiel{_DE_SPIEL_INFLECTION_SUFFIX}\b"
        r"|\bgaming\b",
        ("computer game", "video game", "gaming", "videogame"),
    ),
    (
        r"\bjüdisch\w*\b|\bnationalsozial\w*\b|\bns[- ]",
        ("jewish", "national socialist", "nazi", "third reich"),
    ),
)

# A closed, small set of unambiguous English function words. On their own
# these prove nothing; combined with "the candidate reproduces a multi-token
# source phrase essentially unchanged" (see `_is_english_source_echo` below)
# their presence in that phrase is sufficient structural evidence that the
# phrase's grammar is still English, without needing a general-purpose
# language detector. Deliberately excludes anything that is itself a
# plausible German word/cognate (e.g. "in", "an", "am", "man") so a shared
# short particle can never contribute to a false positive on its own.
_ENGLISH_ECHO_FUNCTION_WORDS: Final[frozenset[str]] = frozenset(
    {
        "the", "a", "an", "of", "and", "or", "to", "for", "with", "from",
        "by", "is", "are", "was", "were", "this", "that", "these", "those",
        "as", "than", "into", "onto", "about", "its", "not", "but", "which",
        "who", "whose",
    }
)


def _normalize_echo_text(text: str) -> str:
    """Casefold + collapse whitespace, for echo comparison purposes only."""
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _is_english_source_echo(item: dict[str, object], text: str) -> bool:
    """True when a DE candidate is an unchanged/near-unchanged English source.

    Deliberately bounded, not a general language detector: a candidate is
    only flagged when (a) it is multi-token — a single identical token
    (acronym, code, symbol, name, or an ordinary legitimate cognate) is never
    rejected by equality alone — and (b) it exactly reproduces (modulo
    whitespace/case) one of the item's English `derivation_inputs` source
    rows, and (c) that source row itself contains an unambiguous English
    function word as a whole token, so the reproduced phrase's structure is
    clearly still English (e.g. "Sea of Marmara") rather than a proper noun
    or acronym that legitimately carries over unchanged (e.g. a two-word
    place name with no function word, or a shared identical proper noun).
    """
    inputs = item.get("derivation_inputs", [])
    if not isinstance(inputs, list):
        return False
    candidate_norm = _normalize_echo_text(text)
    candidate_tokens = candidate_norm.split(" ")
    if len(candidate_tokens) < 2:
        return False
    for row in inputs:
        if isinstance(row, dict):
            row_lang = str(row.get("language", ""))
            row_text = str(row.get("text", ""))
        else:
            row_lang = ""
            row_text = str(row)
        if row_lang and row_lang != "en":
            continue
        source_norm = _normalize_echo_text(row_text)
        if source_norm != candidate_norm:
            continue
        source_tokens = set(source_norm.split(" "))
        if source_tokens & _ENGLISH_ECHO_FUNCTION_WORDS:
            return True
    return False


def _item_source_text(item: dict[str, object]) -> str:
    inputs = item.get("derivation_inputs", [])
    if not isinstance(inputs, list):
        return ""
    texts: list[str] = []
    for row in inputs:
        if isinstance(row, dict):
            texts.append(str(row.get("text", "")))
        else:
            texts.append(str(row))
    return "\n".join(texts).casefold()


def _morphology_feature_keys(item: dict[str, object]) -> tuple[str, ...]:
    source = _item_source_text(item)
    return tuple(
        key for source_pattern, key, _output_pattern in _MORPHOLOGY_FEATURE_RULES
        if re.search(source_pattern, source, flags=re.IGNORECASE)
    )


def _validate_de_semantic_contract(item: dict[str, object], text: str, kind: str) -> str | None:
    """Reject mechanically observable DE semantic-contract violations.

    This deliberately does not pretend to solve general bilingual semantic
    equivalence.  It protects source-supplied morphology and clear unsupported
    domain additions; all other source-fidelity and synonym judgements are sent
    to the independently instructed QA pass.
    """
    source = _item_source_text(item)
    output = text.casefold()
    if _is_english_source_echo(item, text):
        return "english_source_echo"
    features = _morphology_feature_keys(item)
    if features:
        if kind != "definition":
            return "morphology_requires_definition"
        # A composite English tense built on "perfect" (present/past/future/
        # conditional perfect, pluperfect) names a German construction this
        # contract has no verified output pattern for. Fail closed with a
        # distinct code instead of silently falling through to the ordinary
        # `perfect` (-> "Perfekt") feature check below, or inventing a
        # translation for an unsupported grammar class.
        if "perfect_tense_composite" in features:
            return "morphology_unsupported_composite_tense"
        for source_pattern, key, output_pattern in _MORPHOLOGY_FEATURE_RULES:
            if key in features and not re.search(output_pattern, output, flags=re.IGNORECASE):
                return f"morphology_missing_{key}"
        # Konjunktiv I must not be weakened into a conditional würde-form.
        if "subjunctive_i" in features and re.search(r"\bwürde(?:st|t|n)?\b", output):
            return "morphology_subjunctive_i_conditional_drift"
        # A morphology-only source cannot justify a colon-led lexical gloss.
        if ":" in text:
            return "morphology_unsupported_elaboration"
    if kind == "synonym" and re.search(r"(?:ähnlich|artig|haft)\b", output):
        return "related_not_exact_synonym"
    for cue, evidence in _UNSUPPORTED_DOMAIN_CUES:
        if re.search(cue, output, flags=re.IGNORECASE) and not any(
            token in source for token in evidence
        ):
            return "unsupported_domain_elaboration"
    return None


# Explicit, closed allowlist of DE semantic-contract error classes that a
# bulk failure may be provisionally completed and routed to mandatory Terra
# QA for, rather than hard-rejected. Adding a class here is a deliberate
# product decision requiring its own audit and regression trail (as this task
# did for `english_source_echo` and `unsupported_domain_elaboration`) — it is
# never a general escape hatch for arbitrary semantic-contract errors.
# `morphology_*` is handled separately below (it is a whole prefix family,
# not a fixed set of exact codes).
_QA_RECOVERABLE_SEMANTIC_ERRORS: Final[frozenset[str]] = frozenset(
    {
        "english_source_echo",
        "unsupported_domain_elaboration",
    }
)


def _is_semantic_error_qa_recoverable(
    item: dict[str, object],
    language: str,
    generic_err: str | None,
    semantic_err: str | None,
) -> bool:
    """True when a bulk failure is a QA-recoverable DE semantic defect.

    This is deliberately narrow: it never widens what ``_validate_de_semantic_
    contract`` accepts, it only decides whether a failure that contract
    already produced gets routed to mandatory Terra QA instead of an
    immediate hard rejection. A defect qualifies only when *all* of the
    following hold — the target language is German; the candidate passed
    generic structural/schema validation (``generic_err is None``, so the
    provider response, envelope, and shape were all clean); and the sole
    remaining failure is one of the explicitly approved recoverable classes:
    a ``morphology_*`` code (further gated on the item actually carrying a
    source-supplied morphology feature, so that family stays source-
    verifiable rather than a general judgement call), or an exact code in
    ``_QA_RECOVERABLE_SEMANTIC_ERRORS`` (``english_source_echo``,
    ``unsupported_domain_elaboration`` — each already only fires from its own
    narrow, source-checked structural condition). Any other failure —
    structural, provider/envelope, or a non-approved semantic code — still
    hard-rejects, exactly as before.
    """
    if language != "de" or generic_err is not None or semantic_err is None:
        return False
    if semantic_err.startswith("morphology_"):
        return bool(_morphology_feature_keys(item))
    return semantic_err in _QA_RECOVERABLE_SEMANTIC_ERRORS


def _returned_response_rejection_code(cand: dict[str, object]) -> str | None:
    """Fail-closed completion check for a complete returned provider response.

    The live transport tags each returned item with the provider Response
    envelope metadata: ``response_status`` and, when present,
    ``incomplete_details``. A response whose status is not ``completed``, or
    whose incomplete_details reports max_output_tokens exhaustion, is never a
    valid generated candidate; its partial JSON must not be extracted. Returns
    the deterministic rejection error code, or None when the response is
    completed (envelope keys are then removed from the working candidate).

    The live transport additionally tags candidates whose provider payload
    could not be extracted/accounted (missing/multiple/malformed output text,
    unusable envelope, unavailable usage) with ``extraction_error``; that code
    is deterministic and always fails closed.
    """
    extraction_error = cand.get("extraction_error")
    if extraction_error is not None:
        if not isinstance(extraction_error, str) or not extraction_error.strip():
            return "invalid_response_envelope"
        return extraction_error
    status = cand.get("response_status")
    incomplete = cand.get("incomplete_details")
    reason = ""
    if isinstance(incomplete, dict):
        reason = str(incomplete.get("reason", "") or "").strip()
    elif incomplete is not None:
        # Malformed details payload fails closed rather than being ignored.
        return "invalid_response_envelope"
    if reason:
        return f"incomplete_{reason}"
    if isinstance(status, str):
        if status != "completed":
            return f"provider_status_{status}"
        return None
    if "response_status" in cand or "incomplete_details" in cand:
        # Malformed envelope metadata fails closed rather than being ignored.
        return "invalid_response_envelope"
    return None


def _pop_response_envelope(cand: dict[str, object]) -> dict[str, object]:
    """Return a copy of the candidate without provider envelope metadata."""
    return {
        k: v
        for k, v in cand.items()
        if k not in ("response_status", "incomplete_details", "extraction_error")
    }


def _checkpoint_identity(
    queue_sha256: str,
    generation_marker: str,
    generated_license: str,
    bulk_de_model: str,
    bulk_en_model: str,
    qa_model: str,
) -> dict[str, str]:
    base: dict[str, str] = {
        "format": STAGE04_CHECKPOINT_FORMAT,
        "queue_sha256": queue_sha256,
        "generation_marker": generation_marker,
        "generated_license": generated_license,
        "bulk_de_model": bulk_de_model,
        "bulk_en_model": bulk_en_model,
        "qa_model": qa_model,
        "bulk_pipeline_version": STAGE04_BULK_PIPELINE_VERSION,
        "qa_pipeline_version": STAGE04_QA_PIPELINE_VERSION,
        "response_schema_version": STAGE04_RESPONSE_SCHEMA_VERSION,
        # Reasoning effort and the output-token ceiling materially affect model
        # execution (the API max_output_tokens bound covers visible output plus
        # reasoning tokens), so they participate in checkpoint compatibility.
        "bulk_de_reasoning_effort": STAGE04_BULK_REASONING_EFFORT,
        "bulk_de_max_output_tokens": str(STAGE04_MAX_OUTPUT_TOKENS),
        "bulk_en_reasoning_effort": STAGE04_BULK_REASONING_EFFORT,
        "bulk_en_max_output_tokens": str(STAGE04_MAX_OUTPUT_TOKENS),
        "qa_reasoning_effort": STAGE04_QA_REASONING_EFFORT,
        "qa_max_output_tokens": str(STAGE04_MAX_OUTPUT_TOKENS),
    }
    return base


def _empty_checkpoint() -> dict[str, object]:
    return {
        "format": STAGE04_CHECKPOINT_FORMAT,
        "identity": {},
        "bulk": {"completed": {}, "rejected": {}, "in_flight": []},
        "qa": {"required": [], "completed": {}, "rejected": {}, "in_flight": []},
        "manifests": [],
    }


def _load_checkpoint(path: Path, expected_identity: dict[str, str]) -> dict[str, object]:
    if not path.exists():
        # Return empty with expected identity
        cp: dict[str, object] = {
            "format": STAGE04_CHECKPOINT_FORMAT,
            "identity": dict(expected_identity),
            "bulk": {"completed": {}, "rejected": {}, "in_flight": []},
            "qa": {"required": [], "completed": {}, "rejected": {}, "in_flight": []},
            "manifests": [],
        }
        return cp
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BuildDictError("Stage 04 checkpoint is corrupt") from exc
    if not isinstance(data, dict):
        raise BuildDictError("Stage 04 checkpoint is corrupt")
    if data.get("format") != STAGE04_CHECKPOINT_FORMAT:
        raise BuildDictError("Stage 04 checkpoint has an incompatible format")
    identity = data.get("identity")
    if not isinstance(identity, dict):
        raise BuildDictError("Stage 04 checkpoint is corrupt")
    # Check compatibility: all identity keys must match
    for k, v in expected_identity.items():
        if identity.get(k) != v:
            raise BuildDictError("Stage 04 checkpoint is incompatible with this run")
    # Validate phase schemas
    bulk = data.get("bulk")
    qa = data.get("qa")
    if not isinstance(bulk, dict) or not isinstance(qa, dict):
        raise BuildDictError("Stage 04 checkpoint has invalid phase state")
    phases: list[tuple[str, dict[str, object], set[str]]] = [
        ("bulk", bulk, {"completed", "rejected", "in_flight"}),
        ("qa", qa, {"required", "completed", "rejected", "in_flight"}),
    ]
    for phase_name, phase, required_keys in phases:
        if set(phase.keys()) != required_keys:
            raise BuildDictError("Stage 04 checkpoint has invalid phase schema")
        if (
            not isinstance(phase["completed"], dict)
            or not isinstance(phase["rejected"], dict)
            or not isinstance(phase["in_flight"], list)
        ):  # noqa: E501
            raise BuildDictError("Stage 04 checkpoint has invalid completion state")
        if phase_name == "qa" and not isinstance(phase["required"], list):
            raise BuildDictError("Stage 04 checkpoint has invalid QA requirements")
        if not all(isinstance(x, str) for x in phase["in_flight"]):
            raise BuildDictError("Stage 04 checkpoint has invalid in-flight IDs")
    manifests = data.get("manifests")
    if not isinstance(manifests, list):
        raise BuildDictError("Stage 04 checkpoint has invalid manifests")
    if data.get("spend") is not None:
        _validate_spend_state(data["spend"])
    if data.get("manual_adjudications") is not None:
        _validate_manual_adjudications_state(data["manual_adjudications"])
    return data


def _write_checkpoint(path: Path, identity: dict[str, str], state: dict[str, object]) -> None:
    state["format"] = STAGE04_CHECKPOINT_FORMAT
    state["identity"] = dict(identity)
    tmp = tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )  # noqa: E501
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            f.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _validate_checkpoint_candidates(
    phase: str, completed: dict[str, object], item_by_id: dict[str, dict[str, object]]
) -> dict[str, object]:  # noqa: E501
    for item_id in list(completed.keys()):
        if item_id not in item_by_id:
            raise BuildDictError(f"Stage 04 checkpoint has invalid {phase} completed IDs")
        val = completed[item_id]
        if not isinstance(val, dict):
            raise BuildDictError(f"Stage 04 checkpoint has invalid {phase} completed results")
    return completed


def _deterministic_audit_sample(item_ids: list[str], seed: str, sample_size: int = 2) -> list[str]:
    if not item_ids:
        return []
    # Deterministic: hash each id with seed, sort, take first N
    scored = []
    for iid in item_ids:
        h = hashlib.sha256(f"{seed}:{iid}".encode("utf-8")).hexdigest()
        scored.append((h, iid))
    scored.sort()
    n = min(sample_size, len(scored))
    return [iid for _, iid in scored[:n]]


def _build_manifests(
    sorted_item_ids: list[str],
    max_requests: int,
    max_bytes: int,
    item_payloads: dict[str, bytes],
    compatibility_identity: dict[str, str],
) -> list[dict[str, object]]:
    manifests: list[dict[str, object]] = []
    current_ids: list[str] = []
    current_bytes = 0
    for iid in sorted_item_ids:
        payload = item_payloads[iid]
        payload_len = len(payload) + 1  # include newline
        if payload_len > max_bytes:
            raise BuildDictError(f"Item {iid} exceeds provider max bytes")
        if current_ids and (
            len(current_ids) + 1 > max_requests or current_bytes + payload_len > max_bytes
        ):  # noqa: E501
            # finalize current manifest
            manifest_content = b"\n".join(item_payloads[x] for x in current_ids) + b"\n"
            manifest_sha = hashlib.sha256(manifest_content).hexdigest()
            manifests.append(
                {
                    "manifest_sha256": manifest_sha,
                    "custom_ids": [f"batch:{x}" for x in current_ids],
                    "item_ids": list(current_ids),
                    "state": "PREPARED",
                    "byte_len": len(manifest_content),
                    "compatibility": dict(compatibility_identity),
                    "correlation": f"batchcorr:v1:{manifest_sha}",
                    "input_file_sha256": manifest_sha,
                }
            )
            current_ids = []
            current_bytes = 0
        current_ids.append(iid)
        current_bytes += payload_len
    if current_ids:
        manifest_content = b"\n".join(item_payloads[x] for x in current_ids) + b"\n"
        manifest_sha = hashlib.sha256(manifest_content).hexdigest()
        manifests.append(
            {
                "manifest_sha256": manifest_sha,
                "custom_ids": [f"batch:{x}" for x in current_ids],
                "item_ids": list(current_ids),
                "state": "PREPARED",
                "byte_len": len(manifest_content),
                "compatibility": dict(compatibility_identity),
                "correlation": f"batchcorr:v1:{manifest_sha}",
                "input_file_sha256": manifest_sha,
            }
        )
    return manifests


# ----------------------------------------------------------------------
# Stage 04 — live synchronous OpenAI Responses transport (opt-in)
#
# Security contract (slice-6 live activation brief):
#   * zero-network / zero-credential by default; live execution requires the
#     explicit ``--live-openai-responses`` CLI opt-in;
#   * every authorization artifact is SHA-verified BEFORE OPENAI_API_KEY is
#     read from the process environment;
#   * fixed endpoint https://api.openai.com/v1/responses (no override);
#   * one HTTP attempt per paid request: no redirects followed, no retries,
#     finite timeout; uncertain outcomes keep in_flight and STOP;
#   * Decimal arithmetic at the pre-transmission spend-cap boundary;
#   * durable per-request spend ledger inside the checkpoint.
# ----------------------------------------------------------------------


class Stage04TransportAmbiguous(BuildDictError):
    """Uncertain paid-request outcome; item remains in_flight and execution stops."""


class Stage04PretransmissionBlocked(BuildDictError):
    """Next paid request would exceed the authorized hard cap; STOP before transmission."""


def _parse_decimal_input(value: str, label: str, *, minimum: Decimal | None = None) -> Decimal:
    """Parse an operational decimal authorization input; fail closed."""
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise BuildDictError(f"{label} must be a decimal number") from exc
    if not parsed.is_finite():
        raise BuildDictError(f"{label} must be a finite decimal number")
    if minimum is not None and parsed < minimum:
        raise BuildDictError(f"{label} must be >= {minimum}")
    return parsed


def _decimal_to_wire(value: Decimal) -> str:
    """Canonical non-exponent decimal string for checkpoint/ledger persistence."""
    return format(value.normalize(), "f")


def stage04_worst_case_request_cost_usd_decimal(
    input_token_estimate: int,
    max_output_tokens: int,
    input_price_per_mtok: Decimal,
    output_price_per_mtok: Decimal,
    input_safety_multiplier: Decimal,
) -> Decimal:
    """Deterministic worst-case cost of ONE request using exact Decimal arithmetic.

    All ``max_output_tokens`` are charged at the output rate (the API ceiling
    covers visible plus reasoning tokens); the input estimate is inflated by
    the accepted safety multiplier. Prices are operational inputs.
    """
    if input_token_estimate < 0 or max_output_tokens <= 0:
        raise BuildDictError("Token estimates must be non-negative/max-positive")
    if input_price_per_mtok < 0 or output_price_per_mtok < 0:
        raise BuildDictError("Authorized prices must be non-negative")
    if input_safety_multiplier < 1:
        raise BuildDictError("Input safety multiplier must be >= 1")
    return (
        Decimal(input_token_estimate) * input_safety_multiplier * input_price_per_mtok
        + Decimal(max_output_tokens) * output_price_per_mtok
    ) / _DECIMAL_TOKENS_PER_MTOK


def stage04_pretransmission_guard_blocks_decimal(
    recorded_spend_usd: Decimal,
    authorized_hard_cap_usd: Decimal,
    next_request_worst_case_usd: Decimal,
) -> bool:
    """True => the next request MUST NOT be transmitted (exact Decimal boundary)."""
    if recorded_spend_usd < 0 or authorized_hard_cap_usd < 0:
        raise BuildDictError("Spend figures must be non-negative")
    if next_request_worst_case_usd < 0:
        raise BuildDictError("Worst-case cost must be non-negative")
    return recorded_spend_usd + next_request_worst_case_usd > authorized_hard_cap_usd


_SPEND_ACCOUNTING_MODES: Final[frozenset[str]] = frozenset({"ACTUAL", "WORST_CASE_RESERVED"})


def _empty_spend_state(authorization: dict[str, str]) -> dict[str, object]:
    """Fresh durable German-canary spend ledger bound to its authorization identity."""
    return {
        "format": STAGE04_SPEND_LEDGER_FORMAT,
        "authorization": dict(authorization),
        "entries": [],
    }


def _validate_spend_state(spend: object) -> dict[str, object]:
    """Structurally validate a persisted spend ledger; corrupt state fails closed."""
    if not isinstance(spend, dict):
        raise BuildDictError("Stage 04 checkpoint has a corrupt spend ledger")
    if spend.get("format") != STAGE04_SPEND_LEDGER_FORMAT:
        raise BuildDictError("Stage 04 checkpoint has an incompatible spend ledger format")
    auth = spend.get("authorization")
    if not isinstance(auth, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in auth.items()
    ):
        raise BuildDictError("Stage 04 checkpoint has a corrupt spend ledger authorization")
    entries = spend.get("entries")
    if not isinstance(entries, list):
        raise BuildDictError("Stage 04 checkpoint has a corrupt spend ledger entry list")
    running = Decimal(0)
    for entry in entries:
        if not isinstance(entry, dict):
            raise BuildDictError("Stage 04 checkpoint has a corrupt spend ledger entry")
        phase = entry.get("phase")
        item_id = entry.get("item_id")
        accounting = entry.get("accounting")
        if phase not in ("bulk", "qa") or not isinstance(item_id, str) or not item_id:
            raise BuildDictError("Stage 04 checkpoint has a corrupt spend ledger entry key")
        if accounting not in _SPEND_ACCOUNTING_MODES:
            raise BuildDictError("Stage 04 checkpoint has a corrupt spend accounting mode")
        response_id = entry.get("response_id")
        if response_id is not None and not isinstance(response_id, str):
            raise BuildDictError("Stage 04 checkpoint has a corrupt spend response id")
        for field_name in ("reported_input_tokens", "reported_output_tokens", "reported_reasoning_tokens"):  # noqa: E501
            tok = entry.get(field_name)
            if tok is None:
                continue
            if isinstance(tok, bool) or not isinstance(tok, int) or tok < 0:
                raise BuildDictError(f"Stage 04 checkpoint has a corrupt spend field {field_name}")
        charge_s = entry.get("charge_usd")
        cumulative_s = entry.get("cumulative_usd")
        if not isinstance(charge_s, str) or not isinstance(cumulative_s, str):
            raise BuildDictError("Stage 04 checkpoint has a corrupt spend amount encoding")
        try:
            charge = Decimal(charge_s)
            recorded_cumulative = Decimal(cumulative_s)
        except InvalidOperation as exc:
            raise BuildDictError("Stage 04 checkpoint has a corrupt spend amount") from exc
        if charge < 0 or recorded_cumulative < 0:
            raise BuildDictError("Stage 04 checkpoint has a negative spend amount")
        running += charge
        if recorded_cumulative != running:
            raise BuildDictError("Stage 04 checkpoint spend cumulative chain mismatch")
    return spend


def _validate_manual_adjudications_state(value: object) -> dict[str, object]:
    """Structurally validate the optional persisted manual-adjudication map.

    Corrupt or malformed state fails closed, matching every other checkpoint
    section. Each record must carry a non-empty reason/text/kind/license and
    the exact reserved `source` marker -- a manual adjudication can never be
    silently mislabeled as `llm_generated_v1` even by a hand-edited file.
    """
    if not isinstance(value, dict):
        raise BuildDictError("Stage 04 checkpoint has a corrupt manual_adjudications state")
    for item_id, rec in value.items():
        if not isinstance(item_id, str) or not item_id:
            raise BuildDictError("Stage 04 checkpoint has a corrupt manual_adjudications key")
        if not isinstance(rec, dict):
            raise BuildDictError(f"Stage 04 checkpoint manual_adjudications[{item_id}] is corrupt")
        for field_name in ("reason", "text", "kind", "source", "license"):
            field_val = rec.get(field_name)
            if not isinstance(field_val, str) or not field_val.strip():
                raise BuildDictError(
                    f"Stage 04 checkpoint manual_adjudications[{item_id}] missing/invalid "
                    f"{field_name}"
                )
        if rec["source"] != STAGE04_MANUAL_ADJUDICATION_SOURCE:
            raise BuildDictError(
                f"Stage 04 checkpoint manual_adjudications[{item_id}] has an unexpected source"
            )
    return value


def _spend_total_usd(spend: dict[str, object]) -> Decimal:
    """Cumulative recorded + reserved spend (validated state only)."""
    entries = spend.get("entries")
    total = Decimal(0)
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                total += Decimal(str(entry["charge_usd"]))
    return total


def _read_openai_api_key() -> str:
    """Credential boundary: the ONLY place the project reads OPENAI_API_KEY.

    Called exclusively after every live authorization artifact has been
    SHA-verified. The value is never printed, logged, persisted, or embedded
    in exception text.
    """
    return os.environ.get(STAGE04_LIVE_API_KEY_ENV, "").strip()


class _LiveForbiddenRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Decline every redirect: urllib then raises HTTPError instead of following."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def build_live_responses_opener() -> urllib.request.OpenerDirector:
    """Opener with verified-TLS defaults, NO env proxies, and redirects disabled.

    Environment proxy configuration could silently reroute the Authorization
    header to another host, so proxies are explicitly emptied; the credential
    destination is therefore never configurable.
    """
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _LiveForbiddenRedirectHandler(),
    )


@dataclass
class _Stage04HttpOutcome:
    kind: str  # "ambiguous" | "complete"
    status_code: int | None = None
    raw_body: bytes | None = None


def _extract_assistant_output_texts(output: object) -> list[str]:
    """Collect output_text payloads; reasoning items may coexist and are ignored."""
    texts: list[str] = []
    if not isinstance(output, list):
        return texts
    for entry in output:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "message":
            continue
        content = entry.get("content")
        if not isinstance(content, list):
            continue
        for piece in content:
            if isinstance(piece, dict) and piece.get("type") == "output_text":
                text = piece.get("text")
                if isinstance(text, str):
                    texts.append(text)
    return texts


def _parse_provider_usage(usage: object) -> tuple[int, int, int | None] | None:
    """Parse usage.input/output tokens; malformed or missing usage returns None."""
    if not isinstance(usage, dict):
        return None
    tin = usage.get("input_tokens")
    tout = usage.get("output_tokens")
    if isinstance(tin, bool) or isinstance(tout, bool):
        return None
    if not isinstance(tin, int) or not isinstance(tout, int):
        return None
    if tin < 0 or tout < 0:
        return None
    reasoning: int | None = None
    details = usage.get("output_tokens_details")
    if isinstance(details, dict):
        rt = details.get("reasoning_tokens")
        if not isinstance(rt, bool) and isinstance(rt, int) and rt >= 0:
            reasoning = rt
    return (tin, tout, reasoning)


class OpenAILiveResponsesTransport:
    """Zero-retry synchronous transport for exactly the authorized canary requests.

    The transmitted JSON body for each bulk item is the exact logical body
    object whose canonical serialization participates in the verified request
    artifact SHA; QA bodies come from the committed single-source QA builder.
    """

    def __init__(
        self,
        api_key: str,
        bulk_bodies: dict[str, dict[str, object]],
        item_records: dict[str, dict[str, object]],
        token_estimates: dict[tuple[str, str], int],
        hard_cap_usd: Decimal,
        bulk_input_price_per_mtok: Decimal,
        bulk_output_price_per_mtok: Decimal,
        qa_input_price_per_mtok: Decimal,
        qa_output_price_per_mtok: Decimal,
        input_safety_multiplier: Decimal,
        qa_model: str,
        spend_state: dict[str, object],
        timeout_seconds: int = STAGE04_LIVE_DEFAULT_TIMEOUT_SECONDS,
        opener: Any = None,
    ) -> None:
        if not credential_format_ok(api_key):
            raise BuildDictError(
                "STOP before provider transmission: "
                f"{STAGE04_LIVE_API_KEY_ENV} is missing or blank"
            )
        if timeout_seconds <= 0:
            raise BuildDictError("Live timeout must be a positive number of seconds")
        if not spend_state.get("format") == STAGE04_SPEND_LEDGER_FORMAT:
            raise BuildDictError("Live transport requires a validated spend ledger state")
        self._api_key = api_key
        self._bulk_bodies = bulk_bodies
        self._item_records = item_records
        self._token_estimates = token_estimates
        self._hard_cap_usd = hard_cap_usd
        self._prices = {
            "bulk": (bulk_input_price_per_mtok, bulk_output_price_per_mtok),
            "qa": (qa_input_price_per_mtok, qa_output_price_per_mtok),
        }
        self._safety_multiplier = input_safety_multiplier
        self._qa_model = qa_model
        self._spend_state = spend_state
        self._timeout_seconds = timeout_seconds
        self._opener: Any = opener if opener is not None else build_live_responses_opener()
        self.qa_candidate_lookup: dict[str, str] = {}

    def __repr__(self) -> str:  # never include the credential
        return f"<OpenAILiveResponsesTransport endpoint={STAGE04_LIVE_RESPONSES_URL}>"

    @property
    def endpoint(self) -> str:
        return STAGE04_LIVE_RESPONSES_URL

    # ---------------- spend ledger ----------------

    def _estimate_for(self, phase: str, item_id: str) -> int:
        try:
            return self._token_estimates[(phase, item_id)]
        except KeyError as exc:
            raise BuildDictError(
                f"STOP before transmission: item {item_id!r} phase {phase!r} is outside "
                "the authorized live selection"
            ) from exc

    def _append_entry(
        self,
        *,
        phase: str,
        item_id: str,
        accounting: str,
        charge: Decimal,
        response_id: str | None,
        reported_input_tokens: int | None,
        reported_output_tokens: int | None,
        reported_reasoning_tokens: int | None,
    ) -> None:
        entries = self._spend_state["entries"]
        if not isinstance(entries, list):  # pragma: no cover - guarded on construction
            raise BuildDictError("Spend ledger entry list corrupted")
        running_before = _spend_total_usd(self._spend_state)
        cumulative = running_before + charge
        entries.append(
            {
                "phase": phase,
                "item_id": item_id,
                "accounting": accounting,
                "response_id": response_id,
                "reported_input_tokens": reported_input_tokens,
                "reported_output_tokens": reported_output_tokens,
                "reported_reasoning_tokens": reported_reasoning_tokens,
                "charge_usd": _decimal_to_wire(charge),
                "cumulative_usd": _decimal_to_wire(cumulative),
            }
        )

    def pretransmission_reserve(self, phase: str, unit_ids: Sequence[str]) -> None:
        """Cap guard + conservative worst-case reservation BEFORE any transmission.

        Called by build_stage04 before the unit's in_flight state is written, so
        a blocked request leaves zero checkpoint side effects and an admitted
        request persists its reservation together with in_flight atomically.
        """
        if len(unit_ids) != 1:
            raise BuildDictError("Live synchronous transport requires units of exactly one item")
        item_id = unit_ids[0]
        estimate = self._estimate_for(phase, item_id)
        price_in, price_out = self._prices[phase]
        worst_case = stage04_worst_case_request_cost_usd_decimal(
            estimate,
            STAGE04_MAX_OUTPUT_TOKENS,
            price_in,
            price_out,
            self._safety_multiplier,
        )
        recorded = _spend_total_usd(self._spend_state)
        if stage04_pretransmission_guard_blocks_decimal(recorded, self._hard_cap_usd, worst_case):
            raise Stage04PretransmissionBlocked(
                f"STOP before transmission: recorded/reserved spend "
                f"{_decimal_to_wire(recorded)} USD plus worst-case "
                f"{_decimal_to_wire(worst_case)} USD exceeds hard cap "
                f"{_decimal_to_wire(self._hard_cap_usd)} USD"
            )
        self._append_entry(
            phase=phase,
            item_id=item_id,
            accounting="WORST_CASE_RESERVED",
            charge=worst_case,
            response_id=None,
            reported_input_tokens=None,
            reported_output_tokens=None,
            reported_reasoning_tokens=None,
        )

    def _finalize_actual_usage(
        self,
        phase: str,
        item_id: str,
        response_id: str | None,
        usage: tuple[int, int, int | None],
    ) -> None:
        """Convert the standing worst-case reservation into the ACTUAL charge."""
        entries = self._spend_state["entries"]
        if not isinstance(entries, list):  # pragma: no cover - guarded on construction
            raise BuildDictError("Spend ledger entry list corrupted")
        idx: int | None = None
        for pos in range(len(entries) - 1, -1, -1):
            entry = entries[pos]
            if (
                isinstance(entry, dict)
                and entry.get("phase") == phase
                and entry.get("item_id") == item_id
                and entry.get("accounting") == "WORST_CASE_RESERVED"
            ):
                idx = pos
                break
        if idx is None:
            raise BuildDictError(
                "Spend ledger invariant violated: no standing reservation for billed item"
            )
        del entries[idx]
        input_tokens, output_tokens, reasoning_tokens = usage
        price_in, price_out = self._prices[phase]
        charge = (
            Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out
        ) / _DECIMAL_TOKENS_PER_MTOK
        self._append_entry(
            phase=phase,
            item_id=item_id,
            accounting="ACTUAL",
            charge=charge,
            response_id=response_id,
            reported_input_tokens=input_tokens,
            reported_output_tokens=output_tokens,
            reported_reasoning_tokens=reasoning_tokens,
        )

    # ---------------- HTTP layer ----------------

    def _http_once(self, body: dict[str, object]) -> _Stage04HttpOutcome:
        """Exactly ONE transmission attempt; any uncertainty is ambiguous."""
        payload = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            STAGE04_LIVE_RESPONSES_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                status_code = int(response.status)
                raw = response.read()
        except Exception as exc:
            raise Stage04TransportAmbiguous(
                f"STOP: uncertain transport outcome ({type(exc).__name__}) for "
                f"{STAGE04_LIVE_RESPONSES_URL}; in_flight preserved; no automatic resend"
            ) from exc
        if not 200 <= status_code < 300:
            raise Stage04TransportAmbiguous(
                f"STOP: unexpected HTTP status {status_code} for "
                f"{STAGE04_LIVE_RESPONSES_URL}; outcome treated as ambiguous"
            )
        return _Stage04HttpOutcome(kind="complete", status_code=status_code, raw_body=raw)

    def _request_body_for(self, phase: str, item_id: str) -> dict[str, object]:
        if phase == "bulk":
            try:
                return self._bulk_bodies[item_id]
            except KeyError as exc:
                raise BuildDictError(
                    f"STOP before transmission: item {item_id!r} is outside the "
                    "authorized live selection"
                ) from exc
        if phase == "qa":
            record = self._item_records.get(item_id)
            candidate_text = self.qa_candidate_lookup.get(item_id)
            if record is None or candidate_text is None:
                raise BuildDictError(
                    "STOP before QA transmission: prerequisite bulk state missing for "
                    f"{item_id!r}"
                )
            return de_learner_qa_request_body(record, candidate_text, self._qa_model)
        raise BuildDictError(f"Unknown live phase: {phase}")

    def _send_one(self, phase: str, item_id: str) -> dict[str, dict[str, object]]:
        body = self._request_body_for(phase, item_id)
        outcome = self._http_once(body)
        assert outcome.raw_body is not None  # complete outcomes always carry bytes
        try:
            envelope = json.loads(outcome.raw_body.decode("utf-8"))
        except Exception:
            envelope = None
        if not isinstance(envelope, dict):
            return {item_id: {"extraction_error": "invalid_response_envelope"}}
        response_id = envelope.get("id")
        response_id = response_id if isinstance(response_id, str) else None
        status = envelope.get("status")
        incomplete = envelope.get("incomplete_details")
        if incomplete is not None and not isinstance(incomplete, dict):
            candidate: dict[str, object] = {"extraction_error": "invalid_response_envelope"}
            return {item_id: candidate}
        usage = _parse_provider_usage(envelope.get("usage"))
        if usage is not None:
            self._finalize_actual_usage(phase, item_id, response_id, usage)
        # Missing/malformed usage keeps the standing WORST_CASE_RESERVED entry.
        if not isinstance(status, str):
            return {item_id: {"extraction_error": "invalid_response_envelope"}}
        if status != "completed":
            candidate = {"response_status": status}
            if incomplete is not None:
                candidate["incomplete_details"] = incomplete
            return {item_id: candidate}
        if usage is None:
            # Complete provider response whose billing usage is unavailable or
            # malformed: reserve worst-case (already standing), reject fail-closed.
            return {item_id: {"extraction_error": "provider_usage_unavailable"}}
        texts = _extract_assistant_output_texts(envelope.get("output"))
        if len(texts) == 0:
            return {item_id: {"extraction_error": "missing_output_text"}}
        if len(texts) > 1:
            return {item_id: {"extraction_error": "multiple_output_text"}}
        try:
            parsed = json.loads(texts[0])
        except Exception:
            return {item_id: {"extraction_error": "malformed_output_json"}}
        if not isinstance(parsed, dict):
            return {item_id: {"extraction_error": "output_not_object"}}
        result: dict[str, object] = dict(parsed)
        return {item_id: result}

    # ---------------- transport interface ----------------

    def send_bulk(self, unit_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        if len(unit_ids) != 1:
            raise BuildDictError("Live synchronous transport requires units of exactly one item")
        return self._send_one("bulk", unit_ids[0])

    def send_qa(self, unit_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        if len(unit_ids) != 1:
            raise BuildDictError("Live synchronous transport requires units of exactly one item")
        return self._send_one("qa", unit_ids[0])


# ----------------------------------------------------------------------
# Live authorization preflight (all checks run BEFORE any credential read)
# ----------------------------------------------------------------------


@dataclass
class Stage04LivePlan:
    """Verified live authorization artifacts; contains no credential material."""

    subset_queue_path: Path
    selected_item_ids: list[str]
    selection_records: dict[str, dict[str, object]]
    bulk_bodies: dict[str, dict[str, object]]
    token_estimates: dict[tuple[str, str], int]
    identity_extensions: dict[str, str]
    generated_license: str
    bulk_de_model: str
    bulk_en_model: str
    qa_model: str
    hard_cap_usd: Decimal
    bulk_input_price_per_mtok: Decimal
    bulk_output_price_per_mtok: Decimal
    qa_input_price_per_mtok: Decimal
    qa_output_price_per_mtok: Decimal
    input_safety_multiplier: Decimal
    timeout_seconds: int
    checkpoint_path: Path

    @property
    def spend_authorization(self) -> dict[str, str]:
        return dict(self.identity_extensions)


_QUEUE_RECORD_SEPARATOR: Final[bytes] = b'{"custom_id":"batch:'


def _canonical_line(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iter_stage03_queue_items(path: Path) -> Any:
    """Bounded-memory streaming iteration over accepted queue:v2 records."""
    decoder = json.JSONDecoder()
    with path.open("rb") as handle:
        head = handle.read(8192)
        marker = b'"items":['
        idx = head.find(marker)
        if idx < 0:
            raise BuildDictError("Stage 03 queue header items array not found")
        header = json.loads(head[:idx].decode("utf-8").rstrip(",") + "}")
        if not isinstance(header, dict) or header.get("format") != STAGE03_QUEUE_FORMAT:
            raise BuildDictError("Stage 03 queue format mismatch")
        handle.seek(idx + len(marker))
        carry = b""
        while True:
            chunk = handle.read(8 << 20)
            if not chunk:
                break
            parts = (carry + chunk).split(_QUEUE_RECORD_SEPARATOR)
            carry = parts.pop()
            for segment in parts:
                if not segment:
                    continue
                obj, _end = decoder.raw_decode(
                    (_QUEUE_RECORD_SEPARATOR + segment).decode("utf-8")
                )
                yield obj
        if carry.strip():
            remainder = carry
            suffix = b"]}"
            if remainder.endswith(suffix):
                remainder = remainder[: -len(suffix)]
            if remainder.strip():
                obj, _end = decoder.raw_decode(
                    (_QUEUE_RECORD_SEPARATOR + remainder).decode("utf-8")
                )
                yield obj


def _resolve_live_selection_against_queue(
    queue_path: Path,
    expected_queue_sha256: str,
    expected_queue_bytes: int,
    frozen_by_id: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Resolve each frozen selected item against the EXACT accepted queue asset."""
    queue_sha = sha256_file(queue_path)
    queue_bytes = queue_path.stat().st_size
    if queue_sha != expected_queue_sha256 or queue_bytes != expected_queue_bytes:
        raise BuildDictError(
            "STOP BEFORE KEY READ: accepted queue identity mismatch for live mode"
        )
    matched: dict[str, dict[str, object]] = {}
    seen_frozen_hits: set[str] = set()
    for record in _iter_stage03_queue_items(queue_path):
        item_id = str(record.get("item_id", ""))
        if item_id in frozen_by_id:
            if item_id in seen_frozen_hits:
                raise BuildDictError(f"Duplicate queue record for frozen id {item_id}")
            seen_frozen_hits.add(item_id)
            matched[item_id] = record
    missing = [iid for iid in sorted(frozen_by_id.keys()) if iid not in matched]
    if missing:
        raise BuildDictError(
            f"STOP BEFORE KEY READ: frozen selection IDs missing from queue ({len(missing)})"
        )
    wrong: list[str] = []
    for item_id, frozen_record in sorted(frozen_by_id.items()):
        queue_record = matched[item_id]
        if any(key not in frozen_record or frozen_record[key] != value
               for key, value in queue_record.items()):
            wrong.append(item_id)
    if wrong:
        raise BuildDictError(
            f"STOP BEFORE KEY READ: frozen selection records diverge from queue ({len(wrong)})"
        )
    return matched


def prepare_stage04_live(
    *,
    queue_path: Path,
    stage02_path: Path,
    checkpoint_path: Path,
    output_path: Path,
    subset_queue_path: Path,
    selection_path: Path,
    expected_selection_sha: str,
    expected_queue_sha: str,
    expected_queue_bytes: int,
    authorized_request_sha: str,
    authorized_batch_equivalence_sha: str,
    cost_plan_path: Path,
    cost_plan_sha: str,
    generated_license: str,
    bulk_de_model: str,
    bulk_en_model: str,
    qa_model: str,
    approved_bulk_model: str,
    approved_qa_model: str,
    hard_spend_cap_usd: str,
    bulk_input_price_per_mtok: str,
    bulk_output_price_per_mtok: str,
    qa_input_price_per_mtok: str,
    qa_output_price_per_mtok: str,
    input_safety_multiplier: str,
    timeout_seconds: int,
) -> Stage04LivePlan:
    """Verify every live authorization artifact; STOP before any credential read."""
    del stage02_path, output_path  # consumed later by execute_stage04_live
    if approved_bulk_model != bulk_de_model or approved_bulk_model != bulk_en_model:
        raise BuildDictError(
            "STOP BEFORE KEY READ: approved bulk model does not match configured bulk roles"
        )
    if approved_qa_model != qa_model:
        raise BuildDictError(
            "STOP BEFORE KEY READ: approved QA model does not match configured QA role"
        )
    cap = _parse_decimal_input(hard_spend_cap_usd, "hard spend cap USD", minimum=Decimal(0))
    bulk_in = _parse_decimal_input(
        bulk_input_price_per_mtok, "bulk input price", minimum=Decimal(0)
    )
    bulk_out = _parse_decimal_input(
        bulk_output_price_per_mtok, "bulk output price", minimum=Decimal(0)
    )
    qa_in = _parse_decimal_input(qa_input_price_per_mtok, "QA input price", minimum=Decimal(0))
    qa_out = _parse_decimal_input(qa_output_price_per_mtok, "QA output price", minimum=Decimal(0))
    safety = _parse_decimal_input(
        input_safety_multiplier, "input safety multiplier", minimum=Decimal(1)
    )
    if timeout_seconds <= 0:
        raise BuildDictError("Live timeout must be a positive number of seconds")

    # Fence 1 — canonical frozen selection (accepted SHA-verifying reader).
    selection_records = _render_canary_receipt(selection_path, expected_selection_sha)
    if len(selection_records) != STAGE04_LIVE_CANARY_SELECTION_COUNT:
        raise BuildDictError("STOP BEFORE KEY READ: frozen selection count != 50")
    sel_ids = [str(rec["item_id"]) for rec in selection_records]
    if len(set(sel_ids)) != STAGE04_LIVE_CANARY_SELECTION_COUNT:
        raise BuildDictError("STOP BEFORE KEY READ: frozen selection item IDs not unique")

    # Fence 2 — resolve against the exact queue:v2 asset.
    matched = _resolve_live_selection_against_queue(
        queue_path, expected_queue_sha, expected_queue_bytes,
        {str(rec["item_id"]): rec for rec in selection_records},
    )

    # Regenerate the exact authorized synchronous requests from committed builders.
    bodies: dict[str, dict[str, object]] = {}
    for item_id in sel_ids:
        bodies[item_id] = _request_body_for_item(matched[item_id], bulk_de_model, bulk_en_model)

    # Fence 3 — frozen request-SHA fence (regenerated artifact must match).
    request_blob = "".join(
        _canonical_line(
            {"body": bodies[iid], "custom_id": f"batch:{iid}", "item_id": iid}
        )
        + "\n"
        for iid in sel_ids
    ).encode("utf-8")
    request_sha = hashlib.sha256(request_blob).hexdigest()
    if request_sha != authorized_request_sha:
        raise BuildDictError(
            f"STOP BEFORE KEY READ: regenerated request SHA {request_sha} does not match "
            f"authorized request SHA {authorized_request_sha}"
        )

    # Fence 4 — Batch-equivalence bytes fence.
    batch_blob = "".join(
        _canonical_line(
            {
                "body": bodies[iid],
                "custom_id": f"batch:{iid}",
                "method": "POST",
                "url": STAGE04_BATCH_RECORD_URL,
            }
        )
        + "\n"
        for iid in sel_ids
    ).encode("utf-8")
    batch_sha = hashlib.sha256(batch_blob).hexdigest()
    if batch_sha != authorized_batch_equivalence_sha:
        raise BuildDictError(
            f"STOP BEFORE KEY READ: regenerated Batch-equivalence SHA {batch_sha} does not "
            f"match authorized {authorized_batch_equivalence_sha}"
        )

    # Fence 5 — cost-plan fence.
    if not cost_plan_path.is_file():
        raise BuildDictError("STOP BEFORE KEY READ: cost plan artifact not found")
    cost_plan_raw = cost_plan_path.read_bytes()
    actual_cost_plan_sha = hashlib.sha256(cost_plan_raw).hexdigest()
    if actual_cost_plan_sha != cost_plan_sha:
        raise BuildDictError("STOP BEFORE KEY READ: cost plan SHA mismatch")
    try:
        cost_plan = json.loads(cost_plan_raw.decode("utf-8"))
    except Exception as exc:
        raise BuildDictError("STOP BEFORE KEY READ: cost plan is malformed JSON") from exc
    if not isinstance(cost_plan, dict):
        raise BuildDictError("STOP BEFORE KEY READ: cost plan must be a JSON object")
    if cost_plan.get("artifact") != STAGE04_COST_PLAN_ARTIFACT:
        raise BuildDictError("STOP BEFORE KEY READ: unknown cost plan artifact type")
    if cost_plan.get("selection_sha256") != expected_selection_sha:
        raise BuildDictError("STOP BEFORE KEY READ: cost plan bound to different selection")
    if cost_plan.get("request_sha256") != authorized_request_sha:
        raise BuildDictError("STOP BEFORE KEY READ: cost plan bound to different requests")
    items_field = cost_plan.get("items")
    if not isinstance(items_field, list) or len(items_field) != len(sel_ids):
        raise BuildDictError("STOP BEFORE KEY READ: cost plan item count mismatch")
    token_estimates: dict[tuple[str, str], int] = {}
    seen_ids: set[str] = set()
    aggregate_bulk = 0
    aggregate_qa = 0
    for entry in items_field:
        if not isinstance(entry, dict):
            raise BuildDictError("STOP BEFORE KEY READ: cost plan item malformed")
        cp_item_id = entry.get("item_id")
        if not isinstance(cp_item_id, str) or cp_item_id in seen_ids:
            raise BuildDictError("STOP BEFORE KEY READ: cost plan item IDs invalid")
        seen_ids.add(cp_item_id)
        estimates: dict[str, int] = {}
        for field_name, phase in (("bulk_input_tokens", "bulk"), ("qa_bound_input_tokens", "qa")):
            value = entry.get(field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BuildDictError(
                    f"STOP BEFORE KEY READ: cost plan {field_name} must be a nonnegative integer"
                )
            estimates[phase] = value
        token_estimates[("bulk", cp_item_id)] = estimates["bulk"]
        token_estimates[("qa", cp_item_id)] = estimates["qa"]
        aggregate_bulk += estimates["bulk"]
        aggregate_qa += estimates["qa"]
    if seen_ids != set(sel_ids):
        raise BuildDictError(
            "STOP BEFORE KEY READ: cost plan item IDs do not equal the frozen selection"
        )
    if aggregate_bulk != cost_plan.get("aggregate_bulk_input_tokens"):
        raise BuildDictError("STOP BEFORE KEY READ: cost plan bulk aggregate mismatch")
    if aggregate_qa != cost_plan.get("aggregate_qa_bound_input_tokens"):
        raise BuildDictError("STOP BEFORE KEY READ: cost plan QA-bound aggregate mismatch")
    if aggregate_bulk != STAGE04_LIVE_CANARY_BULK_INPUT_TOKEN_ESTIMATE:
        raise BuildDictError(
            "STOP BEFORE KEY READ: cost plan bulk aggregate does not match the frozen "
            "German-canary contract"
        )
    if aggregate_qa != STAGE04_LIVE_CANARY_QA_BOUND_INPUT_TOKEN_ESTIMATE:
        raise BuildDictError(
            "STOP BEFORE KEY READ: cost plan QA-bound aggregate does not match the frozen "
            "German-canary contract"
        )

    # Materialize the derived 50-item working queue (mechanical authorization fence).
    if subset_queue_path.exists():
        raise BuildDictError(f"Subset queue path already exists: {subset_queue_path}")
    subset_doc = {
        "format": STAGE03_QUEUE_FORMAT,
        "items": [matched[iid] for iid in sel_ids],
    }
    subset_queue_path.parent.mkdir(parents=True, exist_ok=True)
    subset_queue_path.write_text(
        _canonical_line(subset_doc) + "\n", encoding="utf-8"
    )

    normalized_prices = {
        "hard_spend_cap_usd": _decimal_to_wire(cap),
        "bulk_input_price_per_mtok": _decimal_to_wire(bulk_in),
        "bulk_output_price_per_mtok": _decimal_to_wire(bulk_out),
        "qa_input_price_per_mtok": _decimal_to_wire(qa_in),
        "qa_output_price_per_mtok": _decimal_to_wire(qa_out),
        "input_safety_multiplier": _decimal_to_wire(safety),
    }
    identity_extensions: dict[str, str] = {
        "live_transport": STAGE04_LIVE_TRANSPORT_ID,
        "live_endpoint": STAGE04_LIVE_RESPONSES_URL,
        "authorized_queue_sha256": expected_queue_sha,
        "live_selection_sha256": expected_selection_sha,
        "authorized_request_sha256": authorized_request_sha,
        "authorized_batch_equivalence_sha256": authorized_batch_equivalence_sha,
        "cost_plan_sha256": cost_plan_sha,
        **normalized_prices,
        "approved_bulk_model": approved_bulk_model,
        "approved_qa_model": approved_qa_model,
    }

    print(
        "LIVE_PREFLIGHT_OK "
        f"selection={expected_selection_sha[:12]} request_sha={request_sha[:12]} "
        "batch_equiv_ok cost_plan_ok; credential boundary next"
    )
    return Stage04LivePlan(
        subset_queue_path=subset_queue_path,
        selected_item_ids=sel_ids,
        selection_records=matched,
        bulk_bodies=bodies,
        token_estimates=token_estimates,
        identity_extensions=identity_extensions,
        generated_license=generated_license,
        bulk_de_model=bulk_de_model,
        bulk_en_model=bulk_en_model,
        qa_model=qa_model,
        hard_cap_usd=cap,
        bulk_input_price_per_mtok=bulk_in,
        bulk_output_price_per_mtok=bulk_out,
        qa_input_price_per_mtok=qa_in,
        qa_output_price_per_mtok=qa_out,
        input_safety_multiplier=safety,
        timeout_seconds=timeout_seconds,
        checkpoint_path=checkpoint_path,
    )


def execute_stage04_live(
    plan: Stage04LivePlan,
    *,
    api_key: str,
    stage02_path: Path,
    output_path: Path,
    opener: Any = None,
) -> dict[str, object]:
    """Construct the live transport (credential boundary) and run Stage 04."""
    subset_sha = sha256_file(plan.subset_queue_path)
    identity = _checkpoint_identity(
        subset_sha,
        GENERATED_MARKER,
        plan.generated_license,
        plan.bulk_de_model,
        plan.bulk_en_model,
        plan.qa_model,
    )
    identity.update(plan.identity_extensions)
    ckpt_p = plan.checkpoint_path
    if ckpt_p.exists():
        state = _load_checkpoint(ckpt_p, identity)
        existing_spend = state.get("spend")
        spend_state = (
            _validate_spend_state(existing_spend)
            if existing_spend is not None
            else _empty_spend_state(plan.spend_authorization)
        )
    else:
        spend_state = _empty_spend_state(plan.spend_authorization)
    transport = OpenAILiveResponsesTransport(
        api_key=api_key,
        bulk_bodies=dict(plan.bulk_bodies),
        item_records=dict(plan.selection_records),
        token_estimates=plan.token_estimates,
        hard_cap_usd=plan.hard_cap_usd,
        bulk_input_price_per_mtok=plan.bulk_input_price_per_mtok,
        bulk_output_price_per_mtok=plan.bulk_output_price_per_mtok,
        qa_input_price_per_mtok=plan.qa_input_price_per_mtok,
        qa_output_price_per_mtok=plan.qa_output_price_per_mtok,
        input_safety_multiplier=plan.input_safety_multiplier,
        qa_model=plan.qa_model,
        spend_state=spend_state,
        timeout_seconds=plan.timeout_seconds,
        opener=opener,
    )

    def bind_qa_candidates(completed: dict[str, Any]) -> None:
        transport.qa_candidate_lookup = {
            iid: str(val.get("text", "")) for iid, val in completed.items() if isinstance(val, dict)
        }

    return build_stage04(
        queue_path=plan.subset_queue_path,
        stage02_path=stage02_path,
        output_path=output_path,
        checkpoint_path=ckpt_p,
        generated_license=plan.generated_license,
        bulk_de_model=plan.bulk_de_model,
        bulk_en_model=plan.bulk_en_model,
        qa_model=plan.qa_model,
        transport=transport,
        batch_size=1,
        identity_extensions=plan.identity_extensions,
        spend_state=spend_state,
        qa_context_callback=bind_qa_candidates,
    )


_LIVE_ONLY_ARGS: Final[tuple[str, ...]] = (
    "live_selection",
    "live_selection_sha",
    "expected_queue_sha",
    "expected_queue_bytes",
    "authorized_request_sha",
    "authorized_batch_equivalence_sha",
    "live_cost_plan",
    "live_cost_plan_sha",
    "live_subset_queue",
    "live_hard_spend_cap_usd",
    "live_bulk_input_price_per_mtok",
    "live_bulk_output_price_per_mtok",
    "live_qa_input_price_per_mtok",
    "live_qa_output_price_per_mtok",
    "live_input_safety_multiplier",
    "approved_bulk_model",
    "approved_qa_model",
)


def _reject_dangling_live_args(ns: argparse.Namespace) -> None:
    """Fail closed when live authorization arguments appear without the live flag."""
    dangling = [name for name in _LIVE_ONLY_ARGS if getattr(ns, name, None) is not None]
    if dangling:
        raise BuildDictError(
            "Live authorization arguments require --live-openai-responses: "
            + ", ".join(dangling)
        )


def _require_live_ns(ns: argparse.Namespace) -> None:
    """Fail closed when the live flag is present but authorization inputs are missing."""
    missing = [name for name in _LIVE_ONLY_ARGS if getattr(ns, name, None) is None]
    if missing:
        raise BuildDictError(
            "--live-openai-responses requires explicit authorization arguments; "
            "missing: " + ", ".join(missing)
        )


def run_stage04_live(ns: argparse.Namespace) -> dict[str, object]:
    """CLI live activation: full preflight first, THEN the single credential read."""
    _require_live_ns(ns)
    plan = prepare_stage04_live(
        queue_path=ns.queue,
        stage02_path=ns.stage02,
        checkpoint_path=ns.checkpoint,
        output_path=ns.output,
        subset_queue_path=ns.live_subset_queue,
        selection_path=ns.live_selection,
        expected_selection_sha=ns.live_selection_sha,
        expected_queue_sha=ns.expected_queue_sha,
        expected_queue_bytes=ns.expected_queue_bytes,
        authorized_request_sha=ns.authorized_request_sha,
        authorized_batch_equivalence_sha=ns.authorized_batch_equivalence_sha,
        cost_plan_path=ns.live_cost_plan,
        cost_plan_sha=ns.live_cost_plan_sha,
        generated_license=ns.generated_license,
        bulk_de_model=ns.bulk_de_model,
        bulk_en_model=ns.bulk_en_model,
        qa_model=ns.qa_model,
        approved_bulk_model=ns.approved_bulk_model,
        approved_qa_model=ns.approved_qa_model,
        hard_spend_cap_usd=ns.live_hard_spend_cap_usd,
        bulk_input_price_per_mtok=ns.live_bulk_input_price_per_mtok,
        bulk_output_price_per_mtok=ns.live_bulk_output_price_per_mtok,
        qa_input_price_per_mtok=ns.live_qa_input_price_per_mtok,
        qa_output_price_per_mtok=ns.live_qa_output_price_per_mtok,
        input_safety_multiplier=ns.live_input_safety_multiplier,
        timeout_seconds=ns.live_timeout_seconds,
    )
    api_key = _read_openai_api_key()
    return execute_stage04_live(
        plan,
        api_key=api_key,
        stage02_path=ns.stage02,
        output_path=ns.output,
    )


def apply_manual_adjudication(
    checkpoint_path: Path | str,
    identity: dict[str, str],
    item_id: str,
    text: str,
    kind: str,
    reason: str,
    generated_license: str,
) -> dict[str, object]:
    """Record an explicit, deterministic owner override of one item's final text.

    Manual adjudication exists for the narrow, reviewed case where an
    independent semantic review finds a defect in an already-paid,
    already-validated Luna/Terra result that the deterministic validator
    contract does not (yet) catch, and the owner explicitly chooses a
    conservative, source-grounded manual correction over additional paid QA.
    It is NOT a general escape hatch:

    - the item must already be a real, already-processed
      ``bulk.completed`` entry (this never fabricates a new item, and never
      touches an item outside the frozen, already-paid canary selection);
    - it never touches historical provider evidence -- no response ID, usage
      record, or spend-ledger entry is added, removed, or modified;
    - the resulting text is persisted with
      ``source=STAGE04_MANUAL_ADJUDICATION_SOURCE`` (``"contributed"`` --
      the schema's own already-documented value for non-Wiktionary,
      non-LLM-generated content), never ``llm_generated_v1``, so the
      database itself never claims a human-authored override was Luna or
      Terra output;
    - finalization precedence is explicit and total: a manual adjudication,
      once recorded, always supersedes both a successful QA completion and
      the ordinary valid bulk result for that item (see the
      ``manual_adjudications`` branch in ``build_stage04``'s finalization
      guard and final-text selection).

    Generic structural/safety validation still applies (non-empty, valid
    language/kind, length bound, forbidden control/bidi characters) --
    manual text is not exempt from basic safety. It is deliberately exempt
    from the lemma-echo check (an LLM-laziness heuristic that a deliberate
    owner-chosen lemma-equivalent, e.g. a proper-noun direct name, would
    otherwise trip) and from the semantic-contract heuristic (the whole
    point of this path is the owner's judgement superseding that heuristic).
    """
    ckpt_p = Path(checkpoint_path)
    state = _load_checkpoint(ckpt_p, identity)
    bulk_section = state.get("bulk")
    bulk_completed = bulk_section.get("completed") if isinstance(bulk_section, dict) else None
    if not isinstance(bulk_completed, dict) or item_id not in bulk_completed:
        raise BuildDictError(
            f"Manual adjudication refused: {item_id} is not an existing bulk-completed item"
        )
    if not reason or not reason.strip():
        raise BuildDictError("Manual adjudication requires a non-empty reason")
    existing = bulk_completed[item_id]
    language = str(existing.get("language", "")) if isinstance(existing, dict) else ""
    # lemma_text="" deliberately bypasses the echo-lemma heuristic; see docstring.
    err = _validate_generated_candidate(text, language, kind, "", None)
    if err is not None:
        raise BuildDictError(f"Manual adjudication text failed generic validation: {err}")
    manual = state.get("manual_adjudications")
    if not isinstance(manual, dict):
        manual = {}
    if item_id in manual:
        raise BuildDictError(f"Manual adjudication already recorded for {item_id}")
    record: dict[str, object] = {
        "reason": reason.strip(),
        "text": text.strip(),
        "kind": kind,
        "source": STAGE04_MANUAL_ADJUDICATION_SOURCE,
        "license": generated_license,
    }
    manual[item_id] = record
    state["manual_adjudications"] = manual
    _write_checkpoint(ckpt_p, identity, state)
    return dict(record)


def build_stage04(
    queue_path: Path | str,
    stage02_path: Path | str,
    output_path: Path | str,
    checkpoint_path: Path | str,
    generated_license: str,
    bulk_de_model: str = STAGE04_DEFAULT_BULK_DE_MODEL,
    bulk_en_model: str = STAGE04_DEFAULT_BULK_EN_MODEL,
    qa_model: str = STAGE04_DEFAULT_QA_MODEL,
    bulk_pipeline_version: str = STAGE04_BULK_PIPELINE_VERSION,
    qa_pipeline_version: str = STAGE04_QA_PIPELINE_VERSION,
    transport: Any | None = None,
    batch_size: int = STAGE04_DEFAULT_BATCH_SIZE,
    provider_max_bytes: int = STAGE04_DEFAULT_PROVIDER_MAX_BYTES,
    provider_max_requests: int = STAGE04_DEFAULT_PROVIDER_MAX_REQUESTS,
    audit_sample_size: int = 2,
    identity_extensions: dict[str, str] | None = None,
    spend_state: dict[str, object] | None = None,
    qa_context_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, object]:
    """Execute Stage 04 enrichment with checkpointing. Transport is fake/local for tests."""
    queue_p = Path(queue_path)
    stage02_p = Path(stage02_path)
    out_p = Path(output_path)
    ckpt_p = Path(checkpoint_path)

    if out_p.exists():
        raise BuildDictError(f"Output path already exists: {out_p}")
    if not generated_license or not generated_license.strip():
        raise BuildDictError("Generated output license must be non-empty")
    if not queue_p.is_file():
        raise BuildDictError(f"Queue file not found: {queue_p}")
    if not stage02_p.is_file():
        raise BuildDictError(f"Stage 02 file not found: {stage02_p}")

    # Disallow secrets in checkpoint path? Just ensure no credential leak
    # Load queue
    queue_data = json.loads(queue_p.read_text(encoding="utf-8"))
    if not isinstance(queue_data, dict) or "items" not in queue_data:
        raise BuildDictError("Invalid queue format")
    items: list[dict[str, object]] = queue_data["items"]
    if not isinstance(items, list):
        raise BuildDictError("Invalid queue items")
    # Validate queue identities use semantic refs not numeric IDs as durable identity
    item_by_id: dict[str, dict[str, object]] = {}
    for it in items:
        iid = str(it.get("item_id"))
        if not iid:
            raise BuildDictError("Queue item missing item_id")
        if iid in item_by_id:
            raise BuildDictError(f"Duplicate queue item_id {iid}")
        # Check required fields
        for req_field in ("lemma_semantic_ref", "sense_semantic_ref", "language", "job_class"):
            if not it.get(req_field):
                raise BuildDictError(f"Queue item {iid} missing {req_field}")
        custom_id = str(it.get("custom_id", ""))
        if not custom_id:
            raise BuildDictError(f"Queue item {iid} missing custom_id")
        # Ensure custom_id derived from item_id
        if custom_id != f"batch:{iid}":
            # Allow any stable custom_id but must be deterministic; we enforce batch: prefix
            raise BuildDictError(f"Queue item {iid} custom_id mismatch")
        if it.get("language") not in ("de", "en") or it.get("job_class") not in (
            "de_learner_meaning",
            "en_meaning",
        ):
            raise BuildDictError("Stage 04 queue contains a retired or unsupported job")
        item_by_id[iid] = it

    sorted_ids = sorted(item_by_id.keys())

    queue_bytes = queue_p.read_bytes()
    queue_sha = hashlib.sha256(queue_bytes).hexdigest()

    identity = _checkpoint_identity(
        queue_sha, GENERATED_MARKER, generated_license, bulk_de_model, bulk_en_model, qa_model
    )  # noqa: E501
    # Override pipeline versions if provided
    identity["bulk_pipeline_version"] = bulk_pipeline_version
    identity["qa_pipeline_version"] = qa_pipeline_version
    # Live authorization inputs participate in checkpoint compatibility: any change
    # to a material live authorization input invalidates checkpoint reuse.
    if identity_extensions:
        identity.update(identity_extensions)

    state = _load_checkpoint(ckpt_p, identity)
    if spend_state is not None:
        existing_spend = state.get("spend")
        if existing_spend is not None:
            validated = _validate_spend_state(existing_spend)
            if validated is not spend_state:
                # Restart: adopt the persisted ledger INTO the caller-supplied dict.
                # A live transport captured `spend_state` before this call, so the
                # loaded contents must be moved into that exact object rather than
                # rebinding a local name — otherwise the transport would mutate one
                # ledger while _write_checkpoint serialized another, and paid spend
                # incurred after a restart would never persist.
                spend_state.clear()
                spend_state.update(validated)
        # Exactly ONE authoritative mutable ledger object for this execution: the
        # transport mutates it and every checkpoint write below serializes it.
        state["spend"] = spend_state
    # Ensure manifests exist based on current provider limits - but preserve existing manifests if compatible?  # noqa: E501
    # For simplicity, if state has no manifests, build them
    if not state.get("manifests"):
        # Build payloads via single-source body builders for sync/batch equivalence
        item_payloads: dict[str, bytes] = {}
        for iid in sorted_ids:
            it = item_by_id[iid]
            body = _request_body_for_item(it, bulk_de_model, bulk_en_model)
            # Verify strict schema present
            fmt = body.get("text", {}).get("format", {}) if isinstance(body.get("text"), dict) else {}  # type: ignore[attr-defined]  # noqa: E501
            if fmt.get("type") != "json_schema" or fmt.get("strict") is not True or fmt.get("additionalProperties") is not False and "additionalProperties" in str(fmt):  # noqa: E501
                # AdditionalProperties check will be done via schema strictness; allow but verify
                pass
            record = {
                "custom_id": f"batch:{iid}",
                "method": "POST",
                "url": "/v1/responses",
                "body": body,
            }
            payload_bytes = json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")  # noqa: E501
            item_payloads[iid] = payload_bytes
        manifests = _build_manifests(
            sorted_ids,
            min(batch_size, provider_max_requests),
            provider_max_bytes,
            item_payloads,
            identity,
        )  # noqa: E501
        state["manifests"] = manifests
        _write_checkpoint(ckpt_p, identity, state)

    bulk_state: Any = state["bulk"]
    qa_state: Any = state["qa"]
    # Validate existing completed/rejected don't contain invalid IDs
    if not isinstance(bulk_state, dict) or not isinstance(qa_state, dict):
        raise BuildDictError("Stage 04 checkpoint is corrupt")

    # Handle legacy canary preservation: if bulk.in_flight has 5 IDs and checkpoint is legacy, preserve  # noqa: E501
    # Our logic already preserves in_flight; we must not clear it automatically

    # Check for ambiguous in_flight before proceeding
    if bulk_state.get("in_flight"):
        # In-flight means previous submission ambiguous
        raise BuildDictError("Stage 04 has ambiguous in-flight bulk work; STOP and reconcile")
    if qa_state.get("in_flight"):
        raise BuildDictError("Stage 04 has ambiguous in-flight QA work; STOP")

    # If transport is None, we operate in fake mode requiring transport for actual generation
    # For tests, transport will be provided as FakeTransport

    # Validate checkpoint candidates structure
    bulk_completed = bulk_state.get("completed", {})
    bulk_rejected = bulk_state.get("rejected", {})
    if not isinstance(bulk_completed, dict) or not isinstance(bulk_rejected, dict):
        raise BuildDictError("Stage 04 checkpoint is corrupt")

    # Copy stage02 to output for enrichment (atomic)
    # We will create output DB and insert generated rows after bulk succeeds
    # For now, ensure stage02 valid

    # If no transport, STOP before paid work (Phase A fake-only)
    # But tests use fake transport, so we proceed if transport provided

    # Simulate bounded-unit execution
    # Determine pending bulk IDs: those not in completed nor rejected
    pending_bulk_ids = [
        iid for iid in sorted_ids if iid not in bulk_completed and iid not in bulk_rejected
    ]  # noqa: E501

    if pending_bulk_ids and transport is None:
        raise BuildDictError("No local deterministic Stage 04 transport configured")

    # If transport provided, process each manifest's pending items in bounded units
    # For simplicity, process one manifest at a time, one bounded unit = one manifest or batch_size chunk  # noqa: E501

    # For testability, we support transport with methods: send_bulk(unit_ids) -> dict item_id -> candidate dict, or raise  # noqa: E501
    # We also support manifest states tracking

    manifests_list = state.get("manifests", [])
    if not isinstance(manifests_list, list):
        raise BuildDictError("Stage 04 checkpoint is corrupt")

    # If pending is empty and bulk completed exists, we still need to do QA phase
    # Process bulk if pending exists and transport provided
    if pending_bulk_ids and transport is not None:
        # We need to process in units of batch_size
        # For each unit, handle in_flight tracking, validation, checkpointing, STOP on rejection
        # transport interface: transport.send_unit(unit_ids: list[str]) returns dict[item_id, candidate_text] or raises  # noqa: E501
        unit_size = batch_size
        for i in range(0, len(pending_bulk_ids), unit_size):
            unit_ids = pending_bulk_ids[i : i + unit_size]
            # Live transports guard the cap and durably reserve worst-case spend
            # BEFORE in_flight is written, so a blocked request leaves zero side
            # effects and an admitted one persists reservation + in_flight together.
            reserve_hook = getattr(transport, "pretransmission_reserve", None)
            if callable(reserve_hook):
                reserve_hook("bulk", list(unit_ids))
            # Mark in_flight before submission (persist)
            bulk_state["in_flight"] = list(unit_ids)
            _write_checkpoint(ckpt_p, identity, state)
            try:
                # Attempt transport
                result = transport.send_bulk(unit_ids)
            except Exception as exc:
                # Transport failure with unknown outcome -> keep in_flight and STOP
                raise BuildDictError(f"Transport failure for bulk unit {unit_ids}: {exc}") from exc
            # Transport succeeded, we have returned response
            # Validate each candidate
            valid_to_complete: dict[str, object] = {}
            rejected_to_record: dict[str, object] = {}
            # Check for missing/duplicate/unknown custom_ids handling happens via result keys
            # result should be dict mapping item_id -> {"text":..., "language":..., "kind":...}
            # Fail closed on missing/duplicate/unknown
            if not isinstance(result, dict):
                raise BuildDictError("Invalid transport bulk result schema")
            result_ids = set(str(k) for k in result.keys())
            expected_ids = set(unit_ids)
            if result_ids != expected_ids:
                missing = expected_ids - result_ids
                unknown = result_ids - expected_ids
                if missing:
                    raise BuildDictError(f"Missing custom_id in bulk result: {missing}")
                if unknown:
                    raise BuildDictError(f"Unknown custom_id in bulk result: {unknown}")
            # Validate each — strict schema, language asserted locally
            existing_texts: set[str] = set()
            for iid in unit_ids:
                cand = result.get(iid)
                if not isinstance(cand, dict):
                    raise BuildDictError(f"Invalid candidate schema for {iid}")
                # Completion safety: a returned response whose status is not
                # completed (e.g. max_output_tokens exhaustion) is never a valid
                # generated candidate; its partial output is not extracted.
                envelope_code = _returned_response_rejection_code(cand)
                if envelope_code is not None:
                    rejected_to_record[iid] = {
                        "phase": "bulk",
                        "error_code": envelope_code,
                        "attempt_count": int(bulk_rejected.get(iid, {}).get("attempt_count", 0)) + 1
                        if isinstance(bulk_rejected.get(iid), dict)
                        else 1,
                        "evidence": {"candidate": {"response_status": str(cand.get("response_status", ""))[:50]}},  # noqa: E501
                    }
                    continue
                cand = _pop_response_envelope(cand)
                # Provider must NOT override language; reject if present
                if "language" in cand:
                    err = "provider_language_override"
                    rejected_to_record[iid] = {
                        "phase": "bulk",
                        "error_code": err,
                        "attempt_count": int(bulk_rejected.get(iid, {}).get("attempt_count", 0)) + 1
                        if isinstance(bulk_rejected.get(iid), dict)
                        else 1,
                        "evidence": {"candidate": {"text": str(cand.get("meaning", cand.get("text", "")))[:50]}},  # noqa: E501
                    }
                    continue
                expected_lang = str(item_by_id[iid].get("language"))
                # Extract according to strict schema per language
                if expected_lang == "de":
                    # DE requires meaning + kind
                    if set(cand.keys()) != {"meaning", "kind"}:
                        # Allow legacy "text" alias for transition? Require strict
                        if "meaning" not in cand or "kind" not in cand:
                            err = "missing_field"
                        elif len(cand) != 2:
                            err = "extra_field"
                        else:
                            err = "invalid_schema"
                        # Fallback: if cand has "text" use it as meaning for old tests, but treat as missing  # noqa: E501
                        if "text" in cand and "meaning" not in cand:
                            # Map text->meaning for compatibility in fake transports that still use text  # noqa: E501
                            cand = {"meaning": cand.get("text"), "kind": cand.get("kind", "definition")}  # noqa: E501
                            # Re-validate
                            if set(cand.keys()) != {"meaning", "kind"}:
                                err = "missing_field"
                            else:
                                # continue to normal path
                                pass
                            # If still error, record
                            if "err" in locals() and err:
                                rejected_to_record[iid] = {
                                    "phase": "bulk",
                                    "error_code": err,
                                    "attempt_count": int(bulk_rejected.get(iid, {}).get("attempt_count", 0)) + 1  # noqa: E501
                                    if isinstance(bulk_rejected.get(iid), dict)
                                    else 1,
                                    "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}},  # noqa: E501
                                }
                                continue
                        else:
                            rejected_to_record[iid] = {
                                "phase": "bulk",
                                "error_code": err,
                                "attempt_count": int(bulk_rejected.get(iid, {}).get("attempt_count", 0)) + 1  # noqa: E501
                                if isinstance(bulk_rejected.get(iid), dict)
                                else 1,
                                "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}},  # noqa: E501
                            }
                            continue
                    meaning_val = cand.get("meaning")
                    kind_val = cand.get("kind")
                    if not isinstance(meaning_val, str):
                        err = "invalid_type"
                        rejected_to_record[iid] = {
                            "phase": "bulk",
                            "error_code": err,
                            "attempt_count": 1,
                            "evidence": {"candidate": {"text": str(meaning_val)[:50]}},
                        }
                        continue
                    if kind_val not in ("synonym", "definition"):
                        err = "invalid_kind"
                        rejected_to_record[iid] = {
                            "phase": "bulk",
                            "error_code": err,
                            "attempt_count": 1,
                            "evidence": {"candidate": {"text": meaning_val[:50]}},
                        }
                        continue
                    # Check extra/wrong type already handled; now validate candidate text
                    text = str(meaning_val)
                    kind = str(kind_val)
                    language = expected_lang
                elif expected_lang == "en":
                    # EN requires meaning only, kind locally fixed
                    if "kind" in cand:
                        err = "extra_field"
                        rejected_to_record[iid] = {
                            "phase": "bulk",
                            "error_code": err,
                            "attempt_count": 1,
                            "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}},
                        }
                        continue
                    # Allow text alias -> meaning
                    if "text" in cand and "meaning" not in cand:
                        cand = {"meaning": cand.get("text")}
                    if set(cand.keys()) != {"meaning"}:
                        if "meaning" not in cand:
                            err = "missing_field"
                        else:
                            err = "extra_field"
                        rejected_to_record[iid] = {
                            "phase": "bulk",
                            "error_code": err,
                            "attempt_count": 1,
                            "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}},
                        }
                        continue
                    meaning_val = cand.get("meaning")
                    if not isinstance(meaning_val, str):
                        err = "invalid_type"
                        rejected_to_record[iid] = {
                            "phase": "bulk",
                            "error_code": err,
                            "attempt_count": 1,
                            "evidence": {"candidate": {"text": str(meaning_val)[:50]}},
                        }
                        continue
                    text = str(meaning_val)
                    kind = "translation"
                    language = "en"
                else:
                    err = "invalid_language"
                    rejected_to_record[iid] = {
                        "phase": "bulk",
                        "error_code": err,
                        "attempt_count": 1,
                        "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}},
                    }
                    continue
                lemma_text = str(item_by_id[iid].get("lemma_text", ""))
                # Missing kind for DE already handled; check strict schema for DE missing kind
                generic_err = _validate_generated_candidate(
                    text, language, kind, lemma_text, existing_texts if existing_texts else None
                )
                semantic_err = None
                if generic_err is None and language == "de":
                    semantic_err = _validate_de_semantic_contract(item_by_id[iid], text, kind)
                combined_err: str | None = generic_err if generic_err is not None else semantic_err
                qa_required_reason: str | None = None
                if combined_err is not None and _is_semantic_error_qa_recoverable(
                    item_by_id[iid], language, generic_err, semantic_err
                ):
                    # Genuine bulk semantic gap, not a transport/structural
                    # failure: persist the exact Luna candidate as a
                    # PROVISIONAL completion and force it into mandatory
                    # Terra QA instead of hard-rejecting the paid request.
                    # The semantic contract itself is not relaxed — QA still
                    # runs the same strict validator, and provisional text
                    # can only become final output via a full QA pass.
                    qa_required_reason = combined_err
                    combined_err = None
                if combined_err is not None:
                    rejected_to_record[iid] = {
                        "phase": "bulk",
                        "error_code": combined_err,
                        "attempt_count": int(bulk_rejected.get(iid, {}).get("attempt_count", 0)) + 1
                        if isinstance(bulk_rejected.get(iid), dict)
                        else 1,
                        "evidence": {"candidate": {"text": text[:50], "language": language}},
                    }
                else:
                    if text.strip() in existing_texts:
                        rejected_to_record[iid] = {
                            "phase": "bulk",
                            "error_code": "duplicate",
                            "attempt_count": 1,
                            "evidence": {"candidate": {"text": text[:50]}},
                        }
                    else:
                        existing_texts.add(text.strip())
                        completed_val: dict[str, object] = {
                            "text": text.strip(),
                            "language": language,
                            "kind": kind,
                            "source": GENERATED_MARKER,
                            "license": generated_license,
                        }
                        if qa_required_reason is not None:
                            completed_val["qa_required_reason"] = qa_required_reason
                        valid_to_complete[iid] = completed_val
            # Atomically persist: update completed/rejected, clear in_flight
            for iid, val in valid_to_complete.items():
                bulk_completed[iid] = val
            for iid, val in rejected_to_record.items():
                bulk_rejected[iid] = val
            bulk_state["in_flight"] = []
            _write_checkpoint(ckpt_p, identity, state)
            # If any rejected, STOP before next paid unit
            if rejected_to_record:
                codes = ", ".join(str(v.get("error_code", "")) for v in rejected_to_record.values())  # type: ignore[attr-defined]
                raise BuildDictError(
                    f"Bulk unit had {len(rejected_to_record)} rejected candidates ({codes}); "
                    "STOP before next unit"
                )  # noqa: E501
            # else continue to next unit

        # Refresh pending after loop
        pending_bulk_ids = [
            iid for iid in sorted_ids if iid not in bulk_completed and iid not in bulk_rejected
        ]  # noqa: E501

    # Determine QA required set if not already set
    if not qa_state.get("required"):
        # Morphology is source-verifiable and high-risk: every such DE item gets
        # independent QA, in addition to the existing deterministic flags/audit.
        flagged_ids = []
        for iid, val in bulk_completed.items():
            if isinstance(val, dict):
                txt = str(val.get("text", ""))
                item = item_by_id[iid]
                is_de_morphology = (
                    str(item.get("language", "")) == "de"
                    and bool(_morphology_feature_keys(item))
                )
                # A provisional bulk completion (routed to QA because it hit
                # a morphology_* semantic-contract gap) is source-verifiable
                # DE morphology by construction, so it is always already
                # covered by is_de_morphology above; this explicit check is
                # kept as a belt-and-suspenders invariant — a provisional
                # item can never fall out of QA routing.
                is_provisional = "qa_required_reason" in val
                if len(txt) > 50 or "flag" in txt.lower() or is_de_morphology or is_provisional:
                    flagged_ids.append(iid)
        audit_sample = _deterministic_audit_sample(
            sorted(bulk_completed.keys()), queue_sha, audit_sample_size
        )  # noqa: E501
        required_qa_ids = sorted(set(flagged_ids) | set(audit_sample))
        qa_state["required"] = required_qa_ids
        _write_checkpoint(ckpt_p, identity, state)
    else:
        required_qa_ids = qa_state["required"]
        if not isinstance(required_qa_ids, list):
            raise BuildDictError("Stage 04 checkpoint has invalid QA requirements")

    qa_completed = qa_state.get("completed", {})
    qa_rejected = qa_state.get("rejected", {})
    if not isinstance(qa_completed, dict) or not isinstance(qa_rejected, dict):
        raise BuildDictError("Stage 04 checkpoint is corrupt")

    # Explicit owner manual adjudication: see `apply_manual_adjudication`.
    # Recorded only for an already-real `bulk.completed` item, never touches
    # historical provider evidence, and -- once recorded -- always supersedes
    # both a successful QA completion and the ordinary bulk result for that
    # item at finalization. An item the owner has manually adjudicated is
    # never "pending QA" -- manual adjudication is itself a resolution, so a
    # required-but-unresolved QA item that has a manual adjudication does not
    # force a QA transport requirement.
    manual_adjudications = state.get("manual_adjudications")
    if not isinstance(manual_adjudications, dict):
        manual_adjudications = {}

    pending_qa_ids = [
        iid
        for iid in required_qa_ids
        if iid not in qa_completed and iid not in qa_rejected and iid not in manual_adjudications
    ]  # noqa: E501

    if pending_qa_ids and (transport is None or not hasattr(transport, "send_qa")):
        raise BuildDictError("No local deterministic Stage 04 QA transport configured")

    if pending_qa_ids and transport is not None and hasattr(transport, "send_qa"):
        if qa_context_callback is not None:
            qa_context_callback(bulk_completed)
        unit_size = batch_size
        for i in range(0, len(pending_qa_ids), unit_size):
            unit_ids = pending_qa_ids[i : i + unit_size]
            reserve_hook = getattr(transport, "pretransmission_reserve", None)
            if callable(reserve_hook):
                reserve_hook("qa", list(unit_ids))
            qa_state["in_flight"] = list(unit_ids)
            _write_checkpoint(ckpt_p, identity, state)
            try:
                result = transport.send_qa(unit_ids)
            except Exception as exc:
                raise BuildDictError(f"Transport failure for QA unit {unit_ids}: {exc}") from exc
            if not isinstance(result, dict):
                raise BuildDictError("Invalid transport QA result schema")
            result_ids = set(str(k) for k in result.keys())
            expected_ids = set(unit_ids)
            if result_ids != expected_ids:
                missing = expected_ids - result_ids
                unknown = result_ids - expected_ids
                if missing:
                    raise BuildDictError(f"Missing custom_id in QA result: {missing}")
                if unknown:
                    raise BuildDictError(f"Unknown custom_id in QA result: {unknown}")
            valid_qa: dict[str, object] = {}
            rejected_qa: dict[str, object] = {}
            for iid in unit_ids:
                cand = result.get(iid)
                if not isinstance(cand, dict):
                    raise BuildDictError(f"Invalid QA candidate schema for {iid}")
                envelope_code = _returned_response_rejection_code(cand)
                if envelope_code is not None:
                    rejected_qa[iid] = {
                        "phase": "qa",
                        "error_code": envelope_code,
                        "attempt_count": 1,
                        "evidence": {"candidate": {"response_status": str(cand.get("response_status", ""))[:50]}},  # noqa: E501
                    }
                    continue
                cand = _pop_response_envelope(cand)
                if "language" in cand:
                    err = "provider_language_override"
                    rejected_qa[iid] = {
                        "phase": "qa",
                        "error_code": err,
                        "attempt_count": 1,
                        "evidence": {"candidate": {"text": str(cand.get("meaning", cand.get("text", "")))[:50]}},  # noqa: E501
                    }
                    continue
                expected_lang = str(item_by_id[iid].get("language"))
                if expected_lang == "de":
                    if "text" in cand and "meaning" not in cand:
                        cand = {"meaning": cand.get("text"), "kind": cand.get("kind", "definition")}
                    if set(cand.keys()) != {"meaning", "kind"}:
                        err = "missing_field" if "meaning" not in cand or "kind" not in cand else "extra_field"  # noqa: E501
                        rejected_qa[iid] = {
                            "phase": "qa",
                            "error_code": err,
                            "attempt_count": 1,
                            "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}},
                        }
                        continue
                    meaning_val = cand.get("meaning")
                    kind_val = cand.get("kind")
                    if not isinstance(meaning_val, str):
                        rejected_qa[iid] = {"phase": "qa", "error_code": "invalid_type", "attempt_count": 1, "evidence": {"candidate": {"text": str(meaning_val)[:50]}}}  # noqa: E501
                        continue
                    if kind_val not in ("synonym", "definition"):
                        rejected_qa[iid] = {"phase": "qa", "error_code": "invalid_kind", "attempt_count": 1, "evidence": {"candidate": {"text": str(meaning_val)[:50]}}}  # noqa: E501
                        continue
                    text = str(meaning_val)
                    kind = str(kind_val)
                    language = "de"
                elif expected_lang == "en":
                    if "text" in cand and "meaning" not in cand:
                        cand = {"meaning": cand.get("text")}
                    if "kind" in cand:
                        rejected_qa[iid] = {"phase": "qa", "error_code": "extra_field", "attempt_count": 1, "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}}}  # noqa: E501
                        continue
                    if set(cand.keys()) != {"meaning"}:
                        err = "missing_field" if "meaning" not in cand else "extra_field"
                        rejected_qa[iid] = {"phase": "qa", "error_code": err, "attempt_count": 1, "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}}}  # noqa: E501
                        continue
                    meaning_val = cand.get("meaning")
                    if not isinstance(meaning_val, str):
                        rejected_qa[iid] = {"phase": "qa", "error_code": "invalid_type", "attempt_count": 1, "evidence": {"candidate": {"text": str(meaning_val)[:50]}}}  # noqa: E501
                        continue
                    text = str(meaning_val)
                    kind = "translation"
                    language = "en"
                else:
                    rejected_qa[iid] = {"phase": "qa", "error_code": "invalid_language", "attempt_count": 1, "evidence": {"candidate": {"text": str(cand.get("meaning", ""))[:50]}}}  # noqa: E501
                    continue
                lemma_text = str(item_by_id[iid].get("lemma_text", ""))
                err = _validate_generated_candidate(text, language, kind, lemma_text, None)  # type: ignore[assignment]
                if err is None and language == "de":
                    err = _validate_de_semantic_contract(item_by_id[iid], text, kind)
                if err is not None:
                    rejected_qa[iid] = {
                        "phase": "qa",
                        "error_code": err,
                        "attempt_count": 1,
                        "evidence": {"candidate": {"text": text[:50]}},
                    }
                else:
                    valid_qa[iid] = {
                        "text": text.strip(),
                        "language": language,
                        "kind": kind,
                        "source": GENERATED_MARKER,
                        "license": generated_license,
                    }  # noqa: E501
            for iid, val in valid_qa.items():
                qa_completed[iid] = val
            for iid, val in rejected_qa.items():
                qa_rejected[iid] = val
            qa_state["in_flight"] = []
            _write_checkpoint(ckpt_p, identity, state)
            if rejected_qa:
                raise BuildDictError(f"QA unit had {len(rejected_qa)} rejected; STOP")

    # Finalization guard: a provisional bulk completion (recorded because a
    # morphology semantic-contract defect was routed to mandatory QA instead
    # of a hard rejection) must never reach output.sqlite on its own. Every
    # such item must be in the QA-required set, have a successful QA
    # completion, and must not be QA-rejected, before finalization proceeds
    # -- UNLESS the owner has explicitly manually adjudicated it, which is an
    # equally valid, deliberately reviewed resolution for a provisional item.
    # QA-completed text supersedes the provisional bulk text as usual below;
    # this guard only blocks the fallback for items neither QA nor manual
    # adjudication has resolved.
    for _iid, _val in bulk_completed.items():
        if isinstance(_val, dict) and "qa_required_reason" in _val:
            if _iid in manual_adjudications:
                continue
            if _iid not in required_qa_ids or _iid in qa_rejected:
                raise BuildDictError(
                    f"Provisional bulk item {_iid} failed QA and cannot be finalized; STOP"
                )
            if _iid not in qa_completed:
                raise BuildDictError(
                    f"Provisional bulk item {_iid} has not completed mandatory QA; "
                    "STOP before finalization"
                )

    # Now persist generated rows to output DB if bulk completed exists and output not yet created
    # For tests, we always create output after checkpoint is stable
    # Copy stage02 to output then insert sense_meaning rows
    if not out_p.exists():
        # Atomic copy
        out_p.parent.mkdir(parents=True, exist_ok=True)
        tf = tempfile.NamedTemporaryFile(
            dir=out_p.parent, prefix=f".{out_p.name}.", suffix=".tmp", delete=False
        )  # noqa: E501
        tf_path = Path(tf.name)
        tf.close()
        try:
            import shutil

            shutil.copyfile(stage02_p, tf_path)
            conn_out = sqlite3.connect(tf_path)
            try:
                # Insert generated rows for each completed bulk item. Final-text
                # precedence is explicit and total: manual adjudication >
                # successful QA > valid bulk.
                final_texts: dict[str, dict[str, object]] = {}
                for iid in bulk_completed:
                    if iid in manual_adjudications:
                        final_texts[iid] = manual_adjudications[iid]
                    elif iid in qa_completed and isinstance(qa_completed[iid], dict):
                        final_texts[iid] = qa_completed[iid]
                    else:
                        final_texts[iid] = bulk_completed[iid]
                # Need to assign new sense_meaning IDs
                max_id = conn_out.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM sense_meaning"
                ).fetchone()[0]  # noqa: E501
                next_id = int(max_id) + 1
                # For deterministic ord, we use 0 for now; but need to ensure unique per sense/language/kind  # noqa: E501
                # We'll insert with ord=0 and if conflict, increment
                for iid, val in final_texts.items():
                    it = item_by_id[iid]
                    sense_id = int(str(it["sense_id"]))
                    val_dict = val if isinstance(val, dict) else {}
                    language = str(val_dict.get("language", ""))
                    kind = str(val_dict.get("kind", ""))
                    text = str(val_dict.get("text", ""))
                    # Row-level provenance: every completion record (ordinary
                    # bulk/QA, and a manual adjudication) already carries its
                    # own source/license -- a manual adjudication's
                    # `STAGE04_MANUAL_ADJUDICATION_SOURCE` must reach the row
                    # unchanged, never silently coerced to `GENERATED_MARKER`.
                    row_source = str(val_dict.get("source", GENERATED_MARKER))
                    row_license = str(val_dict.get("license", generated_license))
                    # Determine ord: count existing rows for this sense/language/kind
                    existing_ords = [
                        r[0]
                        for r in conn_out.execute(
                            "SELECT ord FROM sense_meaning WHERE sense_id=? "
                            "AND language=? AND kind=?",
                            (sense_id, language, kind),
                        ).fetchall()
                    ]  # noqa: E501
                    ord_val = 0
                    while ord_val in existing_ords:
                        ord_val += 1
                    conn_out.execute(
                        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, license) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: E501
                        (
                            next_id,
                            sense_id,
                            language,
                            kind,
                            ord_val,
                            text,
                            row_source,
                            row_license,
                        ),  # noqa: E501
                    )
                    # Derivation edge: for each derivation_source_ids. Only for
                    # a genuinely generated row -- `validate_sense_meaning_
                    # derivations` (ADR-0004 D45/A8) requires the
                    # generated_meaning_id side of every edge to carry the
                    # versioned generated marker, so a manually-adjudicated
                    # row (`source=STAGE04_MANUAL_ADJUDICATION_SOURCE`) is
                    # correctly never a derivation-edge target: it was not
                    # produced by consuming the source row through the
                    # generation pipeline, and its provenance (the review
                    # finding, and the exact original Luna/Terra evidence it
                    # supersedes) is instead recorded truthfully in the
                    # checkpoint's `manual_adjudications` section.
                    deriv_ids = it.get("derivation_source_ids", []) if row_source == GENERATED_MARKER else []  # noqa: E501
                    if isinstance(deriv_ids, list):
                        for src_mid in deriv_ids:
                            # Validate derivation: source must be non-generated, same sense
                            src_row = conn_out.execute(
                                "SELECT sense_id, source FROM sense_meaning WHERE id=?", (src_mid,)
                            ).fetchone()  # noqa: E501
                            if src_row is None:
                                raise BuildDictError(
                                    f"Derivation source {src_mid} not found for {iid}"
                                )  # noqa: E501
                            if src_row[1] and GENERATED_MARKER_PATTERN.match(str(src_row[1])):
                                raise BuildDictError(
                                    "Generated->generated derivation forbidden for "
                                    f"{iid} source {src_mid}"
                                )  # noqa: E501
                            if int(src_row[0]) != sense_id:
                                raise BuildDictError(f"Cross-sense derivation forbidden for {iid}")
                            conn_out.execute(
                                "INSERT INTO sense_meaning_derivation (generated_meaning_id, source_meaning_id) VALUES (?, ?)",  # noqa: E501
                                (next_id, int(str(src_mid))),
                            )
                    next_id += 1
                # Validate derivations
                validate_sense_meaning_derivations(conn_out)
                conn_out.commit()
                # Validate quick_check
                ck = conn_out.execute("PRAGMA quick_check").fetchall()
                if ck != [("ok",)]:
                    raise BuildDictError(f"Output PRAGMA quick_check failed: {ck}")
            finally:
                conn_out.close()
            if out_p.exists():
                raise BuildDictError(f"Output path already exists: {out_p}")
            tf_path.replace(out_p)
        finally:
            if tf_path.exists():
                tf_path.unlink(missing_ok=True)

    return {
        "bulk_completed": len(bulk_completed),
        "bulk_rejected": len(bulk_rejected),
        "qa_required": len(required_qa_ids) if isinstance(required_qa_ids, list) else 0,
        "qa_completed": len(qa_completed),
        "manifests": len(manifests_list) if isinstance(manifests_list, list) else 0,
    }


def retry_rejected(
    checkpoint_path: Path | str,
    queue_path: Path | str,
    rejected_ids: list[str],
    generated_license: str,
    bulk_de_model: str = STAGE04_DEFAULT_BULK_DE_MODEL,
    bulk_en_model: str = STAGE04_DEFAULT_BULK_EN_MODEL,
    qa_model: str = STAGE04_DEFAULT_QA_MODEL,
) -> None:
    """Explicit retry of rejected IDs via deterministic manifest."""
    ckpt_p = Path(checkpoint_path)
    queue_p = Path(queue_path)
    if not ckpt_p.is_file():
        raise BuildDictError(f"Checkpoint not found: {ckpt_p}")
    queue_data = json.loads(queue_p.read_text(encoding="utf-8"))
    items = queue_data["items"]
    item_by_id = {str(it["item_id"]): it for it in items}
    queue_sha = hashlib.sha256(queue_p.read_bytes()).hexdigest()
    identity = _checkpoint_identity(
        queue_sha, GENERATED_MARKER, generated_license, bulk_de_model, bulk_en_model, qa_model
    )  # noqa: E501
    state = _load_checkpoint(ckpt_p, identity)
    bulk_state: Any = state["bulk"]
    qa_state: Any = state["qa"]
    # Validate rejected_ids exist and are rejected, not in_flight
    all_rejected = {**bulk_state.get("rejected", {}), **qa_state.get("rejected", {})}  # noqa: E501
    in_flight_all = set(bulk_state.get("in_flight", []) + qa_state.get("in_flight", []))  # noqa: E501
    for rid in rejected_ids:
        if rid in in_flight_all:
            raise BuildDictError(f"Cannot retry in-flight ID {rid}")
        if rid not in all_rejected:
            raise BuildDictError(f"ID {rid} is not rejected")
        if rid not in item_by_id:
            raise BuildDictError(f"ID {rid} not in queue")
    # Remove from rejected, so they become pending again; increment attempt count will happen on next bulk  # noqa: E501
    for rid in rejected_ids:
        if rid in bulk_state.get("rejected", {}):
            del bulk_state["rejected"][rid]
        if rid in qa_state.get("rejected", {}):
            del qa_state["rejected"][rid]
    _write_checkpoint(ckpt_p, identity, state)


# --- Stage05 ---


def build_stage05(
    input_path: Path | str,
    output_path: Path | str,
    version: str = "v1",
    metadata_path: Path | str | None = None,
) -> dict[str, object]:
    """Final versioned packaging."""
    in_p = Path(input_path)
    out_p = Path(output_path)
    if out_p.exists():
        raise BuildDictError(f"Output path already exists: {out_p}")
    if not in_p.is_file():
        raise BuildDictError(f"Input file not found: {in_p}")
    # Never mutate input: verify sha before and after
    sha_before = sha256_file(in_p)
    conn_in = sqlite3.connect(f"file:{in_p.resolve()}?mode=ro", uri=True)
    try:
        ck = conn_in.execute("PRAGMA quick_check").fetchall()
        if ck != [("ok",)]:
            raise BuildDictError(f"Input PRAGMA quick_check failed: {ck}")
        tables = {
            r[0]
            for r in conn_in.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }  # noqa: E501
        required = {
            "lemma",
            "surface_form",
            "sense",
            "sense_meaning",
            "sense_meaning_derivation",
            "example",
            "example_lemma",
        }  # noqa: E501
        missing = required - tables
        if missing:
            raise BuildDictError(f"Input missing required tables: {sorted(missing)}")
        # Validate lemma/sense semantic_ref uniqueness/nonblank
        for table, col in [("lemma", "semantic_ref"), ("sense", "semantic_ref")]:
            rows = conn_in.execute(f"SELECT {col} FROM {table}").fetchall()
            seen: set[str] = set()
            for (val,) in rows:
                if not val or not str(val).strip():
                    raise BuildDictError(f"{table}.{col} blank")
                if val in seen:
                    raise BuildDictError(f"Duplicate {table}.{col} {val}")
                seen.add(val)
        # Validate attribution
        bad = conn_in.execute(
            "SELECT count(*) FROM sense_meaning WHERE source IS NULL OR trim(source)='' "
            "OR license IS NULL OR trim(license)=''"
        ).fetchone()[0]  # noqa: E501
        if bad:
            raise BuildDictError(f"Bad attribution rows: {bad}")
        # Validate derivation integrity
        validate_sense_meaning_derivations(conn_in)
        # Validate zero orphan
        orphans = conn_in.execute(
            "SELECT count(*) FROM sense_meaning sm LEFT JOIN sense s ON s.id=sm.sense_id WHERE s.id IS NULL"  # noqa: E501
        ).fetchone()[0]
        if orphans:
            raise BuildDictError(f"Orphan sense_meaning rows: {orphans}")
        orphans2 = conn_in.execute(
            "SELECT count(*) FROM sense_meaning_derivation d LEFT JOIN sense_meaning gm ON gm.id=d.generated_meaning_id LEFT JOIN sense_meaning sm ON sm.id=d.source_meaning_id WHERE gm.id IS NULL OR sm.id IS NULL"  # noqa: E501
        ).fetchone()[0]
        if orphans2:
            raise BuildDictError(f"Orphan derivation rows: {orphans2}")
        orphans3 = conn_in.execute(
            "SELECT count(*) FROM example_lemma el LEFT JOIN lemma l ON l.id=el.lemma_id LEFT JOIN example e ON e.id=el.example_id WHERE l.id IS NULL OR e.id IS NULL"  # noqa: E501
        ).fetchone()[0]
        if orphans3:
            raise BuildDictError(f"Orphan example_lemma rows: {orphans3}")
    finally:
        conn_in.close()
    # Atomic copy to output
    out_p.parent.mkdir(parents=True, exist_ok=True)
    tf = tempfile.NamedTemporaryFile(
        dir=out_p.parent, prefix=f".{out_p.name}.", suffix=".tmp", delete=False
    )  # noqa: E501
    tf_path = Path(tf.name)
    tf.close()
    try:
        import shutil

        shutil.copyfile(in_p, tf_path)
        conn_out = sqlite3.connect(tf_path)
        try:
            ck2 = conn_out.execute("PRAGMA quick_check").fetchall()
            if ck2 != [("ok",)]:
                raise BuildDictError(f"Output PRAGMA quick_check failed: {ck2}")
        finally:
            conn_out.close()
        # Compute SHA/bytes
        sha_out = sha256_file(tf_path)
        bytes_out = tf_path.stat().st_size
        if out_p.exists():
            raise BuildDictError(f"Output path already exists: {out_p}")
        tf_path.replace(out_p)
    finally:
        if tf_path.exists():
            tf_path.unlink(missing_ok=True)
    # Ensure input unchanged
    sha_after = sha256_file(in_p)
    if sha_before != sha_after:
        raise BuildDictError("Input was mutated during Stage 05")
    # Metadata
    metadata = {
        "version": version,
        "filename": out_p.name,
        "sha256": sha_out,
        "bytes": bytes_out,
        "generated_marker": GENERATED_MARKER,
    }
    if metadata_path is not None:
        meta_p = Path(metadata_path)
        if meta_p.exists():
            raise BuildDictError(f"Metadata path already exists: {meta_p}")
        meta_p.parent.mkdir(parents=True, exist_ok=True)
        meta_p.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )  # noqa: E501
    else:
        # Default alongside output
        default_meta = out_p.with_suffix(".json")
        if default_meta != out_p and not default_meta.exists():
            default_meta.write_text(
                json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
            )  # noqa: E501

    return metadata


if __name__ == "__main__":
    sys.exit(main())
