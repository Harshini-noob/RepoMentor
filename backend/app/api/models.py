from pydantic import BaseModel, Field
from typing import Any


class ProjectCreateRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL or owner/repo format")


class ProjectCreateResponse(BaseModel):
    project_id: str
    status: str


class ProjectCounts(BaseModel):
    files: int = 0
    commits: int = 0
    prs: int = 0
    issues: int = 0


class ProjectDetailResponse(BaseModel):
    id: str
    repo_full_name: str
    default_branch: str | None = None
    status: str
    facts_status: str | None = None
    ingest_cap: int | None = None
    head_sha: str | None = None
    analysed_at: str | None = None
    error: str | None = None
    counts: ProjectCounts


class ProjectSummary(BaseModel):
    id: str
    repo_full_name: str
    status: str
    facts_status: str | None = None
    default_branch: str | None = None
    analysed_at: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Analysis response models
# ---------------------------------------------------------------------------

class DependencyResponse(BaseModel):
    name: str
    version_spec: str | None = None
    ecosystem: str
    kind: str
    source_file: str


class DerivedFactResponse(BaseModel):
    kind: str
    name: str
    file_path: str
    line_start: int
    line_end: int
    details: dict[str, Any] = Field(default_factory=dict)


class TimelineTopFile(BaseModel):
    file: str
    touches: int


class TimelineWeekBucket(BaseModel):
    week_start: str
    commit_count: int
    additions: int
    deletions: int
    top_files: list[TimelineTopFile] = Field(default_factory=list)


class TimelineBigCommit(BaseModel):
    sha: str
    date: str | None = None
    author: str | None = None
    message: str | None = None
    additions: int = 0
    deletions: int = 0


class TimelineFirstSeen(BaseModel):
    sha: str
    date: str | None = None


class TimelineResponse(BaseModel):
    project_id: str
    weekly_buckets: list[TimelineWeekBucket] = Field(default_factory=list)
    biggest_commits: list[TimelineBigCommit] = Field(default_factory=list)
    dependency_first_seen: dict[str, TimelineFirstSeen] = Field(default_factory=dict)
    dir_first_seen: dict[str, TimelineFirstSeen] = Field(default_factory=dict)
    ingest_cap: int
    history_complete: bool

