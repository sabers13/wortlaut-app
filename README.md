# Wortlaut

Wortlaut is a standalone, offline-first German flashcard application
(version 0.1.0). Your cards, reviews, meanings, and audio live only on
your machine.

Wortlaut is a single-user learning tool. You create decks, capture or
import vocabulary, study on a confidence-based review schedule, and
back up your own data. The dictionary is a read-only distributable
asset; your cards, reviews, and audio are private data you fully own.

Wortlaut works in two dictionary modes. Offline mode uses the full
local dictionary asset. Online mode fetches verified read-only
dictionary shards from the trusted dictionary distribution instead.
In both modes your user database, learning history, meanings, and
custom audio stay local: no study data is uploaded, and there is no
telemetry, no analytics, and no runtime AI.

---

## What you get

* A complete browser-based flashcard product: navigation, manual
  vocabulary entry, CSV import, two-stage capture, review with FSRS
  scheduling, DE/EN learner meanings, Anki (TSV + APKG) export.
* A read-only German dictionary asset covering ~100k indexed lemmas
  and ~777k Tatoeba example sentences.
* A standalone launcher that resolves per-user data paths and brings
  the app up at <http://127.0.0.1:8000>.
* Strict local-only operation. The service binds to the loopback
  interface only; no LAN exposure, no telemetry, and no user-data
  upload. Network use is limited to downloading verified dictionary
  release assets and, in Online mode, fetching read-only dictionary
  shards from the trusted dictionary distribution.

## Prerequisites

* Linux or macOS with Python 3.11 or newer.
* `libespeak-ng1` library for native Piper German pronunciation (on Debian/Ubuntu: `sudo apt install libespeak-ng1`).
* The Python packages declared in `pyproject.toml` (you install them
  yourself; the launcher does not auto-install, see "Install" below).
* `git` if you are installing from a clone.
* A modern browser (any current Chromium, Firefox, or Safari).
* **No Node.js is required.** The browser product's production assets
  ship with the repository (under `app/frontend/`); the launcher
  serves them directly. You never need to run `npm`, Vite, or any
  JavaScript toolchain yourself.

## Install

```bash
# 1. Get the source
git clone https://github.com/sabers13/wortlaut-app.git wortlaut
cd wortlaut

# 2. (Debian/Ubuntu) Install TTS audio system library
sudo apt install libespeak-ng1

# 3. Install Python dependencies into a local virtual environment
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# 4. Download and verify the v2 dictionary release asset.
./wortlaut --install-dictionary

# 5. Download and verify native German voice model (Thorsten high)
./wortlaut setup-voice

# 6. Launch
./wortlaut
```

You only need to install the dictionary once. Subsequent launches
reuse the verified dictionary already on disk.

`./wortlaut` automatically re-executes itself through the
repository-local `.venv/bin/python` interpreter, so you do not have to
`source .venv/bin/activate` first. `./flashcard` (the product's former
name) still works as a compatibility alias — it re-execs `./wortlaut`
with the same arguments — but new scripts and documentation should use
`./wortlaut`.

Installing the package also provides a `wortlaut` console command, so
after the editable install above you can use either form without
activating the virtual environment:

```bash
./wortlaut --version
# wortlaut 0.1.0

.venv/bin/wortlaut --version
# wortlaut 0.1.0

.venv/bin/wortlaut --no-browser
```

## First launch

The first time you start Wortlaut:

1. The launcher prints the resolved per-user data directory, for
   example `~/.local/share/flashcard/`.
2. It creates your private SQLite database (`flashcards.sqlite`) there
   from the authoritative `reference/schema.sql`.
3. It opens <http://127.0.0.1:8000> in your browser.

You will land on the empty deck screen. Create a deck, add a card,
review it, repeat.

## Studying a card

Each due card first shows only its headword, part of speech, and IPA:

```
das Haus
NOUN · /haʊ̯s/

[ Reveal answer ]
```

Press **Space** or click **Reveal answer** to see the answer. The
revealed answer stays deliberately compact — the primary German (and,
where available, English) learner meaning, one primary example
sentence, and a **Play pronunciation** button (replay it any time with
**R**) — followed by your **1–5** confidence rating for how well you
knew it.

Everything else — full grammar (including noun plural/genitive),
additional examples, personal meaning editing, and custom-pronunciation
recording/management — lives behind a **Show extra info** button so it
never clutters the default review path. Click it (or **Hide extra
info** to collapse it again) whenever you want the detail.

