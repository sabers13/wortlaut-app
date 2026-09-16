"""Provider contract for dictionary reads.

ADR-0009 introduces two implementations of one contract:

* ``LocalDictionaryProvider`` — the trusted Local full dictionary.
* ``OnlineDictionaryProvider`` — the trusted Online distribution (later).

Both serve the exact same logical v2 dataset token
``1698b9979099098bf8d6e6fd7f9194134a927d428e3c2b1905a626eb8ee67d4c``.
Switching providers within that dataset performs no D47 relink and never
uses numeric SQLite IDs as durable identity.

This module defines the typed immutable/read-only domain records and the
abstract provider operations that ``app/deck.py`` and ``app/api.py``
currently need. The contract deliberately does **not** expose a generic
``sqlite3.Connection``; storage stays inside each provider implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LemmaHit:
    """One authoritative lemma record returned from exact/surface lookup.

    The fields used by the resolver come from
    ``app.resolve.LemmaRecord`` extended with ``semantic_ref`` and
    ``freq_rank``. Numeric ``lemma_id`` is preserved as an active-asset
    routing cache only — never as durable identity across versions
    (ADR-0004 D47).
    """

    lemma_id: int
    lemma: str
    pos: str
    gender: str | None
    semantic_ref: str
    freq_rank: int | None


@dataclass(frozen=True, slots=True)
class SenseHit:
    """One authoritative sense row referenced from a lemma.

    Mirrors the fields the resolver needs from
    ``app.resolve.SenseRecord``: ``sense_id``, ``lemma_id``, ``ord`` and
    the durable ``semantic_ref``.
    """

    sense_id: int
    lemma_id: int
    ord: int
    semantic_ref: str


@dataclass(frozen=True, slots=True)
class SenseEntry:
    """Full sense row exposed by the provider.

    Includes source-side provenance (``source_namespace``,
    ``source_ref``) and the durable ``semantic_ref``. Sense
    ``source``/``license`` stay per-row (ADR-0004 D36, AGENTS R11).
    """

    sense_id: int
    lemma_id: int
    semantic_ref: str
    source_namespace: str
    source_ref: str
    ord: int
    register: str | None
    source: str | None
    license: str | None


@dataclass(frozen=True, slots=True)
class MeaningRow:
    """One localized meaning row from ``sense_meaning``.

    Field text is immutable dictionary data. The row carries its own
    ``source``/``license`` per row (ADR-0004 D36 / AGENTS R11).
    """

    sense_id: int
    language: str
    kind: str
    ord: int
    text: str
    source: str
    license: str


@dataclass(frozen=True, slots=True)
class ExampleRecord:
    """One example sentence row from the ``example`` family."""

    example_id: int
    de: str
    en: str | None
    source: str | None
    source_ref: str | None
    license: str | None
    token_count: int | None
    has_proper: int


@dataclass(frozen=True, slots=True)
class LemmaEntry:
    """Full lemma row exposed by the provider."""

    lemma_id: int
    semantic_ref: str
    lemma: str
    pos: str
    gender: str | None
    freq_rank: int | None
    plural: str | None
    plural_none: int
    genitive_sg: str | None
    aux: str | None
    separable: int
    particle: str | None
    reflexive: int
    praesens_3sg: str | None
    praeteritum_3sg: str | None
    partizip_ii: str | None
    governs: str | None
    comparative: str | None
    superlative: str | None
    ipa: str | None
    source: str | None
    license: str | None


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    """Composite entry: lemma + senses + meanings + examples + surface forms."""

    lemma: LemmaEntry
    senses: tuple[SenseEntry, ...]
    meanings: tuple[MeaningRow, ...]
    examples: tuple[ExampleRecord, ...]
    surface_forms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateLookup:
    """Candidate returned by a lookup-style read.

    The provider returns ``asset_token`` alongside every read so the
    picker/commit boundary can revalidate D47 dictionary identity
    (ADR-0004 D47).
    """

    asset_token: str
    lemma: LemmaEntry
    senses: tuple[tuple[SenseEntry, tuple[MeaningRow, ...]], ...]
    examples: tuple[ExampleRecord, ...]


@dataclass(frozen=True, slots=True)
class CompoundComponent:
    """One ordered compound component with the localized text selected for it."""

    lemma_ref: str
    sense_ref: str
    lemma: str
    meanings_by_language: dict[str, str]


class ProviderUnavailableError(RuntimeError):
    """Raised when a provider cannot serve the requested read at all.

    A missing dictionary, a corrupt asset, or an unmatched manifest raises
    this; ``needs_gloss`` / ``not_found`` are **not** valid translations
    here. Slice 12 maps this error onto a structured UI/API failure.
    """


class ProviderIntegrityError(ProviderUnavailableError):
    """Raised when an acquired shard fails integrity or structural validation."""


class ProviderBudgetExceededError(ProviderUnavailableError):
    """Raised when a top-level resolution operation would exceed the budget.

    For Online the limit is 32 new lookup-shard downloads; the provider
    never silently translates this into a dictionary miss.
    """


class ProviderNetworkError(ProviderUnavailableError):
    """Raised when Online retrieval cannot reach the trusted distribution.

    Network failures must remain distinguishable from dictionary misses.
    """


class DictionaryProvider(ABC):
    """The Slice 11 abstract provider contract.

    Both ``LocalDictionaryProvider`` and ``OnlineDictionaryProvider``
    implement this contract. Slice 11 does not migrate ``app/api.py``,
    but the contract is intentionally complete enough that
    ``_ConnectionLookupOracle`` and ``DictionaryRuntime`` readers can be
    rewritten onto it in Slice 12 without an additional shard route or
    family.
    """

    @property
    @abstractmethod
    def asset_token(self) -> str:
        """Return the active dictionary asset token (D47)."""

    @abstractmethod
    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[LemmaHit]:
        """Resolve exact lemma text against the active dataset."""

    @abstractmethod
    def lookup_surface_form(self, form: str) -> Sequence[LemmaHit]:
        """Resolve an inflected surface form to its lemma records."""

    @abstractmethod
    def lookup_senses(self, lemma_id: int) -> Sequence[SenseHit]:
        """Resolve senses for a numeric ``lemma_id`` (resolver seam)."""

    @abstractmethod
    def lemma_for_ref(self, lemma_semantic_ref: str) -> LemmaEntry | None:
        """Resolve a durable ``lemma_ref`` to a full lemma row."""

    @abstractmethod
    def lemma_for_id(self, lemma_id: int) -> LemmaEntry | None:
        """Resolve a numeric ``lemma_id`` cache to a full lemma row."""

    @abstractmethod
    def senses_for_lemma(self, lemma_id: int) -> Sequence[SenseEntry]:
        """Return the senses for a numeric ``lemma_id`` cache."""

    @abstractmethod
    def senses_for_ref(self, lemma_semantic_ref: str) -> Sequence[SenseEntry]:
        """Return the senses for a durable ``lemma_ref``."""

    @abstractmethod
    def meanings_for_lemma(self, lemma_id: int) -> Sequence[MeaningRow]:
        """Return all localized meanings attached to the senses of a lemma."""

    @abstractmethod
    def meanings_for_sense(self, sense_id: int) -> Sequence[MeaningRow]:
        """Return all localized meanings attached to one numeric sense."""

    @abstractmethod
    def examples_for_lemma(
        self, lemma_id: int, *, limit: int | None = None
    ) -> Sequence[ExampleRecord]:
        """Return the example sentences linked to a lemma via ``example_lemma``."""

    @abstractmethod
    def surface_forms_for_lemma(self, lemma_id: int) -> Sequence[str]:
        """Return the recorded surface forms for a lemma."""

    @abstractmethod
    def entry_for_ref(
        self,
        lemma_semantic_ref: str,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> DictionaryEntry | None:
        """Return a composite entry for a durable ``lemma_ref`` or ``None``.

        This is the read the card-render and export-payload paths use to
        materialize a lemma card from a stable semantic reference.
        """

    @abstractmethod
    def entry_for_id(
        self,
        lemma_id: int,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> DictionaryEntry | None:
        """Return a composite entry for a numeric ``lemma_id`` cache."""

    @abstractmethod
    def candidate_lookup(self, query: str) -> Sequence[CandidateLookup]:
        """Resolve a bare query against the lookup + surface-form ladder.

        Used by ``DictionaryRuntime.materialize_lookup`` and by the
        picker stage. Empty result means "no dictionary hit", which is a
        legitimate product outcome (e.g. ``needs_gloss``).
        """

    @abstractmethod
    def sense_route(self, sense_ref: str) -> tuple[str, str] | None:
        """Resolve ``sense_ref`` to ``(lemma_ref, sense_ref)``.

        Used by the derived-compound sense-route shard. Returns ``None``
        for an unknown sense_ref.
        """

    @abstractmethod
    def compound_components(
        self, component_refs: Sequence[tuple[str, str]]
    ) -> tuple[CompoundComponent, ...]:
        """Return the ordered compound components for one D46 vector.

        Each component is one durable ``(lemma_ref, sense_ref)`` pair
        resolved through the sense-route shard to the head lemma text
        and the deterministically-selected per-language localized text.
        """

    def close(self) -> None:
        """Release any retained resources; safe to call more than once."""


__all__ = [
    "CandidateLookup",
    "CompoundComponent",
    "DictionaryEntry",
    "DictionaryProvider",
    "ExampleRecord",
    "LemmaEntry",
    "LemmaHit",
    "MeaningRow",
    "ProviderBudgetExceededError",
    "ProviderIntegrityError",
    "ProviderNetworkError",
    "ProviderUnavailableError",
    "SenseEntry",
    "SenseHit",
]
