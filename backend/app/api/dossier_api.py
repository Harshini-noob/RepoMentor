"""Dossier API router."""

from __future__ import annotations

import inspect
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.models import (
    DossierClaimItem,
    DossierSectionDetailResponse,
    DossierSectionSummary,
    DossierSummaryResponse,
)
from app.core.db import get_db
from app.dossier.schema import SECTIONS

router = APIRouter(prefix="/projects/{project_id}", tags=["dossier"])


async def _get_project_or_404(project_id: str, db: Any) -> dict[str, Any]:
    query = {"_id": ObjectId(project_id)} if ObjectId.is_valid(project_id) else {"_id": project_id}
    res = db.projects.find_one(query)
    project = await res if inspect.isawaitable(res) else res
    if not project:
        res2 = db.projects.find_one({"id": project_id})
        project = await res2 if inspect.isawaitable(res2) else res2
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID '{project_id}' not found",
        )
    return project


@router.get(
    "/dossier/summary",
    response_model=DossierSummaryResponse,
)
async def get_dossier_summary(
    project_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> DossierSummaryResponse:
    """Retrieve summary counts for all sections of the project dossier."""
    await _get_project_or_404(project_id, db)

    res = db.dossiers.find_one({"project_id": project_id}, {"_id": 0})
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
        sections_summary.append(
            DossierSectionSummary(
                name=name,
                claims=len(claims),
                facts=facts,
                inferred=inferred,
                uncertain=uncertain,
                user_edited=user_edited,
            )
        )

    for name, claims in sections_data.items():
        if name not in seen:
            facts = sum(1 for c in claims if str(c.get("tag", "")).lower() == "fact")
            inferred = sum(1 for c in claims if str(c.get("tag", "")).lower() == "inferred")
            uncertain = sum(1 for c in claims if str(c.get("tag", "")).lower() == "uncertain")
            user_edited = sum(1 for c in claims if str(c.get("source", "")).lower() == "user")
            sections_summary.append(
                DossierSectionSummary(
                    name=name,
                    claims=len(claims),
                    facts=facts,
                    inferred=inferred,
                    uncertain=uncertain,
                    user_edited=user_edited,
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
    await _get_project_or_404(project_id, db)

    res = db.dossiers.find_one({"project_id": project_id}, {"_id": 0})
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
