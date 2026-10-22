from mongomock_motor import AsyncMongoMockClient
import pytest
from app.core.repositories import init_indexes
from app.ingestion.derive import (
    derive_file_facts,
    derive_project_facts,
    detect_python_routes,
    detect_js_ts_routes_and_components,
    detect_database_usage,
    detect_ml_usage,
    detect_llm_providers,
    detect_ui_apps,
    detect_entry_points,
    detect_env_vars,
    detect_tests,
    DerivedFact,
)


# ---------------------------------------------------------------------------
# 1. Python AST route detection
# ---------------------------------------------------------------------------
def test_fastapi_routes_detected() -> None:
    code = '''\
from fastapi import APIRouter
router = APIRouter()

@router.get("/users")
async def list_users():
    pass

@router.post("/users")
async def create_user():
    pass
'''
    facts = detect_python_routes("app/users.py", code)
    names = {f.name for f in facts}
    assert "GET /users" in names
    assert "POST /users" in names
    for f in facts:
        assert f.kind == "route"
        assert f.file_path == "app/users.py"
        assert f.line_start >= 1
        assert f.details["framework"] == "fastapi/flask"


def test_flask_route_decorator() -> None:
    code = '''\
from flask import Blueprint
bp = Blueprint("auth", __name__)

@bp.route("/login", methods=["GET", "POST"])
def login():
    return "login"
'''
    facts = detect_python_routes("app/auth.py", code)
    names = {f.name for f in facts}
    assert "GET /login" in names
    assert "POST /login" in names


def test_python_route_skips_non_py_files() -> None:
    facts = detect_python_routes("app/main.js", "@app.get('/x')\ndef x(): pass\n")
    assert facts == []


def test_python_route_malformed_syntax_returns_empty() -> None:
    # Broken syntax — must not raise
    facts = detect_python_routes("app/bad.py", "def broken(:\n")
    assert facts == []


def test_python_route_ignores_decorated_strings() -> None:
    # This is a string, not a real decorator — regex would match, AST must not
    code = 'x = "@app.get(\'/path\')"  # not a decorator\n'
    facts = detect_python_routes("app/foo.py", code)
    assert facts == []


# ---------------------------------------------------------------------------
# 2. JavaScript / TypeScript Express + React
# ---------------------------------------------------------------------------
def test_express_routes_detected() -> None:
    code = '''\
const express = require("express");
const router = express.Router();

router.get("/health", (req, res) => res.json({ok: true}));
app.post("/items", createItem);
'''
    facts = detect_js_ts_routes_and_components("routes/api.js", code)
    route_names = {f.name for f in facts if f.kind == "route"}
    assert "GET /health" in route_names
    assert "POST /items" in route_names


def test_express_routes_filtering() -> None:
    code = '''\
// Real Express route
app.get('/users', handleUsers);

// Non-route calls that must not match
const val = map.get('key');
const env = req.app.get('env');
'''
    facts = detect_js_ts_routes_and_components("routes/users.js", code)
    route_names = {f.name for f in facts if f.kind == "route"}
    assert "GET /users" in route_names
    assert not any("key" in name for name in route_names)
    assert not any("env" in name for name in route_names)
    assert len(route_names) == 1


def test_react_components_detected() -> None:
    code = '''\
import React from "react";

export default function Dashboard({ user }) {
  return <div>{user.name}</div>;
}

export const Avatar = ({ src }) => <img src={src} />;
'''
    facts = detect_js_ts_routes_and_components("components/Dashboard.tsx", code)
    comp_names = {f.name for f in facts if f.kind == "component"}
    assert "Dashboard" in comp_names
    assert "Avatar" in comp_names


def test_react_constant_not_detected_as_component() -> None:
    code = "export const API_URL = 'x';\n"
    # In a JS/TS file without React/JSX
    facts_js = detect_js_ts_routes_and_components("constants.js", code)
    assert [f for f in facts_js if f.kind == "component"] == []

    # Even in a TSX file, API_URL is a string constant, not a component
    facts_tsx = detect_js_ts_routes_and_components("constants.tsx", code)
    assert [f for f in facts_tsx if f.kind == "component"] == []


def test_react_multiline_export_default() -> None:
    code = '''\
import React from "react";

function Header() {
  return <header>Title</header>;
}

export default Header;

// trailing comments
'''
    facts = detect_js_ts_routes_and_components("Header.jsx", code)
    comp_names = {f.name for f in facts if f.kind == "component"}
    assert "Header" in comp_names


def test_js_detector_skips_python_files() -> None:
    facts = detect_js_ts_routes_and_components("app/main.py", "app.get('/x')\n")
    assert facts == []


