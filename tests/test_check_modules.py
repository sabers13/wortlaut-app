"""Tests for tools/check_modules.py validation."""

# ruff: noqa: E501

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tools.check_modules import check_all
from tools.check_modules import main as check_modules_main

REPO_ROOT = Path(__file__).resolve().parent.parent


def _init_git_repo(
    tmp_path: Path,
    *,
    commit: bool = False,
    gitignore: str = "",
) -> None:
    """Initialize `tmp_path` as a git repo with optional `.gitignore`."""
    subprocess.run(
        ["git", "init", "--initial-branch=main", "-q", str(tmp_path)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
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
    if gitignore:
        (tmp_path / ".gitignore").write_text(gitignore, encoding="utf-8")
    if commit:
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "-A"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
            check=True,
        )


def _git_add_commit(tmp_path: Path, *paths: str) -> None:
    """Stage and commit the given paths."""
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--", *paths],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "add"],
        check=True,
    )


def _synthetic_base(
    tmp_path: Path,
    *,
    commit_inventory: bool = True,
) -> None:
    """Create a minimal valid git repo with all inventory roots populated."""
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reference").mkdir(parents=True, exist_ok=True)
    (tmp_path / "frontend" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "dummy.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "tools" / "dummy.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "reference" / "schema.sql").write_text("select 1;\n", encoding="utf-8")
    (tmp_path / "reference" / "smoke_test.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "frontend" / "src" / "app.ts").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "tools" / "check_modules.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "affected_tests.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_dummy.py").write_text("def test_dummy(): pass\n", encoding="utf-8")

    if commit_inventory:
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
# Real-repo smoke test
# ---------------------------------------------------------------------------


