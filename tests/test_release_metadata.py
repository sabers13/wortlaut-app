"""Release-metadata coherence for the Wortlaut application version.

The application version (``app.version.__version__``) is the single
user-facing release namespace. It is distinct from the immutable
dictionary release identities (``dictionary-v2`` /
``dictionary-online-v2``): dictionary assets carry their own
version/token namespace and must never be treated as the application
version.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from app.version import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_APPLICATION_VERSION = "0.1.0"


def _pyproject() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_application_version_is_first_release() -> None:
    assert __version__ == EXPECTED_APPLICATION_VERSION


def test_python_distribution_is_branded_wortlaut() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    assert project["name"] == "wortlaut"


def test_python_version_is_sourced_from_app_version() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    assert project.get("dynamic") == ["version"]
    assert "version" not in project
    dynamic = _pyproject()["tool"]["setuptools"]["dynamic"]
    assert isinstance(dynamic, dict)
    assert dynamic["version"] == {"attr": "app.version.__version__"}


def test_installed_wortlaut_console_entrypoint() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    scripts = project.get("scripts")
    assert isinstance(scripts, dict)
    assert scripts.get("wortlaut") == "app.cli:main"
    assert "flashcard" not in scripts


def test_frontend_package_version_matches_application_version() -> None:
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text())
    assert package["version"] == __version__


def test_dictionary_releases_are_not_the_application_version() -> None:
    offline = json.loads(
        (REPO_ROOT / "release" / "dictionary-manifest-v2.json").read_text()
    )
    assert offline["version"] != __version__
    assert offline["version"] == "v2"
    online = json.loads(
        (REPO_ROOT / "release" / "dictionary-online-manifest-v2.json").read_text()
    )
    assert online["dataset_token"] != __version__
    assert "0.1.0" not in json.dumps(online)


@pytest.mark.parametrize("path", ["wortlaut", "app/cli.py", "app/version.py"])
def test_release_surface_files_exist(path: str) -> None:
    assert (REPO_ROOT / path).is_file()
