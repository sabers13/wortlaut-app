"""Tests for app/dict_install.py — manifest parsing, byte-level
verification, and atomic install fail-closed behaviour.

The tests use a tiny synthetic dictionary so they are deterministic and
do not depend on the full ~900 MB artefact. The real-dictionary code
path is exercised through ``app/dictionary.validate_candidate_dictionary``
which the installer reuses; if a real Stage-02 dictionary were
dropped at the path, the same code path would verify it.
"""

from __future__ import annotations

import http.server
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from app.dict_install import (
    DictionaryInstallerError,
    DictionaryManifest,
    compute_sha256,
    expected_bytes,
    expected_filename,
    expected_sha256,
    install_dictionary,
    load_manifest,
    parse_manifest_payload,
    verify_dictionary_bytes,
    verify_dictionary_identity,
)
from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "reference" / "schema.sql"


def _part_a_sql() -> str:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    part_a, marker, _ = text.partition("-- PART B")
    assert marker
    return part_a


@pytest.fixture
def part_a_sql() -> str:
    return _part_a_sql()


@pytest.fixture
def synthetic_dict(tmp_path: Path, part_a_sql: str) -> Path:
    db_path = tmp_path / "tiny.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(part_a_sql)
        lemma_ref = compute_lemma_semantic_ref("Haus", "NOUN", "das")
        sense_ref = compute_sense_semantic_ref(
            lemma_ref, "wiktextract:enwiktionary", "senseid:en-haus-1"
        )
        conn.execute(
            """
            INSERT INTO lemma (id, semantic_ref, lemma, pos, gender,
                plural_none, source, license)
            VALUES (?, ?, ?, ?, ?, 0, 'wiktionary', 'CC BY-SA')
            """,
            (1, lemma_ref, "Haus", "NOUN", "das"),
        )
        conn.execute(
            """
            INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace,
                source_ref, ord, source, license)
            VALUES (?, ?, ?, ?, ?, 0, 'wiktionary', 'CC BY-SA')
            """,
            (1, 1, sense_ref, "wiktextract:enwiktionary", "senseid:en-haus-1"),
        )
        conn.execute(
            """
            INSERT INTO sense_meaning (id, sense_id, language, kind, ord,
                text, source, license)
            VALUES (?, ?, 'en', 'translation', 0, 'house',
                'wiktionary', 'CC BY-SA')
            """,
            (1, 1),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _manifest_dict(synthetic_dict: Path) -> dict[str, object]:
    size = synthetic_dict.stat().st_size
    sha = compute_sha256(synthetic_dict)
    return {
        "version": "v1",
        "filename": synthetic_dict.name,
        "sha256": sha,
        "bytes": size,
        "classification": "source-backed-stage02",
        "attribution": "ATTRIBUTION.md",
        "download_url": None,
    }


@pytest.fixture
def manifest_payload(synthetic_dict: Path) -> dict[str, object]:
    return _manifest_dict(synthetic_dict)


@pytest.fixture
def manifest_file(tmp_path: Path, manifest_payload: dict[str, object]) -> Path:
    path = tmp_path / "dictionary-manifest-v1.json"
    path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    return path


@pytest.fixture
def manifest(tmp_path: Path, manifest_payload: dict[str, object]) -> DictionaryManifest:
    manifest_file_path = tmp_path / "dictionary-manifest-v1.json"
    manifest_file_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    return load_manifest(manifest_file_path)


def test_parse_manifest_payload_basic(manifest_payload: dict[str, object]) -> None:
    parsed = parse_manifest_payload(manifest_payload, manifest_path=Path("/dev/null"))
    assert parsed.version == "v1"
    assert parsed.classification == "source-backed-stage02"
    assert parsed.download_url is None
    assert len(parsed.sha256) == 64


def test_parse_manifest_payload_rejects_non_object() -> None:
    with pytest.raises(DictionaryInstallerError, match="must decode to an object"):
        parse_manifest_payload([], manifest_path=Path("/dev/null"))


def test_parse_manifest_payload_rejects_missing_field() -> None:
    bad = {
        "version": "v1",
        "filename": "x.sqlite",
        "sha256": "00" * 32,
        # missing 'bytes'
        "classification": "x",
        "attribution": "y",
    }
    with pytest.raises(DictionaryInstallerError, match="'bytes'"):
        parse_manifest_payload(bad, manifest_path=Path("/dev/null"))


def test_parse_manifest_payload_rejects_bad_filename() -> None:
    bad = _manifest_dict_path("subdir/evil.sqlite", b"")
    with pytest.raises(DictionaryInstallerError, match="portable single-segment"):
        parse_manifest_payload(bad, manifest_path=Path("/dev/null"))


def test_parse_manifest_payload_rejects_cred_in_url() -> None:
    bad = _manifest_dict_path("ok.sqlite", b"")
    bad["download_url"] = "https://user:token@example.com/x"
    with pytest.raises(DictionaryInstallerError, match="credentials"):
        parse_manifest_payload(bad, manifest_path=Path("/dev/null"))


def test_parse_manifest_payload_accepts_file_scheme() -> None:
    payload = _manifest_dict_path("ok.sqlite", b"")
    payload["download_url"] = "file:///tmp/x.sqlite"
    parsed = parse_manifest_payload(payload, manifest_path=Path("/dev/null"))
    assert parsed.download_url == "file:///tmp/x.sqlite"


def test_parse_manifest_payload_rejects_invalid_sha() -> None:
    bad = _manifest_dict_path("ok.sqlite", b"")
    bad["sha256"] = "not-hex"
    with pytest.raises(DictionaryInstallerError, match="hex"):
        parse_manifest_payload(bad, manifest_path=Path("/dev/null"))


def test_parse_manifest_payload_rejects_oversized_bytes() -> None:
    bad = _manifest_dict_path("ok.sqlite", b"")
    bad["bytes"] = 2 * 1024 * 1024 * 1024 + 1
    with pytest.raises(DictionaryInstallerError, match="bytes"):
        parse_manifest_payload(bad, manifest_path=Path("/dev/null"))


def test_load_manifest_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DictionaryInstallerError, match="not found"):
        load_manifest(tmp_path / "no.json")


