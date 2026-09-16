"""Online dictionary provider.

Implements the abstract ``DictionaryProvider`` contract from
``app.provider`` against the deterministic Online corpus defined in
``app.online_manifest``. The Online provider:

* trusts one committed manifest and only the committed Wortlaut
  distribution configuration;
* routes every lookup through ``bucket256_v1`` and the deterministic
  closure rule;
* resolves ``sense_ref -> lemma_ref`` through the bucket-closed
  ``sense_route`` table that lives INSIDE the lookup shard family
  (no entry-family scan);
* reads example payload from the 64-shard example family keyed by
  ``example.id % 64``;
* uses a Bloom membership filter sized from the actual unique
  closure-key set;
* charges the per-operation budget against NEW remote lookup-shard
  downloads only (entry/example acquisitions are free);
* supports one top-level operation budget across arbitrarily nested
  ``lookup_exact`` / ``lookup_surface_form`` / ``candidate_lookup`` /
  ``compound_components`` calls via the ``operation()`` context manager;
* distinguishes network/integrity/budget failures from dictionary
  misses.

Production corpus is built and validated by
``tools/build_online_dictionary.py``; tests use the deterministic
fixture transport.
"""

from __future__ import annotations

import contextvars
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.online_cache import ShardCache, ShardIdentity, ShardLease, ShardRequest
from app.online_filter import BloomFilter
from app.online_manifest import (
    DEFAULT_DATASET_TOKEN,
    ENTRY_FAMILY_SIZE,
    EXAMPLE_FAMILY_SIZE,
    LOOKUP_FAMILY_SIZE,
    SHARD_FAMILY_ENTRY,
    SHARD_FAMILY_EXAMPLE,
    SHARD_FAMILY_LOOKUP,
    ManifestAsset,
    OnlineManifest,
    lookup_buckets_from_query,
)
from app.provider import (
    CandidateLookup,
    CompoundComponent,
    DictionaryEntry,
    DictionaryProvider,
    ExampleRecord,
    LemmaEntry,
    LemmaHit,
    MeaningRow,
    ProviderBudgetExceededError,
    ProviderIntegrityError,
    SenseEntry,
    SenseHit,
)
from app.routing import bucket256_v1, example_bucket

MAX_NEW_LOOKUP_DOWNLOADS: int = 32


@dataclass(frozen=False, slots=True)
class _Budget:
    """Per-operation new lookup-shard download budget."""

    limit: int = MAX_NEW_LOOKUP_DOWNLOADS
    spent: int = 0
    new_identities: set[ShardIdentity] = field(default_factory=set)

    def charge(self, identity: ShardIdentity) -> None:
        """Charge one new download. Raises if the budget would be exceeded."""
        if identity in self.new_identities:
            return
        if self.spent + 1 > self.limit:
            raise ProviderBudgetExceededError(
                "online_dictionary_budget_exceeded: "
                f"limit {self.limit} lookup-shard downloads per top-level operation"
            )
        self.new_identities.add(identity)
        self.spent += 1


@dataclass(frozen=False, slots=True)
class _OperationBudget:
    """The budget associated with one top-level resolution operation."""

    budget: _Budget = field(default_factory=_Budget)

    def charge(self, identity: ShardIdentity) -> None:
        """Charge one new download against this operation's budget."""
        self.budget.charge(identity)


# Context-local active operation budget. A non-None value means nested
# provider reads share that budget; otherwise each entry-point creates
# its own throwaway budget. This is NOT a process-global counter; the
# contextvar is bound to the current async task / thread.
_active_operation_budget: contextvars.ContextVar[_Budget | None] = contextvars.ContextVar(
    "_active_operation_budget", default=None
)


