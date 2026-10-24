"""
Search commits tool: search stored commit messages and files_changed.
Scoped to project_id.
"""

import re
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput, make_commit_evidence
from app.core.db import get_db


class SearchCommitsArgs(BaseModel):
    query: str = Field(..., description="Search keyword in commit messages or authors.")
    path: str | None = Field(
        default=None,
        description="Optional file path substring to filter commits by files changed.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of commits to return (default: 10).",
    )


class SearchCommitsTool(Tool):
    name = "search_commits"
    description = (
        "Search stored repository commits by message text, author, and changed files. "
        "Allows finding when and why features or bugs were introduced."
    )
    args_schema = SearchCommitsArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: SearchCommitsArgs) -> ToolOutput:
        db = self._get_db()
        raw_query = args.query.strip()
        if not raw_query:
            return ToolOutput(text="ERROR: Search query cannot be empty.", evidence_ids=[])

        query_regex = re.escape(raw_query)
        filter_query: dict[str, Any] = {
            "project_id": project_id,
            "$or": [
                {"message": {"$regex": query_regex, "$options": "i"}},
                {"author": {"$regex": query_regex, "$options": "i"}},
            ],
        }

        if args.path:
            clean_path = args.path.strip()
            filter_query["files_changed.filename"] = {"$regex": re.escape(clean_path), "$options": "i"}

        cursor = db.commits.find(filter_query).sort("date", -1).limit(args.limit)
        commits = []
        evidence_ids = []

        async for doc in cursor:
            sha = doc.get("sha", "")
            sha7 = sha[:7]
            date = (doc.get("date") or "")[:10]
            author = doc.get("author", "unknown")
            msg = (doc.get("message") or "").replace("\n", " ")[:80]
            adds = doc.get("additions", 0)
            dels = doc.get("deletions", 0)
            changed = doc.get("files_changed", []) or []
            file_sample = [f.get("filename") for f in changed[:3] if isinstance(f, dict) and f.get("filename")]
            file_str = f" [files: {', '.join(file_sample)}]" if file_sample else ""

            commits.append(f"- commit {sha7} ({date}) by {author} (+{adds}/-{dels}): {msg}{file_str}")
            if sha:
                evidence_ids.append(make_commit_evidence(sha))

        if not commits:
            path_str = f" touching '{args.path}'" if args.path else ""
            return ToolOutput(
                text=f"No commits found matching query '{raw_query}'{path_str}.",
                evidence_ids=[],
            )

        header = f"Found {len(commits)} commits matching '{raw_query}':"
        output_text = header + "\n" + "\n".join(commits)
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence_ids,
        )
