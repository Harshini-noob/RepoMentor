"""Evidence API router for inspecting grounded repository evidence."""

from __future__ import annotations

import inspect
import re
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.resolve import resolve_project
from app.core.db import get_db

router = APIRouter(prefix="/projects/{project_id}", tags=["evidence"])


@router.get("/evidence")
async def get_evidence(
    project_id: str,
    id: str = Query(..., description="Evidence identifier"),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve detailed evidence record for a given evidence id."""
    project = await resolve_project(db, project_id)
    canonical_id = project["project_id"]
    evidence_id = id.strip()

    # 1. file:<path>#L<a>-<b> or #L<a> or #L<a>-L<b>
    file_match = re.match(r"^file:([^#]+)(?:#L?(\d+)(?:-L?(\d+))?)?$", evidence_id)
    if file_match and not evidence_id.startswith("commit:"):
        path = file_match.group(1).strip()
        a_str = file_match.group(2)
        b_str = file_match.group(3)

        file_res = db.files.find_one({
            "project_id": canonical_id,
            "path": path,
        })
        file_doc = await file_res if inspect.isawaitable(file_res) else file_res
        if not file_doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        content = file_doc.get("content")
        if content is None:
            return {
                "kind": "file",
                "path": path,
                "available": False,
                "reason": "content was not stored",
            }

        start = int(a_str) if a_str else 1
        end = int(b_str) if b_str else (int(a_str) if a_str else 60)
        if start > end:
            start, end = end, start

        all_lines = content.splitlines()
        ctx_start = max(1, start - 2)
        ctx_end = min(len(all_lines), end + 2) if all_lines else 1
        if ctx_end < ctx_start:
            ctx_end = ctx_start

        # Clip to at most 60 lines
        if (ctx_end - ctx_start + 1) > 60:
            ctx_end = ctx_start + 59

        lines = []
        for n in range(ctx_start, ctx_end + 1):
            idx = n - 1
            text = all_lines[idx] if (0 <= idx < len(all_lines)) else ""
            in_range = (start <= n <= end)
            lines.append({"n": n, "text": text, "in_range": in_range})

        return {
            "kind": "file",
            "path": path,
            "language": file_doc.get("language") or "plaintext",
            "start": start,
            "end": end,
            "lines": lines,
        }

    # 2. commit:<sha7> or commit:<sha7>:<path>
    commit_match = re.match(r"^commit:([0-9a-fA-F]{7,40})(?::(.+))?$", evidence_id)
    if commit_match:
        sha_prefix = commit_match.group(1)
        sub_path = commit_match.group(2)

        commit_res = db.commits.find_one({
            "project_id": canonical_id,
            "sha": {"$regex": f"^{re.escape(sha_prefix)}", "$options": "i"},
        })
        commit_doc = await commit_res if inspect.isawaitable(commit_res) else commit_res
        if not commit_doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        files_changed = commit_doc.get("files_changed")
        files_changed_list = (files_changed or [])[:30] if isinstance(files_changed, list) else []

        resp: dict[str, Any] = {
            "kind": "commit",
            "sha": commit_doc["sha"],
            "date": commit_doc.get("date"),
            "author": commit_doc.get("author"),
            "message": (commit_doc.get("message") or "")[:500],
            "additions": commit_doc.get("additions", 0),
            "deletions": commit_doc.get("deletions", 0),
            "files_changed": files_changed_list,
        }
        if sub_path is not None:
            resp["path"] = sub_path
            if files_changed is not None and isinstance(files_changed, list):
                resp["path_in_commit"] = sub_path in files_changed
        return resp

    # 3. dep:<name>
    dep_match = re.match(r"^dep:(.+)$", evidence_id)
    if dep_match:
        dep_name = dep_match.group(1).strip()
        cursor = db.dependencies.find({
            "project_id": canonical_id,
            "name": dep_name,
        })
        docs = await cursor.to_list(length=None) if hasattr(cursor, "to_list") else (await cursor if inspect.isawaitable(cursor) else list(cursor))
        if not docs:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        items = [
            {
                "name": d.get("name"),
                "version_spec": d.get("version_spec"),
                "dep_kind": d.get("kind") or d.get("dep_kind") or "runtime",
                "source_file": d.get("source_file"),
            }
            for d in docs
        ]
        return {"kind": "dependency", "items": items}

    # 4. fact:<kind>:<name>
    fact_match = re.match(r"^fact:([^:]+):(.+)$", evidence_id)
    if fact_match:
        fact_kind = fact_match.group(1).strip()
        fact_name = fact_match.group(2).strip()

        cursor = db.derived_facts.find({
            "project_id": canonical_id,
            "kind": fact_kind,
            "name": fact_name,
        })
        docs = await cursor.to_list(length=None) if hasattr(cursor, "to_list") else (await cursor if inspect.isawaitable(cursor) else list(cursor))
        if not docs:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        # non-test first, at most 20
        docs.sort(key=lambda d: 1 if d.get("in_test") else 0)
        items = [
            {
                "file_path": d.get("file_path"),
                "line_start": d.get("line_start"),
                "line_end": d.get("line_end"),
                "in_test": bool(d.get("in_test")),
            }
            for d in docs[:20]
        ]
        return {"kind": "fact", "items": items}

    # 5. repo:<owner/name>
    repo_match = re.match(r"^repo:(.+)$", evidence_id)
    if repo_match:
        repo_str = repo_match.group(1).strip()
        proj_repo = project.get("repo_full_name") or ""
        if repo_str.lower() != proj_repo.lower() and repo_str != canonical_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        files_cur = db.files.find({"project_id": canonical_id}, {"language": 1})
        files_docs = await files_cur.to_list(length=None) if hasattr(files_cur, "to_list") else (await files_cur if inspect.isawaitable(files_cur) else list(files_cur))
        langs = Counter((f.get("language") or "Other") for f in files_docs)

        commits_count_res = db.commits.count_documents({"project_id": canonical_id})
        commits_count = await commits_count_res if inspect.isawaitable(commits_count_res) else commits_count_res

        return {
            "kind": "repo",
            "repo": repo_str,
            "default_branch": project.get("default_branch"),
            "languages": dict(langs.most_common(10)),
            "commits": commits_count,
            "files": len(files_docs),
        }

    # Unknown format
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="unsupported evidence id",
    )
