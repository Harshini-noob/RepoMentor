from typing import Any
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.models import (
    DependencyResponse,
    DerivedFactResponse,
    TimelineResponse,
)
from app.core.db import get_db

router = APIRouter(prefix="/projects", tags=["analysis"])


async def _get_ready_project(id: str, db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """
    Validate that the project exists and is in 'ready' status.
    Raises 404 if unknown, 409 if not ready yet.
    """
    query = {"_id": ObjectId(id)} if ObjectId.is_valid(id) else {"_id": id}
    project = await db.projects.find_one(query)

    if not project:
        raise HTTPException(status_code=404, detail=f"Project with ID '{id}' not found")

    status = project.get("status")
    if status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Project '{id}' is not ready yet (current status: '{status}')",
        )

    return project


@router.get("/{id}/dependencies", response_model=list[DependencyResponse])
async def get_dependencies(
    id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[DependencyResponse]:
    """Retrieve all parsed dependencies for a project."""
    await _get_ready_project(id, db)

    deps: list[DependencyResponse] = []
    cursor = db.dependencies.find({"project_id": id}).sort("name", 1)
    async for doc in cursor:
        deps.append(
            DependencyResponse(
                name=doc["name"],
                version_spec=doc.get("version_spec"),
                ecosystem=doc["ecosystem"],
                kind=doc["kind"],
                source_file=doc["source_file"],
            )
        )
    return deps


@router.get("/{id}/facts", response_model=list[DerivedFactResponse])
async def get_derived_facts(
    id: str,
    kind: str | None = Query(default=None, description="Filter facts by kind (route, component, database, ml, entry_point, env_var, test)"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max number of facts to return"),
    skip: int = Query(default=0, ge=0, description="Number of facts to skip"),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[DerivedFactResponse]:
    """Retrieve statically derived code facts for a project, optionally filtered by kind."""
    await _get_ready_project(id, db)

    filter_query: dict[str, Any] = {"project_id": id}
    if kind:
        filter_query["kind"] = kind

    facts: list[DerivedFactResponse] = []
    cursor = db.derived_facts.find(filter_query).skip(skip).limit(limit)
    async for doc in cursor:
        facts.append(
            DerivedFactResponse(
                kind=doc["kind"],
                name=doc["name"],
                file_path=doc["file_path"],
                line_start=doc["line_start"],
                line_end=doc["line_end"],
                details=doc.get("details", {}),
            )
        )
    return facts


@router.get("/{id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> TimelineResponse:
    """Retrieve the commit timeline, weekly churn, and first-seen metadata for a project."""
    project = await _get_ready_project(id, db)

    timeline_doc = await db.timeline.find_one({"project_id": id})
    if not timeline_doc:
        timeline_doc = {
            "project_id": id,
            "weekly_buckets": [],
            "biggest_commits": [],
            "dependency_first_seen": {},
            "dir_first_seen": {},
            "ingest_cap": project.get("ingest_cap", 500),
            "history_complete": True,
        }

    return TimelineResponse(**timeline_doc)
