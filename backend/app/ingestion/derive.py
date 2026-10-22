import ast
import logging
import os
import re
import uuid
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.core.db import get_db
from app.core.repositories import upsert_derived_fact

logger = logging.getLogger("repomentor.ingestion.derive")


class DerivedFact(BaseModel):
    kind: str  # "route", "component", "database", "ml", "entry_point", "env_var", "test"
    name: str
    file_path: str
    line_start: int
    line_end: int
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. Python Route Detector (via AST)
# ---------------------------------------------------------------------------
def detect_python_routes(file_path: str, content: str) -> list[DerivedFact]:
    """
    Detect FastAPI and Flask route decorators using Python's ast module.
    Inspects @router.get, @app.post, and @blueprint.route.
    """
    if not file_path.endswith(".py"):
        return []

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as exc:
        logger.debug("Syntax error in %s: %s", file_path, exc)
        return []

    facts: list[DerivedFact] = []
    http_verbs = {"get", "post", "put", "delete", "patch", "options", "head"}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue

            method = func.attr.lower()
            route_path = None
            if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
                route_path = decorator.args[0].value

            if not route_path:
                continue

            line_start = decorator.lineno
            line_end = node.end_lineno or node.lineno

            # FastAPI / Flask verb decorators: @app.get('/path'), @router.post('/path')
            if method in http_verbs:
                facts.append(
                    DerivedFact(
                        kind="route",
                        name=f"{method.upper()} {route_path}",
                        file_path=file_path,
                        line_start=line_start,
                        line_end=line_end,
                        details={
                            "framework": "fastapi/flask",
                            "method": method.upper(),
                            "path": route_path,
                            "handler": node.name,
                        },
                    )
                )
            # Flask @app.route('/path', methods=['GET', 'POST'])
            elif method == "route":
                methods = ["GET"]
                for kw in decorator.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [
                            elt.value for elt in kw.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]
                for m in methods:
                    facts.append(
                        DerivedFact(
                            kind="route",
                            name=f"{m.upper()} {route_path}",
                            file_path=file_path,
                            line_start=line_start,
                            line_end=line_end,
                            details={
                                "framework": "flask",
                                "method": m.upper(),
                                "path": route_path,
                                "handler": node.name,
                            },
                        )
                    )

    return facts


# ---------------------------------------------------------------------------
# 2. JS / TS Express Routes & React Components (via Regex)
# ---------------------------------------------------------------------------
def _is_react_candidate(file_path: str, content: str) -> bool:
    ext = os.path.splitext(file_path.lower())[1]
    if ext in {".jsx", ".tsx"}:
        return True
    if ext in {".js", ".ts", ".mjs"}:
        has_react_import = bool(
            re.search(r'''(?:from\s+['"]react['"]|require\(\s*['"]react['"]\))''', content)
        )
        has_jsx = bool(re.search(r"(?:<[A-Za-z][A-Za-z0-9_]*[\s/>]|</[A-Za-z]|<>)", content))
        return has_react_import or has_jsx
    return False


def detect_js_ts_routes_and_components(file_path: str, content: str) -> list[DerivedFact]:
    """
    Detect Express routes and React component exports in JavaScript/TypeScript files.
    """
    ext = os.path.splitext(file_path.lower())[1]
    if ext not in {".js", ".jsx", ".ts", ".tsx", ".mjs"}:
        return []

    facts: list[DerivedFact] = []

    # Express routes: (app|router).(get|post|put|delete|patch|use)('/path', ...)
    # Escapes the dot and requires the first argument to be a string literal starting with '/'
    route_pattern = re.compile(
        r"(?<![.\w])(?:app|router)\.(get|post|put|delete|patch|use)\s*\(\s*['\"`](/[^'\"`]*)['\"`]",
        re.IGNORECASE,
    )
    for match in route_pattern.finditer(content):
        verb, route_path = match.group(1).upper(), match.group(2)
        line_num = content[: match.start()].count("\n") + 1
        facts.append(
            DerivedFact(
                kind="route",
                name=f"{verb} {route_path}",
                file_path=file_path,
                line_start=line_num,
                line_end=line_num,
                details={"framework": "express", "method": verb, "path": route_path},
            )
        )

    # React components: exported functions or classes starting with a Capital letter
    # Detected only in .jsx/.tsx files, or .js/.ts files containing JSX or an import from "react"
    if _is_react_candidate(file_path, content):
        comp_patterns = [
            re.compile(r"export\s+default\s+function\s+([A-Z][a-zA-Z0-9_]*)"),
            re.compile(r"export\s+function\s+([A-Z][a-zA-Z0-9_]*)"),
            re.compile(r"export\s+const\s+([A-Z][a-zA-Z0-9_]*)\s*[:=]\s*(?!['\"0-9]|true\b|false\b|null\b)"),
            re.compile(r"export\s+class\s+([A-Z][a-zA-Z0-9_]*)"),
            re.compile(r"export\s+default\s+class\s+([A-Z][a-zA-Z0-9_]*)"),
            re.compile(r"^export\s+default\s+([A-Z][a-zA-Z0-9_]*)\s*;?$", re.MULTILINE),
        ]

        for pat in comp_patterns:
            for match in pat.finditer(content):
                comp_name = match.group(1)
                # Skip SCREAMING_SNAKE_CASE constants like API_URL
                if "_" in comp_name and comp_name.isupper():
                    continue
                line_num = content[: match.start()].count("\n") + 1
                facts.append(
                    DerivedFact(
                        kind="component",
                        name=comp_name,
                        file_path=file_path,
                        line_start=line_num,
                        line_end=line_num,
                        details={"framework": "react", "component": comp_name},
                    )
                )

    return facts


