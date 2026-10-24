"""
Overview tool: produces a structured summary of the repository from stored data only.
No LLM calls, strictly scoped to project_id.
"""

from typing import Any
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput
from app.core.db import get_db


class OverviewArgs(BaseModel):
    include_tests: bool = Field(
        default=False,
        description="Whether to include test-related derived facts in counts and details.",
    )


class OverviewTool(Tool):
    name = "get_overview"
    description = (
        "Get a high-level summary of the repository from stored metadata. "
        "Returns repository description, detected languages, dependencies, "
        "derived code fact counts by kind, and timeline summary without making LLM calls."
    )
    args_schema = OverviewArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: OverviewArgs) -> ToolOutput:
        db = self._get_db()
        p_query = {"_id": ObjectId(project_id)} if ObjectId.is_valid(project_id) else {"_id": project_id}
        project = await db.projects.find_one(p_query)
        if not project:
            # Fallback to repo_full_name if project_id was passed as name
            project = await db.projects.find_one({"repo_full_name": project_id})

        repo_name = project.get("repo_full_name", project_id) if project else project_id
        description = (project.get("description") or "No description available") if project else "Project record not found"
        default_branch = project.get("default_branch", "main") if project else "main"

        # 1. Languages detected from files collection
        pipeline_lang = [
            {"$match": {"project_id": project_id, "language": {"$ne": None}}},
            {"$group": {"_id": "$language", "count": {"$sum": 1}, "bytes": {"$sum": "$size"}}},
            {"$sort": {"bytes": -1}},
        ]
        lang_docs = await db.files.aggregate(pipeline_lang).to_list(100)
        languages = [f"{doc['_id']} ({doc['count']} files)" for doc in lang_docs]

        # 2. Dependencies
        dep_cursor = db.dependencies.find({"project_id": project_id}).sort("name", 1)
        deps = []
        evidence_ids = []
        async for doc in dep_cursor:
            v = f" ({doc['version_spec']})" if doc.get("version_spec") else ""
            deps.append(f"{doc['name']}{v} [{doc.get('kind', 'runtime')}]")
            evidence_ids.append(f"dep:{doc['name']}")

        # 3. Derived facts counts grouped by kind
        fact_match: dict[str, Any] = {"project_id": project_id}
        if not args.include_tests:
            fact_match["details.in_test"] = {"$ne": True}
            fact_match["kind"] = {"$ne": "test"}

        pipeline_facts = [
            {"$match": fact_match},
            {"$group": {"_id": "$kind", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        fact_counts = await db.derived_facts.aggregate(pipeline_facts).to_list(100)
        facts_summary = [f"{f['_id']}: {f['count']}" for f in fact_counts]

        # 4. Timeline summary
        timeline_doc = await db.timeline.find_one({"project_id": project_id})
        timeline_info = []
        if timeline_doc:
            weekly = timeline_doc.get("weekly_buckets", [])
            total_commits = sum(w.get("commit_count", 0) for w in weekly)
            timeline_info.append(f"Total commits analyzed: {total_commits} across {len(weekly)} weeks")
            history_complete = timeline_doc.get("history_complete", False)
            timeline_info.append(f"Full history complete: {history_complete}")
            biggest = timeline_doc.get("biggest_commits", [])
            if biggest:
                timeline_info.append(f"Biggest commit: {biggest[0].get('sha', '')[:7]} by {biggest[0].get('author')} ({biggest[0].get('message')})")
        else:
            timeline_info.append("No timeline generated yet.")

        # Construct readable text output
        lines = [
            f"Repository: {repo_name}",
            f"Description: {description}",
            f"Default Branch: {default_branch}",
            "",
            "Languages: " + (", ".join(languages) if languages else "None detected"),
            "",
            f"Dependencies ({len(deps)}): " + (", ".join(deps[:30]) if deps else "None detected"),
        ]
        if len(deps) > 30:
            lines.append(f"... and {len(deps) - 30} more dependencies")

        lines.extend([
            "",
            "Derived Facts: " + (", ".join(facts_summary) if facts_summary else "None"),
            "",
            "Timeline Summary:",
            *[f"- {item}" for item in timeline_info],
        ])

        return ToolOutput(
            text="\n".join(lines),
            evidence_ids=evidence_ids[:20],
        )
