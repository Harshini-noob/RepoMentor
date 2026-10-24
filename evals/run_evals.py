"""Run the repository's agent evaluation questions and save detailed results."""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.agent.evidence import find_unsupported_citations
from app.agent.guards import RepeatedCallGuard
from app.agent.loop import run_agent
from app.agent.state import AgentRun
from app.config import get_settings

QUESTIONS_PATH = Path(__file__).resolve().with_name("questions.json")
DEFAULT_PROJECT = "proj_harshini_noob_llm_cost_copilot"
TAG_RE = re.compile(r"\[(?:Fact|Inferred|Uncertain)\]", re.IGNORECASE)
CLAIM_TAG_RE = re.compile(r"\[(Fact|Inferred|Uncertain)\]", re.IGNORECASE)
ANSWER_MARKERS = str.maketrans("", "", "*`_")
ANSWER_DASHES = str.maketrans({
    **{ord(char): "-" for char in "\u2010\u2011\u2012\u2013\u2014\u2015"},
    ord("\u2212"): "-",
})


def _normalise_answer_text(text: str) -> str:
    return " ".join(
        text.translate(ANSWER_DASHES)
        .translate(ANSWER_MARKERS)
        .split()
    ).lower()


def load_questions() -> list[dict[str, Any]]:
    with QUESTIONS_PATH.open(encoding="utf-8") as questions_file:
        questions = json.load(questions_file)
    if not isinstance(questions, list):
        raise ValueError(f"Expected a JSON array in {QUESTIONS_PATH}")
    return questions


def evaluate_result(
    question: dict[str, Any],
    run: AgentRun,
    seconds: float,
    model: str,
) -> dict[str, Any]:
    expected_tools = set(question.get("expected_tools", []))
    acceptable_tools = set(question.get("acceptable_tools", []))
    called_tools: set[str] = set()
    repeat_guard = RepeatedCallGuard()
    repeated_calls = 0
    unexpected_calls = 0
    evidence_ids: set[str] = set()

    traces = [step.model_dump() for step in run.steps]
    for step in run.steps:
        evidence_ids.update(step.evidence_ids)
        if not step.tool or step.tool in {"policy_nudge", "error"}:
            continue
        if step.tool not in expected_tools:
            unexpected_calls += 1
        if repeat_guard.is_repeated(step.tool, step.args):
            repeated_calls += 1
        called_tools.add(step.tool)

    expected_evidence = question.get("expected_evidence", [])
    answer = run.final_answer or ""
    evidence_hit = (
        any(
            expected.casefold() in evidence_id.casefold()
            for expected in expected_evidence
            for evidence_id in evidence_ids
        )
        or any(
            expected.casefold() in answer.casefold()
            for expected in expected_evidence
        )
        if expected_evidence
        else None
    )
    must_contain = question.get("answer_must_contain_any")
    normalised_answer = _normalise_answer_text(answer)
    content_ok = (
        any(
            _normalise_answer_text(term) in normalised_answer
            for term in must_contain
        )
        if must_contain
        else True
    )
    answer_must_not_match = question.get("answer_must_not_match", [])
    no_bad_claim = not any(
        re.search(pattern, answer, re.IGNORECASE)
        for pattern in answer_must_not_match
    )
    tags = [tag.casefold() for tag in CLAIM_TAG_RE.findall(answer)]
    false_abstention = bool(expected_evidence) and bool(tags) and all(
        tag == "uncertain" for tag in tags
    )

    return {
        "id": question["id"],
        "category": question.get("category"),
        "question": question["question"],
        "model": model,
        "completed": run.stop_reason == "answered",
        "stop_reason": run.stop_reason,
        "steps": len(run.steps),
        "total_tokens": run.total_tokens,
        "seconds": seconds,
        "tool_recall": (
            len(expected_tools & called_tools) / len(expected_tools)
            if expected_tools
            else 1.0
        ),
        "tool_ok": (
            not (expected_tools or acceptable_tools)
            or bool((expected_tools | acceptable_tools) & called_tools)
        ),
        "unnecessary_calls": repeated_calls + unexpected_calls,
        "evidence_hit": evidence_hit,
        "unverified_citations": len(find_unsupported_citations(answer, evidence_ids)),
        "tagged": bool(TAG_RE.search(answer)),
        "content_ok": content_ok,
        "no_bad_claim": no_bad_claim,
        "false_abstention": false_abstention,
        "answer": answer,
        "traces": traces,
    }