def test_valid_real_modules_toml(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_modules_main([str(REPO_ROOT / "MODULES.toml")])
    assert code == 0
    out = capsys.readouterr().out
    assert "MODULES validation passed:" in out
    assert "24 modules" in out


# ---------------------------------------------------------------------------
# Schema/id validation
# ---------------------------------------------------------------------------


def test_missing_id_fails(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    content = textwrap.dedent("""
        [modules.a]
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 0
    assert any("missing required field 'id'" in v for v in violations)


def test_non_string_id_fails(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    content = textwrap.dedent("""
        [modules.a]
        id = 42
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 0
    assert any("field 'id' must be a string" in v for v in violations)


def test_empty_id_fails(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    content = textwrap.dedent("""
        [modules.a]
        id = ""
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 0
    assert any("field 'id' must be non-empty" in v for v in violations)


def test_mismatched_id_fails(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    content = textwrap.dedent("""
        [modules.a]
        id = "wrong"
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 0
    assert any("does not match its explicit 'id' field 'wrong'" in v for v in violations)


def test_duplicate_effective_id_fails(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/dummy.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.b]
        id = "a"
        owned_paths = ["tools/dummy.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 0
    assert any("duplicate module id 'a'" in v for v in violations)


def test_mismatched_keys_with_shared_effective_id_reports_duplicate(
    tmp_path: Path,
) -> None:
    """Two table keys with different names but the same non-empty
    effective id must surface BOTH per-key mismatch diagnostics AND
    a duplicate-effective-id diagnostic. Neither violation may be
    silently absorbed.
    """
    _synthetic_base(tmp_path)
    content = textwrap.dedent("""
        [modules.a]
        id = "shared"
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.b]
        id = "shared"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 0
    mismatch_count = sum(
        1 for v in violations if "does not match its explicit 'id' field 'shared'" in v
    )
    assert mismatch_count == 2, (
        f"expected two mismatch diagnostics, one per mismatched table key, got: {violations}"
    )
    assert any("duplicate module id 'shared'" in v for v in violations), (
        f"expected duplicate-effective-id diagnostic, got: {violations}"
    )


# ---------------------------------------------------------------------------
# Graph / dependency validation
# ---------------------------------------------------------------------------


def test_malformed_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "MODULES.toml").write_text("invalid toml [[[", encoding="utf-8")
    code = check_modules_main([str(tmp_path / "MODULES.toml")])
    assert code != 0
    err = capsys.readouterr().err
    assert "failed" in err.lower() or "malformed" in err.lower()


def test_unknown_dependency(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "a.py").write_text("", encoding="utf-8")
    _git_add_commit(tmp_path, "tests/test_a.py", "app/a.py")
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/a.py", "app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = ["nonexistent"]
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
    violations, _ = check_all(tmp_path / "MODULES.toml")
    assert any("unknown dependency" in v.lower() for v in violations)


def test_missing_focused_test(tmp_path: Path) -> None:
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
    violations, _ = check_all(tmp_path / "MODULES.toml")
    assert any("focused-test" in v.lower() and "does not exist" in v.lower() for v in violations)


def test_duplicate_ambiguous_ownership(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    (tmp_path / "app" / "dup.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("", encoding="utf-8")
    _git_add_commit(tmp_path, "app/dup.py", "tests/test_a.py", "tests/test_b.py")
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/dup.py"]
        dependencies = []
        focused_tests = ["tests/test_a.py"]
        agents_rules = []

        [modules.b]
        id = "b"
        owned_paths = ["app/dup.py"]
        dependencies = []
        focused_tests = ["tests/test_b.py"]
        agents_rules = []

        [modules.c]
        id = "c"
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile", "MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, _ = check_all(tmp_path / "MODULES.toml")
    assert any("ambiguous" in v.lower() for v in violations)


def test_dependency_cycle(tmp_path: Path) -> None:
    _synthetic_base(tmp_path)
    (tmp_path / "app" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_b.py").write_text("", encoding="utf-8")
    _git_add_commit(tmp_path, "app/a.py", "app/b.py", "tests/test_a.py", "tests/test_b.py")
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
        dependencies = ["a"]
        focused_tests = ["tests/test_b.py"]
        agents_rules = []

        [modules.c]
        id = "c"
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile", "MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, _ = check_all(tmp_path / "MODULES.toml")
    assert any("cycle" in v.lower() for v in violations)


# ---------------------------------------------------------------------------
# Git-aware inventory regressions
# ---------------------------------------------------------------------------


def test_tracked_nonignored_unowned_source_fails(tmp_path: Path) -> None:
    """A tracked file outside any owned_paths glob must be flagged."""
    _synthetic_base(tmp_path, commit_inventory=False)
    (tmp_path / "app" / "extra_owned.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "orphan.py").write_text("", encoding="utf-8")
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
        "app/extra_owned.py",
        "app/orphan.py",
    )
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/dummy.py", "app/extra_owned.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, _ = check_all(tmp_path / "MODULES.toml")
    assert any("unowned inventory path: 'app/orphan.py'" in v for v in violations)


def test_untracked_nonignored_unowned_source_fails(tmp_path: Path) -> None:
    """An untracked non-ignored file outside owned_paths must be flagged.

    `git ls-files --others --exclude-standard` includes untracked,
    non-ignored files — so the file appears in inventory and the
    ownership check fails closed.
    """
    _synthetic_base(tmp_path)
    (tmp_path / "app" / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, _ = check_all(tmp_path / "MODULES.toml")
    assert any("unowned inventory path: 'app/new_module.py'" in v for v in violations)


def test_ignored_path_excluded_from_inventory(tmp_path: Path) -> None:
    """An ignored file must NOT participate in ownership validation."""
    _synthetic_base(tmp_path, commit_inventory=False)
    (tmp_path / ".gitignore").write_text("app/frontend/\n", encoding="utf-8")
    _init_git_repo(tmp_path, commit=False)
    _git_add_commit(tmp_path, ".gitignore")
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
    (tmp_path / "app" / "frontend").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "frontend" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 2
    assert all(
        "app/frontend/index.html" not in v and "unowned inventory path: 'app/frontend'" not in v
        for v in violations
    )


def test_git_inventory_failure_fails_closed(tmp_path: Path) -> None:
    """A non-git repo must produce a fail-closed inventory violation."""
    _synthetic_base(tmp_path, commit_inventory=False)
    content = textwrap.dedent("""
        [modules.a]
        id = "a"
        owned_paths = ["app/dummy.py", "tools/dummy.py", "reference/schema.sql", "reference/smoke_test.py", "frontend/src/app.ts", "Dockerfile"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []

        [modules.module_metadata]
        id = "module_metadata"
        owned_paths = ["MODULES.toml", "tools/check_modules.py", "tools/affected_tests.py"]
        dependencies = []
        focused_tests = ["tests/test_dummy.py"]
        agents_rules = []
    """)
    (tmp_path / "MODULES.toml").write_text(content, encoding="utf-8")
    violations, count = check_all(tmp_path / "MODULES.toml")
    assert count == 0
    assert any("git inventory failure" in v.lower() for v in violations)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_subprocess() -> None:
    res = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "check_modules.py"),
            str(REPO_ROOT / "MODULES.toml"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "MODULES validation passed" in res.stdout
