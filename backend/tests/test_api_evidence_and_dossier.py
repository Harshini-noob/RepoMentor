"""Tests for Evidence API and Static Dossier endpoints."""

from __future__ import annotations

import urllib.parse
from bson import ObjectId
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.core.db import get_db
from app.dossier.builder import build_static_dossier
from app.main import app


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_evidence_dossier_db"]


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def pid():
    return str(ObjectId())


async def _setup_test_project(mock_db, pid: str, status: str = "ready", repo_full_name: str | None = None, project_id: str | None = None):
    canonical_pid = project_id or pid
    repo_name = repo_full_name or f"owner/repo_{pid[:6]}"
    doc = {
        "_id": ObjectId(pid) if ObjectId.is_valid(pid) else pid,
        "project_id": canonical_pid,
        "status": status,
        "repo_full_name": repo_name,
        "default_branch": "main",
    }
    await mock_db.projects.insert_one(doc)
    return doc


# ---------------------------------------------------------------------------
# 1. Evidence: File
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_file_good_and_missing(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    lines_text = "\n".join([f"line {i}" for i in range(1, 31)])
    await mock_db.files.insert_one({
        "project_id": pid,
        "path": "app/main.py",
        "content": lines_text,
        "language": "python",
    })

    # Good request: range #L10-15
    res = client.get(f"/projects/{pid}/evidence", params={"id": "file:app/main.py#L10-15"})
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "file"
    assert data["path"] == "app/main.py"
    assert data["language"] == "python"
    assert data["start"] == 10
    assert data["end"] == 15
    assert len(data["lines"]) == 10
    assert data["lines"][0]["n"] == 8
    assert data["lines"][0]["in_range"] is False
    assert data["lines"][2]["n"] == 10
    assert data["lines"][2]["in_range"] is True
    assert data["lines"][7]["n"] == 15
    assert data["lines"][7]["in_range"] is True
    assert data["lines"][8]["n"] == 16
    assert data["lines"][8]["in_range"] is False

    # Also test #L10 format (single line)
    res_single = client.get(f"/projects/{pid}/evidence", params={"id": "file:app/main.py#L10"})
    assert res_single.status_code == 200
    data_single = res_single.json()
    assert data_single["start"] == 10
    assert data_single["end"] == 10
    assert len(data_single["lines"]) == 5

    # Missing file -> 404
    res_missing = client.get(f"/projects/{pid}/evidence", params={"id": "file:not_found.py#L1-10"})
    assert res_missing.status_code == 404
    assert "not found" in res_missing.json()["detail"].lower()


@pytest.mark.asyncio
async def test_evidence_file_clipping_at_60(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    # 100 lines
    content = "\n".join([f"line {i}" for i in range(1, 101)])
    await mock_db.files.insert_one({
        "project_id": pid,
        "path": "big.py",
        "content": content,
        "language": "python",
    })

    # Request 1 to 100 -> should be clipped to at most 60 lines
    res = client.get(f"/projects/{pid}/evidence", params={"id": "file:big.py#L1-100"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["lines"]) <= 60


@pytest.mark.asyncio
async def test_evidence_file_content_unavailable(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    await mock_db.files.insert_one({
        "project_id": pid,
        "path": "binary.bin",
        "content": None,
        "language": "binary",
    })

    res = client.get(f"/projects/{pid}/evidence", params={"id": "file:binary.bin"})
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "file"
    assert data["path"] == "binary.bin"
    assert data["available"] is False
    assert data["reason"] == "content was not stored"


# ---------------------------------------------------------------------------
# 2. Evidence: Commit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_commit_prefix_and_path(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)
    full_sha = "02a5c7be1d82136a9e144a23a31c5b8826760ef1"
    sha7 = full_sha[:7]

    files_list = [f"file_{i}.py" for i in range(40)]  # 40 files
    files_list.append("logger.py")

    await mock_db.commits.insert_one({
        "project_id": pid,
        "sha": full_sha,
        "date": "2026-10-20T12:00:00Z",
        "author": "Alice",
        "message": "fix: resolve memory leak in logger\n\nDetailed description here...",
        "additions": 42,
        "deletions": 5,
        "files_changed": files_list,
    })

    # Request commit:sha7
    res = client.get(f"/projects/{pid}/evidence", params={"id": f"commit:{sha7}"})
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "commit"
    assert data["sha"] == full_sha
    assert data["author"] == "Alice"
    assert data["additions"] == 42
    assert data["deletions"] == 5
    assert len(data["files_changed"]) == 30  # clipped at 30

    # Request commit:sha7:path (path in commit)
    res_path = client.get(f"/projects/{pid}/evidence", params={"id": f"commit:{sha7}:logger.py"})
    assert res_path.status_code == 200
    data_p = res_path.json()
    assert data_p["path"] == "logger.py"
    assert data_p["path_in_commit"] is True

    # Request commit:sha7:path (path NOT in commit)
    res_no = client.get(f"/projects/{pid}/evidence", params={"id": f"commit:{sha7}:unrelated.go"})
    assert res_no.status_code == 200
    data_no = res_no.json()
    assert data_no["path"] == "unrelated.go"
    assert data_no["path_in_commit"] is False


@pytest.mark.asyncio
async def test_evidence_commit_message_clipped_at_500(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)
    long_msg = "A" * 800
    sha = "1234567890abcdef1234567890abcdef12345678"
    await mock_db.commits.insert_one({
        "project_id": pid,
        "sha": sha,
        "message": long_msg,
    })

    res = client.get(f"/projects/{pid}/evidence", params={"id": f"commit:{sha[:7]}"})
    assert res.status_code == 200
    assert len(res.json()["message"]) == 500


# ---------------------------------------------------------------------------
# 3. Evidence: Dependencies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_dependency_matching(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    await mock_db.dependencies.insert_one({
        "project_id": pid,
        "name": "groq",
        "version_spec": "^0.4.0",
        "kind": "standard",
        "source_file": "pyproject.toml",
    })
    await mock_db.dependencies.insert_one({
        "project_id": pid,
        "name": "groq",
        "version_spec": "==0.4.1",
        "kind": "dev",
        "source_file": "requirements-dev.txt",
    })

    res = client.get(f"/projects/{pid}/evidence?id=dep:groq")
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "dependency"
    assert len(data["items"]) == 2
    names = {item["version_spec"] for item in data["items"]}
    assert "^0.4.0" in names
    assert "==0.4.1" in names

    # Unknown dependency -> 404
    res_m = client.get(f"/projects/{pid}/evidence?id=dep:unknown_pkg")
    assert res_m.status_code == 404


# ---------------------------------------------------------------------------
# 4. Evidence: Fact (no env var leakage, non-test first)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_fact_nontest_first_and_no_leak(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    # 1 test fact
    await mock_db.derived_facts.insert_one({
        "project_id": pid,
        "kind": "database",
        "name": "supabase",
        "file_path": "tests/test_db.py",
        "line_start": 10,
        "line_end": 15,
        "in_test": True,
        "details": {"connection_string": "postgres://user:SECRET_PASSWORD_123@host:5432/db"},
    })

    # 1 non-test fact
    await mock_db.derived_facts.insert_one({
        "project_id": pid,
        "kind": "database",
        "name": "supabase",
        "file_path": "app/db.py",
        "line_start": 20,
        "line_end": 25,
        "in_test": False,
        "details": {"api_key": "API_SECRET_XYZ"},
    })

    res = client.get(f"/projects/{pid}/evidence?id=fact:database:supabase")
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "fact"
    items = data["items"]
    assert len(items) == 2
    assert items[0]["file_path"] == "app/db.py"
    assert items[0]["in_test"] is False
    assert items[1]["file_path"] == "tests/test_db.py"
    assert items[1]["in_test"] is True

    # Crucial security check: NEVER leak env values or details
    raw_text = res.text
    assert "SECRET_PASSWORD_123" not in raw_text
    assert "API_SECRET_XYZ" not in raw_text
    assert "postgres://" not in raw_text
    for it in items:
        assert set(it.keys()) == {"file_path", "line_start", "line_end", "in_test"}

    # Missing fact
    res_m = client.get(f"/projects/{pid}/evidence?id=fact:database:unknown")
    assert res_m.status_code == 404


# ---------------------------------------------------------------------------
# 5. Evidence: Repo & Formats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_repo_good_and_missing(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)
    repo_name = f"owner/repo_{pid[:6]}"

    await mock_db.files.insert_one({"project_id": pid, "path": "a.py", "language": "python"})
    await mock_db.files.insert_one({"project_id": pid, "path": "b.py", "language": "python"})
    await mock_db.files.insert_one({"project_id": pid, "path": "c.ts", "language": "typescript"})
    await mock_db.commits.insert_one({"project_id": pid, "sha": "c1"})

    res = client.get(f"/projects/{pid}/evidence?id=repo:{repo_name}")
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "repo"
    assert data["repo"] == repo_name
    assert data["default_branch"] == "main"
    assert data["languages"]["python"] == 2
    assert data["languages"]["typescript"] == 1
    assert data["commits"] == 1
    assert data["files"] == 3

    # Wrong repo
    res_m = client.get(f"/projects/{pid}/evidence?id=repo:other/unmatched")
    assert res_m.status_code == 404


@pytest.mark.asyncio
async def test_evidence_unsupported_format(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)
    res1 = client.get(f"/projects/{pid}/evidence?id=random_string")
    assert res1.status_code == 400
    assert res1.json()["detail"] == "unsupported evidence id"

    res2 = client.get(f"/projects/{pid}/evidence?id=unknown:format:foo")
    assert res2.status_code == 400
    assert res2.json()["detail"] == "unsupported evidence id"


def test_evidence_unknown_project_gives_404(client):
    unknown_id = str(ObjectId())
    res = client.get(f"/projects/{unknown_id}/evidence", params={"id": "file:main.py#L1-10"})
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 6. Build Static Dossier: API build equals CLI build & Canonical ID resolution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_build_equals_cli_build(client, mock_db):
    canonical_pid = "proj_harshini_test"
    repo_name = "Harshini-noob/llm-cost-copilot"
    await _setup_test_project(mock_db, str(ObjectId()), status="ready", repo_full_name=repo_name, project_id=canonical_pid)

    # Seed rich repository data
    await mock_db.files.insert_one({"project_id": canonical_pid, "path": "main.py", "language": "python"})
    await mock_db.files.insert_one({"project_id": canonical_pid, "path": "README.md", "language": "markdown"})
    await mock_db.dependencies.insert_one({"project_id": canonical_pid, "name": "fastapi", "kind": "runtime", "source_file": "pyproject.toml"})
    await mock_db.dependencies.insert_one({"project_id": canonical_pid, "name": "groq", "kind": "runtime", "source_file": "pyproject.toml"})
    await mock_db.derived_facts.insert_one({"project_id": canonical_pid, "kind": "route", "name": "POST /query", "file_path": "main.py", "line_start": 31, "line_end": 109, "in_test": False, "details": {"handler": "query"}})
    await mock_db.derived_facts.insert_one({"project_id": canonical_pid, "kind": "database", "name": "supabase", "file_path": "db.py", "line_start": 10, "line_end": 20, "in_test": False})
    await mock_db.derived_facts.insert_one({"project_id": canonical_pid, "kind": "test", "name": "test_query", "file_path": "test_main.py", "line_start": 1, "line_end": 10, "in_test": True})
    await mock_db.commits.insert_one({"project_id": canonical_pid, "sha": "1234567890abcdef", "date": "2026-10-20T10:00:00Z", "message": "initial commit", "additions": 100, "deletions": 0})

    # CLI build directly with canonical_pid
    cli_dossier = await build_static_dossier(canonical_pid, mock_db)

    # API build called with repo_name (URL encoded)
    encoded_repo = urllib.parse.quote(repo_name, safe="")
    res = client.post(f"/projects/{encoded_repo}/dossier/static")
    assert res.status_code == 200
    api_resp = res.json()
    assert api_resp["version"] == 1
    assert isinstance(api_resp["sections"], dict)

    # Verify persisted document in MongoDB matches CLI claims
    db_dossier = await mock_db.dossiers.find_one({"project_id": canonical_pid})
    assert db_dossier is not None

    for sec_name in ["identity", "technologies", "apis", "data", "timeline", "quality"]:
        cli_claims = [c.claim for c in cli_dossier.sections.get(sec_name, [])]
        db_claims = [c["claim"] for c in db_dossier["sections"].get(sec_name, [])]
        assert cli_claims == db_claims
        assert api_resp["sections"][sec_name] == len(cli_claims)


@pytest.mark.asyncio
async def test_calling_with_repo_name_or_project_id_writes_same_single_document(client, mock_db):
    canonical_pid = "proj_single_doc"
    repo_name = "test-org/repo-single"
    await _setup_test_project(mock_db, str(ObjectId()), status="ready", repo_full_name=repo_name, project_id=canonical_pid)

    await mock_db.files.insert_one({"project_id": canonical_pid, "path": "index.js", "language": "javascript"})

    # 1. Call with canonical project_id
    res1 = client.post(f"/projects/{canonical_pid}/dossier/static")
    assert res1.status_code == 200

    # Verify exactly 1 dossier exists
    count1 = await mock_db.dossiers.count_documents({})
    assert count1 == 1
    doc1 = await mock_db.dossiers.find_one({})
    assert doc1["project_id"] == canonical_pid

    # 2. Call with URL-encoded repo name and force=True
    encoded_repo = urllib.parse.quote(repo_name, safe="")
    res2 = client.post(f"/projects/{encoded_repo}/dossier/static?force=true")
    assert res2.status_code == 200

    # Still exactly 1 dossier exists, updated to version 2
    count2 = await mock_db.dossiers.count_documents({})
    assert count2 == 1
    doc2 = await mock_db.dossiers.find_one({})
    assert doc2["project_id"] == canonical_pid
    assert doc2["version"] == 2


@pytest.mark.asyncio
async def test_unknown_id_gives_404_and_writes_nothing(client, mock_db):
    res = client.post("/projects/completely_nonexistent_repo/dossier/static")
    assert res.status_code == 404
    assert await mock_db.dossiers.count_documents({}) == 0
    assert await mock_db.dossier_backups.count_documents({}) == 0


@pytest.mark.asyncio
async def test_build_incomplete_gives_409_without_force_and_succeeds_with_force(client, mock_db):
    canonical_pid = "proj_guard_test"
    await _setup_test_project(mock_db, str(ObjectId()), status="ready", project_id=canonical_pid)

    # Existing dossier with 6 static claims
    old_claims = [{"claim": f"Static claim {i}", "tag": "fact", "source": "static", "evidence": []} for i in range(6)]
    await mock_db.dossiers.insert_one({
        "project_id": canonical_pid,
        "version": 1,
        "sections": {
            "technologies": old_claims,
        },
    })

    # The current DB has zero files/facts, so a rebuild will only produce default 4 claims (identity + 3 quality)
    # Wait, 4 claims < 6 / 2 is False (4 is not < 3). Let's make old dossier have 12 static claims!
    old_claims_12 = [{"claim": f"Static claim {i}", "tag": "fact", "source": "static", "evidence": []} for i in range(12)]
    await mock_db.dossiers.update_one(
        {"project_id": canonical_pid},
        {"$set": {"sections.technologies": old_claims_12}},
    )

    # 12 static claims existing -> half is 6. New has ~4 -> 4 < 6 -> triggers incomplete guard!
    res_incomplete = client.post(f"/projects/{canonical_pid}/dossier/static")
    assert res_incomplete.status_code == 409
    err_body = res_incomplete.json()
    assert err_body["detail"] == "new dossier looks incomplete"
    assert err_body["existing"] == 12
    assert err_body["new"] < 6

    # Verify nothing was overwritten
    dossier_unchanged = await mock_db.dossiers.find_one({"project_id": canonical_pid})
    assert dossier_unchanged["version"] == 1

    # Retry with force=True
    res_forced = client.post(f"/projects/{canonical_pid}/dossier/static?force=true")
    assert res_forced.status_code == 200
    assert res_forced.json()["version"] == 2


@pytest.mark.asyncio
async def test_backup_stored_before_overwrite(client, mock_db):
    canonical_pid = "proj_backup_test"
    await _setup_test_project(mock_db, str(ObjectId()), status="ready", project_id=canonical_pid)

    await mock_db.files.insert_one({"project_id": canonical_pid, "path": "main.py", "language": "python"})

    # First build
    res1 = client.post(f"/projects/{canonical_pid}/dossier/static")
    assert res1.status_code == 200
    assert await mock_db.dossier_backups.count_documents({"project_id": canonical_pid}) == 0

    # Overwrite 6 times with force=true
    for _ in range(6):
        res = client.post(f"/projects/{canonical_pid}/dossier/static?force=true")
        assert res.status_code == 200

    # Backups collection must cap at 5 per project
    backups_count = await mock_db.dossier_backups.count_documents({"project_id": canonical_pid})
    assert backups_count == 5


@pytest.mark.asyncio
async def test_agent_and_user_claims_survive_rebuild(client, mock_db):
    canonical_pid = "proj_claims_survival"
    await _setup_test_project(mock_db, str(ObjectId()), status="ready", project_id=canonical_pid)

    await mock_db.files.insert_one({"project_id": canonical_pid, "path": "main.py", "language": "python"})

    # Seed existing dossier with 1 static, 1 agent, 1 user claim
    await mock_db.dossiers.insert_one({
        "project_id": canonical_pid,
        "version": 1,
        "sections": {
            "technologies": [
                {"claim": "Old static claim", "tag": "fact", "source": "static", "evidence": []},
                {"claim": "Agent found caching strategy", "tag": "inferred", "source": "agent", "evidence": []},
                {"claim": "User confirmed PostgreSQL", "tag": "fact", "source": "user", "evidence": []},
            ],
            "decisions": [
                {"claim": "User architecture rationale", "tag": "inferred", "source": "user", "evidence": []},
            ],
        },
    })

    res = client.post(f"/projects/{canonical_pid}/dossier/static?force=true")
    assert res.status_code == 200

    saved = await mock_db.dossiers.find_one({"project_id": canonical_pid})
    assert saved["version"] == 2

    tech_claims = [c["claim"] for c in saved["sections"]["technologies"]]
    assert "Agent found caching strategy" in tech_claims
    assert "User confirmed PostgreSQL" in tech_claims
    assert "Old static claim" not in tech_claims

    dec_claims = [c["claim"] for c in saved["sections"]["decisions"]]
    assert "User architecture rationale" in dec_claims


@pytest.mark.asyncio
async def test_list_projects_returns_project_id(client, mock_db):
    oid = str(ObjectId())
    canonical_pid = "proj_canonical_xyz"
    await _setup_test_project(mock_db, oid, status="ready", project_id=canonical_pid)

    res = client.get("/projects")
    assert res.status_code == 200
    items = res.json()
    assert len(items) >= 1
    proj = next((p for p in items if p["id"] == oid or p["project_id"] == canonical_pid), None)
    assert proj is not None
    assert proj["project_id"] == canonical_pid
