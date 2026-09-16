# Wortlaut architecture

Wortlaut (application version 0.1.0) is a local, single-user German
vocabulary and flashcard application. This document describes the
system as built: its shape, data boundaries, identity model, learning
model, security boundary, and verification.

## Product shape

* A **FastAPI backend** (Python) owns all persistence, scheduling, and
  dictionary access. All endpoints live under one `/vocab` prefix.
* A **Lit/TypeScript frontend** implements navigation, vocabulary
  capture, review, and deck management.
* The production frontend is generated (`npm run build`) and served
  directly by the backend — the browser product ships with the
  repository, and end users never need a JavaScript toolchain.
* A single-command launcher (`./wortlaut`, also installed as the
  `wortlaut` console command) resolves per-user data paths, verifies
  the dictionary asset, and starts the server. `./wortlaut --version`
  reports the application version without touching any data or
  starting the server.

## Data separation

Dictionary data and user data never share a file, a lifecycle, or a
fate:

* The **dictionary asset** (`dictionary.sqlite` in the per-user data
  directory) is a read-only, disposable, refetchable distributable.
  Replacing it never touches user state.
* The **user database** (`flashcards.sqlite`) holds decks, notes,
  cards, review history, meanings, and memberships. Deleting user data
  never affects the dictionary.
* **Custom audio and media** live as files under the user data
  directory, referenced by the user database.
* The container layout keeps the same split: the dictionary mounts
  read-only, user data mounts read-write, never onto one host
  directory.

## Dictionary providers

Two dictionary sources exist behind one lookup contract:

* **Offline provider** — the full local dictionary asset
  (`dictionary-v2` release identity).
* **Online provider** — verified read-only dictionary shards fetched
  from the trusted dictionary distribution
  (`dictionary-online-v2` release identity). Shard bytes and manifests
  are integrity-checked before use.

The mode is selected per session (launcher flag or in-app choice when
no local dictionary is installed yet). Neither provider uploads user
data; both serve the same candidate/ranking semantics to the rest of
the app.

## Durable semantic identity

Dictionary SQLite numeric IDs are local per-asset keys only. Durable
identity is defined by stable semantic references:

* Every lemma and sense carries a content-derived semantic ref that
  survives dictionary rebuilds; numeric IDs are treated as caches.
* Each vocabulary note owns exactly one duplicate-safe identity key
  derived from those refs (resolved, never-bound, or ordered
  derived-compound vectors), enforced by a partial UNIQUE index.
* Captures that would create a second note for one identity reuse the
  existing note; genuinely ambiguous legacy duplicates fail closed
  with a dedicated conflict instead of silently merging.
* Stale dictionary tokens are rejected before writes, so a note can
  never be rebound against a dictionary generation it was not picked
  from.
* Dictionary replacement relinks bindings against the new asset and
  atomically swaps the active version: notes keep their identity,
  user-authored meanings, and review history throughout.

## Learning model

* One card identity per note: the card row and its FSRS scheduling
  state belong to the note for its whole lifetime.
* Reviews use the FSRS scheduler on a 1–5 confidence scale with a
  single fixed confidence-to-rating mapping.
* The `review_log` is append-only and stores both the raw 1–5
  confidence and the mapped FSRS rating, so the mapping stays
  revisable by replay.

## Management integrity

* Notes live in decks through membership rows; decks may sit in
  folders.
* Deleting a deck never cascade-deletes notes with review history:
  membership rows are removed and orphaned notes move to the
  protected `Orphaned` recovery deck, from which they can be restored
  into a normal deck with identity, scheduling, and history intact.
* A note's selected dictionary sense can be rebound to another sense
  of the same lemma (resolved to resolved, or promotion of a
  never-bound stub). The rebind preserves note/card identity, review
  history, memberships, user meanings, and custom pronunciation, and
  refuses collisions, cross-lemma targets, and unsupported statuses
  with structured conflicts instead of merging or overwriting.

## Local security boundary

* The server binds `127.0.0.1` only; the bind address is not
  configurable.
* Every request validates a loopback `Host`, and any present `Origin`
  must exactly match the configured allowlist — LAN and DNS-rebinding
  hosts are rejected.
* Every state-changing browser request requires a dedicated request
  header (which forces a CORS preflight for arbitrary web pages) and
  a JSON content type.
* There is no runtime AI of any kind, no telemetry, no analytics, and
  no user-data upload.

## Testing

* Strict `mypy` and `ruff` over the Python tree.
* `pytest` for the backend, including exact-match render/dictionary
  tests, duplicate-identity and restoration invariants, and
  race/rollback regression coverage.
* Frontend unit tests, `tsc` typechecking, and the production build.
* Playwright browser end-to-end tests for the management, study, and
  dictionary-mode flows.
* Machine-checked module ownership and focused-test mapping, wired
  into the same gate.
* CI runs the authoritative backend gate plus frontend
  unit/typecheck/build on every push and pull request; the heavier
  browser suite runs on manual dispatch only.

## Distribution and attribution

* The Wortlaut application source is MIT-licensed.
* Dictionary and source-data content are not relicensed under MIT:
  licensing and attribution are governed separately by the release
  attribution documents and the per-row `source`/`license` metadata
  carried by every sense, meaning, and example row.
