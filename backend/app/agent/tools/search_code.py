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


def matches_glob(path: str, pattern: str | None) -> bool:
    """Match repository paths using convenient recursive and basename globs."""
    normalized_path = path.replace("\\", "/")
    normalized_pattern = (pattern or "").replace("\\", "/").strip()
    if normalized_pattern in {"", "*", "**", "**/*"}:
        return True
    if fnmatch.fnmatch(normalized_path, normalized_pattern):
        return True
    if normalized_pattern.startswith("**/") and fnmatch.fnmatch(
        normalized_path, normalized_pattern[3:]
    ):
        return True
    return "/" not in normalized_pattern and fnmatch.fnmatch(
        normalized_path.rsplit("/", 1)[-1], normalized_pattern
    )


class SearchCodeArgs(BaseModel):
    query: str
    path_glob: str | None = None
    limit: int = Field(
        default=10,
        ge=1,
        le=15,
    )


class SearchCodeTool(Tool):
    name = "search_code"
    description = "Search source lines and return matching snippets; use read_file for more context."
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

        all_files: list[dict[str, Any]] = []
        cursor = db.files.find(
            {"project_id": project_id},
            {"path": 1, "content": 1},
        ).sort("path", 1)
        async for doc in cursor:
            path = doc.get("path")
            if isinstance(path, str) and matches_glob(path, args.path_glob):
                all_files.append(doc)

        display_glob = args.path_glob.replace("\\", "/").strip() if args.path_glob else "none"
        scope_line = f"Searched {len(all_files)} files (path_glob: {display_glob or 'none'})"
        if args.path_glob and not all_files:
            return ToolOutput(
                text=(
                    f"{scope_line}\nERROR: path_glob '{args.path_glob}' matched no files. "
                    "Retry without path_glob."
                ),
                evidence_ids=[],
            )

        full_pattern = re.compile(re.escape(raw_query), re.IGNORECASE)
        word_patterns = [
            (word, re.compile(re.escape(word), re.IGNORECASE))
            for word in dict.fromkeys(re.findall(r"\w+", raw_query))
            if word.casefold() != raw_query.casefold()
        ]
        snippets = []
        evidence_ids = []

        for doc in all_files:
            path = doc["path"]
            content = doc.get("content") or ""
            lines = content.splitlines()

            for idx, line in enumerate(lines, start=1):
                match = full_pattern.search(line)
                matched_word = None
                if match is None:
                    matched_word = next(
                        (
                            word for word, pattern in word_patterns
                            if pattern.search(line)
                        ),
                        None,
                    )
                if match or matched_word:
                    short_line = line.strip()[:160]
                    word_note = f" (matched on word: {matched_word})" if matched_word else ""
                    snippets.append(f"{path}:{idx}: {short_line}{word_note}")
                    evidence_ids.append(make_file_evidence(path, idx, idx))
                    if len(snippets) >= args.limit:
                        break
            if len(snippets) >= args.limit:
                break

        if not snippets:
            return ToolOutput(
                text=f"{scope_line}\nSearched {len(all_files)} files, no matches for '{raw_query}'.",
                evidence_ids=[],
            )

        output_text = (
            f"{scope_line}\nFound {len(snippets)} matches for '{raw_query}':\n"
            + "\n".join(snippets)
        )
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence_ids,
        )
