from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SECTIONS = [
    "identity",
    "technologies",
    "components",
    "apis",
    "data",
    "features",
    "decisions",
    "timeline",
    "quality",
    "interview_map",
]


class Claim(BaseModel):
    claim: str
    tag: Literal["fact", "inferred", "uncertain"]
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    source: Literal["static", "agent", "user"] = "static"
    details: dict[str, Any] = Field(default_factory=dict)


class Dossier(BaseModel):
    project_id: str
    version: int = 1
    generated_from_sha: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sections: dict[str, list[Claim]] = Field(
        default_factory=lambda: {name: [] for name in SECTIONS}
    )