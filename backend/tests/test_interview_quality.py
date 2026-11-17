"""Tests for interview rubric quality and evaluator honesty."""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, patch

import mongomock
import pytest

from app.agent.state import LLMReply
from app.cli import _print_evaluation_result
from app.interview.evaluate import evaluate_answer
from app.interview.generate import generate_question
from app.interview.runner import run_interview
from app.interview.schema import (
    Evaluation,
    InterviewAttempt,
    InterviewQuestion,
    KeyPoint,
    PointResult,
    UnverifiedClaim,
)
from app.interview.seeds import pick_seed


def _make_db():
    return mongomock.MongoClient()["repomentor"]


# ---------------------------------------------------------------------------
# 1. Tech seed includes decisions claims mentioning supabase or logger.py
# ---------------------------------------------------------------------------

def test_tech_seed_includes_related_decisions_claims():
    dossier = {
        "project_id": "proj_test",
        "sections": {
            "technologies": [
                {
                    "claim": "Project uses Supabase for storage and auth in `logger.py`",
                    "tag": "fact",
                    "evidence": ["fact:database:supabase", "file:app/logger.py"],
                    "details": {},
                }
            ],
            "decisions": [
                {
                    "claim": "Chose supabase for managed postgres",
                    "tag": "fact",
                    "evidence": ["commit:c1:db.py"],
                    "details": {"commit": "c1"},
                },
                {
                    "claim": "Added supabase auth integration",
                    "tag": "inferred",
                    "evidence": ["commit:c1:auth.py"],
                    "details": {"commit": "c1"},
                },
                {
                    "claim": "Third commit c1 claim mentioning supabase",
                    "tag": "fact",
                    "evidence": ["commit:c1:extra.py"],
                    "details": {"commit": "c1"},  # Should be skipped: at most 2 for commit c1
                },
                {
                    "claim": "Configured logger.py for structured logging",
                    "tag": "fact",
                    "evidence": ["commit:c2:logger.py"],
                    "details": {"commit": "c2"},
                },
                {
                    "claim": "Another claim mentioning supabase client",
                    "tag": "fact",
                    "evidence": ["commit:c3:api.py"],
                    "details": {"commit": "c3"},
                },
                {
                    "claim": "Fifth related claim mentioning supabase",
                    "tag": "fact",
                    "evidence": ["commit:c4:more.py"],
                    "details": {"commit": "c4"},
                },
            ],
        },
    }

    seed = pick_seed(dossier, round_name="tech", asked=[])
    claims = seed["claims"]

    # Main claim is first
    assert "Supabase" in claims[0]["claim"]
    # Seed has at most 5 claims
    assert len(claims) <= 5
    # Related claims from decisions are included
    rel_texts = [c["claim"] for c in claims[1:]]
    assert any("logger.py" in t or "supabase" in t.lower() for t in rel_texts)

    # At most 2 from commit c1
    c1_count = sum(1 for c in claims[1:] if "c1" in str(c.get("evidence", [])))
    assert c1_count <= 2


