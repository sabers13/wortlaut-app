# Wortlaut Dictionary Attribution (v2)

This document covers the exact recovered Stage-04 dictionary identified by
SHA-256 `1698b9979099098bf8d6e6fd7f9194134a927d428e3c2b1905a626eb8ee67d4c`.
It is the publicly released Wortlaut dictionary v2 asset.

## Upstream sources

### German Wiktionary (`de.wiktionary.org`)

Used for lemma records, sense inventories, German glosses, grammatical metadata,
and IPA transcriptions. License: **CC BY-SA**.

### English Wiktionary (`en.wiktionary.org`)

Used for localized English meanings cross-referenced to the German sense
inventory. License: **CC BY-SA**.

### Tatoeba (`tatoeba.org`)

Used for example sentence pairs and DE/EN translations. License: **CC BY 2.0
FR** (or the recorded license of the originating sentence).

Every relevant dictionary row carries its own `source` and `license` fields; the
SQLite asset remains the authoritative per-row provenance record.

## Stage-04 additions

The recovered asset contains 48 `sense_meaning` rows with
`source='llm_generated_v1'`, `language='de'`, and `license='CC BY-SA'`. It also
contains 58 `sense_meaning_derivation` edges linking those generated rows to
non-generated source meanings. These edges preserve traversal to the source
material used as derivation input.

## Contributed manual adjudications

The recovered database also contains two `sense_meaning` rows with
`source='contributed'`, an asserted `license='CC BY-SA'`, and an empty-string
`language` field:

| `sense_meaning.id` | Associated manual adjudication |
| --- | --- |
| 577162 | Marmarameer |
| 577190 | Mod |

On 2026-09-03, the repository owner explicitly confirmed authorship or
redistribution rights for these manual adjudications and licensed them under CC
BY-SA for redistribution as part of the Wortlaut dictionary. This specific
contributor-rights prerequisite is satisfied. The database's asserted license
field is not itself the evidence of that confirmation.
