import pytest
import mongomock_motor
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import (
    Tool,
    ToolOutput,
    make_commit_evidence,
    make_dep_evidence,
    make_fact_evidence,
    make_file_evidence,
    make_pr_evidence,
)
from app.agent.tools.registry import MAX_OUTPUT_CHARS, ToolRegistry
from app.agent.tools import (
    build_registry,
    OverviewTool,
    ListTreeTool,
    ReadFileTool,
    SearchCodeTool,
    DependenciesTool,
    FactsTool,
    TimelineTool,
    SearchCommitsTool,
    FileHistoryTool,
    GetCommitTool,
    SearchPRsTool,
    GetPRTool,
)
from app.github.client import GitHubClient
import httpx
import respx


class DummyReadArgs(BaseModel):
    file_path: str = Field(..., description="Path to the file to read")
    line_start: int = Field(1, description="1-based starting line")
    line_end: int = Field(100, description="1-based ending line")


class DummyReadTool(Tool):
    name = "dummy_read"
    description = "Read lines from a dummy file."
    args_schema = DummyReadArgs

    async def run(self, project_id: str, args: DummyReadArgs) -> ToolOutput:
        evidence = [make_file_evidence(args.file_path, args.line_start, args.line_end)]
        return ToolOutput(
            text=f"Content of {args.file_path} from line {args.line_start} to {args.line_end}",
            evidence_ids=evidence,
        )


class DummyOversizedTool(Tool):
    name = "oversized_dump"
    description = "Outputs a very large string for testing truncation."
    args_schema = DummyReadArgs

    async def run(self, project_id: str, args: DummyReadArgs) -> ToolOutput:
        # Generate 10000 characters
        large_body = "A" * 10000
        return ToolOutput(text=large_body, evidence_ids=["file:dump.txt#L1-100"])


class DummyShortTool(Tool):
    name = "short_dump"
    description = "Outputs a 100 character string."
    args_schema = DummyReadArgs

    async def run(self, project_id: str, args: DummyReadArgs) -> ToolOutput:
        return ToolOutput(text="B" * 100, evidence_ids=["file:short.txt#L1-10"])


class DummyFailingSecretTool(Tool):
    name = "failing_secret_tool"
    description = "Raises an exception with a github token."
    args_schema = DummyReadArgs

    async def run(self, project_id: str, args: DummyReadArgs) -> ToolOutput:
        raise RuntimeError("token ghp_abcdefghijklmnopqrstuvwxyz0123456789")


@pytest.fixture
def mock_db() -> AsyncIOMotorDatabase:
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_agent_db"]


@pytest.fixture
def base_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(DummyReadTool())
    reg.register(DummyOversizedTool())
    reg.register(DummyShortTool())
    reg.register(DummyFailingSecretTool())
    return reg


