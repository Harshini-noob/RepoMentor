"""Interview session persistence and lifecycle management."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from typing import Any

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
        plan.append({"round": r, "difficulty": diff})
    return plan


async def create_session(
    db: Any,
    project_id: str,
    n: int = 5,
    base_difficulty: int = 2,
) -> dict[str, Any]:
    """Create a new interview session."""
    session_id = str(uuid.uuid4())
    doc: dict[str, Any] = {
        "session_id": session_id,
        "project_id": project_id,
        "status": "active",
        "plan": build_plan(n, base_difficulty),
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
