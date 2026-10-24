"""
Get PR tool: retrieves stored pull request details (title, state, body, files touched, comments).
Scoped to project_id.
"""

from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput, make_file_evidence, make_pr_evidence
from app.core.db import get_db


class GetPRArgs(BaseModel):
    number: int = Field(..., ge=1, description="Pull request number (e.g. 42).")


class GetPRTool(Tool):
    name = "get_pr"
    description = (
        "Get detailed information for a specific pull request from stored data, "
        "including title, author/state, body description, modified files, and reviewer comments."
    )
    args_schema = GetPRArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: GetPRArgs) -> ToolOutput:
        db = self._get_db()

        # Check total PR count for repo
        total_prs = await db.prs.count_documents({"project_id": project_id})
        if total_prs == 0:
            return ToolOutput(
                text="No pull requests exist for this repository.",
                evidence_ids=[],
            )

        doc = await db.prs.find_one({"project_id": project_id, "number": args.number})
        if not doc:
            return ToolOutput(
                text=f"Pull request #{args.number} not found for this repository.",
                evidence_ids=[],
            )

        num = doc.get("number")
        title = doc.get("title", "")
        state = doc.get("state", "unknown")
        created_at = doc.get("created_at") or "unknown"
        merged_at = doc.get("merged_at")
        body = (doc.get("body") or "").strip() or "(no description provided)"

        files = doc.get("files", []) or []
        comments = doc.get("comments", []) or []

        evidence_ids = [make_pr_evidence(num)]

        lines = [
            f"Pull Request #{num}: {title}",
            f"State: {state}" + (f" (merged at {merged_at})" if merged_at else ""),
            f"Created: {created_at}",
            "",
            "Description:",
            body,
            "",
            f"Changed Files ({len(files)}):",
        ]

        for f in files[:20]:
            fname = f.get("filename", "")
            adds = f.get("additions", 0)
            dels = f.get("deletions", 0)
            status = f.get("status", "modified")
            lines.append(f"- {fname} [{status}] (+{adds}/-{dels})")
            if fname and len(evidence_ids) < 15:
                evidence_ids.append(make_file_evidence(fname, 1, 1))

        if len(files) > 20:
            lines.append(f"... and {len(files) - 20} more files")

        if comments:
            lines.extend(["", f"Comments ({len(comments)}):"])
            for c in comments[:10]:
                user = c.get("user") or "unknown"
                cbody = (c.get("body") or "").replace("\n", " ")[:120]
                lines.append(f"- {user}: {cbody}")

        return ToolOutput(
            text="\n".join(lines),
            evidence_ids=evidence_ids,
        )
