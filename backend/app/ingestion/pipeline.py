import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.db import get_db
from app.core.repositories import (
    upsert_commit,
    upsert_file,
    upsert_issue,
    upsert_pr,
    upsert_project,
)
from app.github.client import GitHubClient
from app.core.utils import redact
from app.ingestion.analyze import analyze_facts
from bson import ObjectId

logger = logging.getLogger("repomentor.ingestion")
logging.basicConfig(level=logging.INFO)

# Directory segments that should never have file content fetched
IGNORED_DIRS = {"node_modules", "dist", "build", ".git", "vendor", "__pycache__", ".venv", "venv"}

# Extensions for binary files and images that should not be parsed as text
BINARY_AND_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".dll",
    ".so", ".dylib", ".bin", ".pyc", ".wasm", ".class", ".jar", ".war",
}

# Large machine-generated lockfiles that lack architectural context
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "cargo.lock", "pipfile.lock", "gemfile.lock", "composer.lock",
}

# Project manifests that specify core dependencies and build environments
MANIFEST_NAMES = {
    "package.json", "requirements.txt", "pyproject.toml", "pom.xml",
    "dockerfile", "gemfile", "cargo.toml", "go.mod",
}

CONFIG_EXTENSIONS = {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".editorconfig"}

LANGUAGE_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C/C++ Header",
    ".cs": "C#",
    ".php": "PHP",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
}


def detect_language(path: str) -> str | None:
    _, ext = os.path.splitext(path.lower())
    return LANGUAGE_BY_EXT.get(ext)


def should_fetch_content(path: str, size: int | None) -> tuple[bool, bool]:
    """
    Determines whether a file's full text content should be downloaded.
    Returns (should_fetch, is_config).
    """
    parts = set(path.replace("\\", "/").split("/"))
    if parts & IGNORED_DIRS:
        return False, False

    filename = os.path.basename(path).lower()
    _, ext = os.path.splitext(filename)

    if ext in BINARY_AND_IMAGE_EXTS or filename in LOCKFILES:
        return False, False

    # Max 200KB content fetch cap
    if size is not None and size > 200 * 1024:
        return False, False

    # Check for manifests
    if filename in MANIFEST_NAMES or filename.startswith("docker-compose"):
        return True, True

    # Check for config files
    if filename.startswith(".env") or ext in CONFIG_EXTENSIONS or filename == "tsconfig.json":
        return True, True

    # Check for documentation and READMEs
    if (
        filename.startswith("readme")
        or path.lower().startswith("docs/")
        or path.lower().startswith("doc/")
        or ext in {".md", ".rst", ".txt"}
    ):
        return True, False

    # Check for recognized source code files
    if ext in LANGUAGE_BY_EXT:
        return True, False

    return False, False


