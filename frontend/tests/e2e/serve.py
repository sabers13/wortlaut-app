"""Seed and serve the compiled FastAPI product for Playwright.

This launcher deliberately has no network or Vite dependency. It creates the
small PART-A/PART-B fixtures in a worktree-local directory, then starts the
same ``create_app`` factory used in production. No Piper runner or remote TTS
URL is configured, which makes automatic-pronunciation fallback deterministic.

Slice 12 introduces two deterministic served-product harness states:

* ``state A`` (default; canonical Offline dictionary installed): the
  existing offline / fully-local product path.
* ``state B`` (no canonical full Offline dictionary + deterministic
  fixture Online provider): the chooser state, exercised against the
  Slice-11 Online corpus fixture built from the same Local dictionary.
  The fixture Online corpus is reachable only through the backend
  Product trust/test seam (the in-process ``e2e_online_factory``);
  the browser never supplies a URL or manifest. The fixture Online
  provider is constructed ONLY after the user clicks ``Use Online``;
  startup-time zero Online construction is observed by the
  ``e2e_factory_invocations`` / ``e2e_transport_invocations``
  counters exposed via ``GET /__e2e/online-counters`` (E2E harness
  only, never served in production).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api import _get_nlp, create_app  # noqa: E402


def lemma_ref(lemma: str, pos: str, gender: str | None) -> str:
    payload = json.dumps(
        ["de", unicodedata.normalize("NFC", lemma), pos, gender or "<null>"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"lemma:v1:{hashlib.sha256(payload).hexdigest()}"


def sense_ref(lemma: str, pos: str, gender: str | None, source_ref: str) -> str:
    lemma_semantic_ref = lemma_ref(lemma, pos, gender)
    payload = json.dumps(
        [lemma_semantic_ref, "wiktextract:enwiktionary", source_ref],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return f"sense:v1:{hashlib.sha256(payload).hexdigest()}"


def part_a_schema() -> str:
    schema = (REPO_ROOT / "reference" / "schema.sql").read_text(encoding="utf-8")
    part_a, marker, _ = schema.partition("-- PART B")
    if not marker:
        raise RuntimeError("reference/schema.sql has no PART B marker")
    return part_a


def reset_state(state_dir: Path) -> None:
    """Remove only deterministic artifacts owned by this E2E launcher."""
    state_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "dictionary.sqlite",
        "replacement.sqlite",
        "user.sqlite",
        "online-cache",
        "online-shards",
        # The managed canonical-Offline slot. Previous runs (including
        # killed ones) may have installed the fixture asset here; without
        # clearing it, a fresh server would start with a validated
        # canonical file and the download flow would have nothing to do.
        "dictionary",
    ):
        target = state_dir / filename
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
    for dirname in ("media", "cache"):
        candidate = state_dir / dirname
        if candidate.exists():
            shutil.rmtree(candidate)


def build_dictionary(path: Path, *, include_tisch: bool) -> None:
    entries = [
        (
            1,
            "Haus",
            "NOUN",
            "das",
            "Häuser",
            "Hauses",
            0,
            None,
            None,
            None,
            "house, building",
            "Das Haus ist alt.",
            "The house is old.",
        ),
        (
            2,
            "See",
            "NOUN",
            "der",
            "Seen",
            "Sees",
            0,
            None,
            None,
            None,
            "lake",
            "Der See ist tief.",
            "The lake is deep.",
        ),
        (
            3,
            "See",
            "NOUN",
            "die",
            "Seen",
            None,
            0,
            None,
            None,
            None,
            "sea, ocean",
            "Die See ist stürmisch.",
            "The sea is stormy.",
        ),
        (
            4,
            "anrufen",
            "VERB",
            None,
            None,
            None,
            1,
            "an",
            "rief an",
            "angerufen",
            "to call, phone",
            "Ich rufe dich morgen an.",
            "I will call you tomorrow.",
        ),
        (
            5,
            "Tisch",
            "NOUN",
            "der",
            "Tische",
            "Tisches",
            0,
            None,
            None,
            None,
            "table",
            "Der Tisch ist rund.",
            "The table is round.",
        ),
    ]
    if not include_tisch:
        entries = entries[:-1]
    conn = sqlite3.connect(path)
    try:
        conn.executescript(part_a_schema())
        for entry in entries:
            (
                ident,
                word,
                pos,
                gender,
                plural,
                genitive,
                separable,
                particle,
                past,
                participle,
                gloss,
                example_de,
                example_en,
            ) = entry
            lref = lemma_ref(word, pos, gender)
            sref = sense_ref(word, pos, gender, f"e2e:{ident}")
            conn.execute(
                """INSERT INTO lemma (
                   id, semantic_ref, lemma, pos, gender, plural, genitive_sg,
                   separable, particle, praeteritum_3sg, partizip_ii, ipa,
                   ipa_source, freq_rank, source, license
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ident,
                    lref,
                    word,
                    pos,
                    gender,
                    plural,
                    genitive,
                    separable,
                    particle,
                    past,
                    participle,
                    "test",
                    "fixture",
                    ident * 10,
                    "fixture",
                    "CC0",
                ),
            )
            conn.execute(
                "INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, "
                "source_ref, ord, source, license) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (ident, ident, sref, "wiktextract:enwiktionary", f"e2e:{ident}", "fixture", "CC0"),
            )
            conn.execute(
                "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, "
                "source, license) VALUES (?, ?, 'en', 'translation', 0, ?, 'fixture', 'CC0')",
                (ident, ident, gloss),
            )
            conn.execute(
                "INSERT INTO example (id, de, en, source, source_ref, license, "
                "token_count) VALUES (?, ?, ?, 'fixture', ?, 'CC0', 5)",
                (ident, example_de, example_en, f"e2e:{ident}"),
            )
            conn.execute(
                "INSERT INTO example_lemma (lemma_id, example_id) VALUES (?, ?)", (ident, ident)
            )
        conn.executemany(
            "INSERT INTO surface_form (form, lemma_id) VALUES (?, ?)",
            [("Häuser", 1), ("ruft an", 4), ("rief an", 4)],
        )
        # M6 — add a second direct sense for ``Haus`` (lemma_id=1) so the
        # served-product E2E fixture exposes one durable lemma ref with
        # multiple direct senses (the contract requires this for the
        # positive rebind E2E case). The original Haus sense
        # ``e2e:1`` ("house, building") is preserved; this adds
        # ``e2e:1-home-2`` ("home, figuratively") at ord=1.
        haus_home2_ref = sense_ref("Haus", "NOUN", "das", "e2e:1-home-2")
        conn.execute(
            "INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace, "
            "source_ref, ord, source, license) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (100, 1, haus_home2_ref, "wiktextract:enwiktionary", "e2e:1-home-2", "fixture", "CC0"),
        )
        conn.execute(
            "INSERT INTO sense_meaning (id, sense_id, language, kind, ord, text, "
            "source, license) VALUES (?, ?, 'en', 'translation', 1, ?, 'fixture', 'CC0')",
            (100, 100, "home, figuratively"),
        )
        conn.commit()
    finally:
        conn.close()


