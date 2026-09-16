"""Installed ``wortlaut`` console-script adapter.

The canonical launcher implementation lives in the repository-root
``wortlaut`` script (heavily tested, owns the venv re-exec contract).
This module exposes that same entry point through Python package
console-script metadata without duplicating any launcher behavior: it
locates the root source launcher relative to the installed (or
editable) ``app`` package, loads it with the standard library, and
delegates to its existing ``main`` with arguments and exit code
preserved.

This adapter intentionally serves the documented source-install model
(``pip install -e .`` inside a repository clone). Wheel relocation or
standalone binary packaging is out of scope.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


def _launcher_path() -> Path:
    """Return the repository-root ``wortlaut`` source launcher path."""
    candidate = Path(__file__).resolve().parent.parent / "wortlaut"
    if not candidate.is_file():
        raise RuntimeError(
            "wortlaut source launcher is unavailable at "
            f"{candidate}; the installed console command requires "
            "a repository source install (pip install -e .)"
        )
    return candidate


def _load_launcher_main() -> Callable[[list[str] | None], int]:
    """Load the root launcher module and return its ``main`` callable."""
    path = _launcher_path()
    loader = SourceFileLoader("wortlaut_root_launcher", str(path))
    spec = spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("failed to build a module spec for the wortlaut launcher")
    module = module_from_spec(spec)
    loader.exec_module(module)
    main = getattr(module, "main", None)
    if not callable(main):
        raise RuntimeError("wortlaut source launcher does not expose a callable main()")
    launcher_main: Callable[[list[str] | None], int] = main
    return launcher_main


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point: delegate to the root launcher's ``main``."""
    launcher_main = _load_launcher_main()
    args = list(argv) if argv is not None else sys.argv[1:]
    result = launcher_main(args)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
