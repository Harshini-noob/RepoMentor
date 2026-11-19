from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import mongomock
import pytest

from app.cli import main
from app.interview.concepts import candidate_concepts
from app.interview.plan import build_plan
from app.interview.profile import _apply_attempt_to_doc, next_difficulty, weak_concepts
from app.interview.runner import run_interview
from app.interview.seeds import find_seed_for_concept
from app.interview.session import create_session
from app.interview.summary import build_summary


def _make_db():
    return mongomock.MongoClient()["repomentor"]


def _rated_profile() -> dict:
    return {
        "concepts": {
            "weak-topic": {"score": 1.0, "attempts": 2},
            "partial-topic": {"score": 2.0, "attempts": 1},
            "strong-topic": {"score": 3.5, "attempts": 2},
            "general": {"score": 0.0, "attempts": 3},
        }
    }


def _streamlit_dossier() -> dict:
    return {
        "sections": {
            "technologies": [
                {
                    "claim": "Streamlit dashboard renders charts in the frontend.",
                    "tag": "fact",
                    "evidence": ["fact:ui_app:streamlit"],
                    "details": {"commit": "old001"},
                }
            ]
        }
    }


def test_build_plan_orders_caps_and_adjusts_difficulty():
    plan = build_plan(_rated_profile(), 6, 3, available_concepts=[])

    assert [slot["concept"] for slot in plan] == [
        "weak-topic",
        "partial-topic",
        "strong-topic",
        "weak-topic",
        "partial-topic",
        "strong-topic",
    ]
    assert [slot["difficulty"] for slot in plan] == [2, 3, 4, 2, 3, 4]
    assert [slot["reason"] for slot in plan[:3]] == [
        "weak (1.0), easier",
        "partial (2.0)",
        "strong (3.5), harder",
    ]
    assert all(slot["concept"] != "general" for slot in plan)

    assert build_plan(_rated_profile(), 2, 1, available_concepts=[])[0]["difficulty"] == 1
    assert build_plan(_rated_profile(), 3, 5, available_concepts=[])[2]["difficulty"] == 5


def test_build_plan_uses_fixed_ramp_without_rated_concepts():
    plan = build_plan({"concepts": {"general": {"score": 0, "attempts": 3}}}, 3, 2)

    assert [(slot["concept"], slot["round"], slot["difficulty"], slot["reason"]) for slot in plan] == [
        (None, "history", 2, "no history yet"),
        (None, "tech", 2, "no history yet"),
        (None, "architecture", 3, "no history yet"),
    ]


def test_every_third_plan_slot_uses_an_unrated_seed_concept():
    profile = {
        "concepts": {
            "testing": {"score": 1.0, "attempts": 1},
            "authentication": {"score": 2.0, "attempts": 1},
        }
    }
    plan = build_plan(profile, 3, 2, dossier=_streamlit_dossier())

    assert plan[2]["concept"] == "ui-and-dashboards"
    assert plan[2]["reason"] == "not asked yet"
    assert plan[2]["round"] is None


@pytest.mark.asyncio
async def test_create_session_records_adaptive_or_fixed_mode():
    db = _make_db()
    db.learner_profile.insert_one({
        "project_id": "proj",
        "concepts": {"testing": {"score": 1.0, "attempts": 1}},
    })

    adaptive = await create_session(db, "proj", n=1)
    fixed = await create_session(db, "proj", n=1, adaptive=False)

    assert adaptive["plan_mode"] == "adaptive"
    assert adaptive["plan"][0]["concept"] == "testing"
    assert fixed["plan_mode"] == "fixed"
    assert fixed["plan"][0]["concept"] is None


def test_find_seed_for_concept_prefers_not_yet_asked_seed():
    first_claim = "Streamlit dashboard renders charts in the frontend."
    second_claim = "Gradio UI displays a page with charts."
    dossier = {
        "sections": {
            "technologies": [
                {
                    "claim": first_claim,
                    "tag": "fact",
                    "evidence": ["fact:ui_app:streamlit"],
                    "details": {"commit": "old001"},
                },
                {
                    "claim": second_claim,
                    "tag": "fact",
                    "evidence": ["fact:ui_app:gradio"],
                    "details": {"commit": "new002"},
                },
            ]
        }
    }

    seed_and_round = find_seed_for_concept(
        dossier,
        "ui-and-dashboards",
        [{"seed": {"commit": "old001", "claims": [{"claim": first_claim}]}}],
    )

    assert seed_and_round is not None
    seed, round_name = seed_and_round
    seed_text = " ".join(claim["claim"] for claim in seed["claims"])
    assert "ui-and-dashboards" in candidate_concepts(seed_text, k=3)
    assert seed["claims"][0]["claim"] == second_claim
    assert round_name == "tech"


