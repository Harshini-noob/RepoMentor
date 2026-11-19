"""Tests for interview state machine engine and HTTP API."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import patch

from bson import ObjectId
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.api.locks import get_model_lock
from app.core.db import get_db
from app.interview.generate import InterviewGenerationError
from app.interview.schema import (
    Evaluation,
    InterviewAttempt,
    InterviewQuestion,
    KeyPoint,
    PointResult,
)
from app.main import app


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_interview_engine_db"]


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def pid():
    return str(ObjectId())


async def _setup_project(mock_db, pid: str) -> None:
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid),
        "status": "ready",
        "repo_full_name": f"test/repo_{pid}",
    })
    await mock_db.dossiers.insert_one({
        "project_id": pid,
        "sections": {
            "technologies": {
                "claims": [{"claim": "Uses FastAPI", "tag": "Fact"}],
            }
        },
    })


def _make_fake_gen(mock_db):
    async def fake_generate_question(db, project_id, **kwargs):
        qid = f"q-{uuid.uuid4().hex[:8]}"
        q_doc = {
            "question_id": qid,
            "project_id": project_id,
            "question": f"Question about {kwargs.get('concept', 'tech')}",
            "round": kwargs.get("round_name", "tech"),
            "difficulty": kwargs.get("difficulty", 2),
            "concept": kwargs.get("concept", "tech"),
            "key_points": [
                KeyPoint(point="Secret key point 1", kind="repo"),
                KeyPoint(point="Secret key point 2", kind="repo"),
            ],
            "common_mistakes": ["Secret mistake 1"],
            "seed": {"claims": [{"claim": "Secret seed claim"}]},
        }
        q_obj = InterviewQuestion(**q_doc)
        if kwargs.get("save", True):
            await db.interview_questions.insert_one(q_obj.model_dump(mode="json"))
        return q_obj
    return fake_generate_question


def _make_fake_eval(mock_db):
    async def fake_evaluate_answer(
        db,
        question_id: str,
        answer: str,
        save: bool = True,
        session_id: str | None = None,
        role: str = "initial",
        parent_attempt_id: str | None = None,
        **kwargs: Any,
    ):
        score = 4 if "strong" in answer.lower() else (4 if role == "retry" else 1)
        eval_obj = Evaluation(
            points=[PointResult(id=1, point="p1", status="covered" if score >= 3 else "missing", kind="repo")],
            misconceptions=[],
            unverified=[],
            strengths=["Well explained"] if score >= 3 else [],
            advice="Needs more detail" if score < 3 else "",
            coverage=1.0 if score >= 3 else 0.2,
            score=score,
            notes=[],
        )
        attempt = InterviewAttempt(
            question_id=question_id,
            project_id="proj",
            concept="tech",
            difficulty=2,
            answer=answer,
            evaluation=eval_obj,
            role=role,
            parent_attempt_id=parent_attempt_id,
            session_id=session_id,
        )
        if save:
            await db.interview_attempts.insert_one(attempt.model_dump(mode="json"))
        return attempt
    return fake_evaluate_answer


def _make_fake_lesson(mock_db):
    async def fake_generate_lesson(db, project_id, attempt_id, **kwargs):
        lesson = {
            "lesson_id": f"l-{uuid.uuid4().hex[:8]}",
            "attempt_id": attempt_id,
            "project_id": project_id,
            "concept": "tech",
            "text": "Here is a lesson on tech.",
            "notes": ["Note 1", "Note 2"],
        }
        if kwargs.get("save", True):
            await db.interview_lessons.insert_one(lesson)
        return lesson
    return fake_generate_lesson


# ---------------------------------------------------------------------------
# 1. Complete State Machine: Strong Answer Flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_strong_answer_flow(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)

    fake_gen = _make_fake_gen(mock_db)
    fake_eval = _make_fake_eval(mock_db)
    monkeypatch.setattr("app.interview.engine.generate_question", fake_gen)
    monkeypatch.setattr("app.interview.engine.evaluate_answer", fake_eval)

    # 1. Create session (2 questions)
    create_res = client.post(
        f"/projects/{pid}/interview/sessions",
        json={"questions": 2, "difficulty": 2},
    )
    assert create_res.status_code == 201
    s_data = create_res.json()
    assert "session_id" in s_data
    sid = s_data["session_id"]
    assert s_data["status"] == "active"
    assert len(s_data["plan"]) == 2

    # 2. Check state before first question
    st_res = client.get(f"/projects/{pid}/interview/sessions/{sid}/state")
    assert st_res.status_code == 200
    st = st_res.json()
    assert st["stage"] == "need_question"
    assert st["pending"] is None
    assert st["slot"] == 1
    assert st["total"] == 2

    # 3. Next -> generates first question
    n1 = client.post(f"/projects/{pid}/interview/sessions/{sid}/next")
    assert n1.status_code == 200
    act1 = n1.json()
    assert act1["action"] == "question"
    assert act1["kind"] == "initial"
    assert act1["slot"] == 1
    assert act1["total"] == 2
    q1_id = act1["question_id"]
    assert q1_id

    # 4. Next called again while awaiting_answer returns the SAME question
    n1_dup = client.post(f"/projects/{pid}/interview/sessions/{sid}/next")
    assert n1_dup.status_code == 200
    act1_dup = n1_dup.json()
    assert act1_dup["action"] == "question"
    assert act1_dup["question_id"] == q1_id

    # 5. Check state while awaiting answer
    st_res2 = client.get(f"/projects/{pid}/interview/sessions/{sid}/state")
    assert st_res2.status_code == 200
    st2 = st_res2.json()
    assert st2["stage"] == "awaiting_answer"
    assert st2["pending"]["question_id"] == q1_id

    # 6. Submit strong answer
    ans1 = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": q1_id, "answer": "This is a strong answer with full explanation."},
    )
    assert ans1.status_code == 200
    ev1 = ans1.json()
    assert ev1["score"] == 4
    assert ev1["next"] == "question"

    # 7. Next -> generates question 2
    n2 = client.post(f"/projects/{pid}/interview/sessions/{sid}/next")
    assert n2.status_code == 200
    act2 = n2.json()
    assert act2["action"] == "question"
    assert act2["slot"] == 2
    q2_id = act2["question_id"]
    assert q2_id != q1_id

    # 8. Submit strong answer for question 2 -> session finishes
    ans2 = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": q2_id, "answer": "Another strong answer."},
    )
    assert ans2.status_code == 200
    ev2 = ans2.json()
    assert ev2["score"] == 4
    assert ev2["next"] == "finished"

    # 9. Next -> returns finished action with summary
    n_fin = client.post(f"/projects/{pid}/interview/sessions/{sid}/next")
    assert n_fin.status_code == 200
    act_fin = n_fin.json()
    assert act_fin["action"] == "finished"
    assert "summary" in act_fin
    assert act_fin["summary"]["answered"] == 2


# ---------------------------------------------------------------------------
# 2. Teaching Cycle: Weak Answer Flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_weak_answer_teaching_cycle(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)

    fake_gen = _make_fake_gen(mock_db)
    fake_eval = _make_fake_eval(mock_db)
    fake_lesson = _make_fake_lesson(mock_db)
    monkeypatch.setattr("app.interview.engine.generate_question", fake_gen)
    monkeypatch.setattr("app.interview.engine.evaluate_answer", fake_eval)
    monkeypatch.setattr("app.interview.engine.generate_lesson", fake_lesson)

    # 1. Start 1-question session with teaching enabled
    create_res = client.post(
        f"/projects/{pid}/interview/sessions",
        json={"questions": 1, "teach": True, "teach_below": 2, "max_lessons": 2},
    )
    assert create_res.status_code == 201
    sid = create_res.json()["session_id"]

    # 2. First question
    q_act = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()
    assert q_act["action"] == "question"
    assert q_act["kind"] == "initial"
    q_id = q_act["question_id"]

    # 3. Submit weak answer -> triggers teaching cycle
    ans_res = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": q_id, "answer": "I don't know, very weak"},
    )
    assert ans_res.status_code == 200
    ev = ans_res.json()
    assert ev["score"] == 1
    assert ev["next"] == "lesson"

    # Check state -> stage need_lesson, cycle recorded
    st = client.get(f"/projects/{pid}/interview/sessions/{sid}/state").json()
    assert st["stage"] == "need_lesson"
    assert st["cycle"]["original_question_id"] == q_id
    assert st["cycle"]["before"] == 1
    assert st["lessons_given"] == 1

    # 4. Next -> generates lesson
    l_act = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()
    assert l_act["action"] == "lesson"
    assert "Here is a lesson" in l_act["text"]
    assert len(l_act["notes"]) == 2

    # Check state -> stage need_followup
    st = client.get(f"/projects/{pid}/interview/sessions/{sid}/state").json()
    assert st["stage"] == "need_followup"

    # 5. Next -> generates follow-up question
    fu_act = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()
    assert fu_act["action"] == "question"
    assert fu_act["kind"] == "followup"
    fu_qid = fu_act["question_id"]

    # 6. Submit follow-up answer -> stage need_retry
    ans_fu = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": fu_qid, "answer": "Here is my followup response"},
    )
    assert ans_fu.status_code == 200
    assert ans_fu.json()["next"] == "retry"

    st = client.get(f"/projects/{pid}/interview/sessions/{sid}/state").json()
    assert st["stage"] == "need_retry"

    # 7. Next -> returns ORIGINAL question again with kind retry
    ret_act = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()
    assert ret_act["action"] == "question"
    assert ret_act["kind"] == "retry"
    assert ret_act["question_id"] == q_id

    # 8. Submit retry answer (score 4)
    ans_ret = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": q_id, "answer": "Now improved retry answer"},
    )
    assert ans_ret.status_code == 200
    ret_ev = ans_ret.json()
    assert ret_ev["before"] == 1
    assert ret_ev["after"] == 4
    assert ret_ev["next"] == "finished"

    # Verify session items in DB contain before and after
    s_doc = await mock_db.interview_sessions.find_one({"session_id": sid})
    assert len(s_doc["items"]) == 1
    item = s_doc["items"][0]
    assert item["before"] == 1
    assert item["after"] == 4
    assert item["status"] == "answered"

    # 9. Next -> finished summary
    fin_act = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()
    assert fin_act["action"] == "finished"
    assert fin_act["summary"]["answered"] == 1


# ---------------------------------------------------------------------------
# 3. Wrong Question ID Returns 409
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_answer_wrong_question_id_returns_409(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)
    monkeypatch.setattr("app.interview.engine.generate_question", _make_fake_gen(mock_db))

    create_res = client.post(f"/projects/{pid}/interview/sessions", json={"questions": 1})
    sid = create_res.json()["session_id"]
    client.post(f"/projects/{pid}/interview/sessions/{sid}/next")

    bad_ans = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": "wrong-id", "answer": "my answer"},
    )
    assert bad_ans.status_code == 409
    assert "cannot advance:" in bad_ans.json()["detail"]


# ---------------------------------------------------------------------------
# 4. max_lessons Stops a Second Cycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_lessons_stops_second_cycle(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)

    fake_gen = _make_fake_gen(mock_db)
    fake_eval = _make_fake_eval(mock_db)
    fake_lesson = _make_fake_lesson(mock_db)
    monkeypatch.setattr("app.interview.engine.generate_question", fake_gen)
    monkeypatch.setattr("app.interview.engine.evaluate_answer", fake_eval)
    monkeypatch.setattr("app.interview.engine.generate_lesson", fake_lesson)

    # 2 questions, but max_lessons=1
    create_res = client.post(
        f"/projects/{pid}/interview/sessions",
        json={"questions": 2, "teach": True, "teach_below": 2, "max_lessons": 1},
    )
    sid = create_res.json()["session_id"]

    # Q1: weak answer -> triggers cycle 1
    q1 = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()["question_id"]
    ans1 = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": q1, "answer": "weak answer"},
    ).json()
    assert ans1["next"] == "lesson"

    # Lesson -> Followup -> Followup answer -> Retry -> Retry answer
    client.post(f"/projects/{pid}/interview/sessions/{sid}/next")  # lesson
    fu = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()["question_id"]  # followup
    client.post(f"/projects/{pid}/interview/sessions/{sid}/answer", json={"question_id": fu, "answer": "fu"})
    client.post(f"/projects/{pid}/interview/sessions/{sid}/next")  # retry
    client.post(f"/projects/{pid}/interview/sessions/{sid}/answer", json={"question_id": q1, "answer": "retry"})

    # Q2: weak answer -> because lessons_given == max_lessons (1), should NOT trigger lesson!
    q2_resp = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()
    q2 = q2_resp["question_id"]
    ans2 = client.post(
        f"/projects/{pid}/interview/sessions/{sid}/answer",
        json={"question_id": q2, "answer": "another weak answer"},
    ).json()
    assert ans2["next"] == "finished"  # Did not go to lesson!


# ---------------------------------------------------------------------------
# 5. Skip at All 3 Kinds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_initial_followup_and_retry(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)

    fake_gen = _make_fake_gen(mock_db)
    fake_eval = _make_fake_eval(mock_db)
    fake_lesson = _make_fake_lesson(mock_db)
    monkeypatch.setattr("app.interview.engine.generate_question", fake_gen)
    monkeypatch.setattr("app.interview.engine.evaluate_answer", fake_eval)
    monkeypatch.setattr("app.interview.engine.generate_lesson", fake_lesson)

    # A) Skip initial question
    c_res = client.post(f"/projects/{pid}/interview/sessions", json={"questions": 1})
    sid_a = c_res.json()["session_id"]
    client.post(f"/projects/{pid}/interview/sessions/{sid_a}/next")  # awaiting_answer
    skip_res = client.post(f"/projects/{pid}/interview/sessions/{sid_a}/skip")
    assert skip_res.status_code == 200
    assert skip_res.json()["action"] == "skipped"
    assert skip_res.json()["stage"] == "finished"

    doc_a = await mock_db.interview_sessions.find_one({"session_id": sid_a})
    assert doc_a["items"][0]["status"] == "skipped"

    # B) Skip followup question -> moves to need_retry with cycle note 'followup skipped'
    c_res2 = client.post(f"/projects/{pid}/interview/sessions", json={"questions": 1, "teach": True})
    sid_b = c_res2.json()["session_id"]
    q1 = client.post(f"/projects/{pid}/interview/sessions/{sid_b}/next").json()["question_id"]
    client.post(f"/projects/{pid}/interview/sessions/{sid_b}/answer", json={"question_id": q1, "answer": "weak"})
    client.post(f"/projects/{pid}/interview/sessions/{sid_b}/next")  # lesson
    client.post(f"/projects/{pid}/interview/sessions/{sid_b}/next")  # followup question
    # Skip during followup
    skip_b = client.post(f"/projects/{pid}/interview/sessions/{sid_b}/skip")
    assert skip_b.status_code == 200
    assert skip_b.json()["stage"] == "need_retry"
    doc_b = await mock_db.interview_sessions.find_one({"session_id": sid_b})
    assert doc_b["cycle"]["cycle_note"] == "followup skipped"

    # C) Skip retry question -> records original item with cycle note 'retry skipped'
    # Advance to retry question
    client.post(f"/projects/{pid}/interview/sessions/{sid_b}/next")  # retry question
    # Skip during retry
    skip_c = client.post(f"/projects/{pid}/interview/sessions/{sid_b}/skip")
    assert skip_c.status_code == 200
    assert skip_c.json()["stage"] == "finished"
    doc_c = await mock_db.interview_sessions.find_one({"session_id": sid_b})
    item_c = doc_c["items"][0]
    assert item_c["status"] == "answered"
    assert item_c["cycle_note"] == "retry skipped"


# ---------------------------------------------------------------------------
# 6. Generation Failure Records no_question and Advances
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generation_failure_records_no_question_and_advances(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)

    async def fail_generate(*args, **kwargs):
        raise InterviewGenerationError("LLM API failed")

    monkeypatch.setattr("app.interview.engine.generate_question", fail_generate)

    # 1-question session where generation fails
    c_res = client.post(f"/projects/{pid}/interview/sessions", json={"questions": 1})
    sid = c_res.json()["session_id"]

    next_res = client.post(f"/projects/{pid}/interview/sessions/{sid}/next")
    assert next_res.status_code == 200
    # Because slot 1 failed to generate, it recorded no_question and plan exhausted -> finished
    assert next_res.json()["action"] == "finished"

    doc = await mock_db.interview_sessions.find_one({"session_id": sid})
    assert len(doc["items"]) == 1
    assert doc["items"][0]["status"] == "no_question"


# ---------------------------------------------------------------------------
# 7. get_state and Question Responses NEVER Leak Rubrics or Seed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_leak_of_key_points_common_mistakes_or_seed(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)
    monkeypatch.setattr("app.interview.engine.generate_question", _make_fake_gen(mock_db))

    c_res = client.post(f"/projects/{pid}/interview/sessions", json={"questions": 1})
    sid = c_res.json()["session_id"]

    # 1. State before question
    st1 = client.get(f"/projects/{pid}/interview/sessions/{sid}/state").json()
    assert "key_points" not in st1
    assert "common_mistakes" not in st1
    assert "seed" not in st1

    # 2. Next question
    q_act = client.post(f"/projects/{pid}/interview/sessions/{sid}/next").json()
    assert "key_points" not in q_act
    assert "common_mistakes" not in q_act
    assert "seed" not in q_act

    # 3. State while awaiting answer
    st2 = client.get(f"/projects/{pid}/interview/sessions/{sid}/state").json()
    assert "key_points" not in st2
    assert "common_mistakes" not in st2
    assert "seed" not in st2
    if st2.get("pending"):
        assert "key_points" not in st2["pending"]
        assert "common_mistakes" not in st2["pending"]
        assert "seed" not in st2["pending"]

    # Verify no forbidden substring anywhere in the serialized json text
    st2_str = str(st2)
    assert "Secret key point" not in st2_str
    assert "Secret mistake" not in st2_str
    assert "Secret seed claim" not in st2_str


# ---------------------------------------------------------------------------
# 8. 409 When Model Lock is Held (next and answer)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_collision_returns_409(client, mock_db, pid, monkeypatch) -> None:
    await _setup_project(mock_db, pid)
    monkeypatch.setattr("app.interview.engine.generate_question", _make_fake_gen(mock_db))
    monkeypatch.setattr("app.interview.engine.evaluate_answer", _make_fake_eval(mock_db))

    c_res = client.post(f"/projects/{pid}/interview/sessions", json={"questions": 1})
    sid = c_res.json()["session_id"]

    lock = get_model_lock()
    await lock.acquire()
    try:
        # 1. POST /next while lock is held -> 409
        res_next = client.post(f"/projects/{pid}/interview/sessions/{sid}/next")
        assert res_next.status_code == 409
        assert res_next.json()["detail"] == "busy: another request is using the model"

        # 2. POST /answer while lock is held -> 409
        res_ans = client.post(
            f"/projects/{pid}/interview/sessions/{sid}/answer",
            json={"question_id": "dummy-qid", "answer": "my answer"},
        )
        assert res_ans.status_code == 409
        assert res_ans.json()["detail"] == "busy: another request is using the model"
    finally:
        lock.release()

    # After releasing, next succeeds
    res_ok = client.post(f"/projects/{pid}/interview/sessions/{sid}/next")
    assert res_ok.status_code == 200


# ---------------------------------------------------------------------------
# 9. 404 for Unknown Project or Mismatched Session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_project_or_mismatched_session_returns_404(client, mock_db, pid) -> None:
    await _setup_project(mock_db, pid)

    unknown_pid = str(ObjectId())
    other_pid = str(ObjectId())
    await _setup_project(mock_db, other_pid)

    # Create session on pid
    c_res = client.post(f"/projects/{pid}/interview/sessions", json={"questions": 1})
    sid = c_res.json()["session_id"]

    # A) Unknown project
    assert client.get(f"/projects/{unknown_pid}/interview/sessions/{sid}/state").status_code == 404
    assert client.post(f"/projects/{unknown_pid}/interview/sessions/{sid}/next").status_code == 404
    assert client.post(f"/projects/{unknown_pid}/interview/sessions/{sid}/skip").status_code == 404
    assert client.post(
        f"/projects/{unknown_pid}/interview/sessions/{sid}/answer",
        json={"question_id": "q", "answer": "a"},
    ).status_code == 404

    # B) Session belongs to pid, but request specifies other_pid
    assert client.get(f"/projects/{other_pid}/interview/sessions/{sid}/state").status_code == 404
    assert client.post(f"/projects/{other_pid}/interview/sessions/{sid}/next").status_code == 404
    assert client.post(f"/projects/{other_pid}/interview/sessions/{sid}/skip").status_code == 404
    assert client.post(
        f"/projects/{other_pid}/interview/sessions/{sid}/answer",
        json={"question_id": "q", "answer": "a"},
    ).status_code == 404
