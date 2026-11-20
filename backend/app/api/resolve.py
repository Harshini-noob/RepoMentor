"""Project resolver helper to ensure canonical project_id usage across all API routes."""

from __future__ import annotations

import inspect
import re
from typing import Any
from urllib.parse import unquote

from bson import ObjectId
from fastapi import HTTPException, status


async def resolve_project(db: Any, ident: str) -> dict[str, Any]:
    """
    Resolve a project document by its canonical project_id, repo_full_name, or _id.

    Accepts:
      - project_id field (e.g. 'proj_harshini_noob_llm_cost_copilot')
      - repo_full_name (e.g. 'Harshini-noob/llm-cost-copilot', URL-decoded, case-insensitive)
      - Mongo _id (ObjectId or string)

    Returns the project document with a guaranteed canonical 'project_id' field.
    Raises HTTPException(404) if no project matches.
    """
    if not ident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project identifier cannot be empty",
        )

    raw = unquote(ident).strip()

    # 1. Exact match on project_id field
    res = db.projects.find_one({"project_id": raw})
    project = await res if inspect.isawaitable(res) else res

    # 2. Match on repo_full_name (owner/repo, case-insensitive)
    if not project and "/" in raw:
        pattern = re.compile(f"^{re.escape(raw)}$", re.IGNORECASE)
        res = db.projects.find_one({"repo_full_name": pattern})
        project = await res if inspect.isawaitable(res) else res

    # 3. Match on _id (ObjectId or string)
    if not project:
        query: list[dict[str, Any]] = [{"_id": raw}]
        if ObjectId.is_valid(raw):
            query.append({"_id": ObjectId(raw)})
        res = db.projects.find_one({"$or": query})
        project = await res if inspect.isawaitable(res) else res

    # 4. Fallback match on repo_full_name even if raw didn't contain slash
    if not project:
        pattern = re.compile(f"^{re.escape(raw)}$", re.IGNORECASE)
        res = db.projects.find_one({"repo_full_name": pattern})
        project = await res if inspect.isawaitable(res) else res

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID '{ident}' not found",
        )

    # Ensure canonical project_id is always present as a string
    if not project.get("project_id"):
        project["project_id"] = str(project["_id"])

    return project
