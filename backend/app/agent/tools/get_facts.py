"""
Get derived facts tool: inspect statically derived code facts with file and line references.
Scoped to project_id.
"""

from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput, make_fact_evidence, make_file_evidence
from app.core.db import get_db


class FactsArgs(BaseModel):
    kind: str | None = None
    include_tests: bool = False
    limit: int = Field(
        default=30,
        ge=1,
        le=200,
    )


class FactsTool(Tool):
    name = "get_facts"
    description = "The list is complete for its kind; do not re-verify it with search_code."
    args_schema = FactsArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: FactsArgs) -> ToolOutput:
        db = self._get_db()

        filter_query: dict[str, Any] = {"project_id": project_id}
        if args.kind:
            filter_query["kind"] = args.kind

        if not args.include_tests:
            filter_query["details.in_test"] = {"$ne": True}
            if not args.kind or args.kind != "test":
                filter_query["kind"] = {"$ne": "test"}

        cursor = db.derived_facts.find(filter_query).sort([("kind", 1), ("file_path", 1)])
        facts = []
        evidence_ids = []

        async for doc in cursor:
            kind = doc.get("kind", "unknown")
            name = doc.get("name", "")
            file_path = doc.get("file_path", "")
            l_start = doc.get("line_start", 1)
            l_end = doc.get("line_end", l_start)

            detail_items = []
            for k, v in doc.get("details", {}).items():
                if k != "in_test" and v is not None:
                    detail_items.append(f"{k}={v}")
            extra = f" ({', '.join(detail_items)})" if detail_items else ""

            line_ref = f"{file_path}#L{l_start}" if l_start == l_end else f"{file_path}#L{l_start}-{l_end}"
            facts.append(f"- [{kind}] {name} -> {line_ref}{extra}")

            evidence_ids.append(make_fact_evidence(kind, name))
            if len(evidence_ids) < 30:
                evidence_ids.append(make_file_evidence(file_path, l_start, l_end))

        kind_label = args.kind or "all"
        header = (
            f"{len(facts)} {kind_label} facts found (complete list from static analysis "
            "of the stored files; it detects Python decorator routes, Express routes, "
            "and import-based facts)"
        )
        if not facts:
            return ToolOutput(text=header, evidence_ids=[])

        output_text = header + "\n" + "\n".join(facts)
        return ToolOutput(
            text=output_text,
            evidence_ids=evidence_ids[:30],
        )
