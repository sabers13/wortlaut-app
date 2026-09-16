# Wortlaut Dictionary Releases

Wortlaut keeps application versioning separate from dictionary versioning.
The application is versioned as `0.1.0`; dictionaries are versioned and
distributed independently through GitHub Releases of the distribution
repository.

## Offline dictionary

The offline dictionary distribution contract for the 0.1.0 public
publication is:

| Field | Value |
| --- | --- |
| tag | `dictionary-v2` |
| asset | `dictionary-v2.sqlite` |
| bytes | `945418240` |
| SHA-256 | `1698b9979099098bf8d6e6fd7f9194134a927d428e3c2b1905a626eb8ee67d4c` |

Install it with `./wortlaut --install-dictionary`. The installer
downloads the release asset and verifies its pinned SHA-256 and byte
count before installing it as `dictionary.sqlite`. The manifest's
`sha256` is the durable identity, not the filename.

## Online dictionary

The online dictionary distribution contract for the 0.1.0 public
publication is:

| Field | Value |
| --- | --- |
| tag | `dictionary-online-v2` |
| corpus assets | `577` (`256` lookup shards, `256` entry shards, `64` example shards, `1` membership-filter.bin) |
| plus | `dictionary-online-manifest-v2.json`, `ATTRIBUTION-v2.md` |
| total GitHub Release files | `579` |

Online mode fetches verified read-only dictionary shards from the
trusted dictionary distribution when you look words up. Shard content
is integrity-checked before use.

## Integrity

The corpus and manifests are immutable and integrity checked by
size and SHA-256.

## Distribution

| Concern | Location |
| --- | --- |
| Distribution repository | `https://github.com/sabers13/wortlaut-app` |
| Offline manifest | `release/dictionary-manifest-v2.json` |
| Online manifest | `release/dictionary-online-manifest-v2.json` |
| Historical manifest | `release/dictionary-manifest-v1.json` |

## Attribution

Dictionary and source-data licensing and attribution are governed by
`release/ATTRIBUTION-v2.md` (and `release/ATTRIBUTION.md` for the
historical v1 manifest). The application source code itself is licensed
under the MIT License (`LICENSE`).
