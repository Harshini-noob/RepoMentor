from unittest.mock import AsyncMock
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.core.repositories import init_indexes
from app.github.models import (
    CommitInfo,
    FileContent,
    IssueInfo,
    PullComment,
    PullFile,
    PullRequestInfo,
    RepoInfo,
    TreeItem,
)
from app.ingestion.pipeline import ingest_project, should_fetch_content


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_ingestion_db"]


def test_should_fetch_content_skip_rules() -> None:
    # 1. Ignored directory trees
    fetch, _ = should_fetch_content("node_modules/express/index.js", 100)
    assert fetch is False
    fetch, _ = should_fetch_content("dist/bundle.js", 500)
    assert fetch is False
    fetch, _ = should_fetch_content("build/output.js", 500)
    assert fetch is False

    # 2. Binaries and images
    fetch, _ = should_fetch_content("assets/logo.png", 1024)
    assert fetch is False
    fetch, _ = should_fetch_content("bin/service.exe", 1024)
    assert fetch is False

    # 3. Lockfiles
    fetch, _ = should_fetch_content("package-lock.json", 1024)
    assert fetch is False
    fetch, _ = should_fetch_content("poetry.lock", 2048)
    assert fetch is False

    # 4. Over 200KB file
    fetch, _ = should_fetch_content("data/large_source.py", 250 * 1024)
    assert fetch is False

    # 5. Manifests and config files (must fetch and mark is_config=True)
    fetch, is_config = should_fetch_content("pyproject.toml", 1024)
    assert fetch is True
    assert is_config is True

    fetch, is_config = should_fetch_content("Dockerfile", 500)
    assert fetch is True
    assert is_config is True

    fetch, is_config = should_fetch_content("docker-compose.prod.yml", 800)
    assert fetch is True
    assert is_config is True

    # 6. Documentation and source files (must fetch and mark is_config=False)
    fetch, is_config = should_fetch_content("README.md", 2048)
    assert fetch is True
    assert is_config is False

    fetch, is_config = should_fetch_content("app/main.py", 4096)
    assert fetch is True
    assert is_config is False


@pytest.mark.asyncio
async def test_ingest_project_success(mock_db) -> None:
    await init_indexes(mock_db)

    # Setup mocked GitHubClient
    client = AsyncMock()
    client.get_repo.return_value = RepoInfo(
        name="Hello-World",
        full_name="octocat/Hello-World",
        owner="octocat",
        default_branch="main",
    )
    client.get_tree.return_value = [
        TreeItem(path="README.md", mode="100644", type="blob", sha="sha_readme", size=500),
        TreeItem(path="node_modules/foo.js", mode="100644", type="blob", sha="sha_mod", size=100),
        TreeItem(path="app/main.py", mode="100644", type="blob", sha="sha_code", size=1200),
    ]
    client.get_file.side_effect = lambda owner, repo, path, ref: FileContent(
        path=path,
        sha="file_sha",
        size=100,
        decoded_text=f"content of {path}",
    )
    client.list_commits.return_value = [
        CommitInfo(sha="c_1", message="Commit 1", author_name="Alice", date="2026-10-22T00:00:00Z"),
    ]
    client.get_commit.return_value = CommitInfo(
        sha="c_1",
        message="Commit 1",
        raw={
            "stats": {"additions": 10, "deletions": 2},
            "files": [{"filename": "app/main.py", "status": "modified"}],
            "parents": [{"sha": "p_0"}],
        },
    )
    client.list_pulls.return_value = [
        PullRequestInfo(number=10, title="PR 10", state="open", created_at="2026-10-22T00:00:00Z"),
    ]
    client.get_pull_files.return_value = [
        PullFile(filename="app/main.py", status="modified", additions=5, deletions=1, changes=6)
    ]
    client.get_pull_comments.return_value = [
        PullComment(id=1, user="bob", body="LGTM", created_at="2026-10-22T00:30:00Z")
    ]
    client.list_issues.return_value = [
        IssueInfo(number=5, title="Issue 5", state="open", body="Need docs", raw={"labels": [{"name": "docs"}]})
    ]

    res = await ingest_project("proj_1", "octocat/Hello-World", client=client, db=mock_db)

    # 1. Project status check
    assert res["status"] == "ready"
    assert res["ingest_cap"] == 500
    assert res["head_sha"] == "c_1"
    assert res["error"] is None
    assert res["analysed_at"] is not None

    # 2. File collection check
    readme = await mock_db.files.find_one({"project_id": "proj_1", "path": "README.md"})
    assert readme is not None
    assert readme["content"] == "content of README.md"

    ignored = await mock_db.files.find_one({"project_id": "proj_1", "path": "node_modules/foo.js"})
    assert ignored is not None
    assert ignored["content"] is None  # Skipped download

    # 3. Commit collection check (no raw diff patch stored)
    commit = await mock_db.commits.find_one({"project_id": "proj_1", "sha": "c_1"})
    assert commit is not None
    assert commit["additions"] == 10
    assert commit["parents"] == ["p_0"]

    # 4. PR check
    pr = await mock_db.prs.find_one({"project_id": "proj_1", "number": 10})
    assert pr is not None
    assert len(pr["files"]) == 1
    assert len(pr["comments"]) == 1

    # 5. Issue check
    issue = await mock_db.issues.find_one({"project_id": "proj_1", "number": 5})
    assert issue is not None
    assert issue["labels"] == ["docs"]


@pytest.mark.asyncio
async def test_ingest_project_failure_sets_failed_status(mock_db) -> None:
    await init_indexes(mock_db)

    client = AsyncMock()
    client.get_repo.side_effect = RuntimeError("GitHub repository not found or 404")

    with pytest.raises(RuntimeError):
        await ingest_project("proj_failed", "octocat/NonExistent", client=client, db=mock_db)

    # Verify status is failed with error message and never stuck on ingesting
    proj = await mock_db.projects.find_one({"repo_full_name": "octocat/NonExistent"})
    assert proj is not None
    assert proj["status"] == "failed"
    assert "GitHub repository not found or 404" in proj["error"]
