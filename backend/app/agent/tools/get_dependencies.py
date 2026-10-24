"""
Get dependencies tool: inspect package dependencies from the dependencies collection.
Scoped to project_id.
"""

from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.tools.base import Tool, ToolOutput, make_dep_evidence
from app.core.db import get_db


class DependenciesArgs(BaseModel):
    kind: str | None = None


class DependenciesTool(Tool):
    name = "get_dependencies"
    description = "List parsed project dependencies with versions and ecosystems."
    args_schema = DependenciesArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: DependenciesArgs) -> ToolOutput:
        db = self._get_db()

        filter_query: dict[str, Any] = {"project_id": project_id}
        if args.kind:
            filter_query["kind"] = args.kind

        cursor = db.dependencies.find(filter_query).sort("name", 1)
        deps = []
        evidence_ids = []

        async for doc in cursor:
            name = doc["name"]
            spec = doc.get("version_spec") or "unspecified"
            ecosystem = doc.get("ecosystem", "unknown")
            kind = doc.get("kind", "runtime")
            source = doc.get("source_file", "")
            deps.append(f"- {name} ({spec}) [{ecosystem}, {kind}] via {source}")
            evidence_ids.append(make_dep_evidence(name))

        if not deps:
            filter_msg = f" of kind '{args.kind}'" if args.kind else ""
            return ToolOutput(
                text=f"No dependencies{filter_msg} found for this project.",
                evidence_ids=[],
            )

        header = f"Project Dependencies ({len(deps)} total):"
        output_text = header + "\n" + "\n".join(deps)
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence_ids[:30],
        )
