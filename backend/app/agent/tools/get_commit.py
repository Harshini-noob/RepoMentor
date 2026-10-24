"""
Get commit metadata and explore changed files or an individual file patch live from GitHub.
Scoped to project_id.
"""

import logging
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput, make_commit_evidence
from app.core.db import get_db
from app.core.logging import redact
from app.core.repositories import find_project
from app.github.client import GitHubClient

logger = logging.getLogger("repomentor.agent.tools.get_commit")

MAX_PATCH_CHARS = 1800
MAX_CHANGED_FILES = 40
MAX_SUMMARY_CHARS = 1900


class GetCommitArgs(BaseModel):
    sha: str = Field(..., description="Commit SHA or 7-character short SHA to inspect.")
    path: str | None = Field(default=None, description="Optional changed file path to inspect.")
    offset: int = Field(default=0, ge=0, description="Character offset for paging through a file patch.")


class GetCommitTool(Tool):
    name = "get_commit"
    description = (
        "Use first without path to see which files changed; then call with path for the 1-3 files that matter. "
        "Removed lines (-) show what a commit replaced."
    )
    args_schema = GetCommitArgs

    def __init__(
        self,
        db: AsyncIOMotorDatabase | None = None,
        github_client: GitHubClient | None = None,
    ) -> None:
        self._db = db
        self._github_client = github_client

    def _get_db(self) -> AsyncIOMotorDatabase:
        return self._db if self._db is not None else get_db()

    async def run(self, project_id: str, args: GetCommitArgs) -> ToolOutput:
        db = self._get_db()
        raw_sha = args.sha.strip()
        if not raw_sha:
            return ToolOutput(text="ERROR: Commit SHA cannot be empty.", evidence_ids=[])

        project = await find_project(db, project_id)
        repo_full_name = project.get("repo_full_name") if project else None
        if not repo_full_name:
            return ToolOutput(
                text=f"ERROR: Project '{project_id}' not found.",
                evidence_ids=[],
            )

        commit_query: dict[str, Any] = {"project_id": project_id}
        if len(raw_sha) == 40:
            commit_query["sha"] = raw_sha
        else:
            commit_query["sha"] = {"$regex": f"^{raw_sha}"}
        commit_doc = await db.commits.find_one(commit_query)
        commit_sha = commit_doc["sha"] if commit_doc else raw_sha

        owner, repo = repo_full_name.split("/", 1)
        client = self._github_client
        owns_client = client is None
        if client is None:
            client = GitHubClient()

        try:
            commit_info = await client.get_commit(owner, repo, commit_sha)
        except Exception as exc:
            logger.warning("Could not fetch commit from GitHub: %s", redact(str(exc)))
            return ToolOutput(
                text="ERROR: Could not fetch commit from GitHub.",
                evidence_ids=[make_commit_evidence(commit_sha)],
            )
        finally:
            if owns_client:
                await client.close()

        raw_commit = commit_info.raw
        commit_data = raw_commit.get("commit") or {}
        author_data = commit_data.get("author") or {}
        github_author = raw_commit.get("author")
        author = (
            github_author.get("login")
            if isinstance(github_author, dict)
            else None
        ) or author_data.get("name") or (commit_doc or {}).get("author", "unknown")
        date = author_data.get("date") or (commit_doc or {}).get("date", "unknown")
        message = commit_data.get("message") or (commit_doc or {}).get("message", "")
        stats = raw_commit.get("stats") or {}
        additions = stats.get("additions", (commit_doc or {}).get("additions", 0))
        deletions = stats.get("deletions", (commit_doc or {}).get("deletions", 0))
        files = raw_commit.get("files") or []
        full_sha = commit_info.sha or commit_sha
        evidence_ids = [make_commit_evidence(full_sha)]

        if args.path is not None:
            requested_path = args.path.strip()
            file_entry = next(
                (
                    item
                    for item in files
                    if isinstance(item, dict) and item.get("filename") == requested_path
                ),
                None,
            )
            if file_entry is None:
                paths = [
                    item.get("filename")
                    for item in files[:10]
                    if isinstance(item, dict) and item.get("filename")
                ]
                changed_paths = "\n".join(f"- {path}" for path in paths) or "(none)"
                return ToolOutput(
                    text=f"ERROR: path not in this commit\nChanged files:\n{changed_paths}",
                    evidence_ids=evidence_ids,
                )

            patch = redact(str(file_entry.get("patch") or ""))
            removed = sum(
                1 for line in patch.splitlines()
                if line.startswith("-") and not line.startswith("---")
            )
            added = sum(
                1 for line in patch.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            start = args.offset
            end = min(start + MAX_PATCH_CHARS, len(patch))
            header = (
                f"File: {requested_path} (removed lines (-): {removed}; "
                f"added lines (+): {added})"
            )
            while True:
                patch_slice = patch[start:end]
                if end < len(patch):
                    paging_note = (
                        f"[showing chars {start + 1}-{end} of {len(patch)}; "
                        f"call again with offset={end}]"
                    )
                    text = f"{header}\n{patch_slice}\n{paging_note}"
                else:
                    text = f"{header}\n{patch_slice or '(no patch available)'}"
                if len(text) <= 2000 or end <= start:
                    break
                end -= max(1, len(text) - 2000)
            return ToolOutput(text=text, evidence_ids=evidence_ids)

        sorted_files = sorted(
            (item for item in files if isinstance(item, dict)),
            key=lambda item: (
                int(item.get("additions") or 0) + int(item.get("deletions") or 0)
            ),
            reverse=True,
        )[:MAX_CHANGED_FILES]
        table = [
            "| File | Status | Additions | Deletions |",
            "| --- | --- | ---: | ---: |",
        ]
        for item in sorted_files:
            status = item.get("status", "modified")
            if status == "deleted":
                status = "removed"
            table.append(
                f"| {item.get('filename', '')} | {status} | "
                f"{item.get('additions', 0)} | {item.get('deletions', 0)} |"
            )

        summary = [
            f"Commit: {full_sha}",
            f"Author: {author}",
            f"Date: {date}",
            f"Message: {message or '(no commit message)'}",
            f"Total additions: {additions}",
            f"Total deletions: {deletions}",
            "",
        ]
        footer = "Call get_commit again with path=<file> to see that file's diff."
        while True:
            shown_count = len(table) - 2
            file_count = min(len(files), MAX_CHANGED_FILES)
            caption = f"Changed files (showing top {shown_count} of {file_count}):"
            text = "\n".join([*summary, caption, *table, "", footer])
            if len(text) <= MAX_SUMMARY_CHARS or len(table) <= 2:
                break
            table.pop()
        return ToolOutput(text=text, evidence_ids=evidence_ids)
