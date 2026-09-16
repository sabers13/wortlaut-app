"""Dictionary session facade: the runtime view of the active provider.

The :class:`DictionarySession` is the bound, process-wide facade used by
``app/api.py`` after Slice 12. It hides the choice of provider
(``LocalDictionaryProvider`` vs ``OnlineDictionaryProvider``) behind the
:func:`reading` context manager, the same lookup oracle shape the
frontend resolver already uses, and the structured ``asset_token``
snapshot used to validate stale picker tokens (ADR-0004 D47).

No persisted mode/state is introduced: every :class:`DictionarySession`
exists only for the lifetime of one process, and removal/activation
modes bind state to runtime attributes, not to the user database.

The class does not reimplement the resolver's lookup ladder; it wraps
the abstract :class:`DictionaryProvider` so ``resolve_token`` and
``resolve_word`` from :mod:`app.resolve` can be called unchanged.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from app.deck import DeckCardsListing, ReadingSnapshot
from app.provider import DictionaryProvider
from app.resolve import LookupProtocol
from app.selection import MAX_CARD_EXAMPLES, filter_candidates, rank_senses


class _RuntimeLocalFacade(Protocol):
    """Minimal surface of ``DictionaryRuntime`` that the session uses."""

    @property
    def asset_token(self) -> str: ...

    @property
    def lemma_ids(self) -> Mapping[str, int]: ...

    @property
    def sense_ids(self) -> Mapping[str, tuple[int, int]]: ...

    def reading(self) -> Any: ...

    def provider(self) -> DictionaryProvider: ...

    def materialize_lookup(self, query: str) -> tuple[str, tuple[Mapping[str, object], ...]]: ...

    def observe_card_render(
        self,
        card_id: int | None = None,
        *,
        deck_id: int | None = None,
    ) -> Mapping[str, object] | None: ...

    def observe_export_payload(
        self, deck_id: int | None = None
    ) -> tuple[Mapping[str, object], ...]: ...

    def activate_dictionary(self, path: Any, *, version: str = ...) -> None: ...


@dataclass
class _LocalBackedSnapshot:
    """Adapter from a ``ReadingSnapshot`` to the session shape the API needs."""

    asset_token: str
    lemma_ids: MappingProxyType[str, int]
    sense_ids: MappingProxyType[str, tuple[int, int]]
    provider: DictionaryProvider


class _ProviderBackedLemmaIds:
    """A read-only Mapping that looks up ``lemma_ref -> lemma_id`` via the provider.

    Used only for Online mode, where the provider exposes
    ``lemma_for_ref`` (O(1) entry-shard lookup) but does not maintain an
    upfront inverse map. Off-line mode keeps the eager ``MappingProxy``
    populated by ``DictionaryRuntime``. The proxy supports
    ``__contains__`` and ``__getitem__`` only as needed for the
    served-product endpoints (``in`` membership checks).
    """

    def __init__(self, provider: DictionaryProvider) -> None:
        self._provider = provider

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self._provider.lemma_for_ref(key) is not None

    def __getitem__(self, key: str) -> int:
        entry = self._provider.lemma_for_ref(key)
        if entry is None:
            raise KeyError(key)
        return int(entry.lemma_id)


class _ProviderBackedSenseIds:
    """A read-only Mapping proxy that looks up ``sense_ref -> (sense_id, lemma_id)``.

    Used in Online mode. The provider exposes ``senses_for_ref`` via the
    entry-shard; the proxy materializes the tuple on demand. Iteration
    is intentionally unsupported: the served-product paths only need
    membership and ``__getitem__``.
    """

    def __init__(self, provider: DictionaryProvider) -> None:
        self._provider = provider

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self._provider.sense_route(key) is not None

    def __getitem__(self, key: str) -> tuple[int, int]:
        route = self._provider.sense_route(key)
        if route is None:
            raise KeyError(key)
        lemma_ref, _ = route
        senses = self._provider.senses_for_ref(lemma_ref)
        for s in senses:
            if str(s.semantic_ref) == key:
                return (int(s.sense_id), int(s.lemma_id))
        raise KeyError(key)


class _ProviderOracle(LookupProtocol):
    """Provider-backed :class:`LookupProtocol` (CF1 adapter).

    The provider's hit type uses ``lemma_id`` while ``app.resolve``'s
    ``LemmaRecord`` uses ``id``; this adapter is the smallest possible
    mechanical bridge at the integration boundary.
    """

    def __init__(self, provider: DictionaryProvider) -> None:
        self._provider = provider

    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[Any]:
        from app.resolve import LemmaRecord

        hits = self._provider.lookup_exact(lemma, pos=pos, gender=gender)
        out = []
        for hit in hits:
            out.append(
                LemmaRecord(
                    id=int(hit.lemma_id),
                    lemma=str(hit.lemma),
                    pos=str(hit.pos),
                    gender=hit.gender,
                    semantic_ref=str(hit.semantic_ref),
                    freq_rank=hit.freq_rank,
                )
            )
        return tuple(out)

    def lookup_surface_form(self, form: str) -> Sequence[Any]:
        from app.resolve import LemmaRecord

        hits = self._provider.lookup_surface_form(form)
        out = []
        for hit in hits:
            out.append(
                LemmaRecord(
                    id=int(hit.lemma_id),
                    lemma=str(hit.lemma),
                    pos=str(hit.pos),
                    gender=hit.gender,
                    semantic_ref=str(hit.semantic_ref),
                    freq_rank=hit.freq_rank,
                )
            )
        return tuple(out)

    def lookup_senses(self, lemma_id: int) -> Sequence[Any]:
        from app.resolve import SenseRecord

        hits = self._provider.lookup_senses(int(lemma_id))
        return tuple(
            SenseRecord(
                id=int(h.sense_id),
                lemma_id=int(h.lemma_id),
                ord=int(h.ord),
                semantic_ref=str(h.semantic_ref),
            )
            for h in hits
        )


@dataclass(frozen=True)
class OnlineSessionInfo:
    """Exposed online-session metadata for the Settings UI and tests."""

    dataset_token: str
    asset_token: str
    cache_dir: str


def _provider_materialize_observation(
    provider: DictionaryProvider,
    *,
    user_db: Path,
    lemma_ref: str,
    sense_ref: str | None,
    asset_token: str,
    note_status: str,
    note_id: int,
    selected_languages: tuple[str, ...],
    user_meanings: dict[str, str],
    components: Sequence[Mapping[str, object]],
    has_custom_audio: bool,
    card_id: int | None = None,
    due_at: str = "",
    state: int = 0,
    deck_names: str = "",
) -> Mapping[str, object]:
    """Materialize a card-render / export observation using the provider only.

    Used in Online mode. The user-DB connection is opened once on the
    caller-supplied path for ``note_meaning_lang`` / ``custom_pronunciation``
    / ``note_user_meaning`` reads; the dictionary materialization goes
    through the abstract provider contract — never a raw dictionary
    SQLite connection.
    """
    entry = provider.entry_for_ref(lemma_ref, max_examples=MAX_CARD_EXAMPLES)
    if entry is None:
        lemma_map = None
        senses_tuple: tuple[MappingProxyType[str, object], ...] = ()
        meanings_tuple: tuple[MappingProxyType[str, object], ...] = ()
        examples_tuple: tuple[MappingProxyType[str, object], ...] = ()
    else:
        lemma_map = MappingProxyType(
            {
                "lemma": str(entry.lemma.lemma),
                "pos": str(entry.lemma.pos),
                "gender": entry.lemma.gender,
                "plural": entry.lemma.plural,
                "plural_none": int(entry.lemma.plural_none),
                "genitive_sg": entry.lemma.genitive_sg,
                "aux": entry.lemma.aux,
                "separable": int(entry.lemma.separable),
                "particle": entry.lemma.particle,
                "reflexive": int(entry.lemma.reflexive),
                "praesens_3sg": entry.lemma.praesens_3sg,
                "praeteritum_3sg": entry.lemma.praeteritum_3sg,
                "partizip_ii": entry.lemma.partizip_ii,
                "governs": entry.lemma.governs,
                "comparative": entry.lemma.comparative,
                "superlative": entry.lemma.superlative,
                "ipa": entry.lemma.ipa,
            }
        )
        senses_tuple = tuple(
            MappingProxyType(
                {
                    "id": int(s.sense_id),
                    "semantic_ref": str(s.semantic_ref),
                    "source_namespace": str(s.source_namespace),
                    "source_ref": str(s.source_ref),
                    "ord": int(s.ord),
                    "register": s.register,
                }
            )
            for s in entry.senses
        )
        # Filter meanings to only those attached to a sense with the
        # requested ``sense_ref`` (or all senses for derived compound).
        if sense_ref is not None:
            target_sense_ids = {
                int(s.sense_id) for s in entry.senses if str(s.semantic_ref) == sense_ref
            }
        else:
            target_sense_ids = {int(s.sense_id) for s in entry.senses}
        meanings_tuple = tuple(
            MappingProxyType(
                {
                    "id": int(m.ord),
                    "sense_id": int(m.sense_id),
                    "language": str(m.language),
                    "kind": str(m.kind),
                    "ord": int(m.ord),
                    "text": str(m.text),
                }
            )
            for m in entry.meanings
            if int(m.sense_id) in target_sense_ids
        )
        examples_tuple = tuple(
            MappingProxyType(
                {
                    "id": int(ex.example_id),
                    "de": str(ex.de),
                    "en": str(ex.en) if ex.en is not None else None,
                }
            )
            for ex in entry.examples
        )

    components_tuple: tuple[MappingProxyType[str, object], ...] = ()
    if note_status == "derived_compound" and components:
        component_results: list[MappingProxyType[str, object]] = []
        for cb in components:
            comp_lem_ref = str(cb.get("lemma_ref") or cb.get("lemma_semantic_ref") or "")
            comp_sense_ref = str(cb.get("sense_ref") or cb.get("sense_semantic_ref") or "")
            comp_entry = (
                provider.entry_for_ref(comp_lem_ref, skip_examples=True) if comp_lem_ref else None
            )
            comp_lemma_text = (
                comp_entry.lemma if comp_entry is not None else comp_lem_ref.split(":")[-1]
            )
            comp_meanings: dict[str, str] = {}
            if comp_sense_ref and comp_entry is not None:
                for sense in comp_entry.senses:
                    if str(sense.semantic_ref) != comp_sense_ref:
                        continue
                    for m in comp_entry.meanings:
                        if int(m.sense_id) != int(sense.sense_id):
                            continue
                        lang = str(m.language)
                        if lang not in ("de", "en"):
                            continue
                        if lang in comp_meanings:
                            continue
                        comp_meanings[lang] = str(m.text)
            component_results.append(
                MappingProxyType(
                    {
                        "lemma": comp_lemma_text,
                        "lemma_ref": comp_lem_ref,
                        "sense_ref": comp_sense_ref,
                        "meanings": MappingProxyType(comp_meanings),
                    }
                )
            )
        components_tuple = tuple(component_results)

    payload_dict: dict[str, object] = {
        "card_id": card_id if card_id is not None else 0,
        "note_id": int(note_id),
        "due_at": due_at,
        "state": state,
        "note_status": str(note_status),
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "asset_token": asset_token,
        "selected_languages": tuple(selected_languages),
        "user_meanings": MappingProxyType(dict(user_meanings)),
        "has_custom_audio": bool(has_custom_audio),
        "components": components_tuple,
        "lemma": lemma_map,
        "senses": senses_tuple,
        "meanings": meanings_tuple,
        "examples": examples_tuple,
        "deck_names": deck_names,
    }
    return MappingProxyType(payload_dict)


def _observe_card_render_online(
    *,
    provider: DictionaryProvider,
    user_db_path: Path,
    deck_id: int | None = None,
    card_id: int | None = None,
) -> Mapping[str, object] | None:
    """Read one due card from PART-B and materialize it through the provider."""
    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    try:
        if card_id is not None:
            row = conn.execute(
                """
                SELECT c.id AS card_id, c.note_id, c.due_at, c.state,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       n.status AS note_status
                FROM card c
                JOIN note n ON n.id = c.note_id
                WHERE c.id = ?
                """,
                (card_id,),
            ).fetchone()
        elif deck_id is not None:
            from datetime import datetime, timezone

            now_text = datetime.now(timezone.utc).isoformat()
            row = conn.execute(
                """
                SELECT c.id AS card_id, c.note_id, c.due_at, c.state,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       n.status AS note_status
                FROM card c
                JOIN note n ON n.id = c.note_id
                JOIN note_deck nd ON nd.note_id = n.id
                WHERE nd.deck_id = ? AND c.due_at <= ?
                ORDER BY c.due_at ASC, c.id ASC
                LIMIT 1
                """,
                (deck_id, now_text),
            ).fetchone()
        else:
            from datetime import datetime, timezone

            now_text = datetime.now(timezone.utc).isoformat()
            row = conn.execute(
                """
                SELECT c.id AS card_id, c.note_id, c.due_at, c.state,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       n.status AS note_status
                FROM card c
                JOIN note n ON n.id = c.note_id
                WHERE c.due_at <= ?
                ORDER BY c.due_at ASC, c.id ASC
                LIMIT 1
                """,
                (now_text,),
            ).fetchone()
        if row is None:
            return None

        c_id = int(row["card_id"])
        n_id = int(row["note_id"])
        due_at = str(row["due_at"])
        state = int(row["state"])
        lemma_ref = str(row["lemma_semantic_ref"])
        sense_ref = str(row["sense_semantic_ref"]) if row["sense_semantic_ref"] else None
        note_status = str(row["note_status"])

        lang_rows = conn.execute(
            "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang",
            (n_id,),
        ).fetchall()
        selected_langs = tuple(str(r[0]) for r in lang_rows) if lang_rows else ("de", "en")
        user_meanings_rows = conn.execute(
            "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?",
            (n_id,),
        ).fetchall()
        user_meanings_dict = {str(r[0]): str(r[1]) for r in user_meanings_rows}
        custom_row = conn.execute(
            "SELECT 1 FROM custom_pronunciation WHERE note_id = ?",
            (n_id,),
        ).fetchone()
        has_custom_audio = custom_row is not None

        components_raw: list[dict[str, str]] = []
        if note_status == "derived_compound":
            comp_rows = conn.execute(
                """
                SELECT component_ord, lemma_semantic_ref, sense_semantic_ref
                FROM note_dictionary_binding
                WHERE note_id = ? AND role = 'component'
                ORDER BY component_ord ASC
                """,
                (n_id,),
            ).fetchall()
            for cr in comp_rows:
                components_raw.append(
                    {
                        "lemma_ref": str(cr[1]),
                        "sense_ref": str(cr[2]),
                    }
                )

        return _provider_materialize_observation(
            provider,
            user_db=user_db_path,
            lemma_ref=lemma_ref,
            sense_ref=sense_ref,
            asset_token=str(provider.asset_token),
            note_status=note_status,
            note_id=n_id,
            selected_languages=selected_langs,
            user_meanings=user_meanings_dict,
            components=components_raw,
            has_custom_audio=has_custom_audio,
            card_id=c_id,
            due_at=due_at,
            state=state,
        )
    finally:
        conn.close()


def _observe_export_payload_online(
    *,
    provider: DictionaryProvider,
    user_db_path: Path,
    deck_id: int | None = None,
) -> tuple[Mapping[str, object], ...]:
    """Materialize every card in PART-B (or one deck) through the provider."""
    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    try:
        if deck_id is not None:
            rows = conn.execute(
                """
                SELECT c.id AS card_id, n.id AS note_id, n.status,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       GROUP_CONCAT(DISTINCT d.name) AS deck_names
                FROM card c
                JOIN note n ON n.id = c.note_id
                JOIN note_deck nd ON nd.note_id = n.id
                JOIN deck d ON d.id = nd.deck_id
                WHERE d.id = ?
                GROUP BY c.id, n.id, n.status, n.lemma_semantic_ref,
                         n.sense_semantic_ref
                ORDER BY c.id ASC
                """,
                (deck_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT c.id AS card_id, n.id AS note_id, n.status,
                       n.lemma_semantic_ref, n.sense_semantic_ref,
                       GROUP_CONCAT(DISTINCT d.name) AS deck_names
                FROM card c
                JOIN note n ON n.id = c.note_id
                LEFT JOIN note_deck nd ON nd.note_id = n.id
                LEFT JOIN deck d ON d.id = nd.deck_id
                GROUP BY c.id, n.id, n.status, n.lemma_semantic_ref,
                         n.sense_semantic_ref
                ORDER BY c.id ASC
                """
            ).fetchall()

        items: list[Mapping[str, object]] = []
        for row in rows:
            n_id = int(row["note_id"])
            note_status = str(row["status"])
            lemma_ref = str(row["lemma_semantic_ref"])
            sense_ref = str(row["sense_semantic_ref"]) if row["sense_semantic_ref"] else None
            deck_names_str = str(row["deck_names"]) if row["deck_names"] else ""

            lang_rows = conn.execute(
                "SELECT lang FROM note_meaning_lang WHERE note_id = ? ORDER BY lang",
                (n_id,),
            ).fetchall()
            selected_langs = tuple(str(r[0]) for r in lang_rows) if lang_rows else ("de", "en")
            user_meanings_rows = conn.execute(
                "SELECT lang, meaning_text FROM note_user_meaning WHERE note_id = ?",
                (n_id,),
            ).fetchall()
            user_meanings_dict = {str(r[0]): str(r[1]) for r in user_meanings_rows}
            custom_row = conn.execute(
                "SELECT 1 FROM custom_pronunciation WHERE note_id = ?",
                (n_id,),
            ).fetchone()
            has_custom_audio = custom_row is not None

            components_raw: list[dict[str, str]] = []
            if note_status == "derived_compound":
                comp_rows = conn.execute(
                    """
                    SELECT component_ord, lemma_semantic_ref, sense_semantic_ref
                    FROM note_dictionary_binding
                    WHERE note_id = ? AND role = 'component'
                    ORDER BY component_ord ASC
                    """,
                    (n_id,),
                ).fetchall()
                for cr in comp_rows:
                    components_raw.append(
                        {
                            "lemma_ref": str(cr[1]),
                            "sense_ref": str(cr[2]),
                        }
                    )

            items.append(
                _provider_materialize_observation(
                    provider,
                    user_db=user_db_path,
                    lemma_ref=lemma_ref,
                    sense_ref=sense_ref,
                    asset_token=str(provider.asset_token),
                    note_status=note_status,
                    note_id=n_id,
                    selected_languages=selected_langs,
                    user_meanings=user_meanings_dict,
                    components=components_raw,
                    has_custom_audio=has_custom_audio,
                    card_id=int(row["card_id"]),
                    deck_names=deck_names_str,
                )
            )
        return tuple(items)
    finally:
        conn.close()


