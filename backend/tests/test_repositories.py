import pytest
from mongomock_motor import AsyncMongoMockClient

from app.core.repositories import (
    init_indexes,
    upsert_commit,
    upsert_file,
    upsert_issue,
    upsert_pr,
    upsert_project,
)


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_repomentor_db"]


@pytest.mark.asyncio
async def test_init_indexes(mock_db) -> None:
    await init_indexes(mock_db)

    # Verify compound unique indexes
    projects_idx = await mock_db.projects.index_information()
    assert "repo_full_name_1" in projects_idx
    assert projects_idx["repo_full_name_1"].get("unique") is True

    files_idx = await mock_db.files.index_information()
    assert "project_id_1_path_1" in files_idx
    assert files_idx["project_id_1_path_1"].get("unique") is True

    commits_idx = await mock_db.commits.index_information()
    assert "project_id_1_sha_1" in commits_idx
    assert commits_idx["project_id_1_sha_1"].get("unique") is True

    prs_idx = await mock_db.prs.index_information()
    assert "project_id_1_number_1" in prs_idx
    assert prs_idx["project_id_1_number_1"].get("unique") is True

    issues_idx = await mock_db.issues.index_information()
    assert "project_id_1_number_1" in issues_idx
    assert issues_idx["project_id_1_number_1"].get("unique") is True


@pytest.mark.asyncio
async def test_upsert_project_idempotent(mock_db) -> None:
    await init_indexes(mock_db)

    # Initial insert
    p1 = await upsert_project(
        mock_db,
        {
            "repo_full_name": "octocat/Hello-World",
            "default_branch": "main",
            "status": "queued",
            "ingest_cap": 100,
            "head_sha": "abc1",
            "analysed_at": None,
            "error": None,
        },
    )
    assert p1["status"] == "queued"

    # Idempotent update
    p2 = await upsert_project(
        mock_db,
        {
            "repo_full_name": "octocat/Hello-World",
            "default_branch": "main",
            "status": "ingesting",
            "ingest_cap": 100,
            "head_sha": "abc2",
            "analysed_at": "2026-10-22T01:00:00Z",
            "error": None,
        },
    )
    assert p2["status"] == "ingesting"
    assert p2["head_sha"] == "abc2"

    count = await mock_db.projects.count_documents({"repo_full_name": "octocat/Hello-World"})
    assert count == 1


@pytest.mark.asyncio
async def test_upsert_file_idempotent(mock_db) -> None:
    await init_indexes(mock_db)

    # Initial file insert
    f1 = await upsert_file(
        mock_db,
        {
            "project_id": "proj_123",
            "path": "app/main.py",
            "size": 120,
            "language": "Python",
            "sha": "sha_file_1",
            "content": "print('hello')",
            "is_config": False,
        },
    )
    assert f1["content"] == "print('hello')"

    # Idempotent update to same path
    f2 = await upsert_file(
        mock_db,
        {
            "project_id": "proj_123",
            "path": "app/main.py",
            "size": 150,
            "language": "Python",
            "sha": "sha_file_2",
            "content": "print('hello world')",
            "is_config": False,
        },
    )
    assert f2["content"] == "print('hello world')"

    count = await mock_db.files.count_documents({"project_id": "proj_123", "path": "app/main.py"})
    assert count == 1


@pytest.mark.asyncio
async def test_upsert_commit_idempotent(mock_db) -> None:
    await init_indexes(mock_db)

    c1 = await upsert_commit(
        mock_db,
        {
            "project_id": "proj_123",
            "sha": "commit_sha_1",
            "date": "2026-10-22T00:00:00Z",
            "author": "Alice",
            "message": "Initial commit",
            "files_changed": 3,
            "additions": 45,
            "deletions": 2,
            "parents": [],
        },
    )
    assert c1["message"] == "Initial commit"

    c2 = await upsert_commit(
        mock_db,
        {
            "project_id": "proj_123",
            "sha": "commit_sha_1",
            "date": "2026-10-22T00:00:00Z",
            "author": "Alice",
            "message": "Initial commit (amended)",
            "files_changed": 4,
            "additions": 50,
            "deletions": 5,
            "parents": [],
        },
    )
    assert c2["message"] == "Initial commit (amended)"

    count = await mock_db.commits.count_documents(
        {"project_id": "proj_123", "sha": "commit_sha_1"}
    )
    assert count == 1


@pytest.mark.asyncio
async def test_upsert_pr_and_issue_idempotent(mock_db) -> None:
    await init_indexes(mock_db)

    # PR test
    pr1 = await upsert_pr(
        mock_db,
        {
            "project_id": "proj_123",
            "number": 1,
            "title": "Add feature",
            "body": "Implements core feature",
            "state": "open",
            "created_at": "2026-10-22T00:00:00Z",
            "merged_at": None,
            "files": ["app/main.py"],
            "comments": [],
        },
    )
    assert pr1["state"] == "open"

    pr2 = await upsert_pr(
        mock_db,
        {
            "project_id": "proj_123",
            "number": 1,
            "title": "Add feature",
            "body": "Implements core feature",
            "state": "closed",
            "created_at": "2026-10-22T00:00:00Z",
            "merged_at": "2026-10-22T01:00:00Z",
            "files": ["app/main.py"],
            "comments": [{"id": 1, "body": "Looks good"}],
        },
    )
    assert pr2["state"] == "closed"
    assert len(pr2["comments"]) == 1

    pr_count = await mock_db.prs.count_documents({"project_id": "proj_123", "number": 1})
    assert pr_count == 1

    # Issue test
    issue1 = await upsert_issue(
        mock_db,
        {
            "project_id": "proj_123",
            "number": 10,
            "title": "Bug in parser",
            "body": "Fails on empty input",
            "labels": ["bug"],
            "comments": [],
        },
    )
    assert issue1["title"] == "Bug in parser"

    issue2 = await upsert_issue(
        mock_db,
        {
            "project_id": "proj_123",
            "number": 10,
            "title": "Bug in parser (resolved)",
            "body": "Fails on empty input",
            "labels": ["bug", "resolved"],
            "comments": [{"id": 2, "body": "Fixed in PR #1"}],
        },
    )
    assert issue2["labels"] == ["bug", "resolved"]

    issue_count = await mock_db.issues.count_documents({"project_id": "proj_123", "number": 10})
    assert issue_count == 1
