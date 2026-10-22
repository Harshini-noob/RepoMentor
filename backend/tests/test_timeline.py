import pytest
from mongomock_motor import AsyncMongoMockClient

from app.core.repositories import init_indexes
from app.ingestion.timeline import build_timeline


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_timeline_db"]


# 6 fake commits spread across 3 ISO weeks
# W1 = 2026-09-28 (Mon), W2 = 2026-10-05 (Mon), W3 = 2026-10-12 (Mon)
FAKE_COMMITS = [
    # W1 — two commits
    {
        "sha": "a001",
        "date": "2026-09-28T10:00:00Z",
        "author": "alice",
        "message": "init project",
        "additions": 200,
        "deletions": 0,
        "parents": [],
        "files_changed": [
            {"filename": "requirements.txt", "status": "added"},
            {"filename": "app/main.py", "status": "added"},
        ],
    },
    {
        "sha": "a002",
        "date": "2026-09-30T12:00:00Z",
        "author": "bob",
        "message": "add README",
        "additions": 50,
        "deletions": 0,
        "parents": ["a001"],
        "files_changed": [
            {"filename": "README.md", "status": "added"},
        ],
    },
    # W2 — two commits
    {
        "sha": "b001",
        "date": "2026-10-06T09:00:00Z",
        "author": "alice",
        "message": "add database layer",
        "additions": 300,
        "deletions": 20,
        "parents": ["a002"],
        "files_changed": [
            {"filename": "app/core/db.py", "status": "added"},
            {"filename": "pyproject.toml", "status": "modified"},
        ],
    },
    {
        "sha": "b002",
        "date": "2026-10-08T16:00:00Z",
        "author": "carol",
        "message": "refactor routes",
        "additions": 80,
        "deletions": 60,
        "parents": ["b001"],
        "files_changed": [
            {"filename": "app/main.py", "status": "modified"},
            {"filename": "app/core/db.py", "status": "modified"},
        ],
    },
    # W3 — two commits
    {
        "sha": "c001",
        "date": "2026-10-13T11:00:00Z",
        "author": "bob",
        "message": "add Dockerfile",
        "additions": 40,
        "deletions": 0,
        "parents": ["b002"],
        "files_changed": [
            {"filename": "Dockerfile", "status": "added"},
            {"filename": "docker-compose.yml", "status": "added"},
        ],
    },
    {
        "sha": "c002",
        "date": "2026-10-15T14:30:00Z",
        "author": "alice",
        "message": "fix env config",
        "additions": 10,
        "deletions": 5,
        "parents": ["c001"],
        "files_changed": [
            {"filename": "app/config.py", "status": "modified"},
        ],
    },
]


async def seed(mock_db, project_id: str = "proj_timeline") -> None:
    await init_indexes(mock_db)
    for commit in FAKE_COMMITS:
        await mock_db.commits.insert_one({"project_id": project_id, **commit})


@pytest.mark.asyncio
async def test_timeline_weekly_buckets(mock_db) -> None:
    await seed(mock_db)
    doc = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)

    buckets = doc["weekly_buckets"]
    assert len(buckets) == 3

    # Each bucket is sorted by week_start
    starts = [b["week_start"] for b in buckets]
    assert starts == sorted(starts)

    # W1: commits a001 + a002
    w1 = buckets[0]
    assert w1["commit_count"] == 2
    assert w1["additions"] == 250   # 200 + 50
    assert w1["deletions"] == 0

    # W2: commits b001 + b002
    w2 = buckets[1]
    assert w2["commit_count"] == 2
    assert w2["additions"] == 380
    assert w2["deletions"] == 80

    # W3: commits c001 + c002
    w3 = buckets[2]
    assert w3["commit_count"] == 2
    assert w3["additions"] == 50
    assert w3["deletions"] == 5


@pytest.mark.asyncio
async def test_timeline_top_files_per_bucket(mock_db) -> None:
    await seed(mock_db)
    doc = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)

    # W2 touched app/core/db.py twice (b001 and b002)
    w2 = doc["weekly_buckets"][1]
    top_files = {entry["file"]: entry["touches"] for entry in w2["top_files"]}
    assert top_files.get("app/core/db.py") == 2


@pytest.mark.asyncio
async def test_timeline_biggest_commits(mock_db) -> None:
    await seed(mock_db)
    doc = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)

    biggest = doc["biggest_commits"]
    # Biggest by churn: b001 (300+20=320), a001 (200), b002 (80+60=140), ...
    assert biggest[0]["sha"] == "b001"
    assert biggest[0]["additions"] == 300
    assert len(biggest) <= 10


@pytest.mark.asyncio
async def test_dependency_first_seen(mock_db) -> None:
    await seed(mock_db)
    doc = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)

    dep_first = doc["dependency_first_seen"]
    # requirements.txt first touched in commit a001 (W1)
    assert "requirements.txt" in dep_first
    assert dep_first["requirements.txt"]["sha"] == "a001"

    # pyproject.toml first touched in b001 (W2)
    assert "pyproject.toml" in dep_first
    assert dep_first["pyproject.toml"]["sha"] == "b001"

    # dockerfile first touched in c001 (W3)
    assert "dockerfile" in dep_first
    assert dep_first["dockerfile"]["sha"] == "c001"


@pytest.mark.asyncio
async def test_dir_first_seen(mock_db) -> None:
    await seed(mock_db)
    doc = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)

    dir_first = doc["dir_first_seen"]
    # 'app' directory first appeared in a001 (app/main.py)
    assert "app" in dir_first
    assert dir_first["app"]["sha"] == "a001"

    # 'Dockerfile' is at root, so top-level dir is '.'
    assert "." in dir_first


@pytest.mark.asyncio
async def test_history_complete_flag(mock_db) -> None:
    await seed(mock_db)

    # 6 commits < cap of 500 → history is complete
    doc_complete = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)
    assert doc_complete["history_complete"] is True

    # 6 commits >= cap of 6 → history may be truncated
    doc_truncated = await build_timeline("proj_timeline", db=mock_db, ingest_cap=6)
    assert doc_truncated["history_complete"] is False


@pytest.mark.asyncio
async def test_timeline_idempotent(mock_db) -> None:
    await seed(mock_db)
    doc1 = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)
    doc2 = await build_timeline("proj_timeline", db=mock_db, ingest_cap=500)

    assert doc1["weekly_buckets"] == doc2["weekly_buckets"]
    # Only one document in timeline collection
    count = await mock_db.timeline.count_documents({"project_id": "proj_timeline"})
    assert count == 1


@pytest.mark.asyncio
async def test_timeline_empty_commits(mock_db) -> None:
    await init_indexes(mock_db)
    doc = await build_timeline("proj_no_commits", db=mock_db, ingest_cap=500)
    assert doc["weekly_buckets"] == []
    assert doc["biggest_commits"] == []
    assert doc["dependency_first_seen"] == {}
    assert doc["dir_first_seen"] == {}
    assert doc["history_complete"] is True