class DictionarySession:
    """The Slice 12 session-scoped provider facade used by ``app/api.py``.

    A session owns one of:

    * a ``DictionaryRuntime`` (Offline mode) — exposes its asset as a
      :class:`LocalDictionaryProvider` plus the existing
      ``ReadingSnapshot`` data;
    * an :class:`OnlineDictionaryProvider` directly (Online mode).

    There is no persisted representation; the chosen mode lives only in
    this process. Restart behavior is derived from the canonical
    Offline asset + CLI invocation per ADR-0009.
    """

    def __init__(
        self,
        *,
        runtime: _RuntimeLocalFacade | None = None,
        provider: DictionaryProvider | None = None,
        online_info: OnlineSessionInfo | None = None,
        user_db_path: Path | str | None = None,
    ) -> None:
        if runtime is None and provider is None:
            raise ValueError("DictionarySession requires either a runtime or a provider")
        if runtime is not None and provider is not None:
            raise ValueError("DictionarySession accepts only one of runtime or provider")
        self._runtime = runtime
        self._direct_provider = provider
        self._online_info = online_info
        self._user_db_path = (
            Path(user_db_path).resolve() if isinstance(user_db_path, (str, Path)) else None
        )
        self._closed = False

    @property
    def is_online(self) -> bool:
        return self._direct_provider is not None

    @property
    def online_info(self) -> OnlineSessionInfo | None:
        return self._online_info

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._direct_provider is not None:
                self._direct_provider.close()
        except Exception:
            pass

    def asset_token(self) -> str:
        if self._runtime is not None:
            return str(self._runtime.asset_token)
        assert self._direct_provider is not None
        return str(self._direct_provider.asset_token)

    def provider(self) -> DictionaryProvider:
        if self._runtime is not None:
            return self._runtime.provider()
        assert self._direct_provider is not None
        return self._direct_provider

    def oracle(self) -> LookupProtocol:
        """Return a provider-backed :class:`LookupProtocol` for the resolver."""
        return _ProviderOracle(self.provider())

    @contextmanager
    def reading(self) -> Iterator[ReadingSnapshot | _LocalBackedSnapshot]:
        """Yield an inert immutable snapshot under one read pin."""
        if self._runtime is not None:
            with self._runtime.reading() as snap:
                yield snap
            return
        assert self._direct_provider is not None
        prov = self._direct_provider
        yield _LocalBackedSnapshot(
            asset_token=str(prov.asset_token),
            lemma_ids=_ProviderBackedLemmaIds(prov),  # type: ignore[arg-type]
            sense_ids=_ProviderBackedSenseIds(prov),  # type: ignore[arg-type]
            provider=prov,
        )

    def materialize_lookup(self, query: str) -> tuple[str, tuple[Mapping[str, object], ...]]:
        """Lookup helper matching the legacy runtime/materialize_lookup return shape."""
        if self._runtime is not None:
            result = self._runtime.materialize_lookup(query)
            return (result[0], tuple(result[1]))
        # Online path: produce the same MappingProxyType[str, object] shape.
        from types import MappingProxyType as _MP

        assert self._direct_provider is not None
        candidates = self._direct_provider.candidate_lookup(query)
        token = str(self._direct_provider.asset_token)
        out = []
        for cand in candidates:
            lemma = cand.lemma
            senses_data = []
            for sense, meanings in cand.senses:
                senses_data.append(
                    _MP(
                        {
                            "sense_id": int(sense.sense_id),
                            "sense_semantic_ref": str(sense.semantic_ref),
                            "source_namespace": str(sense.source_namespace),
                            "source_ref": str(sense.source_ref),
                            "ord": int(sense.ord),
                            "register": sense.register,
                            "meanings": tuple(
                                _MP(
                                    {
                                        "language": str(m.language),
                                        "kind": str(m.kind),
                                        "text": str(m.text),
                                        "ord": int(m.ord),
                                    }
                                )
                                for m in meanings
                            ),
                        }
                    )
                )
            out.append(
                _MP(
                    {
                        "lemma_id": int(lemma.lemma_id),
                        "lemma_semantic_ref": str(lemma.semantic_ref),
                        "lemma": str(lemma.lemma),
                        "pos": str(lemma.pos),
                        "gender": lemma.gender,
                        "senses": tuple(rank_senses(tuple(senses_data))),
                        "examples": (),
                    }
                )
            )
        return (token, tuple(filter_candidates(out)))

    def observe_card_render(
        self,
        card_id: int | None = None,
        *,
        deck_id: int | None = None,
    ) -> Mapping[str, object] | None:
        """Observe a card-render observation under a single read pin.

        Offline mode delegates to the legacy ``DictionaryRuntime``
        path. Online mode materializes the lemma / senses / meanings /
        examples through the abstract ``DictionaryProvider`` contract;
        no raw dictionary SQLite connection is opened.
        """
        if self._runtime is not None:
            return self._runtime.observe_card_render(card_id=card_id, deck_id=deck_id)
        assert self._direct_provider is not None
        if self._user_db_path is None:
            return None
        return _observe_card_render_online(
            provider=self._direct_provider,
            user_db_path=self._user_db_path,
            deck_id=deck_id,
            card_id=card_id,
        )

    def observe_export_payload(
        self, deck_id: int | None = None
    ) -> tuple[Mapping[str, object], ...]:
        """Observe export payloads inside a single read pin."""
        if self._runtime is not None:
            return self._runtime.observe_export_payload(deck_id=deck_id)
        if self._direct_provider is None or self._user_db_path is None:
            return ()
        return _observe_export_payload_online(
            provider=self._direct_provider,
            user_db_path=self._user_db_path,
            deck_id=deck_id,
        )

    def materialized_lookup_token(self) -> str:
        """Return the asset token without IO (helper for materialised shims)."""
        return self.asset_token()

    def list_deck_cards_for_management(
        self, deck_id: int
    ) -> tuple[DeckCardsListing, tuple[Mapping[str, object], ...]]:
        """Return the M1 deck-cards management projection plus lemma metadata.

        The first tuple element is the strict PART-B projection produced
        by :func:`app.deck.list_deck_cards`; the second element is the
        per-card dictionary metadata (headword / POS / gender) keyed by
        ``lemma_semantic_ref`` so the API layer can merge the two without
        re-reading the dictionary.

        Materialization never touches ``example_lemma`` or any
        example-family data; it uses only ``lemma_for_ref``. A missing
        lemma is reported as ``None`` and the API layer renders a
        PART-B-only fallback row instead of failing the whole deck.
        """
        if self._user_db_path is None:
            raise RuntimeError("DictionarySession has no user_db_path")
        provider = self.provider()
        listing = _list_deck_cards_direct(self._user_db_path, deck_id)
        lemma_meta = _lemma_meta_for_listing(provider, listing)
        return listing, lemma_meta