If you always want that detail visible, check **Always show extra
info** on any revealed card. From then on, Extra info opens
automatically as soon as you reveal a card — turn the checkbox off to
go back to reviewing compactly. This is a local browser preference
(stored under one `localStorage` key in your browser, never in the
user database or on the server), so it is per-browser and persists
across restarts, but is not part of your synced study data.

## Where data lives

| Concern                | Location                                                    |
| ---------------------- | ----------------------------------------------------------- |
| User data directory    | `$XDG_DATA_HOME/flashcard/` (defaults to `~/.local/share/flashcard/`) |
| Your cards / reviews   | `flashcards.sqlite` inside that directory                   |
| Custom pronunciation   | `media/` inside that directory                              |
| Audio cache            | `cache/` inside that directory                              |
| Dictionary asset       | `dictionary/dictionary.sqlite` inside that directory        |
| Piper voice model      | `$XDG_DATA_HOME/wortlaut/piper/` (defaults to `~/.local/share/wortlaut/piper/`) |

The dictionary file is a read-only distributable asset. Your private
files are independent — replacing the dictionary never touches your
cards, and removing your cards never affects the dictionary.

## Backup

The only user data you own is in `$XDG_DATA_HOME/flashcard/`.
To back up, copy that directory. To restore, copy it back. There is
no other database, no remote storage, no cloud.

If you want to move your cards to Anki, use the **Export APKG** and
**Export Anki TSV** buttons inside the app. They produce a single
self-contained file you can import into Anki.

## Dictionary verification

Dictionary verification happens in two deliberately separate stages so the
~945 MB asset is never validated twice on the same startup:

1. **Release identity precheck** (every ordinary canonical launch, before
   any user data is touched): exact manifest filename, exact byte size,
   streaming SHA-256. No SQLite is opened at this stage.
2. **Full validation**: `PRAGMA quick_check` plus the full PART-A schema
   validation reused from the live app. The live runtime validates an immutable
   private snapshot and requires its SHA-256 to equal the selected manifest, so
   replacing the canonical pathname after the precheck cannot change the active
   dictionary. This runs once per install
   (`--install-dictionary`, or when placing a file manually and letting the
   installer verify it) and once more at runtime activation
   (`DictionaryRuntime`), which is the authoritative integrity/schema gate
   for whichever dictionary file is actually opened.

Any failure aborts the install, deletes the partial file, and never
overwrites a valid dictionary. The dictionary URL is in the manifest
and can be updated without code changes; the same installer works
against any host (GitHub Release, public artifact mirror, local
`file://` URL).

## Offline and Online dictionary modes

Wortlaut selects its dictionary source per session:

* **Offline mode** uses the full local dictionary asset installed by
  `--install-dictionary`. No dictionary traffic leaves your machine.
* **Online mode** fetches verified read-only dictionary shards from the
  trusted dictionary distribution when you look words up. Shard content
  is integrity-checked before use.

The choice is session-scoped: start with `--dictionary-mode offline`
or `--dictionary-mode online`, or pick a mode in the app when no local
dictionary is installed yet. Dictionary installation itself always
downloads release assets from the distribution. Neither mode uploads
your cards, reviews, meanings, or audio.

## Dictionary installation status

Install the dictionary with:

```bash
./wortlaut --install-dictionary
```

The installer downloads the release asset and verifies its pinned SHA-256 and
byte count before installing it as `dictionary.sqlite`. The canonical installed
filename does not change between manifest versions — the manifest's `sha256` is
the durable identity. `--dict-path` is an advanced developer/recovery override;
it still undergoes PART-A validation, but deliberately does not assert the
active release identity.

## Updating the dictionary

The launcher does not silently upgrade. To install a newer
dictionary release:

1. Place the new `dictionary-manifest-vN.json` (and matching attribution /
   `LICENSE`) in `release/`.
2. Run `./wortlaut --manifest release/dictionary-manifest-vN.json
   --install-dictionary --data-dir <your data dir>`.

The installer refuses to overwrite a still-valid dictionary; remove
the old `dictionary.sqlite` first if you want a forced reinstall.
On the next normal startup, Wortlaut relinks semantic-reference-backed note
bindings and atomically replaces the active dictionary metadata; cards, review
history, and user-authored meanings are preserved.

## Docker data mounts

The container keeps the disposable dictionary and persistent user state in
separate mounts. Mount the dictionary read-only at `/dictionary` and user data
read-write at `/data`; do not mount both onto one host directory. The service
still listens only on `127.0.0.1:8000` inside its runtime configuration.

## Stopping and restarting

