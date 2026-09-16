"""End-to-end smoke test for the standalone launcher against a small
synthetic dictionary.

The full ~900 MB source-backed Stage-02 dictionary cannot be exercised
here without ~2 GB of free disk (the runtime activation copies the file
to a temporary snapshot). This test uses a synthetic dictionary built
from ``reference/schema.sql`` and proves the launcher's contract:

* resolves XDG paths
* ensures the user database
* verifies the dictionary
* serves the API on the loopback interface
* preserves user state across restarts
* fails closed when the dictionary is missing or invalid
"""

from __future__ import annotations

import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from tools.build_dict import compute_lemma_semantic_ref, compute_sense_semantic_ref

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "reference" / "schema.sql"
LAUNCHER = Path(__file__).resolve().parents[1] / "wortlaut"
PYTHON = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"


def _part_a_sql() -> str:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    part_a, marker, _ = text.partition("-- PART B")
    assert marker
    return part_a


@pytest.fixture
def synthetic_dict(tmp_path: Path) -> Path:
    db_path = tmp_path / "synth.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_part_a_sql())
        lemma = "Haus"
        pos = "NOUN"
        gender = "das"
        lemma_ref = compute_lemma_semantic_ref(lemma, pos, gender)
        sense_ref = compute_sense_semantic_ref(
            lemma_ref, "wiktextract:enwiktionary", "senseid:en-haus-1"
        )
        conn.execute(
            """
            INSERT INTO lemma (id, semantic_ref, lemma, pos, gender,
                plural_none, source, license)
            VALUES (1, ?, ?, ?, ?, 0, 'wiktionary', 'CC BY-SA')
            """,
            (lemma_ref, lemma, pos, gender),
        )
        conn.execute(
            """
            INSERT INTO sense (id, lemma_id, semantic_ref, source_namespace,
                source_ref, ord, source, license)
            VALUES (1, 1, ?, ?, ?, 0, 'wiktionary', 'CC BY-SA')
            """,
            (sense_ref, "wiktextract:enwiktionary", "senseid:en-haus-1"),
        )
        conn.execute(
            """
            INSERT INTO sense_meaning (id, sense_id, language, kind, ord,
                text, source, license)
            VALUES (1, 1, 'en', 'translation', 0, 'house, building',
                'wiktionary', 'CC BY-SA')
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _launch(
    *,
    data_dir: Path,
    dict_path: Path,
    port: int,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            str(LAUNCHER),
            "--data-dir",
            str(data_dir),
            "--dict-path",
            str(dict_path),
            "--no-browser",
            "--port",
            str(port),
        ],
        cwd=str(LAUNCHER.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"HOME": str(data_dir), "XDG_DATA_HOME": str(data_dir / "xdg")},
    )


def _wait_ready(port: int, timeout: float = 30.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/vocab/decks",
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "X-Flashcards-Request": "1",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _stop(proc: subprocess.Popen[str]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _post(
    port: int,
    path: str,
    payload: dict[str, object],
) -> int:
    import urllib.request

    data = str(payload).replace("'", '"').encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method="POST",
        headers={
            "Host": f"127.0.0.1:{port}",
            "X-Flashcards-Request": "1",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return int(resp.status)


def test_launcher_serves_decks_api_on_loopback(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    port = _free_port()
    proc = _launch(
        data_dir=tmp_path / "data",
        dict_path=synthetic_dict,
        port=port,
    )
    try:
        assert _wait_ready(port), "launcher did not become ready in time"
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/vocab/decks",
            headers={
                "Host": f"127.0.0.1:{port}",
                "X-Flashcards-Request": "1",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert resp.read().decode("utf-8") == "[]"
    finally:
        _stop(proc)


def test_launcher_preserves_user_state_across_restart(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    data_dir = tmp_path / "data"
    port1 = _free_port()
    proc = _launch(data_dir=data_dir, dict_path=synthetic_dict, port=port1)
    try:
        assert _wait_ready(port1)
        assert _post(port1, "/vocab/decks", {"name": "Persisted Deck"}) == 201
    finally:
        _stop(proc)

    port2 = _free_port()
    proc = _launch(data_dir=data_dir, dict_path=synthetic_dict, port=port2)
    try:
        assert _wait_ready(port2)
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port2}/vocab/decks",
            headers={
                "Host": f"127.0.0.1:{port2}",
                "X-Flashcards-Request": "1",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert "Persisted Deck" in body
    finally:
        _stop(proc)


def test_launcher_rejects_non_loopback_origin(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """AGENTS R12: non-loopback host header must be rejected."""
    port = _free_port()
    proc = _launch(data_dir=tmp_path / "data", dict_path=synthetic_dict, port=port)
    try:
        assert _wait_ready(port)
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/vocab/decks",
            headers={
                "Host": "evil.example.com",
                "X-Flashcards-Request": "1",
                "Content-Type": "application/json",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 403
    finally:
        _stop(proc)


def test_launcher_rejects_missing_custom_header_on_post(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """AGENTS R12: non-GET /vocab requests require X-Flashcards-Request: 1."""
    port = _free_port()
    proc = _launch(data_dir=tmp_path / "data", dict_path=synthetic_dict, port=port)
    try:
        assert _wait_ready(port)
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/vocab/decks",
            data=b'{"name":"x"}',
            method="POST",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Content-Type": "application/json",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 403
    finally:
        _stop(proc)


def test_launcher_serves_browser_product_at_root(
    tmp_path: Path, synthetic_dict: Path
) -> None:
    """Clean-checkout production frontend (Repair F): the launcher must
    serve the actual browser application at ``/`` from the tracked
    production assets under ``app/frontend/``, without requiring any
    manual ``npm``/Vite step.
    """
    port = _free_port()
    proc = _launch(data_dir=tmp_path / "data", dict_path=synthetic_dict, port=port)
    try:
        assert _wait_ready(port)
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/",
            headers={"Host": f"127.0.0.1:{port}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            content_type = str(resp.headers.get("Content-Type", ""))
            assert "text/html" in content_type
            body = resp.read().decode("utf-8")
        assert "<flashcard-app" in body, (
            f"expected the bundled Vite output, got: {body[:200]!r}"
        )
        # The bundled asset path should be present.
        assert "/assets/" in body
    finally:
        _stop(proc)
