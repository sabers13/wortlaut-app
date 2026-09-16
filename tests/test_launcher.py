"""Subprocess smoke test for the standalone launcher.

Drives the ``./wortlaut`` launcher through argparse, a missing
dictionary error, the install-dictionary path, and the custom-port
same-origin browser security path. The launcher is invoked as a real
shell script (the shebang ``./wortlaut``) for the dedicated
subprocess tests so a regression in the launcher re-exec path or its
loopback bind contract is caught end-to-end. The test never launches a
long-running server — it verifies the launcher's fail-closed error
paths and CLI plumbing so a regression in the single-command UX is
caught.

A handful of tests near the end additionally drive the ``./flashcard``
legacy compatibility alias to prove it resolves to the exact same
application behavior as ``./wortlaut`` (it re-execs into ``./wortlaut``
and duplicates no launcher logic).
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import types
import venv
from contextlib import closing
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any, cast

import pytest

from app.dict_install import compute_sha256
from app.dictionary_session import OnlineSessionInfo
from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "reference" / "schema.sql"
LAUNCHER = Path(__file__).resolve().parents[1] / "wortlaut"
FLASHCARD_ALIAS = Path(__file__).resolve().parents[1] / "flashcard"
PYTHON = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"


def _load_launcher_module() -> types.ModuleType:
    """Load ``./wortlaut`` as an in-process module for call-count assertions.

    The launcher has no ``.py`` suffix, so the loader must be constructed
    explicitly rather than inferred from the filename. Loaded fresh per
    call; ``if __name__ == "__main__":`` never runs because the module is
    imported, not executed as ``__main__``, so no venv re-exec happens.
    """
    loader = SourceFileLoader("wortlaut_launcher_inprocess", str(LAUNCHER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _fake_uvicorn() -> types.ModuleType:
    """A stand-in ``uvicorn`` module whose ``run`` never blocks or binds a port."""
    fake = types.ModuleType("uvicorn")

    def _run(*_args: object, **_kwargs: object) -> None:
        return None

    fake.run = _run  # type: ignore[attr-defined]
    return fake


def _part_a_sql() -> str:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    part_a, marker, _ = text.partition("-- PART B")
    assert marker
    return part_a


@pytest.fixture
def synthetic_dict(tmp_path: Path) -> Path:
    db_path = tmp_path / "tiny.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_part_a_sql())
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


def _run_launcher(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
    via_shebang: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Drive the launcher.

    ``via_shebang=True`` invokes ``./wortlaut`` directly, exercising the
    real user command path and the launcher's venv re-exec logic.
    ``via_shebang=False`` invokes ``sys.executable ./wortlaut ...``
    for tests that want to bypass the shebang.
    """
    if via_shebang:
        cmd = ["./wortlaut", *args]
    else:
        cmd = [sys.executable, str(LAUNCHER), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    return proc


def _make_repo_venv(repo: Path) -> Path:
    """Create a minimal repository-local interpreter for launcher tests."""
    venv.EnvBuilder(with_pip=False).create(repo / ".venv")
    return repo / ".venv" / "bin" / "python"


def _instrument_launcher_for_reexec(launcher: Path) -> None:
    """Expose post-reexec state in a disposable launcher copy only."""
    text = launcher.read_text(encoding="utf-8")
    text = text.replace("import argparse\n", "import argparse\nimport json\n", 1)
    text = text.replace(
        "    _reexec_into_venv()\n    raise SystemExit(main())",
        "    _reexec_into_venv()\n"
        "    print('REEXEC_TEST=' + json.dumps({'prefix': sys.prefix, 'argv': sys.argv}))\n"
        "    raise SystemExit(main())",
        1,
    )
    launcher.write_text(text, encoding="utf-8")


def _post_reexec_record(stdout: str) -> dict[str, object]:
    line = next(line for line in stdout.splitlines() if line.startswith("REEXEC_TEST="))
    return cast(
        dict[str, object],
        json.loads(line.removeprefix("REEXEC_TEST=")),
    )


@pytest.mark.parametrize(
    ("use_other_venv", "marker"), [(False, False), (True, False), (True, True)]
)
def test_launcher_reexecs_only_into_repository_venv(
    tmp_path: Path, use_other_venv: bool, marker: bool
) -> None:
    """System and unrelated venv launches must enter this repository's venv.

    The instrumented disposable launcher copy records post-reexec state, making
    this a subprocess test rather than a unit test of the helper alone. It also
    proves the re-exec marker cannot make an unrelated environment acceptable
    and preserves arguments.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    launcher = repo / "wortlaut"
    launcher.write_bytes(LAUNCHER.read_bytes())
    _instrument_launcher_for_reexec(launcher)
    launcher.chmod(0o755)
    repo_python = _make_repo_venv(repo)
    system_python = Path(sys.base_prefix) / "bin" / "python3"
    assert system_python.is_file()
    command_python = str(system_python)
    if use_other_venv:
        other = tmp_path / "other"
        venv.EnvBuilder(with_pip=False).create(other)
        command_python = str(other / "bin" / "python")
    env = dict(os.environ)
    if marker:
        env["FLASHCARD_LAUNCHER_REEXEC"] = "1"
    args = ["--data-dir", str(tmp_path / "data with spaces"), "--help"]
    proc = subprocess.run(
        [command_python, str(launcher), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0
    record = _post_reexec_record(proc.stdout)
    assert Path(str(record["prefix"])).resolve() == repo_python.parent.parent.resolve()
    assert record["argv"] == [str(launcher), *args]


def test_launcher_inside_repository_venv_does_not_reexec(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    launcher = repo / "wortlaut"
    launcher.write_bytes(LAUNCHER.read_bytes())
    _instrument_launcher_for_reexec(launcher)
    launcher.chmod(0o755)
    repo_python = _make_repo_venv(repo)
    env = dict(os.environ)
    proc = subprocess.run(
        [str(repo_python), str(launcher), "--help"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0
    record = _post_reexec_record(proc.stdout)
    assert Path(str(record["prefix"])).resolve() == repo_python.parent.parent.resolve()


def test_launcher_help_prints_expected_text(tmp_path: Path) -> None:
    proc = _run_launcher("--help", cwd=tmp_path)
    assert proc.returncode == 0
    assert "Run the Wortlaut standalone web app" in proc.stdout
    assert "--data-dir" in proc.stdout
    assert "--install-dictionary" in proc.stdout
    # Host binding is intentionally non-configurable.
    assert "--host" not in proc.stdout
    # The misleading dictionary-verify bypass has been removed.
    assert "--skip-dict-verify" not in proc.stdout


def test_launcher_help_via_shebang(tmp_path: Path) -> None:
    """``./wortlaut --help`` must succeed even when invoked via the
    shebang path, exercising the launcher's venv re-exec logic.
    """
    proc = _run_launcher("--help", cwd=LAUNCHER.parent, via_shebang=True)
    assert proc.returncode == 0
    assert "Run the Wortlaut standalone web app" in proc.stdout


def test_launcher_fails_closed_without_venv(tmp_path: Path) -> None:
    """When the repository-local venv is missing, the launcher must
    fail closed with a clear message naming the exact setup command
    (Repair A). The message must mention ``python3 -m venv .venv`` and
    ``.venv/bin/pip install -e .``.
    """
    # Invoke via /usr/bin/env python3 with PYTHONPATH unset so the
    # script's venv re-exec sees no .venv.
    fake_no_venv = tmp_path / "no_venv_repo"
    fake_no_venv.mkdir()
    # Copy the launcher script to the empty directory.
    target_launcher = fake_no_venv / "wortlaut"
    target_launcher.write_bytes(LAUNCHER.read_bytes())
    target_launcher.chmod(0o755)
    # Confirm there is no venv next to it.
    assert not (fake_no_venv / ".venv").exists()

    proc = subprocess.run(
        [sys.executable, str(target_launcher), "--help"],
        cwd=str(fake_no_venv),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "repository virtualenv is missing" in proc.stderr
    assert "python3 -m venv .venv" in proc.stderr
    assert ".venv/bin/pip install -e ." in proc.stderr


def test_launcher_no_host_flag_executable(tmp_path: Path) -> None:
    """The ``--host`` flag must be rejected: there is no supported CLI
    path to a non-loopback bind address.
    """
    proc = _run_launcher(
        "--host", "0.0.0.0",
        "--data-dir", str(tmp_path / "data"),
        "--no-browser",
        cwd=tmp_path,
        timeout=10,
    )
    assert proc.returncode != 0
    # argparse rejects unknown options.
    assert "unrecognized arguments" in proc.stderr or "unrecognized argument" in proc.stderr


def test_launcher_rejects_lan_host(tmp_path: Path) -> None:
    proc = _run_launcher(
        "--host", "192.168.1.10",
        "--data-dir", str(tmp_path / "data"),
        "--no-browser",
        cwd=tmp_path,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "unrecognized argument" in proc.stderr


def test_launcher_chooser_state_when_dictionary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slice 12: missing dictionary in default startup reaches the
    unconfigured-chooser state (no early exit, app starts in chooser).
    The legacy ``missing dictionary -> launcher exits`` behavior is
    intentionally superseded by ADR-0009 §6.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    module = _load_launcher_module()
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    rc = module.main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--no-browser",
        ],
    )
    assert rc == 0


def test_launcher_fails_closed_on_dictionary_verify_error(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """Slice 12: explicit ``--dict-path`` still fails closed on a
    non-dictionary file; only the *default* (no-mode, no-path) startup
    is allowed to fall into the chooser state.
    """
    fake = synthetic_dict.parent / "dictionary.sqlite"
    fake.write_bytes(b"NOT A SQLITE FILE")
    env = {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg")}
    proc = _run_launcher(
        "--data-dir",
        str(tmp_path / "data"),
        "--dict-path",
        str(fake),
        "--no-browser",
        cwd=tmp_path,
        env=env,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "verification failed" in proc.stderr or "PART-A" in proc.stderr


def test_launcher_chooser_state_when_default_offline_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slice 12: default + canonical offline MISSING falls into the
    unconfigured chooser. The chooser stays in-process until the user
    chooses; tests run in-process with a fake uvicorn.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    module = _load_launcher_module()
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    rc = module.main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--no-browser",
        ],
    )
    assert rc == 0


def _write_manifest(
    path: Path,
    *,
    filename: str,
    dictionary: Path,
    sha256: str | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "test",
                "filename": filename,
                "sha256": sha256 or compute_sha256(dictionary),
                "bytes": dictionary.stat().st_size,
                "classification": "test",
                "attribution": "ATTRIBUTION-test.md",
                "download_url": None,
            }
        ),
        encoding="utf-8",
    )


def test_canonical_dictionary_wrong_bytes_fails_before_user_db(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    data_dir = tmp_path / "data"
    canonical = data_dir / "dictionary" / "dictionary.sqlite"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"wrong bytes")
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, filename="dictionary.sqlite", dictionary=synthetic_dict)
    proc = _run_launcher(
        "--data-dir",
        str(data_dir),
        "--manifest",
        str(manifest_path),
        "--no-browser",
        cwd=tmp_path,
    )
    assert proc.returncode == 2
    assert "size mismatch" in proc.stderr
    assert not (data_dir / "flashcards.sqlite").exists()


def test_canonical_dictionary_wrong_sha_fails_before_user_db(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    data_dir = tmp_path / "data"
    canonical = data_dir / "dictionary" / "dictionary.sqlite"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(synthetic_dict.read_bytes())
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path,
        filename="dictionary.sqlite",
        dictionary=synthetic_dict,
        sha256="0" * 64,
    )
    proc = _run_launcher(
        "--data-dir",
        str(data_dir),
        "--manifest",
        str(manifest_path),
        "--no-browser",
        cwd=tmp_path,
    )
    assert proc.returncode == 2
    assert "SHA-256 mismatch" in proc.stderr
    assert not (data_dir / "flashcards.sqlite").exists()


def test_canonical_dictionary_manifest_filename_mismatch_fails(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    data_dir = tmp_path / "data"
    canonical = data_dir / "dictionary" / "dictionary.sqlite"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(synthetic_dict.read_bytes())
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, filename="other.sqlite", dictionary=synthetic_dict)
    proc = _run_launcher(
        "--data-dir",
        str(data_dir),
        "--manifest",
        str(manifest_path),
        "--no-browser",
        cwd=tmp_path,
    )
    assert proc.returncode == 2
    assert "filename does not match the canonical path" in proc.stderr
    assert not (data_dir / "flashcards.sqlite").exists()


def test_ordinary_canonical_startup_uses_identity_not_full_installer_verification(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordinary canonical startup must call the lightweight identity helper
    exactly once, and must NEVER call the installer's full
    ``verify_dictionary_bytes`` (Repair 4: avoid duplicate validation).
    """
    module = _load_launcher_module()
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())

    data_dir = tmp_path / "data"
    canonical = data_dir / "dictionary" / "dictionary.sqlite"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(synthetic_dict.read_bytes())
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, filename="dictionary.sqlite", dictionary=synthetic_dict)

    import app.dict_install as dict_install_module

    real_identity = dict_install_module.verify_dictionary_identity
    identity_calls: list[Path] = []

    def _counting_identity(
        path: Path | str, *, expected_sha256: str, expected_bytes: int
    ) -> str:
        identity_calls.append(Path(path))
        return real_identity(
            path, expected_sha256=expected_sha256, expected_bytes=expected_bytes
        )

    def _bytes_boom(path: Path | str, *, expected_sha256: str, expected_bytes: int) -> str:
        raise AssertionError(
            "ordinary canonical startup must not call verify_dictionary_bytes"
        )

    monkeypatch.setattr(dict_install_module, "verify_dictionary_identity", _counting_identity)
    monkeypatch.setattr(dict_install_module, "verify_dictionary_bytes", _bytes_boom)

    rc = module.main(
        [
            "--data-dir",
            str(data_dir),
            "--manifest",
            str(manifest_path),
            "--no-browser",
        ]
    )
    assert rc == 0
    assert len(identity_calls) == 1


