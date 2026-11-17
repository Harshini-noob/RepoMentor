"""Tests for Phase 6, step 3: interview sessions, runner, summary, and reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import mongomock
import pytest

from app.interview.generate import InterviewGenerationError
from app.interview.runner import run_interview
from app.interview.schema import (
    Evaluation,
    InterviewAttempt,
    InterviewQuestion,
    KeyPoint,
    Misconception,
    PointResult,
)
from app.interview.session import build_plan, create_session, get_session
from app.interview.summary import build_summary


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def _make_db():
    return mongomock.MongoClient()["repomentor"]


def _dummy_question(qid: str, round_name: str, difficulty: int, concept: str = "database-choice") -> InterviewQuestion:
    return InterviewQuestion(
        question_id=qid,
        project_id="proj_test",
        round=round_name,
        difficulty=difficulty,
        concept=concept,
        question=f"Question for {qid} ({concept})",
        key_points=[
            KeyPoint(point=f"Fact for {qid}", kind="repo", support=1),
            KeyPoint(point=f"Tradeoff for {qid}", kind="general"),
        ],
        common_mistakes=["Mistake 1"],
        seed={"claims": [{"tag": "fact", "claim": "Fact claim", "evidence": ["file.py"]}]},
    )


def _dummy_attempt(
    aid: str,
    qid: str,
    score: int,
    session_id: str | None = None,
    concept: str = "database-choice",
    missed_points: list[tuple[str, str]] | None = None,
) -> InterviewAttempt:
    pts = []
    if missed_points:
        for idx, (p_text, p_kind) in enumerate(missed_points, start=1):
            pts.append(
                PointResult(
                    id=idx,
                    point=p_text,
                    kind=p_kind,  # "repo" or "general"
                    status="missing",
                )
            )
    else:
        pts = [PointResult(id=1, point="point 1", kind="general", status="covered", quote="sample")]

    ev = Evaluation(
        points=pts,
        misconceptions=[],
        strengths=["Good point"],
        advice="Keep going",
        coverage=score / 4.0,
        score=score,
    )
    return InterviewAttempt(
        attempt_id=aid,
        question_id=qid,
        project_id="proj_test",
        concept=concept,
        difficulty=2,
        answer="Sample candidate answer",
        evaluation=ev,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# 1. Plan Building
# ---------------------------------------------------------------------------

def test_plan_building():
    # n=5 and base 2 gives rounds and difficulties as specified, clamped at 5
    plan = build_plan(n=5, base_difficulty=2)
    assert len(plan) == 5

    expected_rounds = ["history", "tech", "architecture", "history", "tech"]
    expected_diffs = [2, 2, 3, 3, 4]  # offsets: 0, 0, 1, 1, 2

    assert [p["round"] for p in plan] == expected_rounds
    assert [p["difficulty"] for p in plan] == expected_diffs

    # Base 4 clamped at 5: offsets 0, 0, 1, 1, 2, 2 -> 4, 4, 5, 5, 5, 5
    plan_clamped = build_plan(n=6, base_difficulty=4)
    assert [p["difficulty"] for p in plan_clamped] == [4, 4, 5, 5, 5, 5]


# ---------------------------------------------------------------------------
# 2. Full 3-Question Run Stores 3 Items and Finishes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_3_question_run():
    db = _make_db()

    q_count = 0
    generated_questions = []

    async def fake_gen(db, project_id, round_name, difficulty, **kwargs):
        nonlocal q_count
        q_count += 1
        q = _dummy_question(f"q_{q_count}", round_name, difficulty)
        db.interview_questions.insert_one(q.model_dump(mode="json"))
        generated_questions.append(q)
        return q

    eval_count = 0

    async def fake_eval(db, question_id, answer, **kwargs):
        nonlocal eval_count
        eval_count += 1
        att = _dummy_attempt(f"att_{eval_count}", question_id, score=3, session_id=kwargs.get("session_id"))
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    answers = ["Answer 1", "Answer 2", "Answer 3"]
    ans_idx = 0

    def fake_ask():
        nonlocal ans_idx
        a = answers[ans_idx]
        ans_idx += 1
        return a

    session = await run_interview(
        db,
        "proj_test",
        n=3,
        base_difficulty=2,
        ask_fn=fake_ask,
        generate_fn=fake_gen,
        evaluate_fn=fake_eval,
        out=lambda x: None,
    )

    assert session["status"] == "finished"
    assert len(session["items"]) == 3
    assert all(it["status"] == "answered" for it in session["items"])
    assert all(it["score"] == 3 for it in session["items"])

    saved = db.interview_sessions.find_one({"session_id": session["session_id"]})
    assert saved["status"] == "finished"
    assert len(saved["items"]) == 3


# ---------------------------------------------------------------------------
# 3. /skip Records Skipped Item and Excludes From Average
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_records_item_excludes_from_average():
    db = _make_db()

    q_counter = 0

    async def fake_gen(db, project_id, round_name, difficulty, **kwargs):
        nonlocal q_counter
        q_counter += 1
        q = _dummy_question(f"q_{q_counter}", round_name, difficulty)
        db.interview_questions.insert_one(q.model_dump(mode="json"))
        return q

    async def fake_eval(db, question_id, answer, **kwargs):
        score = 4 if question_id == "q_1" else 2
        att = _dummy_attempt(f"att_{question_id}", question_id, score=score)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    # Answers: q1 answered, q2 skipped, q3 answered
    user_inputs = ["Good answer", "/skip", "Another answer"]
    input_idx = 0

    def fake_ask():
        nonlocal input_idx
        res = user_inputs[input_idx]
        input_idx += 1
        return res

    session = await run_interview(
        db,
        "proj_test",
        n=3,
        base_difficulty=2,
        ask_fn=fake_ask,
        generate_fn=fake_gen,
        evaluate_fn=fake_eval,
        out=lambda x: None,
    )

    assert session["status"] == "finished"
    items = session["items"]
    assert len(items) == 3
    assert items[0]["status"] == "answered"
    assert items[0]["score"] == 4
    assert items[1]["status"] == "skipped"
    assert items[1]["score"] is None
    assert items[2]["status"] == "answered"
    assert items[2]["score"] == 2

    # Verify summary: average should be (4 + 2) / 2 = 3.0
    questions = list(db.interview_questions.find({}))
    attempts = list(db.interview_attempts.find({}))
    summary = build_summary(items, questions, attempts)

    assert summary["answered"] == 2
    assert summary["skipped"] == 1
    assert summary["average_score"] == 3.0


# ---------------------------------------------------------------------------
# 4. /quit Saves State and Continues From Right Slot on Resume
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quit_and_resume():
    db = _make_db()

    q_counter = 0

    async def fake_gen(db, project_id, round_name, difficulty, **kwargs):
        nonlocal q_counter
        q_counter += 1
        q = _dummy_question(f"q_{q_counter}", round_name, difficulty)
        db.interview_questions.insert_one(q.model_dump(mode="json"))
        return q

    async def fake_eval(db, question_id, answer, **kwargs):
        att = _dummy_attempt(f"att_{question_id}", question_id, score=3)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    # First run: answers Q1, then quits on Q2
    inputs_1 = ["Answer 1", "/quit"]
    idx_1 = 0

    def fake_ask_1():
        nonlocal idx_1
        res = inputs_1[idx_1]
        idx_1 += 1
        return res

    session1 = await run_interview(
        db,
        "proj_test",
        n=3,
        base_difficulty=2,
        ask_fn=fake_ask_1,
        generate_fn=fake_gen,
        evaluate_fn=fake_eval,
        out=lambda x: None,
    )

    # Session paused at current=1, status is still active
    assert session1["status"] == "active"
    assert session1["current"] == 1
    assert len(session1["items"]) == 1

    # Second run: resumes with session_id, answers remaining 2 slots
    inputs_2 = ["Answer 2", "Answer 3"]
    idx_2 = 0

    def fake_ask_2():
        nonlocal idx_2
        res = inputs_2[idx_2]
        idx_2 += 1
        return res

    session2 = await run_interview(
        db,
        "proj_test",
        session_id=session1["session_id"],
        ask_fn=fake_ask_2,
        generate_fn=fake_gen,
        evaluate_fn=fake_eval,
        out=lambda x: None,
    )

    assert session2["status"] == "finished"
    assert len(session2["items"]) == 3
    assert session2["current"] == 3


# ---------------------------------------------------------------------------
# 5. Generation Failure Twice Records no_question and Continues
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generation_failure_twice_records_no_question():
    db = _make_db()

    call_count = 0

    async def fail_then_succeed_gen(db, project_id, round_name, difficulty, **kwargs):
        nonlocal call_count
        call_count += 1
        # Slot 1: fails on initial call (1) and retry (2)
        if call_count in (1, 2):
            raise InterviewGenerationError("No seed available")
        # Slot 2: succeeds
        q = _dummy_question(f"q_{call_count}", round_name, difficulty)
        db.interview_questions.insert_one(q.model_dump(mode="json"))
        return q

    async def fake_eval(db, question_id, answer, **kwargs):
        att = _dummy_attempt(f"att_{question_id}", question_id, score=4)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    def fake_ask():
        return "Candidate answer"

    session = await run_interview(
        db,
        "proj_test",
        n=2,
        base_difficulty=2,
        ask_fn=fake_ask,
        generate_fn=fail_then_succeed_gen,
        evaluate_fn=fake_eval,
        out=lambda x: None,
    )

    assert session["status"] == "finished"
    items = session["items"]
    assert len(items) == 2
    assert items[0]["status"] == "no_question"
    assert items[0]["score"] is None
    assert items[1]["status"] == "answered"
    assert items[1]["score"] == 4


# ---------------------------------------------------------------------------
# 6. Summary Averages, Weakest Concepts, and Revise List
# ---------------------------------------------------------------------------

def test_summary_averages_weakest_concepts_and_revise():
    q1 = _dummy_question("q1", "tech", 2, concept="caching")
    q2 = _dummy_question("q2", "architecture", 3, concept="caching")
    q3 = _dummy_question("q3", "history", 2, concept="auth")
    q4 = _dummy_question("q4", "tech", 3, concept="database-choice")

    questions = [q1, q2, q3, q4]

    # Attempts with missed points:
    # att1 (caching, score 1): missed general pt "TTL strategies", repo pt "Redis cache layer"
    # att2 (caching, score 2): missed general pt "Cache invalidation", general pt "TTL strategies" (duplicate)
    # att3 (auth, score 3): missed repo pt "JWT secret in env"
    # att4 (database-choice, score 4): perfect
    att1 = _dummy_attempt("att1", "q1", score=1, concept="caching", missed_points=[
        ("TTL strategies", "general"),
        ("Redis cache layer", "repo"),
    ])
    att2 = _dummy_attempt("att2", "q2", score=2, concept="caching", missed_points=[
        ("Cache invalidation", "general"),
        ("TTL strategies", "general"),  # duplicate should be discarded
    ])
    att3 = _dummy_attempt("att3", "q3", score=3, concept="auth", missed_points=[
        ("JWT secret in env", "repo"),
    ])
    att4 = _dummy_attempt("att4", "q4", score=4, concept="database-choice", missed_points=[])

    attempts = [att1, att2, att3, att4]

    items = [
        {"question_id": "q1", "attempt_id": "att1", "status": "answered", "score": 1},
        {"question_id": "q2", "attempt_id": "att2", "status": "answered", "score": 2},
        {"question_id": "q3", "attempt_id": "att3", "status": "answered", "score": 3},
        {"question_id": "q4", "attempt_id": "att4", "status": "answered", "score": 4},
        {"question_id": None, "attempt_id": None, "status": "skipped", "score": None},
    ]

    summary = build_summary(items, questions, attempts)

    assert summary["answered"] == 4
    assert summary["skipped"] == 1
    # Average: (1 + 2 + 3 + 4) / 4 = 2.5
    assert summary["average_score"] == 2.5

    # Concept averages:
    # caching: (1+2)/2 = 1.5
    # auth: 3.0
    # database-choice: 4.0
    assert summary["per_concept"]["caching"]["average"] == 1.5
    assert summary["per_concept"]["auth"]["average"] == 3.0
    assert summary["per_concept"]["database-choice"]["average"] == 4.0

    # Weakest concepts (lowest average, at most 3):
    # caching (1.5) < auth (3.0) < database-choice (4.0)
    assert summary["weakest_concepts"] == ["caching", "auth", "database-choice"]

    # Revise list:
    # repo points first: "Redis cache layer" (repo), "JWT secret in env" (repo)
    # general points: "TTL strategies" (general), "Cache invalidation" (general)
    # duplicate "TTL strategies" must not appear twice
    revise = summary["revise"]
    assert len(revise) == 4

    kinds = [r["kind"] for r in revise]
    assert kinds == ["repo", "repo", "general", "general"]

    points = [r["point"] for r in revise]
    assert "Redis cache layer" in points[:2]
    assert "JWT secret in env" in points[:2]
    assert points.count("TTL strategies") == 1
    assert "Cache invalidation" in points[2:]


# ---------------------------------------------------------------------------
# 7. Wrong Project ID on --session Errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wrong_project_id_errors():
    db = _make_db()
    session = await create_session(db, project_id="proj_alpha", n=3, base_difficulty=2)

    with pytest.raises(ValueError, match="belongs to project 'proj_alpha', not 'proj_beta'"):
        await run_interview(db, project_id="proj_beta", session_id=session["session_id"])


# ---------------------------------------------------------------------------
# 8. Report Makes No Model Call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_makes_no_model_call():
    db = _make_db()
    session = await create_session(db, project_id="proj_report", n=2, base_difficulty=2)

    q = _dummy_question("q_rep", "tech", 2)
    db.interview_questions.insert_one(q.model_dump(mode="json"))

    att = _dummy_attempt("att_rep", "q_rep", score=3, session_id=session["session_id"])
    db.interview_attempts.insert_one(att.model_dump(mode="json"))

    db.interview_sessions.update_one(
        {"session_id": session["session_id"]},
        {
            "$set": {"status": "finished"},
            "$push": {
                "items": {
                    "question_id": "q_rep",
                    "attempt_id": "att_rep",
                    "status": "answered",
                    "score": 3,
                }
            },
        },
    )

    # Calling summary/reporting should never touch any LLM chat
    with patch("app.agent.llm.chat", new_callable=AsyncMock) as mock_chat:
        fetched_session = await get_session(db, session["session_id"])
        items = fetched_session["items"]
        questions = list(db.interview_questions.find({"question_id": "q_rep"}))
        attempts = list(db.interview_attempts.find({"attempt_id": "att_rep"}))

        summary = build_summary(items, questions, attempts)
        assert summary["average_score"] == 3.0
        assert mock_chat.call_count == 0
