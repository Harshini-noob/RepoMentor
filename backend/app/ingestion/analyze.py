import logging
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.db import get_db
from app.core.repositories import upsert_project
from app.ingestion.dependencies import build_dependencies
from app.ingestion.derive import derive_project_facts
from app.ingestion.timeline import build_timeline

logger = logging.getLogger("repomentor.ingestion.analyze")


async def analyze_facts(
    project_id: str,
    repo_full_name: str,
    db: AsyncIOMotorDatabase | None = None,
    ingest_cap: int = 500,
) -> dict[str, Any]:
    """
    Run all Phase-2 static-analysis steps for a project in order:
      1. build_dependencies  — parses manifest files already in 'files'
      2. derive_project_facts — runs code detectors on every file
      3. build_timeline       — computes weekly buckets from 'commits'

    Failures here are NEVER allowed to mark ingestion as failed.
    They are logged and stored in projects.facts_status so the
    project stays in status=ready.

    Returns a summary dict with counts from each step.
    """
    if db is None:
        db = get_db()

    summary: dict[str, Any] = {}

    # 1. Dependency extraction
    try:
        deps = await build_dependencies(project_id, db=db)
        summary["dependencies"] = len(deps)
        logger.info("[%s] analyze_facts: %d dependencies", project_id, len(deps))
    except Exception as exc:
        logger.exception("[%s] build_dependencies failed: %s", project_id, exc)
        summary["dependencies"] = 0
        summary["dependencies_error"] = str(exc)

    # 2. Derived facts (routes, DB usage, ML, env vars, …)
    try:
        facts = await derive_project_facts(project_id, db=db)
        summary["derived_facts"] = len(facts)
        logger.info("[%s] analyze_facts: %d derived facts", project_id, len(facts))
    except Exception as exc:
        logger.exception("[%s] derive_project_facts failed: %s", project_id, exc)
        summary["derived_facts"] = 0
        summary["derived_facts_error"] = str(exc)

    # 3. Commit timeline
    try:
        timeline = await build_timeline(project_id, db=db, ingest_cap=ingest_cap)
        summary["timeline_weeks"] = len(timeline.get("weekly_buckets", []))
        logger.info("[%s] analyze_facts: %d weeks in timeline", project_id, summary["timeline_weeks"])
    except Exception as exc:
        logger.exception("[%s] build_timeline failed: %s", project_id, exc)
        summary["timeline_weeks"] = 0
        summary["timeline_error"] = str(exc)

    # Persist analysis status on the project document
    any_error = any(k.endswith("_error") for k in summary)
    facts_status = "partial" if any_error else "ready"
    await upsert_project(
        db,
        {
            "repo_full_name": repo_full_name,
            "facts_status": facts_status,
        },
    )

    logger.info("[%s] analyze_facts complete (facts_status=%s): %s", project_id, facts_status, summary)
    return summary
