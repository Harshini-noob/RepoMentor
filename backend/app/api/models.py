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
    facts_error: str | None = None
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
    facts_error: str | None = None
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


# ---------------------------------------------------------------------------
# Phase 8: Mentor API response models
# ---------------------------------------------------------------------------

class ChatSessionCreateResponse(BaseModel):
    session_id: str


class ChatMessageItem(BaseModel):
    role: str
    content: str
    ts: Any = None


class ChatSessionDetailResponse(BaseModel):
    session_id: str
    created_at: Any = None
    messages: list[ChatMessageItem] = Field(default_factory=list)


class ChatSessionSummaryItem(BaseModel):
    session_id: str
    created_at: Any = None
    updated_at: Any = None
    turns: int
    first_question: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Phase 8: Dossier API response models
# ---------------------------------------------------------------------------

class DossierSectionSummary(BaseModel):
    name: str
    claims: int
    facts: int
    inferred: int
    uncertain: int
    user_edited: int


class DossierSummaryResponse(BaseModel):
    version: int
    generated_from_sha: str | None = None
    sections: list[DossierSectionSummary] = Field(default_factory=list)


class DossierClaimItem(BaseModel):
    claim: str
    tag: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    source: str = "static"
    commit: str | None = None


class DossierSectionDetailResponse(BaseModel):
    name: str
    claims: list[DossierClaimItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 8: Interview API response models
# ---------------------------------------------------------------------------

class ProfileMissItem(BaseModel):
    point: str
    kind: str = "general"
    count: int = 1
    last_seen: Any = None


class ProfileConceptItem(BaseModel):
    concept: str
    status: str
    score: float
    attempts: int
    last_seen: Any = None
    misses: list[ProfileMissItem] = Field(default_factory=list)


class ProfileResponse(BaseModel):
    concepts: list[ProfileConceptItem] = Field(default_factory=list)
    revise_first: list[str] = Field(default_factory=list)


class InterviewPlanSlot(BaseModel):
    slot: int
    concept: str | None = None
    difficulty: int
    reason: str
    first_seed_claim: str = ""
    claim: str = ""


class InterviewPlanResponse(BaseModel):
    slots: list[InterviewPlanSlot] = Field(default_factory=list)


class InterviewSessionSummaryItem(BaseModel):
    session_id: str
    status: str
    plan_mode: str
    created_at: Any = None
    answered: int = 0
    answered_count: int = 0
    average_score: float | None = None


class InterviewReportResponse(BaseModel):
    answered: int
    skipped: int
    average_score: float | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    per_concept: dict[str, Any] = Field(default_factory=dict)
    concept_scores: dict[str, Any] = Field(default_factory=dict)
    weakest_concepts: list[str] = Field(default_factory=list)
    revise: list[dict[str, Any]] = Field(default_factory=list)
    unverified_count: int = 0
    lessons_given: int = 0
    before_after: list[dict[str, Any]] = Field(default_factory=list)
    average_improvement: float | None = None

