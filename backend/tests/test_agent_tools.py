import pytest
from pydantic import BaseModel, Field

from app.agent.tools.base import (
    MAX_OUTPUT_CHARS,
    Tool,
    ToolOutput,
    make_commit_evidence,
    make_dep_evidence,
    make_fact_evidence,
    make_file_evidence,
    make_pr_evidence,
)
from app.agent.tools.registry import ToolRegistry


class DummyReadArgs(BaseModel):
    file_path: str = Field(..., description="Path to the file to read")
    line_start: int = Field(1, description="1-based starting line")
    line_end: int = Field(100, description="1-based ending line")


class DummyReadTool(Tool):
    name = "read_file"
    description = "Read lines from a repository file."
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
        # Generate 5000 characters
        large_body = "A" * 5000
        return ToolOutput(text=large_body, evidence_ids=["file:dump.txt#L1-100"])


class DummyFailingTool(Tool):
    name = "failing_tool"
    description = "Throws an unexpected exception during execution."
    args_schema = DummyReadArgs

    async def run(self, project_id: str, args: DummyReadArgs) -> ToolOutput:
        raise RuntimeError("Database connection reset mid-tool")


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(DummyReadTool())
    reg.register(DummyOversizedTool())
    reg.register(DummyFailingTool())
    return reg


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_text(registry: ToolRegistry) -> None:
    # Executing an unregistered tool name must NOT raise, but return informative ERROR text
    out = await registry.execute(
        name="non_existent_tool",
        raw_args={"query": "test"},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert "ERROR: Unknown tool 'non_existent_tool'" in out.text
    assert "Available tools" in out.text
    assert out.truncated is False


@pytest.mark.asyncio
async def test_bad_args_validation_error(registry: ToolRegistry) -> None:
    # Missing required argument 'file_path' and invalid line_start type
    out = await registry.execute(
        name="read_file",
        raw_args={"line_start": "not_an_int"},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert "ERROR: Invalid arguments for tool 'read_file'" in out.text
    assert "file_path" in out.text
    assert out.truncated is False


@pytest.mark.asyncio
async def test_bad_args_malformed_json_string(registry: ToolRegistry) -> None:
    # Raw args passed as a string with broken JSON syntax
    out = await registry.execute(
        name="read_file",
        raw_args='{"file_path": "main.py", broken json',
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert "ERROR: Malformed JSON arguments" in out.text
    assert out.truncated is False


@pytest.mark.asyncio
async def test_oversized_output_truncated(registry: ToolRegistry) -> None:
    # Tool returning 5000 characters must be capped at 4000 with visible marker
    out = await registry.execute(
        name="oversized_dump",
        raw_args={"file_path": "dump.txt"},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert out.truncated is True
    # Length of truncated prefix is 4000
    assert out.text.startswith("A" * MAX_OUTPUT_CHARS)
    assert "[truncated, 1000 more chars]" in out.text


@pytest.mark.asyncio
async def test_successful_tool_execution(registry: ToolRegistry) -> None:
    out = await registry.execute(
        name="read_file",
        raw_args={"file_path": "backend/app/main.py", "line_start": 10, "line_end": 20},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert "Content of backend/app/main.py from line 10 to 20" in out.text
    assert out.evidence_ids == ["file:backend/app/main.py#L10-20"]
    assert out.truncated is False


@pytest.mark.asyncio
async def test_failing_tool_caught_as_error_text(registry: ToolRegistry) -> None:
    # Internal tool exception must NOT raise out of execute()
    out = await registry.execute(
        name="failing_tool",
        raw_args={"file_path": "foo.py"},
        project_id="proj_1",
    )
    assert isinstance(out, ToolOutput)
    assert "ERROR: Tool 'failing_tool' failed during execution: Database connection reset mid-tool" in out.text
    assert out.truncated is False


def test_registry_schemas_format(registry: ToolRegistry) -> None:
    schemas = registry.schemas()
    assert len(schemas) == 3

    # Check provider function-calling structure
    names = {s["function"]["name"] for s in schemas}
    assert "read_file" in names
    assert "oversized_dump" in names

    read_schema = next(s for s in schemas if s["function"]["name"] == "read_file")
    assert read_schema["type"] == "function"
    assert "description" in read_schema["function"]
    assert "parameters" in read_schema["function"]
    assert "properties" in read_schema["function"]["parameters"]
    assert "file_path" in read_schema["function"]["parameters"]["properties"]


def test_evidence_id_formatters() -> None:
    assert make_file_evidence("backend/app/main.py", 12, 45) == "file:backend/app/main.py#L12-45"
    assert make_commit_evidence("a1b2c3d4e5f6") == "commit:a1b2c3d"
    assert make_pr_evidence(42) == "pr:42"
    assert make_dep_evidence("fastapi") == "dep:fastapi"
    assert make_fact_evidence("route", "GET /users") == "fact:route:GET /users"