def test_same_process_successful_install_skips_redundant_identity_recheck(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-process successful ``--install-dictionary`` must not repeat a
    manifest SHA verification of the exact same file right afterward: the
    installer already ran full identity + quick_check + PART-A validation.
    """
    module = _load_launcher_module()
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())

    data_dir = tmp_path / "data"
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, filename="dictionary.sqlite", dictionary=synthetic_dict)

    # Pre-place an already-verified dictionary at the canonical install slot
    # so install_dictionary() takes its "existing file verifies" branch
    # (still one full verify_dictionary_bytes pass) without needing a
    # download server.
    canonical_dir = data_dir / "dictionary"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "dictionary.sqlite").write_bytes(synthetic_dict.read_bytes())

    import app.dict_install as dict_install_module

    real_identity = dict_install_module.verify_dictionary_identity
    identity_calls: list[Path] = []

    def _counting_identity(
        path: Path | str, *, expected_sha256: str, expected_bytes: int
    ) -> str:
        identity_calls.append(Path(path))
        return real_identity(
            path, expected_sha256=expected_sha256, expected_bytes=expected_bytes
        )

    monkeypatch.setattr(dict_install_module, "verify_dictionary_identity", _counting_identity)

    def _canonical_boom(*, dictionary_path: Path, manifest: object) -> None:
        raise AssertionError(
            "same-process successful install must not re-run canonical "
            "identity verification"
        )

    monkeypatch.setattr(module, "_verify_canonical_dictionary", _canonical_boom)

    rc = module.main(
        [
            "--data-dir",
            str(data_dir),
            "--manifest",
            str(manifest_path),
            "--install-dictionary",
            "--no-browser",
        ]
    )
    assert rc == 0
    # Exactly one identity pass total: the installer's own, inside
    # verify_dictionary_bytes(). No redundant post-install re-check.
    assert len(identity_calls) == 1


def test_runtime_validation_reached_after_valid_canonical_identity(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a valid canonical identity precheck, DictionaryRuntime must
    still perform its own full integrity/schema validation exactly once.
    """
    module = _load_launcher_module()
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())

    data_dir = tmp_path / "data"
    canonical = data_dir / "dictionary" / "dictionary.sqlite"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(synthetic_dict.read_bytes())
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, filename="dictionary.sqlite", dictionary=synthetic_dict)

    import app.deck as deck_module

    real_validate = deck_module.validate_candidate_dictionary  # type: ignore[attr-defined]
    runtime_calls: list[Path] = []

    def _counting_validate(path: Path) -> object:
        runtime_calls.append(Path(path))
        return real_validate(path)

    monkeypatch.setattr(deck_module, "validate_candidate_dictionary", _counting_validate)

    rc = module.main(
        [
            "--data-dir",
            str(data_dir),
            "--manifest",
            str(manifest_path),
            "--no-browser",
        ]
    )
    assert rc == 0
    assert len(runtime_calls) == 1


