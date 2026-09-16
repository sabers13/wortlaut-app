"""Read-only dictionary asset reader for German flashcards.

Implements PART A of reference/schema.sql (ADR-0004 D36/D45/D46/D47):
- lemma
- surface_form
- sense
- sense_meaning
- sense_meaning_derivation
- example
- example_lemma

Never accesses, writes, or references PART B user tables (AGENTS R9 / C2).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from app.resolve import (
    LemmaRecord,
    LookupProtocol,
    Ref,
    SenseRecord,
    TokenLike,
    resolve_token,
    resolve_word,
)

if TYPE_CHECKING:
    import types


@dataclass(frozen=True)
class LemmaEntry(LemmaRecord):
    """Full structured lemma row matching PART A lemma table."""

    id: int
    lemma: str
    pos: str
    gender: str | None = None
    semantic_ref: str | None = None
    freq_rank: int | None = None
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
    ipa_source: str | None = None
    source: str | None = None
    license: str | None = None


@dataclass(frozen=True)
class SenseEntry(SenseRecord):
    """Sense row matching PART A sense table."""

    source_namespace: str = ""
    source_ref: str = ""
    register: str | None = None
    source: str | None = None
    license: str | None = None


@dataclass(frozen=True)
class MeaningEntry:
    """Localized meaning row matching PART A sense_meaning table (A7)."""

    id: int
    sense_id: int
    language: str
    kind: str
    ord: int
    text: str
    source: str
    license: str


@dataclass(frozen=True)
class ExampleEntry:
    """Example sentence row matching PART A example table."""

    id: int
    de: str
    en: str | None = None
    source: str | None = None
    source_ref: str | None = None
    license: str | None = None
    token_count: int | None = None
    has_proper: int = 0


@dataclass(frozen=True)
class DictionaryEntry:
    """Composite entry containing lemma, senses, examples, surface forms, and meanings."""

    lemma: LemmaEntry
    senses: list[SenseEntry]
    examples: list[ExampleEntry]
    surface_forms: list[str]
    meanings: list[MeaningEntry] = field(default_factory=list)


class DictionaryAssetError(ValueError):
    """Raised when a candidate dictionary cannot safely be activated later."""


_REQUIRED_PART_A_COLUMNS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "lemma": frozenset(
            {
                "id",
                "semantic_ref",
                "lemma",
                "pos",
                "gender",
                "plural",
                "plural_none",
                "genitive_sg",
                "aux",
                "separable",
                "particle",
                "reflexive",
                "praesens_3sg",
                "praeteritum_3sg",
                "partizip_ii",
                "governs",
                "comparative",
                "superlative",
                "ipa",
                "ipa_source",
                "freq_rank",
                "source",
                "license",
            }
        ),
        "surface_form": frozenset({"form", "lemma_id"}),
        "sense": frozenset(
            {
                "id",
                "lemma_id",
                "semantic_ref",
                "source_namespace",
                "source_ref",
                "ord",
                "register",
                "source",
                "license",
            }
        ),
        "sense_meaning": frozenset(
            {"id", "sense_id", "language", "kind", "ord", "text", "source", "license"}
        ),
        "sense_meaning_derivation": frozenset({"generated_meaning_id", "source_meaning_id"}),
        "example": frozenset(
            {"id", "de", "en", "source", "source_ref", "license", "token_count", "has_proper"}
        ),
        "example_lemma": frozenset({"lemma_id", "example_id"}),
    }
)
_LEMMA_REF_RE = re.compile(r"lemma:v1:[0-9a-f]{64}\Z")
_SENSE_REF_RE = re.compile(r"sense:v1:[0-9a-f]{64}\Z")


def _canonical_payload(values: Sequence[str]) -> bytes:
    """Serialize exact persisted fields for a D47 identity hash."""
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _identity_fingerprint(values: Sequence[str]) -> str:
    """Return the SHA-256 fingerprint of one exact stable-identity tuple."""
    return sha256(_canonical_payload(values)).hexdigest()


def _required_text(value: object, field_name: str) -> str:
    """Return exact canonical text without coercion, trimming, or normalization."""
    if (
        not isinstance(value, str)
        or value == ""
        or value[0].isspace()
        or value[-1].isspace()
        or not unicodedata.is_normalized("NFC", value)
    ):
        raise DictionaryAssetError(f"candidate has invalid {field_name}")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    """Return exact nullable text, rejecting all non-text SQLite values."""
    return None if value is None else _required_text(value, field_name)


def _required_id(value: object, field_name: str) -> int:
    """Return an uncoerced SQLite integer identifier."""
    if type(value) is not int:
        raise DictionaryAssetError(f"candidate has invalid {field_name}")
    return value


def _has_unique_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check for the required single-column unique constraint/index."""
    for index in conn.execute(f"PRAGMA index_list({table})"):
        if int(index[2]) != 1:
            continue
        index_name = str(index[1]).replace("'", "''")
        columns = conn.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        if [str(index_column[2]) for index_column in columns] == [column]:
            return True
    return False


