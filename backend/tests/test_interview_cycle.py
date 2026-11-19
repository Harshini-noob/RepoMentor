"""Tests for Phase 7, step 2b: teaching cycle inside the interview."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mongomock
import pytest

from app.interview.generate import InterviewGenerationError, generate_question
from app.interview.lesson import generate_lesson
from app.interview.profile import get_profile, rebuild, update_from_attempt
from app.interview.runner import print_interview_summary, run_interview
from app.interview.schema import (
    Evaluation,
    InterviewAttempt,
    InterviewQuestion,
    KeyPoint,
    PointResult,
)
from app.interview.session import get_session
from app.interview.summary import build_summary

SEED_ID = "file:src/main.py#L1-L2"


def _make_db():
    return mongomock.MongoClient()["repomentor"]


def _make_question(
    qid: str,
    round_name: str = "tech",
    difficulty: int = 2,
    concept: str = "database-choice",
    seed: dict | None = None,
) -> InterviewQuestion:
    return InterviewQuestion(
        question_id=qid,
        project_id="proj",
        round=round_name,
        difficulty=difficulty,
        concept=concept,
        question=f"Question text for {qid} ({concept})",
        key_points=[
            KeyPoint(point="Explain the trade-offs", kind="general"),
            KeyPoint(point="Uses PostgreSQL database", kind="repo", support=1),
        ],
        common_mistakes=["Mistake 1"],
        seed=seed
        or {
            "claims": [
                {
                    "tag": "fact",
                    "claim": "Uses PostgreSQL database storage.",
                    "evidence": [SEED_ID],
                }
            ]
        },
    )


def _make_attempt(
    aid: str,
    qid: str,
    score: int,
    concept: str = "database-choice",
    role: str = "initial",
    parent_attempt_id: str | None = None,
    missed: list[str] | None = None,
) -> InterviewAttempt:
    if missed is None:
        if score == 4:
            pts = [
                PointResult(id=1, point="Explain the trade-offs", kind="general", status="covered", quote="trades"),
                PointResult(id=2, point="Uses PostgreSQL database", kind="repo", status="covered", quote="postgres"),
            ]
        else:
            pts = [
                PointResult(id=1, point="Explain the trade-offs", kind="general", status="missing"),
                PointResult(id=2, point="Uses PostgreSQL database", kind="repo", status="covered", quote="postgres"),
            ]
    else:
        pts = []
        for idx, pt in enumerate(missed, start=1):
            pts.append(PointResult(id=idx, point=pt, kind="general", status="missing"))
        pts.append(PointResult(id=len(missed) + 1, point="Uses PostgreSQL database", kind="repo", status="covered", quote="postgres"))

    ev = Evaluation(
        points=pts,
        misconceptions=[],
        strengths=["Good"],
        advice="Improve",
        coverage=score / 4.0,
        score=score,
    )
    return InterviewAttempt(
        attempt_id=aid,
        question_id=qid,
        project_id="proj",
        concept=concept,
        difficulty=2,
        answer="Candidate answer",
        evaluation=ev,
        role=role,
        parent_attempt_id=parent_attempt_id,
    )


# ---------------------------------------------------------------------------
# 1. Score of 4 triggers no teaching cycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_4_triggers_no_cycle():
    db = _make_db()
    q = _make_question("q1")
    db.interview_questions.insert_one(q.model_dump(mode="json"))

    async def fake_gen(db, project_id, round_name, difficulty, **kwargs):
        return q

    async def fake_eval(db, question_id, answer, **kwargs):
        att = _make_attempt("a1", question_id, score=4)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    fake_lesson = AsyncMock()

    session = await run_interview(
        db,
        "proj",
        n=1,
        base_difficulty=2,
        ask_fn=lambda: "Answer",
        generate_fn=fake_gen,
        evaluate_fn=fake_eval,
        lesson_fn=fake_lesson,
        teach=True,
        out=lambda x: None,
    )

    fake_lesson.assert_not_called()
    assert len(session["items"]) == 1
    assert session["items"][0]["score"] == 4
    assert "lesson_id" not in session["items"][0]


# ---------------------------------------------------------------------------
# 2. Score of 2 triggers lesson, follow-up, and retry in that order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_2_triggers_full_cycle():
    db = _make_db()
    q_init = _make_question("q1", difficulty=2, concept="database-choice")
    db.interview_questions.insert_one(q_init.model_dump(mode="json"))

    call_order: list[str] = []
    eval_roles: list[tuple[str, str | None]] = []

    async def fake_gen(db, project_id, round_name, difficulty, **kwargs):
        call_order.append("initial_gen")
        return q_init

    async def fake_lesson(attempt):
        call_order.append("lesson")
        return {
            "lesson_id": "les_1",
            "attempt_id": getattr(attempt, "attempt_id", "a1"),
            "text": "Good: ...\nIdea: ...\nIn your project: ...\nSay it like this: ...",
            "notes": [],
        }

    q_fu = _make_question("q_fu", difficulty=1, concept="database-choice")

    async def fake_gen_fu(db, project_id, round_name, difficulty, **kwargs):
        call_order.append("followup_gen")
        assert kwargs.get("avoid_question") == q_init.question
        assert kwargs.get("parent_question_id") == q_init.question_id
        assert kwargs.get("concept") == "database-choice"
        assert kwargs.get("seed") == q_init.seed
        assert difficulty == 1  # max(1, 2-1)
        db.interview_questions.insert_one(q_fu.model_dump(mode="json"))
        return q_fu

    eval_count = 0

    async def fake_eval(db, question_id, answer, **kwargs):
        nonlocal eval_count
        eval_count += 1
        role = kwargs.get("role", "initial")
        parent_id = kwargs.get("parent_attempt_id")
        eval_roles.append((role, parent_id))
        score = 2 if role == "initial" else (3 if role == "followup" else 4)
        aid = f"att_{eval_count}"
        att = _make_attempt(aid, question_id, score=score, role=role, parent_attempt_id=parent_id)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    answers = ["Initial answer", "Followup answer", "Retry answer"]
    ans_idx = 0

    def fake_ask():
        nonlocal ans_idx
        a = answers[ans_idx]
        ans_idx += 1
        return a

    session = await run_interview(
        db,
        "proj",
        n=1,
        base_difficulty=2,
        ask_fn=fake_ask,
        generate_fn=fake_gen,
        generate_followup_fn=fake_gen_fu,
        evaluate_fn=fake_eval,
        lesson_fn=fake_lesson,
        teach=True,
        out=lambda x: None,
    )

    assert call_order == ["initial_gen", "lesson", "followup_gen"]
    assert eval_roles == [
        ("initial", None),
        ("followup", "att_1"),
        ("retry", "att_1"),
    ]
    item = session["items"][0]
    assert item["status"] == "answered"
    assert item["lesson_id"] == "les_1"
    assert item["followup_question_id"] == "q_fu"
    assert item["followup_attempt_id"] == "att_2"
    assert item["retry_attempt_id"] == "att_3"
    assert item["before"] == 2
    assert item["after"] == 4


# ---------------------------------------------------------------------------
# 3. Follow-up uses original seed, difficulty d-1 (min 1) and same concept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_followup_uses_original_seed_and_concept_and_clamped_difficulty():
    db = _make_db()
    # Initial question with difficulty 1 -> follow-up difficulty must be max(1, 1-1) = 1
    q_init = _make_question("q1", difficulty=1, concept="observability")
    db.interview_questions.insert_one(q_init.model_dump(mode="json"))

    captured_kwargs: dict = {}

    async def fake_gen_fu(db, project_id, round_name, difficulty, **kwargs):
        captured_kwargs["difficulty"] = difficulty
        captured_kwargs["seed"] = kwargs.get("seed")
        captured_kwargs["concept"] = kwargs.get("concept")
        captured_kwargs["focus_points"] = kwargs.get("focus_points")
        q_fu = _make_question("q_fu", difficulty=difficulty, concept="observability")
        db.interview_questions.insert_one(q_fu.model_dump(mode="json"))
        return q_fu

    async def fake_eval(db, question_id, answer, **kwargs):
        att = _make_attempt("a", question_id, score=2, concept="observability")
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    answers = ["Initial", "Followup", "Retry"]
    ans_idx = 0

    def fake_ask():
        nonlocal ans_idx
        a = answers[ans_idx]
        ans_idx += 1
        return a

    await run_interview(
        db,
        "proj",
        n=1,
        base_difficulty=1,
        ask_fn=fake_ask,
        generate_fn=lambda *a, **k: q_init,
        generate_followup_fn=fake_gen_fu,
        evaluate_fn=fake_eval,
        lesson_fn=lambda a: {"lesson_id": "l1", "text": "Lesson", "notes": []},
        teach=True,
        out=lambda x: None,
    )

    assert captured_kwargs["difficulty"] == 1  # clamped at 1
    assert captured_kwargs["concept"] == "observability"
    assert captured_kwargs["seed"] == q_init.seed
    assert captured_kwargs["focus_points"] == ["Explain the trade-offs"]


# ---------------------------------------------------------------------------
# 4. Third weak answer in session gets no lesson with max_lessons=2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_lessons_2_caps_at_two_lessons():
    db = _make_db()
    lessons_called = 0

    async def fake_lesson(attempt):
        nonlocal lessons_called
        lessons_called += 1
        return {"lesson_id": f"les_{lessons_called}", "text": "Lesson", "notes": []}

    q_counter = 0

    async def fake_gen(db, project_id, round_name, difficulty, **kwargs):
        nonlocal q_counter
        q_counter += 1
        q = _make_question(f"q_{q_counter}", difficulty=difficulty)
        db.interview_questions.insert_one(q.model_dump(mode="json"))
        return q

    async def fake_eval(db, question_id, answer, **kwargs):
        att = _make_attempt(f"att_{question_id}", question_id, score=2)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    # For 3 slots:
    # Slot 1 (weak): initial, fu, retry (3 answers)
    # Slot 2 (weak): initial, fu, retry (3 answers)
    # Slot 3 (weak): initial only (1 answer, since lessons_given reaches max_lessons=2)
    answers = ["ans"] * 7
    ans_idx = 0

    def fake_ask():
        nonlocal ans_idx
        a = answers[ans_idx]
        ans_idx += 1
        return a

    session = await run_interview(
        db,
        "proj",
        n=3,
        base_difficulty=2,
        ask_fn=fake_ask,
        generate_fn=fake_gen,
        evaluate_fn=fake_eval,
        lesson_fn=fake_lesson,
        teach=True,
        max_lessons=2,
        out=lambda x: None,
    )

    assert lessons_called == 2
    assert "lesson_id" in session["items"][0]
    assert "lesson_id" in session["items"][1]
    assert "lesson_id" not in session["items"][2]


# ---------------------------------------------------------------------------
# 5. --no-teach skips cycles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_teach_skips_cycles():
    db = _make_db()
    fake_lesson = AsyncMock()
    q = _make_question("q1")
    db.interview_questions.insert_one(q.model_dump(mode="json"))

    async def fake_eval(db, question_id, answer, **kwargs):
        att = _make_attempt("a1", question_id, score=1)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    session = await run_interview(
        db,
        "proj",
        n=1,
        base_difficulty=2,
        ask_fn=lambda: "Ans",
        generate_fn=lambda *a, **k: q,
        evaluate_fn=fake_eval,
        lesson_fn=fake_lesson,
        teach=False,
        out=lambda x: None,
    )

    fake_lesson.assert_not_called()
    assert len(session["items"]) == 1
    assert "lesson_id" not in session["items"][0]


# ---------------------------------------------------------------------------
# 6. Follow-up generation failure still runs the retry and records cycle_note
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_followup_generation_failure_still_runs_retry():
    db = _make_db()
    q = _make_question("q1")
    db.interview_questions.insert_one(q.model_dump(mode="json"))

    async def fake_gen_fu(*args, **kwargs):
        raise InterviewGenerationError("Could not generate follow-up")

    eval_calls = []

    async def fake_eval(db, question_id, answer, **kwargs):
        role = kwargs.get("role", "initial")
        eval_calls.append(role)
        score = 2 if role == "initial" else 3
        att = _make_attempt(f"att_{role}", question_id, score=score, role=role)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    answers = ["Initial ans", "Retry ans"]
    ans_idx = 0

    def fake_ask():
        nonlocal ans_idx
        a = answers[ans_idx]
        ans_idx += 1
        return a

    session = await run_interview(
        db,
        "proj",
        n=1,
        base_difficulty=2,
        ask_fn=fake_ask,
        generate_fn=lambda *a, **k: q,
        generate_followup_fn=fake_gen_fu,
        evaluate_fn=fake_eval,
        lesson_fn=lambda a: {"lesson_id": "les_1", "text": "Lesson", "notes": []},
        teach=True,
        out=lambda x: None,
    )

    # Initial and retry evaluated (follow-up skipped)
    assert eval_calls == ["initial", "retry"]
    item = session["items"][0]
    assert item["cycle_note"] == "no follow-up question"
    assert item["retry_attempt_id"] == "att_retry"
    assert item["before"] == 2
    assert item["after"] == 3


# ---------------------------------------------------------------------------
# 7. Profile attempts & score come only from initial attempts, covered retry
#    reduces misses, and rebuild gives identical profile
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_role_rules_and_rebuild():
    db = _make_db()

    # Initial attempt 1: score 2, missed "Trade-offs" and "Index design"
    att1 = _make_attempt(
        "att1", "q1", score=2, concept="database-choice", role="initial",
        missed=["Trade-offs", "Index design"]
    )
    db.interview_attempts.insert_one(att1.model_dump(mode="json"))
    await update_from_attempt(db, att1)

    # Follow-up attempt: score 3, role="followup" -> should be ignored by profile
    att_fu = _make_attempt(
        "att_fu", "q_fu", score=3, concept="database-choice", role="followup",
        parent_attempt_id="att1"
    )
    db.interview_attempts.insert_one(att_fu.model_dump(mode="json"))
    await update_from_attempt(db, att_fu)

    # Retry attempt: score 4, role="retry", covers "Trade-offs"
    att_retry = InterviewAttempt(
        attempt_id="att_retry",
        question_id="q1",
        project_id="proj",
        concept="database-choice",
        difficulty=2,
        answer="Retry ans",
        evaluation=Evaluation(
            points=[
                PointResult(id=1, point="Trade-offs", kind="general", status="covered", quote="trades"),
                PointResult(id=2, point="Index design", kind="general", status="missing"),
            ],
            coverage=0.5,
            score=4,
        ),
        role="retry",
        parent_attempt_id="att1",
    )
    db.interview_attempts.insert_one(att_retry.model_dump(mode="json"))
    await update_from_attempt(db, att_retry)

    # Second initial attempt: score 4, role="initial"
    att2 = _make_attempt("att2", "q2", score=4, concept="database-choice", role="initial", missed=[])
    db.interview_attempts.insert_one(att2.model_dump(mode="json"))
    await update_from_attempt(db, att2)

    # Add lesson to interview_lessons for rebuild
    db.interview_lessons.insert_one({
        "lesson_id": "les_1",
        "attempt_id": "att1",
        "question_id": "q1",
        "project_id": "proj",
        "concept": "database-choice",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    profile = await get_profile(db, "proj")
    c = profile["concepts"]["database-choice"]

    # Attempts should only count initial: att1 and att2 -> attempts = 2
    assert c["attempts"] == 2
    # Score: first 2.0, then 0.5 * 2.0 + 0.5 * 4.0 = 3.0
    assert c["score"] == 3.0

    # Misses: "Trade-offs" was covered in retry -> count decremented and removed
    miss_points = [m["point"] for m in c["misses"]]
    assert "Trade-offs" not in miss_points
    assert "Index design" in miss_points

    # Rebuild must yield the same values
    await rebuild(db, "proj")
    rebuilt = await get_profile(db, "proj")
    rc = rebuilt["concepts"]["database-choice"]
    assert rc["attempts"] == 2
    assert rc["score"] == 3.0
    assert [m["point"] for m in rc["misses"]] == [m["point"] for m in c["misses"]]
    # Lesson replayed in rebuild
    assert rc.get("lessons") == 1
    assert rc.get("recent_deltas") == [2]  # after(4) - before(2)


# ---------------------------------------------------------------------------
# 8. Summary before -> after and revise list excludes recovered points
# ---------------------------------------------------------------------------

def test_summary_before_after_and_excludes_recovered_points():
    q = _make_question("q1", concept="database-choice")
    questions = [q]

    # Initial attempt misses "Point A" and "Point B"
    att_init = _make_attempt("a1", "q1", score=1, missed=["Point A", "Point B"])
    # Retry attempt covers "Point A", still misses "Point B"
    att_retry = InterviewAttempt(
        attempt_id="r1",
        question_id="q1",
        project_id="proj",
        concept="database-choice",
        difficulty=2,
        answer="Retry",
        evaluation=Evaluation(
            points=[
                PointResult(id=1, point="Point A", kind="general", status="covered", quote="a"),
                PointResult(id=2, point="Point B", kind="general", status="missing"),
            ],
            coverage=0.5,
            score=3,
        ),
        role="retry",
        parent_attempt_id="a1",
    )
    attempts = [att_init, att_retry]

    items = [
        {
            "question_id": "q1",
            "attempt_id": "a1",
            "status": "answered",
            "score": 1,
            "lesson_id": "les_1",
            "retry_attempt_id": "r1",
            "before": 1,
            "after": 3,
        }
    ]

    summary = build_summary(items, questions, attempts)

    assert summary["lessons_given"] == 1
    assert summary["average_improvement"] == 2.0
    assert len(summary["before_after"]) == 1
    assert summary["before_after"][0]["before"] == 1
    assert summary["before_after"][0]["after"] == 3
    assert summary["before_after"][0]["delta"] == 2

    # Revise list must exclude "Point A" (covered in retry), keep "Point B"
    rev_points = [r["point"] for r in summary["revise"]]
    assert "Point A" not in rev_points
    assert "Point B" in rev_points


def test_copied_retry_is_shown_as_unscored_and_excluded_from_improvement():
    questions = [
        {"question_id": "q_copy", "concept": "testing", "round": "tech", "difficulty": 2},
        {"question_id": "q_normal", "concept": "testing", "round": "tech", "difficulty": 2},
    ]
    attempts = [
        {"attempt_id": "r_copy", "copied": True, "evaluation": {"points": []}},
        {"attempt_id": "r_normal", "copied": False, "evaluation": {"points": []}},
    ]
    items = [
        {
            "question_id": "q_copy",
            "status": "answered",
            "score": 1,
            "retry_attempt_id": "r_copy",
            "before": 1,
            "after": 4,
        },
        {
            "question_id": "q_normal",
            "status": "answered",
            "score": 2,
            "retry_attempt_id": "r_normal",
            "before": 1,
            "after": 3,
        },
    ]

    summary = build_summary(items, questions, attempts)

    assert summary["average_improvement"] == 2.0
    assert summary["before_after"][0]["after"] == "n/a (copied)"
    assert summary["before_after"][0]["delta"] is None
    output: list[str] = []
    print_interview_summary(summary, out=output.append)
    assert any("n/a (copied)" in line for line in output)


@pytest.mark.asyncio
async def test_rubric_mismatched_followup_is_not_evaluated(monkeypatch):
    db = _make_db()
    initial_question = _make_question("q1")
    db.interview_questions.insert_one(initial_question.model_dump(mode="json"))
    followup_question = _make_question("q_fu").model_copy(
        update={"validation_notes": ["rubric may not match the question"]}
    )
    focus_points_seen: list[str] = []
    evaluated_roles: list[str] = []
    output: list[str] = []

    async def fake_generate(*args, **kwargs):
        return initial_question

    async def fake_generate_followup(*args, **kwargs):
        focus_points_seen.extend(kwargs["focus_points"])
        return followup_question

    async def fake_evaluate(db, question_id, answer, **kwargs):
        role = kwargs.get("role", "initial")
        evaluated_roles.append(role)
        attempt = InterviewAttempt(
            attempt_id=f"att_{role}",
            question_id=question_id,
            project_id="proj",
            concept="database-choice",
            difficulty=2,
            answer=answer,
            evaluation=Evaluation(
                points=[
                    PointResult(id=1, point="Explain a design trade-off", kind="general", status="missing"),
                    PointResult(id=2, point="Explain imports from app/agent/llm.py", kind="repo", status="missing"),
                    PointResult(id=3, point="Explain cache invalidation", kind="repo", status="partial"),
                ],
                misconceptions=[],
                coverage=0.5,
                score=2,
            ),
            role=role,
            parent_attempt_id=kwargs.get("parent_attempt_id"),
        )
        db.interview_attempts.insert_one(attempt.model_dump(mode="json"))
        return attempt

    await run_interview(
        db,
        "proj",
        n=1,
        ask_fn=AsyncMock(side_effect=[
            "Initial response with enough explanatory detail.",
            "Follow-up response with enough explanatory detail.",
            "Retry response with enough explanatory detail.",
        ]),
        generate_fn=fake_generate,
        generate_followup_fn=fake_generate_followup,
        evaluate_fn=fake_evaluate,
        lesson_fn=lambda _: {"lesson_id": "lesson1", "text": "Lesson", "notes": []},
        out=output.append,
    )

    assert focus_points_seen == ["Explain cache invalidation"]
    assert evaluated_roles == ["initial", "retry"]
    assert "Follow-up skipped: rubric did not match the question" in output


@pytest.mark.asyncio
async def test_runner_marks_copied_retry_as_not_counted():
    db = _make_db()
    question = _make_question("q1")
    db.interview_questions.insert_one(question.model_dump(mode="json"))
    output: list[str] = []

    async def fake_evaluate(db, question_id, answer, **kwargs):
        role = kwargs.get("role", "initial")
        attempt = _make_attempt(
            f"att_{role}",
            question_id,
            score=2 if role == "initial" else 4,
            role=role,
            parent_attempt_id=kwargs.get("parent_attempt_id"),
        )
        if role == "retry":
            attempt = attempt.model_copy(update={"copied": True})
        db.interview_attempts.insert_one(attempt.model_dump(mode="json"))
        return attempt

    def fail_followup(*args, **kwargs):
        raise InterviewGenerationError("No valid follow-up")

    await run_interview(
        db,
        "proj",
        n=1,
        ask_fn=AsyncMock(side_effect=["Initial answer", "Retry answer"]),
        generate_fn=lambda *args, **kwargs: question,
        generate_followup_fn=fail_followup,
        evaluate_fn=fake_evaluate,
        lesson_fn=lambda _: {"lesson_id": "lesson1", "text": "Lesson", "notes": []},
        out=output.append,
    )

    assert "Before: 2/4 -> After: copied from the lesson (not counted)" in output


# ---------------------------------------------------------------------------
# 9. /skip at the retry records skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_at_retry_records_skipped():
    db = _make_db()
    q = _make_question("q1")
    db.interview_questions.insert_one(q.model_dump(mode="json"))

    answers = ["Initial ans", "Followup ans", "/skip"]
    ans_idx = 0

    def fake_ask():
        nonlocal ans_idx
        a = answers[ans_idx]
        ans_idx += 1
        return a

    async def fake_eval(db, question_id, answer, **kwargs):
        role = kwargs.get("role", "initial")
        att = _make_attempt(f"att_{role}", question_id, score=2, role=role)
        db.interview_attempts.insert_one(att.model_dump(mode="json"))
        return att

    session = await run_interview(
        db,
        "proj",
        n=1,
        base_difficulty=2,
        ask_fn=fake_ask,
        generate_fn=lambda *a, **k: q,
        generate_followup_fn=lambda *a, **k: _make_question("q_fu"),
        evaluate_fn=fake_eval,
        lesson_fn=lambda a: {"lesson_id": "les_1", "text": "Lesson", "notes": []},
        teach=True,
        out=lambda x: None,
    )

    item = session["items"][0]
    assert item["status"] == "answered"
    assert item.get("retry_attempt_id") is None
    assert item["lesson_id"] == "les_1"
    assert session["status"] == "finished"


# ---------------------------------------------------------------------------
# 10. /quit mid-cycle saves partial state and resume continues at next slot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quit_mid_cycle_saves_partial_and_resume_continues():
    db = _make_db()
    q1 = _make_question("q1")
    q2 = _make_question("q2")
    db.interview_questions.insert_one(q1.model_dump(mode="json"))
    db.interview_questions.insert_one(q2.model_dump(mode="json"))

    # Initial ans, then /quit on follow-up
    session1 = await run_interview(
        db,
        "proj",
        n=2,
        base_difficulty=2,
        ask_fn=AsyncMock(side_effect=["Initial answer", "/quit"]),
        generate_fn=lambda *a, **k: q1,
        generate_followup_fn=lambda *a, **k: _make_question("q_fu"),
        evaluate_fn=lambda *a, **k: _make_attempt("a1", "q1", score=2),
        lesson_fn=lambda a: {"lesson_id": "les_1", "text": "Lesson", "notes": []},
        teach=True,
        out=lambda x: None,
    )

    # Saved with cycle interrupted, current at slot 1 (0-based)
    assert session1["status"] == "active"
    assert session1["current"] == 1
    assert session1["items"][0]["cycle_note"] == "cycle interrupted"

    # Resuming continues at slot 2 (Question 2)
    session2 = await run_interview(
        db,
        "proj",
        session_id=session1["session_id"],
        ask_fn=AsyncMock(return_value="Answer for Q2"),
        generate_fn=lambda *a, **k: q2,
        evaluate_fn=lambda *a, **k: _make_attempt("a2", "q2", score=4),
        teach=False,
        out=lambda x: None,
    )

    assert session2["status"] == "finished"
    assert len(session2["items"]) == 2
    assert session2["items"][1]["question_id"] == "q2"


# ---------------------------------------------------------------------------
# 11. Follow-up question too similar is rejected and retried
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_followup_question_too_similar_rejected_and_retried():
    db = _make_db()
    db.dossiers.insert_one({
        "project_id": "proj",
        "sections": {
            "technologies": [
                {
                    "tag": "fact",
                    "claim": "Uses PostgreSQL database relational storage.",
                    "evidence": ["fact:database:postgres"],
                    "details": {},
                }
            ]
        },
    })

    avoid = "Why did you choose PostgreSQL database storage for relational tables in your project?"

    # First attempt: > 70% overlap with avoid
    too_similar = {
        "question": "Why did you choose PostgreSQL database storage for relational tables in your service?",
        "concept": "database-choice",
        "key_points": [
            {"point": "Relational constraints ensure data integrity", "kind": "general"},
            {"point": "Uses PostgreSQL database relational storage", "kind": "repo", "support": 1},
            {"point": "ACID compliance for transactions", "kind": "general"},
        ],
        "common_mistakes": ["Ignoring connection pool limits", "No indexes on foreign keys"],
    }

    # Second attempt: clearly different question (< 70% overlap)
    different = {
        "question": "How do you handle schema migrations and rollback procedures?",
        "concept": "database-choice",
        "key_points": [
            {"point": "Automated migration scripts run in deployment", "kind": "general"},
            {"point": "Uses PostgreSQL database relational storage", "kind": "repo", "support": 1},
            {"point": "Backward-compatible column additions", "kind": "general"},
        ],
        "common_mistakes": ["Breaking changes deployed directly", "Missing rollback scripts"],
    }

    chat_mock = AsyncMock(side_effect=[
        SimpleNamespace(content=str(too_similar).replace("'", '"')),
        SimpleNamespace(content=str(different).replace("'", '"')),
    ])

    q = await generate_question(
        db,
        "proj",
        round_name="tech",
        difficulty=2,
        avoid_question=avoid,
        chat_fn=chat_mock,
        save=False,
    )

    assert chat_mock.await_count == 2
    assert "schema migrations" in q.question


# ---------------------------------------------------------------------------
# 12. Lesson repairs: label rename, line numbers, effect-words, modals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lesson_repairs():
    db = _make_db()
    q = _make_question("q1")
    db.interview_questions.insert_one(q.model_dump(mode="json"))

    att = _make_attempt("a1", "q1", score=2)
    db.interview_attempts.insert_one(att.model_dump(mode="json"))

    raw_text = (
        "Good: You touched on the technology.\n"
        "Idea:\n"
        "[General] Trade-offs should be weighed carefully.\n"
        "[General] Storage requirements dictate database selection.\n"
        "In your project:\n"
        # Line number [1] expansion
        f"[Fact] The system runs on Postgres [1]\n"
        # Effect word "latency" not in seed -> [Inferred]
        f"[Fact] PostgreSQL improves query latency significantly [{SEED_ID}]\n"
        # Modal "could" -> [General] with IDs removed
        f"[Fact] Sharding could be implemented later [{SEED_ID}]\n"
        # "Say:" label rename
        "Say: I selected PostgreSQL for relational transactional integrity."
    )

    chat_mock = AsyncMock(return_value=SimpleNamespace(content=raw_text, usage={}))

    lesson = await generate_lesson(db, attempt_id="a1", chat_fn=chat_mock, save=False)

    text = lesson["text"]
    notes = lesson["notes"]

    # 1. Label renamed
    assert "Say it like this:" in text
    assert any("renamed label" in n for n in notes)

    # 2. Line number [1] expanded
    assert f"[{SEED_ID}]" in text
    assert any("expanded line-number citations" in n for n in notes)

    # 3. Effect word -> [Inferred]
    assert "[Inferred] PostgreSQL improves query latency" in text
    assert any("changed [Fact] to [Inferred]" in n for n in notes)

    # 4. Modal -> [General] with evidence ID removed
    assert "[General] Sharding could be implemented later" in text
    assert SEED_ID not in text.splitlines()[text.splitlines().index(next(l for l in text.splitlines() if "Sharding could" in l))]
    assert any("changed [Fact] to [General]" in n for n in notes)
