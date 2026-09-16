"""Executable AGENTS rule checker for project gate.

Enforces:
- R1: Runtime LLM SDK prohibition (pyproject.toml runtime deps and app/ imports)
- R3: Build-cache keys include resolver hash (tools.resolver_hash canonical helper)
- R6: review_log append-only schema constraints and zero UPDATE/DELETE mutations in app/
- R7: Zero lecture-app coupling (no imports or dependencies on lecture app)
- R12: Browser-facing loopback origin/host guards and X-Flashcards-Request non-GET coverage
- R13: Durable semantic identity validation on activation and stale-token HTTP 409 rejection
- M2: Duplicate-safe note identity (partial UNIQUE dictionary_key; product
  app/api.py uses the shared find_or_create_note instead of create_note)
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path
from typing import Final, Sequence

# Canonical forbidden LLM package names (normalized with hyphens and lowercase)
FORBIDDEN_LLM_PACKAGES: Final[frozenset[str]] = frozenset({
    "anthropic",
    "cohere",
    "google-genai",
    "google-generativeai",
    "groq",
    "huggingface-hub",
    "instructor",
    "langchain",
    "litellm",
    "llama-index",
    "mistralai",
    "ollama",
    "openai",
    "transformers",
    "vllm",
})

# Canonical forbidden LLM Python module import prefixes
FORBIDDEN_LLM_MODULES: Final[frozenset[str]] = frozenset({
    "anthropic",
    "cohere",
    "google.genai",
    "google.generativeai",
    "google_genai",
    "groq",
    "huggingface_hub",
    "instructor",
    "langchain",
    "litellm",
    "llama_index",
    "mistralai",
    "ollama",
    "openai",
    "transformers",
    "vllm",
})

# Canonical forbidden lecture app package names (normalized)
FORBIDDEN_LECTURE_PACKAGES: Final[frozenset[str]] = frozenset({
    "curriculum",
    "german-lecture",
    "lecture",
    "lecture-app",
    "lecture-engine",
})

# Canonical forbidden lecture app Python module import prefixes
FORBIDDEN_LECTURE_MODULES: Final[frozenset[str]] = frozenset({
    "curriculum",
    "german_lecture",
    "lecture",
    "lecture_app",
    "lecture_engine",
})

EXCLUDED_DIR_NAMES: Final[frozenset[str]] = frozenset({
    ".git",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    "handoff",
    "reference",
})


def normalize_package_name(dep: str) -> str:
    """Extract and normalize base package name from a PEP 508 dependency string."""
    match = re.match(r"^([a-zA-Z0-9_.-]+)", dep.strip())
    if not match:
        return dep.strip().lower().replace("_", "-")
    return match.group(1).lower().replace("_", "-")


def is_forbidden_llm_package(dep: str) -> bool:
    """Check if dependency string matches any forbidden LLM package name or prefix."""
    pkg = normalize_package_name(dep)
    if pkg in FORBIDDEN_LLM_PACKAGES:
        return True
    return any(pkg.startswith(f"{p}-") or pkg.startswith(f"{p}_") for p in FORBIDDEN_LLM_PACKAGES)


def is_forbidden_llm_module(mod: str) -> bool:
    """Check if imported module matches any forbidden LLM module prefix."""
    top = mod.split(".")[0]
    for forbidden in FORBIDDEN_LLM_MODULES:
        if mod == forbidden or mod.startswith(f"{forbidden}.") or top == forbidden:
            return True
    return False


def is_forbidden_lecture_package(dep: str) -> bool:
    """Check if dependency string matches any forbidden lecture app package name or prefix."""
    pkg = normalize_package_name(dep)
    if pkg in FORBIDDEN_LECTURE_PACKAGES:
        return True
    return any(
        pkg.startswith(f"{p}-") or pkg.startswith(f"{p}_") for p in FORBIDDEN_LECTURE_PACKAGES
    )


def is_forbidden_lecture_module(mod: str) -> bool:
    """Check if imported module matches any forbidden lecture app module prefix."""
    top = mod.split(".")[0]
    for forbidden in FORBIDDEN_LECTURE_MODULES:
        if mod == forbidden or mod.startswith(f"{forbidden}.") or top == forbidden:
            return True
    return False


def parse_pyproject(repo_root: Path) -> dict[str, object]:
    """Parse pyproject.toml at repo_root, failing closed on missing or malformed file."""
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"Missing required configuration: {pyproject_path}")
    try:
        content = pyproject_path.read_text(encoding="utf-8")
        return tomllib.loads(content)
    except Exception as e:
        raise ValueError(f"Failed to parse {pyproject_path}: {e}") from e


def get_runtime_dependencies(pyproject_data: dict[str, object]) -> list[str]:
    """Extract runtime dependencies from parsed pyproject.toml."""
    project_table = pyproject_data.get("project")
    if not isinstance(project_table, dict):
        raise ValueError("pyproject.toml is missing [project] table")
    deps = project_table.get("dependencies", [])
    if not isinstance(deps, list):
        raise ValueError("[project.dependencies] must be a list")
    for item in deps:
        if not isinstance(item, str):
            raise ValueError(f"Invalid dependency entry: {item}")
    return list(deps)


def get_app_python_files(repo_root: Path) -> list[Path]:
    """Find all .py files in app/ directory if it exists."""
    app_dir = repo_root / "app"
    if not app_dir.exists():
        return []
    if not app_dir.is_dir():
        raise ValueError(f"Expected app to be a directory, but found: {app_dir}")
    return sorted(app_dir.rglob("*.py"))


def get_all_scannable_python_files(repo_root: Path) -> list[Path]:
    """Find all .py files in repo_root excluding cache and distribution directories."""
    python_files: list[Path] = []
    for path in repo_root.rglob("*.py"):
        parts = set(path.relative_to(repo_root).parts)
        if any(excluded in parts for excluded in EXCLUDED_DIR_NAMES):
            continue
        python_files.append(path)
    return sorted(python_files)


def collect_imports_from_file(file_path: Path) -> list[str]:
    """Parse AST of a python file and return all imported module paths.

    Fails closed on syntax errors or read errors.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except Exception as e:
        raise ValueError(f"Failed to read/parse Python file {file_path}: {e}") from e

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
                for alias in node.names:
                    imports.append(f"{node.module}.{alias.name}")
    return imports


