"""Anki package export boundary.

This module is the sole runtime user of :mod:`genanki`.  It converts the
read-only export observations supplied by ``DictionaryRuntime`` into a
temporary APKG representation; it never writes rendered faces or scheduling
state to the flashcard database.
"""

from __future__ import annotations

import html
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import genanki

from app.render import CardRenderInput, render_card

_MODEL_ID = 1790642127


@dataclass(frozen=True, slots=True)
class ExportAudio:
    """A validated audio asset selected for one exported note."""

    filename: str
    data: bytes
    source: str
    export_eligible: bool = True


def _stable_int(identity: str) -> int:
    """Return an Anki-safe positive integer derived from durable identity."""
    return int(sha256(identity.encode("utf-8")).hexdigest()[:15], 16)


def stable_guid(observation: Mapping[str, object]) -> str:
    """Derive an Anki GUID from semantic identity, never local database IDs."""
    status = str(observation["note_status"])
    lemma_ref = str(observation["lemma_semantic_ref"])
    if status == "resolved" and observation.get("sense_semantic_ref"):
        identity = f"resolved:{observation['sense_semantic_ref']}"
    elif status == "derived_compound":
        raw_components = observation.get("components", ())
        components = (
            cast(Sequence[object], raw_components)
            if isinstance(raw_components, Sequence) and not isinstance(raw_components, str)
            else ()
        )
        component_refs = ",".join(
            f"{item['lemma_semantic_ref']}:{item['sense_semantic_ref']}"
            for item in components
            if isinstance(item, Mapping)
            and item.get("lemma_semantic_ref")
            and item.get("sense_semantic_ref")
        )
        identity = f"derived:{component_refs or lemma_ref}"
    else:
        identity = f"{status}:{lemma_ref}"
    return sha256(f"flashcard-apkg-v1:{identity}".encode("utf-8")).hexdigest()


def _field(value: str | None) -> str:
    """Encode plain rendered display text as an Anki HTML field."""
    if not value:
        return ""
    return html.escape(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _back_display(observation: Mapping[str, object], rendered: Any) -> str:
    if str(observation["note_status"]) == "needs_gloss" or not rendered.back.meanings:
        return ""
    sections: list[str] = []
    for meaning in rendered.back.meanings:
        if meaning.lines:
            lines = [f"• {line}" if len(meaning.lines) > 1 else line for line in meaning.lines]
            sections.append(f"{meaning.heading}\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _example_display(rendered: Any) -> str:
    lines: list[str] = []
    for example in rendered.back.examples:
        lines.append(example.de)
        if example.en:
            lines.append(f"  {example.en}")
    return "\n".join(lines)


def _coerce_audio(value: object, source: str) -> ExportAudio | None:
    if isinstance(value, ExportAudio):
        return value
    if not isinstance(value, Mapping):
        return None
    filename = value.get("filename")
    data = value.get("data")
    if not isinstance(filename, str) or not isinstance(data, bytes):
        return None
    eligible = value.get("export_eligible", True)
    return ExportAudio(
        filename=filename, data=data, source=source, export_eligible=eligible is True
    )


def select_export_audio(
    observation: Mapping[str, object],
    supplied: Mapping[str, ExportAudio | Mapping[str, object]] | None = None,
) -> ExportAudio | None:
    """Apply the APKG audio contract: custom, human, Piper, then silence."""
    candidates: dict[str, object] = {}
    raw = observation.get("export_audio")
    if isinstance(raw, Mapping):
        candidates.update(raw)
    if supplied is not None:
        candidates.update(supplied)

    custom = _coerce_audio(candidates.get("custom"), "custom")
    if custom is not None and custom.data:
        return custom
    human = _coerce_audio(candidates.get("human"), "human")
    if human is not None and human.data and human.export_eligible:
        return human
    piper = _coerce_audio(candidates.get("piper"), "piper")
    if piper is not None and piper.data:
        return piper
    return None


def _audio_filename(audio: ExportAudio) -> str:
    filename = Path(audio.filename).name
    if filename in ("", "."):
        raise ValueError("export audio filename must have a basename")
    return filename


def build_apkg(
    observations: Sequence[Mapping[str, object]],
    *,
    deck_name: str,
    render_input_for_observation: Callable[[Mapping[str, object]], CardRenderInput],
    audio_for_observation: (
        Callable[[Mapping[str, object]], Mapping[str, ExportAudio | Mapping[str, object]] | None]
        | None
    ) = None,
) -> bytes:
    """Build a valid APKG from read-only observations.

    Anki's new-card scheduling is intentionally left at its default export
    representation.  No flashcard due date, FSRS state, or rendered card face
    is copied into persistent application state.
    """
    clean_deck_name = deck_name.strip()
    if not clean_deck_name:
        raise ValueError("deck name must not be blank")

    model = genanki.Model(
        _MODEL_ID,
        "German Vocabulary",
        fields=[
            {"name": "Front"},
            {"name": "Back"},
            {"name": "Grammar"},
            {"name": "Example"},
            {"name": "IPA"},
            {"name": "Audio"},
        ],
        templates=[
            {
                "name": "Recognition",
                "qfmt": "{{Front}}<br>{{Audio}}",
                "afmt": (
                    "{{FrontSide}}<hr id=answer>{{Back}}<br>{{Grammar}}<br>"
                    "{{Example}}<br>{{IPA}}"
                ),
            }
        ],
        css=".card { font-family: sans-serif; text-align: left; }",
    )
    deck = genanki.Deck(_stable_int(f"deck:{clean_deck_name}"), clean_deck_name)

    media: dict[str, bytes] = {}
    for observation in observations:
        rendered = render_card(render_input_for_observation(observation))
        supplied = audio_for_observation(observation) if audio_for_observation is not None else None
        audio = select_export_audio(observation, supplied)
        audio_field = ""
        if audio is not None:
            filename = _audio_filename(audio)
            existing = media.get(filename)
            if existing is not None and existing != audio.data:
                stem, suffix = os.path.splitext(filename)
                filename = f"{stem}-{sha256(audio.data).hexdigest()[:12]}{suffix}"
            media[filename] = audio.data
            audio_field = f"[sound:{filename}]"

        note_status = str(observation["note_status"])
        deck_names = str(observation.get("deck_names") or "")
        tags = [name.strip().replace(" ", "_") for name in deck_names.split(",") if name.strip()]
        if note_status in {"needs_gloss", "orphaned"}:
            tags.append(note_status)
        note = genanki.Note(
            model=model,
            fields=[
                _field(rendered.front.text),
                _field(_back_display(observation, rendered)),
                _field("\n".join(rendered.back.grammar.lines)),
                _field(_example_display(rendered)),
                _field(rendered.front.ipa or ""),
                audio_field,
            ],
            tags=tags,
            guid=stable_guid(observation),
            due=0,
        )
        deck.add_note(note)

    with tempfile.TemporaryDirectory(prefix="flashcard-apkg-") as tmp:
        tmp_path = Path(tmp)
        media_paths: list[str] = []
        for filename, data in media.items():
            path = tmp_path / filename
            path.write_bytes(data)
            media_paths.append(str(path))
        package_path = tmp_path / "export.apkg"
        genanki.Package(deck, media_files=media_paths).write_to_file(package_path, timestamp=0)
        return package_path.read_bytes()
