"""Tests for Evidence API and Static Dossier endpoints."""

from __future__ import annotations

from bson import ObjectId
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.core.db import get_db
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


async def _setup_test_project(mock_db, pid: str, status: str = "ready"):
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid),
        "status": status,
        "repo_full_name": f"owner/repo_{pid[:6]}",
        "default_branch": "main",
    })


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
    # Context lines: 2 before (8, 9), 10..15 in range, 2 after (16, 17) -> lines 8..17 (10 lines total)
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
    # Context: 8, 9, 10, 11, 12
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
        "path": "long.py",
        "content": content,
    })

    # Request 80 lines: #L5-85
    res = client.get(f"/projects/{pid}/evidence", params={"id": "file:long.py#L5-85"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["lines"]) == 60
    assert data["lines"][0]["n"] == 3  # context start (5 - 2 = 3)
    assert data["lines"][-1]["n"] == 62  # 3 + 59 = 62


@pytest.mark.asyncio
async def test_evidence_file_without_content(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    await mock_db.files.insert_one({
        "project_id": pid,
        "path": "large_binary.dat",
        "content": None,
    })

    res = client.get(f"/projects/{pid}/evidence", params={"id": "file:large_binary.dat#L1-10"})
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "file"
    assert data["path"] == "large_binary.dat"
    assert data["available"] is False
    assert data["reason"] == "content was not stored"


# ---------------------------------------------------------------------------
# 2. Evidence: Commit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_commit_good_and_missing(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    long_msg = "feat: add feature\n\n" + ("detail " * 100)
    await mock_db.commits.insert_one({
        "project_id": pid,
        "sha": "a1b2c3d4e5f67890abcdef",
        "date": "2026-11-20T10:00:00Z",
        "author": "Alice",
        "message": long_msg,
        "additions": 42,
        "deletions": 5,
        "files_changed": ["app/main.py", "app/utils.py"],
    })

    # Good request without path: commit:<sha7>
    res1 = client.get(f"/projects/{pid}/evidence?id=commit:a1b2c3d")
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["kind"] == "commit"
    assert d1["sha"] == "a1b2c3d4e5f67890abcdef"
    assert d1["author"] == "Alice"
    assert len(d1["message"]) <= 500
    assert d1["additions"] == 42
    assert d1["deletions"] == 5
    assert d1["files_changed"] == ["app/main.py", "app/utils.py"]
    assert "path" not in d1

    # Good request with path: commit:<sha7>:<path>
    res2 = client.get(f"/projects/{pid}/evidence?id=commit:a1b2c3d:app/main.py")
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["path"] == "app/main.py"
    assert d2["path_in_commit"] is True

    # Path not in commit
    res3 = client.get(f"/projects/{pid}/evidence?id=commit:a1b2c3d:other.py")
    assert res3.status_code == 200
    d3 = res3.json()
    assert d3["path"] == "other.py"
    assert d3["path_in_commit"] is False

    # Missing commit
    res_missing = client.get(f"/projects/{pid}/evidence?id=commit:9999999")
    assert res_missing.status_code == 404
    assert "not found" in res_missing.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Evidence: Dependency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_dep_good_and_missing(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    await mock_db.dependencies.insert_one({
        "project_id": pid,
        "name": "fastapi",
        "version_spec": ">=0.100.0",
        "kind": "runtime",
        "source_file": "pyproject.toml",
    })

    res = client.get(f"/projects/{pid}/evidence?id=dep:fastapi")
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "dependency"
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "fastapi"
    assert data["items"][0]["version_spec"] == ">=0.100.0"
    assert data["items"][0]["dep_kind"] == "runtime"
    assert data["items"][0]["source_file"] == "pyproject.toml"

    # Missing
    res_m = client.get(f"/projects/{pid}/evidence?id=dep:missing-package")
    assert res_m.status_code == 404


# ---------------------------------------------------------------------------
# 4. Evidence: Fact (no env var values leaked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_fact_good_missing_and_no_env_leaks(client, mock_db, pid):
    await _setup_test_project(mock_db, pid)

    await mock_db.derived_facts.insert_one({
        "project_id": pid,
        "kind": "database",
        "name": "supabase",
        "file_path": "tests/test_db.py",
        "line_start": 5,
        "line_end": 10,
        "in_test": True,
        "details": {"env_key": "SECRET_PASSWORD_123", "url": "postgres://user:pass@host"},
    })
    await mock_db.derived_facts.insert_one({
        "project_id": pid,
        "kind": "database",
        "name": "supabase",
        "file_path": "app/db.py",
        "line_start": 20,
        "line_end": 25,
        "in_test": False,
        "details": {"env_secret": "API_SECRET_XYZ"},
    })

    res = client.get(f"/projects/{pid}/evidence?id=fact:database:supabase")
    assert res.status_code == 200
    data = res.json()
    assert data["kind"] == "fact"
    items = data["items"]
    assert len(items) == 2
    # Non-test items come first!
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
# 5. Evidence: Repo
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


# ---------------------------------------------------------------------------
# 6. Evidence: Unsupported Format & Unknown Project
# ---------------------------------------------------------------------------

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
# 7. Static Dossier POST: Keep non-static claims & Increment version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_static_dossier_endpoint(client, mock_db, pid):
    # 1. Project not ready -> 409
    await _setup_test_project(mock_db, pid, status="ingesting")
    res_not_ready = client.post(f"/projects/{pid}/dossier/static")
    assert res_not_ready.status_code == 409

    # 2. Unknown project -> 404
    unknown_id = str(ObjectId())
    res_unknown = client.post(f"/projects/{unknown_id}/dossier/static")
    assert res_unknown.status_code == 404

    # 3. Set project to ready and seed existing dossier with agent and user claims
    await mock_db.projects.update_one({"_id": ObjectId(pid)}, {"$set": {"status": "ready"}})
    await mock_db.files.insert_one({"project_id": pid, "path": "main.py", "language": "python"})

    # Existing dossier version 1 with an agent claim and a user claim
    await mock_db.dossiers.insert_one({
        "project_id": pid,
        "version": 1,
        "sections": {
            "technologies": [
                {"claim": "Old static claim", "tag": "fact", "source": "static", "evidence": []},
                {"claim": "Agent inferred caching", "tag": "inferred", "source": "agent", "evidence": []},
                {"claim": "User confirmed Redis", "tag": "fact", "source": "user", "evidence": []},
            ],
            "decisions": [
                {"claim": "User decision note", "tag": "inferred", "source": "user", "evidence": []},
            ],
        },
    })

    # Post to static dossier endpoint
    res = client.post(f"/projects/{pid}/dossier/static")
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == 2  # Incremented from 1 to 2
    assert "sections" in data

    # Check sections in response
    tech_sec = next((s for s in data["sections"] if s["name"] == "technologies"), None)
    assert tech_sec is not None
    claims_text = [c["claim"] for c in tech_sec["claims"]]
    assert "Agent inferred caching" in claims_text
    assert "User confirmed Redis" in claims_text
    # The old static claim should have been replaced by freshly generated static claims
    assert "Old static claim" not in claims_text

    dec_sec = next((s for s in data["sections"] if s["name"] == "decisions"), None)
    assert dec_sec is not None
    assert any(c["claim"] == "User decision note" for c in dec_sec["claims"])

    # Verify persisted in database
    doc = await mock_db.dossiers.find_one({"project_id": pid})
    assert doc["version"] == 2


# ---------------------------------------------------------------------------
# 8. GET /projects shows dossier_version and counts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_projects_shows_dossier_version_and_counts(client, mock_db, pid):
    await _setup_test_project(mock_db, pid, status="ready")

    await mock_db.files.insert_one({"project_id": pid, "path": "app.py"})
    await mock_db.commits.insert_one({"project_id": pid, "sha": "c1"})
    await mock_db.dependencies.insert_one({"project_id": pid, "name": "fastapi", "ecosystem": "pip", "source_file": "r.txt"})
    await mock_db.derived_facts.insert_one({"project_id": pid, "kind": "entry_point", "name": "main", "file_path": "app.py", "line_start": 1})
    await mock_db.dossiers.insert_one({"project_id": pid, "version": 3, "sections": {}})

    res = client.get("/projects")
    assert res.status_code == 200
    items = res.json()
    assert len(items) >= 1
    proj = next((p for p in items if p["id"] == pid), None)
    assert proj is not None
    assert proj["dossier_version"] == 3
    assert proj["counts"]["files"] == 1
    assert proj["counts"]["commits"] == 1
    assert proj["counts"]["dependencies"] == 1
    assert proj["counts"]["facts"] == 1
