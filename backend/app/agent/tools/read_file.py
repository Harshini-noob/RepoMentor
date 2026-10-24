"""
Read file tool: inspect lines from a repository file with 1-based line numbers.
Capped and scoped to project_id.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput, make_file_evidence
from app.core.db import get_db


class ReadFileArgs(BaseModel):
    path: str
    start: int = Field(default=1, ge=1)
    end: int | None = Field(
        default=None,
        ge=1,
    )


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read up to 80 lines of a repository file; use the continuation marker to request more."
    args_schema = ReadFileArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: ReadFileArgs) -> ToolOutput:
        db = self._get_db()

        # Sanitize arguments
        start_line = max(1, args.start)
        requested_end = args.end if args.end is not None else start_line + 79
        requested_end = max(start_line, requested_end)

        file_doc = await db.files.find_one({"project_id": project_id, "path": args.path})
        if not file_doc:
            return ToolOutput(
                text=f"ERROR: File '{args.path}' not found in project repository.",
                evidence_ids=[],
            )

        content = file_doc.get("content")
        if content is None:
            size = file_doc.get("size", 0)
            return ToolOutput(
                text=(
                    f"File '{args.path}' exists ({size} bytes), but its content was not ingested "
                    "(it may be a binary file, large build artifact, or exceeded content limits)."
                ),
                evidence_ids=[make_file_evidence(args.path, 1, 1)],
            )

        lines = content.splitlines()
        total_lines = len(lines)

        if start_line > total_lines:
            return ToolOutput(
                text=f"File '{args.path}' has {total_lines} lines; requested start line {start_line} exceeds file length.",
                evidence_ids=[make_file_evidence(args.path, 1, total_lines or 1)],
            )

        actual_end = min(requested_end, start_line + 79, total_lines)
        sliced_lines = lines[start_line - 1 : actual_end]

        formatted = [f"{i:4d} | {line}" for i, line in enumerate(sliced_lines, start=start_line)]
        header = f"File: {args.path} (Lines {start_line}-{actual_end} of {total_lines})"
        output_text = header + "\n" + "\n".join(formatted)
        if actual_end < total_lines and (args.end is None or requested_end > actual_end):
            output_text += (
                f"\n[showing lines {start_line}-{actual_end} of {total_lines}; "
                f"call again with start={actual_end + 1} for more]"
            )

        evidence = [make_file_evidence(args.path, start_line, actual_end)]
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence,
        )
