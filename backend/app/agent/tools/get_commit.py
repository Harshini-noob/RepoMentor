"""
Get commit tool: retrieves stored commit metadata plus unified diff / patch
fetched LIVE through the existing GitHubClient.
Diffs are not stored; patch is truncated to 3000 chars with an explicit notice.
Scoped to project_id.
"""

from typing import Any
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.agent.tools.base import Tool, ToolOutput, make_commit_evidence
from app.core.db import get_db
from app.core.logging import redact
from app.github.client import GitHubClient, GitHubError, RateLimitExceededError

MAX_PATCH_CHARS = 3000


class GetCommitArgs(BaseModel):
    sha: str = Field(..., description="Commit SHA or 7-character short SHA to inspect.")


class GetCommitTool(Tool):
    name = "get_commit"
    description = (
        "Get detailed commit metadata from stored data plus the unified diff/patch "
        "fetched live from GitHub. Patch is capped at 3000 characters."
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

        # 1. Resolve repository full name from project doc
        p_query = {"_id": ObjectId(project_id)} if ObjectId.is_valid(project_id) else {"_id": project_id}
        project = await db.projects.find_one(p_query)
        if not project:
            project = await db.projects.find_one({"repo_full_name": project_id})

        repo_full_name = project.get("repo_full_name") if project else None
        if not repo_full_name:
            return ToolOutput(
                text=f"ERROR: Project '{project_id}' not found.",
                evidence_ids=[],
            )

        # 2. Find commit metadata from stored commits
        commit_query: dict[str, Any] = {"project_id": project_id}
        if len(raw_sha) == 40:
            commit_query["sha"] = raw_sha
        else:
            commit_query["sha"] = {"$regex": f"^{raw_sha}"}

        commit_doc = await db.commits.find_one(commit_query)
        full_sha = commit_doc["sha"] if commit_doc else raw_sha
        author = commit_doc.get("author", "unknown") if commit_doc else "unknown"
        date = commit_doc.get("date", "unknown") if commit_doc else "unknown"
        message = commit_doc.get("message", "") if commit_doc else ""
        additions = commit_doc.get("additions", 0) if commit_doc else 0
        deletions = commit_doc.get("deletions", 0) if commit_doc else 0

        # 3. Live fetch diff/patch from GitHubClient
        owner, repo = repo_full_name.split("/")
        client = self._github_client
        owns_client = False
        if client is None:
            client = GitHubClient()
            owns_client = True

        patch_text = ""
        try:
            patch_raw = await client.get_commit_patch(owner, repo, full_sha)
            # Redact sensitive tokens in diff if any
            clean_patch = redact(patch_raw)

            if len(clean_patch) > MAX_PATCH_CHARS:
                excess = len(clean_patch) - MAX_PATCH_CHARS
                patch_text = (
                    f"{clean_patch[:MAX_PATCH_CHARS]}\n"
                    f"[patch truncated: showing first {MAX_PATCH_CHARS} characters, {excess} more characters omitted]"
                )
            else:
                patch_text = clean_patch
        except (GitHubError, RateLimitExceededError) as gh_err:
            patch_text = f"[Could not fetch live diff from GitHub: {redact(str(gh_err))}]"
        except Exception as exc:
            patch_text = f"[Could not fetch live diff from GitHub: {redact(str(exc))}]"
        finally:
            if owns_client:
                await client.close()

        lines = [
            f"Commit: {full_sha}",
            f"Author: {author}",
            f"Date: {date}",
            f"Stats: +{additions} / -{deletions}",
            "Message:",
            message or "(no commit message)",
            "",
            "Patch Diff (live from GitHub):",
            patch_text or "(empty patch)",
        ]

        return ToolOutput(
            text="\n".join(lines),
            evidence_ids=[make_commit_evidence(full_sha)],
        )