# ---------------------------------------------------------------------------
# 3. Database Usage Detector
# ---------------------------------------------------------------------------
def detect_database_usage(file_path: str, content: str) -> list[DerivedFact]:
    """
    Detect database library imports or connection strings (mongodb, motor, sqlalchemy, sqlite3, prisma, etc.).
    """
    facts: list[DerivedFact] = []
    db_patterns = [
        ("motor", re.compile(r"\b(motor\.motor_asyncio|from\s+motor\b|import\s+motor\b)")),
        ("pymongo", re.compile(r"\b(import\s+pymongo\b|from\s+pymongo\b)")),
        ("mongoose", re.compile(r"\b(require\(['\"]mongoose['\"]|from\s+['\"]mongoose['\"])")),
        ("mongodb_conn", re.compile(r"mongodb(?:\+srv)?://[^\s'\"`]+")),
        ("sqlalchemy", re.compile(r"\b(import\s+sqlalchemy\b|from\s+sqlalchemy\b)")),
        ("psycopg2", re.compile(r"\b(import\s+psycopg2\b|from\s+psycopg2\b)")),
        ("sqlite3", re.compile(r"\b(import\s+sqlite3\b|from\s+sqlite3\b|sqlite:///[^\s'\"`]+)")),
        ("prisma", re.compile(r'(?:@prisma/client|from\s+prisma\b|import\s+prisma\b)')),
    ]

    for line_idx, line in enumerate(content.splitlines(), start=1):
        for name, pattern in db_patterns:
            if pattern.search(line):
                facts.append(
                    DerivedFact(
                        kind="database",
                        name=name,
                        file_path=file_path,
                        line_start=line_idx,
                        line_end=line_idx,
                        details={"matched_line": line.strip()},
                    )
                )

    return facts


# ---------------------------------------------------------------------------
# 4. Machine Learning (ML) Detector
# ---------------------------------------------------------------------------
def detect_ml_usage(file_path: str, content: str | None) -> list[DerivedFact]:
    """
    Detect ML framework imports (torch, tensorflow, sklearn, transformers) and model weights files.
    """
    facts: list[DerivedFact] = []
    _, ext = os.path.splitext(file_path.lower())

    # Model weight files
    if ext in {".pt", ".pth", ".h5", ".pkl", ".onnx"}:
        facts.append(
            DerivedFact(
                kind="ml",
                name=f"model_artifact:{os.path.basename(file_path)}",
                file_path=file_path,
                line_start=1,
                line_end=1,
                details={"file_type": "model_weights", "extension": ext},
            )
        )

    # In-code imports
    if content:
        ml_patterns = [
            ("torch", re.compile(r"\b(import\s+torch\b|from\s+torch\b)")),
            ("tensorflow", re.compile(r"\b(import\s+tensorflow\b|from\s+tensorflow\b)")),
            ("sklearn", re.compile(r"\b(import\s+sklearn\b|from\s+sklearn\b)")),
            ("transformers", re.compile(r"\b(import\s+transformers\b|from\s+transformers\b)")),
        ]
        for line_idx, line in enumerate(content.splitlines(), start=1):
            for name, pattern in ml_patterns:
                if pattern.search(line):
                    facts.append(
                        DerivedFact(
                            kind="ml",
                            name=name,
                            file_path=file_path,
                            line_start=line_idx,
                            line_end=line_idx,
                            details={"import_statement": line.strip()},
                        )
                    )

    return facts


# ---------------------------------------------------------------------------
# 5. Entry Points Detector
# ---------------------------------------------------------------------------
def detect_entry_points(file_path: str, content: str | None) -> list[DerivedFact]:
    """
    Detect application entry points (main.py, app.py, index.js, uvicorn.run, app.listen).
    Skips files under tests/, test/, __tests__/, and node_modules/.
    """
    norm_path = file_path.replace("\\", "/").lower()
    parts = set(norm_path.split("/"))
    if parts & {"tests", "test", "__tests__", "node_modules"}:
        return []

    facts: list[DerivedFact] = []
    filename = os.path.basename(file_path).lower()

    # Standard entry point filenames
    if filename in {"main.py", "app.py", "index.js", "server.js", "index.ts", "server.ts"}:
        facts.append(
            DerivedFact(
                kind="entry_point",
                name=filename,
                file_path=file_path,
                line_start=1,
                line_end=1,
                details={"type": "standard_filename"},
            )
        )

    # Runtime server start markers in code
    if content:
        server_patterns = [
            ("uvicorn.run", re.compile(r"\buvicorn\.run\s*\(")),
            ("app.listen", re.compile(r"\bapp\.listen\s*\(")),
        ]
        for line_idx, line in enumerate(content.splitlines(), start=1):
            for name, pattern in server_patterns:
                if pattern.search(line):
                    facts.append(
                        DerivedFact(
                            kind="entry_point",
                            name=name,
                            file_path=file_path,
                            line_start=line_idx,
                            line_end=line_idx,
                            details={"matched_statement": line.strip()},
                        )
                    )

    return facts