# ---------------------------------------------------------------------------
# 3. Database usage detection
# ---------------------------------------------------------------------------
def test_database_usage_motor_detected() -> None:
    code = '''\
from motor.motor_asyncio import AsyncIOMotorClient
client = AsyncIOMotorClient("mongodb://localhost:27017")
'''
    facts = detect_database_usage("app/db.py", code)
    kinds = {f.name for f in facts}
    assert "motor" in kinds


def test_database_usage_sqlalchemy_detected() -> None:
    code = "from sqlalchemy import create_engine\n"
    facts = detect_database_usage("app/db.py", code)
    assert any(f.name == "sqlalchemy" for f in facts)


def test_database_usage_conn_string_detected() -> None:
    code = 'MONGO_URL = "mongodb+srv://user:pass@cluster.mongodb.net/db"\n'
    facts = detect_database_usage("config.py", code)
    assert any(f.name == "mongodb_conn" for f in facts)


def test_database_usage_prisma_detected() -> None:
    code = 'import { PrismaClient } from "@prisma/client";\n'
    facts = detect_database_usage("db/client.ts", code)
    assert any(f.name == "prisma" for f in facts)


def test_database_usage_supabase_detected() -> None:
    code = "from supabase import create_client\nclient = create_client('url', 'key')\n"
    facts = detect_database_usage("db/supabase.py", code)
    assert len(facts) >= 1
    assert any(f.name == "supabase" and f.kind == "database" for f in facts)
    assert facts[0].line_start == 1


def test_database_usage_psycopg_and_asyncpg_detected() -> None:
    code = "import psycopg2\nimport asyncpg\nimport psycopg\n"
    facts = detect_database_usage("db/postgres.py", code)
    names = {f.name for f in facts}
    assert "psycopg2" in names
    assert "asyncpg" in names
    assert "psycopg" in names
    assert all(f.kind == "database" for f in facts)


# ---------------------------------------------------------------------------
# 4. ML imports and model files
# ---------------------------------------------------------------------------
def test_ml_torch_import_detected() -> None:
    code = "import torch\nimport torch.nn as nn\n"
    facts = detect_ml_usage("model/train.py", code)
    assert any(f.name == "torch" for f in facts)


def test_ml_sentence_transformers_detected() -> None:
    code = "from sentence_transformers import SentenceTransformer\n"
    facts = detect_ml_usage("embed.py", code)
    assert any(f.name == "sentence_transformers" and f.kind == "ml" for f in facts)


def test_ml_sklearn_detected() -> None:
    code = "from sklearn.linear_model import LogisticRegression\n"
    facts = detect_ml_usage("ml/classifier.py", code)
    assert any(f.name == "sklearn" for f in facts)


def test_ml_model_file_detected() -> None:
    facts = detect_ml_usage("weights/model.pt", None)
    assert len(facts) == 1
    assert facts[0].kind == "ml"
    assert facts[0].details["file_type"] == "model_weights"


def test_ml_onnx_file_detected() -> None:
    facts = detect_ml_usage("deploy/model.onnx", None)
    assert len(facts) == 1


# ---------------------------------------------------------------------------
# 4b. LLM Provider tests
# ---------------------------------------------------------------------------
def test_llm_provider_detection() -> None:
    code = '''\
import openai
from anthropic import Anthropic
from google import genai
from groq import Groq
import cohere
from mistralai import Mistral
import ollama
'''
    facts = detect_llm_providers("llm/providers.py", code)
    names = {f.name for f in facts}
    assert "openai" in names
    assert "anthropic" in names
    assert "google_genai" in names
    assert "groq" in names
    assert "cohere" in names
    assert "mistralai" in names
    assert "ollama" in names
    assert all(f.kind == "llm_provider" for f in facts)
    assert all(f.line_start >= 1 for f in facts)


def test_llm_provider_comment_negative() -> None:
    # Comments mentioning 'groq' must NOT match
    code = '''\
# groq is an ultra-fast LLM inference engine
# from groq import Groq
x = 100  # groq token limit
'''
    facts = detect_llm_providers("notes.py", code)
    assert facts == []


# ---------------------------------------------------------------------------
# 4c. UI App tests
# ---------------------------------------------------------------------------
def test_ui_app_streamlit_and_gradio() -> None:
    code = "import streamlit as st\nimport gradio as gr\n"
    facts = detect_ui_apps("app.py", code)
    names = {f.name for f in facts}
    assert "streamlit" in names
    assert "gradio" in names
    assert all(f.kind == "ui_app" for f in facts)