# ---------------------------------------------------------------------------
# 2. Difficulty-2 rubric with no general point triggers one retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_difficulty_2_no_general_point_triggers_retry():
    db = _make_db()
    dossier = {
        "project_id": "proj_diff2",
        "sections": {
            "technologies": [
                {
                    "claim": "Uses PostgreSQL database for relational tables storage",
                    "tag": "fact",
                    "evidence": ["fact:database:postgres"],
                    "details": {},
                }
            ],
            "decisions": [],
        },
    }
    db.dossiers.insert_one(dossier)

    # Response 1: 0 general points (all 3 are valid repo points with >=2 word overlap)
    res_1 = {
        "question": "What role does PostgreSQL play in the architecture?",
        "concept": "database-choice",
        "key_points": [
            {"point": "Uses PostgreSQL database", "kind": "repo", "support": 1},
            {"point": "PostgreSQL database storage", "kind": "repo", "support": 1},
            {"point": "Relational tables in PostgreSQL", "kind": "repo", "support": 1},
        ],
        "common_mistakes": ["Mistake 1", "Mistake 2"],
    }

    # Response 2 (retry): 1 general point
    res_2 = {
        "question": "What role does PostgreSQL play in the architecture?",
        "concept": "database-choice",
        "key_points": [
            {"point": "Uses PostgreSQL database", "kind": "repo", "support": 1},
            {"point": "ACID compliance ensures financial data integrity", "kind": "general", "support": None},
            {"point": "Relational tables in PostgreSQL", "kind": "repo", "support": 1},
        ],
        "common_mistakes": ["Mistake 1", "Mistake 2"],
    }

    call_count = 0

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        content = json.dumps(res_1 if call_count == 1 else res_2)
        return LLMReply(content=content)

    q = await generate_question(
        db,
        "proj_diff2",
        round_name="tech",
        difficulty=2,
        chat_fn=fake_chat,
        save=True,
    )

    # Assert retry was triggered
    assert call_count == 2
    # Result has at least 1 general point
    assert any(kp.kind == "general" for kp in q.key_points)


# ---------------------------------------------------------------------------
# 3. General point containing a seed filename is dropped with note
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_general_point_with_seed_filename_dropped():
    db = _make_db()
    dossier = {
        "project_id": "proj_drop",
        "sections": {
            "components": [
                {
                    "claim": "Logger configured in logger.py with formatting",
                    "tag": "fact",
                    "evidence": ["file:app/logger.py"],
                    "details": {},
                }
            ],
            "decisions": [],
        },
    }
    db.dossiers.insert_one(dossier)

    # Model returns 1 general point that mentions seed filename "logger.py"
    # and 2 clean general points
    res = {
        "question": "How is logging handled in your application?",
        "concept": "observability",
        "key_points": [
            {"point": "Configure logger.py with standard library", "kind": "general", "support": None},
            {"point": "Centralised logging improves traceability across services", "kind": "general", "support": None},
            {"point": "Structured logs allow automated log aggregation and monitoring", "kind": "general", "support": None},
            {"point": "Logger configured in logger.py with formatting", "kind": "repo", "support": 1},
        ],
        "common_mistakes": ["Mistake 1", "Mistake 2"],
    }

    fake_chat = AsyncMock(return_value=LLMReply(content=json.dumps(res)))

    q = await generate_question(
        db,
        "proj_drop",
        round_name="architecture",
        difficulty=2,
        chat_fn=fake_chat,
        save=False,
    )

    # General point mentioning logger.py must be dropped
    assert not any("Configure logger.py" in kp.point for kp in q.key_points)
    assert "general point mentions repo names" in q.validation_notes


# ---------------------------------------------------------------------------
# 4. Unverified quotes not found in answer are dropped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unverified_quotes_not_found_dropped():
    db = _make_db()
    db.interview_questions.insert_one({
        "question_id": "q_unv",
        "project_id": "proj_eval",
        "concept": "database-choice",
        "difficulty": 2,
        "question": "Why did you use SQLite?",
        "key_points": [
            {"point": "Lightweight embedded storage", "kind": "general", "support": None},
        ],
        "common_mistakes": [],
        "seed": {"claims": []},
    })

    candidate_answer = "We used SQLite because it is lightweight and needs zero configuration."

    model_response = {
        "points": [
            {"id": 1, "status": "covered", "quote": "lightweight"},
        ],
        "misconceptions": [],
        "unverified": [
            {"quote": "needs zero configuration", "why": "Unverified zero configuration claim"},
            {"quote": "We deployed to Kubernetes clusters", "why": "Invented quote not in text"},
        ],
        "strengths": [],
        "advice": "",
    }

    fake_chat = AsyncMock(return_value=LLMReply(content=json.dumps(model_response)))

    attempt = await evaluate_answer(db, "q_unv", candidate_answer, chat_fn=fake_chat, save=False)

    # Found quote kept, unfound dropped
    assert len(attempt.evaluation.unverified) == 1
    assert attempt.evaluation.unverified[0].quote == "needs zero configuration"


