# Flashcard Dictionary Attribution

This file documents the upstream sources and licences that contribute
content to the standalone release dictionary. Every row in the
dictionary carries its own ``source`` and ``license`` columns, so the
authoritative per-row attribution lives inside the SQLite file itself;
this document summarises the upstream data suppliers whose work is
aggregated there.

The dictionary is a **read-only distributable application asset**. It
is not a derived database of any one source; it is a curated index of
lemmas, senses, localised meanings, and example sentences drawn from
the following upstreams, each under its own licence.

## Upstream sources

### German Wiktionary (`de.wiktionary.org`)

* Used for: lemma records, sense inventories, German glosses,
  grammatical metadata, IPA transcriptions.
* License: **Creative Commons Attribution-ShareAlike** (CC BY-SA).
* Each row carries ``source='wiktionary'`` and ``license='CC BY-SA'``.
  Reuse of CC BY-SA material under that licence requires preserving
  attribution and the share-alike obligation.

### English Wiktionary (`en.wiktionary.org`)

* Used for: localised English meanings (``sense_meaning`` rows where
  ``language='en'``) cross-referenced to the German sense inventory.
* License: **Creative Commons Attribution-ShareAlike** (CC BY-SA).
* Each row carries ``source='wiktionary'`` and ``license='CC BY-SA'``.

### Tatoeba (`tatoeba.org`)

* Used for: example sentence pairs and their DE/EN translations,
  surfaced through ``example`` rows and the ``example_lemma`` index.
* License: **Creative Commons Attribution 2.0 France** (CC BY 2.0 FR)
  for paired sentences; sentence-level metadata retains the original
  attribution.
* Each row carries ``source='tatoeba'`` and ``license='CC BY 2.0 FR'``
  (or the originating sentence's recorded licence).

## Stage classification

The release dictionary is **source-backed Stage-02 content** produced
by the offline build pipeline (``tools/build_dict.py``). No generated
rows are present in this artefact; rows with
``source='llm_generated_vN'`` belong to a separate, later dictionary
release that this manifest does not cover.

## Reuse obligations summary

| Source        | License          | Reuse obligation                              |
| ------------- | ---------------- | --------------------------------------------- |
| German Wiktionary | CC BY-SA       | Attribution + share-alike                     |
| English Wiktionary | CC BY-SA     | Attribution + share-alike                     |
| Tatoeba       | CC BY 2.0 FR      | Attribution                                   |

The full upstream licence texts are bundled with the dictionary
release in the ``LICENSE/`` directory beside this manifest.
