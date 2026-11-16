"""Interview answer evaluator.

Grades a candidate's answer against an InterviewQuestion rubric,
checking quotes against the answer text and computing coverage and score.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from app.agent.llm import chat
from app.agent.state import LLMReply
from app.config import get_settings
from app.interview.schema import (
    Evaluation,
    InterviewAttempt,
    Misconception,
    PointResult,
)
from app.interview.scoring import score_evaluation

logger = logging.getLogger("repomentor.interview.evaluate")

_PROMPT_PATH = Path(__file__).parent / "prompts" / "evaluate.md"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


class InterviewEvaluationError(RuntimeError):
    """Raised when evaluation fails."""


def _parse_json(text: str) -> tuple[dict, bool]:
    """Strip fences, extract {...}, attempt json.loads."""
    text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n?```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}, False
    try:
        return json.loads(text[start : end + 1]), True
    except (json.JSONDecodeError, ValueError):
        return {}, False


def _normalise(text: str) -> str:
    """Normalise text: lowercase, collapse whitespace, strip quotes and trailing punctuation."""
    if not text:
        return ""
    t = text.lower()
    t = " ".join(t.split())
    t = t.strip("\"'“”‘’`")
    t = t.rstrip(".,;:!?")
    t = t.strip("\"'“”‘’`")
    return t


def _quote_in_answer(quote: str | None, norm_answer: str) -> bool:
    """Check if quote is a valid substring in the normalised answer.

    A quote longer than 25 words counts as not found.
    """
    if not quote or not isinstance(quote, str):
        return False
    words = quote.strip().split()
    if len(words) == 0 or len(words) > 25:
        return False
    norm_quote = _normalise(quote)
    if not norm_quote:
        return False
    if norm_quote in norm_answer:
        return True
    # Strip embedded quotes for resilient matching
    clean_q = re.sub(r"[\"\'“”‘’`]", "", norm_quote)
    clean_a = re.sub(r"[\"\'“”‘’`]", "", norm_answer)
    if clean_q and clean_q in clean_a:
        return True
    return False


async def evaluate_answer(
    db: Any,
    question_id: str,
    answer: str,
    chat_fn: Callable[..., Any] | None = None,
    save: bool = True,
) -> InterviewAttempt:
    """Grade a candidate's answer against the given question."""
    # 1. Load the question
    find_res = db.interview_questions.find_one({"question_id": question_id}, {"_id": 0})
    if inspect.isawaitable(find_res):
        doc = await find_res
    else:
        doc = find_res

    if not doc:
        raise InterviewEvaluationError(f"Question not found: {question_id}")

    key_points = doc.get("key_points", [])
    seed = doc.get("seed", {})
    seed_claims = seed.get("claims", [])
    common_mistakes = doc.get("common_mistakes", [])

    # 2. Check for short answer (< 5 words)
    words = answer.strip().split()
    if len(words) < 5:
        points = [
            PointResult(
                id=i + 1,
                point=kp.get("point", ""),
                kind=kp.get("kind", "general"),
                status="missing",
                quote=None,
                note=None,
            )
            for i, kp in enumerate(key_points)
        ]
        ev = Evaluation(
            points=points,
            misconceptions=[],
            strengths=[],
            advice="Answer in a few full sentences.",
            coverage=0.0,
            score=0,
            notes=[],
        )
        attempt = InterviewAttempt(
            question_id=question_id,
            project_id=doc.get("project_id", ""),
            concept=doc.get("concept", ""),
            difficulty=doc.get("difficulty", 1),
            answer=answer,
            evaluation=ev,
            model=None,
            tokens=0,
        )
        if save:
            insert_coro = db.interview_attempts.insert_one(attempt.model_dump(mode="json"))
            if inspect.isawaitable(insert_coro):
                await insert_coro
        return attempt

    # 3. Truncate answer longer than 3000 characters for model call
    eval_notes: list[str] = []
    if len(answer) > 3000:
        model_answer = answer[:3000]
        eval_notes.append("answer truncated to 3000 characters for evaluation")
    else:
        model_answer = answer

    # 4. Build user message
    rubric_lines = "\n".join(
        f"{i + 1}. [{kp.get('kind', 'general')}] {kp.get('point', '')}"
        for i, kp in enumerate(key_points)
    )
    mistakes_lines = "\n".join(f"- {m}" for m in common_mistakes) if common_mistakes else "None"
    seed_lines_text = "\n".join(
        f"{i + 1}. [{c.get('tag', '').capitalize()}] {c.get('claim', '')} (evidence: {', '.join(c.get('evidence', [])[:4])})"
        for i, c in enumerate(seed_claims)
    )

    user_msg = (
        f"Question: {doc.get('question', '')}\n"
        f"Difficulty: {doc.get('difficulty', 1)}\n\n"
        f"Rubric:\n{rubric_lines}\n\n"
        f"Common mistakes:\n{mistakes_lines}\n\n"
        f"Seed facts:\n{seed_lines_text}\n\n"
        f"Candidate's answer:\nANSWER START\n{model_answer}\nANSWER END"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    _chat = chat_fn if chat_fn is not None else chat
    reply: LLMReply = await _chat(messages, tools=None)

    data, ok = _parse_json(reply.content or "")
    if not ok:
        retry_messages = list(messages)
        retry_messages.append({"role": "assistant", "content": reply.content or ""})
        retry_messages.append({"role": "user", "content": "Return only the JSON object."})
        reply = await _chat(retry_messages, tools=None)
        data, ok = _parse_json(reply.content or "")
        if not ok:
            raise InterviewEvaluationError("Failed to parse evaluation response from model as JSON after retry")

    # 5. Validate model output
    norm_answer = _normalise(answer)

    # Every rubric point 1..N appears exactly once
    model_points_by_id: dict[int, dict] = {}
    for p in data.get("points", []):
        if isinstance(p, dict) and "id" in p and isinstance(p["id"], int):
            pid = p["id"]
            if pid not in model_points_by_id:
                model_points_by_id[pid] = p

    point_results: list[PointResult] = []
    for idx, kp in enumerate(key_points, start=1):
        kp_point = kp.get("point", "")
        kp_kind = kp.get("kind", "general")
        mp = model_points_by_id.get(idx)

        if mp is None:
            point_results.append(
                PointResult(
                    id=idx,
                    point=kp_point,
                    kind=kp_kind,
                    status="missing",
                    quote=None,
                    note=None,
                )
            )
        else:
            raw_status = mp.get("status")
            raw_quote = mp.get("quote")
            if raw_status in ("covered", "partial"):
                if _quote_in_answer(raw_quote, norm_answer):
                    point_results.append(
                        PointResult(
                            id=idx,
                            point=kp_point,
                            kind=kp_kind,
                            status=raw_status,
                            quote=raw_quote,
                            note=None,
                        )
                    )
                else:
                    point_results.append(
                        PointResult(
                            id=idx,
                            point=kp_point,
                            kind=kp_kind,
                            status="missing",
                            quote=None,
                            note="quote not found in answer",
                        )
                    )
            else:
                point_results.append(
                    PointResult(
                        id=idx,
                        point=kp_point,
                        kind=kp_kind,
                        status="missing",
                        quote=None,
                        note=None,
                    )
                )

    # Validate misconceptions
    misconceptions: list[Misconception] = []
    for m in data.get("misconceptions", []):
        if not isinstance(m, dict):
            continue
        m_quote = m.get("quote")
        if not _quote_in_answer(m_quote, norm_answer):
            continue  # unfound quote is dropped

        why = str(m.get("why", "") or "")
        seed_line = m.get("seed_line")
        if seed_line is not None:
            if not isinstance(seed_line, int) or seed_line < 1 or seed_line > len(seed_claims):
                seed_line = None

        contradicts_repo = seed_line is not None
        misconceptions.append(
            Misconception(
                quote=m_quote,
                why=why,
                seed_line=seed_line,
                contradicts_repo=contradicts_repo,
            )
        )

    strengths = [s for s in data.get("strengths", []) if isinstance(s, str)][:2]
    advice = str(data.get("advice", "") or "")

    # 6. Scoring via scoring.py (never take score from model)
    coverage, score = score_evaluation(point_results, misconceptions)

    evaluation = Evaluation(
        points=point_results,
        misconceptions=misconceptions,
        strengths=strengths,
        advice=advice,
        coverage=coverage,
        score=score,
        notes=eval_notes,
    )

    # 7. Model name and tokens
    usage = getattr(reply, "usage", {}) or {}
    tokens = usage.get("total_tokens", 0) or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
    model_name = getattr(reply, "model", None) or getattr(get_settings(), "llm_model", None)

    attempt = InterviewAttempt(
        question_id=question_id,
        project_id=doc.get("project_id", ""),
        concept=doc.get("concept", ""),
        difficulty=doc.get("difficulty", 1),
        answer=answer,
        evaluation=evaluation,
        model=model_name,
        tokens=tokens,
    )

    # 8. Persist attempt
    if save:
        insert_coro = db.interview_attempts.insert_one(attempt.model_dump(mode="json"))
        if inspect.isawaitable(insert_coro):
            await insert_coro

    return attempt