# ---------------------------------------------------------------------------
# 5. Unverified items never change score or coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unverified_items_do_not_change_score():
    db = _make_db()
    db.interview_questions.insert_one({
        "question_id": "q_score",
        "project_id": "proj_eval",
        "concept": "caching",
        "difficulty": 2,
        "question": "How does caching work?",
        "key_points": [
            {"point": "In-memory cache layer", "kind": "general", "support": None},
            {"point": "TTL invalidation", "kind": "general", "support": None},
        ],
        "common_mistakes": [],
        "seed": {"claims": []},
    })

    ans = "We use an in-memory cache layer with TTL invalidation."

    res_without_unverified = {
        "points": [
            {"id": 1, "status": "covered", "quote": "in-memory cache layer"},
            {"id": 2, "status": "covered", "quote": "TTL invalidation"},
        ],
        "misconceptions": [],
        "unverified": [],
        "strengths": [],
        "advice": "",
    }

    res_with_unverified = {
        "points": [
            {"id": 1, "status": "covered", "quote": "in-memory cache layer"},
            {"id": 2, "status": "covered", "quote": "TTL invalidation"},
        ],
        "misconceptions": [],
        "unverified": [
            {"quote": "in-memory cache layer", "why": "Unverified implementation details"},
        ],
        "strengths": [],
        "advice": "",
    }

    fake_chat1 = AsyncMock(return_value=LLMReply(content=json.dumps(res_without_unverified)))
    att1 = await evaluate_answer(db, "q_score", ans, chat_fn=fake_chat1, save=False)

    fake_chat2 = AsyncMock(return_value=LLMReply(content=json.dumps(res_with_unverified)))
    att2 = await evaluate_answer(db, "q_score", ans, chat_fn=fake_chat2, save=False)

    assert att1.evaluation.score == att2.evaluation.score
    assert att1.evaluation.coverage == att2.evaluation.coverage
    assert att1.evaluation.score == 4


# ---------------------------------------------------------------------------
# 6. Both CLI paths print the "Check these" section
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_both_cli_paths_print_check_these_section():
    # Path 1: _print_evaluation_result (used by interview-answer)
    attempt = InterviewAttempt(
        question_id="q_cli",
        project_id="proj_cli",
        concept="testing",
        difficulty=2,
        answer="We use pytest and mock external APIs.",
        evaluation=Evaluation(
            points=[PointResult(id=1, point="Unit testing", kind="general", status="covered", quote="pytest")],
            misconceptions=[],
            unverified=[UnverifiedClaim(quote="mock external APIs", why="Not stated in seed lines")],
            strengths=[],
            advice="",
            coverage=1.0,
            score=4,
        ),
    )

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        _print_evaluation_result(attempt)

    output1 = buf.getvalue()
    assert "Check these against your repo:" in output1
    assert 'mock external APIs' in output1

    # Path 2: runner.py (used by interview CLI command)
    db = _make_db()
    captured_output: list[str] = []

    async def fake_gen(db, project_id, round_name, difficulty, **kwargs):
        return InterviewQuestion(
            question_id="q_run",
            project_id="proj_run",
            round="tech",
            difficulty=2,
            concept="testing",
            question="Tell me about tests",
            key_points=[],
            common_mistakes=[],
            seed={"claims": []},
        )

    async def fake_eval(db, question_id, answer, **kwargs):
        return attempt

    await run_interview(
        db,
        "proj_cli",
        n=1,
        base_difficulty=2,
        ask_fn=lambda: "We use pytest and mock external APIs.",
        generate_fn=fake_gen,
        evaluate_fn=fake_eval,
        out=captured_output.append,
    )

    combined_output = "\n".join(captured_output)
    assert "Check these against your repo:" in combined_output
    assert 'mock external APIs' in combined_output
