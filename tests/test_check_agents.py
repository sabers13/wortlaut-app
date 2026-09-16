"""Unit and integration tests for tools/check_agents.py and tools/resolver_hash.py."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_agents import (
    check_all,
    check_r1,
    check_r3,
    check_r6,
    check_r7,
    check_r12,
    check_r13,
    main,
    normalize_package_name,
)
from tools.resolver_hash import (
    get_resolver_hash,
    get_resolver_short_hash,
)
from tools.resolver_hash import (
    main as resolver_hash_main,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_normalize_package_name() -> None:
    assert normalize_package_name("openai>=1.0.0") == "openai"
    assert normalize_package_name("Google_Genai~=0.1") == "google-genai"
    assert normalize_package_name("anthropic[extra]<=0.5") == "anthropic"
    assert normalize_package_name("german_lecture==1.0") == "german-lecture"


def test_clean_repo_passes() -> None:
    """The repository tree must pass all rule checks cleanly."""
    violations = check_all(REPO_ROOT)
    assert violations == []


def test_resolver_hash_matches_raw_bytes() -> None:
    """tools/resolver_hash.py computes exact SHA-256 over app/resolve.py raw bytes."""
    resolve_path = REPO_ROOT / "app" / "resolve.py"
    expected = hashlib.sha256(resolve_path.read_bytes()).hexdigest()
    assert get_resolver_hash(resolve_path) == expected
    assert get_resolver_short_hash(resolve_path, 8) == expected[:8]


def test_resolver_hash_fails_on_missing_file(tmp_path: Path) -> None:
    """tools/resolver_hash.py fails closed when resolve.py is missing."""
    missing = tmp_path / "missing_resolve.py"
    with pytest.raises(FileNotFoundError):
        get_resolver_hash(missing)


def test_resolver_hash_cli(capsys: pytest.CaptureFixture[str]) -> None:
    """tools/resolver_hash.py CLI prints hash to stdout and exits 0."""
    resolve_path = REPO_ROOT / "app" / "resolve.py"
    exit_code = resolver_hash_main([str(resolve_path)])
    assert exit_code == 0
    captured = capsys.readouterr()
    expected = hashlib.sha256(resolve_path.read_bytes()).hexdigest()
    assert captured.out.strip() == expected


# --- R1 Tests ---


def test_r1_clean_subproject(tmp_path: Path) -> None:
    """A clean subproject without LLM deps or imports passes R1."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["fastapi", "pydantic"]\n',
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text(
        "import json\nfrom pathlib import Path\n\ndef run():\n    return 42\n",
        encoding="utf-8",
    )
    assert check_r1(tmp_path) == []


@pytest.mark.parametrize(
    "forbidden_dep",
    [
        "openai>=1.0",
        "anthropic==0.20.0",
        "google-genai",
        "google_generativeai>=0.3",
        "langchain",
        "llama-index-core",
    ],
)
def test_r1_rejects_forbidden_runtime_dependencies(tmp_path: Path, forbidden_dep: str) -> None:
    """R1 rejects any forbidden LLM SDK in pyproject.toml runtime dependencies."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["{forbidden_dep}"]\n',
        encoding="utf-8",
    )
    violations = check_r1(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R1 violation" in v and "Forbidden runtime LLM dependency" in v for v in violations
    )


@pytest.mark.parametrize(
    "forbidden_code",
    [
        "import openai\n",
        "from anthropic import Anthropic\n",
        "import google.genai\n",
        "import google_genai\n",
        "from google import genai\n",
        "from google import generativeai\n",
        "from langchain.chains import LLMChain\n",
        "import litellm\n",
    ],
)
def test_r1_rejects_forbidden_imports_in_app(tmp_path: Path, forbidden_code: str) -> None:
    """R1 rejects forbidden LLM imports in any file under app/."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    app_dir = tmp_path / "app" / "submodule"
    app_dir.mkdir(parents=True)
    (app_dir / "service.py").write_text(forbidden_code, encoding="utf-8")

    violations = check_r1(tmp_path)
    assert len(violations) >= 1
    assert any("R1 violation" in v and "Forbidden LLM import" in v for v in violations)


