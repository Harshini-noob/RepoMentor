"""
List tree tool: inspect directory structure and file sizes from the files collection.
Scoped to project_id.
"""

from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput
from app.core.db import get_db


class ListTreeArgs(BaseModel):
    path: str = Field(
        default="",
        description="Subdirectory path to list (e.g. 'src' or 'backend/app'). Leave empty for project root.",
    )
    depth: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Depth limit for directory listing traversal (default is 2).",
    )


class ListTreeTool(Tool):
    name = "list_tree"
    description = (
        "Explore directory tree structure and file sizes from stored files. "
        "Allows navigating the repository layout up to a specified depth."
    )
    args_schema = ListTreeArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: ListTreeArgs) -> ToolOutput:
        db = self._get_db()

        prefix = args.path.strip("/")
        if prefix:
            prefix = prefix + "/"

        # Fetch files matching prefix
        query: dict[str, Any] = {"project_id": project_id}
        if prefix:
            # Matches prefix/anything
            query["path"] = {"$regex": f"^{prefix}"}

        cursor = db.files.find(query, {"path": 1, "size": 1, "language": 1}).sort("path", 1)

        tree_entries: dict[str, dict[str, Any]] = {}
        target_depth = args.depth
        prefix_len = len(prefix.split("/")) - 1 if prefix else 0

        async for doc in cursor:
            rel_path = doc["path"]
            if prefix and rel_path.startswith(prefix):
                sub_path = rel_path[len(prefix):]
            elif not prefix:
                sub_path = rel_path
            else:
                continue

            parts = [p for p in sub_path.split("/") if p]
            if not parts:
                continue

            if len(parts) > target_depth:
                # Group as a folder entry at target_depth
                folder_key = "/".join(parts[:target_depth]) + "/"
                if folder_key not in tree_entries:
                    tree_entries[folder_key] = {"type": "dir", "count": 1, "size": doc.get("size", 0)}
                else:
                    tree_entries[folder_key]["count"] += 1
                    tree_entries[folder_key]["size"] += doc.get("size", 0)
            elif len(parts) == target_depth or len(parts) < target_depth:
                # Explicit file or direct item
                item_key = "/".join(parts)
                tree_entries[item_key] = {
                    "type": "file",
                    "size": doc.get("size", 0),
                    "full_path": doc["path"],
                }

        if not tree_entries:
            display_path = args.path if args.path else "root"
            return ToolOutput(
                text=f"No files found under path '{display_path}' for this project.",
                evidence_ids=[],
            )

        lines = [f"Tree for '{args.path or '.'}' (depth={args.depth}):"]
        for key in sorted(tree_entries.keys()):
            entry = tree_entries[key]
            if entry["type"] == "dir":
                lines.append(f"📁 {key} ({entry['count']} files, {entry['size']} bytes)")
            else:
                lines.append(f"📄 {key} ({entry['size']} bytes)")

        return ToolOutput(
            text="\n".join(lines),
            evidence_ids=[],
        )
