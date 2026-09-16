"""Tests for tools/affected_tests.py resolver."""

# ruff: noqa: E501

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tools.affected_tests import main as affected_main

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_affected(
    capsys: pytest.CaptureFixture[str],
    paths: list[str],
    repo_root: Path = REPO_ROOT,
) -> tuple[int, str, str]:
    args = ["--repo-root", str(repo_root)] + paths
    code = affected_main(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _init_git_repo(tmp_path: Path, *, commit: bool = False) -> None:
    subprocess.run(
        ["git", "init", "--initial-branch=main", "-q", str(tmp_path)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "T"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "commit.gpgsign", "false"],
        check=True,
    )
    if commit:
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "-A"], check=True
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
            check=True,
        )


def _git_add_commit(tmp_path: Path, *paths: str) -> None:
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--", *paths],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "add"],
        check=True,
    )


def _synthetic_base(tmp_path: Path) -> None:
    """Create a minimal valid git repo with all inventory roots populated."""
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reference").mkdir(parents=True, exist_ok=True)
    (tmp_path / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "dummy.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "tools" / "dummy.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "reference" / "schema.sql").write_text(
        "select 1;\n", encoding="utf-8"
    )
    (tmp_path / "reference" / "smoke_test.py").write_text(
        "x=1\n", encoding="utf-8"
    )
    (tmp_path / "frontend" / "src" / "app.ts").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "tools" / "check_modules.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "affected_tests.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_dummy.py").write_text(
        "def test_dummy(): pass\n", encoding="utf-8"
    )
    _init_git_repo(tmp_path, commit=False)
    _git_add_commit(
        tmp_path,
        "app/dummy.py",
        "tools/dummy.py",
        "tools/check_modules.py",
        "tools/affected_tests.py",
        "reference/schema.sql",
        "reference/smoke_test.py",
        "frontend/src/app.ts",
        "Dockerfile",
        "tests/test_dummy.py",
    )


# ---------------------------------------------------------------------------
# Closure / focus tests against the real repo
# ---------------------------------------------------------------------------