# --- R3 Tests ---


def _setup_valid_r3_project(tmp_path: Path) -> None:
    """Helper to set up minimal valid R3 project layout."""
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "resolve.py").write_text(
        "SVP_DEP = 'svp'\ndef resolve(): pass\n",
        encoding="utf-8",
    )
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "resolver_hash.py").write_text(
        "import hashlib\nfrom pathlib import Path\ndef get_resolver_hash(): return 'abc'\n",
        encoding="utf-8",
    )


def test_r3_clean_project_passes(tmp_path: Path) -> None:
    """A clean project with resolve.py and canonical resolver_hash passes R3."""
    _setup_valid_r3_project(tmp_path)
    assert check_r3(tmp_path) == []


def test_r3_fails_when_resolve_py_missing(tmp_path: Path) -> None:
    """R3 fails closed when app/resolve.py is missing."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "resolver_hash.py").write_text(
        "def get_resolver_hash(): pass\n",
        encoding="utf-8",
    )

    violations = check_r3(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R3 fail-closed" in v and "Required resolver file missing" in v
        for v in violations
    )


def test_r3_rejects_independent_resolve_hash(tmp_path: Path) -> None:
    """R3 rejects a second, independent SHA-256 calculation of app/resolve.py."""
    _setup_valid_r3_project(tmp_path)
    (tmp_path / "tools" / "other_tool.py").write_text(
        "import hashlib\nfrom pathlib import Path\n"
        "h = hashlib.sha256(Path('app/resolve.py').read_bytes()).hexdigest()\n",
        encoding="utf-8",
    )

    violations = check_r3(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R3 violation" in v and "Independent SHA-256 of app/resolve.py" in v
        for v in violations
    )


def test_r3_rejects_stage_02_without_resolver_hash(tmp_path: Path) -> None:
    """R3 rejects a stage-02 module that caches without tools.resolver_hash."""
    _setup_valid_r3_project(tmp_path)
    (tmp_path / "tools" / "index_tatoeba.py").write_text(
        "def checkpoint(name, fn):\n"
        "    pass\n\n"
        "def index_tatoeba():\n"
        "    checkpoint('tatoeba_index_v1', lambda: None)\n",
        encoding="utf-8",
    )

    violations = check_r3(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R3 violation" in v and "constructs cache key without using tools.resolver_hash" in v
        for v in violations
    )


def test_r3_accepts_stage_02_with_resolver_hash(tmp_path: Path) -> None:
    """R3 accepts a stage-02 build module that calls tools.resolver_hash."""
    _setup_valid_r3_project(tmp_path)
    (tmp_path / "tools" / "index_tatoeba.py").write_text(
        "from tools.resolver_hash import get_resolver_hash\n\n"
        "def checkpoint(name, fn):\n"
        "    pass\n\n"
        "def index_tatoeba():\n"
        "    r_hash = get_resolver_hash()[:8]\n"
        "    checkpoint(f'tatoeba_index_{r_hash}', lambda: None)\n",
        encoding="utf-8",
    )

    violations = check_r3(tmp_path)
    assert violations == []


def test_r3_fails_closed_on_unparseable_file(tmp_path: Path) -> None:
    """R3 fails closed when a scanned Python file cannot be parsed."""
    _setup_valid_r3_project(tmp_path)
    (tmp_path / "tools" / "broken.py").write_text("def broken_syntax(:\n", encoding="utf-8")

    violations = check_r3(tmp_path)
    assert len(violations) >= 1
    assert any("R3 fail-closed" in v and "Failed to read/parse" in v for v in violations)


# --- R7 Tests ---


@pytest.mark.parametrize(
    "forbidden_dep",
    [
        "german-lecture",
        "lecture-app>=0.1",
        "lecture_engine",
        "curriculum",
    ],
)
def test_r7_rejects_forbidden_runtime_dependencies(tmp_path: Path, forbidden_dep: str) -> None:
    """R7 rejects lecture-app runtime dependencies in pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["{forbidden_dep}"]\n',
        encoding="utf-8",
    )
    violations = check_r7(tmp_path)
    assert len(violations) >= 1
    assert any("R7 violation" in v and "Forbidden lecture-app dependency" in v for v in violations)