__all__ = [
    "DictionarySession",
    "OnlineSessionInfo",
]


def _list_deck_cards_direct(
    user_db_path: Path, deck_id: int
) -> DeckCardsListing:
    """Open the user DB on ``user_db_path`` and call :func:`list_deck_cards`."""
    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Import locally to keep the dependency direction one-way
        # (dictionary_session -> deck) without re-importing at module load.
        from app.deck import list_deck_cards

        return list_deck_cards(conn, deck_id)
    finally:
        conn.close()


def _lemma_meta_for_listing(
    provider: DictionaryProvider, listing: DeckCardsListing
) -> tuple[Mapping[str, object], ...]:
    """Return one metadata Mapping per card, in the same order as ``listing``."""
    metas: list[Mapping[str, object]] = []
    for card in listing.cards:
        lemma_ref = card.lemma_semantic_ref
        lemma_entry = provider.lemma_for_ref(lemma_ref)
        if lemma_entry is None:
            metas.append(
                {
                    "lemma_semantic_ref": lemma_ref,
                    "headword": None,
                    "pos": None,
                    "gender": None,
                }
            )
            continue
        metas.append(
            {
                "lemma_semantic_ref": lemma_ref,
                "headword": str(lemma_entry.lemma),
                "pos": str(lemma_entry.pos),
                "gender": lemma_entry.gender,
            }
        )
    return tuple(metas)


__all__ = [
    "DictionarySession",
    "OnlineSessionInfo",
]