def test_audio_source_focused_with_reverse_closure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """audio.py has a reverse dependent (runtime_api), so closure fires."""
    code, out, _ = _run_affected(capsys, ["app/audio.py"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=audio,runtime_api" in out
    assert "test_audio.py" in out


def test_audio_source_plus_test_focused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mixed source + known test stays FOCUSED."""
    code, out, _ = _run_affected(
        capsys, ["app/audio.py", "tests/test_audio.py"]
    )
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=audio,runtime_api" in out


def test_audio_test_only_focused_no_closure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test-only change picks the module the test directly tests,
    and does NOT trigger reverse closure."""
    code, out, _ = _run_affected(capsys, ["tests/test_audio.py"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    for line in out.splitlines():
        if line.startswith("MODULES="):
            assert line.strip() == "MODULES=audio"
        if line.startswith("PYTEST="):
            assert line.strip() == "PYTEST=pytest -q tests/test_audio.py"
    assert "runtime_api" not in out


def test_resolve_source_transitive_reverse_closure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """resolve.py is depended on by dictionary/render/deck/export/runtime_api/build_dict/gate2."""
    code, out, _ = _run_affected(capsys, ["app/resolve.py"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    for name in [
        "resolve",
        "dictionary",
        "render",
        "deck",
        "export",
        "runtime_api",
        "build_dict",
        "gate2",
    ]:
        assert name in out


def test_api_source_excludes_unrelated_build_dict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """app/api.py is owned by runtime_api; build_dict must not appear."""
    code, out, _ = _run_affected(capsys, ["app/api.py"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=runtime_api" in out
    assert "test_build_dict" not in out


def test_build_dict_source_selects_all_stage_tests(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = _run_affected(capsys, ["tools/build_dict.py"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=build_dict" in out
    for stage in ["stage01", "stage02", "stage03", "stage04", "stage05"]:
        assert stage in out


def test_frontend_api_emits_pytest_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """frontend/src/api/client.ts picks frontend_api (+ frontend_shell via
    reverse closure); no Python pytest anywhere."""
    code, out, _ = _run_affected(capsys, ["frontend/src/api/client.ts"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=frontend_api" in out
    assert "PYTEST=NONE" in out
    assert "pytest -q" not in out
    assert "tests/test_" not in out


def test_frontend_shell_includes_e2e_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """frontend/src/app.ts picks frontend_shell with the Playwright command."""
    code, out, _ = _run_affected(capsys, ["frontend/src/app.ts"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=frontend_shell" in out
    assert "PYTEST=NONE" in out
    assert "npm run --prefix frontend test:e2e" in out


def test_frontend_e2e_test_only_focused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """frontend/tests/e2e/product.spec.ts → frontend_shell + E2E command."""
    code, out, _ = _run_affected(
        capsys, ["frontend/tests/e2e/product.spec.ts"]
    )
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=frontend_shell" in out
    assert "PYTEST=NONE" in out
    assert "npm run --prefix frontend test:e2e" in out


def test_affected_tests_test_only_focused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tests/test_affected_tests.py → module_metadata, both test files."""
    code, out, _ = _run_affected(capsys, ["tests/test_affected_tests.py"])
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=module_metadata" in out
    for line in out.splitlines():
        if line.startswith("PYTEST="):
            assert line.strip() == (
                "PYTEST=pytest -q tests/test_affected_tests.py "
                "tests/test_check_modules.py"
            )


def test_mixed_source_and_python_test_keeps_resolve_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """app/audio.py + tests/test_resolve.py → exactly audio,resolve,runtime_api.

    audio is a source path (reverse closure → runtime_api). test_resolve.py
    is a test-only path; resolve must NOT acquire the reverse
    dependency neighborhood of audio (dictionary, render, deck, export,
    build_dict, gate2) just because the slice also touched a source file.
    """
    code, out, _ = _run_affected(
        capsys, ["app/audio.py", "tests/test_resolve.py"]
    )
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=audio,resolve,runtime_api" in out
    for forbidden in [
        "dictionary",
        "deck",
        "render",
        "export",
        "build_dict",
        "gate2",
    ]:
        assert forbidden not in out, (
            f"unexpected module/word '{forbidden}' in output:\n{out}"
        )


def test_frontend_test_only_classified_as_test(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """frontend/src/api/client.test.ts is BOTH a frontend_api owned_path
    AND the exact focused_tests entry of frontend_api. The exact focused
    test classification wins; the path MUST be classified as TEST, so:
    - MODULES=frontend_api (no frontend_shell)
    - PYTEST=NONE (it is not a Python test path)
    - frontend_api's own focused_commands (npm test, typecheck) present
    - frontend_shell, test:e2e, and frontend build are absent
    """
    code, out, _ = _run_affected(
        capsys, ["frontend/src/api/client.test.ts"]
    )
    assert code == 0
    assert "MODE=FOCUSED" in out
    for line in out.splitlines():
        if line.startswith("MODULES="):
            assert line.strip() == "MODULES=frontend_api", out
        if line.startswith("PYTEST="):
            assert line.strip() == "PYTEST=NONE", out
    assert "pytest -q" not in out
    assert "tests/test_" not in out
    assert "npm test --prefix frontend" in out
    assert "npm run --prefix frontend typecheck" in out
    for forbidden in [
        "frontend_shell",
        "test:e2e",
        "npm run --prefix frontend build",
    ]:
        assert forbidden not in out, (
            f"unexpected token '{forbidden}' in output:\n{out}"
        )


def test_mixed_source_and_frontend_test(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """app/audio.py + frontend/src/api/client.test.ts → exactly
    audio,frontend_api,runtime_api,standalone.

    audio is a source (closure → runtime_api → standalone). client.test.ts is a
    frontend_api focused test (direct, no closure). frontend_shell,
    test:e2e, and frontend build MUST NOT be selected solely because
    of the test path.
    """
    code, out, _ = _run_affected(
        capsys, ["app/audio.py", "frontend/src/api/client.test.ts"]
    )
    assert code == 0
    assert "MODE=FOCUSED" in out
    for line in out.splitlines():
        if line.startswith("MODULES="):
            assert line.strip() == (
                "MODULES=audio,frontend_api,runtime_api,standalone"
            ), out
    # audio + runtime_api + standalone Python focused tests are present.
    assert "tests/test_audio.py" in out
    assert "tests/test_api.py" in out
    assert "tests/test_standalone.py" in out
    # frontend_api focused_commands are present.
    assert "npm test --prefix frontend" in out
    assert "npm run --prefix frontend typecheck" in out
    # Solely-because-of-test forbidden tokens are absent.
    for forbidden in [
        "frontend_shell",
        "test:e2e",
        "npm run --prefix frontend build",
    ]:
        assert forbidden not in out, (
            f"unexpected token '{forbidden}' in output:\n{out}"
        )


def test_unknown_test_path_broad(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tests/conftest.py is not a focused_tests entry → BROAD."""
    code, out, _ = _run_affected(capsys, ["tests/conftest.py"])
    assert code == 2
    assert "MODE=BROAD" in out
    assert "PYTEST=pytest -q" in out


def test_malformed_metadata_broad(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "MODULES.toml").write_text("invalid toml [[[", encoding="utf-8")
    code, out, _ = _run_affected(
        capsys, ["app/audio.py"], repo_root=tmp_path
    )
    assert code == 2
    assert "MODE=BROAD" in out
    assert "PYTEST=pytest -q" in out


def test_globally_invalid_metadata_broad_even_for_resolvable_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Even when the requested path resolves to a real module,
    a globally invalid MODULES.toml must force BROAD."""
    _synthetic_base(tmp_path)
    (tmp_path / "app" / "audio.py").write_text("", encoding="utf-8")
    _git_add_commit(tmp_path, "app/audio.py")
    content = textwrap.dedent("""
        [modules.audio]
        id = "audio"
        owned_paths = ["app/audio.py"]
        dependencies = ["nonexistent"]
        focused_tests = ["tests/test_audio.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py", "app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    code, out, _ = _run_affected(capsys, ["app/audio.py"], repo_root=tmp_path)
    assert code == 2
    assert "MODE=BROAD" in out
    assert "unknown dependency" in out.lower()


def test_deterministic_ordering(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code1, out1, _ = _run_affected(capsys, ["app/resolve.py"])
    assert code1 == 0
    code2, out2, _ = _run_affected(capsys, ["app/resolve.py"])
    assert code2 == 0
    assert out1 == out2
    code3, out3, _ = _run_affected(
        capsys, ["app/audio.py", "app/resolve.py"]
    )
    code4, out4, _ = _run_affected(
        capsys, ["app/resolve.py", "app/audio.py"]
    )
    assert out3 == out4
    code5, out5, _ = _run_affected(
        capsys, ["frontend/src/api/client.ts", "app/audio.py"]
    )
    code6, out6, _ = _run_affected(
        capsys, ["app/audio.py", "frontend/src/api/client.ts"]
    )
    assert out5 == out6


# ---------------------------------------------------------------------------
# Synthetic / tmp_path coverage
# ---------------------------------------------------------------------------


def test_unmapped_path_gives_broad(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _synthetic_base(tmp_path)
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "a.py").write_text("", encoding="utf-8")
    _git_add_commit(tmp_path, "tests/test_a.py", "app/a.py")
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/a.py", "app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_a.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    code, out, _ = _run_affected(
        capsys, ["unmapped/random.txt"], repo_root=tmp_path
    )
    assert code == 2
    assert "MODE=BROAD" in out
    assert "REASON" in out
    assert "pytest -q" in out


def test_frontend_unmapped_includes_frontend_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _synthetic_base(tmp_path)
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "a.py").write_text("", encoding="utf-8")
    _git_add_commit(tmp_path, "tests/test_a.py", "app/a.py")
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/a.py", "app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_a.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    code, out, _ = _run_affected(
        capsys, ["frontend/src/unmapped.ts"], repo_root=tmp_path
    )
    assert code == 2
    assert "MODE=BROAD" in out
    assert "PYTEST=pytest -q" in out
    assert "FRONTEND=" in out
    assert "npm test --prefix frontend" in out


def test_missing_focused_test_gives_broad(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _synthetic_base(tmp_path)
    (tmp_path / "app" / "a.py").write_text("", encoding="utf-8")
    _git_add_commit(tmp_path, "app/a.py")
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/a.py", "app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/missing.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    code, out, _ = _run_affected(capsys, ["app/a.py"], repo_root=tmp_path)
    assert code == 2
    assert "MODE=BROAD" in out


def test_reverse_closure_with_synthetic_graph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """a→b→c: changing c must include a, b; changing a must not."""
    _synthetic_base(tmp_path)
    for name in ["a", "b", "c"]:
        (tmp_path / "tests" / f"test_{name}.py").write_text("", encoding="utf-8")
        (tmp_path / "app" / f"{name}.py").write_text("", encoding="utf-8")
    _git_add_commit(
        tmp_path,
        "app/a.py",
        "app/b.py",
        "app/c.py",
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
    )
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/a.py"]
        dependencies = ["b"]
        focused_tests = ["tests/test_a.py"]
        agents_rules = []

        [modules.b]
        id = "b"
        owned_paths = ["app/b.py"]
        dependencies = ["c"]
        focused_tests = ["tests/test_b.py"]
        agents_rules = []

        [modules.c]
        id = "c"
        owned_paths = ["app/c.py", "app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_c.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    code, out, _ = _run_affected(capsys, ["app/c.py"], repo_root=tmp_path)
    assert code == 0
    assert "MODE=FOCUSED" in out
    assert "MODULES=a,b,c" in out
    code2, out2, _ = _run_affected(capsys, ["app/a.py"], repo_root=tmp_path)
    assert code2 == 0
    assert "MODULES=a" in out2
    assert "test_b.py" not in out2
    assert "test_c.py" not in out2


def test_git_diff_mode_with_real_repo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ = affected_main(
        ["--repo-root", str(REPO_ROOT), "--base", "main", "--head", "HEAD"]
    )
    captured = capsys.readouterr()
    assert "MODE=" in captured.out
    assert captured.out.strip() != ""


def test_cli_subprocess_with_explicit_path() -> None:
    res = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "affected_tests.py"),
            "--repo-root",
            str(REPO_ROOT),
            "app/audio.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "MODE=FOCUSED" in res.stdout
    assert "MODULES=audio,runtime_api" in res.stdout
