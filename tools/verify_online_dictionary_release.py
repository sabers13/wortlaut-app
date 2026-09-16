"""Production Online dictionary release verifier.

This is the Slice-13 publication-bound verifier. It validates the
production Online dictionary corpus against the verified v2 dictionary
in two distinct modes:

1. ``local`` — pre-publication staging verification.

   * Reads the produced manifest from the staging directory.
   * Builds an ``OnlineDictionaryProvider`` from the staging corpus
     via a backend-controlled local transport.
   * Builds a ``LocalDictionaryProvider`` from the verified v2 full
     dictionary.
   * Selects a deterministic differential sample from the
     authoritative source.
   * Compares Local vs Online on every served-product read shape:
     hit/miss, lemma identity, semantic refs, surface resolution,
     sense routing, entries, meanings, examples, normalized routing,
     and provider error behavior.
   * Reports per-case pass/fail.

2. ``public`` — post-publication anonymous verification.

   * Downloads the manifest anonymously from the public GitHub
     Release for the configured release tag.
   * Streams every public asset, verifying the exact
     ``asset.name`` / ``byte_size`` / ``sha256`` triple against
     the manifest.
   * Loads the membership filter (the well-known first non-corpus
     asset) through the trusted Product transport; rejects
     mismatches as structured integrity failures.

Both modes share the deterministic sample-selection logic so the
pre-publication staging run and the post-publication release run
exercise the same offline observable reads over the same source.

The tool depends only on the existing ``app.provider``,
``app.provider_local``, ``app.provider_online``, ``app.online_cache``,
``app.online_manifest``, ``app.online_transport``, ``app.routing``,
and ``app.dictionary`` contracts. No new runtime dependency is added.

Exit codes:

    0 -> all checks pass.
    1 -> structural integrity failure (file/size/SHA/integrity_check).
    2 -> differential mismatch between Local and Online providers.
    3 -> anonymous public release missing a required asset or asset
         count drift.

Output:

    JSON report file with ``mode``, per-case results, plus
    aggregated counters and decision lines.

The verifier never reaches a public GitHub network from local mode;
``public`` mode uses the trusted ``GitHubReleaseProductTransport``
exactly as the Product Online provider does.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.online_cache import ShardCache, ShardRequest
from app.online_manifest import (
    ENTRY_FAMILY_SIZE,
    EXAMPLE_FAMILY_SIZE,
    LOOKUP_FAMILY_SIZE,
    SHARD_FAMILY_FILTER,
    OnlineManifest,
    manifest_hash,
    parse_manifest,
)
from app.provider import (
    DictionaryProvider,
    ExampleRecord,
    LemmaHit,
    MeaningRow,
    SenseEntry,
)
from app.provider_local import LocalDictionaryProvider
from app.provider_online import OnlineDictionaryProvider
from app.routing import (
    example_bucket,
    lookup_buckets_for_text,
)

# Test corpus source bytes (the verified offline dictionary used as
# the Local-side ground truth).  The verifier only opens this file
# read-only and never writes to it.
EXPECTED_SOURCE_BYTES: int = 945418240
EXPECTED_SOURCE_SHA256: str = (
    "1698b9979099098bf8d6e6fd7f9194134a927d428e3c2b1905a626eb8ee67d4c"
)


@dataclass(frozen=True)
class CaseResult:
    name: str
    category: str
    passed: bool
    detail: str
    local_summary: str = ""
    online_summary: str = ""


@dataclass
class DiffReport:
    mode: str
    started_at: str
    finished_at: str = ""
    cases: list[CaseResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, case: CaseResult) -> None:
        self.cases.append(case)

    def finalise(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "passed": self.passed(),
            "case_count": len(self.cases),
            "passed_count": sum(1 for c in self.cases if c.passed),
            "cases": [
                {
                    "name": c.name,
                    "category": c.category,
                    "passed": c.passed,
                    "detail": c.detail,
                    "local_summary": c.local_summary,
                    "online_summary": c.online_summary,
                }
                for c in self.cases
            ],
            "notes": list(self.notes),
        }


def _open_source_readonly(source_path: Path) -> sqlite3.Connection:
    """Open the verified v2 dictionary read-only; reject any mutation."""
    if not source_path.exists():
        raise RuntimeError(f"source dictionary not found: {source_path}")
    size = source_path.stat().st_size
    if size != EXPECTED_SOURCE_BYTES:
        raise RuntimeError(
            f"source dictionary bytes {size} != expected {EXPECTED_SOURCE_BYTES}"
        )
    digest = sha256(source_path.read_bytes()).hexdigest()
    if digest != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(
            f"source dictionary SHA-256 {digest} != expected {EXPECTED_SOURCE_SHA256}"
        )
    connection = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def _sqlite_ascii_lower(value: str) -> str:
    """Reproduce SQLite's built-in ``lower()`` (ASCII-oriented)."""
    return "".join(ch.lower() if "A" <= ch <= "Z" else ch for ch in value)


