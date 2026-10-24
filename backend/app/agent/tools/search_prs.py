"""
Search PRs tool: search stored pull requests by title, body, or state.
Scoped to project_id.
"""

import re
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.tools.base import Tool, ToolOutput, make_pr_evidence
from app.core.db import get_db


class SearchPRsArgs(BaseModel):
    query: str
    state: str | None = None


class SearchPRsTool(Tool):
    name = "search_prs"
    description = "Search pull requests by title or body, optionally filtered by state."
    args_schema = SearchPRsArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: SearchPRsArgs) -> ToolOutput:
        db = self._get_db()

        # Check total PR count for repo
        total_prs = await db.prs.count_documents({"project_id": project_id})
        if total_prs == 0:
            return ToolOutput(
                text="No pull requests exist for this repository.",
                evidence_ids=[],
            )

        raw_query = args.query.strip()
        if not raw_query:
            return ToolOutput(text="ERROR: Search query cannot be empty.", evidence_ids=[])

        query_regex = re.escape(raw_query)
        filter_query: dict[str, Any] = {
            "project_id": project_id,
            "$or": [
                {"title": {"$regex": query_regex, "$options": "i"}},
                {"body": {"$regex": query_regex, "$options": "i"}},
            ],
        }
        if args.state:
            filter_query["state"] = args.state.lower().strip()

        cursor = db.prs.find(filter_query).sort("number", -1).limit(20)
        prs = []
        evidence_ids = []

        async for doc in cursor:
            num = doc.get("number")
            title = doc.get("title", "")
            state = doc.get("state", "unknown")
            merged = " (merged)" if doc.get("merged_at") else ""
            created = (doc.get("created_at") or "")[:10]
            prs.append(f"- PR #{num} [{state}{merged}] on {created}: {title}")
            if num is not None:
                evidence_ids.append(make_pr_evidence(num))

        if not prs:
            state_msg = f" with state '{args.state}'" if args.state else ""
            return ToolOutput(
                text=f"No pull requests found matching '{raw_query}'{state_msg}.",
                evidence_ids=[],
            )

        header = f"Found {len(prs)} pull requests matching '{raw_query}':"
        output_text = header + "\n" + "\n".join(prs)
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence_ids,
        )