* **Stop**: `Ctrl+C` in the terminal where the launcher is running.
  The FastAPI server shuts down cleanly.
* **Restart**: `./wortlaut` again. Your state is preserved.
* **Run in the background**: `./wortlaut --no-browser` keeps the
  server up without opening a browser window. Combine with a
  process supervisor of your choice if you want a daemon.

## Custom port

To bind the API to a non-default port:

```bash
./wortlaut --port 8123
```

The launcher then binds `127.0.0.1:8123`, opens
`http://127.0.0.1:8123`, and the browser security middleware accepts
`http://127.0.0.1:8123` (and `http://localhost:8123`) as the same
origin. Non-loopback hosts and arbitrary origins remain rejected.

## Export to Anki

From any deck, click **Export APKG** (preferred) or **Export Anki
TSV**. The export contains:

* the German vocabulary front and back (with grammar, IPA, and
  examples) for every card in the deck;
* your custom pronunciation audio, attached to the right notes;
* your user-authored DE/EN meanings.

The export is a real `.apkg` you can drag into Anki; the TSV is the
tab-separated fallback for any tool that prefers plain text. Both
sanitise German commas and embedded newlines (the app never emits
a literal newline inside an Anki field).

## Privacy and local-only behavior

Wortlaut is a local, single-user application — not an internet-facing
multi-user service:

* The server binds to `127.0.0.1` only — never to a LAN interface.
  The bind address is not user-configurable.
* The browser-facing API enforces a loopback `Host` check and an
  exact-origin allowlist. No LAN or DNS-rebinding host can reach the
  deck API, and every browser-issued state-changing request carries a
  dedicated request header that arbitrary web pages cannot forge
  without triggering a CORS preflight.
* Your user database, learning history, meanings, and custom audio
  stay on your machine. Dictionary installation downloads release
  assets, and Online mode fetches verified read-only dictionary
  shards — but no user study data is ever uploaded.
* The active dictionary is immutable and identity-verified (pinned
  SHA-256 and byte count); replacing it never touches your cards.
* No runtime AI: no LLM SDK is installed, and there is no telemetry,
  no analytics, and no error reporting.
* No credentials, API keys, or tokens are stored anywhere.

## Troubleshooting

| Symptom                                  | Likely cause / fix                                          |
| ---------------------------------------- | ----------------------------------------------------------- |
| "repository virtualenv is missing"       | Run `python3 -m venv .venv && .venv/bin/pip install -e .` once. |
| "dictionary asset is missing"            | Run `./wortlaut --install-dictionary`, or place a verified `dictionary.sqlite` at the default slot. |
| "dictionary verification failed"        | The canonical file under `dictionary/` does not match the active v2 manifest. Remove the file and re-install it. |
| "no verified dictionary and the manifest has no download_url" | The selected manifest has no download URL. Use the v2 manifest or place a verified `dictionary.sqlite` manually at the default slot. |
| Browser does not open                    | Use `--no-browser` and visit <http://127.0.0.1:8000> yourself. |
| Port 8000 already in use                 | Pass `--port 8001` (and update your browser bookmark). The same-origin Origin header is accepted at the new port automatically. |
| `spacy` model not installed              | Reinstall project dependencies with `.venv/bin/pip install -e .`. |
| Native pronunciation not working         | Install `libespeak-ng1` (`sudo apt install libespeak-ng1`) and run `./wortlaut setup-voice`. |
| Stale deck state after a long offline    | Your data lives in `~/.local/share/flashcard/flashcards.sqlite`; back it up before any manual surgery. |

## Development and verification

To verify a source checkout, run the authoritative validation:

```bash
make gate
```

This runs linting (ruff), strict type checking (mypy), the full
Python test suite (pytest), and the repository's executable
consistency checks.

Frontend checks run from `frontend/`:

```bash
cd frontend
npm test
npm run typecheck
npm run build
npm run test:e2e
```

The browser end-to-end suite (`npm run test:e2e`, Playwright) is
heavier: it starts real servers and drives Chromium, so run it
separately rather than on every edit.

For the system design, see [`docs/architecture.md`](docs/architecture.md).
The authoritative database schema lives in `reference/schema.sql`.

## Licensing

The Wortlaut application source code is licensed under the [MIT License](LICENSE).
The dictionary and source-data content are not relicensed under MIT: their
licensing and attribution are governed separately by
[`release/ATTRIBUTION-v2.md`](release/ATTRIBUTION-v2.md) and the per-row
`source`/`license` metadata in the dictionary.