def test_load_manifest_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(DictionaryInstallerError, match="JSON"):
        load_manifest(path)


def test_default_v2_manifest_and_historical_v1_manifest_load() -> None:
    release_dir = Path(__file__).resolve().parents[1] / "release"
    v2 = load_manifest(release_dir / "dictionary-manifest-v2.json")
    v1 = load_manifest(release_dir / "dictionary-manifest-v1.json")
    assert v2.version == "v2"
    assert v2.filename == "dictionary.sqlite"
    assert v2.sha256 == "1698b9979099098bf8d6e6fd7f9194134a927d428e3c2b1905a626eb8ee67d4c"
    assert v2.bytes == 945418240
    assert (
        v2.download_url
        == "https://github.com/sabers13/wortlaut-app/releases/download/dictionary-v2/dictionary-v2.sqlite"
    )
    assert v1.version == "v1"
    assert v1.sha256 == "75658966655bd68729b105dbae1b62f500b30e8e2d08b9689b207f72c4997f97"


def test_expected_helpers(manifest: DictionaryManifest) -> None:
    assert expected_filename(manifest) == manifest.filename
    assert expected_sha256(manifest) == manifest.sha256
    assert expected_bytes(manifest) == manifest.bytes


def test_compute_sha256_matches_hashlib(synthetic_dict: Path) -> None:
    from hashlib import sha256

    digest = compute_sha256(synthetic_dict)
    assert digest == sha256(synthetic_dict.read_bytes()).hexdigest()


def test_verify_dictionary_bytes_ok(synthetic_dict: Path) -> None:
    digest = verify_dictionary_bytes(
        synthetic_dict,
        expected_sha256=compute_sha256(synthetic_dict),
        expected_bytes=synthetic_dict.stat().st_size,
    )
    assert len(digest) == 64


