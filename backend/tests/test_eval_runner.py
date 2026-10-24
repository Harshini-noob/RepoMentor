import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_DIR = Path(__file__).resolve().parents[2]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from app.agent.state import AgentRun, TraceStep
from evals import run_evals


def test_evaluate_result_computes_metrics_and_keeps_trace() -> None:
    question = {
        "id": "test",
        "category": "test",
        "question": "Question?",
        "expected_tools": ["search_code", "read_file"],
        "expected_evidence": ["file:src/main.py"],
        "answer_must_contain_any": ["verified"],
    }
    run = AgentRun(
        goal="Question?",
        project_id="test",
        stop_reason="answered",
        final_answer="[Fact] Verified at file:src/main.py#L1-2.",
        total_tokens=27,
        steps=[
            TraceStep(
                step=1,
                tool="search_code",
                args={"query": "main"},
                evidence_ids=["file:src/main.py#L1-2"],
            ),
            TraceStep(
                step=2,
                tool="search_code",
                args={"query": "main"},
            ),
            TraceStep(
                step=3,
                tool="unexpected_tool",
                args={},
            ),
        ],
    )

    result = run_evals.evaluate_result(question, run, 1.25, "test-model")

    assert result["completed"] is True
    assert result["steps"] == 3
    assert result["total_tokens"] == 27
    assert result["tool_recall"] == 0.5
    assert result["tool_ok"] is True
    assert result["unnecessary_calls"] == 2
    assert result["evidence_hit"] is True
    assert result["unverified_citations"] == 0
    assert result["tagged"] is True
    assert result["content_ok"] is True
    assert result["no_bad_claim"] is True
    assert result["false_abstention"] is False
    assert len(result["traces"]) == 3


def test_content_check_normalises_dashes_and_answer_markers() -> None:
    run = AgentRun(
        goal="Question?",
        project_id="test",
        final_answer="[Fact] The date is **2026\u201107\u201127**.",
    )
    result = run_evals.evaluate_result(
        {
            "id": "date",
            "question": "Question?",
            "expected_tools": [],
            "expected_evidence": [],
            "answer_must_contain_any": ["2026-07-27"],
        },
        run,
        0.1,
        "test-model",
    )

    assert result["content_ok"] is True


def test_no_bad_claim_and_false_abstention_metrics() -> None:
    question = {
        "id": "claims",
        "question": "Question?",
        "expected_tools": [],
        "expected_evidence": ["commit:"],
        "answer_must_not_match": [
            r"pr:\d+",
            r"pull request #\d+",
            r"PR #\d+",
        ],
    }
    bad_claim = AgentRun(
        goal="Question?",
        project_id="test",
        final_answer="[Fact] PR #12 introduced this.",
    )
    abstention = AgentRun(
        goal="Question?",
        project_id="test",
        final_answer="[Uncertain] I could not verify this.",
    )

    bad_claim_result = run_evals.evaluate_result(
        question, bad_claim, 0.1, "test-model"
    )
    abstention_result = run_evals.evaluate_result(
        question, abstention, 0.1, "test-model"
    )

    assert bad_claim_result["no_bad_claim"] is False
    assert bad_claim_result["false_abstention"] is False
    assert abstention_result["no_bad_claim"] is True
    assert abstention_result["false_abstention"] is True


def test_evidence_hit_is_substring_and_empty_expectations_are_not_applicable() -> None:
    base_question = {
        "id": "evidence",
        "question": "Question?",
        "expected_tools": ["search_code"],
        "expected_evidence": ["file:auth.py", "dep:"],
    }
    run = AgentRun(
        goal="Question?",
        project_id="test",
        stop_reason="answered",
        final_answer="[Fact] auth code found.",
        steps=[
            TraceStep(
                step=1,
                tool="search_code",
                evidence_ids=["FILE:AUTH.PY#L3-3", "dep:groq"],
            )
        ],
    )

    result = run_evals.evaluate_result(base_question, run, 0.1, "test-model")
    empty_result = run_evals.evaluate_result(
        {**base_question, "expected_evidence": []},
        run,
        0.1,
        "test-model",
    )
    assert result["evidence_hit"] is True
    assert empty_result["evidence_hit"] is None


def test_tool_ok_uses_acceptable_tools_or_allows_empty_lists() -> None:
    run = AgentRun(
        goal="Question?",
        project_id="test",
        final_answer="Done.",
        steps=[TraceStep(step=1, tool="get_dependencies")],
    )
    common = {"id": "tool-test", "question": "Question?", "expected_evidence": []}

    accepted = run_evals.evaluate_result(
        {
            **common,
            "expected_tools": ["get_overview"],
            "acceptable_tools": ["get_dependencies"],
        },
        run,
        0.1,
        "test-model",
    )
    no_tools_expected = run_evals.evaluate_result(
        {**common, "expected_tools": []},
        run,
        0.1,
        "test-model",
    )
    assert accepted["tool_ok"] is True
    assert accepted["tool_recall"] == 0
    assert no_tools_expected["tool_ok"] is True


def test_summary_excludes_not_applicable_evidence_hits() -> None:
    summary = run_evals._make_summary(
        [
            {"completed": True, "steps": 2, "total_tokens": 10, "seconds": 1.0,
             "tool_recall": 1.0, "unnecessary_calls": 0, "unverified_citations": 0,
             "tagged": True, "content_ok": True, "no_bad_claim": True,
             "false_abstention": False, "evidence_hit": True, "tool_ok": True},
            {"completed": False, "steps": 4, "total_tokens": 30, "seconds": 3.0,
             "tool_recall": 0.0, "unnecessary_calls": 2, "unverified_citations": 1,
             "tagged": False, "content_ok": False, "no_bad_claim": False,
             "false_abstention": True, "evidence_hit": None, "tool_ok": False},
        ],
        "test-model",
    )

    assert summary["averages"]["evidence_hit"] == 1.0
    assert summary["averages"]["tool_ok"] == 0.5
    assert summary["averages"]["false_abstention"] == 0.5
    assert summary["averages"]["total_tokens"] == 20
    assert summary["averages"]["seconds"] == 2


@pytest.mark.asyncio
async def test_run_evaluations_continues_after_question_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    questions = [
        {
            "id": "first",
            "question": "Fails",
            "expected_tools": [],
            "expected_evidence": [],
            "answer_must_contain_any": [],
        },
        {
            "id": "second",
            "question": "Succeeds",
            "expected_tools": [],
            "expected_evidence": [],
            "answer_must_contain_any": [],
        },
    ]
    monkeypatch.setattr(run_evals, "load_questions", lambda: questions)
    monkeypatch.setattr(
        run_evals,
        "get_settings",
        lambda: SimpleNamespace(llm_model="test-model"),
    )
    calls = 0

    async def fake_run_agent(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("test failure")
        return AgentRun(
            goal=kwargs["goal"],
            project_id=kwargs["project_id"],
            stop_reason="answered",
            final_answer="Done.",
        )

    monkeypatch.setattr(run_evals, "run_agent", fake_run_agent)
    monkeypatch.setattr(run_evals.asyncio, "sleep", _no_sleep)

    payload = await run_evals.run_evaluations(
        project="test-project",
        sleep_seconds=0,
        out_dir=tmp_path,
    )

    assert [item["id"] for item in payload["results"]] == ["first", "second"]
    assert payload["results"][0]["completed"] is False
    assert payload["results"][1]["completed"] is True
    result_files = list(tmp_path.glob("*.json"))
    assert len(result_files) == 1
    saved = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert saved["model"] == "test-model"
    assert len(saved["results"]) == 2


async def _no_sleep(seconds: float) -> None:
    return None