def _has_foreign_key(conn: sqlite3.Connection, table: str, column: str, target_table: str) -> bool:
    """Check a required PART-A foreign-key link."""
    return any(
        str(row[2]) == target_table and str(row[3]) == column
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    )


def _validate_part_a_schema(conn: sqlite3.Connection) -> None:
    """Validate the PART-A shape and D47 constraints needed for safe relinking."""
    tables = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table, required_columns in _REQUIRED_PART_A_COLUMNS.items():
        if table not in tables:
            raise DictionaryAssetError(f"candidate lacks PART-A table {table}")
        columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        if missing := required_columns - columns:
            raise DictionaryAssetError(
                f"candidate table {table} lacks columns: {', '.join(sorted(missing))}"
            )

    lemma_info = {str(row[1]): row for row in conn.execute("PRAGMA table_info(lemma)")}
    sense_info = {str(row[1]): row for row in conn.execute("PRAGMA table_info(sense)")}
    if int(lemma_info["id"][5]) != 1:
        raise DictionaryAssetError("candidate lemma.id is not a primary key")
    if int(sense_info["id"][5]) != 1:
        raise DictionaryAssetError("candidate sense.id is not a primary key")
    if int(lemma_info["semantic_ref"][3]) != 1 or not _has_unique_column(
        conn, "lemma", "semantic_ref"
    ):
        raise DictionaryAssetError("candidate lemma.semantic_ref must be NOT NULL and UNIQUE")
    if (
        int(sense_info["lemma_id"][3]) != 1
        or int(sense_info["semantic_ref"][3]) != 1
        or int(sense_info["source_namespace"][3]) != 1
        or int(sense_info["source_ref"][3]) != 1
        or not _has_unique_column(conn, "sense", "semantic_ref")
        or not _has_foreign_key(conn, "sense", "lemma_id", "lemma")
    ):
        raise DictionaryAssetError("candidate sense D47 identity constraints are incomplete")


def _build_lemma_ref_maps(
    rows: Iterable[tuple[object, object, object, object, object]],
) -> tuple[dict[str, int], dict[str, str], dict[int, str]]:
    """Verify lemma refs and build indexes from exact persisted row values."""
    lemma_ids: dict[str, int] = {}
    fingerprints: dict[str, str] = {}
    refs_by_id: dict[int, str] = {}
    for raw_id, raw_ref, raw_lemma, raw_pos, raw_gender in rows:
        lemma_id = _required_id(raw_id, "lemma.id")
        ref = _required_text(raw_ref, "lemma.semantic_ref")
        lemma = _required_text(raw_lemma, "lemma.lemma")
        pos = _required_text(raw_pos, "lemma.pos")
        gender = _optional_text(raw_gender, "lemma.gender")
        fingerprint = _identity_fingerprint(("de", lemma, pos, gender or "<null>"))
        if not _LEMMA_REF_RE.fullmatch(ref) or ref != f"lemma:v1:{fingerprint}":
            raise DictionaryAssetError("candidate lemma semantic_ref is malformed or mismatched")
        if ref in lemma_ids or lemma_id in refs_by_id:
            raise DictionaryAssetError("candidate has duplicate or ambiguous lemma semantic_ref")
        lemma_ids[ref] = lemma_id
        fingerprints[ref] = fingerprint
        refs_by_id[lemma_id] = ref
    return lemma_ids, fingerprints, refs_by_id