async def run_evaluations(
    project: str = DEFAULT_PROJECT,
    only: set[str] | None = None,
    limit: int | None = None,
    sleep_seconds: float = 20,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    questions = load_questions()
    if only is not None:
        questions = [question for question in questions if question["id"] in only]
    if limit is not None:
        questions = questions[:limit]

    model = get_settings().llm_model
    results: list[dict[str, Any]] = []
    for index, question in enumerate(questions):
        started = time.perf_counter()
        try:
            run = await run_agent(
                goal=question["question"],
                project_id=project,
                max_steps=10,
            )
            elapsed = time.perf_counter() - started
            results.append(evaluate_result(question, run, elapsed, model))
        except Exception as exc:
            elapsed = time.perf_counter() - started
            results.append({
                "id": question.get("id", f"question-{index + 1}"),
                "category": question.get("category"),
                "question": question.get("question"),
                "model": model,
                "completed": False,
                "stop_reason": "error",
                "steps": 0,
                "total_tokens": 0,
                "seconds": elapsed,
                "tool_recall": 0.0 if question.get("expected_tools") else 1.0,
                "unnecessary_calls": 0,
                "evidence_hit": None,
                "tool_ok": not (
                    question.get("expected_tools") or question.get("acceptable_tools")
                ),
                "unverified_citations": 0,
                "tagged": False,
                "content_ok": not question.get("answer_must_contain_any"),
                "no_bad_claim": True,
                "false_abstention": False,
                "answer": f"Evaluation failed: {type(exc).__name__}: {exc}",
                "traces": [],
            })
        if index + 1 < len(questions) and sleep_seconds > 0:
            await asyncio.sleep(sleep_seconds)

    summary = _make_summary(results, model)
    payload = {
        "project": project,
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "results": results,
    }
    result_dir = out_dir if out_dir is not None else REPO_DIR / "evals" / "results"
    if not result_dir.is_absolute():
        result_dir = REPO_DIR / result_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = result_dir / f"{timestamp}.json"
    with result_path.open("w", encoding="utf-8") as result_file:
        json.dump(payload, result_file, indent=2, ensure_ascii=False)
        result_file.write("\n")

    _print_results(results, summary, model)
    print(f"Saved results: {result_path}")
    return payload


def _make_summary(results: list[dict[str, Any]], model: str) -> dict[str, Any]:
    metrics = (
        "completed",
        "steps",
        "total_tokens",
        "seconds",
        "tool_recall",
        "unnecessary_calls",
        "unverified_citations",
        "tagged",
        "content_ok",
        "no_bad_claim",
        "false_abstention",
    )
    count = len(results)
    averages = {
        metric: (
            sum(float(result[metric]) for result in results) / count
            if count
            else 0.0
        )
        for metric in metrics
    }
    evidence_results = [
        result["evidence_hit"]
        for result in results
        if result["evidence_hit"] is not None
    ]
    averages["evidence_hit"] = (
        sum(bool(hit) for hit in evidence_results) / len(evidence_results)
        if evidence_results
        else None
    )
    averages["tool_ok"] = (
        sum(bool(result["tool_ok"]) for result in results) / count
        if count
        else 0.0
    )
    return {"model": model, "count": count, "averages": averages}


def _print_results(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    model: str,
) -> None:
    columns = (
        "id",
        "model",
        "completed",
        "steps",
        "total_tokens",
        "seconds",
        "tool_recall",
        "tool_ok",
        "unnecessary_calls",
        "evidence_hit",
        "unverified_citations",
        "tagged",
        "content_ok",
        "no_bad_claim",
        "false_abstention",
    )
    print(" | ".join(columns))
    for result in results:
        print(" | ".join(
            "n/a" if column == "evidence_hit" and result[column] is None
            else f"{result[column]:.2f}" if column in {"seconds", "tool_recall"}
            else str(result[column])
            for column in columns
        ))
    averages = summary["averages"]
    evidence_rate = averages["evidence_hit"]
    evidence_text = (
        f"{evidence_rate:.2%}" if evidence_rate is not None else "n/a"
    )
    print(
        "SUMMARY | "
        f"model={model} | "
        f"completion_rate={averages['completed']:.2%} | "
        f"tool_ok_rate={averages['tool_ok']:.2%} | "
        f"evidence_hit_rate={evidence_text} | "
        f"content_ok_rate={averages['content_ok']:.2%} | "
        f"no_bad_claim_rate={averages['no_bad_claim']:.2%} | "
        f"false_abstention_rate={averages['false_abstention']:.2%} | "
        f"average_tokens={averages['total_tokens']:.2f} | "
        f"average_seconds={averages['seconds']:.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--only", help="Comma-separated question IDs to run.")
    parser.add_argument("--limit", type=int, help="Maximum number of questions to run.")
    parser.add_argument("--sleep", type=float, default=20, help="Delay between questions.")
    parser.add_argument("--out", default="evals/results", help="Result directory.")
    args = parser.parse_args()

    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.sleep < 0:
        parser.error("--sleep must be non-negative")
    only = (
        {question_id.strip() for question_id in args.only.split(",") if question_id.strip()}
        if args.only is not None
        else None
    )
    asyncio.run(
        run_evaluations(
            project=args.project,
            only=only,
            limit=args.limit,
            sleep_seconds=args.sleep,
            out_dir=Path(args.out),
        )
    )


if __name__ == "__main__":
    main()