def select_sample(conn: sqlite3.Connection) -> dict[str, Any]:
    """Pick a stable, sortable sample from the authoritative source.

    Selection is fully deterministic and biased toward stable common
    words when available so the differential covers real product
    lookups. The sample covers every Slice-13 category:

        ASCII exact lemma (preferred: "Haus"; fallback: smallest id)
        ASCII case variant (lowercased)
        Umlaut lemma (preferred: "Mädchen"; fallback: first ä/ö/ü)
        ß lemma (preferred: "groß"; fallback: first ß)
        Combined umlaut + ß
        Surface form that differs from its canonical lemma
        Unknown sentinel ("ZZZZ_NONEXISTENT_SENTINEL_ZZZZ")
        Representative sense_ref -> parent lemma_ref
        Representative lemma_ref materialization
        Representative example ids via the entry shard
        Representative DE/EN sense_meaning rows
        NFD-decomposed equivalent of the umlaut lemma

    The unknown sentinel is a fixed string and is not derived from
    the source.
    """
    sample: dict[str, Any] = {
        "smallest_lemma_text": None,
        "smallest_lemma_row": None,
        "smallest_lemma_pos": None,
        "smallest_lemma_gender": None,
        "umlaut_lemma_text": None,
        "umlaut_lemma_row": None,
        "umlaut_nfd_text": None,
        "esszett_lemma_text": None,
        "esszett_lemma_row": None,
        "esszett_case_variant_text": None,
        "combined_lemma_text": None,
        "combined_lemma_row": None,
        "surface_form_text": None,
        "surface_form_lemma_id": None,
        "surface_form_lemma_text": None,
        "representative_lemma_id": None,
        "representative_lemma_ref": None,
        "representative_lemma_text": None,
        "representative_sense_id": None,
        "representative_sense_ref": None,
        "representative_sense_lemma_id": None,
        "representative_example_ids": [],
        "representative_meanings": [],
        "unknown_lemma_text": "ZZZZ_NONEXISTENT_SENTINEL_ZZZZ",
    }

    lemma_rows = list(
        conn.execute(
            "SELECT id, semantic_ref, lemma, pos, gender, freq_rank FROM lemma "
            "ORDER BY id ASC"
        )
    )
    if not lemma_rows:
        raise RuntimeError("source dictionary has no lemma rows")

    def _select(preferred: Iterable[str], *, fallback_chars: Iterable[str] = ()) -> tuple[Any, str] | None:
        for preferred_text in preferred:
            for row in lemma_rows:
                if str(row["lemma"]) == preferred_text:
                    return row, preferred_text
        if fallback_chars:
            wanted = set(fallback_chars)
            for row in lemma_rows:
                text = str(row["lemma"])
                if text and any(ch in text for ch in wanted) and len(text) >= 3:
                    return row, text
        for row in lemma_rows:
            text = str(row["lemma"])
            if text and len(text) >= 3 and text[0].isalpha():
                return row, text
        return None

    ascii_choice = _select(["Haus", "See", "Karte", "Tag"])
    if ascii_choice is not None:
        sample["smallest_lemma_row"] = tuple(ascii_choice[0])
        sample["smallest_lemma_text"] = ascii_choice[1]
        sample["smallest_lemma_pos"] = str(ascii_choice[0]["pos"])
        sample["smallest_lemma_gender"] = (
            str(ascii_choice[0]["gender"])
            if ascii_choice[0]["gender"] is not None
            else None
        )

    umlaut_choice = _select(
        ["Mädchen", "Straße", "Schönheit"],
        fallback_chars=("ä", "ö", "ü", "Ä", "Ö", "Ü"),
    )
    if umlaut_choice is not None:
        sample["umlaut_lemma_row"] = tuple(umlaut_choice[0])
        sample["umlaut_lemma_text"] = umlaut_choice[1]
        decomposed = (
            umlaut_choice[1]
            .replace("ä", "a\u0308")
            .replace("ö", "o\u0308")
            .replace("ü", "u\u0308")
            .replace("Ä", "A\u0308")
            .replace("Ö", "O\u0308")
            .replace("Ü", "U\u0308")
        )
        if decomposed == umlaut_choice[1]:
            decomposed = "A\u0308"
        sample["umlaut_nfd_text"] = decomposed

    ess_choice = _select(["groß", "Straße", "großen"], fallback_chars=("ß",))
    if ess_choice is not None:
        sample["esszett_lemma_row"] = tuple(ess_choice[0])
        sample["esszett_lemma_text"] = ess_choice[1]
        if any(ch in ess_choice[1] for ch in ("a", "e", "i", "o", "u")):
            sample["esszett_case_variant_text"] = ess_choice[1].upper()

    combined_choice = _select(
        ["Straße", "größere"],
        fallback_chars=("ä", "ö", "ü", "Ä", "Ö", "Ü", "ß"),
    )
    if combined_choice is not None:
        sample["combined_lemma_row"] = tuple(combined_choice[0])
        sample["combined_lemma_text"] = combined_choice[1]

    surface_form_choice = _select_surface_form(conn)
    if surface_form_choice is not None:
        sample["surface_form_text"] = surface_form_choice["form"]
        sample["surface_form_lemma_id"] = surface_form_choice["lemma_id"]
        sample["surface_form_lemma_text"] = surface_form_choice["lemma_text"]

    if sample["representative_lemma_id"] is None:
        _populate_representative(conn, lemma_rows, sample)
    return sample


