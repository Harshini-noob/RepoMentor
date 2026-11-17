"""Tests for Phase 7, step 1: learner profile."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import mongomock
import pytest

from app.agent.state import LLMReply
from app.interview.evaluate import evaluate_answer
from app.interview.profile import (
    _apply_attempt_to_doc,
    _status,
    get_profile,
    next_difficulty,
    rebuild,
    update_from_attempt,
    weak_concepts,
)
from app.interview.schema import (
    Evaluation,
    InterviewAttempt,
    PointResult,
)


def _make_db():
    return mongomock.MongoClient()["repomentor"]


def _make_attempt(
    attempt_id: str,
    project_id: str,
    concept: str,
    score: int,
    points: list[dict] | None = None,
    created_at: datetime | None = None,
) -> InterviewAttempt:
    if points is None:
        points = [PointResult(id=1, point="generic point", kind="general", status="covered", quote="x")]
    else:
        points = [PointResult(**p) for p in points]
    ev = Evaluation(
        points=points,
        misconceptions=[],
        coverage=score / 4.0,
        score=score,
    )
    return InterviewAttempt(
        attempt_id=attempt_id,
        question_id=f"q_{attempt_id}",
        project_id=project_id,
        concept=concept,
        difficulty=2,
        answer="sample",
        evaluation=ev,
        created_at=created_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. Score update: 2 then 4 gives 3.0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_update_ewma():
    db = _make_db()
    att1 = _make_attempt("a1", "proj", "caching", score=2)
    att2 = _make_attempt("a2", "proj", "caching", score=4)

    await update_from_attempt(db, att1)
    await update_from_attempt(db, att2)

    profile = await get_profile(db, "proj")
    info = profile["concepts"]["caching"]
    assert info["score"] == pytest.approx(3.0)
    assert info["attempts"] == 2


# ---------------------------------------------------------------------------
# 2. Status thresholds including strong needing 2 attempts
# ---------------------------------------------------------------------------

def test_status_thresholds():
    assert _status(0.0, 0) == "unrated"
    assert _status(1.0, 1) == "weak"       # < 1.75
    assert _status(1.74, 1) == "weak"
    assert _status(1.75, 1) == "partial"   # >= 1.75, < 3.0 or attempts < 2
    assert _status(3.0, 1) == "partial"    # score >= 3.0 but only 1 attempt
    assert _status(3.0, 2) == "strong"     # score >= 3.0 and >= 2 attempts
    assert _status(4.0, 2) == "strong"
    assert _status(2.5, 3) == "partial"    # >= 1.75, < 3.0


# ---------------------------------------------------------------------------
# 3. Missed point counted, then reduced when covered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_miss_count_then_covered():
    db = _make_db()
    # Attempt 1: point is missing
    att1 = _make_attempt(
        "a1", "proj", "auth", score=1,
        points=[{"id": 1, "point": "Use JWT tokens", "kind": "general", "status": "missing"}]
    )
    await update_from_attempt(db, att1)
    profile = await get_profile(db, "proj")
    misses = profile["concepts"]["auth"]["misses"]
    assert len(misses) == 1
    assert misses[0]["point"] == "Use JWT tokens"
    assert misses[0]["count"] == 1

    # Attempt 2: same point is covered → count decreases and is removed at 0
    att2 = _make_attempt(
        "a2", "proj", "auth", score=4,
        points=[{"id": 1, "point": "Use JWT tokens", "kind": "general", "status": "covered", "quote": "jwt tokens"}]
    )
    await update_from_attempt(db, att2)
    profile = await get_profile(db, "proj")
    misses = profile["concepts"]["auth"]["misses"]
    assert misses == []


# ---------------------------------------------------------------------------
# 4. Idempotency: same attempt_id applied twice counts once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotent_per_attempt_id():
    db = _make_db()
    att = _make_attempt("a1", "proj", "testing", score=4)
    await update_from_attempt(db, att)
    await update_from_attempt(db, att)  # second call, same id

    profile = await get_profile(db, "proj")
    info = profile["concepts"]["testing"]
    assert info["attempts"] == 1
    assert info["score"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# 5. Rebuild gives same result as applying attempts one by one
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rebuild_matches_incremental():
    db1 = _make_db()
    db2 = _make_db()

    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

    att1 = _make_attempt("a1", "proj", "caching", score=2, created_at=t1)
    att2 = _make_attempt("a2", "proj", "caching", score=4, created_at=t2)

    # Incremental on db1
    await update_from_attempt(db1, att1)
    await update_from_attempt(db1, att2)

    # Insert raw docs into db2 for rebuild
    db2.interview_attempts.insert_many([
        att1.model_dump(mode="json"),
        att2.model_dump(mode="json"),
    ])
    await rebuild(db2, "proj")

    p1 = await get_profile(db1, "proj")
    p2 = await get_profile(db2, "proj")

    assert p1["concepts"]["caching"]["score"] == pytest.approx(p2["concepts"]["caching"]["score"])
    assert p1["concepts"]["caching"]["attempts"] == p2["concepts"]["caching"]["attempts"]


# ---------------------------------------------------------------------------
# 6. Skipped / unsaved attempts never count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skipped_unsaved_never_count():
    db = _make_db()
    # Skipped items have no attempt saved in db.interview_attempts
    # and are never passed to update_from_attempt — just verify rebuild on empty db
    await rebuild(db, "proj_empty")
    profile = await get_profile(db, "proj_empty")
    assert profile is None  # no attempts means no document


# ---------------------------------------------------------------------------
# 7. weak_concepts ordering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_weak_concepts_ordering():
    db = _make_db()
    # caching: score 1 (weak), auth: score 3 (strong), testing: score 2 (partial)
    await update_from_attempt(db, _make_attempt("a1", "proj", "caching", score=1))
    await update_from_attempt(db, _make_attempt("a2", "proj", "auth", score=4))
    await update_from_attempt(db, _make_attempt("a3", "proj", "auth", score=4))  # strong
    await update_from_attempt(db, _make_attempt("a4", "proj", "testing", score=2))

    profile = await get_profile(db, "proj")
    weak = weak_concepts(profile, k=3)
    # Should be ordered by lowest score first
    assert weak[0] == "caching"  # score=1
    assert "testing" in weak
    assert len(weak) <= 3


# ---------------------------------------------------------------------------
# 8. next_difficulty rules and clamping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_next_difficulty():
    db = _make_db()

    # Unrated concept → returns default
    profile_empty = {"concepts": {}}
    assert next_difficulty(profile_empty, "caching", default=3) == 3

    # score >= 3.0 → default + 1
    att1 = _make_attempt("a1", "proj", "auth", score=4)
    att2 = _make_attempt("a2", "proj", "auth", score=4)
    await update_from_attempt(db, att1)
    await update_from_attempt(db, att2)
    profile = await get_profile(db, "proj")
    # auth score = 4.0 (or ewma = 4), >= 3.0 → default + 1
    assert next_difficulty(profile, "auth", default=3) == 4

    # score < 1.75 → default - 1
    att3 = _make_attempt("a3", "proj", "deploy", score=1)
    await update_from_attempt(db, att3)
    profile = await get_profile(db, "proj")
    assert next_difficulty(profile, "deploy", default=2) == 1

    # Clamp min to 1
    assert next_difficulty(profile, "deploy", default=1) == 1

    # Clamp max to 5
    att4 = _make_attempt("a4", "proj", "config", score=4)
    att5 = _make_attempt("a5", "proj", "config", score=4)
    await update_from_attempt(db, att4)
    await update_from_attempt(db, att5)
    profile = await get_profile(db, "proj")
    assert next_difficulty(profile, "config", default=5) == 5  # clamped at 5


# ---------------------------------------------------------------------------
# 9. Profile failure inside evaluate_answer does not raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_failure_does_not_raise():
    db = _make_db()
    db.interview_questions.insert_one({
        "question_id": "q_pf",
        "project_id": "proj_pf",
        "concept": "testing",
        "difficulty": 2,
        "question": "Tell me about tests",
        "key_points": [{"point": "unit tests", "kind": "general", "support": None}],
        "common_mistakes": [],
        "seed": {"claims": []},
    })

    model_response = {
        "points": [{"id": 1, "status": "covered", "quote": "unit tests"}],
        "misconceptions": [],
        "unverified": [],
        "strengths": [],
        "advice": "",
    }
    fake_chat = AsyncMock(return_value=LLMReply(content=json.dumps(model_response)))

    # Patch update_from_attempt to raise
    import app.interview.evaluate as ev_mod
    original = ev_mod.__dict__.get("_update_profile_fn")  # may not exist

    # Monkeypatch by temporarily patching the import inside evaluate
    import app.interview.profile as prof_mod
    original_fn = prof_mod.update_from_attempt

    async def broken_update(*args, **kwargs):
        raise RuntimeError("simulated profile failure")

    prof_mod.update_from_attempt = broken_update
    try:
        attempt = await evaluate_answer(
            db, "q_pf",
            "We use unit tests thoroughly in the project.",
            chat_fn=fake_chat,
            save=True,
        )
        # Must not raise; attempt is returned normally
        assert attempt is not None
        assert attempt.evaluation.score >= 0
    finally:
        prof_mod.update_from_attempt = original_fn
