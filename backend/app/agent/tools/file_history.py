"""
File history tool: inspect commits that touched a specific file path, newest first.
From stored commit data, scoped to project_id.
"""

from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.tools.base import Tool, ToolOutput, make_commit_evidence, make_file_evidence
from app.core.db import get_db


class FileHistoryArgs(BaseModel):
    path: str


class FileHistoryTool(Tool):
    name = "file_history"
    description = "Retrieve commits that changed a file, newest first, with author, date, and message."
    args_schema = FileHistoryArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: FileHistoryArgs) -> ToolOutput:
        db = self._get_db()
        clean_path = args.path.strip()
        if not clean_path:
            return ToolOutput(text="ERROR: File path cannot be empty.", evidence_ids=[])

        filter_query: dict[str, Any] = {
            "project_id": project_id,
            "files_changed.filename": clean_path,
        }

        cursor = db.commits.find(filter_query).sort("date", -1).limit(50)
        history = []
        evidence_ids = [make_file_evidence(clean_path, 1, 1)]

        async for doc in cursor:
            sha = doc.get("sha", "")
            sha7 = sha[:7]
            date = (doc.get("date") or "")[:10]
            author = doc.get("author", "unknown")
            msg = (doc.get("message") or "").replace("\n", " ")[:80]

            # Find matching file diff stat if present
            matching_file = next(
                (f for f in doc.get("files_changed", []) if isinstance(f, dict) and f.get("filename") == clean_path),
                None,
            )
            stat = ""
            if matching_file:
                adds = matching_file.get("additions", 0)
                dels = matching_file.get("deletions", 0)
                stat = f" (+{adds}/-{dels})"

            history.append(f"- commit {sha7} ({date}) by {author}{stat}: {msg}")
            if sha:
                evidence_ids.append(make_commit_evidence(sha))

        if not history:
            return ToolOutput(
                text=f"No stored commits found touching '{clean_path}'.",
                evidence_ids=[make_file_evidence(clean_path, 1, 1)],
            )

        header = f"Commit history for '{clean_path}' ({len(history)} commits):"
        output_text = header + "\n" + "\n".join(history)
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence_ids[:30],
        )
