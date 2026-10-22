import re
from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.models import (
    ProjectCounts,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetailResponse,
    ProjectSummary,
)
from app.core.db import get_db
from app.core.repositories import upsert_project
from app.ingestion.pipeline import ingest_project

router = APIRouter(prefix="/projects", tags=["projects"])


def parse_github_url(url: str) -> str:
    """
    Parse and validate owner/repo from GitHub URL or raw owner/repo string.
    Supports formats:
      - https://github.com/owner/repo
      - https://github.com/owner/repo.git
      - github.com/owner/repo
      - git@github.com:owner/repo.git
      - owner/repo
    """
    cleaned = url.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Repository URL cannot be empty")

    # Strip protocol and domain prefixes if present
    cleaned = re.sub(r"^(https?:\/\/)?(www\.)?github\.com\/", "", cleaned)
    cleaned = re.sub(r"^git@github\.com:", "", cleaned)

    # Strip .git suffix and trailing slash
    cleaned = re.sub(r"\.git$", "", cleaned).rstrip("/")

    parts = cleaned.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid GitHub repository URL '{url}'. Expected format: https://github.com/owner/repo or owner/repo",
        )

    owner, repo = parts[0], parts[1]
    # Validate alphanumeric characters, hyphens, underscores, dots
    if not re.match(r"^[a-zA-Z0-9_.-]+$", owner) or not re.match(r"^[a-zA-Z0-9_.-]+$", repo):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid characters in repository name '{url}'",
        )

    return f"{owner}/{repo}"


@router.post("", response_model=ProjectCreateResponse, status_code=202)
async def create_project(
    payload: ProjectCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ProjectCreateResponse:
    repo_full_name = parse_github_url(payload.repo_url)

    # Look up existing project or create new one
    project = await db.projects.find_one({"repo_full_name": repo_full_name})
    if not project:
        project = await upsert_project(
            db,
            {
                "repo_full_name": repo_full_name,
                "status": "queued",
                "default_branch": None,
                "ingest_cap": None,
                "head_sha": None,
                "analysed_at": None,
                "error": None,
            },
        )

    project_id = str(project["_id"])

    # Trigger background ingestion if not currently in progress
    if project.get("status") != "ingesting":
        background_tasks.add_task(ingest_project, project_id=project_id, repo_full_name=repo_full_name, db=db)

    return ProjectCreateResponse(project_id=project_id, status=project.get("status", "queued"))


@router.get("/{id}", response_model=ProjectDetailResponse)
async def get_project(
    id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ProjectDetailResponse:
    # Support both Mongo ObjectId and raw string IDs
    query = {"_id": ObjectId(id)} if ObjectId.is_valid(id) else {"_id": id}
    project = await db.projects.find_one(query)

    if not project:
        raise HTTPException(status_code=404, detail=f"Project with ID '{id}' not found")

    # Aggregate counts across related collections
    files_count = await db.files.count_documents({"project_id": id})
    commits_count = await db.commits.count_documents({"project_id": id})
    prs_count = await db.prs.count_documents({"project_id": id})
    issues_count = await db.issues.count_documents({"project_id": id})

    return ProjectDetailResponse(
        id=str(project["_id"]),
        repo_full_name=project["repo_full_name"],
        default_branch=project.get("default_branch"),
        status=project.get("status", "unknown"),
        ingest_cap=project.get("ingest_cap"),
        head_sha=project.get("head_sha"),
        analysed_at=project.get("analysed_at"),
        error=project.get("error"),
        counts=ProjectCounts(
            files=files_count,
            commits=commits_count,
            prs=prs_count,
            issues=issues_count,
        ),
    )


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[ProjectSummary]:
    projects: list[ProjectSummary] = []
    cursor = db.projects.find().sort("repo_full_name", 1)
    async for doc in cursor:
        projects.append(
            ProjectSummary(
                id=str(doc["_id"]),
                repo_full_name=doc["repo_full_name"],
                status=doc.get("status", "unknown"),
                default_branch=doc.get("default_branch"),
                analysed_at=doc.get("analysed_at"),
                error=doc.get("error"),
            )
        )
    return projects
