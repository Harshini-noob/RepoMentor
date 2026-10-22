import logging
from typing import Any
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.db import get_db
from app.core.utils import redact
from app.ingestion.dependencies import build_dependencies
from app.ingestion.derive import derive_project_facts
from app.ingestion.timeline import build_timeline

logger = logging.getLogger("repomentor.ingestion.analyze")


async def _update_project_facts(
    db: AsyncIOMotorDatabase,
    project_id: str,
    repo_full_name: str | None,
    facts_status: str,
    facts_error: str | None = None,
) -> None:
    """Helper to update facts_status and facts_error on the project document."""
    query = {"_id": ObjectId(project_id)} if ObjectId.is_valid(project_id) else {"_id": project_id}
    res = await db.projects.update_one(
        query,
        {"$set": {"facts_status": facts_status, "facts_error": facts_error}},
    )
    # Fallback to repo_full_name if query didn't match (e.g. string vs ObjectId mismatch)
    if res.matched_count == 0 and repo_full_name:
        await db.projects.update_one(
            {"repo_full_name": repo_full_name},
            {"$set": {"facts_status": facts_status, "facts_error": facts_error}},
        )


async def analyze_facts(
    project_id: str,
    db: AsyncIOMotorDatabase | None = None,
    ingest_cap: int = 500,
    repo_full_name: str | None = None,
) -> dict[str, Any]:
    """
    Run all Phase-2 static-analysis steps for a project in order:
      1. build_dependencies   — parses manifest files already in 'files'
      2. derive_project_facts — runs code detectors on every file
      3. build_timeline       — computes weekly buckets from 'commits'

    Failures here are NEVER allowed to mark ingestion as failed.
    Any failure is caught, logged with redact(), and recorded as
    facts_status='failed' and facts_error on the project document.
    """
    if db is None:
        db = get_db()

    summary: dict[str, Any] = {}

    try:
        # 1. Dependency extraction
        logger.info("[%s] analyze_facts: Step 1 building dependencies", project_id)
        deps = await build_dependencies(project_id, db=db)
        summary["dependencies"] = len(deps)

        # 2. Derived facts (routes, DB usage, ML, env vars, etc.)
        logger.info("[%s] analyze_facts: Step 2 deriving code facts", project_id)
        facts = await derive_project_facts(project_id, db=db)
        summary["derived_facts"] = len(facts)

        # 3. Commit timeline
        logger.info("[%s] analyze_facts: Step 3 building commit timeline", project_id)
        timeline = await build_timeline(project_id, db=db, ingest_cap=ingest_cap)
        summary["timeline_weeks"] = len(timeline.get("weekly_buckets", []))

        # Mark facts_status = "ok" on project
        await _update_project_facts(
            db=db,
            project_id=project_id,
            repo_full_name=repo_full_name,
            facts_status="ok",
            facts_error=None,
        )
        summary["facts_status"] = "ok"
        logger.info("[%s] analyze_facts completed successfully: %s", project_id, summary)
        return summary

    except Exception as exc:
        clean_error = redact(str(exc))
        logger.error("[%s] analyze_facts failed: %s", project_id, clean_error)

        await _update_project_facts(
            db=db,
            project_id=project_id,
            repo_full_name=repo_full_name,
            facts_status="failed",
            facts_error=clean_error,
        )
        summary["facts_status"] = "failed"
        summary["facts_error"] = clean_error
        return summary
