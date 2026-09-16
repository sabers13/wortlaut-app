"""Semantic checks for real, deterministic Anki package export."""

from __future__ import annotations

import io
import json
import sqlite3
import wave
import zipfile
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.api import _export_audio_for_observation, _render_input_from_observation
from app.audio import HumanAudioProvenance, save_custom_pronunciation
from app.export import ExportAudio, build_apkg, select_export_audio, stable_guid


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def _observation() -> Mapping[str, object]:
    lemma_ref = "lemma:v1:haus"
    sense_ref = "sense:v1:haus-home"
    return {
        "card_id": 1,
        "note_id": 1,
        "note_status": "resolved",
        "lemma_semantic_ref": lemma_ref,
        "sense_semantic_ref": sense_ref,
        "deck_names": "Export deck",
        "selected_languages": ("de", "en"),
        "user_meanings": {},
        "components": (),
        "lemma": {
            "lemma": "Haus",
            "pos": "NOUN",
            "gender": "das",
            "plural": "Häuser",
            "plural_none": 0,
            "genitive_sg": "Hauses",
            "aux": None,
            "separable": 0,
            "particle": None,
            "reflexive": 0,
            "praesens_3sg": None,
            "praeteritum_3sg": None,
            "partizip_ii": None,
            "governs": None,
            "comparative": None,
            "superlative": None,
            "ipa": "haʊs",
        },
        "senses": ({"id": 1, "semantic_ref": sense_ref},),
        "meanings": (
            {"sense_id": 1, "language": "de", "text": "Gebäude zum Wohnen"},
            {"sense_id": 1, "language": "en", "text": "house"},
        ),
        "examples": ({"de": "Das Haus ist groß.", "en": "The house is large."},),
    }


def test_build_apkg_contains_real_collection_stable_guid_and_media(
    tmp_path: Path
) -> None:
    observation = _observation()
    audio_bytes = _wav_bytes()
    media_filename = "note_1.wav"

    package = build_apkg(
        (observation,),
        deck_name="Export deck",
        render_input_for_observation=_render_input_from_observation,
        audio_for_observation=lambda _: {
            "custom": ExportAudio(
                filename=f"untrusted/path/{media_filename}", data=audio_bytes, source="custom"
            )
        },
    )

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert "collection.anki2" in archive.namelist()
        manifest = json.loads(archive.read("media"))
        assert list(manifest.values()) == [media_filename]
        media_index = next(index for index, name in manifest.items() if name == media_filename)
        assert archive.read(media_index) == audio_bytes

        collection_path = tmp_path / "collection.anki2"
        collection_path.write_bytes(archive.read("collection.anki2"))
    conn = sqlite3.connect(collection_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1
        guid, fields = conn.execute("SELECT guid, flds FROM notes").fetchone()
        assert guid == stable_guid(observation)
        fields_list = fields.split("\x1f")
        assert fields_list[5] == f"[sound:{media_filename}]"
        assert "/" not in fields_list[5]
        decks = json.loads(conn.execute("SELECT decks FROM col").fetchone()[0])
        models = json.loads(conn.execute("SELECT models FROM col").fetchone()[0])
        assert any(deck["name"] == "Export deck" for deck in decks.values())
        assert any(model["name"] == "German Vocabulary" for model in models.values())
    finally:
        conn.close()


def test_export_audio_precedence_uses_only_export_eligible_human_audio() -> None:
    observation: Mapping[str, object] = {
        "note_status": "needs_gloss",
        "lemma_semantic_ref": "lemma:v1:test",
        "export_audio": {
            "human": ExportAudio("human.wav", b"human", "human", export_eligible=False),
            "piper": ExportAudio("piper.wav", b"piper", "piper"),
        },
    }
    selected = select_export_audio(observation)
    assert selected is not None and selected.source == "piper"

    with_human = dict(observation)
    with_human["export_audio"] = {
        "custom": ExportAudio("custom.wav", b"custom", "custom"),
        "human": ExportAudio("human.wav", b"human", "human", export_eligible=True),
        "piper": ExportAudio("piper.wav", b"piper", "piper"),
    }
    selected_with_custom = select_export_audio(with_human)
    assert selected_with_custom is not None and selected_with_custom.source == "custom"


def _eligible_human_provenance(*, redistribution_eligible: bool = True) -> HumanAudioProvenance:
    return HumanAudioProvenance(
        media_policy_version="1.0",
        upstream_source_site="commons.wikimedia.org",
        upstream_identifier="File:de-test.wav",
        upstream_revision="123",
        media_source_ref="https://upload.wikimedia.org/de-test.wav",
        retrieval_metadata={"retrieved_at": "2026-08-30T00:00:00Z"},
        author_attribution="Test speaker",
        raw_license="CC BY 4.0",
        policy_classification_key="cc_by_4_0",
        runtime_cache_eligible=True,
        redistribution_eligible=redistribution_eligible,
    )


def _export_callback_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    human_resolver: object = None,
    piper_runner: object = None,
) -> tuple[Any, Path]:
    user_db_path = tmp_path / "user.sqlite"
    conn = sqlite3.connect(user_db_path)
    try:
        conn.execute(
            """
            CREATE TABLE custom_pronunciation (
                note_id INTEGER PRIMARY KEY,
                media_filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                format TEXT NOT NULL,
                source_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    app = SimpleNamespace(
        state=SimpleNamespace(
            media_dir=tmp_path / "media",
            human_audio_id_for_observation=lambda _: "File:de-test.wav",
            human_audio_resolver=human_resolver,
            piper_runner=piper_runner,
        )
    )
    monkeypatch.setattr("app.api._get_user_db_conn", lambda _: sqlite3.connect(user_db_path))
    return app, user_db_path


def test_export_callback_applies_custom_human_piper_absent_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API callback uses D48 selection, excludes remote /speak, and writes no cache."""
    custom_bytes = _wav_bytes()
    human_bytes = _wav_bytes()
    piper_bytes = _wav_bytes()
    piper_calls: list[tuple[str, str]] = []

    def human_resolver(_: str) -> tuple[bytes, HumanAudioProvenance]:
        return human_bytes, _eligible_human_provenance()

    def piper_runner(text: str, voice: str) -> bytes:
        piper_calls.append((text, voice))
        return piper_bytes

    app, user_db_path = _export_callback_app(
        tmp_path,
        monkeypatch,
        human_resolver=human_resolver,
        piper_runner=piper_runner,
    )
    observation = _observation()

    human = _export_audio_for_observation(app, observation)
    assert human["human"].data == human_bytes
    assert piper_calls == []

    conn = sqlite3.connect(user_db_path)
    try:
        save_custom_pronunciation(conn, 1, custom_bytes, app.state.media_dir)
    finally:
        conn.close()
    custom = _export_audio_for_observation(app, observation)
    assert custom["custom"].data == custom_bytes
    assert piper_calls == []

    conn = sqlite3.connect(user_db_path)
    try:
        conn.execute("DELETE FROM custom_pronunciation")
        conn.commit()
    finally:
        conn.close()
    app.state.human_audio_resolver = lambda _: (
        human_bytes,
        _eligible_human_provenance(redistribution_eligible=False),
    )
    piper = _export_audio_for_observation(app, observation)
    assert piper["piper"].data == piper_bytes
    assert piper_calls == [("Haus", "de_DE-thorsten-high")]

    app.state.piper_runner = None
    absent = _export_audio_for_observation(app, observation)
    assert absent == {}
    assert not (tmp_path / "cache").exists()