def test_verify_dictionary_bytes_size_mismatch(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    with pytest.raises(DictionaryInstallerError, match="size mismatch"):
        verify_dictionary_bytes(
            synthetic_dict,
            expected_sha256=compute_sha256(synthetic_dict),
            expected_bytes=synthetic_dict.stat().st_size + 1,
        )


def test_verify_dictionary_bytes_sha_mismatch(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    with pytest.raises(DictionaryInstallerError, match="SHA-256 mismatch"):
        verify_dictionary_bytes(
            synthetic_dict,
            expected_sha256="0" * 64,
            expected_bytes=synthetic_dict.stat().st_size,
        )


def test_verify_dictionary_bytes_quick_check_fails(
    tmp_path: Path, part_a_sql: str
) -> None:
    bad = tmp_path / "broken.sqlite"
    bad.write_bytes(b"NOT A SQLITE FILE")
    with pytest.raises(DictionaryInstallerError):
        verify_dictionary_bytes(
            bad,
            expected_sha256="0" * 64,
            expected_bytes=len(b"NOT A SQLITE FILE"),
        )


def test_verify_dictionary_bytes_normalizes_malformed_sqlite_with_matching_identity(
    tmp_path: Path,
) -> None:
    """SQLite errors reached after a matching identity stay in installer domain."""
    bad = tmp_path / "matching-but-malformed.sqlite"
    bad.write_bytes(b"NOT A SQLITE FILE")
    with pytest.raises(DictionaryInstallerError, match="readable SQLite"):
        verify_dictionary_bytes(
            bad,
            expected_sha256=compute_sha256(bad),
            expected_bytes=bad.stat().st_size,
        )


def test_verify_dictionary_bytes_normalizes_part_a_validation_failure(
    tmp_path: Path,
) -> None:
    """A real, valid SQLite file whose PART-A schema is invalid must still
    surface as DictionaryInstallerError, not the dictionary-domain
    DictionaryAssetError raised internally by validate_candidate_dictionary.

    This exercises the full path: identity passes, the file opens as
    SQLite, PRAGMA quick_check succeeds, and only then does PART-A schema
    validation fail (required tables absent).
    """
    candidate = tmp_path / "no-part-a.sqlite"
    conn = sqlite3.connect(str(candidate))
    try:
        # A real, valid SQLite database that passes quick_check but has
        # none of the required PART-A tables (lemma, sense, ...).
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    size = candidate.stat().st_size
    sha = compute_sha256(candidate)

    with pytest.raises(DictionaryInstallerError, match="PART-A validation failed") as excinfo:
        verify_dictionary_bytes(candidate, expected_sha256=sha, expected_bytes=size)

    from app.dictionary import DictionaryAssetError

    # The public exception must be the installer-domain type, not a raw
    # dictionary-domain or sqlite3 exception escaping unnormalized.
    assert not isinstance(excinfo.value, DictionaryAssetError)
    assert not isinstance(excinfo.value, sqlite3.Error)
    assert isinstance(excinfo.value.__cause__, DictionaryAssetError)


def test_verify_dictionary_identity_ok(synthetic_dict: Path) -> None:
    digest = verify_dictionary_identity(
        synthetic_dict,
        expected_sha256=compute_sha256(synthetic_dict),
        expected_bytes=synthetic_dict.stat().st_size,
    )
    assert digest == compute_sha256(synthetic_dict)


def test_verify_dictionary_identity_size_mismatch_no_sqlite_open(
    monkeypatch: pytest.MonkeyPatch, synthetic_dict: Path
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise AssertionError("verify_dictionary_identity must not open SQLite")

    monkeypatch.setattr("app.dict_install._open_readonly_sqlite", _boom)
    with pytest.raises(DictionaryInstallerError, match="size mismatch"):
        verify_dictionary_identity(
            synthetic_dict,
            expected_sha256=compute_sha256(synthetic_dict),
            expected_bytes=synthetic_dict.stat().st_size + 1,
        )


def test_verify_dictionary_identity_sha_mismatch_no_sqlite_open(
    monkeypatch: pytest.MonkeyPatch, synthetic_dict: Path
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise AssertionError("verify_dictionary_identity must not open SQLite")

    monkeypatch.setattr("app.dict_install._open_readonly_sqlite", _boom)
    with pytest.raises(DictionaryInstallerError, match="SHA-256 mismatch"):
        verify_dictionary_identity(
            synthetic_dict,
            expected_sha256="0" * 64,
            expected_bytes=synthetic_dict.stat().st_size,
        )


def test_verify_dictionary_identity_never_invokes_quick_check_or_validator(
    monkeypatch: pytest.MonkeyPatch, synthetic_dict: Path
) -> None:
    """The identity helper must succeed on a matching candidate without ever
    opening the file as SQLite or calling the full PART-A candidate
    validator — those remain ``verify_dictionary_bytes`` / ``DictionaryRuntime``
    responsibilities.
    """

    def _sqlite_boom(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise AssertionError("verify_dictionary_identity must not open SQLite")

    def _validator_boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verify_dictionary_identity must not run PART-A validation")

    monkeypatch.setattr("app.dict_install._open_readonly_sqlite", _sqlite_boom)
    monkeypatch.setattr("app.dictionary.validate_candidate_dictionary", _validator_boom)

    digest = verify_dictionary_identity(
        synthetic_dict,
        expected_sha256=compute_sha256(synthetic_dict),
        expected_bytes=synthetic_dict.stat().st_size,
    )
    assert len(digest) == 64


def test_verify_dictionary_bytes_still_runs_full_validator(
    monkeypatch: pytest.MonkeyPatch, synthetic_dict: Path
) -> None:
    """Refactoring ``verify_dictionary_bytes`` to reuse the identity helper
    must not drop its quick_check / full PART-A validation — it must still
    perform identity, quick_check, AND the full candidate validation.
    """
    import app.dictionary as dictionary_module

    real_validate = dictionary_module.validate_candidate_dictionary
    calls: list[Path] = []

    def _counting(path: Path) -> object:
        calls.append(Path(path))
        return real_validate(path)

    monkeypatch.setattr(dictionary_module, "validate_candidate_dictionary", _counting)

    digest = verify_dictionary_bytes(
        synthetic_dict,
        expected_sha256=compute_sha256(synthetic_dict),
        expected_bytes=synthetic_dict.stat().st_size,
    )
    assert len(digest) == 64
    assert calls == [synthetic_dict]


def test_install_dictionary_reuses_existing_valid(
    synthetic_dict: Path, manifest: DictionaryManifest
) -> None:
    target = synthetic_dict.parent / "dest"
    target.mkdir()
    # Pre-place a verified file
    placed = target / manifest.filename
    placed.write_bytes(synthetic_dict.read_bytes())
    result = install_dictionary(manifest, target_dir=target)
    assert result == placed
    # Must not have moved/recreated the file
    assert placed.read_bytes() == synthetic_dict.read_bytes()


def test_install_dictionary_rejects_existing_invalid(
    synthetic_dict: Path, manifest: DictionaryManifest, tmp_path: Path
) -> None:
    target = tmp_path / "dest"
    target.mkdir()
    placed = target / manifest.filename
    # Existing file is broken and does not match the manifest
    placed.write_bytes(b"corrupt")
    with pytest.raises(DictionaryInstallerError, match="existing dictionary"):
        install_dictionary(manifest, target_dir=target)


def test_install_dictionary_fails_closed_without_url(
    synthetic_dict: Path, manifest: DictionaryManifest, tmp_path: Path
) -> None:
    target = tmp_path / "dest"
    target.mkdir()
    with pytest.raises(DictionaryInstallerError, match="no verified dictionary"):
        install_dictionary(manifest, target_dir=target)


def test_install_dictionary_atomic_download(
    synthetic_dict: Path, manifest_payload: dict[str, object], tmp_path: Path
) -> None:
    """Drive the install path via a local HTTP server."""


    payload = dict(manifest_payload)
    target = tmp_path / "dest"
    target.mkdir()
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    # Set up a tiny local HTTP server that serves the synthetic
    # dictionary bytes for the installer's URL.
    served_bytes = synthetic_dict.read_bytes()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(served_bytes)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(served_bytes)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    _, port = server.server_address  # type: ignore[misc]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload["download_url"] = f"http://127.0.0.1:{port}/dictionary.sqlite"
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(manifest_file)
        result = install_dictionary(
            manifest, target_dir=target, download_dir=download_dir
        )
        assert result == target / manifest.filename
        assert result.read_bytes() == served_bytes
        # No leftover temp file
        leftovers = list(download_dir.glob(".*.partial"))
        assert leftovers == []
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_install_dictionary_truncated_download_fails_closed(
    synthetic_dict: Path, manifest_payload: dict[str, object], tmp_path: Path
) -> None:
    payload = dict(manifest_payload)
    target = tmp_path / "dest"
    target.mkdir()
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    served_bytes = synthetic_dict.read_bytes()
    # Serve half the bytes; the installer must reject the truncated file.
    truncated = served_bytes[: len(served_bytes) // 2]

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(truncated)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(truncated)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    _, port = server.server_address  # type: ignore[misc]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload["download_url"] = f"http://127.0.0.1:{port}/dictionary.sqlite"
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(manifest_file)
        with pytest.raises(DictionaryInstallerError):
            install_dictionary(
                manifest, target_dir=target, download_dir=download_dir
            )
        # No partial file should remain
        leftovers = list(download_dir.glob(".*.partial"))
        assert leftovers == []
        # No dictionary should have been placed
        assert not (target / manifest.filename).exists()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _manifest_dict_path(filename: str, _unused: bytes) -> dict[str, object]:
    """A manifest template whose filename / size / sha can be edited."""
    return {
        "version": "v1",
        "filename": filename,
        "sha256": "00" * 32,
        "bytes": 16,
        "classification": "source-backed-stage02",
        "attribution": "ATTRIBUTION.md",
        "download_url": None,
    }