@pytest.mark.parametrize(
    "forbidden_code",
    [
        "import german_lecture\n",
        "from lecture_app.models import Lesson\n",
        "from lecture_engine import audio\n",
        "import lecture\n",
        "from curriculum import vocab\n",
    ],
)
def test_r7_rejects_forbidden_imports_in_app(tmp_path: Path, forbidden_code: str) -> None:
    """R7 rejects lecture-app imports in any file under app/."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "adapter.py").write_text(forbidden_code, encoding="utf-8")

    violations = check_r7(tmp_path)
    assert len(violations) >= 1
    assert any("R7 violation" in v and "Forbidden lecture-app import" in v for v in violations)


# --- CLI and Generic Fail-Closed Tests ---


def test_fail_closed_on_missing_pyproject(tmp_path: Path) -> None:
    """Missing pyproject.toml causes rule checks to fail closed."""
    violations = check_all(tmp_path)
    assert len(violations) >= 1
    assert any("fail-closed" in v for v in violations)


def test_fail_closed_on_malformed_pyproject(tmp_path: Path) -> None:
    """Unparseable pyproject.toml causes rule checks to fail closed."""
    (tmp_path / "pyproject.toml").write_text(
        "[project\nname = invalid toml",
        encoding="utf-8",
    )
    violations = check_all(tmp_path)
    assert len(violations) >= 1
    assert any("fail-closed" in v and "Failed to parse" in v for v in violations)


def test_fail_closed_on_missing_project_table(tmp_path: Path) -> None:
    """pyproject.toml without [project] table fails closed."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.other]\nkey = "value"\n',
        encoding="utf-8",
    )
    violations = check_all(tmp_path)
    assert len(violations) >= 1
    assert any("missing [project] table" in v for v in violations)


