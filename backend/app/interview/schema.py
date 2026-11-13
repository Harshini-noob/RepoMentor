"""Pydantic models for interview question generation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class KeyPoint(BaseModel):
    point: str
    kind: Literal["repo", "general"]
    support: int | None = None   # 1-based seed-line number; only set when kind="repo"
    note: str | None = None      # set when kind was downgraded from repo → general


class InterviewQuestion(BaseModel):
    question_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    round: str
    difficulty: int
    concept: str
    question: str
    key_points: list[KeyPoint]
    common_mistakes: list[str]
    seed: dict
    candidate_concepts: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
