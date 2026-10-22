import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to sys.path so app modules can be imported
ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.db import get_client, get_db
from app.core.repositories import init_indexes
from app.github.client import GitHubClient, RateLimitExceededError
from app.ingestion.pipeline import ingest_project


async def run_smoke_test(repo_full_name: str = "octocat/Hello-World") -> None:
    print(f"=== RepoMentor Phase 1 Smoke Test ===")
    print(f"Target repository: {repo_full_name}")

    # Determine whether to use real MongoDB or in-memory mongomock
    db = get_db()
    try:
        # Check database connectivity with a 2-second timeout
        await db.command("ping")
        print("Connected to MongoDB successfully.")
    except Exception as exc:
        print(f"Notice: Local MongoDB not available ({exc}).")
        print("Falling back to in-memory mongomock-motor for standalone verification.")
        from mongomock_motor import AsyncMongoMockClient
        mock_client = AsyncMongoMockClient()
        db = mock_client["repomentor_smoke"]

    await init_indexes(db)

    project_id = "smoke_project_1"
    async with GitHubClient() as client:
        print(f"\nTriggering ingest_project for {repo_full_name}...")
        try:
            project_doc = await ingest_project(
                project_id=project_id,
                repo_full_name=repo_full_name,
                client=client,
                db=db,
                ingest_cap=50,
            )
        except Exception as exc:
            print(f"\nIngestion encountered an error: {exc}")
            project_doc = await db.projects.find_one({"repo_full_name": repo_full_name}) or {}

    print("\n=== Project Document in MongoDB ===")
    print(f"Project ID: {project_id}")
    print(f"Repository: {project_doc.get('repo_full_name')}")
    print(f"Status:     {project_doc.get('status')}")
    print(f"Default Br: {project_doc.get('default_branch')}")
    print(f"Head SHA:   {project_doc.get('head_sha')}")
    print(f"Analysed:   {project_doc.get('analysed_at')}")
    print(f"Error:      {project_doc.get('error')}")

    # Query counts across collections
    projects_count = await db.projects.count_documents({})
    files_count = await db.files.count_documents({"project_id": project_id})
    commits_count = await db.commits.count_documents({"project_id": project_id})
    prs_count = await db.prs.count_documents({"project_id": project_id})
    issues_count = await db.issues.count_documents({"project_id": project_id})

    print("\n=== Collection Counts ===")
    print(f"  projects: {projects_count}")
    print(f"  files:    {files_count}")
    print(f"  commits:  {commits_count}")
    print(f"  prs:      {prs_count}")
    print(f"  issues:   {issues_count}")
    print("=========================")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "octocat/Hello-World"
    asyncio.run(run_smoke_test(target))
