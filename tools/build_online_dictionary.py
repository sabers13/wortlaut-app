"""Build the deterministic Online dictionary corpus from a verified Local asset.

This tool is the Slice 11 builder. It consumes a verified Local
``dictionary_vN.sqlite`` (the same validated PART-A asset the Local
provider opens), partitions every authoritative lemma/sense/example
across the fixed shard families, emits deterministic per-shard SQLite
files, an immutable membership filter, and a strict Online manifest.

Slice 11 uses this tool only against tiny test inputs. Production
execution is gated to Slice 13.

Determinism is total: identical verified input bytes and identical
configuration emit identical shards, identical filter bytes, and an
identical manifest digest. No timestamps, no random UUIDs, and no
non-deterministic SQLite iteration order affect the emitted bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.online_filter import BloomFilter
from app.online_manifest import (
    DEFAULT_DATASET_TOKEN,
    ENTRY_FAMILY_SIZE,
    EXAMPLE_FAMILY_SIZE,
    LOOKUP_FAMILY_SIZE,
    MANIFEST_SCHEMA_VERSION,
    SHARD_FAMILY_ENTRY,
    SHARD_FAMILY_EXAMPLE,
    SHARD_FAMILY_FILTER,
    SHARD_FAMILY_LOOKUP,
    ManifestAsset,
    OnlineManifest,
    TrustedDistribution,
    manifest_hash,
)
from app.routing import bucket256_v1, example_bucket


@dataclass(frozen=True)
class BuildInputs:
    """Inputs needed to build one Online corpus."""

    source_path: Path
    output_dir: Path
    dataset_token: str = DEFAULT_DATASET_TOKEN
    release_tag: str = "dictionary-online-v2"
    base_origin: str = "https://github.com"


# Stable local filename per shard family/bucket; the manifest pairs
# ``name`` with ``path`` (which is the relative URL path used by the
# Online distribution).
def _asset_name(family: str, bucket: int) -> str:
    return f"{family}-{bucket:03d}.sqlite"


def _asset_path(family: str, bucket: int) -> str:
    """Return the relative URL path used by the Online distribution."""
    return f"shards/{family}/{bucket:03d}.sqlite"


def _filter_name() -> str:
    return "membership-filter.bin"


def _filter_path() -> str:
    return "shards/membership-filter.bin"


def _sqlite_ascii_lower(value: str) -> str:
    """Reproduce SQLite built-in ``lower()`` for ASCII-oriented text."""
    return "".join(
        ch.lower() if "A" <= ch <= "Z" else ch for ch in value
    )


def _validate_local_input(source: Path) -> None:
    """Validate the Local input asset before consuming it."""
    if not source.exists():
        raise RuntimeError(f"source dictionary not found: {source}")
    if source.stat().st_size == 0:
        raise RuntimeError(f"source dictionary is empty: {source}")
    digest = sha256(source.read_bytes()).hexdigest()
    if digest != DEFAULT_DATASET_TOKEN:
        raise RuntimeError(
            f"source dictionary SHA-256 {digest} does not match expected "
            f"v2 dataset token {DEFAULT_DATASET_TOKEN}"
        )
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for required_table in (
            "lemma",
            "sense",
            "sense_meaning",
            "example",
            "example_lemma",
            "surface_form",
        ):
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (required_table,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"source dictionary is missing required table: {required_table}"
                )
    finally:
        connection.close()


def _open_destination(directory: Path) -> sqlite3.Connection:
    """Open a private temporary SQLite file for shard emission."""
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(suffix=".sqlite", dir=str(directory))
    path = Path(name)
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _atomic_install(source_path: Path, canonical_path: Path) -> None:
    """Atomically install a freshly-written shard into its canonical location."""
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, final_name = tempfile.mkstemp(
        suffix=".sqlite", dir=str(canonical_path.parent)
    )
    final_path = Path(final_name)
    try:
        with os.fdopen(descriptor, "wb") as out:
            out.write(source_path.read_bytes())
            out.flush()
            os.fsync(out.fileno())
        os.replace(final_path, canonical_path)
    finally:
        if final_path.exists():
            final_path.unlink(missing_ok=True)


def _install_blob(source_path: Path, canonical_path: Path) -> None:
    """Atomically install a non-SQLite shard (membership filter)."""
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, final_name = tempfile.mkstemp(
        suffix=".tmp", dir=str(canonical_path.parent)
    )
    final_path = Path(final_name)
    try:
        with os.fdopen(descriptor, "wb") as out:
            out.write(source_path.read_bytes())
            out.flush()
            os.fsync(out.fileno())
        os.replace(final_path, canonical_path)
    finally:
        if final_path.exists():
            final_path.unlink(missing_ok=True)


def _read_authoritative_lemmas(
    connection: sqlite3.Connection,
) -> list[tuple[Any, ...]]:
    """Return all authoritative lemmas in stable id order."""
    rows = connection.execute(
        "SELECT id, semantic_ref, lemma, pos, gender, freq_rank, plural, plural_none, "
        "genitive_sg, aux, separable, particle, reflexive, praesens_3sg, "
        "praeteritum_3sg, partizip_ii, governs, comparative, superlative, ipa, "
        "source, license "
        "FROM lemma ORDER BY id ASC"
    ).fetchall()
    return [tuple(r) for r in rows]


def _read_authoritative_senses(
    connection: sqlite3.Connection,
) -> list[tuple[int, int, str, str, str, int, str | None, str | None, str | None]]:
    """Return all senses in stable id order."""
    rows = connection.execute(
        "SELECT id, lemma_id, semantic_ref, source_namespace, source_ref, ord, "
        "register, source, license FROM sense ORDER BY id ASC"
    ).fetchall()
    return [
        (
            int(r[0]),
            int(r[1]),
            str(r[2]),
            str(r[3]),
            str(r[4]),
            int(r[5]),
            str(r[6]) if r[6] is not None else None,
            str(r[7]) if r[7] is not None else None,
            str(r[8]) if r[8] is not None else None,
        )
        for r in rows
    ]


def _read_authoritative_meanings(
    connection: sqlite3.Connection,
) -> list[tuple[int, int, str, str, int, str, str, str]]:
    """Return all sense_meaning rows in stable id order."""
    rows = connection.execute(
        "SELECT id, sense_id, language, kind, ord, text, source, license "
        "FROM sense_meaning ORDER BY id ASC"
    ).fetchall()
    return [
        (
            int(r[0]),
            int(r[1]),
            str(r[2]),
            str(r[3]),
            int(r[4]),
            str(r[5]),
            str(r[6]),
            str(r[7]),
        )
        for r in rows
    ]


def _read_authoritative_examples(
    connection: sqlite3.Connection,
) -> list[tuple[int, str, str | None, str | None, str | None, str | None, int | None, int]]:
    """Return all example rows in stable id order."""
    rows = connection.execute(
        "SELECT id, de, en, source, source_ref, license, token_count, has_proper "
        "FROM example ORDER BY id ASC"
    ).fetchall()
    return [
        (
            int(r[0]),
            str(r[1]),
            str(r[2]) if r[2] is not None else None,
            str(r[3]) if r[3] is not None else None,
            str(r[4]) if r[4] is not None else None,
            str(r[5]) if r[5] is not None else None,
            int(r[6]) if r[6] is not None else None,
            int(r[7]) if r[7] is not None else 0,
        )
        for r in rows
    ]


def _read_authoritative_surface_forms(
    connection: sqlite3.Connection,
) -> list[tuple[str, int]]:
    """Return all surface_form rows in stable form+lemma order."""
    rows = connection.execute(
        "SELECT form, lemma_id FROM surface_form ORDER BY form ASC, lemma_id ASC"
    ).fetchall()
    return [(str(r[0]), int(r[1])) for r in rows]


def _read_authoritative_example_lemma(
    connection: sqlite3.Connection,
) -> list[tuple[int, int]]:
    """Return all ``example_lemma`` rows in stable lemma+example order."""
    rows = connection.execute(
        "SELECT lemma_id, example_id FROM example_lemma ORDER BY lemma_id ASC, example_id ASC"
    ).fetchall()
    return [(int(r[0]), int(r[1])) for r in rows]


def _init_lookup_shard(connection: sqlite3.Connection) -> None:
    """Create the lookup-shard schema.

    The lookup shard carries:

    * the lemma rows the runtime predicate reads;
    * the surface_form rows used for surface-form lookup;
    * a ``sense_route`` table that resolves ``sense_ref -> lemma_ref`` in a
      single bucket-closed fetch. Every authoritative ``sense_ref`` is
      indexed by ``bucket256_v1(sense_ref)`` (the same lookup-family routing
      function used for lemma lookups), so the runtime never scans across
      families to recover the sense-to-lemma mapping.
    """
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS lemma (
          id           INTEGER PRIMARY KEY,
          semantic_ref TEXT NOT NULL,
          lemma        TEXT NOT NULL,
          pos          TEXT NOT NULL,
          gender       TEXT,
          freq_rank    INTEGER
        );
        CREATE INDEX IF NOT EXISTS ix_lookup_lemma ON lemma(lemma, pos, gender);
        CREATE INDEX IF NOT EXISTS ix_lookup_lower ON lemma(lower(lemma));
        CREATE TABLE IF NOT EXISTS surface_form (
          form      TEXT NOT NULL,
          lemma_id  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_lookup_surface ON surface_form(form);
        CREATE TABLE IF NOT EXISTS sense_route (
          sense_ref TEXT PRIMARY KEY,
          lemma_ref TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_lookup_sense_route_lemma ON sense_route(lemma_ref);
        """
    )