async def seed_project_data(mock_db: AsyncIOMotorDatabase) -> str:
    pid = "proj_test_123"
    await mock_db.projects.insert_one({
        "_id": pid,
        "repo_full_name": "octocat/Hello-World",
        "description": "My sample repo for agent testing",
        "default_branch": "main",
        "status": "ready",
    })
    await mock_db.files.insert_many([
        {
            "project_id": pid,
            "path": "src/main.py",
            "size": 150,
            "language": "Python",
            "content": "import fastapi\n\napp = fastapi.FastAPI()\n\n@app.get('/hello')\ndef hello():\n    return {'msg': 'world'}\n",
        },
        {
            "project_id": pid,
            "path": "src/utils.py",
            "size": 80,
            "language": "Python",
            "content": "def add(a, b):\n    return a + b\n",
        },
        {
            "project_id": pid,
            "path": "tests/test_main.py",
            "size": 95,
            "language": "Python",
            "content": "def test_hello():\n    assert True\n",
        },
        {
            "project_id": pid,
            "path": "package.json",
            "size": 120,
            "language": "JSON",
            "content": '{"name": "hello"}',
        },
    ])
    await mock_db.dependencies.insert_many([
        {
            "project_id": pid,
            "name": "fastapi",
            "version_spec": "^0.110.0",
            "ecosystem": "pip",
            "kind": "runtime",
            "source_file": "requirements.txt",
        },
        {
            "project_id": pid,
            "name": "pytest",
            "version_spec": ">=8.0.0",
            "ecosystem": "pip",
            "kind": "dev",
            "source_file": "requirements-dev.txt",
        },
    ])
    await mock_db.derived_facts.insert_many([
        {
            "project_id": pid,
            "kind": "route",
            "name": "GET /hello",
            "file_path": "src/main.py",
            "line_start": 5,
            "line_end": 7,
            "details": {"method": "GET", "in_test": False},
        },
        {
            "project_id": pid,
            "kind": "test",
            "name": "test_hello",
            "file_path": "tests/test_main.py",
            "line_start": 1,
            "line_end": 2,
            "details": {"in_test": True},
        },
    ])
    await mock_db.timeline.insert_one({
        "project_id": pid,
        "weekly_buckets": [
            {
                "week_start": "2026-03-01T00:00:00Z",
                "commit_count": 5,
                "additions": 120,
                "deletions": 10,
                "top_files": [{"file": "src/main.py", "touches": 4}],
            }
        ],
        "biggest_commits": [
            {
                "sha": "a1b2c3d4e5f67890",
                "date": "2026-03-02T10:00:00Z",
                "author": "octocat",
                "message": "Initial release",
                "additions": 120,
                "deletions": 10,
            }
        ],
        "dependency_first_seen": {"requirements.txt": {"sha": "a1b2c3d", "date": "2026-03-02"}},
        "dir_first_seen": {"src": {"sha": "a1b2c3d", "date": "2026-03-02"}},
        "history_complete": True,
    })
    await mock_db.commits.insert_many([
        {
            "project_id": pid,
            "sha": "a1b2c3d4e5f67890123456789012345678901234",
            "date": "2026-03-02T10:00:00Z",
            "author": "octocat",
            "message": "Initial release with FastAPI server",
            "additions": 120,
            "deletions": 10,
            "files_changed": [
                {"filename": "src/main.py", "additions": 100, "deletions": 5},
                {"filename": "package.json", "additions": 20, "deletions": 5},
            ],
        },
        {
            "project_id": pid,
            "sha": "b2c3d4e5f6789012345678901234567890123456",
            "date": "2026-03-05T12:00:00Z",
            "author": "alice",
            "message": "Fix math utility function",
            "additions": 5,
            "deletions": 2,
            "files_changed": [
                {"filename": "src/utils.py", "additions": 5, "deletions": 2},
            ],
        },
    ])
    await mock_db.prs.insert_many([
        {
            "project_id": pid,
            "number": 101,
            "title": "Add FastAPI routing endpoint",
            "body": "This PR integrates the hello world route.",
            "state": "closed",
            "created_at": "2026-03-03T11:00:00Z",
            "merged_at": "2026-03-03T12:00:00Z",
            "files": [
                {"filename": "src/main.py", "status": "added", "additions": 20, "deletions": 0},
            ],
            "comments": [
                {"user": "reviewer_bob", "body": "Looks great to me!"},
            ],
        },
    ])
    return pid