def _build_sense_ref_maps(
    rows: Iterable[tuple[object, object, object, object, object]],
    lemma_refs_by_id: Mapping[int, str],
) -> tuple[dict[str, tuple[int, int]], dict[str, str]]:
    """Verify sense refs and build indexes from exact persisted row values."""
    sense_ids: dict[str, tuple[int, int]] = {}
    fingerprints: dict[str, str] = {}
    for raw_id, raw_lemma_id, raw_ref, raw_namespace, raw_source_ref in rows:
        sense_id = _required_id(raw_id, "sense.id")
        lemma_id = _required_id(raw_lemma_id, "sense.lemma_id")
        ref = _required_text(raw_ref, "sense.semantic_ref")
        namespace = _required_text(raw_namespace, "sense.source_namespace")
        source_ref = _required_text(raw_source_ref, "sense.source_ref")
        lemma_ref = lemma_refs_by_id.get(lemma_id)
        if lemma_ref is None:
            raise DictionaryAssetError("candidate sense references an unknown lemma")
        fingerprint = _identity_fingerprint((lemma_ref, namespace, source_ref))
        if not _SENSE_REF_RE.fullmatch(ref) or ref != f"sense:v1:{fingerprint}":
            raise DictionaryAssetError("candidate sense semantic_ref is malformed or mismatched")
        if ref in sense_ids:
            raise DictionaryAssetError("candidate has duplicate or ambiguous sense semantic_ref")
        sense_ids[ref] = (sense_id, lemma_id)
        fingerprints[ref] = fingerprint
    return sense_ids, fingerprints


class _AssetLease:
    """Own one private byte snapshot and its read-only SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._released = False

    def release(self) -> None:
        """Release both resources; safe to call more than once."""
        if self._released:
            return
        self._released = True
        self.connection.close()


@dataclass(frozen=True, slots=True)
class DictionaryAsset:
    """Validated immutable snapshot plus D47 indexes for a later atomic swap."""

    path: Path
    sha256: str
    lemma_ids: Mapping[str, int]
    sense_ids: Mapping[str, tuple[int, int]]
    lemma_identity_fingerprints: Mapping[str, str]
    sense_identity_fingerprints: Mapping[str, str]
    _lease: _AssetLease = field(repr=False, compare=False)

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the prevalidated read-only handle for the hashed snapshot."""
        return self._lease.connection

    @property
    def asset_token(self) -> str:
        """Return the digest token a later activation owner will publish."""
        return self.sha256

    def close(self) -> None:
        """Close/release the candidate snapshot when it is not installed."""
        self._lease.release()

    def release(self) -> None:
        """Alias for close, for activation rollback paths."""
        self.close()


