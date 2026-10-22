from unittest.mock import AsyncMock, patch
from bson import ObjectId
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.core.db import get_db
from app.core.repositories import init_indexes
from app.main import app


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    db = client["test_api_db"]
    return db


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_create_project_invalid_urls(client) -> None:
    # Empty URL
    res = client.post("/projects", json={"repo_url": ""})
    assert res.status_code == 400
    assert "cannot be empty" in res.json()["detail"]

    # Incomplete URL without repo name
    res = client.post("/projects", json={"repo_url": "https://github.com/single_part"})
    assert res.status_code == 400
    assert "Invalid GitHub repository URL" in res.json()["detail"]

    # Invalid special characters
    res = client.post("/projects", json={"repo_url": "https://github.com/invalid$/repo!"})
    assert res.status_code == 400
    assert "Invalid characters" in res.json()["detail"]


@patch("app.api.projects.ingest_project", new_callable=AsyncMock)
def test_create_and_reuse_project(mock_ingest, client, mock_db) -> None:
    # 1. Create project
    res = client.post("/projects", json={"repo_url": "https://github.com/octocat/Hello-World.git"})
    assert res.status_code == 202
    data = res.json()
    project_id = data["project_id"]
    assert project_id is not None
    assert data["status"] == "queued"

    # Background task should be scheduled
    assert mock_ingest.called

    # 2. Reuse existing project
    res_reuse = client.post("/projects", json={"repo_url": "octocat/Hello-World"})
    assert res_reuse.status_code == 202
    assert res_reuse.json()["project_id"] == project_id


def test_get_project_not_found(client) -> None:
    random_id = str(ObjectId())
    res = client.get(f"/projects/{random_id}")
    assert res.status_code == 404
    assert f"Project with ID '{random_id}' not found" in res.json()["detail"]


@pytest.mark.asyncio
async def test_get_project_detail_with_counts(client, mock_db) -> None:
    await init_indexes(mock_db)

    # Insert a sample project
    proj_res = await mock_db.projects.insert_one(
        {
            "repo_full_name": "tiangolo/fastapi",
            "default_branch": "master",
            "status": "ready",
            "ingest_cap": 500,
            "head_sha": "sha_head",
            "analysed_at": "2026-10-22T01:00:00Z",
            "error": None,
        }
    )
    project_id = str(proj_res.inserted_id)

    # Seed related collection documents
    await mock_db.files.insert_many([
        {"project_id": project_id, "path": "README.md"},
        {"project_id": project_id, "path": "main.py"},
    ])
    await mock_db.commits.insert_many([
        {"project_id": project_id, "sha": "c1"},
        {"project_id": project_id, "sha": "c2"},
        {"project_id": project_id, "sha": "c3"},
    ])
    await mock_db.prs.insert_one({"project_id": project_id, "number": 1})
    await mock_db.issues.insert_many([
        {"project_id": project_id, "number": 10},
        {"project_id": project_id, "number": 11},
    ])

    res = client.get(f"/projects/{project_id}")
    assert res.status_code == 200
    data = res.json()

    assert data["id"] == project_id
    assert data["repo_full_name"] == "tiangolo/fastapi"
    assert data["status"] == "ready"
    assert data["counts"]["files"] == 2
    assert data["counts"]["commits"] == 3
    assert data["counts"]["prs"] == 1
    assert data["counts"]["issues"] == 2


@pytest.mark.asyncio
async def test_list_projects(client, mock_db) -> None:
    await init_indexes(mock_db)

    await mock_db.projects.insert_many([
        {"repo_full_name": "pallets/flask", "status": "ready"},
        {"repo_full_name": "django/django", "status": "queued"},
    ])

    res = client.get("/projects")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    names = [item["repo_full_name"] for item in items]
    assert names == ["django/django", "pallets/flask"]