def build_user_db(path: Path) -> None:
    schema = (REPO_ROOT / "reference" / "schema.sql").read_text(encoding="utf-8")
    _, marker, part_b = schema.partition("-- PART B")
    if not marker:
        raise RuntimeError("reference/schema.sql has no PART B marker")
    conn = sqlite3.connect(path)
    try:
        conn.executescript("-- PART B" + part_b)
    finally:
        conn.close()


class _OnlineCounters:
    """In-process counters exposed to the E2E harness for transport/factory assertions."""

    def __init__(self) -> None:
        self.factory_invocations = 0
        self.transport_invocations = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "factory_invocations": int(self.factory_invocations),
            "transport_invocations": int(self.transport_invocations),
        }


class _CountingTransport:
    """In-process fixture transport that increments the E2E counter."""

    def __init__(
        self,
        inner: Any,
        counters: _OnlineCounters,
    ) -> None:
        self._inner = inner
        self._counters = counters

    def __call__(self, request: Any) -> bytes:
        self._counters.transport_invocations += 1
        result: bytes = self._inner(request)
        return result


def _prebuild_online_fixture(state_dir: Path) -> Any:
    """Pre-build the static Online fixture shard files + manifest.

    This is slow (576 SQLite files) and runs once at server startup.
    It does NOT construct an ``OnlineDictionaryProvider``, a
    ``ShardCache``, or invoke any transport — those happen only when
    the user picks ``Use Online`` via the factory below. The returned
    bundle is ``(manifest, filter_bytes, dataset_token)``.
    """
    from app.online_filter import BloomFilter  # noqa: PLC0415
    from app.online_manifest import (  # noqa: PLC0415
        ENTRY_FAMILY_SIZE,
        EXAMPLE_FAMILY_SIZE,
        LOOKUP_FAMILY_SIZE,
        MANIFEST_SCHEMA_VERSION,
        SHARD_FAMILY_FILTER,
        ManifestAsset,
        OnlineManifest,
        TrustedDistribution,
    )
    from tools.build_online_dictionary import (  # noqa: PLC0415
        _partition_entry_shards,
        _partition_example_shards,
        _partition_lookup_shards,
        _read_authoritative_example_lemma,
        _read_authoritative_examples,
        _read_authoritative_lemmas,
        _read_authoritative_meanings,
        _read_authoritative_senses,
        _read_authoritative_surface_forms,
        _write_entry_shard,
        _write_example_shard,
        _write_lookup_shard,
    )

    local_fixture = state_dir / "dictionary.sqlite"
    cache_dir = state_dir / "online-cache"
    shard_dir = state_dir / "online-shards"
    if shard_dir.exists():
        shutil.rmtree(shard_dir)
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    shard_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    actual_token = hashlib.sha256(local_fixture.read_bytes()).hexdigest()

    source_conn = sqlite3.connect(f"file:{local_fixture.as_posix()}?mode=ro", uri=True)
    source_conn.row_factory = sqlite3.Row
    try:
        lemmas = _read_authoritative_lemmas(source_conn)
        senses = _read_authoritative_senses(source_conn)
        meanings = _read_authoritative_meanings(source_conn)
        surface_forms = _read_authoritative_surface_forms(source_conn)
        examples = _read_authoritative_examples(source_conn)
        example_lemma = _read_authoritative_example_lemma(source_conn)
    finally:
        source_conn.close()

    lookup_partitions, surface_partitions, sense_route_partitions = _partition_lookup_shards(
        lemmas, surface_forms, senses
    )
    entry_partitions = _partition_entry_shards(
        lemmas, senses, meanings, surface_forms, example_lemma, examples
    )
    example_partitions = _partition_example_shards(examples)

    assets: list[ManifestAsset] = []

    def _write_shard(family: str, bucket: int, writer: Any, partition_data: Any) -> ManifestAsset:
        canonical = shard_dir / f"{family}-{bucket:03d}.sqlite"
        tmp = shard_dir / f".{family}-{bucket:03d}.sqlite.tmp"
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        # E2E fixture build only: skip journal/fsync overhead while
        # assembling the deterministic shards. The manifest below
        # records the SHA-256/byte-size of the exact bytes written,
        # and the provider re-validates (size + SHA + integrity_check)
        # on every lease, so the speedup cannot weaken validation.
        try:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA temp_store=MEMORY")
        except sqlite3.Error:
            pass
        writer(conn, bucket, *partition_data)
        conn.close()
        os.replace(tmp, canonical)
        payload = canonical.read_bytes()
        return ManifestAsset(
            family=family,
            bucket=bucket,
            name=f"{family}-{bucket:03d}.sqlite",
            path=f"shards/{family}/{bucket:03d}.sqlite",
            byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            schema_version=f"{family}-v1",
        )

    for bucket in range(LOOKUP_FAMILY_SIZE):
        assets.append(
            _write_shard(
                "lookup",
                bucket,
                _write_lookup_shard,
                (
                    lookup_partitions.get(bucket, []),
                    surface_partitions.get(bucket, []),
                    sense_route_partitions.get(bucket, ()),
                ),
            )
        )
    for bucket in range(ENTRY_FAMILY_SIZE):
        state = entry_partitions.get(
            bucket,
            {
                "lemmas": [],
                "senses": [],
                "meanings": [],
                "surface_forms": [],
                "example_lemma": [],
            },
        )
        assets.append(
            _write_shard(
                "entry",
                bucket,
                _write_entry_shard,
                (
                    state["lemmas"],
                    state["senses"],
                    state["meanings"],
                    state["surface_forms"],
                    state["example_lemma"],
                ),
            )
        )
    for bucket in range(EXAMPLE_FAMILY_SIZE):
        assets.append(
            _write_shard(
                "example",
                bucket,
                _write_example_shard,
                (example_partitions[bucket],),
            )
        )

    closure_keys: list[str] = []
    seen_closure: set[str] = set()
    for row in lemmas:
        text = str(row[2])
        for variant in (text, text.lower()):
            if variant in seen_closure:
                continue
            seen_closure.add(variant)
            closure_keys.append(variant)
    filter_bytes = BloomFilter.from_closure_keys(closure_keys).to_bytes()
    assets.append(
        ManifestAsset(
            family=SHARD_FAMILY_FILTER,
            bucket=0,
            name="membership-filter.bin",
            path="shards/membership-filter.bin",
            byte_size=len(filter_bytes),
            sha256=hashlib.sha256(filter_bytes).hexdigest(),
            schema_version="membership-filter-v1",
        )
    )

    manifest = OnlineManifest(
        dataset_token=actual_token,
        schema_version=MANIFEST_SCHEMA_VERSION,
        distribution=TrustedDistribution(
            base_origin="https://github.com",
            release_tag="dictionary-online-fixture",
            redirect_policy="github_release_redirect_only",
        ),
        assets=tuple(assets),
    )

    return (manifest, filter_bytes, actual_token)