def _snapshot_candidate(path: Path) -> tuple[sqlite3.Connection, str]:
    """Copy candidate bytes once into an unlinked private snapshot and open it read-only."""
    sidecars = (Path(f"{path}-journal"), Path(f"{path}-wal"), Path(f"{path}-shm"))
    if any(sidecar.exists() for sidecar in sidecars):
        raise DictionaryAssetError("candidate has SQLite sidecar state outside its asset bytes")
    try:
        # This is deliberately the sole read of candidate content: the digest,
        # validation, indexes, and retained handle all derive from these bytes.
        candidate_bytes = path.read_bytes()
    except OSError as exc:
        raise DictionaryAssetError(f"candidate cannot be read: {path}") from exc
    digest = sha256(candidate_bytes).hexdigest()
    descriptor, snapshot_name = tempfile.mkstemp(suffix=".sqlite")
    snapshot_path = Path(snapshot_name)
    connection: sqlite3.Connection | None = None
    try:
        with os.fdopen(descriptor, "wb") as snapshot:
            snapshot.write(candidate_bytes)
            snapshot.flush()
            os.fsync(snapshot.fileno())
            if not stat.S_ISREG(os.fstat(snapshot.fileno()).st_mode):
                raise DictionaryAssetError("candidate snapshot is not a regular file")
        connection = sqlite3.connect(
            f"{snapshot_path.as_uri()}?mode=ro&immutable=1",
            uri=True,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        snapshot_path.unlink()
        return connection, digest
    except (OSError, sqlite3.Error, DictionaryAssetError) as exc:
        if connection is not None:
            connection.close()
        snapshot_path.unlink(missing_ok=True)
        if isinstance(exc, DictionaryAssetError):
            raise
        raise DictionaryAssetError(
            "candidate cannot be opened as a read-only SQLite asset"
        ) from exc


def validate_candidate_dictionary(path: Path | str) -> DictionaryAsset:
    """Validate a candidate PART-A asset against one immutable byte snapshot.

    The reported digest is over the one source read. The SQLite checks, D47 map
    construction, and retained handle all operate on an unlinked temporary copy
    of those exact bytes, never the candidate pathname after that read.
    """
    candidate_path = Path(path)
    if not candidate_path.is_file():
        raise DictionaryAssetError(f"candidate dictionary file not found: {candidate_path}")
    connection, asset_sha256 = _snapshot_candidate(candidate_path)
    try:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        if not integrity_rows or any(str(row[0]).lower() != "ok" for row in integrity_rows):
            raise DictionaryAssetError("candidate integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DictionaryAssetError("candidate foreign_key_check failed")
        _validate_part_a_schema(connection)
        lemma_ids, lemma_fingerprints, lemma_refs_by_id = _build_lemma_ref_maps(
            connection.execute("SELECT id, semantic_ref, lemma, pos, gender FROM lemma")
        )
        sense_ids, sense_fingerprints = _build_sense_ref_maps(
            connection.execute(
                "SELECT id, lemma_id, semantic_ref, source_namespace, source_ref FROM sense"
            ),
            lemma_refs_by_id,
        )
        return DictionaryAsset(
            path=candidate_path,
            sha256=asset_sha256,
            lemma_ids=MappingProxyType(lemma_ids),
            sense_ids=MappingProxyType(sense_ids),
            lemma_identity_fingerprints=MappingProxyType(lemma_fingerprints),
            sense_identity_fingerprints=MappingProxyType(sense_fingerprints),
            _lease=_AssetLease(connection),
        )
    except (sqlite3.Error, ValueError, TypeError) as exc:
        connection.close()
        if isinstance(exc, DictionaryAssetError):
            raise
        raise DictionaryAssetError("candidate PART-A validation failed") from exc


def inspect_dictionary_asset(path: Path | str) -> DictionaryAsset:
    """Backward-compatible name for candidate validation without activation."""
    return validate_candidate_dictionary(path)


def _row_to_lemma(row: sqlite3.Row) -> LemmaEntry:
    """Map a sqlite3.Row to LemmaEntry."""
    return LemmaEntry(
        id=int(row["id"]),
        lemma=str(row["lemma"]),
        pos=str(row["pos"]),
        gender=str(row["gender"]) if row["gender"] is not None else None,
        semantic_ref=(str(row["semantic_ref"]) if row["semantic_ref"] is not None else None),
        freq_rank=int(row["freq_rank"]) if row["freq_rank"] is not None else None,
        plural=str(row["plural"]) if row["plural"] is not None else None,
        plural_none=(
            int(row["plural_none"])
            if "plural_none" in row.keys() and row["plural_none"] is not None
            else 0
        ),
        genitive_sg=str(row["genitive_sg"]) if row["genitive_sg"] is not None else None,
        aux=str(row["aux"]) if row["aux"] is not None else None,
        separable=int(row["separable"]) if row["separable"] is not None else 0,
        particle=str(row["particle"]) if row["particle"] is not None else None,
        reflexive=int(row["reflexive"]) if row["reflexive"] is not None else 0,
        praesens_3sg=(str(row["praesens_3sg"]) if row["praesens_3sg"] is not None else None),
        praeteritum_3sg=(
            str(row["praeteritum_3sg"]) if row["praeteritum_3sg"] is not None else None
        ),
        partizip_ii=(str(row["partizip_ii"]) if row["partizip_ii"] is not None else None),
        governs=str(row["governs"]) if row["governs"] is not None else None,
        comparative=str(row["comparative"]) if row["comparative"] is not None else None,
        superlative=str(row["superlative"]) if row["superlative"] is not None else None,
        ipa=str(row["ipa"]) if row["ipa"] is not None else None,
        ipa_source=str(row["ipa_source"]) if row["ipa_source"] is not None else None,
        source=str(row["source"]) if row["source"] is not None else None,
        license=str(row["license"]) if row["license"] is not None else None,
    )


def _row_to_sense(row: sqlite3.Row) -> SenseEntry:
    """Map a sqlite3.Row to SenseEntry."""
    return SenseEntry(
        id=int(row["id"]),
        lemma_id=int(row["lemma_id"]),
        ord=int(row["ord"]),
        semantic_ref=str(row["semantic_ref"]),
        source_namespace=str(row["source_namespace"]),
        source_ref=str(row["source_ref"]),
        register=str(row["register"]) if row["register"] is not None else None,
        source=str(row["source"]) if row["source"] is not None else None,
        license=str(row["license"]) if row["license"] is not None else None,
    )


def _row_to_meaning(row: sqlite3.Row) -> MeaningEntry:
    """Map a sqlite3.Row to MeaningEntry."""
    return MeaningEntry(
        id=int(row["id"]),
        sense_id=int(row["sense_id"]),
        language=str(row["language"]),
        kind=str(row["kind"]),
        ord=int(row["ord"]),
        text=str(row["text"]),
        source=str(row["source"]),
        license=str(row["license"]),
    )


def _row_to_example(row: sqlite3.Row) -> ExampleEntry:
    """Map a sqlite3.Row to ExampleEntry."""
    return ExampleEntry(
        id=int(row["id"]),
        de=str(row["de"]),
        en=str(row["en"]) if row["en"] is not None else None,
        source=str(row["source"]) if row["source"] is not None else None,
        source_ref=str(row["source_ref"]) if row["source_ref"] is not None else None,
        license=str(row["license"]) if row["license"] is not None else None,
        token_count=int(row["token_count"]) if row["token_count"] is not None else None,
        has_proper=int(row["has_proper"]) if row["has_proper"] is not None else 0,
    )


class Dictionary(LookupProtocol):
    """Read-only SQLite dictionary reader implementing LookupProtocol."""

    def __init__(self, db_path: Path | str) -> None:
        """Open SQLite database in read-only mode."""
        self._path = Path(db_path)
        if not self._path.exists():
            raise FileNotFoundError(f"Dictionary database file not found: {self._path}")

        # Open SQLite read-only with URI mode=ro
        uri = f"file:{self._path.resolve().as_posix()}?mode=ro"
        try:
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.OperationalError:
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.execute("PRAGMA query_only = ON;")

        self._conn.row_factory = sqlite3.Row

    @property
    def path(self) -> Path:
        """Return the database path."""
        return self._path

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> Dictionary:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.close()

    def lookup_exact(
        self, lemma: str, pos: str | None = None, gender: str | None = None
    ) -> Sequence[LemmaEntry]:
        """Look up lemma by exact text, optionally filtered by POS and/or gender."""
        query = [
            "SELECT id, semantic_ref, lemma, pos, gender, plural, plural_none, genitive_sg, aux,",
            "       separable, particle, reflexive, praesens_3sg, praeteritum_3sg, partizip_ii,",
            "       governs, comparative, superlative, ipa, ipa_source, freq_rank, source, license",
            "FROM lemma",
            "WHERE (lemma = ? OR lower(lemma) = ?)",
        ]
        params: list[Any] = [lemma, lemma.lower()]

        if pos is not None:
            query.append("AND pos = ?")
            params.append(pos)

        if gender is not None:
            query.append("AND gender = ?")
            params.append(gender)

        query.append(
            "ORDER BY freq_rank ASC NULLS LAST, pos ASC, gender ASC NULLS LAST, semantic_ref ASC"
        )
        cur = self._conn.execute(" ".join(query), params)
        return [_row_to_lemma(row) for row in cur.fetchall()]

    def lookup_surface_form(self, form: str) -> Sequence[LemmaEntry]:
        """Look up lemmas associated with an inflected surface form."""
        query = (
            "SELECT l.id, l.semantic_ref, l.lemma, l.pos, l.gender, l.plural, l.plural_none, "
            "       l.genitive_sg, l.aux, l.separable, l.particle, l.reflexive, l.praesens_3sg, "
            "       l.praeteritum_3sg, l.partizip_ii, l.governs, l.comparative, "
            "       l.superlative, l.ipa, l.ipa_source, l.freq_rank, l.source, l.license "
            "FROM surface_form sf "
            "JOIN lemma l ON sf.lemma_id = l.id "
            "WHERE (sf.form = ? OR lower(sf.form) = ?) "
            "ORDER BY l.freq_rank ASC NULLS LAST, l.pos ASC, l.gender ASC NULLS LAST, "
            "         l.semantic_ref ASC"
        )
        cur = self._conn.execute(query, [form, form.lower()])
        seen: set[int] = set()
        results: list[LemmaEntry] = []
        for row in cur.fetchall():
            lemma_entry = _row_to_lemma(row)
            if lemma_entry.id not in seen:
                seen.add(lemma_entry.id)
                results.append(lemma_entry)
        return results

    def lookup_senses(self, lemma_id: int) -> Sequence[SenseEntry]:
        """Look up source senses for a lemma (satisfies LookupProtocol / A11)."""
        return self.get_senses_for_lemma(lemma_id)

    def get_lemma_by_id(self, lemma_id: int) -> LemmaEntry | None:
        """Fetch a single lemma row by primary key."""
        query = (
            "SELECT id, semantic_ref, lemma, pos, gender, plural, plural_none, genitive_sg, aux, "
            "       separable, particle, reflexive, praesens_3sg, praeteritum_3sg, partizip_ii, "
            "       governs, comparative, superlative, ipa, ipa_source, freq_rank, source, license "
            "FROM lemma WHERE id = ?"
        )
        cur = self._conn.execute(query, [lemma_id])
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_lemma(row)

    def get_senses_for_lemma(self, lemma_id: int) -> list[SenseEntry]:
        """Fetch all senses for a lemma, ordered by ord, then semantic_ref (A13)."""
        query = (
            "SELECT id, lemma_id, semantic_ref, source_namespace, source_ref, ord, "
            "       register, source, license "
            "FROM sense WHERE lemma_id = ? "
            "ORDER BY ord ASC, semantic_ref ASC, id ASC"
        )
        cur = self._conn.execute(query, [lemma_id])
        return [_row_to_sense(row) for row in cur.fetchall()]

    def get_meanings_for_sense(self, sense_id: int) -> list[MeaningEntry]:
        """Fetch all localized meanings for a sense, ordered deterministically (A13)."""
        query = (
            "SELECT id, sense_id, language, kind, ord, text, source, license "
            "FROM sense_meaning WHERE sense_id = ? "
            "ORDER BY language ASC, kind ASC, ord ASC, id ASC"
        )
        cur = self._conn.execute(query, [sense_id])
        return [_row_to_meaning(row) for row in cur.fetchall()]

    def get_meanings_for_lemma(self, lemma_id: int) -> list[MeaningEntry]:
        """Fetch all localized meanings for a lemma via its senses (A13)."""
        query = (
            "SELECT sm.id, sm.sense_id, sm.language, sm.kind, sm.ord, sm.text, "
            "       sm.source, sm.license "
            "FROM sense_meaning sm "
            "JOIN sense s ON sm.sense_id = s.id "
            "WHERE s.lemma_id = ? "
            "ORDER BY sm.language ASC, sm.kind ASC, sm.ord ASC, sm.id ASC"
        )
        cur = self._conn.execute(query, [lemma_id])
        return [_row_to_meaning(row) for row in cur.fetchall()]

    def get_examples_for_lemma(
        self, lemma_id: int, *, limit: int | None = None
    ) -> list[ExampleEntry]:
        """Fetch example sentences indexed for a lemma via example_lemma."""
        query = (
            "SELECT e.id, e.de, e.en, e.source, e.source_ref, e.license, "
            "       e.token_count, e.has_proper "
            "FROM example_lemma el "
            "JOIN example e ON el.example_id = e.id "
            "WHERE el.lemma_id = ? "
            "ORDER BY e.id ASC"
        )
        params: list[Any] = [lemma_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
        cur = self._conn.execute(query, params)
        return [_row_to_example(row) for row in cur.fetchall()]

    def get_surface_forms_for_lemma(self, lemma_id: int) -> list[str]:
        """Fetch all surface forms recorded for a lemma."""
        query = "SELECT form FROM surface_form WHERE lemma_id = ? ORDER BY form ASC"
        cur = self._conn.execute(query, [lemma_id])
        return [str(row["form"]) for row in cur.fetchall()]

    def get_entry(
        self,
        lemma_id: int,
        *,
        skip_examples: bool = False,
        limit_examples: int | None = None,
    ) -> DictionaryEntry | None:
        """Fetch composite entry (lemma + senses + meanings + examples + surface forms)."""
        lemma = self.get_lemma_by_id(lemma_id)
        if lemma is None:
            return None
        senses = self.get_senses_for_lemma(lemma_id)
        meanings = self.get_meanings_for_lemma(lemma_id)
        examples = (
            [] if skip_examples else self.get_examples_for_lemma(lemma_id, limit=limit_examples)
        )
        surface_forms = self.get_surface_forms_for_lemma(lemma_id)
        return DictionaryEntry(
            lemma=lemma,
            senses=senses,
            examples=examples,
            surface_forms=surface_forms,
            meanings=meanings,
        )

    def suggest_lemmas(self, prefix: str, limit: int = 10) -> list[LemmaEntry]:
        """Prefix lookup for autocomplete suggestions (ADR-0001 §10)."""
        query = (
            "SELECT id, semantic_ref, lemma, pos, gender, plural, plural_none, genitive_sg, aux, "
            "       separable, particle, reflexive, praesens_3sg, praeteritum_3sg, partizip_ii, "
            "       governs, comparative, superlative, ipa, ipa_source, freq_rank, source, license "
            "FROM lemma "
            "WHERE lemma LIKE ? || '%' "
            "ORDER BY freq_rank ASC NULLS LAST, lemma ASC "
            "LIMIT ?"
        )
        cur = self._conn.execute(query, [prefix, limit])
        return [_row_to_lemma(row) for row in cur.fetchall()]

    def resolve(
        self,
        word: str,
        pos: str | None = None,
        gender: str | None = None,
    ) -> Sequence[Ref]:
        """Convenience method resolving a bare word through the ladder."""
        return resolve_word(word, oracle=self, pos=pos, gender=gender)

    def resolve_tok(self, tok: TokenLike) -> Sequence[Ref]:
        """Convenience method resolving a token through the ladder."""
        return resolve_token(tok, oracle=self)
