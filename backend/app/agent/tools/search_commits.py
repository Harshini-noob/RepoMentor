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
    query: str = Field(..., description="Commit message text, changed file path, or hash prefix.")
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
    description = "Search commits by message text, file path, or commit hash prefix."
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
        or_filters: list[dict[str, Any]] = [
            {"message": {"$regex": query_regex, "$options": "i"}},
            {"files_changed.filename": {"$regex": query_regex, "$options": "i"}},
            {"files_changed": {"$regex": query_regex, "$options": "i"}},
        ]
        is_sha_prefix = re.fullmatch(r"[0-9a-fA-F]{7,40}", raw_query) is not None
        if is_sha_prefix:
            or_filters.append({"sha": {"$regex": f"^{query_regex}", "$options": "i"}})

        filter_query: dict[str, Any] = {
            "project_id": project_id,
            "$or": or_filters,
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
            message = (doc.get("message") or "").split("\n", 1)[0][:120]
            changed = doc.get("files_changed", []) or []
            paths = [
                item.get("filename") if isinstance(item, dict) else item
                for item in changed
            ]
            matched = []
            if raw_query.casefold() in (doc.get("message") or "").casefold():
                matched.append("message")
            if any(
                isinstance(path, str) and raw_query.casefold() in path.casefold()
                for path in paths
            ):
                matched.append("path")
            if is_sha_prefix and sha.lower().startswith(raw_query.lower()):
                matched.append("sha")

            commits.append(
                f"- {sha7} ({date}) {message} [matched: {', '.join(matched)}]"
            )
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