def test_canonical_runtime_rejects_replacement_after_identity_precheck(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slice 12: ``--manifest CUSTOM`` is Developer/Recovery. Identity
    mismatch on canonical startup remains a fail-closed exit 2 — the
    custom-manifest integrity contract is preserved.

    The missing-default-dictionary fallback to the chooser does NOT
    apply here because the user explicitly supplied a custom manifest.
    That path proves the unique stale-metadata bypass protection remains
    binding.
    """
    module = _load_launcher_module()
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    data_dir = tmp_path / "data"
    canonical = data_dir / "dictionary" / "dictionary.sqlite"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(synthetic_dict.read_bytes())
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, filename="dictionary.sqlite", dictionary=synthetic_dict)

    replacement = tmp_path / "replacement.sqlite"
    replacement.write_bytes(synthetic_dict.read_bytes() + b"replaced")
    import app.dict_install as dict_install_module

    real_identity = dict_install_module.verify_dictionary_identity

    def replace_after_precheck(
        path: Path | str, *, expected_sha256: str, expected_bytes: int
    ) -> str:
        digest = real_identity(
            path, expected_sha256=expected_sha256, expected_bytes=expected_bytes
        )
        replacement.replace(Path(path))
        return digest

    monkeypatch.setattr(dict_install_module, "verify_dictionary_identity", replace_after_precheck)
    rc = module.main(
        ["--data-dir", str(data_dir), "--manifest", str(manifest_path), "--no-browser"]
    )
    assert rc == 2


def test_explicit_dict_path_skips_manifest_but_still_runtime_validated(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit ``--dict-path`` must not trigger manifest-identity
    verification, but DictionaryRuntime must still validate it.
    """
    module = _load_launcher_module()
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())

    data_dir = tmp_path / "data"

    import app.deck as deck_module
    import app.dict_install as dict_install_module

    identity_calls: list[Path] = []

    def _identity_boom(path: Path | str, *, expected_sha256: str, expected_bytes: int) -> str:
        identity_calls.append(Path(path))
        raise AssertionError("--dict-path must not trigger manifest identity verification")

    real_validate = deck_module.validate_candidate_dictionary  # type: ignore[attr-defined]
    runtime_calls: list[Path] = []

    def _counting_validate(path: Path) -> object:
        runtime_calls.append(Path(path))
        return real_validate(path)

    monkeypatch.setattr(dict_install_module, "verify_dictionary_identity", _identity_boom)
    monkeypatch.setattr(deck_module, "validate_candidate_dictionary", _counting_validate)

    rc = module.main(
        [
            "--data-dir",
            str(data_dir),
            "--dict-path",
            str(synthetic_dict),
            "--no-browser",
        ]
    )
    assert rc == 0
    assert identity_calls == []
    assert len(runtime_calls) == 1