def test_find_seed_for_concept_returns_none_without_match():
    dossier = {
        "sections": {
            "technologies": [
                {
                    "claim": "PostgreSQL stores the application records.",
                    "tag": "fact",
                    "evidence": ["fact:database:postgres"],
                    "details": {},
                }
            ]
        }
    }

    assert find_seed_for_concept(dossier, "ui-and-dashboards", []) is None


def test_general_is_unrated_and_listing_points_are_filtered():
    profile = {
        "concepts": {
            "general": {"score": 0.0, "attempts": 5},
            "testing": {"score": 1.0, "attempts": 1},
        }
    }
    assert weak_concepts(profile) == ["testing"]
    assert next_difficulty(profile, "general", 3) == 3

    attempt = {
        "attempt_id": "a1",
        "concept": "testing",
        "evaluation": {
            "score": 1,
            "points": [
                {
                    "point": "Explain imports from app/agent/llm.py",
                    "kind": "repo",
                    "status": "missing",
                },
                {
                    "point": "Explain the design trade-off",
                    "kind": "general",
                    "status": "missing",
                },
            ],
        },
    }
    doc = _apply_attempt_to_doc({}, attempt)
    assert [miss["point"] for miss in doc["concepts"]["testing"]["misses"]] == [
        "Explain the design trade-off"
    ]

    summary = build_summary(
        [{"question_id": "q1", "attempt_id": "a1", "status": "answered", "score": 1}],
        [{"question_id": "q1", "round": "tech", "difficulty": 2, "concept": "testing"}],
        [{"attempt_id": "a1", "evaluation": attempt["evaluation"]}],
    )
    assert [point["point"] for point in summary["revise"]] == [
        "Explain the design trade-off"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_seed", [True, False])
async def test_runner_passes_targeted_seed_or_records_fallback_note(monkeypatch, has_seed):
    import app.interview.runner as runner

    db = _make_db()
    db.learner_profile.insert_one({
        "project_id": "proj",
        "concepts": {"testing": {"score": 1.0, "attempts": 1}},
    })
    db.dossiers.insert_one({"project_id": "proj", "sections": {}})
    seed = {"claims": [{"claim": "Test claim", "tag": "fact", "evidence": []}]}

    def fake_find_seed(dossier, concept, asked):
        assert dossier is not None
        assert concept == "testing"
        assert isinstance(asked, list)
        return (seed, "tech") if has_seed else None

    monkeypatch.setattr(
        runner,
        "find_seed_for_concept",
        fake_find_seed,
    )
    generated: dict = {}

    async def fake_generate(db_arg, project_arg, **kwargs):
        assert db_arg is db
        assert project_arg == "proj"
        generated.update(kwargs)
        return SimpleNamespace(
            question_id="q1",
            question="Describe testing.",
            concept=kwargs.get("concept") or "general",
            seed=kwargs.get("seed"),
        )

    async def fake_evaluate(*args, **kwargs):
        assert args[0] is db
        assert kwargs["question_id"] == "q1"
        return SimpleNamespace(
            attempt_id="a1",
            evaluation=SimpleNamespace(
                score=4,
                unverified=[],
                points=[],
                misconceptions=[],
                advice="",
            ),
        )

    session = await run_interview(
        db,
        "proj",
        n=1,
        ask_fn=lambda: "Tests cover expected behavior.",
        out=lambda _: None,
        generate_fn=fake_generate,
        evaluate_fn=fake_evaluate,
        teach=False,
    )

    if has_seed:
        assert generated["seed"] == seed
        assert generated["concept"] == "testing"
        assert generated["round_name"] == "tech"
        assert not session["notes"]
    else:
        assert "seed" not in generated
        assert "concept" not in generated
        assert generated["round_name"] == "history"
        assert "no seed for testing, used round order" in session["notes"]


def test_interview_plan_cli_prints_without_saving_or_generating(monkeypatch, capsys):
    import app.cli as cli

    db = _make_db()
    db.dossiers.insert_one({
        "project_id": "proj",
        "sections": {
            "decisions": [
                {
                    "claim": "Chose PostgreSQL for structured records.",
                    "tag": "fact",
                    "evidence": ["commit:abc1234:app/db.py"],
                    "details": {"commit": "abc1234"},
                }
            ]
        },
    })
    generate_mock = AsyncMock()
    monkeypatch.setattr(cli, "get_db", lambda: db)
    monkeypatch.setattr(cli, "generate_question", generate_mock)
    monkeypatch.setattr(
        "sys.argv",
        ["app.cli", "interview-plan", "proj", "--questions", "1", "--difficulty", "2"],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "1. concept: -" in output
    assert "difficulty: 2" in output
    assert "Chose PostgreSQL for structured records." in output
    generate_mock.assert_not_awaited()
    assert db.interview_sessions.count_documents({}) == 0
    assert db.interview_questions.count_documents({}) == 0
