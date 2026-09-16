"""Display-time card rendering and tri-state noun plural formatting.

Implements pure function rendering for German vocabulary cards per ADR-0001 §11,
ADR-0004 D39/D41/D46, and ADR-0007 D80:
- Front face: headword, article+gender (for nouns), POS, IPA (conditional), audio trigger.
- Back face: front content + tri-state noun plural + conditional German grammar metadata +
  selected learner meanings (DE/EN only, user precedence, D46 derived compound decomposition) +
  example sentences (de + optional en).

Pure function layer:
- No module-level mutable state.
- No environment reads.
- No I/O or database access.
- Faces computed at display time and NEVER stored (AGENTS R4).
- Dependency direction AGENTS C2: imports only from app.dictionary (and app.resolve),
  never from app.deck or app.api.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from app.dictionary import ExampleEntry, LemmaEntry
from app.selection import MAX_CARD_EXAMPLES

VALID_MEANING_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "en"})
LANGUAGE_NAMES: Final[dict[str, str]] = {
    "de": "Deutsch",
    "en": "English",
}

MeaningOrigin = Literal["user", "dictionary", "derived_component"]


@dataclass(frozen=True, slots=True)
class RenderLemmaData:
    """Structured grammar and lexical fields for card rendering."""

    lemma: str
    pos: str
    gender: str | None = None
    plural: str | None = None
    plural_none: int = 0
    genitive_sg: str | None = None
    aux: str | None = None
    separable: int = 0
    particle: str | None = None
    reflexive: int = 0
    praesens_3sg: str | None = None
    praeteritum_3sg: str | None = None
    partizip_ii: str | None = None
    governs: str | None = None
    comparative: str | None = None
    superlative: str | None = None
    ipa: str | None = None

    @classmethod
    def from_lemma_entry(cls, entry: LemmaEntry) -> RenderLemmaData:
        """Create RenderLemmaData from a dictionary LemmaEntry."""
        return cls(
            lemma=entry.lemma,
            pos=entry.pos,
            gender=entry.gender,
            plural=entry.plural,
            plural_none=entry.plural_none,
            genitive_sg=entry.genitive_sg,
            aux=entry.aux,
            separable=entry.separable,
            particle=entry.particle,
            reflexive=entry.reflexive,
            praesens_3sg=entry.praesens_3sg,
            praeteritum_3sg=entry.praeteritum_3sg,
            partizip_ii=entry.partizip_ii,
            governs=entry.governs,
            comparative=entry.comparative,
            superlative=entry.superlative,
            ipa=entry.ipa,
        )


@dataclass(frozen=True, slots=True)
class DerivedComponent:
    """Component of a derived compound (ordered left-to-right, grammatical head last)."""

    lemma: str
    text: str | None = None


@dataclass(frozen=True, slots=True)
class MeaningBlock:
    """Input meaning block for a single language."""

    language: str
    origin: MeaningOrigin | str
    texts: tuple[str, ...] = ()
    components: tuple[DerivedComponent, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderExample:
    """Example sentence for card rendering."""

    de: str
    en: str | None = None

    @classmethod
    def from_example_entry(cls, entry: ExampleEntry) -> RenderExample:
        """Create RenderExample from a dictionary ExampleEntry."""
        return cls(de=entry.de, en=entry.en)


@dataclass(frozen=True, slots=True)
class AudioTrigger:
    """Structured pronunciation-audio trigger affordance."""

    available: bool = True
    lemma: str = ""
    token: str | None = None


@dataclass(frozen=True, slots=True)
class CardRenderInput:
    """Frozen input contract for display-time card rendering."""

    lemma: RenderLemmaData | LemmaEntry
    selected_languages: tuple[str, ...]
    meanings: tuple[MeaningBlock, ...] = ()
    examples: tuple[RenderExample | ExampleEntry, ...] = ()
    audio_trigger: AudioTrigger | None = None


@dataclass(frozen=True, slots=True)
class RenderedFrontFace:
    """Rendered front face of a vocabulary card."""

    headword: str
    display_headword: str
    pos: str
    gender: str | None
    article: str | None
    ipa: str | None
    audio_trigger: AudioTrigger
    text: str


@dataclass(frozen=True, slots=True)
class RenderedGrammar:
    """Rendered German grammar metadata."""

    pos: str
    gender: str | None = None
    article: str | None = None
    plural: str | None = None
    genitive_sg: str | None = None
    aux: str | None = None
    separable: bool = False
    particle: str | None = None
    reflexive: bool = False
    praesens_3sg: str | None = None
    praeteritum_3sg: str | None = None
    partizip_ii: str | None = None
    principal_parts: tuple[str, ...] = ()
    governs: str | None = None
    comparative: str | None = None
    superlative: str | None = None
    lines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedMeaningBlock:
    """Rendered learner meaning block for a single language."""

    language: str
    origin: str
    is_user_authored: bool
    heading: str
    lines: tuple[str, ...]
    decomposition: tuple[DerivedComponent, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedExampleItem:
    """Rendered example sentence."""

    de: str
    en: str | None = None
    lines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedBackFace:
    """Rendered back face of a vocabulary card."""

    front: RenderedFrontFace
    display_headword: str
    pos: str
    gender: str | None
    article: str | None
    ipa: str | None
    audio_trigger: AudioTrigger
    plural: str | None
    grammar: RenderedGrammar
    meanings: tuple[RenderedMeaningBlock, ...]
    examples: tuple[RenderedExampleItem, ...]
    text: str


@dataclass(frozen=True, slots=True)
class RenderedCard:
    """Rendered card carrying both front and back display-time faces."""

    front: RenderedFrontFace
    back: RenderedBackFace


def _normalize_gender(gender: str | None) -> tuple[str | None, str | None]:
    """Normalize gender string to canonical article and gender."""
    if gender is None:
        return None, None
    g = gender.strip().lower()
    if g in ("der", "m", "masculine", "maskulin"):
        return "der", "der"
    if g in ("die", "f", "feminine", "feminin"):
        return "die", "die"
    if g in ("das", "n", "neuter", "neutral", "sächlich"):
        return "das", "das"
    return None, gender


def render_plural(pos: str, plural: str | None, plural_none: int) -> str | None:
    """Apply the normative ADR-0004 D39 tri-state noun plural rendering rule."""
    if pos.strip().upper() != "NOUN":
        return None
    if plural is not None and plural.strip():
        clean_plural = plural.strip()
        if clean_plural.lower().startswith("die "):
            clean_plural = clean_plural[4:].strip()
        return f"Plural: die {clean_plural}"
    if plural_none == 1:
        return "kein Plural"
    return None


def validate_selected_languages(languages: Sequence[str]) -> tuple[str, ...]:
    """Validate that selected languages form a non-empty subset of {'de', 'en'}."""
    langs = tuple(languages)
    if not langs:
        raise ValueError("selected languages must be a non-empty subset of {'de', 'en'}")
    if len(set(langs)) != len(langs):
        raise ValueError("selected languages must not contain duplicates")
    valid_sorted = sorted(VALID_MEANING_LANGUAGES)
    for lang in langs:
        if lang not in VALID_MEANING_LANGUAGES:
            raise ValueError(f"unsupported meaning language {lang!r}; must be in {valid_sorted!r}")
    return langs


def _parse_governs(governs: str | None) -> str | None:
    """Parse JSON or raw string governed case."""
    if governs is None or not governs.strip():
        return None
    trimmed = governs.strip()
    if trimmed.startswith("[") and trimmed.endswith("]"):
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, list):
                return ", ".join(str(item) for item in parsed if item)
        except Exception:
            pass
    return trimmed


def _build_grammar(
    lemma: RenderLemmaData,
    article: str | None,
    gender: str | None,
    plural_str: str | None,
) -> RenderedGrammar:
    """Build structured grammar metadata and formatted lines."""
    lines: list[str] = []

    lines.append(f"Wortart: {lemma.pos}")

    if plural_str is not None:
        lines.append(plural_str)

    if lemma.genitive_sg and lemma.genitive_sg.strip():
        lines.append(f"Genitiv: {lemma.genitive_sg.strip()}")

    if lemma.aux and lemma.aux.strip():
        lines.append(f"Hilfsverb: {lemma.aux.strip()}")

    particle = lemma.particle.strip() if lemma.particle and lemma.particle.strip() else None
    separable = bool(lemma.separable or particle)
    if particle:
        lines.append(f"Trennbar (Präfix: {particle})")
    elif separable:
        lines.append("trennbar")

    reflexive = bool(lemma.reflexive)
    if reflexive:
        lines.append("reflexiv")

    principal_parts: list[str] = []
    pp_labels: list[str] = []
    if lemma.praesens_3sg and lemma.praesens_3sg.strip():
        p = lemma.praesens_3sg.strip()
        principal_parts.append(p)
        pp_labels.append(f"3. P. Sing.: {p}")
    if lemma.praeteritum_3sg and lemma.praeteritum_3sg.strip():
        p = lemma.praeteritum_3sg.strip()
        principal_parts.append(p)
        pp_labels.append(f"Prät.: {p}")
    if lemma.partizip_ii and lemma.partizip_ii.strip():
        p = lemma.partizip_ii.strip()
        principal_parts.append(p)
        pp_labels.append(f"Part. II: {p}")
    if pp_labels:
        lines.append(f"Stammformen: {', '.join(pp_labels)}")

    governs_parsed = _parse_governs(lemma.governs)
    if governs_parsed:
        lines.append(f"Rektion: {governs_parsed}")

    grad: list[str] = []
    if lemma.comparative and lemma.comparative.strip():
        grad.append(f"Komparativ: {lemma.comparative.strip()}")
    if lemma.superlative and lemma.superlative.strip():
        grad.append(f"Superlativ: {lemma.superlative.strip()}")
    if grad:
        lines.append(f"Steigerung: {', '.join(grad)}")

    return RenderedGrammar(
        pos=lemma.pos,
        gender=gender,
        article=article,
        plural=plural_str,
        genitive_sg=lemma.genitive_sg.strip() if lemma.genitive_sg else None,
        aux=lemma.aux.strip() if lemma.aux else None,
        separable=separable,
        particle=particle,
        reflexive=reflexive,
        praesens_3sg=lemma.praesens_3sg.strip() if lemma.praesens_3sg else None,
        praeteritum_3sg=lemma.praeteritum_3sg.strip() if lemma.praeteritum_3sg else None,
        partizip_ii=lemma.partizip_ii.strip() if lemma.partizip_ii else None,
        principal_parts=tuple(principal_parts),
        governs=governs_parsed,
        comparative=lemma.comparative.strip() if lemma.comparative else None,
        superlative=lemma.superlative.strip() if lemma.superlative else None,
        lines=tuple(lines),
    )


def _render_meanings(
    selected_languages: tuple[str, ...],
    meanings: tuple[MeaningBlock, ...],
) -> tuple[RenderedMeaningBlock, ...]:
    """Render learner meanings in deterministic order (de then en) with user precedence."""
    ordered_langs: list[str] = []
    for lang in ("de", "en"):
        if lang in selected_languages:
            ordered_langs.append(lang)

    rendered: list[RenderedMeaningBlock] = []

    for lang in ordered_langs:
        lang_blocks = [mb for mb in meanings if mb.language == lang]
        lang_name = LANGUAGE_NAMES[lang]

        # 1. User-authored meaning takes precedence (F2b, T3)
        user_blocks = [
            mb
            for mb in lang_blocks
            if mb.origin == "user" and mb.texts and any(t.strip() for t in mb.texts)
        ]
        if user_blocks:
            user_block = user_blocks[0]
            clean_texts = tuple(t.strip() for t in user_block.texts if t.strip())
            rendered.append(
                RenderedMeaningBlock(
                    language=lang,
                    origin="user",
                    is_user_authored=True,
                    heading=f"{lang_name} (user-authored):",
                    lines=clean_texts,
                )
            )
            continue

        # 2. Derived compound component decomposition (F3, T7)
        comp_blocks = [mb for mb in lang_blocks if mb.origin == "derived_component"]
        if comp_blocks:
            comp_block = comp_blocks[0]
            if comp_block.components:
                # All components must have non-empty localized text in this language (all-or-none)
                all_valid = all(
                    c.text is not None and c.text.strip() != "" for c in comp_block.components
                )
                if all_valid:
                    decomp_lines = tuple(
                        f"{c.lemma.strip()}: {c.text.strip()}"  # type: ignore[union-attr]
                        for c in comp_block.components
                    )
                    rendered.append(
                        RenderedMeaningBlock(
                            language=lang,
                            origin="derived_component",
                            is_user_authored=False,
                            heading=f"{lang_name}:",
                            lines=decomp_lines,
                            decomposition=comp_block.components,
                        )
                    )
                # If any component lacks text in this language, no dictionary block is rendered
            elif comp_block.texts and any(t.strip() for t in comp_block.texts):
                clean_texts = tuple(t.strip() for t in comp_block.texts if t.strip())
                rendered.append(
                    RenderedMeaningBlock(
                        language=lang,
                        origin="derived_component",
                        is_user_authored=False,
                        heading=f"{lang_name}:",
                        lines=clean_texts,
                    )
                )
            continue

        # 3. Direct dictionary meaning
        dict_blocks = [
            mb
            for mb in lang_blocks
            if mb.origin == "dictionary" and mb.texts and any(t.strip() for t in mb.texts)
        ]
        if dict_blocks:
            dict_block = dict_blocks[0]
            clean_texts = tuple(t.strip() for t in dict_block.texts if t.strip())
            rendered.append(
                RenderedMeaningBlock(
                    language=lang,
                    origin="dictionary",
                    is_user_authored=False,
                    heading=f"{lang_name}:",
                    lines=clean_texts,
                )
            )

    return tuple(rendered)


def _render_examples(
    examples: tuple[RenderExample | ExampleEntry, ...],
) -> tuple[RenderedExampleItem, ...]:
    """Render example sentences with optional English translation (at most MAX_CARD_EXAMPLES)."""
    rendered: list[RenderedExampleItem] = []
    for ex in examples[:MAX_CARD_EXAMPLES]:
        de_clean = ex.de.strip()
        en_clean = ex.en.strip() if ex.en and ex.en.strip() else None
        lines = (de_clean, en_clean) if en_clean else (de_clean,)
        rendered.append(RenderedExampleItem(de=de_clean, en=en_clean, lines=lines))
    return tuple(rendered)


def _format_front_face_text(display_headword: str, pos: str, ipa: str | None) -> str:
    """Format human-readable text for the front face."""
    if ipa:
        return f"{display_headword}\n{pos} • {ipa}"
    return f"{display_headword}\n{pos}"


def _format_back_face_text(
    front_text: str,
    grammar: RenderedGrammar,
    meanings: tuple[RenderedMeaningBlock, ...],
    examples: tuple[RenderedExampleItem, ...],
) -> str:
    """Format human-readable text for the back face."""
    sections: list[str] = [front_text]

    if grammar.lines:
        sections.append("Grammatik:\n" + "\n".join(grammar.lines))

    for mb in meanings:
        if mb.lines:
            lines_formatted = [f"• {line}" if len(mb.lines) > 1 else line for line in mb.lines]
            meaning_body = "\n".join(lines_formatted)
            sections.append(f"{mb.heading}\n{meaning_body}")

    if examples:
        ex_lines: list[str] = []
        for ex in examples:
            ex_lines.append(f"• {ex.de}")
            if ex.en:
                ex_lines.append(f"  {ex.en}")
        sections.append("Beispiele:\n" + "\n".join(ex_lines))

    return "\n\n".join(sections)


def render_front_face(render_input: CardRenderInput) -> RenderedFrontFace:
    """Compute display-time front face (pure function)."""
    lemma_data = (
        render_input.lemma
        if isinstance(render_input.lemma, RenderLemmaData)
        else RenderLemmaData.from_lemma_entry(render_input.lemma)
    )

    pos_upper = lemma_data.pos.strip().upper()
    article: str | None = None
    gender: str | None = None

    if pos_upper == "NOUN":
        article, gender = _normalize_gender(lemma_data.gender)

    if article and pos_upper == "NOUN":
        display_headword = f"{article} {lemma_data.lemma}"
    else:
        display_headword = lemma_data.lemma

    ipa = lemma_data.ipa.strip() if lemma_data.ipa and lemma_data.ipa.strip() else None

    audio_trigger = (
        render_input.audio_trigger
        if render_input.audio_trigger is not None
        else AudioTrigger(available=True, lemma=lemma_data.lemma)
    )

    text = _format_front_face_text(display_headword, lemma_data.pos, ipa)

    return RenderedFrontFace(
        headword=lemma_data.lemma,
        display_headword=display_headword,
        pos=lemma_data.pos,
        gender=gender,
        article=article,
        ipa=ipa,
        audio_trigger=audio_trigger,
        text=text,
    )


def render_back_face(render_input: CardRenderInput) -> RenderedBackFace:
    """Compute display-time back face (pure function)."""
    # 1. Validate languages
    validated_langs = validate_selected_languages(render_input.selected_languages)

    valid_sorted = sorted(VALID_MEANING_LANGUAGES)
    for mb in render_input.meanings:
        if mb.language not in VALID_MEANING_LANGUAGES:
            raise ValueError(
                f"unsupported meaning block language {mb.language!r}; must be in {valid_sorted!r}"
            )

    # 2. Render front face
    front = render_front_face(render_input)

    lemma_data = (
        render_input.lemma
        if isinstance(render_input.lemma, RenderLemmaData)
        else RenderLemmaData.from_lemma_entry(render_input.lemma)
    )

    # 3. Tri-state plural
    plural_str = render_plural(lemma_data.pos, lemma_data.plural, lemma_data.plural_none)

    # 4. Grammar metadata
    grammar = _build_grammar(lemma_data, front.article, front.gender, plural_str)

    # 5. Selected learner meanings
    meanings = _render_meanings(validated_langs, render_input.meanings)

    # 6. Examples
    examples = _render_examples(render_input.examples)

    # 7. Format complete back face text
    text = _format_back_face_text(front.text, grammar, meanings, examples)

    return RenderedBackFace(
        front=front,
        display_headword=front.display_headword,
        pos=front.pos,
        gender=front.gender,
        article=front.article,
        ipa=front.ipa,
        audio_trigger=front.audio_trigger,
        plural=plural_str,
        grammar=grammar,
        meanings=meanings,
        examples=examples,
        text=text,
    )


def render_card(render_input: CardRenderInput) -> RenderedCard:
    """Compute display-time front and back card faces (pure function)."""
    front = render_front_face(render_input)
    back = render_back_face(render_input)
    return RenderedCard(front=front, back=back)
