"""Interview session persistence and lifecycle management."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from typing import Any

from app.interview.plan import build_plan as build_plan_adaptive
from app.interview.profile import get_profile

ROUNDS_ORDER = [
    "history",
    "tech",
    "architecture",
    "history",
    "tech",
    "architecture",
    "history",
    "tech",
]

DIFFICULTY_OFFSETS = [0, 0, 1, 1, 2]


def build_plan(n: int, base_difficulty: int) -> list[dict[str, Any]]:
    """Build a question plan from n (1 to 8) slots with round order and difficulty ramp."""
    n = max(1, min(8, n))
    plan: list[dict[str, Any]] = []
    for i in range(n):
        r = ROUNDS_ORDER[i % len(ROUNDS_ORDER)]
        offset = DIFFICULTY_OFFSETS[i] if i < len(DIFFICULTY_OFFSETS) else DIFFICULTY_OFFSETS[-1]
        diff = max(1, min(5, base_difficulty + offset))
        plan.append({
            "round": r,
            "difficulty": diff,
            "concept": None,
            "reason": "no history yet",
        })
    return plan


async def create_session(
    db: Any,
    project_id: str,
    n: int = 5,
    base_difficulty: int = 2,
    adaptive: bool = True,
    teach: bool = True,
    teach_below: int = 2,
    max_lessons: int = 2,
) -> dict[str, Any]:
    """Create a new interview session."""
    plan_mode = "fixed"
    plan = None

    if adaptive:
        profile = await get_profile(db, project_id)
        has_rated = False
        if profile and isinstance(profile.get("concepts"), dict):
            for c_name, c_info in profile["concepts"].items():
                if c_name != "general" and isinstance(c_info, dict) and c_info.get("attempts", 0) >= 1:
                    has_rated = True
                    break
        if has_rated:
            raw_dossier = db.dossiers.find_one({"project_id": project_id}, {"_id": 0})
            dossier = await raw_dossier if inspect.isawaitable(raw_dossier) else raw_dossier
            plan = build_plan_adaptive(profile, n, base_difficulty, dossier=dossier)
            plan_mode = "adaptive"

    if plan is None:
        plan = build_plan(n, base_difficulty)
        plan_mode = "fixed"

    session_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "session_id": session_id,
        "project_id": project_id,
        "status": "active",
        "stage": "need_question",
        "pending": None,
        "cycle": None,
        "teach": teach,
        "teach_below": teach_below,
        "max_lessons": max_lessons,
        "lessons_given": 0,
        "plan_mode": plan_mode,
        "plan": plan,
        "current": 0,
        "items": [],
        "notes": [],
        "created_at": datetime.now(timezone.utc),
        "finished_at": None,
    }
    res = db.interview_sessions.insert_one(doc)
    if inspect.isawaitable(res):
        await res
    return doc


async def get_session(db: Any, session_id: str) -> dict[str, Any] | None:
    """Retrieve an interview session by session_id."""
    res = db.interview_sessions.find_one({"session_id": session_id}, {"_id": 0})
    if inspect.isawaitable(res):
        return await res
    return res


async def record_item(
    db: Any,
    session_id: str,
    item: dict[str, Any],
    note: str | None = None,
) -> None:
    """Record an item in the session's items array, optionally adding a note."""
    push_payload: dict[str, Any] = {"items": item}
    if note:
        push_payload["notes"] = note
    res = db.interview_sessions.update_one(
        {"session_id": session_id},
        {"$push": push_payload},
    )
    if inspect.isawaitable(res):
        await res


async def advance(db: Any, session_id: str) -> None:
    """Advance the session pointer to the next slot."""
    res = db.interview_sessions.update_one(
        {"session_id": session_id},
        {"$inc": {"current": 1}},
    )
    if inspect.isawaitable(res):
        await res


async def finish(db: Any, session_id: str) -> None:
    """Mark the session as finished."""
    res = db.interview_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "status": "finished",
                "finished_at": datetime.now(timezone.utc),
            }
        },
    )
    if inspect.isawaitable(res):
        await res