def check_r1(repo_root: Path) -> list[str]:
    """Check AGENTS Rule R1: No LLM at runtime.

    Scans pyproject.toml runtime dependencies and app/ imports.
    """
    violations: list[str] = []

    # 1. Check runtime dependencies in pyproject.toml
    try:
        pyproject_data = parse_pyproject(repo_root)
        runtime_deps = get_runtime_dependencies(pyproject_data)
        for dep in runtime_deps:
            if is_forbidden_llm_package(dep):
                violations.append(
                    f"R1 violation: Forbidden runtime LLM dependency '{dep}' in pyproject.toml"
                )
    except Exception as e:
        violations.append(f"R1 fail-closed: {e}")

    # 2. Check imports in app/
    try:
        app_files = get_app_python_files(repo_root)
        for py_file in app_files:
            try:
                imported_modules = collect_imports_from_file(py_file)
                for mod in imported_modules:
                    if is_forbidden_llm_module(mod):
                        violations.append(
                            f"R1 violation: Forbidden LLM import '{mod}' in {py_file}"
                        )
            except Exception as e:
                violations.append(f"R1 fail-closed: {e}")
    except Exception as e:
        violations.append(f"R1 fail-closed: {e}")

    return violations


def detects_independent_resolve_hash(tree: ast.AST, source: str) -> bool:
    """Check if AST contains an independent SHA-256 calculation over app/resolve.py."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            is_sha256_call = False
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "sha256":
                is_sha256_call = True
            elif isinstance(func, ast.Name) and func.id == "sha256":
                is_sha256_call = True
            elif isinstance(func, ast.Attribute) and func.attr == "new":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "sha256":
                        is_sha256_call = True

            if is_sha256_call:
                call_segment = ast.get_source_segment(source, node)
                if call_segment and (
                    "resolve.py" in call_segment or "app/resolve.py" in call_segment
                ):
                    return True
                for arg in node.args:
                    for sub in ast.walk(arg):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                            if "resolve.py" in sub.value:
                                return True
    return False


def is_stage_02_module(file_path: Path, tree: ast.AST, source: str) -> bool:
    """Detect whether a module is a stage-02 build/indexing module."""
    if file_path.name in ("check_agents.py", "resolver_hash.py"):
        return False
    stem = file_path.stem.lower()
    if any(k in stem for k in ("stage02", "stage_02", "index_tatoeba")):
        return True
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name.lower()
            if any(k in name for k in ("index_tatoeba", "stage_02", "stage02", "build_stage_02")):
                return True
    return False


def stage_02_uses_resolver_hash(tree: ast.AST) -> bool:
    """Check if stage-02 module properly imports and calls tools.resolver_hash."""
    has_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in ("tools.resolver_hash", "resolver_hash"):
                has_import = True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("tools.resolver_hash", "resolver_hash"):
                    has_import = True

    if not has_import:
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in (
                "get_resolver_hash",
                "get_resolver_short_hash",
                "resolver_hash",
            ):
                return True
            if isinstance(func, ast.Attribute) and func.attr in (
                "get_resolver_hash",
                "get_resolver_short_hash",
                "resolver_hash",
            ):
                return True
    return False


def check_r3(repo_root: Path) -> list[str]:
    """Check AGENTS Rule R3: Build-cache keys include resolver hash.

    Enforces:
    1. app/resolve.py exists, is readable, and non-empty.
    2. No SHA-256 calculation over app/resolve.py exists outside tools/resolver_hash.py.
    3. Any stage-02 build module that performs caching calls tools.resolver_hash.
    """
    violations: list[str] = []

    # 1. Verify app/resolve.py exists and is readable
    resolve_path = repo_root / "app" / "resolve.py"
    if not resolve_path.exists() or not resolve_path.is_file():
        violations.append(f"R3 fail-closed: Required resolver file missing: {resolve_path}")
    else:
        try:
            raw_bytes = resolve_path.read_bytes()
            if not raw_bytes:
                violations.append(
                    f"R3 fail-closed: Required resolver file is empty: {resolve_path}"
                )
        except Exception as e:
            violations.append(f"R3 fail-closed: Cannot read resolver file {resolve_path}: {e}")

    # 2 & 3. Scan all scannable Python files in repository
    try:
        py_files = get_all_scannable_python_files(repo_root)
        canonical_helper = (repo_root / "tools" / "resolver_hash.py").resolve()

        for py_file in py_files:
            try:
                # tools/resolver_hash.py is the single authorized canonical definition
                if py_file.resolve() == canonical_helper:
                    continue

                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))

                # Check 2: Independent SHA-256 calculation
                if detects_independent_resolve_hash(tree, source):
                    violations.append(
                        f"R3 violation: Independent SHA-256 of app/resolve.py in {py_file}; "
                        "must use tools.resolver_hash"
                    )

                # Check 3: Stage-02 module cache key construction
                if is_stage_02_module(py_file, tree, source):
                    # Check if it performs caching/checkpointing
                    has_caching = (
                        "checkpoint" in source
                        or "cache" in source
                        or "tatoeba_index" in source
                    )
                    if has_caching and not stage_02_uses_resolver_hash(tree):
                        violations.append(
                            f"R3 violation: Stage-02 build module '{py_file}' constructs "
                            "cache key without using tools.resolver_hash"
                        )

            except Exception as e:
                violations.append(
                    f"R3 fail-closed: Failed to read/parse Python file {py_file}: {e}"
                )

    except Exception as e:
        violations.append(f"R3 fail-closed: {e}")

    return violations


def check_r6(repo_root: Path) -> list[str]:
    """Check AGENTS Rule R6: review_log is append-only and logs the raw confidence.

    Enforces:
    1. reference/schema.sql enforces confidence INTEGER NOT NULL CHECK (1..5)
       and rating INTEGER NOT NULL CHECK (1..4) on review_log.
    2. Every file under app/ contains zero UPDATE review_log or DELETE FROM review_log SQL.
    """
    violations: list[str] = []

    # 1. Check schema constraints in reference/schema.sql
    schema_path = repo_root / "reference" / "schema.sql"
    if not schema_path.exists() or not schema_path.is_file():
        violations.append(f"R6 fail-closed: Missing required schema file: {schema_path}")
    else:
        try:
            schema_sql = schema_path.read_text(encoding="utf-8")
            table_match = re.search(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?review_log\s*\((.*?)\);",
                schema_sql,
                re.DOTALL | re.IGNORECASE,
            )
            if not table_match:
                violations.append(
                    "R6 violation: reference/schema.sql missing CREATE TABLE review_log definition"
                )
            else:
                table_body = table_match.group(1)

                # Check confidence column: INTEGER NOT NULL + CHECK (confidence BETWEEN 1 AND 5)
                conf_match = re.search(
                    r"\bconfidence\s+INTEGER\s+NOT\s+NULL\b.*?\bCHECK\s*\(\s*confidence\s+(?:BETWEEN\s+1\s+AND\s+5|(?:>=\s*1\s+AND\s+confidence\s*<=\s*5))\s*\)",
                    table_body,
                    re.IGNORECASE | re.DOTALL,
                )
                if not conf_match:
                    violations.append(
                        "R6 violation: reference/schema.sql review_log table missing "
                        "'confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5)'"
                    )

                # Check rating column: INTEGER NOT NULL + CHECK (rating BETWEEN 1 AND 4)
                rating_match = re.search(
                    r"\brating\s+INTEGER\s+NOT\s+NULL\b.*?\bCHECK\s*\(\s*rating\s+(?:BETWEEN\s+1\s+AND\s+4|(?:>=\s*1\s+AND\s+rating\s*<=\s*4))\s*\)",
                    table_body,
                    re.IGNORECASE | re.DOTALL,
                )
                if not rating_match:
                    violations.append(
                        "R6 violation: reference/schema.sql review_log table missing "
                        "'rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4)'"
                    )
        except Exception as e:
            violations.append(f"R6 fail-closed: Failed to read schema file: {e}")

    # 2. Scan every Python file under app/ for SQL UPDATE or DELETE on review_log
    try:
        app_files = get_app_python_files(repo_root)
        mutation_pattern = re.compile(
            r"\b(UPDATE\s+review_log|DELETE\s+FROM\s+review_log)\b",
            re.IGNORECASE,
        )
        for py_file in app_files:
            try:
                content = py_file.read_text(encoding="utf-8")
                matches = mutation_pattern.findall(content)
                if matches:
                    violations.append(
                        f"R6 violation: Forbidden SQL mutation on review_log ({matches[0]}) "
                        f"in {py_file}"
                    )
            except Exception as e:
                violations.append(f"R6 fail-closed: Failed to read {py_file}: {e}")
    except Exception as e:
        violations.append(f"R6 fail-closed: {e}")

    return violations


def check_r7(repo_root: Path) -> list[str]:
    """Check AGENTS Rule R7: Zero coupling to the lecture app.

    Scans pyproject.toml runtime dependencies and app/ imports.
    """
    violations: list[str] = []

    # 1. Check runtime dependencies in pyproject.toml
    try:
        pyproject_data = parse_pyproject(repo_root)
        runtime_deps = get_runtime_dependencies(pyproject_data)
        for dep in runtime_deps:
            if is_forbidden_lecture_package(dep):
                violations.append(
                    f"R7 violation: Forbidden lecture-app dependency '{dep}' in pyproject.toml"
                )
    except Exception as e:
        violations.append(f"R7 fail-closed: {e}")

    # 2. Check imports in app/
    try:
        app_files = get_app_python_files(repo_root)
        for py_file in app_files:
            try:
                imported_modules = collect_imports_from_file(py_file)
                for mod in imported_modules:
                    if is_forbidden_lecture_module(mod):
                        violations.append(
                            f"R7 violation: Forbidden lecture-app import '{mod}' in {py_file}"
                        )
            except Exception as e:
                violations.append(f"R7 fail-closed: {e}")
    except Exception as e:
        violations.append(f"R7 fail-closed: {e}")

    return violations


def check_r12(repo_root: Path) -> list[str]:
    """Check AGENTS Rule R12: Browser-facing localhost requests are origin/host guarded.

    Enforces:
    1. create_app rejects wildcard '*' cors origins at creation.
    2. app/api.py has structural host-and-origin security middleware.
    3. X-Flashcards-Request guard covers every non-GET /vocab route by parsing the route table.
    """
    violations: list[str] = []
    api_path = repo_root / "app" / "api.py"
    if not api_path.exists() or not api_path.is_file():
        violations.append(f"R12 fail-closed: Required API file missing: {api_path}")
        return violations

    try:
        source = api_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(api_path))
    except Exception as e:
        violations.append(f"R12 fail-closed: Failed to read/parse {api_path}: {e}")
        return violations

    # 1. Wildcard origin rejection check in create_app
    has_wildcard_rejection = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "create_app":
            func_segment = ast.get_source_segment(source, node) or ""
            if (
                re.search(r'["\']\*["\']\s+in\b', func_segment)
                or re.search(r'==\s+["\']\*["\']', func_segment)
                or re.search(r"wildcard origin is forbidden", func_segment, re.IGNORECASE)
            ):
                has_wildcard_rejection = True
            break

    if not has_wildcard_rejection:
        violations.append(
            "R12 violation: create_app does not reject wildcard '*' in cors_origins"
        )

    # 2. Structural host-and-origin middleware check
    has_host_guard = (
        "_is_loopback_host" in source
        or ("127.0.0.1" in source and "localhost" in source)
    )
    has_origin_guard = "cors_origins" in source and "origin" in source.lower()
    has_middleware_registration = (
        "add_middleware" in source and "BrowserSecurityMiddleware" in source
    )
    if not (has_host_guard and has_origin_guard and has_middleware_registration):
        violations.append(
            "R12 violation: app/api.py missing structural host/origin security middleware"
        )

    # 3. Route table parsing and X-Flashcards-Request guard coverage
    routes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    func = dec.func
                    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                        if func.value.id == "app" and func.attr in (
                            "get",
                            "post",
                            "delete",
                            "put",
                            "patch",
                        ):
                            method = func.attr.upper()
                            if dec.args and isinstance(dec.args[0], ast.Constant):
                                path_val = str(dec.args[0].value)
                                routes.append((method, path_val))

    non_get_vocab_routes = [
        (m, p) for m, p in routes if p.startswith("/vocab") and m != "GET"
    ]
    if not non_get_vocab_routes:
        violations.append("R12 violation: No non-GET /vocab routes found in app/api.py")
    else:
        has_custom_header_guard = (
            "x-flashcards-request" in source.lower()
            and re.search(
                r'path\.startswith\(["\']/vocab["\']\)\s+and\s+request\.method\s*!=\s*["\']GET["\']',
                source,
            )
            is not None
        )
        if not has_custom_header_guard:
            violations.append(
                "R12 violation: Non-GET /vocab route table not fully covered by "
                "X-Flashcards-Request guard in middleware"
            )

    return violations


def check_r13(repo_root: Path) -> list[str]:
    """Check AGENTS Rule R13: Dictionary numeric IDs are never durable semantic identity.

    Enforces:
    1. DictionaryRuntime in app/deck.py validates candidate dictionary on activation
       and validates/relinks using durable semantic refs (lemma_semantic_ref, sense_semantic_ref).
    2. app/api.py rejects stale picker asset tokens with HTTP 409 Conflict in note capture.
    """
    violations: list[str] = []

    # 1. Verify app/deck.py activation and relink logic
    deck_path = repo_root / "app" / "deck.py"
    if not deck_path.exists() or not deck_path.is_file():
        violations.append(f"R13 fail-closed: Required deck file missing: {deck_path}")
    else:
        try:
            deck_source = deck_path.read_text(encoding="utf-8")
            has_activate = "def activate_dictionary" in deck_source
            has_candidate_validation = "validate_candidate_dictionary" in deck_source
            has_semantic_ref_relink = (
                "lemma_semantic_ref" in deck_source
                and "sense_semantic_ref" in deck_source
                and "binding_status" in deck_source
            )

            if not (has_activate and has_candidate_validation and has_semantic_ref_relink):
                violations.append(
                    "R13 violation: DictionaryRuntime activation missing candidate validation "
                    "or stable semantic ref relink logic in app/deck.py"
                )
        except Exception as e:
            violations.append(f"R13 fail-closed: Failed to read/parse {deck_path}: {e}")

    # 2. Verify app/api.py stale asset token 409 rejection logic
    api_path = repo_root / "app" / "api.py"
    if not api_path.exists() or not api_path.is_file():
        violations.append(f"R13 fail-closed: Required API file missing: {api_path}")
    else:
        try:
            api_source = api_path.read_text(encoding="utf-8")
            has_token_check = (
                "asset_token" in api_source
                and ("HTTP_409_CONFLICT" in api_source or "409" in api_source)
                and "picker_token != active_token" in api_source
            )
            if not has_token_check:
                violations.append(
                    "R13 violation: app/api.py missing stale-token HTTP 409 rejection logic "
                    "in note capture endpoint"
                )
        except Exception as e:
            violations.append(f"R13 fail-closed: Failed to read/parse {api_path}: {e}")

    return violations


def check_all(repo_root: Path) -> list[str]:
    """Run all scaffolded executable rule checks (R1, R3, R6, R7, R12, R13, M2)."""
    violations: list[str] = []
    violations.extend(check_r1(repo_root))
    violations.extend(check_r3(repo_root))
    violations.extend(check_r6(repo_root))
    violations.extend(check_r7(repo_root))
    violations.extend(check_r12(repo_root))
    violations.extend(check_r13(repo_root))
    violations.extend(check_m2_identity(repo_root))
    return violations


def check_m2_identity(repo_root: Path) -> list[str]:
    """Check the M2 duplicate-safe note identity contract.

    Enforces:
    1. reference/schema.sql PART-B defines the partial UNIQUE
       ``ux_note_dictionary_key`` index over ``note(dictionary_key)``
       restricted to non-NULL keys, and stamps fresh databases at
       ``PRAGMA user_version = 2`` (the M4 folder schema step is applied on
       top of the immutable M2 identity invariant).
    2. Product app/api.py performs no direct ``create_note(...)`` call;
       every product note-creation path goes through the shared
       ``find_or_create_note`` domain operation in app/deck.py.
    3. review_log UPDATE/DELETE remains forbidden under R6 (re-asserted
       here so the identity work cannot smuggle a history rewrite).
    """
    violations: list[str] = []

    schema_path = repo_root / "reference" / "schema.sql"
    if not schema_path.exists() or not schema_path.is_file():
        violations.append(f"M2 fail-closed: Missing required schema file: {schema_path}")
    else:
        try:
            schema_sql = schema_path.read_text(encoding="utf-8")
            partial_unique = re.search(
                r"CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                r"ux_note_dictionary_key\s+ON\s+note\s*\(\s*dictionary_key\s*\)"
                r"\s+WHERE\s+dictionary_key\s+IS\s+NOT\s+NULL",
                schema_sql,
                re.IGNORECASE | re.DOTALL,
            )
            if not partial_unique:
                violations.append(
                    "M2 violation: reference/schema.sql missing the partial UNIQUE "
                    "ux_note_dictionary_key index on note(dictionary_key) "
                    "WHERE dictionary_key IS NOT NULL"
                )
            version_stamp = re.search(
                r"PRAGMA\s+user_version\s*=\s*2\s*;", schema_sql, re.IGNORECASE
            )
            if not version_stamp:
                violations.append(
                    "M2 violation: reference/schema.sql missing "
                    "'PRAGMA user_version = 2' for fresh PART-B databases"
                )
        except Exception as e:
            violations.append(f"M2 fail-closed: Failed to read schema file: {e}")

    api_path = repo_root / "app" / "api.py"
    if not api_path.exists() or not api_path.is_file():
        violations.append(f"M2 fail-closed: Required API file missing: {api_path}")
    else:
        try:
            api_source = api_path.read_text(encoding="utf-8")
            api_tree = ast.parse(api_source, filename=str(api_path))
            direct_calls = [
                node
                for node in ast.walk(api_tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "create_note"
            ]
            if direct_calls:
                violations.append(
                    "M2 violation: product app/api.py calls create_note(...) directly; "
                    "product note-creation paths must use deck.find_or_create_note"
                )
            if "find_or_create_note" not in api_source:
                violations.append(
                    "M2 violation: product app/api.py does not use "
                    "deck.find_or_create_note"
                )
        except Exception as e:
            violations.append(f"M2 fail-closed: Failed to read/parse {api_path}: {e}")

    deck_path = repo_root / "app" / "deck.py"
    if not deck_path.exists() or not deck_path.is_file():
        violations.append(f"M2 fail-closed: Required deck file missing: {deck_path}")
    else:
        try:
            deck_source = deck_path.read_text(encoding="utf-8")
            if "def find_or_create_note" not in deck_source:
                violations.append(
                    "M2 violation: app/deck.py missing the shared "
                    "find_or_create_note domain operation"
                )
        except Exception as e:
            violations.append(f"M2 fail-closed: Failed to read {deck_path}: {e}")

    try:
        app_files = get_app_python_files(repo_root)
        mutation_pattern = re.compile(
            r"\b(UPDATE\s+review_log|DELETE\s+FROM\s+review_log)\b",
            re.IGNORECASE,
        )
        for py_file in app_files:
            try:
                content = py_file.read_text(encoding="utf-8")
                matches = mutation_pattern.findall(content)
                if matches:
                    violations.append(
                        f"M2 violation: Forbidden SQL mutation on review_log ({matches[0]}) "
                        f"in {py_file}"
                    )
            except Exception as e:
                violations.append(f"M2 fail-closed: Failed to read {py_file}: {e}")
    except Exception as e:
        violations.append(f"M2 fail-closed: {e}")

    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for executable AGENTS checks."""
    args = sys.argv[1:] if argv is None else list(argv)
    repo_root = Path(args[0]) if args else Path.cwd()

    violations = check_all(repo_root)
    if violations:
        sys.stderr.write("AGENTS check failed with the following violations:\n")
        for v in violations:
            sys.stderr.write(f"  - {v}\n")
        return 1

    sys.stdout.write(
        "AGENTS checks passed: R1 (runtime LLM), R3 (resolver cache key), "
        "R6 (review log append-only), R7 (lecture coupling), "
        "R12 (browser origin/host guards), R13 (durable semantic identity), "
        "M2 (duplicate-safe note identity)\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