def _select_surface_form(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Pick a stable surface form whose ``form`` differs from its lemma text.

    Preferred: ``"Häuser"`` (the canonical plural of "Haus"). Falls
    back to the first alphabetic surface form whose text differs
    from its lemma and is at least four characters long. The
    selection is fully deterministic across runs.
    """
    preferred_pairs: tuple[tuple[str, str], ...] = (
        ("Häuser", "Haus"),
        ("Hause", "Haus"),
        ("ging", "gehen"),
    )
    for form, lemma in preferred_pairs:
        for row in conn.execute(
            "SELECT sf.form, l.id AS lemma_id, l.lemma FROM surface_form sf "
            "JOIN lemma l ON l.id = sf.lemma_id WHERE sf.form = ? AND l.lemma = ?",
            (form, lemma),
        ):
            return {
                "form": str(row["form"]),
                "lemma_id": int(row["lemma_id"]),
                "lemma_text": str(row["lemma"]),
            }
    for row in conn.execute(
        "SELECT sf.form, l.id AS lemma_id, l.lemma FROM surface_form sf "
        "JOIN lemma l ON l.id = sf.lemma_id "
        "ORDER BY sf.form ASC, sf.lemma_id ASC"
    ):
        form = str(row["form"])
        lemma_text = str(row["lemma"])
        if (
            form
            and form.strip()
            and form != lemma_text
            and len(form) >= 4
            and form[0].isalpha()
        ):
            return {
                "form": form,
                "lemma_id": int(row["lemma_id"]),
                "lemma_text": lemma_text,
            }
    return None


def _populate_representative(
    conn: sqlite3.Connection,
    lemma_rows: list[Any],
    sample: dict[str, Any],
) -> None:
    """Populate the representative-lemma and representative-sense fields.

    Preferred: a known stable word such as "Haus". A lemma with senses
    (and therefore a representative sense_route and sense_meaning
    rows) is found by stable ordering. The lemma is required to be a
    real word: at least three characters and starting with a letter,
    so the test exercises an actual lookup/entry path rather than
    punctuation or abbreviations.
    """
    preferred_lemmas: tuple[str, ...] = (
        "Haus", "See", "Karte", "anrufen", "Tag",
    )
    chosen_lemma: Any = None
    for word in preferred_lemmas:
        for row in lemma_rows:
            if str(row["lemma"]) == word:
                chosen_lemma = row
                break
        if chosen_lemma is not None:
            break
    if chosen_lemma is None:
        for row in lemma_rows:
            text = str(row["lemma"])
            if len(text) >= 3 and text[0].isalpha():
                chosen_lemma = row
                break
    if chosen_lemma is None:
        chosen_lemma = lemma_rows[0]
    sample["representative_lemma_id"] = int(chosen_lemma["id"])
    sample["representative_lemma_ref"] = str(chosen_lemma["semantic_ref"])
    sample["representative_lemma_text"] = str(chosen_lemma["lemma"])

    sense_rows = list(
        conn.execute(
            "SELECT id, semantic_ref, lemma_id FROM sense WHERE lemma_id = ? "
            "ORDER BY id ASC",
            (int(chosen_lemma["id"]),),
        ).fetchall()
    )
    if not sense_rows:
        for row in conn.execute(
            "SELECT id, semantic_ref, lemma_id FROM sense WHERE lemma_id IN ("
            "  SELECT lemma_id FROM sense WHERE lemma_id IN ("
            "    SELECT id FROM lemma WHERE length(lemma) >= 3 AND substr(lemma, 1, 1) GLOB '[A-Za-z]') "
            "  ) ORDER BY id ASC"
        ):
            sense_rows.append(row)
    if sense_rows:
        first = sense_rows[0]
        sample["representative_sense_id"] = int(first["id"])
        sample["representative_sense_ref"] = str(first["semantic_ref"])
        sample["representative_sense_lemma_id"] = int(first["lemma_id"])
    sample["representative_example_ids"] = [
        int(r["example_id"])
        for r in conn.execute(
            "SELECT example_id FROM example_lemma "
            "WHERE lemma_id = ? ORDER BY example_id ASC",
            (int(chosen_lemma["id"]),),
        ).fetchall()
    ]
    sample["representative_meanings"] = [
        (str(m["language"]), str(m["text"]))
        for m in conn.execute(
            "SELECT sm.language, sm.text FROM sense_meaning sm "
            "JOIN sense s ON s.id = sm.sense_id "
            "WHERE s.lemma_id = ? ORDER BY sm.language ASC, sm.ord ASC, sm.id ASC",
            (int(chosen_lemma["id"]),),
        ).fetchall()
    ]


def _summarize_hit(hit: LemmaHit) -> str:
    return (
        f"sref={hit.semantic_ref} "
        f"text={hit.lemma!r} pos={hit.pos} "
        f"gender={hit.gender!r} id={hit.lemma_id}"
    )


def _summarize_sense(sense: SenseEntry) -> str:
    return (
        f"id={sense.sense_id} lemma_id={sense.lemma_id} "
        f"sref={sense.semantic_ref} ord={sense.ord}"
    )


def _summarize_meaning(meaning: MeaningRow) -> str:
    return (
        f"sense_id={meaning.sense_id} lang={meaning.language} "
        f"kind={meaning.kind} text={meaning.text!r}"
    )


def _summarize_example(example: ExampleRecord) -> str:
    return (
        f"id={example.example_id} de={example.de!r} en={example.en!r}"
    )


def _local_provider_lookup(
    provider: DictionaryProvider, query: str, surface: bool = False
) -> list[LemmaHit]:
    if surface:
        return list(provider.lookup_surface_form(query))
    return list(provider.lookup_exact(query))


def _online_provider_lookup(
    provider: DictionaryProvider, query: str, surface: bool = False
) -> list[LemmaHit]:
    if surface:
        return list(provider.lookup_surface_form(query))
    return list(provider.lookup_exact(query))


def _compare_hits(local: Sequence[LemmaHit], online: Sequence[LemmaHit]) -> bool:
    local_keys = sorted(
        (hit.semantic_ref, hit.lemma_id, hit.pos, hit.gender or "") for hit in local
    )
    online_keys = sorted(
        (hit.semantic_ref, hit.lemma_id, hit.pos, hit.gender or "") for hit in online
    )
    return local_keys == online_keys


def _warm_online_numeric_caches(
    online: DictionaryProvider,
    queries: Iterable[str],
) -> None:
    """Resolve ``queries`` through Online so its in-process numeric caches are populated."""
    for query in queries:
        online.lookup_exact(query)


def _build_local_transport(corpus_dir: Path) -> tuple[Any, Any]:
    """Build an in-process shard transport that reads from ``corpus_dir``.

    Returns ``(transport_callable, asset_path_resolver)``.
    """

    def transport(request: ShardRequest) -> bytes:
        identity = request.identity
        candidates: list[Path] = []
        if identity.family == "membership_filter":
            candidates.append(corpus_dir / "membership-filter.bin")
        else:
            family_name = identity.family
            candidates.append(
                corpus_dir / f"{family_name}-{identity.bucket:03d}.sqlite"
            )
            candidates.append(
                corpus_dir / "shards" / family_name / f"{identity.bucket:03d}.sqlite"
            )
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_bytes()
        from app.provider import ProviderIntegrityError
        raise ProviderIntegrityError(
            f"missing local shard: family={identity.family} bucket={identity.bucket}"
        )

    return transport, None


def _build_corpus_provider(
    manifest: OnlineManifest,
    corpus_dir: Path,
    *,
    cache_dir: Path,
    filter_payload: bytes,
) -> OnlineDictionaryProvider:
    """Construct an OnlineDictionaryProvider reading from a local staging corpus."""
    transport, _ = _build_local_transport(corpus_dir)
    cache = ShardCache(cache_dir, transport=transport)
    return OnlineDictionaryProvider(
        manifest=manifest,
        cache=cache,
        filter_payload=filter_payload,
        dataset_token=manifest.dataset_token,
    )


def _validate_manifest_assets(
    manifest: OnlineManifest,
    corpus_dir: Path,
    report: DiffReport,
) -> bool:
    """Verify every manifest asset exists on disk with the recorded size and SHA-256.

    Returns ``True`` only if every asset passes.
    """
    ok = True
    for asset in manifest.assets:
        candidate = corpus_dir / asset.name
        if not candidate.exists():
            report.add(
                CaseResult(
                    name=f"asset_exists:{asset.name}",
                    category="integrity",
                    passed=False,
                    detail=f"missing file: {candidate}",
                )
            )
            ok = False
            continue
        size = candidate.stat().st_size
        digest = sha256(candidate.read_bytes()).hexdigest()
        if size != asset.byte_size:
            report.add(
                CaseResult(
                    name=f"asset_byte_size:{asset.name}",
                    category="integrity",
                    passed=False,
                    detail=f"file bytes {size} != manifest {asset.byte_size}",
                )
            )
            ok = False
        elif digest != asset.sha256:
            report.add(
                CaseResult(
                    name=f"asset_sha256:{asset.name}",
                    category="integrity",
                    passed=False,
                    detail=f"file SHA-256 {digest} != manifest {asset.sha256}",
                )
            )
            ok = False
        else:
            report.add(
                CaseResult(
                    name=f"asset_ok:{asset.name}",
                    category="integrity",
                    passed=True,
                    detail=(
                        f"bytes={size} sha256={digest[:16]}... family={asset.family} "
                        f"bucket={asset.bucket}"
                    ),
                )
            )
    return ok


def _validate_sqlite_shards(
    manifest: OnlineManifest,
    corpus_dir: Path,
    report: DiffReport,
) -> bool:
    """Open every SQLite shard (``lookup`` / ``entry`` / ``example``) read-only."""
    ok = True
    for asset in manifest.assets:
        if asset.family == SHARD_FAMILY_FILTER:
            continue
        path = corpus_dir / asset.name
        if not path.exists():
            report.add(
                CaseResult(
                    name=f"sqlite_open:{asset.name}",
                    category="integrity",
                    passed=False,
                    detail="file missing",
                )
            )
            ok = False
            continue
        try:
            uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
            connection = sqlite3.connect(uri, uri=True)
            try:
                row = connection.execute("PRAGMA integrity_check").fetchone()
                if row is None or str(row[0]).lower() != "ok":
                    report.add(
                        CaseResult(
                            name=f"sqlite_integrity:{asset.name}",
                            category="integrity",
                            passed=False,
                            detail=f"integrity_check returned {row[0]!r}",
                        )
                    )
                    ok = False
                    continue
            finally:
                connection.close()
            report.add(
                CaseResult(
                    name=f"sqlite_ok:{asset.name}",
                    category="integrity",
                    passed=True,
                    detail=f"family={asset.family} bucket={asset.bucket}",
                )
            )
        except sqlite3.Error as exc:
            report.add(
                CaseResult(
                    name=f"sqlite_open:{asset.name}",
                    category="integrity",
                    passed=False,
                    detail=f"SQLite open failed: {exc}",
                )
            )
            ok = False
    return ok


def _validate_membership_filter(
    manifest: OnlineManifest,
    corpus_dir: Path,
    report: DiffReport,
) -> tuple[bytes, bool]:
    """Load ``membership-filter.bin`` and ensure the production parser accepts it."""
    filter_assets = list(manifest.filter_assets)
    if not filter_assets:
        report.add(
            CaseResult(
                name="filter_asset_present",
                category="integrity",
                passed=False,
                detail="manifest declares no membership filter asset",
            )
        )
        return b"", False
    asset = filter_assets[0]
    path = corpus_dir / asset.name
    if not path.exists():
        report.add(
            CaseResult(
                name="filter_file_present",
                category="integrity",
                passed=False,
                detail=f"filter file missing at {path}",
            )
        )
        return b"", False
    payload = path.read_bytes()
    if len(payload) != asset.byte_size:
        report.add(
            CaseResult(
                name="filter_byte_size",
                category="integrity",
                passed=False,
                detail=f"got {len(payload)} expected {asset.byte_size}",
            )
        )
        return payload, False
    if sha256(payload).hexdigest() != asset.sha256:
        report.add(
            CaseResult(
                name="filter_sha256",
                category="integrity",
                passed=False,
                detail="filter digest mismatch",
            )
        )
        return payload, False
    from app.online_filter import BloomFilter
    try:
        BloomFilter.from_bytes(payload)
    except ValueError as exc:
        report.add(
            CaseResult(
                name="filter_parse",
                category="integrity",
                passed=False,
                detail=f"BloomFilter.from_bytes failed: {exc}",
            )
        )
        return payload, False
    report.add(
        CaseResult(
            name="filter_ok",
            category="integrity",
            passed=True,
            detail=f"bytes={len(payload)} sha256={asset.sha256[:16]}...",
        )
    )
    return payload, True


def _validate_topology(manifest: OnlineManifest, report: DiffReport) -> bool:
    """Prove the manifest declares exactly the frozen topology."""
    expected_counts = {
        "lookup": LOOKUP_FAMILY_SIZE,
        "entry": ENTRY_FAMILY_SIZE,
        "example": EXAMPLE_FAMILY_SIZE,
        "membership_filter": 1,
    }
    observed_counts: dict[str, int] = {
        family: 0 for family in expected_counts
    }
    for asset in manifest.assets:
        observed_counts[asset.family] = (
            observed_counts.get(asset.family, 0) + 1
        )
    ok = True
    for family, expected in expected_counts.items():
        observed = observed_counts.get(family, 0)
        if observed != expected:
            report.add(
                CaseResult(
                    name=f"topology:{family}",
                    category="topology",
                    passed=False,
                    detail=f"observed {observed} != expected {expected}",
                )
            )
            ok = False
        else:
            report.add(
                CaseResult(
                    name=f"topology:{family}",
                    category="topology",
                    passed=True,
                    detail=f"{observed} assets",
                )
            )
    if len(manifest.assets) != sum(expected_counts.values()):
        report.add(
            CaseResult(
                name="topology:total",
                category="topology",
                passed=False,
                detail=(
                    "manifest declares "
                    f"{len(manifest.assets)} assets, expected "
                    f"{sum(expected_counts.values())}"
                ),
            )
        )
        ok = False
    return ok


def _validate_dataset_token(
    manifest: OnlineManifest, report: DiffReport
) -> bool:
    if manifest.dataset_token != EXPECTED_SOURCE_SHA256:
        report.add(
            CaseResult(
                name="dataset_token",
                category="dataset",
                passed=False,
                detail=(
                    f"manifest token {manifest.dataset_token!r} != "
                    f"expected source {EXPECTED_SOURCE_SHA256!r}"
                ),
            )
        )
        return False
    report.add(
        CaseResult(
            name="dataset_token",
            category="dataset",
            passed=True,
            detail=f"{manifest.dataset_token}",
        )
    )
    return True


def _compare_meaning_records(
    local: Sequence[MeaningRow],
    online: Sequence[MeaningRow],
) -> bool:
    local_keys = sorted(
        (m.sense_id, m.language, m.kind, m.ord, m.text) for m in local
    )
    online_keys = sorted(
        (m.sense_id, m.language, m.kind, m.ord, m.text) for m in online
    )
    return local_keys == online_keys


def _compare_example_records(
    local: Sequence[ExampleRecord], online: Sequence[ExampleRecord]
) -> bool:
    local_keys = sorted(
        (e.example_id, e.de, e.en, e.token_count or 0, e.has_proper) for e in local
    )
    online_keys = sorted(
        (e.example_id, e.de, e.en, e.token_count or 0, e.has_proper) for e in online
    )
    return local_keys == online_keys


def _compare_sense_records(
    local: Sequence[SenseEntry], online: Sequence[SenseEntry]
) -> bool:
    local_keys = sorted(
        (
            s.sense_id,
            s.semantic_ref,
            s.source_namespace,
            s.source_ref,
            s.ord,
        )
        for s in local
    )
    online_keys = sorted(
        (
            s.sense_id,
            s.semantic_ref,
            s.source_namespace,
            s.source_ref,
            s.ord,
        )
        for s in online
    )
    return local_keys == online_keys


def run_local_verification(
    source_path: Path,
    manifest_path: Path,
    corpus_dir: Path,
    *,
    cache_dir: Path | None = None,
    report: DiffReport | None = None,
) -> DiffReport:
    """Run pre-publication staging verification against the verified v2 dictionary."""
    started = datetime.now(timezone.utc).isoformat()
    if report is None:
        report = DiffReport(mode="local", started_at=started)

    manifest_text = manifest_path.read_text()
    manifest = parse_manifest(manifest_text)
    if cache_dir is None:
        cache_dir = Path(tempfile.mkdtemp(prefix="wortlaut-verify-cache-"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not _validate_dataset_token(manifest, report):
        report.finalise()
        return report
    if not _validate_topology(manifest, report):
        report.finalise()
        return report
    if not _validate_manifest_assets(manifest, corpus_dir, report):
        report.finalise()
        return report
    filter_payload, filter_ok = _validate_membership_filter(
        manifest, corpus_dir, report
    )
    if not filter_ok:
        report.finalise()
        return report
    if not _validate_sqlite_shards(manifest, corpus_dir, report):
        report.finalise()
        return report

    online = _build_corpus_provider(
        manifest,
        corpus_dir,
        cache_dir=cache_dir,
        filter_payload=filter_payload,
    )
    report.add(
        CaseResult(
            name="online_provider_constructed",
            category="runtime",
            passed=True,
            detail=(
                f"asset_token={online.asset_token[:16]}... "
                f"manifest_hash={manifest_hash(manifest)[:16]}..."
            ),
        )
    )

    local_conn = _open_source_readonly(source_path)
    try:
        integrity_row = local_conn.execute("PRAGMA integrity_check").fetchone()
        if integrity_row is None or str(integrity_row[0]).lower() != "ok":
            report.add(
                CaseResult(
                    name="local_source_integrity",
                    category="source",
                    passed=False,
                    detail=f"PRAGMA integrity_check returned {integrity_row[0]!r}",
                )
            )
            report.finalise()
            return report
        report.add(
            CaseResult(
                name="local_source_integrity",
                category="source",
                passed=True,
                detail=f"PRAGMA integrity_check=ok bytes={EXPECTED_SOURCE_BYTES}",
            )
        )
        local = LocalDictionaryProvider(source_path)

        sample = select_sample(local_conn)
        report.add(
            CaseResult(
                name="sample_selected",
                category="sample",
                passed=True,
                detail=(
                    "ascii="
                    f"{sample['smallest_lemma_text']!r}, "
                    "umlaut="
                    f"{sample['umlaut_lemma_text']!r}, "
                    "esszett="
                    f"{sample['esszett_lemma_text']!r}, "
                    "surface="
                    f"{sample['surface_form_text']!r}, "
                    "lemma_ref="
                    f"{sample['representative_lemma_ref']}"
                ),
            )
        )

        if not (
            local.asset_token == online.asset_token == manifest.dataset_token
        ):
            report.add(
                CaseResult(
                    name="dataset_token_alignment",
                    category="dataset",
                    passed=False,
                    detail=(
                        f"local={local.asset_token[:16]}... "
                        f"online={online.asset_token[:16]}... "
                        f"manifest={manifest.dataset_token[:16]}..."
                    ),
                )
            )
            report.finalise()
            return report
        report.add(
            CaseResult(
                name="dataset_token_alignment",
                category="dataset",
                passed=True,
                detail="local == online == manifest",
            )
        )

        case_letter_text = sample["smallest_lemma_text"]
        if case_letter_text is None:
            raise RuntimeError("smallest lemma missing in sample")
        case_letter_text_lower = case_letter_text.lower()

        queries_to_warm: list[str] = [
            case_letter_text,
            sample["umlaut_lemma_text"] or case_letter_text,
            sample["esszett_lemma_text"] or case_letter_text,
            sample["surface_form_text"] or case_letter_text,
        ]
        _warm_online_numeric_caches(online, queries_to_warm)

        def _diff_case(
            name: str,
            category: str,
            *,
            local_value: Any,
            online_value: Any,
            local_summary: str,
            online_summary: str,
            equal: bool,
        ) -> None:
            report.add(
                CaseResult(
                    name=name,
                    category=category,
                    passed=bool(equal),
                    detail=f"local_count={1 if local_value else 0}; "
                    f"online_count={1 if online_value else 0}",
                    local_summary=local_summary,
                    online_summary=online_summary,
                )
            )

        local_hits = _local_provider_lookup(local, case_letter_text)
        online_hits = _online_provider_lookup(online, case_letter_text)
        _diff_case(
            "lookup_exact:ascii",
            "lookup",
            local_value=local_hits,
            online_value=online_hits,
            local_summary="; ".join(_summarize_hit(h) for h in local_hits)
            or "(empty)",
            online_summary="; ".join(_summarize_hit(h) for h in online_hits)
            or "(empty)",
            equal=_compare_hits(local_hits, online_hits),
        )

        local_hits_lower = _local_provider_lookup(local, case_letter_text_lower)
        online_hits_lower = _online_provider_lookup(
            online, case_letter_text_lower
        )
        _diff_case(
            "lookup_exact:ascii_case",
            "lookup",
            local_value=local_hits_lower,
            online_value=online_hits_lower,
            local_summary="; ".join(_summarize_hit(h) for h in local_hits_lower)
            or "(empty)",
            online_summary="; ".join(_summarize_hit(h) for h in online_hits_lower)
            or "(empty)",
            equal=_compare_hits(local_hits_lower, online_hits_lower),
        )

        if sample["umlaut_lemma_text"] is not None:
            umlaut_text = sample["umlaut_lemma_text"]
            local_hits = _local_provider_lookup(local, umlaut_text)
            online_hits = _online_provider_lookup(online, umlaut_text)
            _diff_case(
                "lookup_exact:umlaut",
                "lookup",
                local_value=local_hits,
                online_value=online_hits,
                local_summary="; ".join(_summarize_hit(h) for h in local_hits)
                or "(empty)",
                online_summary="; ".join(_summarize_hit(h) for h in online_hits)
                or "(empty)",
                equal=_compare_hits(local_hits, online_hits),
            )
            if sample["umlaut_nfd_text"] is not None:
                nfd_text = sample["umlaut_nfd_text"]
                local_nfd = _local_provider_lookup(local, nfd_text)
                online_nfd = _online_provider_lookup(online, nfd_text)
                _diff_case(
                    "lookup_exact:umlaut_nfd",
                    "lookup",
                    local_value=local_nfd,
                    online_value=online_nfd,
                    local_summary="; ".join(_summarize_hit(h) for h in local_nfd)
                    or "(empty)",
                    online_summary="; ".join(_summarize_hit(h) for h in online_nfd)
                    or "(empty)",
                    equal=_compare_hits(local_nfd, online_nfd),
                )

        if sample["esszett_lemma_text"] is not None:
            ess_text = sample["esszett_lemma_text"]
            local_hits = _local_provider_lookup(local, ess_text)
            online_hits = _online_provider_lookup(online, ess_text)
            _diff_case(
                "lookup_exact:esszett",
                "lookup",
                local_value=local_hits,
                online_value=online_hits,
                local_summary="; ".join(_summarize_hit(h) for h in local_hits)
                or "(empty)",
                online_summary="; ".join(_summarize_hit(h) for h in online_hits)
                or "(empty)",
                equal=_compare_hits(local_hits, online_hits),
            )

        if sample["surface_form_text"] is not None:
            surface_text = sample["surface_form_text"]
            local_hits = _local_provider_lookup(local, surface_text, surface=True)
            online_hits = _online_provider_lookup(
                online, surface_text, surface=True
            )
            _diff_case(
                "lookup_surface_form:surface",
                "surface",
                local_value=local_hits,
                online_value=online_hits,
                local_summary="; ".join(_summarize_hit(h) for h in local_hits)
                or "(empty)",
                online_summary="; ".join(_summarize_hit(h) for h in online_hits)
                or "(empty)",
                equal=_compare_hits(local_hits, online_hits),
            )

        unknown_text = sample["unknown_lemma_text"]
        local_unknown = _local_provider_lookup(local, unknown_text)
        online_unknown = _online_provider_lookup(online, unknown_text)
        _diff_case(
            "lookup_exact:unknown",
            "lookup",
            local_value=local_unknown,
            online_value=online_unknown,
            local_summary="; ".join(_summarize_hit(h) for h in local_unknown)
            or "(empty)",
            online_summary="; ".join(_summarize_hit(h) for h in online_unknown)
            or "(empty)",
            equal=not local_unknown and not online_unknown,
        )

        if sample["representative_sense_ref"] is not None:
            sense_ref = sample["representative_sense_ref"]
            local_route = local.sense_route(sense_ref)
            online_route = online.sense_route(sense_ref)
            _diff_case(
                "sense_route:sense_ref",
                "sense_route",
                local_value=local_route,
                online_value=online_route,
                local_summary=str(local_route),
                online_summary=str(online_route),
                equal=local_route == online_route,
            )

        lemma_id = sample["representative_lemma_id"]
        lemma_ref = sample["representative_lemma_ref"]
        if lemma_id is not None and lemma_ref is not None:
            online.lookup_exact(sample["representative_lemma_text"] or "")
            local_entry = local.entry_for_ref(lemma_ref)
            online_entry = online.entry_for_ref(lemma_ref)
            if local_entry is None and online_entry is None:
                entry_equal = True
            elif local_entry is None or online_entry is None:
                entry_equal = False
            else:
                entry_equal = (
                    local_entry.lemma.semantic_ref == online_entry.lemma.semantic_ref
                    and local_entry.lemma.lemma == online_entry.lemma.lemma
                    and local_entry.lemma.pos == online_entry.lemma.pos
                )
            report.add(
                CaseResult(
                    name="entry_for_ref:lemma_ref",
                    category="entry",
                    passed=bool(entry_equal)
                    and (local_entry is not None)
                    and (online_entry is not None),
                    detail=(
                        f"local_lemma={local_entry.lemma.lemma if local_entry else None!r} "
                        f"online_lemma={online_entry.lemma.lemma if online_entry else None!r}"
                    ),
                    local_summary=(
                        "; ".join(_summarize_sense(s) for s in local_entry.senses)
                        if local_entry
                        else "(none)"
                    ),
                    online_summary=(
                        "; ".join(_summarize_sense(s) for s in online_entry.senses)
                        if online_entry
                        else "(none)"
                    ),
                )
            )
            if local_entry and online_entry:
                _diff_case(
                    "entry_for_ref:senses",
                    "entry",
                    local_value=local_entry.senses,
                    online_value=online_entry.senses,
                    local_summary="; ".join(
                        _summarize_sense(s) for s in local_entry.senses
                    ) or "(none)",
                    online_summary="; ".join(
                        _summarize_sense(s) for s in online_entry.senses
                    ) or "(none)",
                    equal=_compare_sense_records(
                        local_entry.senses, online_entry.senses
                    ),
                )
                _diff_case(
                    "entry_for_ref:meanings",
                    "meanings",
                    local_value=local_entry.meanings,
                    online_value=online_entry.meanings,
                    local_summary="; ".join(
                        _summarize_meaning(m) for m in local_entry.meanings
                    ) or "(none)",
                    online_summary="; ".join(
                        _summarize_meaning(m) for m in online_entry.meanings
                    ) or "(none)",
                    equal=_compare_meaning_records(
                        local_entry.meanings, online_entry.meanings
                    ),
                )
                _diff_case(
                    "entry_for_ref:examples",
                    "examples",
                    local_value=local_entry.examples,
                    online_value=online_entry.examples,
                    local_summary="; ".join(
                        _summarize_example(e) for e in local_entry.examples
                    ) or "(none)",
                    online_summary="; ".join(
                        _summarize_example(e) for e in online_entry.examples
                    ) or "(none)",
                    equal=_compare_example_records(
                        local_entry.examples, online_entry.examples
                    ),
                )

        if sample["representative_example_ids"]:
            repr_examples = sample["representative_example_ids"]
            online_lookup_keys = lookup_buckets_for_text(
                sample["representative_lemma_text"] or ""
            )
            online_lease_buckets: set[int] = set(online_lookup_keys)
            for eid in repr_examples:
                online_lease_buckets.add(int(example_bucket(int(eid))))
            report.add(
                CaseResult(
                    name="example_routing:closure",
                    category="routing",
                    passed=True,
                    detail=(
                        "lemma_buckets="
                        f"{list(online_lookup_keys)}, example_buckets="
                        f"{sorted(set(example_bucket(int(eid)) for eid in repr_examples))}"
                    ),
                )
            )

        local_input_buckets = sorted(
            lookup_buckets_for_text(case_letter_text)
        )
        online_input_buckets = sorted(
            online_lookup_keys if sample["representative_lemma_text"] else tuple()
        )
        local_lemma_buckets = sorted(
            lookup_buckets_for_text(case_letter_text)
        )
        report.add(
            CaseResult(
                name="routing:256_lookup_buckets",
                category="routing",
                passed=(
                    all(0 <= b < LOOKUP_FAMILY_SIZE for b in local_input_buckets)
                    and all(
                        0 <= b < LOOKUP_FAMILY_SIZE for b in online_input_buckets
                    )
                ),
                detail=(
                    f"local_buckets={local_input_buckets} "
                    f"online_buckets={online_input_buckets} "
                    f"lemma_buckets={local_lemma_buckets}"
                ),
            )
        )

        report.add(
            CaseResult(
                name="routing:64_example_buckets",
                category="routing",
                passed=(
                    all(
                        0 <= int(eid) % 64 < 64
                        for eid in (sample["representative_example_ids"] or [])
                    )
                ),
                detail=(
                    "first_examples="
                    f"{sample['representative_example_ids']}"
                ),
            )
        )

        try:
            online.sense_route("sense:v1:" + "0" * 64)
            unknown_route_local = (
                local.sense_route("sense:v1:" + "0" * 64) is None
            )
            unknown_route_online = online.sense_route(
                "sense:v1:" + "0" * 64
            ) is None
            report.add(
                CaseResult(
                    name="sense_route:unknown_sense_ref",
                    category="sense_route",
                    passed=unknown_route_local and unknown_route_online,
                    detail=(
                        f"local_none={unknown_route_local} online_none={unknown_route_online}"
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - structural fail-closed guard.
            report.add(
                CaseResult(
                    name="sense_route:unknown_sense_ref",
                    category="sense_route",
                    passed=False,
                    detail=f"raised: {exc}",
                )
            )
    finally:
        try:
            local_conn.close()
        except Exception:  # noqa: BLE001 - close-time errors are non-fatal.
            pass
        try:
            online.close()
        except Exception:  # noqa: BLE001 - close-time errors are non-fatal.
            pass

    report.finalise()
    return report


def run_public_verification(
    *,
    release_tag: str,
    download_dir: Path,
    report: DiffReport | None = None,
) -> DiffReport:
    """Download + verify the production ``dictionary-online-v2`` release anonymously.

    The verifier uses the trusted :class:`GitHubReleaseProductTransport`
    so its downloads travel the same code path the Product Online
    provider uses. No authenticated bytes are consumed.
    """
    started = datetime.now(timezone.utc).isoformat()
    if report is None:
        report = DiffReport(mode="public", started_at=started)

    from app.online_manifest import TrustedDistribution
    distribution = TrustedDistribution(
        base_origin="https://github.com",
        release_tag=release_tag,
        redirect_policy="github_release_redirect_only",
    )

    download_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = download_dir / "dictionary-online-manifest-v2.json"

    from app.online_transport import build_seam_transport
    transport = build_seam_transport(distribution)
    try:
        manifest_bytes = transport.fetch(
            type("M", (), {"asset": type("A", (), {"name": manifest_path.name})()})()
        )
    except Exception as exc:  # noqa: BLE001
        report.add(
            CaseResult(
                name="manifest_download",
                category="integrity",
                passed=False,
                detail=f"manifest fetch failed: {exc}",
            )
        )
        report.finalise()
        return report
    manifest_path.write_bytes(manifest_bytes)
    report.add(
        CaseResult(
            name="manifest_download",
            category="integrity",
            passed=True,
            detail=f"bytes={len(manifest_bytes)}",
        )
    )

    manifest = parse_manifest(manifest_path.read_text())
    report.add(
        CaseResult(
            name="manifest_dataset_token",
            category="dataset",
            passed=manifest.dataset_token == EXPECTED_SOURCE_SHA256,
            detail=f"{manifest.dataset_token}",
        )
    )
    if not _validate_dataset_token(manifest, report):
        report.finalise()
        return report
    if not _validate_topology(manifest, report):
        report.finalise()
        return report

    ok = True
    for asset in manifest.assets:
        target_path = download_dir / asset.name
        try:
            payload = transport.fetch(
                type("M", (), {"asset": type("A", (), {"name": asset.name})()})()
            )
        except Exception as exc:  # noqa: BLE001
            report.add(
                CaseResult(
                    name=f"asset_download:{asset.name}",
                    category="integrity",
                    passed=False,
                    detail=f"transport error: {exc}",
                )
            )
            ok = False
            continue
        target_path.write_bytes(payload)
        size_ok = len(payload) == asset.byte_size
        sha_ok = sha256(payload).hexdigest() == asset.sha256
        passed = size_ok and sha_ok
        detail = f"bytes={len(payload)} manifest_bytes={asset.byte_size} sha_ok={sha_ok}"
        if not passed:
            ok = False
        report.add(
            CaseResult(
                name=f"asset_ok:{asset.name}",
                category="integrity",
                passed=passed,
                detail=detail,
            )
        )
    if not ok:
        report.finalise()
        return report
    report.add(
        CaseResult(
            name="asset_count_drift",
            category="integrity",
            passed=len(manifest.assets) == 577,
            detail=f"manifest declares {len(manifest.assets)}",
        )
    )
    report.finalise()
    return report


def _print_summary(report: DiffReport) -> int:
    """Render the report to stdout and return the recommended exit code."""
    data = report.to_dict()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    if data["passed"]:
        return 0
    structural_failure = any(
        not c["passed"]
        for c in data["cases"]
        if c["category"] in ("integrity", "topology", "dataset", "source")
    )
    if structural_failure:
        return 1
    public_failure = data["mode"] == "public" and not data["passed"]
    if public_failure:
        return 3
    return 2


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    local = subparsers.add_parser(
        "local", help="Local pre-publication staging verification."
    )
    local.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to the verified v2 full dictionary asset.",
    )
    local.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to the validated staging production manifest.",
    )
    local.add_argument(
        "--corpus",
        type=Path,
        required=True,
        help="Directory containing the validated staging corpus assets.",
    )
    local.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional explicit cache directory (default: ephemeral).",
    )
    local.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write the JSON report.",
    )

    public = subparsers.add_parser(
        "public", help="Anonymous post-publication public release verification."
    )
    public.add_argument(
        "--release-tag",
        type=str,
        default="dictionary-online-v2",
        help="Release tag to verify (default: dictionary-online-v2).",
    )
    public.add_argument(
        "--download-dir",
        type=Path,
        required=True,
        help="Directory into which the manifest and assets are downloaded.",
    )
    public.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path to write the JSON report.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)
    if args.mode == "local":
        report = run_local_verification(
            source_path=args.source,
            manifest_path=args.manifest,
            corpus_dir=args.corpus,
            cache_dir=args.cache_dir,
        )
    else:
        report = run_public_verification(
            release_tag=args.release_tag,
            download_dir=args.download_dir,
        )
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
        )
    return _print_summary(report)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DiffReport",
    "CaseResult",
    "EXPECTED_SOURCE_BYTES",
    "EXPECTED_SOURCE_SHA256",
    "run_local_verification",
    "run_public_verification",
    "select_sample",
]

# ruff: noqa: E501