def test_ui_app_nextjs_and_react_entry() -> None:
    # Next.js App Router entry
    next_facts = detect_ui_apps("app/page.tsx", None)
    assert len(next_facts) == 1
    assert next_facts[0].kind == "ui_app"
    assert next_facts[0].name == "nextjs_page"

    # React App entry
    react_facts = detect_ui_apps("src/App.tsx", None)
    assert len(react_facts) == 1
    assert react_facts[0].kind == "ui_app"
    assert react_facts[0].name == "react_app"


# ---------------------------------------------------------------------------
# 5. Entry points
# ---------------------------------------------------------------------------
def test_standard_entrypoint_filenames() -> None:
    for name in ("main.py", "app.py", "index.js", "server.js"):
        facts = detect_entry_points(f"src/{name}", None)
        assert any(f.kind == "entry_point" for f in facts), f"Missing entry_point for {name}"


def test_uvicorn_run_detected() -> None:
    code = '''\
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
'''
    facts = detect_entry_points("run.py", code)
    assert any(f.name == "uvicorn.run" for f in facts)


def test_app_listen_detected() -> None:
    code = "app.listen(3000, () => console.log('running'));\n"
    facts = detect_entry_points("index.js", code)
    names = {f.name for f in facts}
    assert "app.listen" in names


def test_entry_points_skips_test_and_node_modules() -> None:
    # Files under test directories or node_modules must NOT be flagged as entry points
    assert detect_entry_points("tests/app.py", "app.listen(3000)") == []
    assert detect_entry_points("test/main.py", "uvicorn.run()") == []
    assert detect_entry_points("src/__tests__/server.js", "app.listen(3000)") == []
    assert detect_entry_points("node_modules/express/index.js", None) == []

    # Valid non-test entry point must still be flagged
    assert len(detect_entry_points("src/main.py", None)) == 1


# ---------------------------------------------------------------------------
# 6. Environment variable detection
# ---------------------------------------------------------------------------
def test_env_vars_python_detected() -> None:
    code = '''\
import os
db_url = os.getenv("DATABASE_URL")
secret = os.environ["SECRET_KEY"]
timeout = os.environ.get("TIMEOUT", "30")
'''
    facts = detect_env_vars("config.py", code)
    names = {f.name for f in facts}
    assert "DATABASE_URL" in names
    assert "SECRET_KEY" in names
    assert "TIMEOUT" in names


def test_env_vars_js_detected() -> None:
    code = '''\
const port = process.env.PORT;
const key = process.env["API_KEY"];
'''
    facts = detect_env_vars("server.js", code)
    names = {f.name for f in facts}
    assert "PORT" in names
    assert "API_KEY" in names


def test_env_vars_lowercase_and_mixed_case() -> None:
    code = '''\
const apiKey = process.env.apiKey;
const dbUser = process.env["db_user"];
'''
    py_code = '''\
import os
host = os.getenv("db_host")
pwd = os.environ["secretPass"]
'''
    js_facts = detect_env_vars("app.js", code)
    js_names = {f.name for f in js_facts}
    assert "apiKey" in js_names
    assert "db_user" in js_names

    py_facts = detect_env_vars("config.py", py_code)
    py_names = {f.name for f in py_facts}
    assert "db_host" in py_names
    assert "secretPass" in py_names


# ---------------------------------------------------------------------------
# 7. Test file detection
# ---------------------------------------------------------------------------
def test_test_file_python_detected() -> None:
    facts = detect_tests("tests/test_main.py")
    assert len(facts) == 1
    assert facts[0].kind == "test"


def test_test_file_js_detected() -> None:
    facts = detect_tests("src/App.test.jsx")
    assert len(facts) == 1
    assert facts[0].kind == "test"


def test_non_test_file_not_detected() -> None:
    facts = detect_tests("app/main.py")
    assert facts == []


# ---------------------------------------------------------------------------
# 8. Orchestrator aggregation
# ---------------------------------------------------------------------------
def test_derive_file_facts_aggregate() -> None:
    code = '''\
import os
import streamlit as st
import openai
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

DB_URI = os.getenv("MONGO_URI")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app)
'''
    facts = derive_file_facts("app/main.py", code)
    kinds = {f.kind for f in facts}
    assert "route" in kinds
    assert "database" in kinds
    assert "env_var" in kinds
    assert "entry_point" in kinds
    assert "ui_app" in kinds
    assert "llm_provider" in kinds


def test_manifest_produces_no_llm_fact() -> None:
    assert derive_file_facts("pyproject.toml", 'dependencies = ["google-genai>=1.0"]') == []


