"""Learner profile: per-concept performance tracking.

No LLM calls. All updates are pure arithmetic and idempotent per attempt_id.
Collection: learner_profile, one document per project_id.
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("repomentor.interview.profile")

_MAX_APPLIED = 200
_MAX_MISSES = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(score: float, attempts: int) -> str:
    if attempts == 0:
        return "unrated"
    if score >= 3.0 and attempts >= 2:
        return "strong"
    if score < 1.75:
        return "weak"
    return "partial"


def _apply_attempt_to_doc(doc: dict, attempt: Any) -> dict:
    """Apply one attempt to a profile document (mutates doc in-place, returns it)."""
    # Normalise attempt fields
    attempt_id = _gv(attempt, "attempt_id")
    concept = _gv(attempt, "concept")
    if not concept or not attempt_id:
        return doc

    # Idempotency: skip if already applied
    applied = doc.setdefault("applied_attempts", [])
    if attempt_id in applied:
        return doc

    # Role-aware logic
    role = _gv(attempt, "role") or "initial"

    if role == "followup":
        # Followup attempts: do nothing, just mark as applied
        applied.append(attempt_id)
        if len(applied) > _MAX_APPLIED:
            applied[:] = applied[-_MAX_APPLIED:]
        return doc

    if role == "retry":
        # Retry attempts: only decrement misses of points that were covered
        ev = _gv(attempt, "evaluation")
        points = _gv(ev, "points") or []
        concepts: dict = doc.setdefault("concepts", {})
        c = concepts.get(concept)
        if c is not None:
            misses: list = c.setdefault("misses", [])
            for p in points:
                p_text = _gv(p, "point") or ""
                p_status = _gv(p, "status")
                if p_status == "covered" and p_text:
                    for m in misses:
                        if m.get("point") == p_text:
                            m["count"] = m.get("count", 1) - 1
                            break
                    c["misses"] = [m for m in misses if m.get("count", 1) > 0]
        applied.append(attempt_id)
        if len(applied) > _MAX_APPLIED:
            applied[:] = applied[-_MAX_APPLIED:]
        return doc

    # "initial" (or missing role): apply score/attempts/misses as before
    ev = _gv(attempt, "evaluation")
    score_raw = _gv(ev, "score")
    if score_raw is None:
        return doc
    new_score = int(score_raw)
    created_at = _gv(attempt, "created_at")
    if isinstance(created_at, datetime):
        ts_iso = created_at.isoformat()
    else:
        ts_iso = str(created_at) if created_at else _now_iso()

    concepts: dict = doc.setdefault("concepts", {})
    c = concepts.setdefault(concept, {
        "score": 0.0,
        "attempts": 0,
        "last_score": 0,
        "last_seen": ts_iso,
        "recent_scores": [],
        "status": "unrated",
        "misses": [],
    })

    # Score update
    if c["attempts"] == 0:
        c["score"] = float(new_score)
    else:
        c["score"] = 0.5 * c["score"] + 0.5 * float(new_score)
    c["attempts"] += 1
    c["last_score"] = new_score
    c["last_seen"] = ts_iso

    # Recent scores (last 5)
    recent = c.setdefault("recent_scores", [])
    recent.append(new_score)
    if len(recent) > 5:
        recent[:] = recent[-5:]

    # Status
    c["status"] = _status(c["score"], c["attempts"])

    # Misses: collect missing/partial rubric points
    points = _gv(ev, "points") or []
    for p in points:
        p_text = _gv(p, "point") or ""
        p_kind = _gv(p, "kind") or "general"
        p_status = _gv(p, "status")
        if not p_text:
            continue

        misses: list = c.setdefault("misses", [])

        if p_status in ("missing", "partial"):
            # Upsert: find existing entry by text
            existing = next((m for m in misses if m.get("point") == p_text), None)
            if existing:
                existing["count"] = existing.get("count", 0) + 1
                existing["last_seen"] = ts_iso
            else:
                misses.append({
                    "point": p_text,
                    "kind": p_kind,
                    "count": 1,
                    "last_seen": ts_iso,
                })
            # Sort by last_seen descending, keep at most 5
            misses.sort(key=lambda m: m.get("last_seen", ""), reverse=True)
            c["misses"] = misses[:_MAX_MISSES]

        elif p_status == "covered":
            # Reduce count of matching miss, remove at 0
            for m in misses:
                if m.get("point") == p_text:
                    m["count"] = m.get("count", 1) - 1
                    break
            c["misses"] = [m for m in misses if m.get("count", 1) > 0]

    # Record attempt as applied
    applied.append(attempt_id)
    if len(applied) > _MAX_APPLIED:
        applied[:] = applied[-_MAX_APPLIED:]

    doc["updated_at"] = ts_iso
    return doc


def _gv(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def _get_doc(db: Any, project_id: str) -> dict | None:
    res = db.learner_profile.find_one({"project_id": project_id}, {"_id": 0})
    if inspect.isawaitable(res):
        res = await res
    return res


async def _upsert_doc(db: Any, project_id: str, doc: dict) -> None:
    # Remove _id if present (mongomock quirk)
    doc_to_save = {k: v for k, v in doc.items() if k != "_id"}
    res = db.learner_profile.replace_one(
        {"project_id": project_id},
        doc_to_save,
        upsert=True,
    )
    if inspect.isawaitable(res):
        await res


async def record_teaching(db: Any, project_id: str, concept: str, before: int, after: int) -> None:
    """Record a teaching lesson delta for the given concept."""
    doc = await _get_doc(db, project_id)
    if doc is None:
        doc = {
            "project_id": project_id,
            "updated_at": _now_iso(),
            "concepts": {},
            "applied_attempts": [],
        }

    concepts: dict = doc.setdefault("concepts", {})
    c = concepts.setdefault(concept, {
        "score": 0.0,
        "attempts": 0,
        "last_score": 0,
        "last_seen": _now_iso(),
        "recent_scores": [],
        "status": "unrated",
        "misses": [],
    })
    c["lessons"] = c.get("lessons", 0) + 1
    recent_deltas = c.setdefault("recent_deltas", [])
    recent_deltas.append(after - before)
    if len(recent_deltas) > 5:
        c["recent_deltas"] = recent_deltas[-5:]
    doc["updated_at"] = _now_iso()
    await _upsert_doc(db, project_id, doc)


async def update_from_attempt(db: Any, attempt: Any) -> None:
    """Update the learner profile from a saved attempt. Idempotent per attempt_id."""
    project_id = _gv(attempt, "project_id")
    if not project_id:
        return

    doc = await _get_doc(db, project_id)
    if doc is None:
        doc = {
            "project_id": project_id,
            "updated_at": _now_iso(),
            "concepts": {},
            "applied_attempts": [],
        }

    _apply_attempt_to_doc(doc, attempt)
    await _upsert_doc(db, project_id, doc)


async def rebuild(db: Any, project_id: str) -> None:
    """Reset and rebuild the profile from all saved attempts in created_at order."""
    # Clear existing doc
    res = db.learner_profile.delete_one({"project_id": project_id})
    if inspect.isawaitable(res):
        await res

    # Fetch all attempts ordered by created_at
    cur = db.interview_attempts.find({"project_id": project_id}).sort("created_at", 1)
    if hasattr(cur, "to_list"):
        attempts = await cur.to_list(None)
    elif inspect.isawaitable(cur):
        attempts = list(await cur)
    else:
        attempts = list(cur)

    # Fetch all lessons ordered by created_at
    l_cur = db.interview_lessons.find({"project_id": project_id}).sort("created_at", 1)
    if hasattr(l_cur, "to_list"):
        lessons = await l_cur.to_list(None)
    elif inspect.isawaitable(l_cur):
        lessons = list(await l_cur)
    else:
        lessons = list(l_cur)

    if not attempts and not lessons:
        return

    doc: dict = {
        "project_id": project_id,
        "updated_at": _now_iso(),
        "concepts": {},
        "applied_attempts": [],
    }
    for att in attempts:
        _apply_attempt_to_doc(doc, att)

    # Replay lessons from interview_lessons:
    # (the before/after from the attempt chain: retry attempt score minus its parent attempt's score)
    attempts_by_id = {str(_gv(a, "attempt_id")): a for a in attempts}
    for lesson in lessons:
        lesson_attempt_id = str(_gv(lesson, "attempt_id"))
        parent_att = attempts_by_id.get(lesson_attempt_id)
        retry_att = next(
            (
                a for a in attempts
                if str(_gv(a, "parent_attempt_id")) == lesson_attempt_id
                and _gv(a, "role") == "retry"
            ),
            None,
        )
        if retry_att is not None and parent_att is not None:
            before = _gv(_gv(parent_att, "evaluation"), "score")
            after = _gv(_gv(retry_att, "evaluation"), "score")
            concept = _gv(lesson, "concept") or _gv(parent_att, "concept")
            if before is not None and after is not None and concept:
                c = doc["concepts"].setdefault(concept, {
                    "score": 0.0,
                    "attempts": 0,
                    "last_score": 0,
                    "last_seen": _now_iso(),
                    "recent_scores": [],
                    "status": "unrated",
                    "misses": [],
                })
                c["lessons"] = c.get("lessons", 0) + 1
                recent_deltas = c.setdefault("recent_deltas", [])
                recent_deltas.append(int(after) - int(before))
                if len(recent_deltas) > 5:
                    c["recent_deltas"] = recent_deltas[-5:]

    await _upsert_doc(db, project_id, doc)


async def get_profile(db: Any, project_id: str) -> dict | None:
    """Return the learner profile document for the given project_id, or None."""
    return await _get_doc(db, project_id)


def weak_concepts(profile: dict | None, k: int = 3) -> list[str]:
    """Return up to k concepts with attempts >= 1, sorted by lowest score first."""
    if not profile:
        return []
    concepts = profile.get("concepts") or {}
    eligible = [
        (name, info)
        for name, info in concepts.items()
        if isinstance(info, dict) and info.get("attempts", 0) >= 1
    ]
    eligible.sort(key=lambda x: (x[1].get("score", 0.0), x[0]))
    return [name for name, _ in eligible[:k]]


def next_difficulty(profile: dict | None, concept: str, default: int) -> int:
    """Return recommended next difficulty for a concept based on profile score.

    - unrated (no attempts) → default
    - score >= 3.0 → default + 1 (harder)
    - score < 1.75 → default - 1 (easier)
    - otherwise → default
    Clamped to 1..5.
    """
    if not profile:
        return max(1, min(5, default))
    concepts = profile.get("concepts") or {}
    info = concepts.get(concept)
    if not info or info.get("attempts", 0) == 0:
        return max(1, min(5, default))

    score = info.get("score", 0.0)
    if score >= 3.0:
        return max(1, min(5, default + 1))
    if score < 1.75:
        return max(1, min(5, default - 1))
    return max(1, min(5, default))
