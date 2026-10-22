from bson import ObjectId
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.core.db import get_db
from app.main import app


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_analysis_api_db"]


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. 404 Not Found Tests
# ---------------------------------------------------------------------------
def test_analysis_endpoints_404_for_unknown_project(client) -> None:
    unknown_id = str(ObjectId())
    for endpoint in ("/dependencies", "/facts", "/timeline"):
        res = client.get(f"/projects/{unknown_id}{endpoint}")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 2. 409 Conflict Tests (Project not ready)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_analysis_endpoints_409_when_project_not_ready(client, mock_db) -> None:
    project_id = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(project_id),
        "repo_full_name": "owner/repo",
        "status": "ingesting",
    })

    for endpoint in ("/dependencies", "/facts", "/timeline"):
        res = client.get(f"/projects/{project_id}{endpoint}")
        assert res.status_code == 409
        assert "not ready" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. Success Tests (200 OK)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_dependencies_success(client, mock_db) -> None:
    project_id = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(project_id),
        "repo_full_name": "owner/repo",
        "status": "ready",
    })

    await mock_db.dependencies.insert_many([
        {
            "project_id": project_id,
            "name": "fastapi",
            "version_spec": ">=0.115.0",
            "ecosystem": "pypi",
            "kind": "runtime",
            "source_file": "requirements.txt",
        },
        {
            "project_id": project_id,
            "name": "pytest",
            "version_spec": ">=8.0.0",
            "ecosystem": "pypi",
            "kind": "dev",
            "source_file": "requirements-dev.txt",
        },
    ])

    res = client.get(f"/projects/{project_id}/dependencies")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    names = {d["name"]: d["kind"] for d in data}
    assert names["fastapi"] == "runtime"
    assert names["pytest"] == "dev"


@pytest.mark.asyncio
async def test_get_derived_facts_success_and_filtering(client, mock_db) -> None:
    project_id = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(project_id),
        "repo_full_name": "owner/repo",
        "status": "ready",
    })

    await mock_db.derived_facts.insert_many([
        {
            "project_id": project_id,
            "kind": "route",
            "name": "GET /users",
            "file_path": "app/main.py",
            "line_start": 10,
            "line_end": 12,
            "details": {"method": "GET"},
        },
        {
            "project_id": project_id,
            "kind": "route",
            "name": "POST /users",
            "file_path": "app/main.py",
            "line_start": 15,
            "line_end": 18,
            "details": {"method": "POST"},
        },
        {
            "project_id": project_id,
            "kind": "env_var",
            "name": "MONGO_URI",
            "file_path": "app/config.py",
            "line_start": 5,
            "line_end": 5,
            "details": {},
        },
    ])

    # 1. Query all facts
    res_all = client.get(f"/projects/{project_id}/facts")
    assert res_all.status_code == 200
    assert len(res_all.json()) == 3

    # 2. Filter by kind=route
    res_routes = client.get(f"/projects/{project_id}/facts?kind=route")
    assert res_routes.status_code == 200
    assert len(res_routes.json()) == 2
    assert all(f["kind"] == "route" for f in res_routes.json())

    # 3. Filter by kind=env_var
    res_env = client.get(f"/projects/{project_id}/facts?kind=env_var")
    assert res_env.status_code == 200
    assert len(res_env.json()) == 1
    assert res_env.json()[0]["name"] == "MONGO_URI"

    # 4. Pagination: limit=1, skip=1
    res_paginated = client.get(f"/projects/{project_id}/facts?limit=1&skip=1")
    assert res_paginated.status_code == 200
    assert len(res_paginated.json()) == 1


@pytest.mark.asyncio
async def test_get_timeline_success(client, mock_db) -> None:
    project_id = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(project_id),
        "repo_full_name": "owner/repo",
        "status": "ready",
        "ingest_cap": 500,
    })

    timeline_doc = {
        "project_id": project_id,
        "weekly_buckets": [
            {
                "week_start": "2026-10-19T00:00:00+00:00",
                "commit_count": 5,
                "additions": 120,
                "deletions": 30,
                "top_files": [{"file": "app/main.py", "touches": 4}],
            }
        ],
        "biggest_commits": [
            {
                "sha": "abc1234",
                "date": "2026-10-20T10:00:00Z",
                "author": "Alice",
                "message": "Major feature",
                "additions": 100,
                "deletions": 20,
            }
        ],
        "dependency_first_seen": {"package.json": {"sha": "abc1234", "date": "2026-10-20T10:00:00Z"}},
        "dir_first_seen": {"app": {"sha": "abc1234", "date": "2026-10-20T10:00:00Z"}},
        "ingest_cap": 500,
        "history_complete": True,
    }
    await mock_db.timeline.insert_one(timeline_doc)

    res = client.get(f"/projects/{project_id}/timeline")
    assert res.status_code == 200
    data = res.json()
    assert data["project_id"] == project_id
    assert len(data["weekly_buckets"]) == 1
    assert data["weekly_buckets"][0]["commit_count"] == 5
    assert len(data["biggest_commits"]) == 1
    assert data["biggest_commits"][0]["sha"] == "abc1234"
    assert data["history_complete"] is True