def test_launcher_install_dictionary_lands_at_canonical_filename(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """Integration: ``--install-dictionary`` over a local HTTP URL places
    the verified dictionary at the canonical slot
    ``<data-dir>/dictionary/dictionary.sqlite`` so the NEXT normal
    launch can find it without ``--dict-path``.
    """
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

    proc: subprocess.Popen[str] | None = None
    try:
        manifest_payload = {
            "version": "v1",
            "filename": "dictionary.sqlite",
            "sha256": compute_sha256(synthetic_dict),
            "bytes": synthetic_dict.stat().st_size,
            "classification": "source-backed-stage02",
            "attribution": "ATTRIBUTION.md",
            "download_url": f"http://127.0.0.1:{port}/dict.sqlite",
        }
        manifest_path = tmp_path / "dictionary-manifest-v1.json"
        manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

        env = {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg")}
        # Run the install path via the real shebang so we exercise the
        # re-exec into .venv/bin/python as well. The launcher then
        # starts uvicorn and blocks; we kill the process after the
        # install lands and check the artifact on disk.
        proc = subprocess.Popen(
            [
                "./wortlaut",
                "--data-dir",
                str(tmp_path / "data"),
                "--manifest",
                str(manifest_path),
                "--install-dictionary",
                "--no-browser",
            ],
            cwd=str(LAUNCHER.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        # Wait for the canonical dictionary file to land.
        installed = tmp_path / "data" / "dictionary" / "dictionary.sqlite"
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if installed.is_file():
                break
            time.sleep(0.1)
        assert installed.is_file(), (
            "dictionary must install at the canonical filename "
            "<data-dir>/dictionary/dictionary.sqlite"
        )
        assert installed.read_bytes() == served_bytes
        # No leftover temp file under the dictionary directory.
        leftovers = list((tmp_path / "data" / "dictionary").glob(".*.partial"))
        assert leftovers == []
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        server.shutdown()
        thread.join(timeout=2)


def test_launcher_install_then_normal_launch_succeeds_without_dict_path(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """Integration end-to-end: install via ``--install-dictionary`` then
    start the launcher WITHOUT ``--dict-path``; the launcher must find
    the canonical dictionary and serve the decks API at the user-chosen
    loopback port. Proves the manifest's canonical filename and the
    launcher's default path agree.
    """
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

    install_proc: subprocess.Popen[str] | None = None
    serve_proc: subprocess.Popen[str] | None = None
    try:
        manifest_payload = {
            "version": "v1",
            "filename": "dictionary.sqlite",
            "sha256": compute_sha256(synthetic_dict),
            "bytes": synthetic_dict.stat().st_size,
            "classification": "source-backed-stage02",
            "attribution": "ATTRIBUTION.md",
            "download_url": f"http://127.0.0.1:{port}/dict.sqlite",
        }
        manifest_path = tmp_path / "dictionary-manifest-v1.json"
        manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

        env = {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg")}

        # Install path via shebang.
        install_proc = subprocess.Popen(
            [
                "./wortlaut",
                "--data-dir",
                str(tmp_path / "data"),
                "--manifest",
                str(manifest_path),
                "--install-dictionary",
                "--no-browser",
            ],
            cwd=str(LAUNCHER.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        installed = tmp_path / "data" / "dictionary" / "dictionary.sqlite"
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if installed.is_file():
                break
            time.sleep(0.1)
        assert installed.is_file()

        # Free port and start the launcher without --dict-path.
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.bind(("127.0.0.1", 0))
            serve_port = int(s.getsockname()[1])

        serve_proc = subprocess.Popen(
            [
                "./wortlaut",
                "--data-dir",
                str(tmp_path / "data"),
                "--manifest",
                str(manifest_path),
                "--port",
                str(serve_port),
                "--no-browser",
            ],
            cwd=str(LAUNCHER.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        deadline = time.monotonic() + 30.0
        ready = False
        import urllib.request

        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{serve_port}/vocab/decks",
                    headers={
                        "Host": f"127.0.0.1:{serve_port}",
                        "X-Flashcards-Request": "1",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.3)
        assert ready, "launcher did not serve decks API from default path"
    finally:
        for proc in (install_proc, serve_proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        server.shutdown()
        thread.join(timeout=2)


def test_launcher_custom_port_same_origin_succeeds_and_hostile_origin_rejected(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """Custom port: same-origin POST at the bound port is accepted
    (Repair C) while a non-loopback Origin is still rejected (R12).
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        serve_port = int(s.getsockname()[1])

    env = {"HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "xdg")}
    proc = subprocess.Popen(
        [
            sys.executable,
            str(LAUNCHER),
            "--data-dir",
            str(tmp_path / "data"),
            "--dict-path",
            str(synthetic_dict),
            "--port",
            str(serve_port),
            "--no-browser",
        ],
        cwd=str(LAUNCHER.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        # Wait for the server to come up.
        import urllib.request

        deadline = time.monotonic() + 30.0
        ready = False
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{serve_port}/vocab/decks",
                    headers={
                        "Host": f"127.0.0.1:{serve_port}",
                        "X-Flashcards-Request": "1",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.3)
        assert ready, "launcher did not become ready at custom port"

        # Same-origin POST at the custom port succeeds (Repair C).
        post_req = urllib.request.Request(
            f"http://127.0.0.1:{serve_port}/vocab/decks",
            data=b'{"name":"Custom Port Deck"}',
            method="POST",
            headers={
                "Host": f"127.0.0.1:{serve_port}",
                "Origin": f"http://127.0.0.1:{serve_port}",
                "X-Flashcards-Request": "1",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(post_req, timeout=5) as resp:
            assert resp.status == 201

        # Hostile (non-loopback) Origin is still rejected (R12).
        import urllib.error

        bad_req = urllib.request.Request(
            f"http://127.0.0.1:{serve_port}/vocab/decks",
            headers={
                "Host": f"127.0.0.1:{serve_port}",
                "Origin": "http://evil.example.com",
                "X-Flashcards-Request": "1",
                "Content-Type": "application/json",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(bad_req, timeout=5)
        assert exc_info.value.code == 403
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


# ---------------------------------------------------------------------------
# ``./flashcard`` legacy compatibility alias
#
# The alias duplicates no launcher logic: it re-execs ``./wortlaut`` with the
# same argv. These tests prove that resolution actually happens and that the
# two commands are observably the same application from a user's point of
# view, not just that both files exist.
# ---------------------------------------------------------------------------


def test_flashcard_alias_help_matches_wortlaut(tmp_path: Path) -> None:
    """``./flashcard --help`` must produce the exact same output as
    ``./wortlaut --help``: the alias re-execs the canonical launcher rather
    than maintaining a second copy of its argparse configuration.
    """
    wortlaut_proc = subprocess.run(
        ["./wortlaut", "--help"],
        cwd=str(LAUNCHER.parent),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    flashcard_proc = subprocess.run(
        ["./flashcard", "--help"],
        cwd=str(FLASHCARD_ALIAS.parent),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert wortlaut_proc.returncode == 0
    assert flashcard_proc.returncode == wortlaut_proc.returncode
    assert flashcard_proc.stdout == wortlaut_proc.stdout
    assert "Run the Wortlaut standalone web app" in flashcard_proc.stdout


def test_flashcard_alias_reexecs_wortlaut_with_same_argv(tmp_path: Path) -> None:
    """The alias must re-exec into ``./wortlaut`` (not a copy of it),
    preserving argv exactly, so a fix to the canonical launcher never needs
    a matching fix in the alias.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    wortlaut_copy = repo / "wortlaut"
    wortlaut_copy.write_bytes(LAUNCHER.read_bytes())
    _instrument_launcher_for_reexec(wortlaut_copy)
    wortlaut_copy.chmod(0o755)
    flashcard_copy = repo / "flashcard"
    flashcard_copy.write_bytes(FLASHCARD_ALIAS.read_bytes())
    flashcard_copy.chmod(0o755)
    _make_repo_venv(repo)

    args = ["--data-dir", str(tmp_path / "data"), "--help"]
    proc = subprocess.run(
        [str(flashcard_copy), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0
    record = _post_reexec_record(proc.stdout)
    # argv[0] is the wortlaut script path the alias re-exec'd into, not the
    # flashcard alias path.
    assert record["argv"] == [str(wortlaut_copy), *args]


def test_flashcard_alias_fails_closed_without_venv(tmp_path: Path) -> None:
    """The alias must surface the same fail-closed venv error as the
    canonical launcher, since it is the same process after re-exec.
    """
    fake_no_venv = tmp_path / "no_venv_repo"
    fake_no_venv.mkdir()
    wortlaut_copy = fake_no_venv / "wortlaut"
    wortlaut_copy.write_bytes(LAUNCHER.read_bytes())
    wortlaut_copy.chmod(0o755)
    flashcard_copy = fake_no_venv / "flashcard"
    flashcard_copy.write_bytes(FLASHCARD_ALIAS.read_bytes())
    flashcard_copy.chmod(0o755)
    assert not (fake_no_venv / ".venv").exists()

    proc = subprocess.run(
        [sys.executable, str(flashcard_copy), "--help"],
        cwd=str(fake_no_venv),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "repository virtualenv is missing" in proc.stderr


def test_cli_online_with_custom_manifest_exits_2(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slice 12 / ADR-0009: ``--dictionary-mode online --manifest CUSTOM``
    is a contradictory combination. The launcher must exit 2 BEFORE
    network, provider activation, Offline install, or PART-B mutation.
    """
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    module = _load_launcher_module()
    data_dir = tmp_path / "data"
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, filename="dictionary.sqlite", dictionary=synthetic_dict)
    with pytest.raises(SystemExit) as exc_info:
        module.main([
            "--data-dir",
            str(data_dir),
            "--dictionary-mode",
            "online",
            "--manifest",
            str(manifest_path),
            "--no-browser",
        ])
    assert exc_info.value.code == 2


def test_cli_online_with_dict_path_exits_2(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slice 12 / ADR-0009: ``--dictionary-mode online --dict-path PATH``
    is a contradictory combination; launcher exits 2 BEFORE any
    network or provider action.
    """
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    module = _load_launcher_module()
    data_dir = tmp_path / "data"
    with pytest.raises(SystemExit) as exc_info:
        module.main([
            "--data-dir",
            str(data_dir),
            "--dictionary-mode",
            "online",
            "--dict-path",
            str(synthetic_dict),
            "--no-browser",
        ])
    assert exc_info.value.code == 2


def test_cli_online_with_install_dictionary_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slice 12 / ADR-0009: ``--dictionary-mode online --install-dictionary``
    is a contradictory combination; launcher exits 2 BEFORE any
    network or provider action.
    """
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    module = _load_launcher_module()
    data_dir = tmp_path / "data"
    with pytest.raises(SystemExit) as exc_info:
        module.main([
            "--data-dir",
            str(data_dir),
            "--dictionary-mode",
            "online",
            "--install-dictionary",
            "--no-browser",
        ])
    assert exc_info.value.code == 2


def test_cli_offline_with_missing_canonical_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit ``--dictionary-mode offline`` with no canonical asset
    fails closed (it never falls back to Online).
    """
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    module = _load_launcher_module()
    data_dir = tmp_path / "data"
    with pytest.raises(SystemExit) as exc_info:
        module.main([
            "--data-dir",
            str(data_dir),
            "--dictionary-mode",
            "offline",
            "--no-browser",
        ])
    assert exc_info.value.code in (1, 2)


def test_launcher_help_exposes_no_online_manifest_or_source_selection(
    tmp_path: Path,
) -> None:
    """Slice 12 C3: Product Online loads ONLY the committed trusted manifest.
    ``--help`` must expose no custom Online manifest/source selection.
    """
    proc = _run_launcher("--help", cwd=tmp_path)
    assert proc.returncode == 0
    assert "--online-manifest" not in proc.stdout
    assert "--online-cache-dir" not in proc.stdout
    assert "--online-manifest" not in proc.stderr
    assert "--online-cache-dir" not in proc.stderr
    # Custom --manifest remains the Offline Developer/Recovery feature.
    assert "--manifest" in proc.stdout
    assert "--dictionary-mode" in proc.stdout


def test_launcher_rejects_online_manifest_flag(tmp_path: Path) -> None:
    """Slice 12 C3: ``--online-manifest`` is not a Product flag."""
    proc = _run_launcher(
        "--online-manifest",
        "whatever.json",
        "--data-dir",
        str(tmp_path / "data"),
        "--no-browser",
        cwd=tmp_path,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "unrecognized arguments" in proc.stderr or "unrecognized argument" in proc.stderr


def test_launcher_rejects_online_cache_dir_flag(tmp_path: Path) -> None:
    """Slice 12 C3: ``--online-cache-dir`` is not a Product flag."""
    proc = _run_launcher(
        "--online-cache-dir",
        str(tmp_path / "cache"),
        "--data-dir",
        str(tmp_path / "data"),
        "--no-browser",
        cwd=tmp_path,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "unrecognized arguments" in proc.stderr or "unrecognized argument" in proc.stderr


# ---------------------------------------------------------------------------
# Post-publication explicit-Online startup repair
# ---------------------------------------------------------------------------


class _FakeOnlineProvider:
    """Minimal Online provider stand-in for launcher startup tests."""

    asset_token = "fake-online-asset-token"

    def close(self) -> None:
        return None


def _recording_uvicorn() -> tuple[types.ModuleType, list[object]]:
    """A fake ``uvicorn`` module recording the app passed to ``run``."""
    fake = types.ModuleType("uvicorn")
    captured: list[object] = []

    def _run(*args: object, **_kwargs: object) -> None:
        captured.append(args[0] if args else None)

    fake.run = _run  # type: ignore[attr-defined]
    return fake, captured


def _patch_lazy_online_factory(
    monkeypatch: pytest.MonkeyPatch,
    module: types.ModuleType,
    *,
    provider: object,
    info: OnlineSessionInfo,
    factory_calls: list[int],
) -> None:
    """Replace the launcher's Online-factory builder with a counting fake.

    The real ``_build_online_factory`` closes over the committed trusted
    Online manifest and the Product transport. The patched builder returns
    a factory that records each invocation and hands back the given
    provider/info pair, so launcher tests never touch the network.
    """

    def _builder(**_kwargs: object) -> object:
        def _factory() -> tuple[object, OnlineSessionInfo]:
            factory_calls.append(1)
            return provider, info

        return _factory

    monkeypatch.setattr(module, "_build_online_factory", _builder)


def _explicit_online_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    data_dir: Path,
    port: int,
) -> tuple[types.ModuleType, list[object], list[int], OnlineSessionInfo]:
    """Run explicit ``--dictionary-mode online`` startup in-process.

    Patches the canonical-identity verification and the Offline installer
    to explode, swaps in the counting lazy Online factory, and runs
    ``main`` under the recording fake uvicorn. Returns the launcher
    module, captured apps, factory invocation log, and session info.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    module = _load_launcher_module()
    uvicorn_fake, captured = _recording_uvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn_fake)

    def _verify_boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "_verify_canonical_dictionary must not run for explicit online startup"
        )

    monkeypatch.setattr(module, "_verify_canonical_dictionary", _verify_boom)

    def _install_boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Offline install must not run for explicit online startup")

    import app.dict_install as dict_install_module

    monkeypatch.setattr(dict_install_module, "install_dictionary", _install_boom)

    provider = _FakeOnlineProvider()
    info = OnlineSessionInfo(
        dataset_token="dataset-repair-test",
        asset_token="fake-online-asset-token",
        cache_dir=str(data_dir / "online-cache"),
    )
    factory_calls: list[int] = []
    _patch_lazy_online_factory(
        monkeypatch, module, provider=provider, info=info, factory_calls=factory_calls
    )

    rc = module.main(
        [
            "--data-dir",
            str(data_dir),
            "--dictionary-mode",
            "online",
            "--port",
            str(port),
            "--no-browser",
        ],
    )
    assert rc == 0
    return module, captured, factory_calls, info


def test_explicit_online_starts_without_canonical_offline_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit ``--dictionary-mode online`` with a fresh data dir and NO
    canonical Offline dictionary starts successfully: the canonical
    identity check is never called, no Offline install/copy happens, and
    the full Offline asset is never created.
    """
    data_dir = tmp_path / "data"
    canonical = data_dir / "dictionary" / "dictionary.sqlite"
    assert not canonical.exists()

    _explicit_online_main(tmp_path, monkeypatch, data_dir=data_dir, port=8091)

    assert not canonical.exists()
    # The Online factory was invoked exactly once (provider constructed).
    # No Offline asset materialized in any form.
    assert not canonical.exists()
    assert not list((data_dir / "dictionary").glob("*"))


def test_explicit_online_binds_provider_and_session_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit Online startup invokes the Online factory exactly once,
    the actual provider + OnlineSessionInfo reach ``create_app``, the
    runtime mode is ``online`` (not ``unconfigured``) before any
    ``/use-online`` call, the factory is retained for session switching,
    and no dictionary-mode preference file is persisted.
    """
    data_dir = tmp_path / "data"
    _, captured, factory_calls, info = _explicit_online_main(
        tmp_path, monkeypatch, data_dir=data_dir, port=8092
    )

    assert factory_calls == [1]
    app = cast(Any, captured[0])
    assert app.state.dictionary_mode == "online"
    assert app.state.online_provider is not None
    assert app.state.online_session_info is info
    assert app.state.online_session_info.dataset_token == "dataset-repair-test"
    assert app.state.session is not None
    assert app.state.session.is_online
    # Factory retained on app.state for normal session switching.
    assert app.state.online_provider_factory is not None
    # Session-only mode semantics: no persisted mode/preference file.
    persisted = [p.name for p in data_dir.rglob("*") if p.is_file()]
    assert all(name == "flashcards.sqlite" for name in persisted)


def test_default_chooser_startup_activates_no_online_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default startup (no explicit mode, no valid canonical Offline) keeps
    mode ``unconfigured`` and constructs/activates NO Online provider at
    startup: the factory is bound but never invoked.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    module = _load_launcher_module()
    uvicorn_fake, captured = _recording_uvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn_fake)

    provider = _FakeOnlineProvider()
    info = OnlineSessionInfo(
        dataset_token="dataset-chooser-test",
        asset_token="fake-online-asset-token",
        cache_dir=str(tmp_path / "data" / "online-cache"),
    )
    factory_calls: list[int] = []
    _patch_lazy_online_factory(
        monkeypatch, module, provider=provider, info=info, factory_calls=factory_calls
    )

    rc = module.main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--port",
            str(8093),
            "--no-browser",
        ],
    )
    assert rc == 0
    assert factory_calls == []
    app = cast(Any, captured[0])
    assert app.state.dictionary_mode == "unconfigured"
    assert app.state.online_provider is None
    assert app.state.session is None
    # Factory is bound for the later user choice but was never invoked.
    assert app.state.online_provider_factory is not None


def test_cli_online_conflicts_fail_before_online_factory_construction(
    tmp_path: Path, synthetic_dict: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three explicit-Online CLI conflicts exit 2 before the Online
    factory is even built (fail-before-side-effects).
    """
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn())
    module = _load_launcher_module()

    def _factory_builder_boom(**_kwargs: object) -> object:
        raise AssertionError(
            "online factory must not be built on a contradictory CLI row"
        )

    monkeypatch.setattr(module, "_build_online_factory", _factory_builder_boom)

    manifest_path = tmp_path / "manifest.json"
    _write_manifest(
        manifest_path, filename="dictionary.sqlite", dictionary=synthetic_dict
    )

    conflict_rows = [
        ["--manifest", str(manifest_path)],
        ["--dict-path", str(synthetic_dict)],
        ["--install-dictionary"],
    ]
    for extra in conflict_rows:
        with pytest.raises(SystemExit) as exc_info:
            module.main(
                [
                    "--data-dir",
                    str(tmp_path / "data"),
                    "--dictionary-mode",
                    "online",
                    *extra,
                    "--no-browser",
                ],
            )
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# R1 — application version surface
# ---------------------------------------------------------------------------


def test_launcher_version_prints_application_version(tmp_path: Path) -> None:
    """``./wortlaut --version`` prints exactly ``wortlaut 0.1.0``."""
    from app.version import __version__

    proc = _run_launcher("--version", cwd=tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"wortlaut {__version__}"
    assert __version__ == "0.1.0"


def test_launcher_version_via_shebang(tmp_path: Path) -> None:
    """``./wortlaut --version`` works through the real shebang path."""
    proc = _run_launcher("--version", cwd=LAUNCHER.parent, via_shebang=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "wortlaut 0.1.0"


def test_flashcard_alias_version_matches_wortlaut(tmp_path: Path) -> None:
    """``./flashcard --version`` matches ``./wortlaut --version`` exactly."""
    wortlaut_proc = subprocess.run(
        ["./wortlaut", "--version"],
        cwd=str(LAUNCHER.parent),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    flashcard_proc = subprocess.run(
        ["./flashcard", "--version"],
        cwd=str(FLASHCARD_ALIAS.parent),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert wortlaut_proc.returncode == 0
    assert flashcard_proc.returncode == wortlaut_proc.returncode
    assert flashcard_proc.stdout == wortlaut_proc.stdout
    assert flashcard_proc.stdout.strip() == "wortlaut 0.1.0"


def test_installed_console_adapter_delegates_to_launcher(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``app.cli:main`` (the installed ``wortlaut`` entry point) exposes
    the root launcher's ``--version`` surface with identical output."""
    import app.cli as cli_module

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == "wortlaut 0.1.0"
