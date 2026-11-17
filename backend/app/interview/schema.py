"""Pydantic models for interview question generation and evaluation."""

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


class PointResult(BaseModel):
    id: int
    point: str
    kind: Literal["repo", "general"]
    status: Literal["covered", "partial", "missing"]
    quote: str | None = None
    note: str | None = None


class Misconception(BaseModel):
    quote: str
    why: str
    seed_line: int | None = None
    contradicts_repo: bool = False


class Evaluation(BaseModel):
    points: list[PointResult]
    misconceptions: list[Misconception] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    advice: str = ""
    coverage: float
    score: int = Field(..., ge=0, le=4)
    notes: list[str] = Field(default_factory=list)


class InterviewAttempt(BaseModel):
    attempt_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question_id: str
    project_id: str
    concept: str
    difficulty: int
    answer: str
    evaluation: Evaluation
    model: str | None = None
    tokens: int = 0
    session_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