class OnlineDictionaryProvider(DictionaryProvider):
    """Provider implementation against the trusted Online manifest."""

    def __init__(
        self,
        manifest: OnlineManifest,
        cache: ShardCache,
        *,
        filter_payload: bytes,
        dataset_token: str = DEFAULT_DATASET_TOKEN,
    ) -> None:
        if manifest.dataset_token != dataset_token:
            raise ProviderIntegrityError(
                f"manifest dataset token {manifest.dataset_token!r} "
                f"does not match expected {dataset_token!r}"
            )
        self._manifest = manifest
        self._cache = cache
        self._filter = BloomFilter.from_bytes(filter_payload)
        self._dataset_token = dataset_token
        # Process/session-local cache identity maps. Populated as rows
        # are legitimately observed by this provider instance. Used to
        # resolve a cold numeric ID without scanning 256 shards.
        self._lemma_id_to_ref: dict[int, str] = {}
        self._sense_id_to_ref: dict[int, str] = {}
        self._sense_id_to_lemma_ref: dict[int, str] = {}
        self._closed = False

    @property
    def asset_token(self) -> str:
        """Return the manifest dataset token as the active asset identity."""
        return self._dataset_token

    @property
    def manifest(self) -> OnlineManifest:
        """Return the validated manifest."""
        return self._manifest

    @property
    def filter(self) -> BloomFilter:
        """Return the membership filter used for lemma-oracle pruning."""
        return self._filter

    def close(self) -> None:
        """Idempotently close the provider. Active leases are still released."""
        self._closed = True

    def clear_cache(self) -> None:
        """Clear the Online provider's immutable shard cache directory.

        Delegates to :meth:`ShardCache.clear`, which serializes cache
        mutation against new acquisitions and active leases. The
        Settings ``Clear Online cache`` action uses this method so the
        Product trust boundary and the cache lifecycle stay aligned.
        """
        self._cache.clear()

    @contextmanager
    def operation(self) -> Iterator[_Budget]:
        """Bind one operation budget across nested provider reads.

        All provider reads executed within the ``with`` block share one
        budget. Reads outside the block fall back to a throwaway budget,
        preserving the existing per-call API. A nested ``operation()``
        block does not reset the budget: it yields the SAME budget object
        as the enclosing block, so downloads before, inside, and after
        the nested block accumulate into one counter. Only the outermost
        block creates, binds, and resets the budget.
        """
        existing = _active_operation_budget.get()
        if existing is not None:
            yield existing
            return
        budget = _Budget()
        token = _active_operation_budget.set(budget)
        try:
            yield budget
        finally:
            _active_operation_budget.reset(token)

    def _current_budget(self) -> _Budget:
        """Return the active operation budget, or a fresh throwaway one."""
        budget = _active_operation_budget.get()
        if budget is not None:
            return budget
        return _Budget()

    # ------------------------------------------------------------------
    # Provider reads
    # ------------------------------------------------------------------

    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[LemmaHit]:
        """Resolve exact lemma text against the Online lookup family."""
        return self._lookup_exact_with_budget(
            lemma, pos=pos, gender=gender, budget=self._current_budget()
        )

    def lookup_surface_form(self, form: str) -> Sequence[LemmaHit]:
        """Resolve an inflected surface form through the Online lookup family."""
        return self._lookup_exact_with_budget(form, surface=True, budget=self._current_budget())

    def lookup_senses(self, lemma_id: int) -> Sequence[SenseHit]:
        """Resolve senses for a numeric ``lemma_id`` cache.

        Numeric ``lemma_id`` is an active-asset cache identity only
        (ADR-0009 / AGENTS R13). The provider maps the ID to its durable
        ``lemma_ref`` from the in-process cache populated when lookup
        hits and entry reads legitimately observe the lemma. A cold
        unknown ID is documented cache-miss semantics and returns an
        empty tuple without triggering any 256-bucket scan.
        """
        if not isinstance(lemma_id, int) or isinstance(lemma_id, bool):
            raise TypeError("lemma_id must be an int")
        if lemma_id <= 0:
            return ()
        lemma_ref = self._lemma_ref_for_numeric_id(lemma_id)
        if lemma_ref is None:
            return ()
        senses = self.senses_for_ref(lemma_ref)
        return tuple(
            SenseHit(
                sense_id=int(s.sense_id),
                lemma_id=int(s.lemma_id),
                ord=int(s.ord),
                semantic_ref=str(s.semantic_ref),
            )
            for s in senses
        )

    def lemma_for_ref(self, lemma_semantic_ref: str) -> LemmaEntry | None:
        """Resolve a durable ``lemma_ref`` via the entry family."""
        if not isinstance(lemma_semantic_ref, str) or not lemma_semantic_ref:
            return None
        bucket = bucket256_v1(lemma_semantic_ref)
        lease = self._lease_entry(bucket)
        try:
            with self._open_readonly(lease) as conn:
                row = conn.execute(
                    "SELECT id, semantic_ref, lemma, pos, gender, freq_rank, plural, "
                    "plural_none, genitive_sg, aux, separable, particle, reflexive, "
                    "praesens_3sg, praeteritum_3sg, partizip_ii, governs, comparative, "
                    "superlative, ipa, source, license FROM lemma WHERE semantic_ref = ?",
                    (lemma_semantic_ref,),
                ).fetchone()
                if row is None:
                    return None
                # Populate the numeric cache identity map: a later
                # numeric downstream operation resolves without a scan.
                self._lemma_id_to_ref.setdefault(int(row[0]), lemma_semantic_ref)
                return _row_to_lemma_entry(row)
        finally:
            self._cache.release(lease)

    def lemma_for_id(self, lemma_id: int) -> LemmaEntry | None:
        """Resolve a numeric ``lemma_id`` cache to a lemma row."""
        lemma_ref = self._lemma_ref_for_numeric_id(int(lemma_id))
        if lemma_ref is None:
            return None
        return self.lemma_for_ref(lemma_ref)

    def senses_for_lemma(self, lemma_id: int) -> Sequence[SenseEntry]:
        """Return the senses for a numeric ``lemma_id`` cache."""
        lemma_ref = self._lemma_ref_for_numeric_id(int(lemma_id))
        if lemma_ref is None:
            return ()
        return self.senses_for_ref(lemma_ref)

    def senses_for_ref(self, lemma_semantic_ref: str) -> Sequence[SenseEntry]:
        """Return the senses for a durable ``lemma_ref``."""
        if not isinstance(lemma_semantic_ref, str) or not lemma_semantic_ref:
            return ()
        bucket = bucket256_v1(lemma_semantic_ref)
        lease = self._lease_entry(bucket)
        try:
            with self._open_readonly(lease) as conn:
                sense_rows = conn.execute(
                    "SELECT id, lemma_id, semantic_ref, source_namespace, source_ref, ord, "
                    "register, source, license FROM sense WHERE lemma_id = "
                    "(SELECT id FROM lemma WHERE semantic_ref = ?) "
                    "ORDER BY ord ASC, semantic_ref ASC, id ASC",
                    (lemma_semantic_ref,),
                ).fetchall()
                lemma_id_row = conn.execute(
                    "SELECT id FROM lemma WHERE semantic_ref = ?", (lemma_semantic_ref,)
                ).fetchone()
                lemma_id = int(lemma_id_row[0]) if lemma_id_row is not None else 0
                # Populate cache identity maps for any cold numeric IDs
                # we now legitimately observe.
                if lemma_id > 0:
                    self._lemma_id_to_ref.setdefault(lemma_id, lemma_semantic_ref)
                for row in sense_rows:
                    sid = int(row[0])
                    sref = str(row[2])
                    self._sense_id_to_ref.setdefault(sid, sref)
                    if lemma_id > 0:
                        self._sense_id_to_lemma_ref.setdefault(sid, lemma_semantic_ref)
                return tuple(_row_to_sense_entry(row, lemma_id=int(lemma_id)) for row in sense_rows)
        finally:
            self._cache.release(lease)

    def meanings_for_lemma(self, lemma_id: int) -> Sequence[MeaningRow]:
        """Return all localized meanings attached to the senses of a lemma."""
        lemma_ref = self._lemma_ref_for_numeric_id(int(lemma_id))
        if lemma_ref is None:
            return ()
        bucket = bucket256_v1(lemma_ref)
        lease = self._lease_entry(bucket)
        try:
            with self._open_readonly(lease) as conn:
                rows = conn.execute(
                    "SELECT sm.sense_id, sm.language, sm.kind, sm.ord, sm.text, sm.source, "
                    "sm.license FROM sense_meaning sm "
                    "JOIN sense s ON s.id = sm.sense_id "
                    "JOIN lemma l ON l.id = s.lemma_id "
                    "WHERE l.semantic_ref = ? "
                    "ORDER BY sm.language ASC, sm.kind ASC, sm.ord ASC, sm.id ASC",
                    (lemma_ref,),
                ).fetchall()
                return tuple(_row_to_meaning(row) for row in rows)
        finally:
            self._cache.release(lease)

    def meanings_for_sense(self, sense_id: int) -> Sequence[MeaningRow]:
        """Return all localized meanings attached to one numeric ``sense_id``."""
        sense_ref = self._sense_ref_for_numeric_id(int(sense_id))
        if sense_ref is None:
            return ()
        # Entry shards route by lemma_ref, so we must first translate
        # the sense_ref to its lemma_ref via the bucket-closed
        # sense_route index inside the lookup family.
        route = self.sense_route(sense_ref)
        if route is None:
            return ()
        lemma_ref, _ = route
        bucket = bucket256_v1(lemma_ref)
        lease = self._lease_entry(bucket)
        try:
            with self._open_readonly(lease) as conn:
                rows = conn.execute(
                    "SELECT sm.sense_id, sm.language, sm.kind, sm.ord, sm.text, sm.source, "
                    "sm.license FROM sense_meaning sm "
                    "JOIN sense s ON s.id = sm.sense_id "
                    "WHERE s.semantic_ref = ? "
                    "ORDER BY sm.language ASC, sm.kind ASC, sm.ord ASC, sm.id ASC",
                    (sense_ref,),
                ).fetchall()
                return tuple(_row_to_meaning(row) for row in rows)
        finally:
            self._cache.release(lease)

    def examples_for_lemma(
        self, lemma_id: int, *, limit: int | None = None
    ) -> Sequence[ExampleRecord]:
        """Return the example sentences linked to a lemma."""
        lemma_ref = self._lemma_ref_for_numeric_id(int(lemma_id))
        if lemma_ref is None:
            return ()
        bucket = bucket256_v1(lemma_ref)
        entry_lease = self._lease_entry(bucket)
        example_ids: list[int] = []
        limit_clause = f" LIMIT {int(limit)}" if limit is not None and limit >= 0 else ""
        try:
            with self._open_readonly(entry_lease) as conn:
                example_id_rows = conn.execute(
                    "SELECT el.example_id FROM example_lemma el "
                    "JOIN lemma l ON l.id = el.lemma_id "
                    "WHERE l.semantic_ref = ? "
                    "ORDER BY el.example_id ASC" + limit_clause,
                    (lemma_ref,),
                ).fetchall()
                example_ids = [int(r[0]) for r in example_id_rows]
        finally:
            self._cache.release(entry_lease)
        if not example_ids:
            return ()
        if limit is not None and limit >= 0:
            example_ids = example_ids[:limit]
        bucket_to_ids: dict[int, list[int]] = {}
        for example_id in example_ids:
            bucket_to_ids.setdefault(example_bucket(int(example_id)), []).append(int(example_id))
        results: list[ExampleRecord] = []
        wanted_ids = set(example_ids)
        for example_bucket_id, ids_in_bucket in sorted(bucket_to_ids.items()):
            example_lease = self._lease_example(int(example_bucket_id))
            try:
                with self._open_readonly(example_lease) as conn:
                    placeholders = ",".join("?" for _ in ids_in_bucket)
                    rows = conn.execute(
                        "SELECT id, de, en, source, source_ref, license, token_count, "
                        "has_proper FROM example WHERE id IN ("
                        + placeholders
                        + ") ORDER BY id ASC",
                        list(ids_in_bucket),
                    ).fetchall()
                    for row in rows:
                        eid = int(row[0])
                        if eid not in wanted_ids:
                            continue
                        results.append(
                            ExampleRecord(
                                example_id=eid,
                                de=str(row[1]),
                                en=str(row[2]) if row[2] is not None else None,
                                source=str(row[3]) if row[3] is not None else None,
                                source_ref=(str(row[4]) if row[4] is not None else None),
                                license=str(row[5]) if row[5] is not None else None,
                                token_count=(int(row[6]) if row[6] is not None else None),
                                has_proper=int(row[7]) if row[7] is not None else 0,
                            )
                        )
            finally:
                self._cache.release(example_lease)
        return tuple(results)

    def surface_forms_for_lemma(self, lemma_id: int) -> Sequence[str]:
        """Return the recorded surface forms for a lemma."""
        lemma_ref = self._lemma_ref_for_numeric_id(int(lemma_id))
        if lemma_ref is None:
            return ()
        bucket = bucket256_v1(lemma_ref)
        lease = self._lease_entry(bucket)
        try:
            with self._open_readonly(lease) as conn:
                rows = conn.execute(
                    "SELECT sf.form FROM surface_form sf "
                    "JOIN lemma l ON l.id = sf.lemma_id "
                    "WHERE l.semantic_ref = ? ORDER BY sf.form ASC",
                    (lemma_ref,),
                ).fetchall()
                return tuple(str(r[0]) for r in rows)
        finally:
            self._cache.release(lease)

    def entry_for_ref(
        self,
        lemma_semantic_ref: str,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> DictionaryEntry | None:
        """Return a composite entry for a durable ``lemma_ref``."""
        if not isinstance(lemma_semantic_ref, str) or not lemma_semantic_ref:
            return None
        lemma = self.lemma_for_ref(lemma_semantic_ref)
        if lemma is None:
            return None
        return self._build_entry(lemma, skip_examples=skip_examples, max_examples=max_examples)

    def entry_for_id(
        self,
        lemma_id: int,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> DictionaryEntry | None:
        """Return a composite entry for a numeric ``lemma_id`` cache."""
        lemma = self.lemma_for_id(int(lemma_id))
        if lemma is None:
            return None
        return self._build_entry(lemma, skip_examples=skip_examples, max_examples=max_examples)

    def _build_entry(
        self,
        lemma: LemmaEntry,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> DictionaryEntry:
        """Compose a provider entry for an already-resolved lemma."""
        senses = tuple(self.senses_for_ref(lemma.semantic_ref))
        meanings = tuple(self.meanings_for_lemma(lemma.lemma_id))
        examples = (
            ()
            if skip_examples
            else tuple(self.examples_for_lemma(lemma.lemma_id, limit=max_examples))
        )
        surface = tuple(self.surface_forms_for_lemma(lemma.lemma_id))
        return DictionaryEntry(
            lemma=lemma,
            senses=senses,
            meanings=meanings,
            examples=examples,
            surface_forms=surface,
        )

    def candidate_lookup(self, query: str) -> Sequence[CandidateLookup]:
        """Resolve a bare query against the lookup + surface-form ladder."""
        return self._candidate_lookup_with_budget(query, self._current_budget())

    def sense_route(self, sense_ref: str) -> tuple[str, str] | None:
        """Resolve ``sense_ref`` to ``(lemma_ref, sense_ref)``.

        The sense_route is bucket-closed inside the lookup shard family:
        ``bucket256_v1(sense_ref)`` is the lookup bucket that owns the
        ``sense_route`` row. The runtime fetches exactly that lookup
        shard, queries ``sense_route(sense_ref) -> lemma_ref``, and
        returns ``None`` if the sense_ref is unknown. No entry-family
        scan occurs. The lookup-family download shares the active
        operation budget exactly like any other new lookup shard.
        """
        return self._sense_route_with_budget(sense_ref, self._current_budget())

    def _sense_route_with_budget(self, sense_ref: str, budget: _Budget) -> tuple[str, str] | None:
        """Resolve ``sense_ref`` charging one lookup identity to ``budget``."""
        if not isinstance(sense_ref, str) or not sense_ref:
            return None
        bucket = bucket256_v1(sense_ref)
        if not 0 <= bucket < LOOKUP_FAMILY_SIZE:
            return None
        asset = self._lookup_asset_for_bucket(bucket)
        if asset is None:
            return None
        request = ShardRequest(
            identity=ShardIdentity(family=SHARD_FAMILY_LOOKUP, bucket=bucket),
            asset=asset,
        )
        lease = self._lease_with_budget(request, budget)
        try:
            with self._open_readonly(lease) as conn:
                row = conn.execute(
                    "SELECT lemma_ref FROM sense_route WHERE sense_ref = ?",
                    (sense_ref,),
                ).fetchone()
                if row is None:
                    return None
                return str(row[0]), sense_ref
        finally:
            self._cache.release(lease)

    def compound_components(
        self, component_refs: Sequence[tuple[str, str]]
    ) -> tuple[CompoundComponent, ...]:
        """Return the ordered compound components for one D46 vector."""
        out: list[CompoundComponent] = []
        for lemma_ref, sense_ref in component_refs:
            entry = self.lemma_for_ref(lemma_ref)
            lemma_text = entry.lemma if entry is not None else lemma_ref.split(":")[-1]
            meanings_by_lang = self._select_component_text(sense_ref)
            out.append(
                CompoundComponent(
                    lemma_ref=lemma_ref,
                    sense_ref=sense_ref,
                    lemma=lemma_text,
                    meanings_by_language=meanings_by_lang,
                )
            )
        return tuple(out)

    # ------------------------------------------------------------------
    # Budgeted reads (Slice 12 entrypoint shape)
    # ------------------------------------------------------------------

    def lookup_with_budget(self, query: str) -> tuple[Sequence[LemmaHit], _OperationBudget]:
        """Run exact-lemma lookup returning the per-operation budget state."""
        budget = _OperationBudget()
        hits = self._lookup_exact_with_budget(query, budget=budget.budget)
        return hits, budget

    def surface_with_budget(self, query: str) -> tuple[Sequence[LemmaHit], _OperationBudget]:
        """Run surface-form lookup returning the per-operation budget state."""
        budget = _OperationBudget()
        hits = self._lookup_exact_with_budget(query, surface=True, budget=budget.budget)
        return hits, budget

    def charge_for_test(self, identity: ShardIdentity, budget: _Budget) -> None:
        """Test-only seam to exercise budget accounting without I/O."""
        budget.charge(identity)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lookup_exact_with_budget(
        self,
        query: str,
        *,
        pos: str | None = None,
        gender: str | None = None,
        surface: bool = False,
        budget: _Budget,
    ) -> Sequence[LemmaHit]:
        if not isinstance(query, str) or not query:
            return ()
        # Bloom membership check for lemma-oracle pruning. Surface-form
        # lookup is allowed to bypass the filter because the filter only
        # covers authoritative lemma texts, not surface forms.
        if not surface and not self.filter.contains_query(query):
            return ()
        buckets = lookup_buckets_from_query(query)
        primary = query
        secondary = query.lower()
        results: dict[int, LemmaHit] = {}
        seen_lemma_ids: set[int] = set()
        for bucket in buckets:
            asset = self._lookup_asset_for_bucket(bucket)
            if asset is None:
                continue
            request = ShardRequest(
                identity=ShardIdentity(family=SHARD_FAMILY_LOOKUP, bucket=bucket),
                asset=asset,
            )
            lease = self._lease_lookup(request, budget)
            try:
                with self._open_readonly(lease) as conn:
                    if surface:
                        # Direct surface-form resolution: query the
                        # ``surface_form`` table ONLY. The lemma table
                        # is intentionally NOT consulted, so a query
                        # string that is BOTH an authoritative lemma
                        # AND a surface form of another lemma does NOT
                        # suppress valid surface_form matches (CF2).
                        rows = conn.execute(
                            "SELECT l.id, l.semantic_ref, l.lemma, l.pos, l.gender, "
                            "l.freq_rank FROM surface_form sf "
                            "JOIN lemma l ON sf.lemma_id = l.id "
                            "WHERE (sf.form = ? OR lower(sf.form) = ?) "
                            "ORDER BY l.freq_rank ASC NULLS LAST, l.pos ASC, "
                            "l.gender ASC NULLS LAST, l.semantic_ref ASC",
                            [primary, secondary],
                        ).fetchall()
                    else:
                        sql = (
                            "SELECT id, semantic_ref, lemma, pos, gender, freq_rank "
                            "FROM lemma WHERE (lemma = ? OR lower(lemma) = ?) "
                        )
                        params: list[Any] = [primary, secondary]
                        if pos is not None:
                            sql += "AND pos = ? "
                            params.append(pos)
                        if gender is not None:
                            sql += "AND gender = ? "
                            params.append(gender)
                        sql += (
                            "ORDER BY freq_rank ASC NULLS LAST, pos ASC, gender ASC NULLS LAST, "
                            "semantic_ref ASC"
                        )
                        rows = conn.execute(sql, params).fetchall()
                    for row in rows:
                        lemma_id = int(row[0])
                        lemma_ref = str(row[1])
                        # Populate cache identity maps as we observe rows.
                        self._lemma_id_to_ref.setdefault(lemma_id, lemma_ref)
                        if lemma_id in seen_lemma_ids:
                            continue
                        seen_lemma_ids.add(lemma_id)
                        results[lemma_id] = LemmaHit(
                            lemma_id=lemma_id,
                            lemma=str(row[2]),
                            pos=str(row[3]),
                            gender=str(row[4]) if row[4] is not None else None,
                            semantic_ref=lemma_ref,
                            freq_rank=int(row[5]) if row[5] is not None else None,
                        )
            finally:
                self._cache.release(lease)
        return tuple(results.values())

    def _candidate_lookup_with_budget(
        self, query: str, budget: _Budget
    ) -> Sequence[CandidateLookup]:
        if not isinstance(query, str) or not query:
            return ()
        # No lemma-Bloom guard around the whole ladder: the filter
        # covers authoritative LEMMA closure keys, not arbitrary surface
        # forms, so a valid inflected form absent from the lemma Bloom
        # must still reach the surface fallback. Exact-lemma lookup keeps
        # its own lemma Bloom pruning internally; the surface fallback
        # bypasses it.
        token = self.asset_token
        exact = self._lookup_exact_with_budget(query, budget=budget)
        source = exact
        if not source:
            source = self._lookup_exact_with_budget(query, surface=True, budget=budget)
        results: list[CandidateLookup] = []
        for hit in source:
            entry = self.entry_for_id(hit.lemma_id, skip_examples=True)
            if entry is None:
                continue
            results.append(
                CandidateLookup(
                    asset_token=token,
                    lemma=entry.lemma,
                    senses=tuple(
                        (sense, tuple(m for m in entry.meanings if m.sense_id == sense.sense_id))
                        for sense in entry.senses
                    ),
                    examples=entry.examples,
                )
            )
        return tuple(results)

    def _select_component_text(self, sense_ref: str) -> dict[str, str]:
        """Return one deterministic localized text per language for a sense.

        The compound-component read uses the bucket-closed ``sense_route``
        table to discover the parent ``lemma_ref``, then opens exactly the
        entry shard for that lemma. It deliberately does NOT bucket the
        entry shard by ``bucket256_v1(sense_ref)`` — that path was the
        compound sense lookup routing defect this repair removes.
        """
        if not isinstance(sense_ref, str) or not sense_ref:
            return {}
        route = self.sense_route(sense_ref)
        if route is None:
            return {}
        lemma_ref, _ = route
        bucket = bucket256_v1(lemma_ref)
        lease = self._lease_entry(bucket)
        try:
            with self._open_readonly(lease) as conn:
                rows = conn.execute(
                    "SELECT sm.language, sm.text FROM sense_meaning sm "
                    "JOIN sense s ON s.id = sm.sense_id "
                    "WHERE s.semantic_ref = ? "
                    "ORDER BY "
                    "CASE sm.language WHEN 'de' THEN 0 WHEN 'en' THEN 1 ELSE 2 END, "
                    "CASE sm.kind WHEN 'synonym' THEN 0 WHEN 'definition' THEN 1 "
                    "WHEN 'translation' THEN 2 ELSE 3 END, "
                    "sm.ord ASC, sm.id ASC",
                    (sense_ref,),
                ).fetchall()
                result: dict[str, str] = {}
                for lang, text in rows:
                    language = str(lang)
                    if language not in ("de", "en"):
                        continue
                    if language in result:
                        continue
                    result[language] = str(text)
                return result
        finally:
            self._cache.release(lease)

    def _lookup_asset_for_bucket(self, bucket: int) -> ManifestAsset | None:
        if bucket < 0 or bucket >= LOOKUP_FAMILY_SIZE:
            return None
        for asset in self._manifest.lookup_assets:
            if asset.bucket == bucket:
                return asset
        return None

    def _entry_asset_for_bucket(self, bucket: int) -> ManifestAsset | None:
        if bucket < 0 or bucket >= ENTRY_FAMILY_SIZE:
            return None
        for asset in self._manifest.entry_assets:
            if asset.bucket == bucket:
                return asset
        return None

    def _example_asset_for_bucket(self, bucket: int) -> ManifestAsset | None:
        if bucket < 0 or bucket >= EXAMPLE_FAMILY_SIZE:
            return None
        for asset in self._manifest.example_assets:
            if asset.bucket == bucket:
                return asset
        return None

    def _filter_asset(self) -> ManifestAsset:
        for asset in self._manifest.filter_assets:
            return asset
        raise ProviderIntegrityError("manifest declares no membership filter asset")

    def _lease_lookup(self, request: ShardRequest, budget: _Budget) -> ShardLease:
        """Acquire a lookup shard under the budget."""
        return self._lease_with_budget(request, budget)

    def _lease_entry(self, bucket: int) -> ShardLease:
        """Acquire an entry shard for the given durable bucket."""
        asset = self._entry_asset_for_bucket(int(bucket))
        if asset is None:
            raise ProviderIntegrityError(f"no entry asset for bucket {bucket}")
        request = ShardRequest(
            identity=ShardIdentity(family=SHARD_FAMILY_ENTRY, bucket=int(bucket)),
            asset=asset,
        )
        return self._cache.lease(request)

    def _lease_example(self, bucket: int) -> ShardLease:
        """Acquire an example shard for the given ``example.id % 64`` bucket."""
        asset = self._example_asset_for_bucket(int(bucket))
        if asset is None:
            raise ProviderIntegrityError(f"no example asset for bucket {bucket}")
        request = ShardRequest(
            identity=ShardIdentity(family=SHARD_FAMILY_EXAMPLE, bucket=int(bucket)),
            asset=asset,
        )
        return self._cache.lease(request)

    def _lease_with_budget(self, request: ShardRequest, budget: _Budget) -> ShardLease:
        """Acquire a shard; charge the budget before any new download.

        Entry / example shards are not counted against the per-operation
        lookup budget because the budget is on remote lookup downloads
        (ADR-0009: at most 32 new remote lookup-shard identities). The
        charge runs as the cache's ``before_download`` reservation hook:
        only the single-flight leader is charged, immediately before the
        transport is invoked (and before a corruption refetch), so the
        33rd distinct identity is rejected BEFORE any network transfer.
        Verified cache hits never reach the hook and stay free; duplicate
        identities in one operation charge once via budget deduplication;
        single-flight waiters perform no download and are not charged.
        """
        if request.identity.family != SHARD_FAMILY_LOOKUP:
            return self._cache.lease(request)
        return self._cache.lease(request, before_download=lambda identity: budget.charge(identity))

    @staticmethod
    def _open_readonly(lease: ShardLease) -> Any:
        """Open a verified lease as an immutable SQLite connection."""
        uri = f"file:{lease.snapshot_path.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _lemma_ref_for_numeric_id(self, lemma_id: int) -> str | None:
        """Resolve a numeric ``lemma_id`` cache to its durable ``lemma_ref``.

        Reads only the process-local cache populated when this provider
        instance legitimately observed the lemma via a lookup hit or an
        entry-shard read. A cold unknown numeric ID is a documented
        cache-miss (returns ``None``) without triggering any 256-bucket
        remote scan (ADR-0009 R1 / Defect R1B).
        """
        if not isinstance(lemma_id, int) or isinstance(lemma_id, bool):
            raise TypeError("lemma_id must be an int")
        if lemma_id <= 0:
            return None
        return self._lemma_id_to_ref.get(int(lemma_id))

    def _sense_ref_for_numeric_id(self, sense_id: int) -> str | None:
        """Resolve a numeric ``sense_id`` cache to its durable ``sense_ref``.

        Reads only the process-local cache populated when this provider
        instance legitimately observed the sense via the senses-for-ref
        read. A cold unknown numeric ID is a documented cache-miss.
        """
        if not isinstance(sense_id, int) or isinstance(sense_id, bool):
            raise TypeError("sense_id must be an int")
        if sense_id <= 0:
            return None
        return self._sense_id_to_ref.get(int(sense_id))


def _row_to_lemma_entry(row: sqlite3.Row) -> LemmaEntry:
    """Project an entry-shard SELECT result into a ``LemmaEntry``."""
    return LemmaEntry(
        lemma_id=int(row[0]),
        semantic_ref=str(row[1]),
        lemma=str(row[2]),
        pos=str(row[3]),
        gender=str(row[4]) if row[4] is not None else None,
        freq_rank=int(row[5]) if row[5] is not None else None,
        plural=str(row[6]) if row[6] is not None else None,
        plural_none=int(row[7]) if row[7] is not None else 0,
        genitive_sg=str(row[8]) if row[8] is not None else None,
        aux=str(row[9]) if row[9] is not None else None,
        separable=int(row[10]) if row[10] is not None else 0,
        particle=str(row[11]) if row[11] is not None else None,
        reflexive=int(row[12]) if row[12] is not None else 0,
        praesens_3sg=str(row[13]) if row[13] is not None else None,
        praeteritum_3sg=str(row[14]) if row[14] is not None else None,
        partizip_ii=str(row[15]) if row[15] is not None else None,
        governs=str(row[16]) if row[16] is not None else None,
        comparative=str(row[17]) if row[17] is not None else None,
        superlative=str(row[18]) if row[18] is not None else None,
        ipa=str(row[19]) if row[19] is not None else None,
        source=str(row[20]) if row[20] is not None else None,
        license=str(row[21]) if row[21] is not None else None,
    )


def _row_to_sense_entry(row: sqlite3.Row, *, lemma_id: int) -> SenseEntry:
    """Project an entry-shard SELECT result into a ``SenseEntry``."""
    return SenseEntry(
        sense_id=int(row[0]),
        lemma_id=int(lemma_id),
        semantic_ref=str(row[2]),
        source_namespace=str(row[3]),
        source_ref=str(row[4]),
        ord=int(row[5]),
        register=str(row[6]) if row[6] is not None else None,
        source=str(row[7]) if row[7] is not None else None,
        license=str(row[8]) if row[8] is not None else None,
    )


def _row_to_meaning(row: sqlite3.Row) -> MeaningRow:
    """Project an entry-shard SELECT result into a ``MeaningRow``."""
    return MeaningRow(
        sense_id=int(row[0]),
        language=str(row[1]),
        kind=str(row[2]),
        ord=int(row[3]),
        text=str(row[4]),
        source=str(row[5]),
        license=str(row[6]),
    )


__all__ = [
    "MAX_NEW_LOOKUP_DOWNLOADS",
    "OnlineDictionaryProvider",
    "_Budget",
    "_OperationBudget",
]