def _init_entry_shard(connection: sqlite3.Connection) -> None:
    """Create the entry-shard schema.

    The entry shard carries the lemma row, its senses, the localized
    meanings attached to those senses, the surface forms and the
    ``example_lemma`` join. It deliberately does **not** carry the
    authoritative ``example`` payload: example rows live in the 64-shard
    example family keyed by ``example.id % 64``. Carrying the full payload
    here would silently allow the runtime to bypass the example family.
    """
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS lemma (
          id             INTEGER PRIMARY KEY,
          semantic_ref   TEXT NOT NULL,
          lemma          TEXT NOT NULL,
          pos            TEXT NOT NULL,
          gender         TEXT,
          freq_rank      INTEGER,
          plural         TEXT,
          plural_none    INTEGER NOT NULL DEFAULT 0,
          genitive_sg    TEXT,
          aux            TEXT,
          separable      INTEGER NOT NULL DEFAULT 0,
          particle       TEXT,
          reflexive      INTEGER NOT NULL DEFAULT 0,
          praesens_3sg   TEXT,
          praeteritum_3sg TEXT,
          partizip_ii    TEXT,
          governs        TEXT,
          comparative    TEXT,
          superlative    TEXT,
          ipa            TEXT,
          source         TEXT,
          license        TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_entry_lemma_semantic ON lemma(semantic_ref);

        CREATE TABLE IF NOT EXISTS sense (
          id                INTEGER PRIMARY KEY,
          lemma_id          INTEGER NOT NULL,
          semantic_ref      TEXT NOT NULL,
          source_namespace  TEXT NOT NULL,
          source_ref        TEXT NOT NULL,
          ord               INTEGER NOT NULL DEFAULT 0,
          register          TEXT,
          source            TEXT,
          license           TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_entry_sense_semantic ON sense(semantic_ref);

        CREATE TABLE IF NOT EXISTS sense_meaning (
          id        INTEGER PRIMARY KEY,
          sense_id  INTEGER NOT NULL,
          language  TEXT,
          kind      TEXT,
          ord       INTEGER NOT NULL DEFAULT 0,
          text      TEXT,
          source    TEXT,
          license   TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_entry_meaning ON sense_meaning(sense_id, language);

        CREATE TABLE IF NOT EXISTS surface_form (
          form      TEXT NOT NULL,
          lemma_id  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_entry_surface ON surface_form(form);

        CREATE TABLE IF NOT EXISTS example_lemma (
          lemma_id   INTEGER NOT NULL,
          example_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_entry_example_lemma ON example_lemma(lemma_id);
        """
    )


def _init_example_shard(connection: sqlite3.Connection) -> None:
    """Create the example-shard schema."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS example (
          id           INTEGER PRIMARY KEY,
          de           TEXT NOT NULL,
          en           TEXT,
          source       TEXT,
          source_ref   TEXT,
          license      TEXT,
          token_count  INTEGER,
          has_proper   INTEGER NOT NULL DEFAULT 0
        );
        """
    )


def _compute_bucket_targets_for_lookup(
    *,
    lemma_text: str,
    lemma_id: int,
    surface_form_lookup: list[tuple[int, str]],
) -> set[int]:
    """Return the deduplicated lookup buckets for one authoritative lemma.

    Mirrors :func:`app.online_manifest.lookup_buckets_from_query` for
    both ``lemma`` and ``lower(lemma)``, plus the same closure for every
    recorded ``surface_form`` row tied to ``lemma_id``.
    """
    targets: set[int] = set()
    primary = bucket256_v1(lemma_text)
    targets.add(primary)
    secondary = bucket256_v1(_sqlite_ascii_lower(lemma_text))
    if secondary != primary:
        targets.add(secondary)
    for _, form in surface_form_lookup:
        if form == form.strip() and form:
            primary_form = bucket256_v1(form)
            targets.add(primary_form)
            secondary_form = bucket256_v1(_sqlite_ascii_lower(form))
            if secondary_form != primary_form:
                targets.add(secondary_form)
    return targets


def _validate_sense_route_partitions(
    *,
    senses: Sequence[tuple[int, int, str, str, str, int, str | None, str | None, str | None]],
    sense_route_partitions: Mapping[int, Sequence[tuple[str, str]]],
    lemma_refs_by_id: Mapping[int, str],
) -> None:
    """Validate that every authoritative ``sense_ref`` is bucket-closed.

    Each authoritative sense must appear in exactly one sense_route
    bucket, the bucket must equal ``bucket256_v1(sense_ref)`` (checked
    explicitly against the actual partition bucket, not assumed from the
    producer), and the routed ``lemma_ref`` must equal the lemma's
    authoritative ``semantic_ref``. Missing, extra, misrouted, or
    mis-bucketed sense_refs are fatal builder errors so the runtime can
    rely on the index.

    Runs in approximately linear time in the number of authoritative
    rows: two precomputed maps (``sense_owner_by_ref`` and the routed
    map) replace the previous nested full-list ``next()`` scans, which
    were O(S*L) and O(S^2) on the real corpus.
    """
    sense_owner_by_ref: dict[str, int] = {}
    for sense_id, lemma_id, sense_ref, _, _, _, _, _, _ in senses:
        if not isinstance(sense_ref, str) or not sense_ref:
            raise RuntimeError(f"sense_id={sense_id} has empty sense_ref")
        if sense_ref in sense_owner_by_ref:
            raise RuntimeError(f"duplicate sense_ref={sense_ref!r}")
        sense_owner_by_ref[sense_ref] = int(lemma_id)
    routed: dict[str, tuple[str, int]] = {}
    for bucket, rows in sense_route_partitions.items():
        for sense_ref, lemma_ref in rows:
            if sense_ref in routed:
                raise RuntimeError(
                    f"duplicate sense_route bucket={bucket} sense_ref={sense_ref!r}"
                )
            actual_bucket = bucket256_v1(str(sense_ref))
            if actual_bucket != int(bucket):
                raise RuntimeError(
                    f"sense_route sense_ref={sense_ref!r} partitioned into "
                    f"bucket {bucket} but bucket256_v1 gives {actual_bucket}"
                )
            routed[sense_ref] = (lemma_ref, int(bucket))
    missing = set(sense_owner_by_ref.keys()) - set(routed.keys())
    if missing:
        sample = sorted(missing)[:5]
        raise RuntimeError(
            f"sense_route missing for sense_refs: {sample} "
            f"(and {len(missing) - len(sample)} more)"
        )
    extra = set(routed.keys()) - set(sense_owner_by_ref.keys())
    if extra:
        sample = sorted(extra)[:5]
        raise RuntimeError(
            f"sense_route has unexpected sense_refs: {sample} "
            f"(and {len(extra) - len(sample)} more)"
        )
    for sense_ref, owner_lemma_id in sense_owner_by_ref.items():
        lemma_ref, expected_bucket = routed[sense_ref]
        if lemma_refs_by_id.get(owner_lemma_id) != lemma_ref:
            raise RuntimeError(
                f"sense_route bucket={expected_bucket} sense_ref={sense_ref!r} "
                f"routes to {lemma_ref!r} but authoritative lemma_ref is "
                f"{lemma_refs_by_id.get(owner_lemma_id)!r}"
            )


def _partition_lookup_shards(
    lemmas: Sequence[tuple[Any, ...]],
    surface_forms: Sequence[tuple[str, int]],
    senses: Sequence[tuple[int, int, str, str, str, int, str | None, str | None, str | None]],
) -> tuple[
    dict[int, list[tuple[Any, ...]]],
    dict[int, list[tuple[str, int]]],
    dict[int, list[tuple[str, str]]],
]:
    """Partition lookup-shard rows into 256 buckets using the closure rule.

    Returns:

    * ``lemma_partitions``: ``bucket -> lemma_rows`` placed for every
      authoritative lemma by ``bucket256_v1(X)`` union
      ``bucket256_v1(sqlite_ascii_lower(X))`` (plus the surface-form
      closure buckets, so the surface_form -> lemma join stays locally
      closed in every bucket).
    * ``surface_partitions``: ``bucket -> (form, lemma_id)`` rows where
      each authoritative surface row appears ONLY in the union of
      ``bucket256_v1(form)`` and
      ``bucket256_v1(sqlite_ascii_lower(form))`` (deduplicated) — never
      duplicated into all 256 buckets.
    * ``sense_route_partitions``: ``bucket -> (sense_ref, lemma_ref)`` rows
      indexed by ``bucket256_v1(sense_ref)``. The sense_route is the
      single bucket-closed routing table that replaces the cross-family
      scan the previous candidate used.

    The full lemma row tuple is preserved; the lookup-shard writer reads
    only the columns it needs.

    Runs in approximately linear time in the number of authoritative
    rows: ``lemma_ref_by_id`` is built once and sense ownership resolves
    by O(1) map lookup instead of nested full-list ``next()`` scans.
    """
    surface_by_lemma: dict[int, list[tuple[int, str]]] = {}
    for form, lemma_id in surface_forms:
        surface_by_lemma.setdefault(lemma_id, []).append((lemma_id, form))
    lemma_partitions: dict[int, list[tuple[Any, ...]]] = {}
    for row in lemmas:
        lemma_id = row[0]
        lemma_text = row[2]
        targets = _compute_bucket_targets_for_lookup(
            lemma_text=lemma_text,
            lemma_id=lemma_id,
            surface_form_lookup=surface_by_lemma.get(lemma_id, []),
        )
        for bucket in targets:
            lemma_partitions.setdefault(bucket, []).append(row)

    # Surface-form partitions: each distinct authoritative (form,
    # lemma_id) row lands only in its own closure buckets.
    surface_partitions: dict[int, list[tuple[str, int]]] = {}
    seen_surface_pairs: set[tuple[str, int]] = set()
    for form, lemma_id in surface_forms:
        pair = (str(form), int(lemma_id))
        if pair in seen_surface_pairs:
            continue
        seen_surface_pairs.add(pair)
        closure_buckets = {
            bucket256_v1(pair[0]),
            bucket256_v1(_sqlite_ascii_lower(pair[0])),
        }
        for bucket in closure_buckets:
            surface_partitions.setdefault(bucket, []).append(pair)

    lemma_ref_by_id: dict[int, str] = {
        int(row[0]): str(row[1]) for row in lemmas
    }
    sense_route_partitions: dict[int, list[tuple[str, str]]] = {}
    seen_routes: set[str] = set()
    for sense_row in senses:
        sense_id, lemma_id, sense_ref, _, _, _, _, _, _ = sense_row
        lemma_ref = lemma_ref_by_id.get(int(lemma_id))
        if lemma_ref is None:
            raise RuntimeError(
                f"sense_id={sense_id} references unknown lemma_id={lemma_id}"
            )
        bucket = bucket256_v1(str(sense_ref))
        if not 0 <= bucket < LOOKUP_FAMILY_SIZE:
            raise RuntimeError(
                f"sense_ref={sense_ref!r} routed to out-of-range bucket {bucket}"
            )
        if sense_ref in seen_routes:
            raise RuntimeError(f"duplicate sense_route for sense_ref={sense_ref!r}")
        seen_routes.add(str(sense_ref))
        sense_route_partitions.setdefault(bucket, []).append((str(sense_ref), lemma_ref))
    return lemma_partitions, surface_partitions, sense_route_partitions


def _partition_entry_shards(
    lemmas: Sequence[tuple[Any, ...]],
    senses: Sequence[tuple[int, int, str, str, str, int, str | None, str | None, str | None]],
    meanings: Sequence[tuple[int, int, str, str, int, str, str, str]],
    surface_forms: Sequence[tuple[str, int]],
    example_lemma: Sequence[tuple[int, int]],
    examples: Sequence[tuple[Any, ...]],
) -> dict[int, dict[str, list[Any]]]:
    """Partition lemma-driven rows by ``bucket256_v1(lemma_semantic_ref)``.

    The entry shard owns the lemma row, its senses, the meanings attached
    to those senses, the surface forms and the ``example_lemma`` join.
    It deliberately does **not** carry the example payload: example rows
    live in the example family keyed by ``example.id % 64``.

    Every ``example_lemma.example_id`` is validated against the
    authoritative example-id set before writing; a dangling reference is
    a fatal builder error. Example routing itself stays
    ``example.id % 64``.
    """
    buckets: dict[int, dict[str, list[Any]]] = {}
    lemma_bucket: dict[int, int] = {}
    for row in lemmas:
        lemma_id, semantic_ref = int(row[0]), str(row[1])
        bucket = bucket256_v1(semantic_ref)
        lemma_bucket[lemma_id] = bucket
        bucket_state = buckets.setdefault(bucket, {
            "lemmas": [],
            "senses": [],
            "meanings": [],
            "surface_forms": [],
            "example_lemma": [],
        })
        bucket_state["lemmas"].append(row)
    sense_by_id: dict[int, tuple[int, int]] = {}
    for row in senses:
        sense_id, lemma_id = int(row[0]), int(row[1])
        sense_by_id[sense_id] = (sense_id, lemma_id)
        sense_bucket_for_lemma = lemma_bucket.get(int(lemma_id))
        if sense_bucket_for_lemma is None:
            raise RuntimeError(f"sense references unknown lemma_id={lemma_id}")
        bucket_for_sense = sense_bucket_for_lemma
        bucket_state = buckets[bucket_for_sense]
        bucket_state["senses"].append(row)
    sense_bucket: dict[int, int] = {}
    for sense_id, pair in sense_by_id.items():
        sense_bucket[sense_id] = lemma_bucket[int(pair[1])]
    for row in meanings:
        sense_id = int(row[1])
        meaning_bucket = sense_bucket.get(int(sense_id))
        if meaning_bucket is None:
            raise RuntimeError(f"meaning references unknown sense_id={sense_id}")
        buckets[meaning_bucket]["meanings"].append(row)
    for form, lemma_id in surface_forms:
        surface_bucket = lemma_bucket.get(int(lemma_id))
        if surface_bucket is None:
            raise RuntimeError(f"surface_form references unknown lemma_id={lemma_id}")
        buckets[surface_bucket]["surface_forms"].append((form, lemma_id))
    example_ids = {int(row[0]) for row in examples}
    for lemma_id, example_id in example_lemma:
        el_bucket = lemma_bucket.get(int(lemma_id))
        if el_bucket is None:
            raise RuntimeError(
                f"example_lemma references unknown lemma_id={lemma_id}"
            )
        if int(example_id) not in example_ids:
            raise RuntimeError(
                f"example_lemma references unknown example_id={example_id}"
            )
        buckets[el_bucket]["example_lemma"].append((lemma_id, example_id))
    return buckets


def _partition_example_shards(
    examples: Sequence[
        tuple[int, str, str | None, str | None, str | None, str | None, int | None, int]
    ],
) -> dict[int, list[
    tuple[int, str, str | None, str | None, str | None, str | None, int | None, int]
]]:
    """Partition authoritative examples into 64 buckets by ``id % 64``."""
    buckets: dict[int, list[tuple[Any, ...]]] = {b: [] for b in range(EXAMPLE_FAMILY_SIZE)}
    for row in examples:
        bucket = example_bucket(int(row[0]))
        buckets[bucket].append(row)
    return buckets


def _write_lookup_shard(
    connection: sqlite3.Connection,
    bucket: int,
    lemma_rows: Sequence[tuple[int, str, str, str | None, int | None, str, str]],
    surface_rows: Sequence[tuple[str, int]],
    sense_route_rows: Sequence[tuple[str, str]] = (),
) -> None:
    """Populate a single lookup shard.

    The lookup shard carries the lemma rows the runtime predicate reads,
    ONLY the surface-form rows whose closure bucket is this bucket, and
    the ``sense_route`` rows whose ``sense_ref`` falls in
    ``bucket256_v1(sense_ref) == bucket``. The sense_route table is
    bucket-closed: every authoritative ``sense_ref`` appears in exactly
    one lookup shard and points to its real parent ``lemma_ref``.
    """
    _init_lookup_shard(connection)
    lemma_rows_sorted = sorted(
        lemma_rows,
        key=lambda r: (
            int(r[5]) if r[5] is not None else 1,
            str(r[3]),
            str(r[4]) if r[4] is not None else "",
            str(r[1]),
            int(r[0]),
        ),
    )
    connection.executemany(
        "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, freq_rank) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (r[0], r[1], r[2], r[3], r[4], r[5])
            for r in lemma_rows_sorted
        ],
    )
    seen: set[tuple[int, str]] = set()
    surface_rows_sorted = sorted(
        ((int(lemma_id), str(form)) for form, lemma_id in surface_rows)
    )
    ordered_surface_rows: list[tuple[str, int]] = []
    for lemma_id, form in surface_rows_sorted:
        key = (lemma_id, form)
        if key in seen:
            continue
        seen.add(key)
        ordered_surface_rows.append((form, lemma_id))
    connection.executemany(
        "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
        ordered_surface_rows,
    )
    if sense_route_rows:
        deduped_routes: dict[str, str] = {}
        for sense_ref, lemma_ref in sense_route_rows:
            if sense_ref in deduped_routes:
                if deduped_routes[sense_ref] != lemma_ref:
                    raise RuntimeError(
                        f"sense_route bucket {bucket} has conflicting "
                        f"lemma_ref for sense_ref={sense_ref!r}"
                    )
                continue
            deduped_routes[sense_ref] = lemma_ref
        connection.executemany(
            "INSERT INTO sense_route (sense_ref, lemma_ref) VALUES (?, ?)",
            [(sense_ref, lemma_ref) for sense_ref, lemma_ref in sorted(deduped_routes.items())],
        )
    connection.commit()
    connection.execute("VACUUM")
    connection.commit()


def _write_entry_shard(
    connection: sqlite3.Connection,
    bucket: int,
    lemma_rows: Sequence[tuple[Any, ...]],
    sense_rows: Sequence[tuple[Any, ...]],
    meaning_rows: Sequence[tuple[Any, ...]],
    surface_rows: Sequence[tuple[str, int]],
    example_lemma_rows: Sequence[tuple[int, int]],
) -> None:
    """Populate a single entry shard.

    The entry shard carries the lemma row, its senses, the meanings
    attached to those senses, the surface forms and the ``example_lemma``
    join. It does not carry the authoritative ``example`` payload: that
    lives in the example family keyed by ``example.id % 64``.
    """
    _init_entry_shard(connection)
    connection.executemany(
        "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, freq_rank, plural, "
        "plural_none, genitive_sg, aux, separable, particle, reflexive, praesens_3sg, "
        "praeteritum_3sg, partizip_ii, governs, comparative, superlative, ipa, source, "
        "license) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        lemma_rows,
    )
    connection.executemany(
        "INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, source_ref, ord, "
        "register, source, license) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        sense_rows,
    )
    connection.executemany(
        "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, source, "
        "license) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        meaning_rows,
    )
    connection.executemany(
        "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
        surface_rows,
    )
    connection.executemany(
        "INSERT INTO example_lemma (lemma_id, example_id) VALUES (?, ?)",
        example_lemma_rows,
    )
    connection.commit()
    connection.execute("VACUUM")
    connection.commit()


def _write_example_shard(
    connection: sqlite3.Connection,
    bucket: int,
    example_rows: Sequence[tuple[Any, ...]],
) -> None:
    """Populate a single example shard."""
    _init_example_shard(connection)
    connection.executemany(
        "INSERT INTO example (id, de, en, source, source_ref, license, token_count, "
        "has_proper) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        example_rows,
    )
    connection.commit()
    connection.execute("VACUUM")
    connection.commit()


def _validate_lookup_surface_closure(
    lemma_partitions: Mapping[int, Sequence[tuple[Any, ...]]],
    surface_partitions: Mapping[int, Sequence[tuple[str, int]]],
) -> None:
    """Prove the surface_form -> lemma join is locally closed per bucket.

    Every ``(form, lemma_id)`` surface row must share its bucket with the
    corresponding lemma row, so the runtime join never needs another
    bucket. Runs in approximately linear time in the partitioned rows.
    """
    for bucket, rows in surface_partitions.items():
        lemma_ids = {int(row[0]) for row in lemma_partitions.get(bucket, ())}
        for form, lemma_id in rows:
            if int(lemma_id) not in lemma_ids:
                raise RuntimeError(
                    f"surface_form {form!r} for lemma_id={lemma_id} in "
                    f"lookup bucket {bucket} has no lemma row in that bucket"
                )


def _canonicalize_blob(blob: bytes) -> bytes:
    """Return an exact blob unchanged; placeholder for future canonicalization."""
    return blob


# ---------------------------------------------------------------------------
# Disk-backed streaming builder.
#
# The in-memory implementation above keeps the entire authoritative corpus
# (≈1.1 M lemmas, ≈4.7 M surface_form rows, ≈480 K senses, ≈577 K meanings,
# ≈777 K examples, ≈6.5 M example_lemma rows) live as Python lists and
# dict-of-list partitions during the build. On a memory-constrained host
# that produces a multi-gigabyte resident working set and gets OOM-killed
# before any shard is written.
#
# The production Slice 13 builder keeps the same output semantics — every
# shard is byte-identical to what the in-memory version would have written —
# but never holds more than one bucket's worth of rows in Python at once.
# All intermediate partition state lives on disk in a private SQLite
# staging database keyed by family/bucket, and shards are emitted one at
# a time before the staging partition tables are dropped.
#
# The staging database lives at ``<output_dir>/.stage/staging.sqlite`` and
# is removed on success or failure. It is OUTSIDE the canonical corpus
# directory: the final corpus directory only contains the shard files,
# the membership filter, and the manifest.
# ---------------------------------------------------------------------------

_STAGING_SUBDIR: str = ".stage"
_STAGING_DB_NAME: str = "staging.sqlite"

# Cap the staging-DB page cache so a multi-gigabyte staging DB does not
# silently consume gigabytes of process RSS. The on-disk spill holds the
# working set; the cache is just an optimization.
_STAGING_CACHE_SIZE_PAGES: int = 16384  # ≈64 MiB at 4 KiB pages


def _open_staging(path: Path, *, fresh: bool = False) -> sqlite3.Connection:
    """Open the staging SQLite file with bounded cache + WAL mode.

    ``fresh=True`` removes any pre-existing file (used by the first
    pass of :func:`build_corpus`). Subsequent passes open the existing
    file in place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if fresh and path.exists():
        # Remove the WAL/SHM siblings so they cannot be replayed onto
        # the freshly recreated main DB file.
        for sibling in path.parent.glob(path.name + "*"):
            try:
                sibling.unlink()
            except OSError:
                pass
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(f"PRAGMA cache_size = -{_STAGING_CACHE_SIZE_PAGES}")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = FILE")
    return connection


def _stage_source(staging: sqlite3.Connection, source_path: Path) -> None:
    """Stream every authoritative PART-A row into the staging DB.

    The staging tables (``s_*``) hold one verbatim copy of every source
    row, in source ``id`` order where the source has one. Streaming
    row-by-row keeps the resident working set bounded: at most one source
    row and one staging-row INSERT are alive per iteration.
    """
    staging.executescript(
        """
        CREATE TABLE s_lemma (
          id INTEGER PRIMARY KEY,
          semantic_ref TEXT NOT NULL,
          lemma TEXT NOT NULL,
          pos TEXT NOT NULL,
          gender TEXT,
          freq_rank INTEGER,
          plural TEXT,
          plural_none INTEGER NOT NULL DEFAULT 0,
          genitive_sg TEXT,
          aux TEXT,
          separable INTEGER NOT NULL DEFAULT 0,
          particle TEXT,
          reflexive INTEGER NOT NULL DEFAULT 0,
          praesens_3sg TEXT,
          praeteritum_3sg TEXT,
          partizip_ii TEXT,
          governs TEXT,
          comparative TEXT,
          superlative TEXT,
          ipa TEXT,
          source TEXT,
          license TEXT
        );
        CREATE TABLE s_sense (
          id INTEGER PRIMARY KEY,
          lemma_id INTEGER NOT NULL,
          semantic_ref TEXT NOT NULL,
          source_namespace TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          ord INTEGER NOT NULL DEFAULT 0,
          register TEXT,
          source TEXT,
          license TEXT
        );
        CREATE TABLE s_meaning (
          id INTEGER PRIMARY KEY,
          sense_id INTEGER NOT NULL,
          language TEXT,
          kind TEXT,
          ord INTEGER NOT NULL DEFAULT 0,
          text TEXT,
          source TEXT,
          license TEXT
        );
        CREATE TABLE s_example (
          id INTEGER PRIMARY KEY,
          de TEXT NOT NULL,
          en TEXT,
          source TEXT,
          source_ref TEXT,
          license TEXT,
          token_count INTEGER,
          has_proper INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE s_example_lemma (
          lemma_id INTEGER NOT NULL,
          example_id INTEGER NOT NULL,
          PRIMARY KEY(lemma_id, example_id)
        );
        CREATE TABLE s_surface (
          form TEXT NOT NULL,
          lemma_id INTEGER NOT NULL
        );
        CREATE INDEX ix_s_sense_lemma_id ON s_sense(lemma_id);
        CREATE INDEX ix_s_meaning_sense_id ON s_meaning(sense_id);
        CREATE INDEX ix_s_surface_lemma ON s_surface(lemma_id);
        CREATE INDEX ix_s_example_lemma_example ON s_example_lemma(example_id);
        """
    )

    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro", uri=True
    )
    source.row_factory = sqlite3.Row
    try:
        # (table, column_count, sql)
        streams: tuple[tuple[str, int, str], ...] = (
            (
                "s_lemma",
                22,
                "SELECT id, semantic_ref, lemma, pos, gender, freq_rank, "
                "plural, plural_none, genitive_sg, aux, separable, particle, "
                "reflexive, praesens_3sg, praeteritum_3sg, partizip_ii, "
                "governs, comparative, superlative, ipa, source, license "
                "FROM lemma ORDER BY id ASC",
            ),
            (
                "s_sense",
                9,
                "SELECT id, lemma_id, semantic_ref, source_namespace, "
                "source_ref, ord, register, source, license "
                "FROM sense ORDER BY id ASC",
            ),
            (
                "s_meaning",
                8,
                "SELECT id, sense_id, language, kind, ord, text, source, "
                "license FROM sense_meaning ORDER BY id ASC",
            ),
            (
                "s_example",
                8,
                "SELECT id, de, en, source, source_ref, license, "
                "token_count, has_proper FROM example ORDER BY id ASC",
            ),
            (
                "s_example_lemma",
                2,
                "SELECT lemma_id, example_id FROM example_lemma "
                "ORDER BY lemma_id ASC, example_id ASC",
            ),
            (
                "s_surface",
                2,
                "SELECT form, lemma_id FROM surface_form "
                "ORDER BY form ASC, lemma_id ASC",
            ),
        )
        for table, ncols, sql in streams:
            insert_sql = (
                f"INSERT INTO {table} VALUES ({','.join('?' for _ in range(ncols))})"
            )
            count = 0
            for row in source.execute(sql):
                values = tuple(row[:ncols])
                if len(values) != ncols:
                    raise RuntimeError(
                        f"{table} row has {len(values)} columns, expected {ncols}"
                    )
                staging.execute(insert_sql, values)
                count += 1
                if count % 200000 == 0:
                    staging.commit()
            staging.commit()
    finally:
        source.close()


def _build_lookup_partitions(staging: sqlite3.Connection) -> None:
    """Derive the lookup-family partition tables from the staged source.

    Three lookup-family tables are produced, each keyed by
    ``bucket256_v1`` of the relevant text:

    * ``lookup_lemma_p`` — one row per ``(bucket, lemma_id)`` covering
      the lemma-text closure. The surface-form closure is folded in
      later (see :func:`_extend_lookup_lemma_p_with_surface_closure`).
    * ``lookup_surface_p`` — one row per ``(bucket, form, lemma_id)``
      covering the form-text closure. Authoritative surface rows are
      placed only in their own closure buckets, never duplicated.
    * ``lookup_sense_route_p`` — one row per ``(bucket, sense_ref)``
      carrying the ``sense_ref -> lemma_ref`` mapping the runtime uses
      before opening any entry shard.
    """
    staging.executescript(
        """
        CREATE TABLE lookup_lemma_p (
          bucket       INTEGER NOT NULL,
          id           INTEGER NOT NULL,
          semantic_ref TEXT NOT NULL,
          lemma        TEXT NOT NULL,
          pos          TEXT NOT NULL,
          gender       TEXT,
          freq_rank    INTEGER,
          PRIMARY KEY(bucket, id)
        );
        CREATE TABLE lookup_surface_p (
          bucket   INTEGER NOT NULL,
          form     TEXT NOT NULL,
          lemma_id INTEGER NOT NULL,
          PRIMARY KEY(bucket, form, lemma_id)
        );
        CREATE TABLE lookup_sense_route_p (
          bucket    INTEGER NOT NULL,
          sense_ref TEXT NOT NULL,
          lemma_ref TEXT NOT NULL,
          PRIMARY KEY(bucket, sense_ref)
        );
        CREATE INDEX ix_lookup_lemma_p_bucket ON lookup_lemma_p(bucket);
        CREATE INDEX ix_lookup_surface_p_bucket ON lookup_surface_p(bucket);
        CREATE INDEX ix_lookup_sense_route_p_bucket ON lookup_sense_route_p(bucket);
        """
    )

    # lookup_surface_p first: each authoritative (form, lemma_id) row
    # appears only in its own form-text closure buckets. The natural
    # PRIMARY KEY (bucket, form, lemma_id) deduplicates naturally.
    insert_surface_sql = (
        "INSERT OR IGNORE INTO lookup_surface_p (bucket, form, lemma_id) "
        "VALUES (?, ?, ?)"
    )
    for form, lemma_id in staging.execute(
        "SELECT form, lemma_id FROM s_surface ORDER BY form ASC, lemma_id ASC"
    ):
        for bucket in (
            bucket256_v1(str(form)),
            bucket256_v1(_sqlite_ascii_lower(str(form))),
        ):
            staging.execute(insert_surface_sql, (bucket, str(form), int(lemma_id)))

    # lookup_sense_route_p: every sense's bucket is bucket256_v1(sense_ref),
    # exactly one row per sense. Join s_sense -> s_lemma to recover the
    # routed lemma_ref from the authoritative ``semantic_ref``.
    insert_route_sql = (
        "INSERT INTO lookup_sense_route_p (bucket, sense_ref, lemma_ref) "
        "VALUES (?, ?, ?)"
    )
    for sense_id, sense_ref, lemma_ref in staging.execute(
        "SELECT s.id, s.semantic_ref, l.semantic_ref FROM s_sense s "
        "JOIN s_lemma l ON l.id = s.lemma_id ORDER BY s.id ASC"
    ):
        staging.execute(
            insert_route_sql, (bucket256_v1(str(sense_ref)), str(sense_ref), str(lemma_ref))
        )

    # lookup_lemma_p: seed from s_lemma's own closure on lemma_text, then
    # extend with surface-form closure in a second pass.
    insert_lemma_sql = (
        "INSERT OR IGNORE INTO lookup_lemma_p "
        "(bucket, id, semantic_ref, lemma, pos, gender, freq_rank) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    for row in staging.execute(
        "SELECT id, semantic_ref, lemma, pos, gender, freq_rank FROM s_lemma "
        "ORDER BY id ASC"
    ):
        lemma_id = int(row[0])
        sem_ref = str(row[1])
        lemma_text = str(row[2])
        pos = str(row[3])
        gender = row[4]
        freq_rank = row[5]
        primary = bucket256_v1(lemma_text)
        secondary = bucket256_v1(_sqlite_ascii_lower(lemma_text))
        row_tuple = (lemma_id, sem_ref, lemma_text, pos, gender, freq_rank)
        staging.execute(insert_lemma_sql, (primary,) + row_tuple)
        if secondary != primary:
            staging.execute(insert_lemma_sql, (secondary,) + row_tuple)

    # Surface-form closure: for each authoritative lemma, every bucket of
    # every tied surface_form is added to the lemma's lookup placement.
    # Process the surface_form rows in a streaming pass; the per-row
    # Python state stays bounded.
    surface_by_lemma: dict[int, list[str]] = {}
    SURFACE_FLUSH_THRESHOLD = 50000
    for form, lemma_id in staging.execute(
        "SELECT form, lemma_id FROM s_surface ORDER BY lemma_id ASC, form ASC"
    ):
        surface_by_lemma.setdefault(int(lemma_id), []).append(str(form))
        if len(surface_by_lemma) >= SURFACE_FLUSH_THRESHOLD:
            _flush_lookup_lemma_p_with_surface(staging, surface_by_lemma)
            surface_by_lemma.clear()
    if surface_by_lemma:
        _flush_lookup_lemma_p_with_surface(staging, surface_by_lemma)

    staging.commit()


def _flush_lookup_lemma_p_with_surface(
    staging: sqlite3.Connection,
    surface_by_lemma: dict[int, list[str]],
) -> None:
    """Insert surface-form closure buckets for a batch of lemmas.

    For every lemma in ``surface_by_lemma`` we add all
    ``bucket256_v1(form)`` and ``bucket256_v1(lower(form))`` buckets to
    the lemma's lookup placement. Lemma rows are read once and joined in
    Python; the staging DB only sees the resulting bucket closures.
    """
    insert_sql = (
        "INSERT OR IGNORE INTO lookup_lemma_p "
        "(bucket, id, semantic_ref, lemma, pos, gender, freq_rank) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    lemma_ids = sorted(surface_by_lemma.keys())
    placeholder = ",".join("?" for _ in lemma_ids)
    rows = list(
        staging.execute(
            "SELECT id, semantic_ref, lemma, pos, gender, freq_rank FROM s_lemma "
            f"WHERE id IN ({placeholder})",
            tuple(lemma_ids),
        )
    )
    by_id = {int(r[0]): r for r in rows}
    for lemma_id in lemma_ids:
        row = by_id.get(lemma_id)
        if row is None:
            raise RuntimeError(
                f"surface_form references unknown lemma_id={lemma_id}"
            )
        sem_ref = str(row[1])
        lemma_text = str(row[2])
        pos = str(row[3])
        gender = row[4]
        freq_rank = row[5]
        closure_buckets: set[int] = set()
        for form in surface_by_lemma[lemma_id]:
            closure_buckets.add(bucket256_v1(form))
            closure_buckets.add(bucket256_v1(_sqlite_ascii_lower(form)))
        for bucket in closure_buckets:
            staging.execute(
                insert_sql, (bucket, lemma_id, sem_ref, lemma_text, pos, gender, freq_rank)
            )
    staging.commit()


def _build_entry_partitions(staging: sqlite3.Connection) -> None:
    """Derive the entry-family partition tables from the staged source.

    Every entry-bucket is keyed by ``bucket256_v1(lemma.semantic_ref)``.
    Senses, meanings, surface_forms and example_lemma rows follow the
    bucket of their parent lemma; ``example`` rows are routed purely by
    ``example.id % 64`` (handled in :func:`_build_example_partitions`).
    """
    staging.executescript(
        """
        CREATE TABLE lemma_bucket_map (
          lemma_id INTEGER PRIMARY KEY,
          bucket   INTEGER NOT NULL
        );
        CREATE TABLE entry_lemma_p (
          bucket INTEGER NOT NULL,
          id INTEGER PRIMARY KEY,
          semantic_ref TEXT NOT NULL,
          lemma TEXT NOT NULL,
          pos TEXT NOT NULL,
          gender TEXT,
          freq_rank INTEGER,
          plural TEXT,
          plural_none INTEGER NOT NULL DEFAULT 0,
          genitive_sg TEXT,
          aux TEXT,
          separable INTEGER NOT NULL DEFAULT 0,
          particle TEXT,
          reflexive INTEGER NOT NULL DEFAULT 0,
          praesens_3sg TEXT,
          praeteritum_3sg TEXT,
          partizip_ii TEXT,
          governs TEXT,
          comparative TEXT,
          superlative TEXT,
          ipa TEXT,
          source TEXT,
          license TEXT
        );
        CREATE INDEX ix_entry_lemma_p_bucket ON entry_lemma_p(bucket);
        CREATE TABLE entry_sense_p (
          bucket INTEGER NOT NULL,
          id INTEGER PRIMARY KEY,
          lemma_id INTEGER NOT NULL,
          semantic_ref TEXT NOT NULL,
          source_namespace TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          ord INTEGER NOT NULL DEFAULT 0,
          register TEXT,
          source TEXT,
          license TEXT
        );
        CREATE INDEX ix_entry_sense_p_bucket ON entry_sense_p(bucket);
        CREATE TABLE entry_meaning_p (
          bucket INTEGER NOT NULL,
          id INTEGER PRIMARY KEY,
          sense_id INTEGER NOT NULL,
          language TEXT,
          kind TEXT,
          ord INTEGER NOT NULL DEFAULT 0,
          text TEXT,
          source TEXT,
          license TEXT
        );
        CREATE INDEX ix_entry_meaning_p_bucket ON entry_meaning_p(bucket);
        CREATE TABLE entry_surface_p (
          bucket INTEGER NOT NULL,
          form TEXT NOT NULL,
          lemma_id INTEGER NOT NULL,
          PRIMARY KEY(bucket, form, lemma_id)
        );
        CREATE INDEX ix_entry_surface_p_bucket ON entry_surface_p(bucket);
        CREATE TABLE entry_example_lemma_p (
          bucket INTEGER NOT NULL,
          lemma_id INTEGER NOT NULL,
          example_id INTEGER NOT NULL,
          PRIMARY KEY(bucket, lemma_id, example_id)
        );
        CREATE INDEX ix_entry_example_lemma_p_bucket
          ON entry_example_lemma_p(bucket);
        """
    )

    # Seed lemma_bucket_map and entry_lemma_p from the authoritative lemmas.
    insert_lemma_bucket_sql = (
        "INSERT INTO lemma_bucket_map (lemma_id, bucket) VALUES (?, ?)"
    )
    insert_lemma_p_sql = (
        "INSERT INTO entry_lemma_p "
        "(bucket, id, semantic_ref, lemma, pos, gender, freq_rank, plural, "
        "plural_none, genitive_sg, aux, separable, particle, reflexive, "
        "praesens_3sg, praeteritum_3sg, partizip_ii, governs, comparative, "
        "superlative, ipa, source, license) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    for row in staging.execute(
        "SELECT id, semantic_ref, lemma, pos, gender, freq_rank, plural, "
        "plural_none, genitive_sg, aux, separable, particle, reflexive, "
        "praesens_3sg, praeteritum_3sg, partizip_ii, governs, comparative, "
        "superlative, ipa, source, license FROM s_lemma ORDER BY id ASC"
    ):
        lemma_id = int(row[0])
        sem_ref = str(row[1])
        bucket = bucket256_v1(sem_ref)
        staging.execute(insert_lemma_bucket_sql, (lemma_id, bucket))
        staging.execute(insert_lemma_p_sql, (bucket,) + tuple(row))

    # Senses follow their lemma's bucket.
    insert_sense_p_sql = (
        "INSERT INTO entry_sense_p "
        "(bucket, id, lemma_id, semantic_ref, source_namespace, source_ref, "
        "ord, register, source, license) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    for row in staging.execute(
        "SELECT s.id, s.lemma_id, s.semantic_ref, s.source_namespace, "
        "s.source_ref, s.ord, s.register, s.source, s.license, m.bucket "
        "FROM s_sense s JOIN lemma_bucket_map m ON m.lemma_id = s.lemma_id "
        "ORDER BY s.id ASC"
    ):
        sense_id, lemma_id, sense_ref, ns, src_ref, ord_, register, src, lic, bucket = row
        staging.execute(
            insert_sense_p_sql,
            (int(bucket), int(sense_id), int(lemma_id), str(sense_ref),
             str(ns), str(src_ref), int(ord_), register, src, lic),
        )

    # Meanings follow their sense's lemma's bucket.
    insert_meaning_p_sql = (
        "INSERT INTO entry_meaning_p "
        "(bucket, id, sense_id, language, kind, ord, text, source, license) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    for row in staging.execute(
        "SELECT sm.id, sm.sense_id, sm.language, sm.kind, sm.ord, sm.text, "
        "sm.source, sm.license, m.bucket "
        "FROM s_meaning sm "
        "JOIN s_sense s ON s.id = sm.sense_id "
        "JOIN lemma_bucket_map m ON m.lemma_id = s.lemma_id "
        "ORDER BY sm.id ASC"
    ):
        meaning_id, sense_id, lang, kind, ord_, text, src, lic, bucket = row
        staging.execute(
            insert_meaning_p_sql,
            (int(bucket), int(meaning_id), int(sense_id), lang, kind,
             int(ord_), text, src, lic),
        )

    # Surface forms follow their lemma's bucket.
    insert_surface_p_sql = (
        "INSERT OR IGNORE INTO entry_surface_p (bucket, form, lemma_id) "
        "VALUES (?, ?, ?)"
    )
    for row in staging.execute(
        "SELECT s.form, s.lemma_id, m.bucket FROM s_surface s "
        "JOIN lemma_bucket_map m ON m.lemma_id = s.lemma_id "
        "ORDER BY s.lemma_id ASC, s.form ASC"
    ):
        form, lemma_id, bucket = row
        staging.execute(
            insert_surface_p_sql, (int(bucket), str(form), int(lemma_id))
        )

    # example_lemma rows follow their lemma's bucket.
    insert_el_sql = (
        "INSERT INTO entry_example_lemma_p (bucket, lemma_id, example_id) "
        "VALUES (?, ?, ?)"
    )
    for row in staging.execute(
        "SELECT el.lemma_id, el.example_id, m.bucket FROM s_example_lemma el "
        "JOIN lemma_bucket_map m ON m.lemma_id = el.lemma_id "
        "ORDER BY el.lemma_id ASC, el.example_id ASC"
    ):
        lemma_id, example_id, bucket = row
        staging.execute(
            insert_el_sql, (int(bucket), int(lemma_id), int(example_id))
        )

    staging.commit()


def _build_example_partitions(staging: sqlite3.Connection) -> None:
    """Derive the example-family partition table from the staged source."""
    staging.executescript(
        """
        CREATE TABLE example_p (
          bucket      INTEGER NOT NULL,
          id          INTEGER PRIMARY KEY,
          de          TEXT NOT NULL,
          en          TEXT,
          source      TEXT,
          source_ref  TEXT,
          license     TEXT,
          token_count INTEGER,
          has_proper  INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX ix_example_p_bucket ON example_p(bucket);
        """
    )
    insert_sql = (
        "INSERT INTO example_p (bucket, id, de, en, source, source_ref, "
        "license, token_count, has_proper) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    for row in staging.execute(
        "SELECT id, de, en, source, source_ref, license, token_count, "
        "has_proper FROM s_example ORDER BY id ASC"
    ):
        example_id = int(row[0])
        bucket = example_bucket(example_id)
        staging.execute(insert_sql, (bucket,) + tuple(row))
    staging.commit()


def _build_closure_keys(staging: sqlite3.Connection) -> None:
    """Stage the deduped Bloom-filter closure keys on disk.

    Each authoritative lemma ``X`` produces both ``X`` and
    ``sqlite_ascii_lower(X)`` as candidate keys; PRIMARY KEY on the
    ``key`` column deduplicates the pair so the count and the iteration
    downstream only see each unique variant once.
    """
    staging.executescript(
        """
        CREATE TABLE closure_keys (
          key TEXT PRIMARY KEY
        );
        """
    )
    insert_sql = "INSERT OR IGNORE INTO closure_keys (key) VALUES (?)"
    for row in staging.execute(
        "SELECT lemma FROM s_lemma ORDER BY id ASC"
    ):
        text = str(row[0])
        staging.execute(insert_sql, (text,))
        staging.execute(insert_sql, (_sqlite_ascii_lower(text),))
    staging.commit()


def _build_bloom_filter(staging: sqlite3.Connection) -> bytes:
    """Stream the staged closure keys through a Bloom filter.

    The filter is sized from the actual deduplicated closure-key count
    via :func:`app.online_filter.bloom_size_bits`, matching the ADR-0009
    sizing rule. The closure keys themselves never leave the staging DB;
    only the small bit array lives in Python memory.
    """
    (n,) = staging.execute("SELECT COUNT(*) FROM closure_keys").fetchone()
    if n <= 0:
        raise RuntimeError("no closure keys staged for bloom filter")
    size_bits = int(
        staging.execute(
            "SELECT ?", (n,)
        ).fetchone()[0]  # placeholder; replaced below
    )
    # Direct compute via the BloomFilter module (avoids importing math here).
    from app.online_filter import _positions, bloom_hash_count, bloom_size_bits

    size_bits = bloom_size_bits(n)
    hash_count = bloom_hash_count(n, size_bits)
    bits = bytearray(size_bits // 8)
    for (key,) in staging.execute("SELECT key FROM closure_keys ORDER BY key ASC"):
        for pos in _positions(str(key), size_bits, hash_count):
            bits[pos // 8] |= 1 << (pos % 8)
    return BloomFilter(bits=bits, size_bits=size_bits, hash_count=hash_count).to_bytes()


def _validate_lookup_partitions(staging: sqlite3.Connection) -> None:
    """Confirm the lookup-family partitions are bucket-closed and consistent."""
    # Every authoritative sense_ref appears in exactly one lookup bucket
    # with a lemma_ref that matches the authoritative lemma_ref.
    missing_route = list(
        staging.execute(
            "SELECT s.semantic_ref FROM s_sense s "
            "LEFT JOIN lookup_sense_route_p p ON p.sense_ref = s.semantic_ref "
            "WHERE p.sense_ref IS NULL "
            "ORDER BY s.semantic_ref ASC LIMIT 5"
        )
    )
    if missing_route:
        sample = [str(r[0]) for r in missing_route]
        raise RuntimeError(
            f"lookup_sense_route_p missing for sense_refs: {sample} "
            f"(showing {len(sample)} of potentially more)"
        )

    extra_route = list(
        staging.execute(
            "SELECT p.sense_ref FROM lookup_sense_route_p p "
            "LEFT JOIN s_sense s ON s.semantic_ref = p.sense_ref "
            "WHERE s.semantic_ref IS NULL "
            "ORDER BY p.sense_ref ASC LIMIT 5"
        )
    )
    if extra_route:
        sample = [str(r[0]) for r in extra_route]
        raise RuntimeError(
            f"lookup_sense_route_p has unexpected sense_refs: {sample}"
        )

    bad_route = list(
        staging.execute(
            "SELECT p.sense_ref, p.lemma_ref, l.semantic_ref "
            "FROM lookup_sense_route_p p "
            "JOIN s_sense s ON s.semantic_ref = p.sense_ref "
            "JOIN s_lemma l ON l.id = s.lemma_id "
            "WHERE p.lemma_ref != l.semantic_ref "
            "ORDER BY p.sense_ref ASC LIMIT 5"
        )
    )
    if bad_route:
        bad_route_sample: list[tuple[str, str, str]] = [
            (str(r[0]), str(r[1]), str(r[2])) for r in bad_route
        ]
        raise RuntimeError(
            f"lookup_sense_route_p routed lemma_ref mismatch: {bad_route_sample}"
        )

    # Every (form, lemma_id) in lookup_surface_p shares a bucket with
    # the corresponding lemma row in lookup_lemma_p.
    bad_surface = list(
        staging.execute(
            "SELECT DISTINCT p.bucket, p.form, p.lemma_id FROM lookup_surface_p p "
            "LEFT JOIN lookup_lemma_p l "
            "  ON l.bucket = p.bucket AND l.id = p.lemma_id "
            "WHERE l.id IS NULL LIMIT 5"
        )
    )
    if bad_surface:
        bad_surface_sample: list[tuple[int, str, int]] = [
            (int(r[0]), str(r[1]), int(r[2])) for r in bad_surface
        ]
        raise RuntimeError(
            f"lookup_surface_p missing lemma bucket-closure: {bad_surface_sample}"
        )


def _validate_entry_partitions(staging: sqlite3.Connection) -> None:
    """Confirm the entry-family partitions are bucket-closed and consistent."""
    # Every authoritative lemma_id is mapped exactly once.
    missing_lemma = list(
        staging.execute(
            "SELECT id FROM s_lemma l "
            "LEFT JOIN lemma_bucket_map m ON m.lemma_id = l.id "
            "WHERE m.lemma_id IS NULL "
            "ORDER BY l.id ASC LIMIT 5"
        )
    )
    if missing_lemma:
        sample = [int(r[0]) for r in missing_lemma]
        raise RuntimeError(
            f"entry lemma_bucket_map missing for lemma_ids: {sample}"
        )

    extra_lemma = list(
        staging.execute(
            "SELECT lemma_id FROM lemma_bucket_map m "
            "LEFT JOIN s_lemma l ON l.id = m.lemma_id "
            "WHERE l.id IS NULL LIMIT 5"
        )
    )
    if extra_lemma:
        sample = [int(r[0]) for r in extra_lemma]
        raise RuntimeError(
            f"entry lemma_bucket_map has unexpected lemma_ids: {sample}"
        )

    # Every authoritative sense is present in exactly one entry bucket.
    missing_sense = list(
        staging.execute(
            "SELECT s.id FROM s_sense s "
            "LEFT JOIN entry_sense_p e ON e.id = s.id "
            "WHERE e.id IS NULL "
            "ORDER BY s.id ASC LIMIT 5"
        )
    )
    if missing_sense:
        sample = [int(r[0]) for r in missing_sense]
        raise RuntimeError(f"entry_sense_p missing for sense_ids: {sample}")

    # Every authoritative meaning is present in exactly one entry bucket.
    missing_meaning = list(
        staging.execute(
            "SELECT sm.id FROM s_meaning sm "
            "LEFT JOIN entry_meaning_p e ON e.id = sm.id "
            "WHERE e.id IS NULL "
            "ORDER BY sm.id ASC LIMIT 5"
        )
    )
    if missing_meaning:
        sample = [int(r[0]) for r in missing_meaning]
        raise RuntimeError(f"entry_meaning_p missing for meaning_ids: {sample}")

    # Every authoritative example_lemma row is present.
    missing_el = list(
        staging.execute(
            "SELECT el.lemma_id, el.example_id FROM s_example_lemma el "
            "LEFT JOIN entry_example_lemma_p e "
            "  ON e.lemma_id = el.lemma_id AND e.example_id = el.example_id "
            "WHERE e.lemma_id IS NULL LIMIT 5"
        )
    )
    if missing_el:
        missing_el_sample: list[tuple[int, int]] = [
            (int(r[0]), int(r[1])) for r in missing_el
        ]
        raise RuntimeError(
            f"entry_example_lemma_p missing for (lemma_id, example_id): {missing_el_sample}"
        )

    # Every example_lemma references an authoritative example_id.
    bad_el = list(
        staging.execute(
            "SELECT DISTINCT e.example_id FROM entry_example_lemma_p e "
            "LEFT JOIN s_example x ON x.id = e.example_id "
            "WHERE x.id IS NULL LIMIT 5"
        )
    )
    if bad_el:
        sample = [int(r[0]) for r in bad_el]
        raise RuntimeError(
            f"entry_example_lemma_p references unknown example_ids: {sample}"
        )


def _validate_example_partitions(staging: sqlite3.Connection) -> None:
    """Confirm every authoritative example is bucket-closed in example_p."""
    missing_example = list(
        staging.execute(
            "SELECT x.id FROM s_example x "
            "LEFT JOIN example_p e ON e.id = x.id "
            "WHERE e.id IS NULL "
            "ORDER BY x.id ASC LIMIT 5"
        )
    )
    if missing_example:
        sample = [int(r[0]) for r in missing_example]
        raise RuntimeError(f"example_p missing for example_ids: {sample}")


def _emit_lookup_shard_from_staging(
    staging: sqlite3.Connection,
    shard_path: Path,
    bucket: int,
) -> None:
    """Emit one lookup shard from the staging partitions.

    The lemma, surface and sense_route rows for this bucket are read
    from staging, sorted in the canonical order, and inserted into a
    fresh shard SQLite connection. ``VACUUM`` then canonicalises the
    on-disk bytes — the same VACUUM the in-memory builder used.
    """
    connection = sqlite3.connect(shard_path)
    connection.row_factory = sqlite3.Row
    try:
        _init_lookup_shard(connection)
        lemma_rows = [
            (
                int(r[0]),
                str(r[1]),
                str(r[2]),
                str(r[3]),
                r[4],
                int(r[5]) if r[5] is not None else None,
            )
            for r in staging.execute(
                "SELECT id, semantic_ref, lemma, pos, gender, freq_rank "
                "FROM lookup_lemma_p WHERE bucket = ? "
                "ORDER BY "
                "  CASE WHEN freq_rank IS NULL THEN 1 ELSE 0 END, "
                "  freq_rank, pos, COALESCE(gender, ''), semantic_ref, id",
                (bucket,),
            )
        ]
        if lemma_rows:
            connection.executemany(
                "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, "
                "freq_rank) VALUES (?, ?, ?, ?, ?, ?)",
                lemma_rows,
            )

        surface_rows = [
            (str(r[0]), int(r[1]))
            for r in staging.execute(
                "SELECT form, lemma_id FROM lookup_surface_p WHERE bucket = ? "
                "ORDER BY lemma_id ASC, form ASC",
                (bucket,),
            )
        ]
        if surface_rows:
            connection.executemany(
                "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
                surface_rows,
            )

        route_rows = [
            (str(r[0]), str(r[1]))
            for r in staging.execute(
                "SELECT sense_ref, lemma_ref FROM lookup_sense_route_p "
                "WHERE bucket = ? ORDER BY sense_ref ASC",
                (bucket,),
            )
        ]
        if route_rows:
            connection.executemany(
                "INSERT INTO sense_route (sense_ref, lemma_ref) "
                "VALUES (?, ?)",
                route_rows,
            )

        connection.commit()
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()


def _emit_entry_shard_from_staging(
    staging: sqlite3.Connection,
    shard_path: Path,
    bucket: int,
) -> None:
    """Emit one entry shard from the staging partitions."""
    connection = sqlite3.connect(shard_path)
    connection.row_factory = sqlite3.Row
    try:
        _init_entry_shard(connection)
        lemma_rows = list(
            staging.execute(
                "SELECT id, semantic_ref, lemma, pos, gender, freq_rank, "
                "plural, plural_none, genitive_sg, aux, separable, particle, "
                "reflexive, praesens_3sg, praeteritum_3sg, partizip_ii, "
                "governs, comparative, superlative, ipa, source, license "
                "FROM entry_lemma_p WHERE bucket = ? ORDER BY id ASC",
                (bucket,),
            )
        )
        if lemma_rows:
            connection.executemany(
                "INSERT INTO lemma (id, semantic_ref, lemma, pos, gender, "
                "freq_rank, plural, plural_none, genitive_sg, aux, separable, "
                "particle, reflexive, praesens_3sg, praeteritum_3sg, "
                "partizip_ii, governs, comparative, superlative, ipa, "
                "source, license) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                lemma_rows,
            )

        sense_rows = list(
            staging.execute(
                "SELECT id, lemma_id, semantic_ref, source_namespace, "
                "source_ref, ord, register, source, license "
                "FROM entry_sense_p WHERE bucket = ? ORDER BY id ASC",
                (bucket,),
            )
        )
        if sense_rows:
            connection.executemany(
                "INSERT INTO sense (id, lemma_id, semantic_ref, "
                "source_namespace, source_ref, ord, register, source, "
                "license) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                sense_rows,
            )

        meaning_rows = list(
            staging.execute(
                "SELECT id, sense_id, language, kind, ord, text, source, "
                "license FROM entry_meaning_p WHERE bucket = ? "
                "ORDER BY id ASC",
                (bucket,),
            )
        )
        if meaning_rows:
            connection.executemany(
                "INSERT INTO sense_meaning (id, sense_id, language, kind, "
                "ord, text, source, license) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                meaning_rows,
            )

        surface_rows = [
            (str(r[0]), int(r[1]))
            for r in staging.execute(
                "SELECT form, lemma_id FROM entry_surface_p WHERE bucket = ? "
                "ORDER BY lemma_id ASC, form ASC",
                (bucket,),
            )
        ]
        if surface_rows:
            connection.executemany(
                "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
                surface_rows,
            )

        el_rows = [
            (int(r[0]), int(r[1]))
            for r in staging.execute(
                "SELECT lemma_id, example_id FROM entry_example_lemma_p "
                "WHERE bucket = ? ORDER BY lemma_id ASC, example_id ASC",
                (bucket,),
            )
        ]
        if el_rows:
            connection.executemany(
                "INSERT INTO example_lemma (lemma_id, example_id) "
                "VALUES (?, ?)",
                el_rows,
            )

        connection.commit()
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()


def _emit_example_shard_from_staging(
    staging: sqlite3.Connection,
    shard_path: Path,
    bucket: int,
) -> None:
    """Emit one example shard from the staging partitions."""
    connection = sqlite3.connect(shard_path)
    connection.row_factory = sqlite3.Row
    try:
        _init_example_shard(connection)
        rows = list(
            staging.execute(
                "SELECT id, de, en, source, source_ref, license, "
                "token_count, has_proper FROM example_p WHERE bucket = ? "
                "ORDER BY id ASC",
                (bucket,),
            )
        )
        if rows:
            connection.executemany(
                "INSERT INTO example (id, de, en, source, source_ref, "
                "license, token_count, has_proper) VALUES (?, ?, ?, ?, ?, "
                "?, ?, ?)",
                rows,
            )
        connection.commit()
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()


def _emit_shard_atomically(
    staging: sqlite3.Connection,
    output_dir: Path,
    family: str,
    bucket: int,
    emitter: Any,
) -> tuple[Path, int, str]:
    """Write one shard to a tmp path, validate it, install it canonically.

    Returns ``(canonical_path, byte_size, sha256_hexdigest)``.
    """
    canonical = output_dir / _asset_name(family, bucket)
    tmp_source = output_dir / f".{family}-{bucket:03d}.sqlite.tmp"
    if tmp_source.exists():
        tmp_source.unlink()
    emitter(staging, tmp_source, bucket)
    if not tmp_source.exists():
        raise RuntimeError(
            f"shard emitter produced no file for {family}-{bucket:03d}"
        )
    _atomic_install(tmp_source, canonical)
    payload = canonical.read_bytes()
    digest = sha256(payload).hexdigest()
    return canonical, len(payload), digest


def build_corpus(
    inputs: BuildInputs,
) -> tuple[OnlineManifest, bytes]:
    """Build the deterministic Online corpus from one verified Local asset.

    Production Slice 13 implementation uses a private SQLite staging DB
    to spill partition state to disk, so the resident working set is
    bounded by approximately one bucket's worth of rows at any moment.
    The output shards, the membership filter, and the manifest are
    byte-identical to what the in-memory Slice 11 builder would have
    written against the same verified input.

    Returns the validated :class:`OnlineManifest` and the membership
    filter bytes.
    """
    _validate_local_input(inputs.source_path)
    inputs.output_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = inputs.output_dir / _STAGING_SUBDIR
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_path = staging_dir / _STAGING_DB_NAME

    # Clean up any leftover ``.tmp`` marker files the in-memory builder
    # used; the streaming builder uses them too.
    tmp_dir = inputs.output_dir / ".tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    assets: list[ManifestAsset] = []
    filter_payload: bytes = b""
    try:
        # Pass 1: stream the authoritative source into the staging DB.
        with _open_staging(staging_path, fresh=True) as stage:
            _stage_source(stage, inputs.source_path)

        # Pass 2: derive the per-family partition tables.
        with _open_staging(staging_path) as stage:
            _build_lookup_partitions(stage)
            _build_entry_partitions(stage)
            _build_example_partitions(stage)
            _build_closure_keys(stage)

        # Pass 3: validate the partitions against the authoritative source.
        with _open_staging(staging_path) as stage:
            _validate_lookup_partitions(stage)
            _validate_entry_partitions(stage)
            _validate_example_partitions(stage)

        # Pass 4: build the membership filter from the staged closure keys.
        with _open_staging(staging_path) as stage:
            filter_payload = _build_bloom_filter(stage)

        # Pass 5: emit every shard one at a time from the staging DB.
        with _open_staging(staging_path) as stage:
            for bucket in range(LOOKUP_FAMILY_SIZE):
                canonical, size, digest = _emit_shard_atomically(
                    stage,
                    inputs.output_dir,
                    SHARD_FAMILY_LOOKUP,
                    bucket,
                    _emit_lookup_shard_from_staging,
                )
                assets.append(
                    ManifestAsset(
                        family=SHARD_FAMILY_LOOKUP,
                        bucket=bucket,
                        name=_asset_name(SHARD_FAMILY_LOOKUP, bucket),
                        path=_asset_path(SHARD_FAMILY_LOOKUP, bucket),
                        byte_size=size,
                        sha256=digest,
                        schema_version="lookup-shard-v1",
                    )
                )

            for bucket in range(ENTRY_FAMILY_SIZE):
                canonical, size, digest = _emit_shard_atomically(
                    stage,
                    inputs.output_dir,
                    SHARD_FAMILY_ENTRY,
                    bucket,
                    _emit_entry_shard_from_staging,
                )
                assets.append(
                    ManifestAsset(
                        family=SHARD_FAMILY_ENTRY,
                        bucket=bucket,
                        name=_asset_name(SHARD_FAMILY_ENTRY, bucket),
                        path=_asset_path(SHARD_FAMILY_ENTRY, bucket),
                        byte_size=size,
                        sha256=digest,
                        schema_version="entry-shard-v1",
                    )
                )

            for bucket in range(EXAMPLE_FAMILY_SIZE):
                canonical, size, digest = _emit_shard_atomically(
                    stage,
                    inputs.output_dir,
                    SHARD_FAMILY_EXAMPLE,
                    bucket,
                    _emit_example_shard_from_staging,
                )
                assets.append(
                    ManifestAsset(
                        family=SHARD_FAMILY_EXAMPLE,
                        bucket=bucket,
                        name=_asset_name(SHARD_FAMILY_EXAMPLE, bucket),
                        path=_asset_path(SHARD_FAMILY_EXAMPLE, bucket),
                        byte_size=size,
                        sha256=digest,
                        schema_version="example-shard-v1",
                    )
                )

            # Membership filter: install into the canonical location.
            tmp_filter = inputs.output_dir / ".membership-filter.bin.tmp"
            tmp_filter.write_bytes(_canonicalize_blob(filter_payload))
            filter_canonical = inputs.output_dir / _filter_name()
            _install_blob(tmp_filter, filter_canonical)
            digest = sha256(filter_canonical.read_bytes()).hexdigest()
            assets.append(
                ManifestAsset(
                    family=SHARD_FAMILY_FILTER,
                    bucket=0,
                    name=_filter_name(),
                    path=_filter_path(),
                    byte_size=filter_canonical.stat().st_size,
                    sha256=digest,
                    schema_version="membership-filter-v1",
                )
            )

        manifest = OnlineManifest(
            dataset_token=inputs.dataset_token,
            schema_version=MANIFEST_SCHEMA_VERSION,
            distribution=TrustedDistribution(
                base_origin=inputs.base_origin,
                release_tag=inputs.release_tag,
                redirect_policy="github_release_redirect_only",
            ),
            assets=tuple(assets),
        )
        return manifest, filter_canonical.read_bytes()
    finally:
        # Drop the staging DB and any leftover tmp marker files. The
        # canonical output_dir ends up with only the 577 shard files,
        # the membership filter, and the manifest.
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        # Reap any ``.tmp`` files the emitter may have left behind.
        for child in inputs.output_dir.glob(".*-*.sqlite.tmp"):
            try:
                child.unlink()
            except OSError:
                pass
        for child in inputs.output_dir.glob(".membership-filter.bin.tmp"):
            try:
                child.unlink()
            except OSError:
                pass


def write_manifest(
    manifest: OnlineManifest, target_path: Path | str
) -> None:
    """Write one validated manifest as canonical JSON."""
    payload = json.dumps(
        {
            "dataset_token": manifest.dataset_token,
            "schema_version": manifest.schema_version,
            "distribution": {
                "base_origin": manifest.distribution.base_origin,
                "release_tag": manifest.distribution.release_tag,
                "redirect_policy": manifest.distribution.redirect_policy,
            },
            "assets": sorted(
                (
                    {
                        "family": a.family,
                        "bucket": a.bucket,
                        "name": a.name,
                        "path": a.path,
                        "byte_size": a.byte_size,
                        "sha256": a.sha256,
                        "schema_version": a.schema_version,
                    }
                    for a in manifest.assets
                ),
                key=lambda item: (item["family"], item["bucket"]),
            ),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload + "\n")


def main(argv: Iterable[str] | None = None) -> int:
    """Entry point used by the Slice 11 builder CLI."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to the verified Local dictionary asset",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where shard files, the filter, and the manifest are written",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Output path for the canonical manifest JSON",
    )
    args = parser.parse_args(arguments)

    inputs = BuildInputs(
        source_path=args.source.resolve(),
        output_dir=args.output_dir.resolve(),
    )
    manifest, _filter = build_corpus(inputs)
    write_manifest(manifest, args.manifest.resolve())
    digest = manifest_hash(manifest)
    sys.stdout.write(
        json.dumps(
            {
                "manifest_hash": digest,
                "asset_count": len(manifest.assets),
                "filter_size": next(
                    a.byte_size for a in manifest.assets
                    if a.family == SHARD_FAMILY_FILTER
                ),
            }
        )
        + "\n"
    )
    return 0


__all__ = [
    "BuildInputs",
    "build_corpus",
    "main",
    "write_manifest",
]

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