def _build_online_provider_from_bundle(
    state_dir: Path,
    bundle: Any,
    counters: _OnlineCounters,
) -> Any:
    """Construct the Online provider from a pre-built fixture bundle.

    Fast (<1s): wraps the static shard files in a counting transport +
    ShardCache and returns the provider. Called ONLY from the factory
    after the user picks ``Use Online``.
    """
    from app.online_cache import ShardCache, ShardRequest  # noqa: PLC0415
    from app.provider import ProviderIntegrityError  # noqa: PLC0415
    from app.provider_online import OnlineDictionaryProvider  # noqa: PLC0415

    manifest, filter_bytes, actual_token = bundle
    shard_dir = state_dir / "online-shards"
    cache_dir = state_dir / "online-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def transport(request: ShardRequest) -> bytes:
        for asset in manifest.assets:
            if asset.family == request.identity.family and asset.bucket == request.identity.bucket:
                payload: bytes = (shard_dir / asset.name).read_bytes()
                return payload
        raise ProviderIntegrityError("missing fixture shard")

    counting_transport = _CountingTransport(transport, counters)
    cache = ShardCache(cache_dir, transport=counting_transport)
    return OnlineDictionaryProvider(
        manifest=manifest,
        cache=cache,
        filter_payload=filter_bytes,
        dataset_token=actual_token,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("E2E_PORT", "8817")))
    parser.add_argument(
        "--state",
        choices=("A", "B"),
        default=os.environ.get("E2E_STATE", "A"),
        help=(
            "Deterministic served-product harness state. "
            "A: valid canonical Offline dictionary installed; "
            "B: no canonical full Offline dictionary + Online fixture "
            "(chooser rendered in UI, deterministic online corpus)."
        ),
    )
    args = parser.parse_args()
    state_dir = Path(os.environ.get("E2E_STATE_DIR", REPO_ROOT / ".e2e-state")).resolve()
    reset_state(state_dir)
    build_dictionary(state_dir / "dictionary.sqlite", include_tisch=False)
    build_dictionary(state_dir / "replacement.sqlite", include_tisch=True)
    build_user_db(state_dir / "user.sqlite")

    state_a = args.state.upper() == "A"
    counters = _OnlineCounters()

    if state_a:
        # State A also needs a deferred Online factory so the
        # Offline -> Online Settings switch can be exercised. The
        # fixture bundle is pre-built from the same local dictionary;
        # no provider exists until the user picks Online.
        from app.dictionary_session import OnlineSessionInfo as _OSI  # noqa: PLC0415

        fixture_bundle_a = _prebuild_online_fixture(state_dir)

        def online_factory_a() -> Tuple[Any, Any]:
            counters.factory_invocations += 1
            provider_a = _build_online_provider_from_bundle(state_dir, fixture_bundle_a, counters)
            info_a = _OSI(
                dataset_token=str(getattr(provider_a, "_dataset_token", "online-fixture")),
                asset_token=str(provider_a.asset_token),
                cache_dir=str(state_dir / "online-cache"),
            )
            return provider_a, info_a

        app = create_app(
            state_dir / "dictionary.sqlite",
            state_dir / "user.sqlite",
            cors_origins=(f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"),
            service_port=args.port,
            online_provider_factory=online_factory_a,
            # Point the managed canonical slot at the real state-A asset so
            # Settings reports its presence honestly and the session can
            # switch back Offline after an Online excursion. No install
            # triple exists here, so install/remove stay 409-unconfigured;
            # only status reporting and use-offline reactivation use it.
            managed_dictionary_dir=state_dir,
            manifest_filename="dictionary.sqlite",
        )
    else:
        # Slice 12 state B: no canonical full Offline asset. The chooser
        # is shown. The Online provider is constructed only when the user
        # invokes ``POST /vocab/settings/dictionary/use-online`` (which
        # calls the factory). The factory builds a provider backed by the
        # in-process fixture corpus + counting transport.
        from app.dictionary_mode import OfflineInstallTriple  # noqa: PLC0415
        from app.dictionary_session import OnlineSessionInfo  # noqa: PLC0415

        # Pre-build static shard files + manifest at startup (slow).
        # No provider, cache, or transport exists yet.
        fixture_bundle = _prebuild_online_fixture(state_dir)

        def online_factory() -> Tuple[Any, Any]:
            counters.factory_invocations += 1
            provider = _build_online_provider_from_bundle(state_dir, fixture_bundle, counters)
            info = OnlineSessionInfo(
                dataset_token=str(getattr(provider, "_dataset_token", "online-fixture")),
                asset_token=str(provider.asset_token),
                cache_dir=str(state_dir / "online-cache"),
            )
            return provider, info

        # The E2E fixture Offline installer uses a file:// URL pointing
        # at the deterministic fixture dictionary. The triple carries
        # the exact fixture SHA + bytes so the server-owned install
        # path validates exactly like production.
        import hashlib as _hashlib  # noqa: PLC0415

        fixture_bytes = (state_dir / "dictionary.sqlite").read_bytes()
        triple = OfflineInstallTriple(
            version="v2",
            filename="dictionary.sqlite",
            sha256=_hashlib.sha256(fixture_bytes).hexdigest(),
            bytes=len(fixture_bytes),
            download_url=(state_dir / "dictionary.sqlite").as_uri(),
            manifest_path=REPO_ROOT / "release" / "dictionary-manifest-v2.json",
        )

        app = create_app(
            dict_path=None,
            user_db_path=state_dir / "user.sqlite",
            cors_origins=(f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"),
            service_port=args.port,
            online_provider=None,
            managed_dictionary_dir=state_dir / "dictionary",
            manifest_filename="dictionary.sqlite",
            offline_install_triple=triple,
            online_provider_factory=online_factory,
        )
        # State B: the chooser stays in unconfigured until the user picks
        # a mode. We deliberately do NOT bind an Online provider here, and
        # we do NOT build one until the user picks "Use Online".

    # Expose the counters via app.state so the Settings endpoint
    # embeds them (a dedicated /__e2e/* route would be shadowed by the
    # frontend catch-all). Production never sets this attribute.
    app.state.e2e_counters = counters.snapshot
    # Ensure no Piper runner or remote TTS is active for deterministic E2E audio fallback
    app.state.piper_runner = None
    app.state.tts_remote_url = None

    _get_nlp()
    import uvicorn

    print(f"[e2e-server] FastAPI state: {state_dir} state={args.state.upper()}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