# ---------------------------------------------------------------------------
# 6. Environment Variables Detector (Names Only)
# ---------------------------------------------------------------------------
def detect_env_vars(file_path: str, content: str) -> list[DerivedFact]:
    """
    Extract environment variable names accessed in Python and JS/TS code.
    Records ONLY names, never values. Supports uppercase, lowercase, and mixed-case identifiers.
    """
    facts: list[DerivedFact] = []
    env_patterns = [
        re.compile(r"os\.getenv\(\s*['\"]([a-zA-Z0-9_]+)['\"]"),
        re.compile(r"os\.environ\[\s*['\"]([a-zA-Z0-9_]+)['\"]"),
        re.compile(r"os\.environ\.get\(\s*['\"]([a-zA-Z0-9_]+)['\"]"),
        re.compile(r"process\.env\.([a-zA-Z0-9_]+)"),
        re.compile(r"process\.env\[\s*['\"]([a-zA-Z0-9_]+)['\"]"),
    ]

    for line_idx, line in enumerate(content.splitlines(), start=1):
        for pattern in env_patterns:
            for match in pattern.finditer(line):
                var_name = match.group(1)
                facts.append(
                    DerivedFact(
                        kind="env_var",
                        name=var_name,
                        file_path=file_path,
                        line_start=line_idx,
                        line_end=line_idx,
                        details={"variable_name": var_name},
                    )
                )

    return facts


# ---------------------------------------------------------------------------
# 7. Tests Present Detector
# ---------------------------------------------------------------------------
def detect_tests(file_path: str) -> list[DerivedFact]:
    """
    Identify test files matching standard Python and JS/TS test naming conventions.
    """
    filename = os.path.basename(file_path).lower()
    is_test = (
        (filename.startswith("test_") and filename.endswith(".py"))
        or filename.endswith("_test.py")
        or filename.endswith((".test.js", ".test.ts", ".test.jsx", ".test.tsx", ".spec.js", ".spec.ts"))
    )

    if is_test:
        return [
            DerivedFact(
                kind="test",
                name=filename,
                file_path=file_path,
                line_start=1,
                line_end=1,
                details={"test_file": file_path},
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Aggregator & Orchestrator
# ---------------------------------------------------------------------------
def derive_file_facts(file_path: str, content: str | None) -> list[DerivedFact]:
    """
    Execute all detectors on a single file and return aggregated derived facts.
    """
    facts: list[DerivedFact] = []

    # File path only detectors
    facts.extend(detect_tests(file_path))

    # Path + optional content detectors
    facts.extend(detect_ml_usage(file_path, content))
    facts.extend(detect_entry_points(file_path, content))

    # Content-based detectors
    if content:
        facts.extend(detect_python_routes(file_path, content))
        facts.extend(detect_js_ts_routes_and_components(file_path, content))
        facts.extend(detect_database_usage(file_path, content))
        facts.extend(detect_env_vars(file_path, content))

    return facts


async def derive_project_facts(
    project_id: str,
    db: AsyncIOMotorDatabase | None = None,
) -> list[dict[str, Any]]:
    """
    Scan all files in the project, run static analysis detectors,
    and persist results idempotently in the 'derived_facts' collection with a run_id.
    After all upserts succeed, safely removes stale facts where run_id != current.
    """
    if db is None:
        db = get_db()

    logger.info("[%s] Deriving static facts from repository files", project_id)
    run_id = str(uuid.uuid4())
    cursor = db.files.find({"project_id": project_id})

    saved_facts: list[dict[str, Any]] = []

    async for file_doc in cursor:
        path = file_doc.get("path", "")
        content = file_doc.get("content")

        facts = derive_file_facts(path, content)
        for fact in facts:
            fact_data = {
                "project_id": project_id,
                "kind": fact.kind,
                "name": fact.name,
                "file_path": fact.file_path,
                "line_start": fact.line_start,
                "line_end": fact.line_end,
                "details": fact.details,
                "run_id": run_id,
            }
            saved = await upsert_derived_fact(db, fact_data)
            saved_facts.append(saved)

    # Only after all upserts succeed, safely remove stale records for this project
    await db.derived_facts.delete_many(
        {
            "project_id": project_id,
            "run_id": {"$ne": run_id},
        }
    )

    logger.info("[%s] Derived and stored %d facts", project_id, len(saved_facts))
    return saved_facts
