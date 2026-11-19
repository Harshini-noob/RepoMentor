"""Tests for Phase 8, step 1a: HTTP API for chat and read-only data."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from bson import ObjectId
from fastapi.testclient import TestClient
import httpx
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.agent.state import AgentRun, TraceStep
from app.core.db import get_db
from app.dossier.schema import SECTIONS
from app.main import app


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_phase8_api_db"]


@pytest.fixture
def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def ready_project_id(mock_db):
    pid = str(ObjectId())
    # synchronously setup or async fixture
    return pid


# ---------------------------------------------------------------------------
# 1. CORS Headers
# ---------------------------------------------------------------------------

def test_cors_headers(client, mock_db) -> None:
    # Test preflight and GET with allowed origin
    headers = {"Origin": "http://localhost:5173"}
    res = client.get("/health", headers=headers)
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"

    headers_127 = {"Origin": "http://127.0.0.1:5173"}
    res2 = client.get("/health", headers=headers_127)
    assert res2.status_code == 200
    assert res2.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


# ---------------------------------------------------------------------------
# 2. 404 for Unknown Projects
# ---------------------------------------------------------------------------

def test_unknown_project_returns_404(client) -> None:
    unknown_id = str(ObjectId())

    endpoints_get = [
        f"/projects/{unknown_id}/chat/sessions",
        f"/projects/{unknown_id}/chat/sessions/any_session",
        f"/projects/{unknown_id}/dossier/summary",
        f"/projects/{unknown_id}/dossier/sections/technologies",
        f"/projects/{unknown_id}/profile",
        f"/projects/{unknown_id}/interview/plan",
        f"/projects/{unknown_id}/interview/sessions",
        f"/projects/{unknown_id}/interview/sessions/any_session/report",
    ]
    for ep in endpoints_get:
        res = client.get(ep)
        assert res.status_code == 404, f"{ep} did not return 404: {res.status_code}"
        assert "not found" in res.json()["detail"].lower()

    # POST endpoints
    res_post_session = client.post(f"/projects/{unknown_id}/chat/sessions")
    assert res_post_session.status_code == 404

    res_post_chat = client.post(f"/projects/{unknown_id}/chat", json={"message": "hello"})
    assert res_post_chat.status_code == 404


# ---------------------------------------------------------------------------
# 3. Chat Session Lifecycle: Create, Get, List
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_session_create_get_list(client, mock_db) -> None:
    pid = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid),
        "status": "ready",
        "repo_full_name": "test/repo",
    })

    # 1. Create session
    res = client.post(f"/projects/{pid}/chat/sessions")
    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    sid = data["session_id"]

    # 2. Add some messages into the session
    await mock_db.sessions.update_one(
        {"session_id": sid},
        {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "content": "What is this repo?", "ts": "2026-11-19T12:00:00Z"},
                        {"role": "assistant", "content": "A test repo.", "ts": "2026-11-19T12:00:05Z"},
                    ]
                }
            }
        },
    )

    # 3. Get session
    res_get = client.get(f"/projects/{pid}/chat/sessions/{sid}")
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert get_data["session_id"] == sid
    assert len(get_data["messages"]) == 2
    assert get_data["messages"][0]["role"] == "user"
    assert get_data["messages"][0]["content"] == "What is this repo?"

    # 4. List sessions
    res_list = client.get(f"/projects/{pid}/chat/sessions")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert len(list_data) == 1
    assert list_data[0]["session_id"] == sid
    assert list_data[0]["turns"] == 1
    assert list_data[0]["first_question"] == "What is this repo?"

    # 5. Project not ready gives 409 on create session
    pid_not_ready = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid_not_ready),
        "status": "ingesting",
        "repo_full_name": "test/notready",
    })
    res_conflict = client.post(f"/projects/{pid_not_ready}/chat/sessions")
    assert res_conflict.status_code == 409
    assert "not ready" in res_conflict.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Chat SSE Stream: Order of Events (session, event, step, answer, done)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_stream_order(client, mock_db) -> None:
    pid = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid),
        "status": "ready",
        "repo_full_name": "test/repo",
    })

    async def fake_ask(db, project_id, session_id, message, on_step=None, on_event=None, **kwargs):
        if on_event:
            on_event("thinking about the query")
        if on_step:
            step = TraceStep(
                step=1,
                thought_summary="Searching files",
                tool="file_search",
                args={"pattern": "*.py"},
                result_preview="Found main.py",
                evidence_ids=["file:main.py"],
                ms=100,
                tokens=40,
            )
            on_step(step)
        return AgentRun(
            goal=message,
            project_id=project_id,
            steps=[],
            final_answer="The project uses Python.",
            stop_reason="answered",
            total_tokens=140,
        )

    with patch("app.api.mentor_api.ask", side_effect=fake_ask):
        res = client.post(
            f"/projects/{pid}/chat",
            json={"message": "What language is used?"},
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers.get("content-type", "")

        events = []
        for line in res.text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                events.append(payload)

        # Verify event sequence
        types = [e["type"] for e in events]
        assert types == ["session", "event", "step", "answer", "done"]

        # Check event details
        assert "session_id" in events[0]
        assert events[1]["text"] == "thinking about the query"
        assert events[2]["step"] == 1
        assert events[2]["tool"] == "file_search"
        assert events[2]["preview"] == "Found main.py"
        assert events[2]["evidence"] == ["file:main.py"]
        assert events[2]["ms"] == 100
        assert events[2]["tokens"] == 40
        assert events[3]["text"] == "The project uses Python."
        assert events[3]["stop_reason"] == "answered"
        assert events[3]["tokens"] == 140
        assert events[4]["type"] == "done"


# ---------------------------------------------------------------------------
# 5. Concurrency 409 and Lock Release after Error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_concurrency_409_and_lock_release_after_error(client, mock_db) -> None:
    pid = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid),
        "status": "ready",
        "repo_full_name": "test/repo",
    })

    # Test lock release after error
    async def failing_ask(*args, **kwargs):
        raise RuntimeError("Internal provider timeout error: model gemini-pro failed")

    with patch("app.api.mentor_api.ask", side_effect=failing_ask):
        res = client.post(
            f"/projects/{pid}/chat",
            json={"message": "hello error"},
        )
        assert res.status_code == 200
        events = []
        for line in res.text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))

        types = [e["type"] for e in events]
        assert "error" in types
        assert "done" in types

        # Check generic error message (never reveals internal provider or model info)
        err_event = next(e for e in events if e["type"] == "error")
        msg = err_event["message"].lower()
        assert "gemini" not in msg
        assert "provider" not in msg
        assert "error occurred" in msg

    # Now verify the lock was released: another call succeeds immediately
    async def ok_ask(*args, **kwargs):
        return AgentRun(
            goal="ok",
            project_id=pid,
            steps=[],
            final_answer="Recovered successfully",
            stop_reason="answered",
            total_tokens=20,
        )

    with patch("app.api.mentor_api.ask", side_effect=ok_ask):
        res_after = client.post(
            f"/projects/{pid}/chat",
            json={"message": "retry after error"},
        )
        assert res_after.status_code == 200
        assert "Recovered successfully" in res_after.text

    # Test 409 when lock is held
    from app.api.mentor_api import _chat_lock
    await _chat_lock.acquire()
    try:
        res_busy = client.post(
            f"/projects/{pid}/chat",
            json={"message": "should be busy"},
        )
        assert res_busy.status_code == 409
        assert "busy" in res_busy.json()["detail"].lower()
    finally:
        _chat_lock.release()


# ---------------------------------------------------------------------------
# 6. Dossier Summary Counts and Section Details
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dossier_summary_and_sections(client, mock_db) -> None:
    pid = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid),
        "status": "ready",
        "repo_full_name": "test/repo",
    })

    dossier_doc = {
        "project_id": pid,
        "version": 1,
        "generated_from_sha": "def5678",
        "sections": {
            "technologies": [
                {
                    "claim": "FastAPI is the web framework",
                    "tag": "fact",
                    "evidence": ["file:requirements.txt:1"],
                    "confidence": 1.0,
                    "source": "static",
                    "details": {"commit": "sha1234"},
                },
                {
                    "claim": "Pydantic is used for validation",
                    "tag": "inferred",
                    "evidence": ["file:app/main.py"],
                    "confidence": 0.9,
                    "source": "user",
                    "details": {},
                },
            ],
            "decisions": [
                {
                    "claim": "Chose MongoDB for flexible document store",
                    "tag": "uncertain",
                    "evidence": [],
                    "confidence": 0.5,
                    "source": "static",
                }
            ],
        },
    }
    await mock_db.dossiers.insert_one(dossier_doc)

    # 1. Summary
    res_sum = client.get(f"/projects/{pid}/dossier/summary")
    assert res_sum.status_code == 200
    sum_data = res_sum.json()
    assert sum_data["version"] == 1
    assert sum_data["generated_from_sha"] == "def5678"

    tech_sum = next(s for s in sum_data["sections"] if s["name"] == "technologies")
    assert tech_sum["claims"] == 2
    assert tech_sum["facts"] == 1
    assert tech_sum["inferred"] == 1
    assert tech_sum["user_edited"] == 1

    dec_sum = next(s for s in sum_data["sections"] if s["name"] == "decisions")
    assert dec_sum["claims"] == 1
    assert dec_sum["uncertain"] == 1

    # 2. Section detail
    res_sec = client.get(f"/projects/{pid}/dossier/sections/technologies")
    assert res_sec.status_code == 200
    sec_data = res_sec.json()
    assert sec_data["name"] == "technologies"
    assert len(sec_data["claims"]) == 2
    assert sec_data["claims"][0]["claim"] == "FastAPI is the web framework"
    assert sec_data["claims"][0]["commit"] == "sha1234"
    assert sec_data["claims"][1]["source"] == "user"

    # 3. Unknown section returns 404
    res_unknown_sec = client.get(f"/projects/{pid}/dossier/sections/nonexistent_section")
    assert res_unknown_sec.status_code == 404


# ---------------------------------------------------------------------------
# 7. Interview API: Profile, Plan, Sessions, Report (Pure code, no models)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interview_profile_plan_sessions_report(client, mock_db) -> None:
    pid = str(ObjectId())
    await mock_db.projects.insert_one({
        "_id": ObjectId(pid),
        "status": "ready",
        "repo_full_name": "test/repo",
    })

    # Empty profile returns 200 with concepts: []
    res_empty_prof = client.get(f"/projects/{pid}/profile")
    assert res_empty_prof.status_code == 200
    assert res_empty_prof.json()["concepts"] == []

    # Insert learner profile
    await mock_db.learner_profile.insert_one({
        "project_id": pid,
        "concepts": {
            "caching": {
                "score": 1.0,
                "status": "weak",
                "attempts": 2,
                "last_seen": "2026-11-19T10:00:00Z",
                "misses": [{"point": "Did not mention TTL", "kind": "repo", "count": 2}],
            },
            "auth": {
                "score": 3.5,
                "status": "strong",
                "attempts": 3,
                "last_seen": "2026-11-19T11:00:00Z",
                "misses": [],
            },
        },
    })

    res_prof = client.get(f"/projects/{pid}/profile")
    assert res_prof.status_code == 200
    prof_data = res_prof.json()
    assert len(prof_data["concepts"]) == 2
    # Sorted by score ascending: caching (1.0) comes before auth (3.5)
    assert prof_data["concepts"][0]["concept"] == "caching"
    assert prof_data["concepts"][0]["score"] == 1.0
    assert prof_data["concepts"][1]["concept"] == "auth"
    assert "caching" in prof_data["revise_first"]

    # Dossier for plan
    await mock_db.dossiers.insert_one({
        "project_id": pid,
        "sections": {
            "technologies": [
                {
                    "claim": "Redis cache used with TTL",
                    "tag": "fact",
                    "evidence": ["file:cache.py"],
                    "details": {"commit": "c1"},
                }
            ],
            "decisions": [
                {
                    "claim": "Chose JWT for authentication",
                    "tag": "fact",
                    "evidence": ["commit:c2"],
                    "details": {"commit": "c2"},
                }
            ],
        },
    })

    # Interview Plan: pure code, no models called, nothing saved
    res_plan = client.get(f"/projects/{pid}/interview/plan?questions=3&difficulty=2")
    assert res_plan.status_code == 200
    plan_data = res_plan.json()
    assert "slots" in plan_data
    assert len(plan_data["slots"]) == 3
    # Check slots have required fields
    for s in plan_data["slots"]:
        assert "slot" in s
        assert "difficulty" in s
        assert "reason" in s
        assert "claim" in s

    # Ensure no interview session was saved during plan generation
    session_count = await mock_db.interview_sessions.count_documents({})
    assert session_count == 0

    # Insert interview session with items
    session_id = "test-session-123"
    await mock_db.interview_sessions.insert_one({
        "session_id": session_id,
        "project_id": pid,
        "status": "finished",
        "plan_mode": "adaptive",
        "created_at": "2026-11-19T10:00:00Z",
        "items": [
            {
                "question_id": "q1",
                "attempt_id": "a1",
                "status": "answered",
                "score": 3,
            },
            {
                "question_id": "q2",
                "attempt_id": None,
                "status": "skipped",
                "score": None,
            },
        ],
    })

    # List interview sessions
    res_sess = client.get(f"/projects/{pid}/interview/sessions")
    assert res_sess.status_code == 200
    sess_list = res_sess.json()
    assert len(sess_list) == 1
    assert sess_list[0]["session_id"] == session_id
    assert sess_list[0]["plan_mode"] == "adaptive"
    assert sess_list[0]["answered"] == 1
    assert sess_list[0]["average_score"] == 3.0

    # Questions and Attempts for Report
    await mock_db.interview_questions.insert_one({
        "question_id": "q1",
        "project_id": pid,
        "round": "tech",
        "difficulty": 2,
        "concept": "caching",
        "question": "How is cache configured?",
        "key_points": [],
        "common_mistakes": [],
        "seed": {},
    })
    await mock_db.interview_attempts.insert_one({
        "attempt_id": "a1",
        "question_id": "q1",
        "project_id": pid,
        "concept": "caching",
        "difficulty": 2,
        "answer": "Candidate answer here",
        "evaluation": {
            "points": [
                {"id": 1, "point": "Uses Redis TTL", "kind": "repo", "status": "covered"},
                {"id": 2, "point": "Cache invalidation", "kind": "general", "status": "missing"},
            ],
            "misconceptions": [],
            "unverified": [],
            "strengths": ["Good understanding"],
            "advice": "Review invalidation",
            "coverage": 0.5,
            "score": 3,
        },
    })

    # Report endpoint (same data as interview-report, pure code)
    res_rep = client.get(f"/projects/{pid}/interview/sessions/{session_id}/report")
    assert res_rep.status_code == 200
    rep_data = res_rep.json()
    assert rep_data["answered"] == 1
    assert rep_data["skipped"] == 1
    assert rep_data["average_score"] == 3.0
    assert len(rep_data["rows"]) == 2
    assert "caching" in rep_data["per_concept"]
