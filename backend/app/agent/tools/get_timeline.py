"""
Get timeline tool: inspect weekly commit churn, top modified files, biggest commits,
and first-seen metadata. Scoped to project_id.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.agent.tools.base import Tool, ToolOutput, make_commit_evidence
from app.core.db import get_db


class TimelineArgs(BaseModel):
    pass


class TimelineTool(Tool):
    name = "get_timeline"
    description = (
        "Get project evolution and churn timeline: weekly commit activity, "
        "top modified files, biggest commits by diff size, and first-seen dates "
        "for dependencies and top-level directories."
    )
    args_schema = TimelineArgs

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: TimelineArgs) -> ToolOutput:
        db = self._get_db()

        doc = await db.timeline.find_one({"project_id": project_id})
        if not doc:
            return ToolOutput(
                text="No timeline data has been generated yet for this project.",
                evidence_ids=[],
            )

        weekly = doc.get("weekly_buckets", [])
        biggest = doc.get("biggest_commits", [])
        dep_first = doc.get("dependency_first_seen", {})
        dir_first = doc.get("dir_first_seen", {})
        history_complete = doc.get("history_complete", False)

        lines = [
            f"Timeline Summary (history_complete={history_complete}):",
            f"Active Weeks: {len(weekly)}",
            "",
            "Recent Weekly Activity (latest 5 weeks):",
        ]

        evidence_ids = []
        for w in weekly[-5:]:
            wk_date = w.get("week_start", "")[:10]
            commits = w.get("commit_count", 0)
            adds = w.get("additions", 0)
            dels = w.get("deletions", 0)
            top_files = [f"{tf['file']} ({tf['touches']} touches)" for tf in w.get("top_files", [])[:3]]
            files_str = f" [Top files: {', '.join(top_files)}]" if top_files else ""
            lines.append(f"- Week {wk_date}: {commits} commits (+{adds}/-{dels}){files_str}")

        lines.extend([
            "",
            f"Top Commits by Churn ({min(5, len(biggest))} shown):",
        ])
        for c in biggest[:5]:
            sha = c.get("sha", "")
            sha_short = sha[:7]
            author = c.get("author", "unknown")
            msg = c.get("message", "").replace("\n", " ")[:60]
            adds = c.get("additions", 0)
            dels = c.get("deletions", 0)
            lines.append(f"- commit {sha_short} by {author} (+{adds}/-{dels}): {msg}")
            if sha:
                evidence_ids.append(make_commit_evidence(sha))

        if dep_first:
            lines.extend(["", "First-Seen Dependencies / Manifests:"])
            for manifest, info in sorted(dep_first.items()):
                lines.append(f"- {manifest}: first seen in commit {info.get('sha', '')[:7]} on {info.get('date', '')[:10]}")

        if dir_first:
            lines.extend(["", "First-Seen Directories:"])
            for d, info in sorted(dir_first.items()):
                lines.append(f"- {d}/: first seen in commit {info.get('sha', '')[:7]} on {info.get('date', '')[:10]}")

        return ToolOutput(
            text="\n".join(lines),
            evidence_ids=evidence_ids,
        )
