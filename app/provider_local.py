"""Local dictionary provider.

Adapts the existing trusted Local dictionary/runtime behavior to the
abstract ``DictionaryProvider`` contract. It reuses
``app.dictionary.Dictionary`` and the asset handle already validated by
``app.dictionary.validate_candidate_dictionary`` so that all PART-A reads
stay aligned with the D47 stable-ref contract.

Low-level SQLite remains valid inside this provider boundary; the
provider does **not** expose a raw ``sqlite3.Connection`` as a public
operation, per ADR-0009.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.dictionary import (
    Dictionary,
    DictionaryAsset,
    ExampleEntry,
    MeaningEntry,
    validate_candidate_dictionary,
)
from app.dictionary import (
    LemmaEntry as LocalLemmaEntry,
)
from app.dictionary import (
    SenseEntry as LocalSenseEntry,
)
from app.provider import (
    CandidateLookup,
    CompoundComponent,
    DictionaryProvider,
    ExampleRecord,
    LemmaEntry,
    LemmaHit,
    MeaningRow,
    ProviderUnavailableError,
    SenseEntry,
    SenseHit,
)
from app.provider import (
    DictionaryEntry as ProviderEntry,
)


def _local_lemma_to_hit(lemma: LocalLemmaEntry) -> LemmaHit:
    """Project a local ``LemmaEntry`` to a provider resolver-facing hit."""
    return LemmaHit(
        lemma_id=int(lemma.id or 0),
        lemma=str(lemma.lemma),
        pos=str(lemma.pos),
        gender=lemma.gender,
        semantic_ref=str(lemma.semantic_ref or ""),
        freq_rank=lemma.freq_rank,
    )


def _local_sense_to_hit(sense: LocalSenseEntry) -> SenseHit:
    """Project a local ``SenseEntry`` to a provider resolver-facing hit."""
    return SenseHit(
        sense_id=int(sense.id or 0),
        lemma_id=int(sense.lemma_id or 0),
        ord=int(sense.ord),
        semantic_ref=str(sense.semantic_ref),
    )


def _local_lemma_to_entry(lemma: LocalLemmaEntry) -> LemmaEntry:
    """Project a local ``LemmaEntry`` to the provider full row shape."""
    return LemmaEntry(
        lemma_id=int(lemma.id),
        semantic_ref=str(lemma.semantic_ref or ""),
        lemma=str(lemma.lemma),
        pos=str(lemma.pos),
        gender=lemma.gender,
        freq_rank=lemma.freq_rank,
        plural=lemma.plural,
        plural_none=int(lemma.plural_none),
        genitive_sg=lemma.genitive_sg,
        aux=lemma.aux,
        separable=int(lemma.separable),
        particle=lemma.particle,
        reflexive=int(lemma.reflexive),
        praesens_3sg=lemma.praesens_3sg,
        praeteritum_3sg=lemma.praeteritum_3sg,
        partizip_ii=lemma.partizip_ii,
        governs=lemma.governs,
        comparative=lemma.comparative,
        superlative=lemma.superlative,
        ipa=lemma.ipa,
        source=lemma.source,
        license=lemma.license,
    )


def _local_sense_to_entry(sense: LocalSenseEntry) -> SenseEntry:
    """Project a local ``SenseEntry`` to the provider full row shape."""
    return SenseEntry(
        sense_id=int(sense.id or 0),
        lemma_id=int(sense.lemma_id or 0),
        semantic_ref=str(sense.semantic_ref),
        source_namespace=str(sense.source_namespace),
        source_ref=str(sense.source_ref),
        ord=int(sense.ord),
        register=sense.register,
        source=sense.source,
        license=sense.license,
    )


def _local_meaning_to_row(meaning: MeaningEntry) -> MeaningRow:
    """Project a local ``MeaningEntry`` to the provider row shape."""
    return MeaningRow(
        sense_id=int(meaning.sense_id),
        language=str(meaning.language),
        kind=str(meaning.kind),
        ord=int(meaning.ord),
        text=str(meaning.text),
        source=str(meaning.source),
        license=str(meaning.license),
    )


def _local_example_to_record(example: ExampleEntry) -> ExampleRecord:
    """Project a local ``ExampleEntry`` to the provider row shape."""
    return ExampleRecord(
        example_id=int(example.id),
        de=str(example.de),
        en=example.en,
        source=example.source,
        source_ref=example.source_ref,
        license=example.license,
        token_count=example.token_count,
        has_proper=int(example.has_proper),
    )


@dataclass(frozen=True)
class _RefMaps:
    """Durable refs to active-asset numeric ID maps derived from one asset."""

    lemma_ids: Mapping[str, int]
    sense_ids: Mapping[str, tuple[int, int]]


class LocalDictionaryProvider(DictionaryProvider):
    """Adapter from the Local SQLite asset to the abstract provider contract."""

    def __init__(
        self,
        dict_path: Path | str,
        *,
        asset: DictionaryAsset | None = None,
    ) -> None:
        self._path = Path(dict_path)
        if asset is None:
            if not self._path.exists():
                raise ProviderUnavailableError(f"dictionary file not found: {self._path}")
            try:
                self._asset = validate_candidate_dictionary(self._path)
            except Exception as exc:
                raise ProviderUnavailableError(
                    f"failed to open Local dictionary asset: {exc}"
                ) from exc
        else:
            self._asset = asset
        self._dictionary = Dictionary(self._path)
        self._refs = _RefMaps(
            lemma_ids=MappingProxyType(dict(self._asset.lemma_ids)),
            sense_ids=MappingProxyType(dict(self._asset.sense_ids)),
        )
        self._closed = False

    @property
    def asset_token(self) -> str:
        """Return the active dictionary asset token (D47)."""
        return self._asset.sha256

    @property
    def path(self) -> Path:
        """Return the dictionary file path (Local-only helper)."""
        return self._path

    @property
    def lemma_ids(self) -> Mapping[str, int]:
        """Return the durable ``lemma_ref -> lemma_id`` map."""
        return self._refs.lemma_ids

    @property
    def sense_ids(self) -> Mapping[str, tuple[int, int]]:
        """Return the durable ``sense_ref -> (sense_id, lemma_id)`` map."""
        return self._refs.sense_ids

    def close(self) -> None:
        """Release the underlying dictionary handle; safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        try:
            self._dictionary.close()
        except Exception:
            pass
        try:
            self._asset.close()
        except Exception:
            pass

    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[LemmaHit]:
        """Delegate to the existing Local lookup closure."""
        rows = self._dictionary.lookup_exact(lemma, pos=pos, gender=gender)
        return [_local_lemma_to_hit(row) for row in rows]

    def lookup_surface_form(self, form: str) -> Sequence[LemmaHit]:
        """Delegate to the existing Local surface-form closure."""
        rows = self._dictionary.lookup_surface_form(form)
        return [_local_lemma_to_hit(row) for row in rows]

    def lookup_senses(self, lemma_id: int) -> Sequence[SenseHit]:
        """Delegate to the existing Local senses-by-id closure."""
        rows = self._dictionary.lookup_senses(lemma_id)
        return [_local_sense_to_hit(row) for row in rows]

    def lemma_for_ref(self, lemma_semantic_ref: str) -> LemmaEntry | None:
        """Resolve one durable ``lemma_ref`` to a full lemma row."""
        lemma_id = self._refs.lemma_ids.get(lemma_semantic_ref)
        if lemma_id is None:
            return None
        return self.lemma_for_id(int(lemma_id))

    def lemma_for_id(self, lemma_id: int) -> LemmaEntry | None:
        """Resolve a numeric ``lemma_id`` cache to a full lemma row."""
        row = self._dictionary.get_lemma_by_id(int(lemma_id))
        if row is None:
            return None
        return _local_lemma_to_entry(row)

    def senses_for_lemma(self, lemma_id: int) -> Sequence[SenseEntry]:
        """Return the full senses for a numeric ``lemma_id`` cache."""
        rows = self._dictionary.get_senses_for_lemma(int(lemma_id))
        return [_local_sense_to_entry(row) for row in rows]

    def senses_for_ref(self, lemma_semantic_ref: str) -> Sequence[SenseEntry]:
        """Return the full senses for a durable ``lemma_ref``."""
        lemma_id = self._refs.lemma_ids.get(lemma_semantic_ref)
        if lemma_id is None:
            return ()
        return self.senses_for_lemma(int(lemma_id))

    def meanings_for_lemma(self, lemma_id: int) -> Sequence[MeaningRow]:
        """Return all localized meanings for one numeric ``lemma_id``."""
        rows = self._dictionary.get_meanings_for_lemma(int(lemma_id))
        return [_local_meaning_to_row(row) for row in rows]

    def meanings_for_sense(self, sense_id: int) -> Sequence[MeaningRow]:
        """Return all localized meanings for one numeric ``sense_id``."""
        rows = self._dictionary.get_meanings_for_sense(int(sense_id))
        return [_local_meaning_to_row(row) for row in rows]

    def examples_for_lemma(
        self, lemma_id: int, *, limit: int | None = None
    ) -> Sequence[ExampleRecord]:
        """Return the example sentences linked to a lemma."""
        rows = self._dictionary.get_examples_for_lemma(int(lemma_id), limit=limit)
        return [_local_example_to_record(row) for row in rows]

    def surface_forms_for_lemma(self, lemma_id: int) -> Sequence[str]:
        """Return the recorded surface forms for a lemma."""
        return list(self._dictionary.get_surface_forms_for_lemma(int(lemma_id)))

    def entry_for_ref(
        self,
        lemma_semantic_ref: str,
        *,
        skip_examples: bool = False,
        max_examples: int | None = None,
    ) -> ProviderEntry | None:
        """Return a composite entry for a durable ``lemma_ref``."""
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
    ) -> ProviderEntry | None:
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
    ) -> ProviderEntry:
        """Compose a provider entry for an already-resolved lemma."""
        senses = tuple(self.senses_for_lemma(lemma.lemma_id))
        meanings = tuple(self.meanings_for_lemma(lemma.lemma_id))
        examples = (
            ()
            if skip_examples
            else tuple(self.examples_for_lemma(lemma.lemma_id, limit=max_examples))
        )
        surface = tuple(self.surface_forms_for_lemma(lemma.lemma_id))
        return ProviderEntry(
            lemma=lemma,
            senses=senses,
            meanings=meanings,
            examples=examples,
            surface_forms=surface,
        )

    def candidate_lookup(self, query: str) -> Sequence[CandidateLookup]:
        """Resolve a bare query through the exact/surface ladder."""
        clean = (query or "").strip()
        if not clean:
            return ()
        token = self.asset_token
        exact = self.lookup_exact(clean)
        source = exact
        if not exact:
            source = self.lookup_surface_form(clean)
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

    def sense_route(self, sense_ref: str) -> tuple[str, str] | None:
        """Resolve ``sense_ref`` to ``(lemma_ref, sense_ref)`` via the asset maps."""
        if not isinstance(sense_ref, str) or not sense_ref:
            return None
        mapping = self._refs.sense_ids.get(sense_ref)
        if mapping is None:
            return None
        lemma_id = int(mapping[1])
        lemma_ref = next(
            (ref for ref, lid in self._refs.lemma_ids.items() if int(lid) == lemma_id),
            None,
        )
        if lemma_ref is None:
            return None
        return lemma_ref, sense_ref

    def compound_components(
        self, component_refs: Sequence[tuple[str, str]]
    ) -> tuple[CompoundComponent, ...]:
        """Return the ordered compound components for one D46 vector."""
        out: list[CompoundComponent] = []
        for lemma_ref, sense_ref in component_refs:
            lemma_id = self._refs.lemma_ids.get(lemma_ref)
            sense_id_pair = self._refs.sense_ids.get(sense_ref)
            if lemma_id is None or sense_id_pair is None or int(sense_id_pair[1]) != int(lemma_id):
                out.append(
                    CompoundComponent(
                        lemma_ref=lemma_ref,
                        sense_ref=sense_ref,
                        lemma=lemma_ref.split(":")[-1],
                        meanings_by_language={},
                    )
                )
                continue
            lemma_row = self._dictionary.get_lemma_by_id(int(lemma_id))
            if lemma_row is None:
                out.append(
                    CompoundComponent(
                        lemma_ref=lemma_ref,
                        sense_ref=sense_ref,
                        lemma=lemma_ref.split(":")[-1],
                        meanings_by_language={},
                    )
                )
                continue
            meanings = self._select_component_text(int(sense_id_pair[0]))
            out.append(
                CompoundComponent(
                    lemma_ref=lemma_ref,
                    sense_ref=sense_ref,
                    lemma=str(lemma_row.lemma),
                    meanings_by_language=meanings,
                )
            )
        return tuple(out)

    def _select_component_text(self, sense_id: int) -> dict[str, str]:
        """Return one deterministic localized text per supported language."""
        meanings = self._dictionary.get_meanings_for_sense(int(sense_id))
        result: dict[str, str] = {}
        for meaning in meanings:
            language = str(meaning.language)
            if language not in ("de", "en"):
                continue
            if language in result:
                continue
            result[language] = str(meaning.text)
        return result


__all__ = ["LocalDictionaryProvider"]
