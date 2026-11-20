"""Dossier API router."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.models import (
    DossierClaimItem,
    DossierSectionDetailResponse,
    DossierSectionSummary,
    DossierSummaryResponse,
)
from app.api.resolve import resolve_project
from app.core.db import get_db
from app.dossier.schema import Claim, SECTIONS

router = APIRouter(prefix="/projects/{project_id:path}", tags=["dossier"])


@router.get(
    "/dossier/summary",
    response_model=DossierSummaryResponse,
)
async def get_dossier_summary(
    project_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> DossierSummaryResponse:
    """Retrieve summary counts for all sections of the project dossier."""
    project = await resolve_project(db, project_id)
    canonical_id = project["project_id"]

    res = db.dossiers.find_one({"project_id": canonical_id}, {"_id": 0})
    dossier = await res if inspect.isawaitable(res) else res

    version = dossier.get("version", 1) if dossier else 1
    generated_from_sha = dossier.get("generated_from_sha") if dossier else None
    sections_data = (dossier.get("sections") or {}) if dossier else {}

    sections_summary: list[DossierSectionSummary] = []
    seen = set()
    for name in SECTIONS:
        seen.add(name)
        claims = sections_data.get(name) or []
        facts = sum(1 for c in claims if str(c.get("tag", "")).lower() == "fact")
        inferred = sum(1 for c in claims if str(c.get("tag", "")).lower() == "inferred")
        uncertain = sum(1 for c in claims if str(c.get("tag", "")).lower() == "uncertain")
        user_edited = sum(1 for c in claims if str(c.get("source", "")).lower() == "user")
        agent_edited = sum(1 for c in claims if str(c.get("source", "")).lower() == "agent")
        sections_summary.append(
            DossierSectionSummary(
                name=name,
                claims=len(claims),
                facts=facts,
                inferred=inferred,
                uncertain=uncertain,
                user_edited=user_edited,
                agent=agent_edited,
            )
        )

    for name, claims in sections_data.items():
        if name not in seen:
            facts = sum(1 for c in claims if str(c.get("tag", "")).lower() == "fact")
            inferred = sum(1 for c in claims if str(c.get("tag", "")).lower() == "inferred")
            uncertain = sum(1 for c in claims if str(c.get("tag", "")).lower() == "uncertain")
            user_edited = sum(1 for c in claims if str(c.get("source", "")).lower() == "user")
            agent_edited = sum(1 for c in claims if str(c.get("source", "")).lower() == "agent")
            sections_summary.append(
                DossierSectionSummary(
                    name=name,
                    claims=len(claims),
                    facts=facts,
                    inferred=inferred,
                    uncertain=uncertain,
                    user_edited=user_edited,
                    agent=agent_edited,
                )
            )

    return DossierSummaryResponse(
        version=version,
        generated_from_sha=generated_from_sha,
        sections=sections_summary,
    )


@router.get(
    "/dossier/sections/{name}",
    response_model=DossierSectionDetailResponse,
)
async def get_dossier_section(
    project_id: str,
    name: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> DossierSectionDetailResponse:
    """Retrieve claims in a specific dossier section. 404 for an unknown section."""
    project = await resolve_project(db, project_id)
    canonical_id = project["project_id"]

    res = db.dossiers.find_one({"project_id": canonical_id}, {"_id": 0})
    dossier = await res if inspect.isawaitable(res) else res

    sections_data = (dossier.get("sections") or {}) if dossier else {}
    if name not in SECTIONS and name not in sections_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dossier section '{name}'",
        )

    raw_claims = sections_data.get(name) or []
    claim_items: list[DossierClaimItem] = []
    for c in raw_claims:
        details = c.get("details") or {}
        commit_val = details.get("commit")
        claim_items.append(
            DossierClaimItem(
                claim=c.get("claim", ""),
                tag=c.get("tag", "uncertain"),
                evidence=c.get("evidence") or [],
                confidence=float(c.get("confidence", 1.0)),
                source=c.get("source", "static"),
                commit=commit_val,
            )
        )

    return DossierSectionDetailResponse(name=name, claims=claim_items)


@router.post(
    "/dossier/static",
)
async def build_static_dossier_endpoint(
    project_id: str,
    force: bool = Query(default=False),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Any:
    """Build static dossier with build_static_dossier and save with save_dossier keeping non-static claims."""
    project = await resolve_project(db, project_id)
    canonical_id = project["project_id"]

    if project.get("status") != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project '{canonical_id}' is not ready (status: {project.get('status')})",
        )

    from app.dossier.builder import build_static_dossier, save_dossier

    # Check existing dossier
    existing_res = db.dossiers.find_one({"project_id": canonical_id})
    existing = await existing_res if inspect.isawaitable(existing_res) else existing_res

    existing_static_count = 0
    if existing:
        for claims in (existing.get("sections") or {}).values():
            existing_static_count += sum(1 for c in claims if c.get("source") == "static")

    # Build fresh static dossier with canonical_id
    new_dossier = await build_static_dossier(canonical_id, db)
    new_static_count = sum(len(claims) for claims in new_dossier.sections.values())

    # Guard: if new static claims are fewer than half of existing static claims, refuse with 409
    if existing_static_count > 0 and new_static_count < (existing_static_count / 2) and not force:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "new dossier looks incomplete",
                "existing": existing_static_count,
                "new": new_static_count,
            },
        )

    # Merge non-static claims (agent and user claims survive)
    if existing:
        for sec_name, raw_claims in (existing.get("sections") or {}).items():
            kept = [Claim.model_validate(c) for c in raw_claims if c.get("source") != "static"]
            new_dossier.sections.setdefault(sec_name, []).extend(kept)

    # Save backup before overwrite (keep last 5 per project)
    if existing:
        backup_doc = {k: v for k, v in existing.items() if k != "_id"}
        backup_doc["project_id"] = canonical_id
        backup_doc["backed_up_at"] = datetime.now(timezone.utc).isoformat()
        b_res = db.dossier_backups.insert_one(backup_doc)
        if inspect.isawaitable(b_res):
            await b_res

        cursor = db.dossier_backups.find({"project_id": canonical_id}).sort([("_id", -1)])
        backups = await cursor.to_list(length=None) if hasattr(cursor, "to_list") else (await cursor if inspect.isawaitable(cursor) else list(cursor))
        if len(backups) > 5:
            old_ids = [b["_id"] for b in backups[5:]]
            d_res = db.dossier_backups.delete_many({"_id": {"$in": old_ids}})
            if inspect.isawaitable(d_res):
                await d_res

    saved = await save_dossier(db, new_dossier)

    # Return version and claim count per section
    section_counts: dict[str, int] = {}
    for name in SECTIONS:
        section_counts[name] = len(saved.sections.get(name) or [])
    for name, claims in saved.sections.items():
        if name not in section_counts:
            section_counts[name] = len(claims)

    return {
        "version": saved.version,
        "sections": section_counts,
    }
