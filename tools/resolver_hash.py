"""Canonical SHA-256 helper for app/resolve.py.

This module provides the single canonical definition of the resolver hash
in the repository, enforcing AGENTS R3.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

RESOLVER_PATH_REL = Path("app") / "resolve.py"


def get_default_resolve_path() -> Path:
    """Return the canonical location of app/resolve.py relative to repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / RESOLVER_PATH_REL


def get_resolver_hash(resolve_path: Path | str | None = None) -> str:
    """Compute canonical SHA-256 hex digest over app/resolve.py raw bytes.

    Fails closed if the file does not exist or cannot be read.
    """
    path = Path(resolve_path) if resolve_path is not None else get_default_resolve_path()
    if not path.is_file():
        raise FileNotFoundError(f"Resolver file not found at: {path}")
    raw_bytes = path.read_bytes()
    return hashlib.sha256(raw_bytes).hexdigest()


def get_resolver_short_hash(resolve_path: Path | str | None = None, length: int = 8) -> str:
    """Return leading slice of canonical resolver SHA-256 hex digest."""
    return get_resolver_hash(resolve_path)[:length]


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for resolver hash computation."""
    args = sys.argv[1:] if argv is None else list(argv)
    path = Path(args[0]) if args else None
    try:
        digest = get_resolver_hash(path)
        sys.stdout.write(f"{digest}\n")
        return 0
    except Exception as e:
        sys.stderr.write(f"Error computing resolver hash: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