async def ingest_project(
    project_id: str,
    repo_full_name: str,
    client: GitHubClient | None = None,
    db: AsyncIOMotorDatabase | None = None,
    ingest_cap: int = 500,
) -> dict[str, Any]:
    """
    Ingest a GitHub repository into MongoDB:
    1. Fetch repo metadata, set status=ingesting.
    2. Fetch tree, store every path, download filtered contents.
    3. Fetch last 500 commits (concurrency 5), PRs with files/comments, and issues.
    4. Record ingest_cap.
    5. Set status=ready on success or failed with error message on exception.
    """
    if db is None:
        db = get_db()

    owner, repo = repo_full_name.split("/")
    owns_client = False
    if client is None:
        client = GitHubClient()
        owns_client = True

    try:
        logger.info("[%s] Step 1: Starting ingestion for %s", project_id, repo_full_name)
        repo_info = await client.get_repo(owner, repo)

        project_doc = await upsert_project(
            db,
            {
                "repo_full_name": repo_full_name,
                "default_branch": repo_info.default_branch,
                "status": "ingesting",
                "ingest_cap": ingest_cap,
                "head_sha": None,
                "analysed_at": None,
                "error": None,
            },
        )

        # 2. Recursive tree and files
        logger.info("[%s] Step 2: Fetching git tree for branch %s", project_id, repo_info.default_branch)
        tree_items = await client.get_tree(owner, repo, sha=repo_info.default_branch, recursive=True)

        stored_files_count = 0
        fetched_content_count = 0

        for item in tree_items:
            # We only track file blobs in the files collection
            if item.type != "blob":
                continue

            fetch_content, is_config = should_fetch_content(item.path, item.size)
            content_text: str | None = None

            if fetch_content:
                try:
                    file_data = await client.get_file(owner, repo, item.path, ref=repo_info.default_branch)
                    content_text = file_data.decoded_text
                    fetched_content_count += 1
                except Exception as file_exc:
                    logger.warning("Could not fetch file content for %s: %s", item.path, file_exc)
                    content_text = None

            await upsert_file(
                db,
                {
                    "project_id": project_id,
                    "path": item.path,
                    "size": item.size or 0,
                    "language": detect_language(item.path),
                    "sha": item.sha,
                    "content": content_text,
                    "is_config": is_config,
                },
            )
            stored_files_count += 1

        logger.info(
            "[%s] Stored %d file entries (%d content bodies downloaded)",
            project_id,
            stored_files_count,
            fetched_content_count,
        )

        # 3. Commits with bounded concurrency of 5
        logger.info("[%s] Step 3: Fetching up to %d commits", project_id, ingest_cap)
        commit_summaries = await client.list_commits(owner, repo, limit=ingest_cap)
        sem = asyncio.Semaphore(5)

        async def fetch_and_store_commit(c_summary: Any) -> None:
            async with sem:
                try:
                    c_detail = await client.get_commit(owner, repo, c_summary.sha)
                    raw_data = c_detail.raw
                except Exception as c_exc:
                    logger.warning("Failed detailed fetch for commit %s: %s", c_summary.sha, c_exc)
                    raw_data = {}

            stats = raw_data.get("stats", {})
            # Only store file metadata (filename/status), omit diff patches to keep diffs out of storage
            files_meta = [
                {
                    "filename": f.get("filename"),
                    "status": f.get("status"),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "changes": f.get("changes", 0),
                }
                for f in raw_data.get("files", [])
            ]
            parents = [p["sha"] for p in raw_data.get("parents", []) if "sha" in p]

            await upsert_commit(
                db,
                {
                    "project_id": project_id,
                    "sha": c_summary.sha,
                    "date": c_summary.date,
                    "author": c_summary.author_name,
                    "message": c_summary.message,
                    "files_changed": files_meta,
                    "additions": stats.get("additions", 0),
                    "deletions": stats.get("deletions", 0),
                    "parents": parents,
                },
            )

        await asyncio.gather(*(fetch_and_store_commit(c) for c in commit_summaries))
        head_sha = commit_summaries[0].sha if commit_summaries else None
        logger.info("[%s] Stored %d commits (head_sha=%s)", project_id, len(commit_summaries), head_sha)

        # Pull requests with files and comments (omitting patch diffs)
        logger.info("[%s] Fetching pull requests (limit=%d)", project_id, ingest_cap)
        prs = await client.list_pulls(owner, repo, state="all", limit=ingest_cap)
        for pr in prs:
            pr_files = await client.get_pull_files(owner, repo, pr.number)
            pr_comments = await client.get_pull_comments(owner, repo, pr.number)

            files_summary = [
                {
                    "filename": f.filename,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "changes": f.changes,
                }
                for f in pr_files
            ]
            comments_summary = [
                {
                    "id": c.id,
                    "user": c.user,
                    "body": c.body,
                    "created_at": c.created_at,
                }
                for c in pr_comments
            ]

            await upsert_pr(
                db,
                {
                    "project_id": project_id,
                    "number": pr.number,
                    "title": pr.title,
                    "body": pr.raw.get("body"),
                    "state": pr.state,
                    "created_at": pr.created_at,
                    "merged_at": pr.merged_at,
                    "files": files_summary,
                    "comments": comments_summary,
                },
            )
        logger.info("[%s] Stored %d pull requests", project_id, len(prs))

        # Issues
        logger.info("[%s] Fetching issues (limit=%d)", project_id, ingest_cap)
        issues = await client.list_issues(owner, repo, state="all", limit=ingest_cap)
        for issue in issues:
            labels = [
                lbl["name"] for lbl in issue.raw.get("labels", []) if isinstance(lbl, dict) and "name" in lbl
            ]
            await upsert_issue(
                db,
                {
                    "project_id": project_id,
                    "number": issue.number,
                    "title": issue.title,
                    "body": issue.body,
                    "labels": labels,
                    "comments": [],
                },
            )
        logger.info("[%s] Stored %d issues", project_id, len(issues))

        # 4 & 5. Finalize status=ready
        now_iso = datetime.now(timezone.utc).isoformat()
        final_doc = await upsert_project(
            db,
            {
                "repo_full_name": repo_full_name,
                "status": "ready",
                "ingest_cap": ingest_cap,
                "head_sha": head_sha,
                "analysed_at": now_iso,
                "error": None,
            },
        )
        logger.info("[%s] Successfully ingested %s (status=ready)", project_id, repo_full_name)

        # Phase 2 analysis — failures must NOT mark ingestion failed
        try:
            await analyze_facts(
                project_id,
                db=db,
                ingest_cap=ingest_cap,
                repo_full_name=repo_full_name,
            )
        except Exception as analysis_exc:
            clean_err = redact(str(analysis_exc))
            logger.exception(
                "[%s] analyze_facts raised unexpectedly: %s", project_id, clean_err
            )
            # Ensure facts_status is recorded as failed on the project document
            query = {"_id": ObjectId(project_id)} if ObjectId.is_valid(project_id) else {"_id": project_id}
            res = await db.projects.update_one(
                query,
                {"$set": {"facts_status": "failed", "facts_error": clean_err}},
            )
            if res.matched_count == 0 and repo_full_name:
                await db.projects.update_one(
                    {"repo_full_name": repo_full_name},
                    {"$set": {"facts_status": "failed", "facts_error": clean_err}},
                )

        # Refresh project doc so returned document includes facts_status and facts_error
        query = {"_id": ObjectId(project_id)} if ObjectId.is_valid(project_id) else {"_id": project_id}
        refreshed_doc = await db.projects.find_one(query)
        if not refreshed_doc and repo_full_name:
            refreshed_doc = await db.projects.find_one({"repo_full_name": repo_full_name})

        return refreshed_doc or final_doc

    except Exception as exc:
        logger.exception("[%s] Ingestion failed for %s: %s", project_id, repo_full_name, exc)
        await upsert_project(
            db,
            {
                "repo_full_name": repo_full_name,
                "status": "failed",
                "error": str(exc),
            },
        )
        raise
    finally:
        if owns_client:
            await client.close()