def test_test_file_is_flagged() -> None:
    facts = derive_file_facts("test_call.py", "import groq\n")
    llm = [f for f in facts if f.kind == "llm_provider"]
    assert len(llm) == 1 and llm[0].in_test is True


def test_normal_file_not_flagged() -> None:
    facts = derive_file_facts("providers/groq_provider.py", "import groq\n")
    llm = [f for f in facts if f.kind == "llm_provider"]
    assert llm[0].in_test is False


def test_model_weights_still_detected_without_content() -> None:
    facts = derive_file_facts("models/best.pt", None)
    assert any(f.kind == "ml" for f in facts)


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_derive_facts_db"]


@pytest.mark.asyncio
async def test_derive_project_facts_persists_in_test(mock_db) -> None:
    await init_indexes(mock_db)
    project_id = "proj_facts_test_flag"
    await mock_db.files.insert_one({
        "project_id": project_id,
        "path": "tests/test_provider.py",
        "content": "import groq\n",
    })

    facts = await derive_project_facts(project_id, db=mock_db)

    llm = next(f for f in facts if f["kind"] == "llm_provider")
    assert llm["in_test"] is True
    stored = await mock_db.derived_facts.find_one({
        "project_id": project_id,
        "kind": "llm_provider",
    })
    assert stored is not None and stored["in_test"] is True


@pytest.mark.asyncio
async def test_derive_project_facts_stale_cleanup(mock_db) -> None:
    await init_indexes(mock_db)
    project_id = "proj_facts_cleanup"

    # Step 1: File with 2 routes
    code_v1 = '''\
from fastapi import APIRouter
router = APIRouter()

@router.get("/v1/users")
def get_users(): pass

@router.get("/v1/items")
def get_items(): pass
'''
    await mock_db.files.insert_one({
        "project_id": project_id,
        "path": "app/routes.py",
        "content": code_v1,
    })

    initial_facts = await derive_project_facts(project_id, db=mock_db)
    assert len(initial_facts) == 2
    assert await mock_db.derived_facts.count_documents({"project_id": project_id}) == 2

    # Step 2: Remove /v1/items, re-run derive_project_facts
    code_v2 = '''\
from fastapi import APIRouter
router = APIRouter()

@router.get("/v1/users")
def get_users(): pass
'''
    await mock_db.files.update_one(
        {"project_id": project_id, "path": "app/routes.py"},
        {"$set": {"content": code_v2}},
    )

    second_facts = await derive_project_facts(project_id, db=mock_db)
    assert len(second_facts) == 1

    # Assert old /v1/items fact was removed and exactly 1 fact remains
    docs = await mock_db.derived_facts.find({"project_id": project_id}).to_list(length=10)
    assert len(docs) == 1
    assert docs[0]["name"] == "GET /v1/users"


@pytest.mark.asyncio
async def test_derive_project_facts_failure_leaves_old_records(mock_db, monkeypatch) -> None:
    await init_indexes(mock_db)
    project_id = "proj_facts_failure"

    # Initial run with 1 fact
    code_v1 = "import os\nk = os.getenv('INITIAL_VAR')\n"
    await mock_db.files.insert_one({
        "project_id": project_id,
        "path": "app/config.py",
        "content": code_v1,
    })
    initial_facts = await derive_project_facts(project_id, db=mock_db)
    assert len(initial_facts) == 1
    assert await mock_db.derived_facts.count_documents({"project_id": project_id}) == 1

    # Update file with new vars
    code_v2 = "import os\nk = os.getenv('VAR1')\nk2 = os.getenv('VAR2')\n"
    await mock_db.files.update_one(
        {"project_id": project_id, "path": "app/config.py"},
        {"$set": {"content": code_v2}},
    )

    # Monkeypatch upsert_derived_fact to raise an exception mid-run
    from app.ingestion import derive as derive_module
    call_count = 0
    orig_upsert = derive_module.upsert_derived_fact

    async def failing_upsert(db, data):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Database error mid-fact-upsert")
        return await orig_upsert(db, data)

    monkeypatch.setattr(derive_module, "upsert_derived_fact", failing_upsert)

    with pytest.raises(RuntimeError, match="Database error mid-fact-upsert"):
        await derive_project_facts(project_id, db=mock_db)

    # Old records must not have been deleted
    surviving_docs = await mock_db.derived_facts.find({"project_id": project_id}).to_list(length=10)
    assert len(surviving_docs) >= 1
    assert any(d["name"] == "INITIAL_VAR" for d in surviving_docs)