def test_fail_closed_on_syntax_error_in_app(tmp_path: Path) -> None:
    """Syntax error in app/ python file causes rule checks to fail closed."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "broken.py").write_text("def broken_func(\n", encoding="utf-8")

    violations = check_all(tmp_path)
    assert len(violations) >= 1
    assert any("fail-closed" in v and "broken.py" in v for v in violations)


# --- R6 Tests ---


def _setup_valid_r6_project(tmp_path: Path) -> None:
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "schema.sql").write_text(
        """
        CREATE TABLE IF NOT EXISTS review_log (
          id             INTEGER PRIMARY KEY,
          card_id        INTEGER NOT NULL REFERENCES card(id) ON DELETE RESTRICT,
          confidence     INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
          rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
          scheduled_days REAL NOT NULL,
          elapsed_days   REAL NOT NULL,
          reviewed_at    TEXT NOT NULL
        );
        """,
        encoding="utf-8",
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "deck.py").write_text(
        """
        def log_review(conn, card_id, confidence, rating):
            conn.execute(
                "INSERT INTO review_log (card_id, confidence, rating) VALUES (?, ?, ?)",
                (card_id, confidence, rating),
            )
        """,
        encoding="utf-8",
    )


def test_r6_clean_repo_passes() -> None:
    """The repository reference/schema.sql and app/ pass R6 cleanly."""
    assert check_r6(REPO_ROOT) == []


def test_r6_clean_project_passes(tmp_path: Path) -> None:
    _setup_valid_r6_project(tmp_path)
    assert check_r6(tmp_path) == []


def test_r6_detects_missing_schema_file(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "deck.py").write_text("pass\n", encoding="utf-8")
    violations = check_r6(tmp_path)
    assert len(violations) >= 1
    assert any("R6 fail-closed" in v and "Missing required schema file" in v for v in violations)


def test_r6_detects_missing_table(tmp_path: Path) -> None:
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir(parents=True)
    (ref_dir / "schema.sql").write_text(
        "CREATE TABLE deck (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    violations = check_r6(tmp_path)
    assert len(violations) >= 1
    assert any("R6 violation" in v and "missing CREATE TABLE review_log" in v for v in violations)


def test_r6_detects_missing_confidence_constraint(tmp_path: Path) -> None:
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir(parents=True)
    (ref_dir / "schema.sql").write_text(
        """
        CREATE TABLE review_log (
          id INTEGER PRIMARY KEY,
          confidence INTEGER NOT NULL,
          rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4)
        );
        """,
        encoding="utf-8",
    )
    violations = check_r6(tmp_path)
    assert len(violations) >= 1
    assert any("R6 violation" in v and "confidence BETWEEN 1 AND 5" in v for v in violations)


def test_r6_detects_missing_rating_constraint(tmp_path: Path) -> None:
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir(parents=True)
    (ref_dir / "schema.sql").write_text(
        """
        CREATE TABLE review_log (
          id INTEGER PRIMARY KEY,
          confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
          rating INTEGER NOT NULL
        );
        """,
        encoding="utf-8",
    )
    violations = check_r6(tmp_path)
    assert len(violations) >= 1
    assert any("R6 violation" in v and "rating BETWEEN 1 AND 4" in v for v in violations)


@pytest.mark.parametrize(
    "mutation_sql",
    [
        'conn.execute("UPDATE review_log SET rating = 1 WHERE id = ?", (log_id,))',
        'conn.execute("DELETE FROM review_log WHERE card_id = ?", (card_id,))',
        'conn.execute("update review_log set confidence = 5")',
        'conn.execute("delete from review_log where id = 1")',
    ],
)
def test_r6_detects_mutation_queries_in_app(tmp_path: Path, mutation_sql: str) -> None:
    _setup_valid_r6_project(tmp_path)
    (tmp_path / "app" / "bad.py").write_text(
        f"def bad(conn):\n    {mutation_sql}\n", encoding="utf-8"
    )
    violations = check_r6(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R6 violation" in v and "Forbidden SQL mutation on review_log" in v for v in violations
    )


# --- R12 Tests ---


def _setup_valid_r12_project(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "api.py").write_text(
        '''
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

def _is_loopback_host(host: str | None) -> bool:
    return host in ("127.0.0.1", "localhost", "[::1]")

class BrowserSecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, cors_origins):
        super().__init__(app)
        self.cors_origins = cors_origins

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host")
        if not _is_loopback_host(host):
            return None
        origin = request.headers.get("origin")
        if origin and origin not in self.cors_origins:
            return None
        path = request.url.path
        if path.startswith("/vocab") and request.method != "GET":
            if request.headers.get("x-flashcards-request") != "1":
                return None
        return await call_next(request)

def create_app(cors_origins):
    for orig in cors_origins:
        if "*" in orig:
            raise ValueError("Wildcard origin is forbidden")
    app = FastAPI()
    app.add_middleware(BrowserSecurityMiddleware, cors_origins=set(cors_origins))

    @app.post("/vocab/notes")
    def capture_note():
        pass

    return app
''',
        encoding="utf-8",
    )


def test_r12_clean_repo_passes() -> None:
    """The repository app/api.py passes R12 cleanly."""
    assert check_r12(REPO_ROOT) == []


def test_r12_clean_project_passes(tmp_path: Path) -> None:
    _setup_valid_r12_project(tmp_path)
    assert check_r12(tmp_path) == []


def test_r12_detects_missing_api_file(tmp_path: Path) -> None:
    violations = check_r12(tmp_path)
    assert len(violations) >= 1
    assert any("R12 fail-closed" in v and "Required API file missing" in v for v in violations)


def test_r12_detects_wildcard_origin_acceptance(tmp_path: Path) -> None:
    _setup_valid_r12_project(tmp_path)
    api_source = (tmp_path / "app" / "api.py").read_text(encoding="utf-8")
    assert 'if "*" in orig:' in api_source
    bad_source = api_source.replace(
        '        if "*" in orig:\n            raise ValueError("Wildcard origin is forbidden")',
        "        pass",
    )
    assert 'if "*" in orig:' not in bad_source
    (tmp_path / "app" / "api.py").write_text(bad_source, encoding="utf-8")
    violations = check_r12(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R12 violation" in v and "does not reject wildcard '*'" in v for v in violations
    )


def test_r12_detects_missing_middleware_marker(tmp_path: Path) -> None:
    _setup_valid_r12_project(tmp_path)
    api_source = (tmp_path / "app" / "api.py").read_text(encoding="utf-8")
    bad_source = api_source.replace(
        "app.add_middleware(BrowserSecurityMiddleware, cors_origins=set(cors_origins))",
        "# no middleware",
    )
    (tmp_path / "app" / "api.py").write_text(bad_source, encoding="utf-8")
    violations = check_r12(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R12 violation" in v and "missing structural host/origin security middleware" in v
        for v in violations
    )


def test_r12_detects_uncovered_non_get_route(tmp_path: Path) -> None:
    _setup_valid_r12_project(tmp_path)
    api_source = (tmp_path / "app" / "api.py").read_text(encoding="utf-8")
    bad_source = api_source.replace(
        'request.headers.get("x-flashcards-request")',
        'request.headers.get("x-other-header")',
    )
    (tmp_path / "app" / "api.py").write_text(bad_source, encoding="utf-8")
    violations = check_r12(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R12 violation" in v and "X-Flashcards-Request guard" in v for v in violations
    )


# --- R13 Tests ---


def _setup_valid_r13_project(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "deck.py").write_text(
        '''
def validate_candidate_dictionary(path):
    pass

class DictionaryRuntime:
    def activate_dictionary(self, path):
        asset = validate_candidate_dictionary(path)
        self._relink_part_b(None, asset, "now")

    def _relink_part_b(self, conn, asset, now_text):
        # references lemma_semantic_ref and sense_semantic_ref
        # sets binding_status
        pass
''',
        encoding="utf-8",
    )
    (app_dir / "api.py").write_text(
        '''
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

def register_routes(app, runtime):
    @app.post("/vocab/notes")
    def capture_note(picker_token: str):
        active_token = runtime.asset_token
        if picker_token != active_token:
            return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={})
''',
        encoding="utf-8",
    )


def test_r13_clean_repo_passes() -> None:
    """The repository app/deck.py and app/api.py pass R13 cleanly."""
    assert check_r13(REPO_ROOT) == []


def test_r13_clean_project_passes(tmp_path: Path) -> None:
    _setup_valid_r13_project(tmp_path)
    assert check_r13(tmp_path) == []


def test_r13_detects_missing_activation_ref_validation(tmp_path: Path) -> None:
    _setup_valid_r13_project(tmp_path)
    (tmp_path / "app" / "deck.py").write_text(
        '''
class DictionaryRuntime:
    def activate_dictionary(self, path):
        pass
''',
        encoding="utf-8",
    )
    violations = check_r13(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R13 violation" in v
        and "missing candidate validation or stable semantic ref relink logic" in v
        for v in violations
    )


def test_r13_detects_missing_stale_token_409_rejection(tmp_path: Path) -> None:
    _setup_valid_r13_project(tmp_path)
    (tmp_path / "app" / "api.py").write_text(
        '''
def register_routes(app, runtime):
    @app.post("/vocab/notes")
    def capture_note():
        pass
''',
        encoding="utf-8",
    )
    violations = check_r13(tmp_path)
    assert len(violations) >= 1
    assert any(
        "R13 violation" in v and "missing stale-token HTTP 409 rejection logic" in v
        for v in violations
    )


# --- CLI and Generic Fail-Closed Tests ---


def test_cli_success(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI returns 0 on clean repository."""
    exit_code = main([str(REPO_ROOT)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "AGENTS checks passed" in captured.out


def test_cli_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI returns 1 on repository with violations."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "bad"\nversion = "0.1.0"\ndependencies = ["openai"]\n',
        encoding="utf-8",
    )
    exit_code = main([str(tmp_path)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "AGENTS check failed" in captured.err


def test_cli_subprocess_invocation() -> None:
    """Subprocess execution of tools/check_agents.py against the repository root succeeds."""
    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_agents.py"), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "AGENTS checks passed" in res.stdout

