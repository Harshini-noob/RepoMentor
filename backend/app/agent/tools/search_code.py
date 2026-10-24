"""
Search code tool: search files content via MongoDB text search or regex fallback.
Returns file, line number, and a short snippet. Scoped to project_id.
"""

import fnmatch
import re
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput, make_file_evidence
from app.core.db import get_db


class SearchCodeArgs(BaseModel):
    query: str = Field(..., description="Text query or keyword to search for.")
    path_glob: str | None = Field(
        default=None,
        description="Optional glob pattern to filter file paths (e.g. '*.py', 'src/**', 'backend/**').",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of search results to return (default: 10).",
    )


class SearchCodeTool(Tool):
    name = "search_code"
    description = (
        "Search source code files in the repository using text search or regex fallback. "
        "Returns matching files, line numbers, and snippets."
    )
    args_schema = SearchCodeArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: SearchCodeArgs) -> ToolOutput:
        db = self._get_db()
        raw_query = args.query.strip()
        if not raw_query:
            return ToolOutput(text="ERROR: Search query cannot be empty.", evidence_ids=[])

        # 1. Attempt MongoDB text search index
        results: list[dict[str, Any]] = []
        try:
            filter_query: dict[str, Any] = {
                "project_id": project_id,
                "$text": {"$search": raw_query},
            }
            cursor = (
                db.files.find(
                    filter_query,
                    {"path": 1, "content": 1, "score": {"$meta": "textScore"}},
                )
                .sort([("score", {"$meta": "textScore"})])
                .limit(args.limit * 3)
            )
            async for doc in cursor:
                if doc.get("content"):
                    results.append(doc)
        except Exception:
            # mongomock or MongoDB deployment without text index
            results = []

        # 2. Regex fallback if text search returned nothing
        if not results:
            escaped = re.escape(raw_query)
            regex_filter: dict[str, Any] = {
                "project_id": project_id,
                "content": {"$regex": escaped, "$options": "i"},
            }
            cursor = db.files.find(regex_filter, {"path": 1, "content": 1}).limit(args.limit * 3)
            async for doc in cursor:
                if doc.get("content"):
                    results.append(doc)

        # 3. Apply path_glob filter if specified
        if args.path_glob:
            glob_pat = args.path_glob.strip()
            results = [doc for doc in results if fnmatch.fnmatch(doc["path"], glob_pat)]

        if not results:
            return ToolOutput(
                text=f"No matching files found for query '{raw_query}'.",
                evidence_ids=[],
            )

        # 4. Extract line numbers and short snippets for matches
        pattern = re.compile(re.escape(raw_query), re.IGNORECASE)
        snippets = []
        evidence_ids = []

        for doc in results:
            path = doc["path"]
            content = doc.get("content") or ""
            lines = content.splitlines()

            for idx, line in enumerate(lines, start=1):
                if pattern.search(line):
                    short_line = line.strip()[:160]
                    snippets.append(f"{path}:{idx}: {short_line}")
                    evidence_ids.append(make_file_evidence(path, idx, idx))
                    if len(snippets) >= args.limit:
                        break
            if len(snippets) >= args.limit:
                break

        if not snippets:
            return ToolOutput(
                text=f"Query '{raw_query}' appeared in documents but no matching lines were extracted.",
                evidence_ids=[],
            )

        output_text = f"Found {len(snippets)} matches for '{raw_query}':\n" + "\n".join(snippets)
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence_ids,
        )
