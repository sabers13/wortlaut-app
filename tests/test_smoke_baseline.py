"""Subprocess test verifying that reference/smoke_test.py executes cleanly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_smoke_baseline_execution() -> None:
    """Execute reference/smoke_test.py and assert exit 0 with OK output."""
    repo_root = Path(__file__).resolve().parent.parent
    smoke_script = repo_root / "reference" / "smoke_test.py"
    assert smoke_script.exists(), f"Smoke test script not found at {smoke_script}"

    proc = subprocess.run(
        [sys.executable, str(smoke_script)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        check=False,
    )

    assert proc.returncode == 0, f"smoke_test.py failed with code {proc.returncode}:\n{proc.stderr}"
    assert "OK" in proc.stdout, f"'OK' not found in smoke_test.py stdout:\n{proc.stdout}"
