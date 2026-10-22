from typing import Any
from pydantic import BaseModel, Field


class RepoInfo(BaseModel):
    name: str
    full_name: str
    owner: str
    default_branch: str
    description: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TreeItem(BaseModel):
    path: str
    mode: str
    type: str  # "blob" or "tree"
    sha: str
    size: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CommitInfo(BaseModel):
    sha: str
    message: str
    author_name: str | None = None
    author_email: str | None = None
    date: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PullRequestInfo(BaseModel):
    number: int
    title: str
    state: str
    user: str | None = None
    created_at: str | None = None
    merged_at: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PullFile(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)


class PullComment(BaseModel):
    id: int
    user: str | None = None
    body: str | None = None
    created_at: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class IssueInfo(BaseModel):
    number: int
    title: str
    state: str
    user: str | None = None
    created_at: str | None = None
    body: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class FileContent(BaseModel):
    path: str
    sha: str
    size: int
    content: str | None = None
    encoding: str | None = None
    decoded_text: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
