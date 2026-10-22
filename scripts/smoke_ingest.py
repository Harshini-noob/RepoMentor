import asyncio
import os
import re
import sys
from pathlib import Path

# Add backend directory to sys.path so app modules can be imported
ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings
from app.core.db import get_client, get_db
from app.core.repositories import init_indexes
from app.github.client import GitHubClient, RateLimitExceededError
from app.ingestion.pipeline import ingest_project


async def run_smoke_test(repo_arg: str = "octocat/Hello-World") -> None:
    # 1. Normalize repository name
    cleaned = repo_arg.strip()
    cleaned = re.sub(r"^(https?:\/\/)?(www\.)?github\.com\/", "", cleaned)
    cleaned = re.sub(r"^git@github\.com:", "", cleaned)
    cleaned = re.sub(r"\.git$", "", cleaned).rstrip("/")
    repo_full_name = cleaned or "octocat/Hello-World"

    # 2. Derive stable project_id from repo name so re-runs reuse the same project
    stable_suffix = re.sub(r"[^a-zA-Z0-9_]", "_", repo_full_name.lower())
    project_id = f"proj_{stable_suffix}"

    print(f"=== RepoMentor Smoke Ingestion ===")
    print(f"Target repository: {repo_full_name}")
    print(f"Stable Project ID: {project_id}")

    # 3. Connect to real MongoDB from .env and print which one is used
    settings = get_settings()
    print(f"MongoDB configured in .env: {settings.mongodb_uri} (db: {settings.mongodb_db})")

    db = get_db()
    try:
        await db.command("ping")
        print(f"Connected to real MongoDB: {settings.mongodb_uri}/{settings.mongodb_db}")
    except Exception as exc:
        print(f"Notice: Could not connect to real MongoDB at {settings.mongodb_uri} ({exc}).")
        print("Falling back to in-memory mongomock-motor for standalone verification.")
        from mongomock_motor import AsyncMongoMockClient
        mock_client = AsyncMongoMockClient()
        db = mock_client[settings.mongodb_db]

    await init_indexes(db)

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

    # Query fresh document from DB to include any post-ingestion updates
    project_doc = await db.projects.find_one({"repo_full_name": repo_full_name}) or project_doc

    print("\n=== Project Document in MongoDB ===")
    print(f"Project ID:   {project_id}")
    print(f"Repository:   {project_doc.get('repo_full_name')}")
    print(f"Status:       {project_doc.get('status')}")
    print(f"Facts Status: {project_doc.get('facts_status')}")
    print(f"Facts Error:  {project_doc.get('facts_error')}")
    print(f"Default Br:   {project_doc.get('default_branch')}")
    print(f"Head SHA:     {project_doc.get('head_sha')}")
    print(f"Analysed:     {project_doc.get('analysed_at')}")
    print(f"Error:        {project_doc.get('error')}")

    # Query counts across collections
    projects_count = await db.projects.count_documents({})
    files_count = await db.files.count_documents({"project_id": project_id})
    commits_count = await db.commits.count_documents({"project_id": project_id})
    prs_count = await db.prs.count_documents({"project_id": project_id})
    issues_count = await db.issues.count_documents({"project_id": project_id})
    dependencies_count = await db.dependencies.count_documents({"project_id": project_id})
    facts_count = await db.derived_facts.count_documents({"project_id": project_id})
    timeline_count = await db.timeline.count_documents({"project_id": project_id})

    timeline_doc = await db.timeline.find_one({"project_id": project_id})
    weekly_buckets = len(timeline_doc.get("weekly_buckets", [])) if timeline_doc else 0

    print("\n=== Collection Counts ===")
    print(f"  projects:      {projects_count}")
    print(f"  files:         {files_count}")
    print(f"  commits:       {commits_count}")
    print(f"  prs:           {prs_count}")
    print(f"  issues:        {issues_count}")
    print(f"  dependencies:  {dependencies_count}")
    print(f"  derived_facts: {facts_count}")
    print(f"  timeline:      {timeline_count} (containing {weekly_buckets} weekly buckets)")
    print("=========================")

    # Group derived facts by kind
    facts_cursor = db.derived_facts.find({"project_id": project_id})
    facts_by_kind: dict[str, int] = {}
    async for fact in facts_cursor:
        kind = fact.get("kind", "unknown")
        facts_by_kind[kind] = facts_by_kind.get(kind, 0) + 1

    print("\n=== Derived Facts Grouped by Kind ===")
    if facts_by_kind:
        for kind, count in sorted(facts_by_kind.items()):
            print(f"  - {kind}: {count}")
    else:
        print("  None detected")
    print("=====================================")

    # All dependencies (name, version, kind, source file)
    print("\n=== All Dependencies ===")
    deps_cursor = db.dependencies.find({"project_id": project_id}).sort("name", 1)
    deps_list = await deps_cursor.to_list(length=None)
    if deps_list:
        for d in deps_list:
            name = d.get("name")
            version = d.get("version_spec") or "unspecified"
            kind = d.get("kind")
            source_file = d.get("source_file")
            print(f"  - {name} ({version}) [{kind}] from {source_file}")
    else:
        print("  None detected")
    print("========================")

    # 10 sample facts with kind, name, file path and line_start
    print("\n=== Sample Facts (up to 10) ===")
    sample_cursor = db.derived_facts.find({"project_id": project_id}).limit(10)
    sample_facts = await sample_cursor.to_list(length=10)
    if sample_facts:
        for i, fact in enumerate(sample_facts, start=1):
            kind = fact.get("kind")
            name = fact.get("name")
            file_path = fact.get("file_path")
            line_start = fact.get("line_start")
            print(f"  {i}. [{kind}] {name} in {file_path}:{line_start}")
    else:
        print("  None detected")
    print("===============================")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "octocat/Hello-World"
    asyncio.run(run_smoke_test(target))