# ---------------------------------------------------------------------------
# Phase 3.2 Fix Tests (Central Truncation & Exception Redaction)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_truncation_enforced_centrally_for_10000_chars(base_registry: ToolRegistry) -> None:
    out = await base_registry.execute(
        name="oversized_dump",
        raw_args={"file_path": "dump.txt"},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert out.truncated is True
    # Length of result text must be at most the configured cap plus marker length
    assert len(out.text) <= MAX_OUTPUT_CHARS + 60
    assert out.text.startswith("A" * MAX_OUTPUT_CHARS)
    assert "[truncated, 8000 more chars]" in out.text
    assert out.evidence_ids == ["file:dump.txt#L1-100"]


@pytest.mark.asyncio
async def test_short_output_not_truncated(base_registry: ToolRegistry) -> None:
    out = await base_registry.execute(
        name="short_dump",
        raw_args={"file_path": "short.txt"},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert out.truncated is False
    assert out.text == "B" * 100
    assert "[truncated" not in out.text


@pytest.mark.asyncio
async def test_exception_redaction_and_capping(base_registry: ToolRegistry) -> None:
    out = await base_registry.execute(
        name="failing_secret_tool",
        raw_args={"file_path": "foo.py"},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert out.text.startswith("ERROR: Tool 'failing_secret_tool' failed during execution:")
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in out.text
    assert "[REDACTED_GITHUB_TOKEN]" in out.text
    assert len(out.text) <= 400


# ---------------------------------------------------------------------------
# Phase 3.3 Tests: All 7 Domain Tools Scoped to MongoDB Project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_overview_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = OverviewTool(db=mock_db)
    # Default: include_tests=False excludes the test fact
    out = await tool.run(seeded_project, tool.args_schema(include_tests=False))
    assert "Repository: octocat/Hello-World" in out.text
    assert "My sample repo for agent testing" in out.text
    assert "Python" in out.text
    assert "fastapi" in out.text
    assert "route: 1" in out.text
    assert "test:" not in out.text

    # include_tests=True includes test fact
    out_tests = await tool.run(seeded_project, tool.args_schema(include_tests=True))
    assert "route: 1" in out_tests.text
    assert "test: 1" in out_tests.text


@pytest.mark.asyncio
async def test_list_tree_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = ListTreeTool(db=mock_db)
    out = await tool.run(seeded_project, tool.args_schema(path="", depth=2))
    assert "Tree for '.'" in out.text
    assert "package.json" in out.text
    assert "src/main.py" in out.text or "📁 src/" in out.text
    assert out.evidence_ids == []


@pytest.mark.asyncio
async def test_read_file_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = ReadFileTool(db=mock_db)
    out = await tool.run(seeded_project, tool.args_schema(path="src/main.py", start=1, end=5))
    assert "File: src/main.py (Lines 1-5" in out.text
    assert "import fastapi" in out.text
    assert "@app.get('/hello')" in out.text
    assert out.evidence_ids == ["file:src/main.py#L1-5"]

    # Unknown file returns error
    out_err = await tool.run(seeded_project, tool.args_schema(path="does/not/exist.py"))
    assert "ERROR: File 'does/not/exist.py' not found" in out_err.text


@pytest.mark.asyncio
async def test_read_file_defaults_to_80_lines_and_marks_continuation(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = "proj_line_limit"
    await mock_db.files.insert_one({
        "project_id": project_id,
        "path": "large.py",
        "content": "\n".join(f"line {line}" for line in range(1, 501)),
    })
    tool = ReadFileTool(db=mock_db)

    default_out = await tool.run(
        project_id,
        tool.args_schema(path="large.py", start=10),
    )
    assert "Lines 10-89 of 500" in default_out.text
    assert "showing lines 10-89 of 500; call again with start=90 for more" in default_out.text

    capped_out = await tool.run(
        project_id,
        tool.args_schema(path="large.py", start=10, end=500),
    )
    assert "Lines 10-89 of 500" in capped_out.text
    assert "showing lines 10-89 of 500; call again with start=90 for more" in capped_out.text


@pytest.mark.asyncio
async def test_get_overview_finds_project_by_stable_project_id(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = "stable-overview-id"
    await mock_db.projects.insert_one({
        "_id": "mongo-internal-id",
        "project_id": project_id,
        "repo_full_name": "octocat/overview-repo",
        "description": "A fixture project description.",
        "default_branch": "develop",
    })

    tool = OverviewTool(db=mock_db)
    out = await tool.run(project_id, tool.args_schema())

    assert "Description: A fixture project description." in out.text
    assert "Default Branch: develop" in out.text
    assert "Project record not found" not in out.text


@pytest.mark.asyncio
async def test_search_code_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = SearchCodeTool(db=mock_db)
    out = await tool.run(seeded_project, tool.args_schema(query="fastapi", limit=10))
    assert "Found" in out.text
    assert "src/main.py:1: import fastapi" in out.text
    assert "file:src/main.py#L1-1" in out.evidence_ids

    # Search with path_glob that filters it out
    out_filtered = await tool.run(seeded_project, tool.args_schema(query="fastapi", path_glob="*.js"))
    assert "No matching files found" in out_filtered.text


@pytest.mark.asyncio
async def test_get_dependencies_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = DependenciesTool(db=mock_db)
    # All dependencies
    out = await tool.run(seeded_project, tool.args_schema(kind=None))
    assert "Project Dependencies (2 total)" in out.text
    assert "fastapi (^0.110.0)" in out.text
    assert "pytest (>=8.0.0)" in out.text
    assert "dep:fastapi" in out.evidence_ids

    # Filtered by kind=dev
    out_dev = await tool.run(seeded_project, tool.args_schema(kind="dev"))
    assert "pytest (>=8.0.0)" in out_dev.text
    assert "fastapi" not in out_dev.text


@pytest.mark.asyncio
async def test_get_facts_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = FactsTool(db=mock_db)
    # Default excludes test facts
    out = await tool.run(seeded_project, tool.args_schema(include_tests=False))
    assert "[route] GET /hello" in out.text
    assert "[test]" not in out.text

    # include_tests=True includes test facts
    out_with_tests = await tool.run(seeded_project, tool.args_schema(include_tests=True))
    assert "[route] GET /hello" in out_with_tests.text
    assert "[test] test_hello" in out_with_tests.text


@pytest.mark.asyncio
async def test_get_timeline_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = TimelineTool(db=mock_db)
    out = await tool.run(seeded_project, tool.args_schema())
    assert "Timeline Summary (history_complete=True):" in out.text
    assert "Active Weeks: 1" in out.text
    assert "commit a1b2c3d by octocat" in out.text
    assert "First-Seen Dependencies" in out.text
    assert "commit:a1b2c3d" in out.evidence_ids


@pytest.mark.asyncio
async def test_search_commits_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = SearchCommitsTool(db=mock_db)

    # Search by message text
    out = await tool.run(seeded_project, tool.args_schema(query="FastAPI"))
    assert "Found 1 commits matching 'FastAPI'" in out.text
    assert "a1b2c3d (2026-03-02) Initial release with FastAPI server" in out.text
    assert "matched: message" in out.text
    assert "commit:a1b2c3d" in out.evidence_ids

    # Search by SHA prefix
    out_sha = await tool.run(seeded_project, tool.args_schema(query="a1b2c3d"))
    assert "a1b2c3d" in out_sha.text
    assert "matched: sha" in out_sha.text

    # Search by a changed path substring
    await mock_db.commits.update_one(
        {"project_id": seeded_project, "sha": "b2c3d4e5f6789012345678901234567890123456"},
        {
            "$set": {
                "files_changed": [
                    {"filename": "src/utils.py"},
                    {"filename": "src/logger_utils.py"},
                ]
            }
        },
    )
    out_path_query = await tool.run(seeded_project, tool.args_schema(query="logger"))
    assert "b2c3d4e" in out_path_query.text
    assert "matched: path" in out_path_query.text

    # Search by path filter
    out_path = await tool.run(seeded_project, tool.args_schema(query="math", path="src/utils.py"))
    assert "Found 1 commits matching 'math'" in out_path.text
    assert "- b2c3d4e" in out_path.text

    # Search with non-matching path
    out_none = await tool.run(seeded_project, tool.args_schema(query="math", path="package.json"))
    assert "No commits found" in out_none.text


@pytest.mark.asyncio
async def test_file_history_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = FileHistoryTool(db=mock_db)

    out = await tool.run(seeded_project, tool.args_schema(path="src/main.py"))
    assert "Commit history for 'src/main.py' (1 commits):" in out.text
    assert "commit a1b2c3d" in out.text
    assert "(+100/-5)" in out.text
    assert "file:src/main.py#L1-1" in out.evidence_ids
    assert "commit:a1b2c3d" in out.evidence_ids

    # Untouched file
    out_empty = await tool.run(seeded_project, tool.args_schema(path="unknown/file.txt"))
    assert "No stored commits found touching 'unknown/file.txt'" in out_empty.text


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_tool_live_fetch(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)

    # Mock the live GitHub commit response.
    payload = {
        "sha": "a1b2c3d4e5f67890123456789012345678901234",
        "author": {"login": "octocat"},
        "commit": {
            "author": {"name": "Octo Cat", "date": "2026-03-02T10:00:00Z"},
            "message": "Initial release",
        },
        "stats": {"additions": 120, "deletions": 10},
        "files": [
            {"filename": "package.json", "status": "added", "additions": 20, "deletions": 5},
            {"filename": "src/main.py", "status": "modified", "additions": 100, "deletions": 5},
            {"filename": "uv.lock", "status": "modified", "additions": 10000, "deletions": 10000},
        ],
    }
    respx.get("https://api.github.com/repos/octocat/Hello-World/commits/a1b2c3d4e5f67890123456789012345678901234").mock(
        return_value=httpx.Response(200, json=payload)
    )

    async with GitHubClient(token="fake-token") as gh_client:
        tool = GetCommitTool(db=mock_db, github_client=gh_client)
        out = await tool.run(seeded_project, tool.args_schema(sha="a1b2c3d4e5f67890123456789012345678901234"))
        assert "Commit: a1b2c3d4e5f67890123456789012345678901234" in out.text
        assert "Author: octocat" in out.text
        assert out.text.index("src/main.py") < out.text.index("package.json") < out.text.index("uv.lock")
        assert "| uv.lock | modified (lockfile, generated) |" in out.text
        assert "Files changed: 3 total (3 shown)" in out.text
        assert "Total additions: 120" in out.text
        assert "Total deletions: 10" in out.text
        assert "Call get_commit again with path=<file> to see that file's diff." in out.text
        assert "commit:a1b2c3d" in out.evidence_ids


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_tool_diff_truncation_and_redaction(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)

    # Oversized patch with secret token
    large_patch = "diff --git a/secret.py\n+ghp_abcdefghijklmnopqrstuvwxyz0123456789\n" + ("+" + "x" * 70 + "\n") * 60
    payload = {
        "sha": "a1b2c3d4e5f67890123456789012345678901234",
        "commit": {"author": {}, "message": "Large patch"},
        "files": [{"filename": "secret.py", "status": "modified", "patch": large_patch}],
    }

    respx.get("https://api.github.com/repos/octocat/Hello-World/commits/a1b2c3d4e5f67890123456789012345678901234").mock(
        return_value=httpx.Response(
            200,
            json=payload,
        )
    )

    async with GitHubClient(token="fake-token") as gh_client:
        tool = GetCommitTool(db=mock_db, github_client=gh_client)
        out = await tool.run(
            seeded_project,
            tool.args_schema(sha="a1b2c3d", path="secret.py"),
        )
        assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in out.text
        assert "[REDACTED_GITHUB_TOKEN]" in out.text
        assert "[showing chars 1-1800" in out.text


def _mock_commit_payload(files: list[dict]) -> dict:
    return {
        "sha": "a1b2c3d4e5f67890123456789012345678901234",
        "author": {"login": "octocat"},
        "commit": {
            "author": {"name": "Octo Cat", "date": "2026-03-02T10:00:00Z"},
            "message": "Improve repository",
        },
        "stats": {"additions": 30, "deletions": 20, "total": 50},
        "files": files,
    }


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_path_returns_selected_file_patch(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    seeded_project = await seed_project_data(mock_db)
    payload = _mock_commit_payload([
        {
            "filename": "src/main.py",
            "status": "modified",
            "additions": 2,
            "deletions": 1,
            "patch": "@@ -1 +1,2 @@\n-old\n+new\n+extra\n",
        },
        {
            "filename": "other.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": "+other",
        },
    ])
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/commits/"
        "a1b2c3d4e5f67890123456789012345678901234"
    ).mock(return_value=httpx.Response(200, json=payload))

    async with GitHubClient(token="fake-token") as gh_client:
        tool = GetCommitTool(db=mock_db, github_client=gh_client)
        out = await tool.run(
            seeded_project,
            tool.args_schema(sha="a1b2c3d", path="src/main.py"),
        )

    assert "File: src/main.py (removed lines (-): 1; added lines (+): 2)" in out.text
    assert "@@ -1 +1,2 @@\n-old\n+new\n+extra\n" in out.text
    assert "other.py" not in out.text
    assert "commit:a1b2c3d:src/main.py" in out.evidence_ids


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_path_patch_offset_paging(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    seeded_project = await seed_project_data(mock_db)
    patch = "x" * 2200
    payload = _mock_commit_payload([
        {"filename": "src/main.py", "status": "modified", "patch": patch},
    ])
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/commits/"
        "a1b2c3d4e5f67890123456789012345678901234"
    ).mock(return_value=httpx.Response(200, json=payload))

    async with GitHubClient(token="fake-token") as gh_client:
        tool = GetCommitTool(db=mock_db, github_client=gh_client)
        first_page = await tool.run(
            seeded_project,
            tool.args_schema(sha="a1b2c3d", path="src/main.py"),
        )
        second_page = await tool.run(
            seeded_project,
            tool.args_schema(sha="a1b2c3d", path="src/main.py", offset=1800),
        )

    assert patch[:1800] in first_page.text
    assert "[showing chars 1-1800 of 2200; call again with offset=1800]" in first_page.text
    assert patch[1800:] in second_page.text
    assert "[showing chars" not in second_page.text


@pytest.mark.asyncio
@respx.mock
async def test_get_commit_unknown_path_lists_first_ten_files(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    seeded_project = await seed_project_data(mock_db)
    files = [
        {"filename": f"file_{number}.py", "status": "modified"}
        for number in range(12)
    ]
    payload = _mock_commit_payload(files)
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/commits/"
        "a1b2c3d4e5f67890123456789012345678901234"
    ).mock(return_value=httpx.Response(200, json=payload))

    async with GitHubClient(token="fake-token") as gh_client:
        tool = GetCommitTool(db=mock_db, github_client=gh_client)
        out = await tool.run(
            seeded_project,
            tool.args_schema(sha="a1b2c3d", path="missing.py"),
        )

    assert out.text.startswith("ERROR: path not in this commit")
    assert "file_0.py" in out.text and "file_9.py" in out.text
    assert "file_10.py" not in out.text


@pytest.mark.asyncio
async def test_search_prs_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = SearchPRsTool(db=mock_db)

    # Matching query
    out = await tool.run(seeded_project, tool.args_schema(query="routing"))
    assert "Found 1 pull requests matching 'routing'" in out.text
    assert "PR #101 [closed (merged)]" in out.text
    assert "pr:101" in out.evidence_ids

    # Query matching nothing
    out_none = await tool.run(seeded_project, tool.args_schema(query="unrelated"))
    assert "No pull requests found matching 'unrelated'" in out_none.text

    # Empty repository with 0 PRs
    await mock_db.prs.delete_many({})
    out_zero = await tool.run(seeded_project, tool.args_schema(query="routing"))
    assert "No pull requests exist for this repository." in out_zero.text


@pytest.mark.asyncio
async def test_get_pr_tool(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    tool = GetPRTool(db=mock_db)

    out = await tool.run(seeded_project, tool.args_schema(number=101))
    assert "Pull Request #101: Add FastAPI routing endpoint" in out.text
    assert "State: closed (merged at 2026-03-03T12:00:00Z)" in out.text
    assert "This PR integrates the hello world route." in out.text
    assert "src/main.py [added] (+20/-0)" in out.text
    assert "reviewer_bob: Looks great to me!" in out.text
    assert "pr:101" in out.evidence_ids
    assert "file:src/main.py#L1-1" in out.evidence_ids

    # Not found PR number
    out_404 = await tool.run(seeded_project, tool.args_schema(number=999))
    assert "Pull request #999 not found" in out_404.text

    # Zero PRs in repository
    await mock_db.prs.delete_many({})
    out_zero = await tool.run(seeded_project, tool.args_schema(number=101))
    assert "No pull requests exist for this repository." in out_zero.text


@pytest.mark.asyncio
async def test_build_registry_with_mock_db(mock_db: AsyncIOMotorDatabase) -> None:
    seeded_project = await seed_project_data(mock_db)
    reg = build_registry(db=mock_db)
    schemas = reg.schemas()
    assert len(schemas) == 12
    tool_names = {s["function"]["name"] for s in schemas}
    expected = {
        "get_overview",
        "list_tree",
        "read_file",
        "search_code",
        "get_dependencies",
        "get_facts",
        "get_timeline",
        "search_commits",
        "file_history",
        "get_commit",
        "search_prs",
        "get_pr",
    }
    assert tool_names == expected

    # Execute get_overview via registry
    exec_out = await reg.execute("get_overview", {}, seeded_project)
    assert "octocat/Hello-World" in exec_out.text
    assert exec_out.truncated is False


def test_evidence_id_formatters() -> None:
    assert make_file_evidence("backend/app/main.py", 12, 45) == "file:backend/app/main.py#L12-45"
    assert make_commit_evidence("a1b2c3d4e5f6") == "commit:a1b2c3d"
    assert make_pr_evidence(42) == "pr:42"
    assert make_dep_evidence("fastapi") == "dep:fastapi"
    assert make_fact_evidence("route", "GET /users") == "fact:route:GET /users"
