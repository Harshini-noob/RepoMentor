"""Grounded teaching lessons for missed interview concepts."""

from __future__ import annotations

import inspect
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.agent.evidence import (
    ANSWER_CHAR_MAP,
    CITE_RE,
    find_unsupported_citations,
)
from app.agent.llm import chat
from app.agent.state import LLMReply
from app.config import get_settings

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "teach.md").read_text(
    encoding="utf-8"
)
_IMPORT_USAGE_RE = re.compile(r"\b(imports?|imported|used in|used across)\b", re.I)
_FILENAME_RE = re.compile(r"\b[\w/.-]+\.(?:py|js|ts|md|toml|ya?ml|json)\b", re.I)
_FACT_LINE_RE = re.compile(r"^\s*\[Fact\]", re.I)
_LABELS = ("Good:", "Idea:", "In your project:", "Say it like this:")


class LessonGenerationError(RuntimeError):
    """Raised when a saved attempt cannot be used to generate a lesson."""


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _find_newest_attempt(db: Any, project_id: str) -> dict | None:
    cursor = db.interview_attempts.find({"project_id": project_id}).sort(
        "created_at", -1
    )
    if hasattr(cursor, "to_list"):
        docs = await cursor.to_list(1)
    else:
        cursor = await _await_if_needed(cursor)
        docs = list(cursor)
    return docs[0] if docs else None


def _citation_state(text: str, seed_ids: set[str]) -> tuple[list[str], list[str]]:
    normalised = text.translate(ANSWER_CHAR_MAP)
    unsupported = find_unsupported_citations(normalised, seed_ids)
    invalid_ids = {
        item.removeprefix("malformed citation: ") for item in unsupported
    }
    uncited_fact_lines = [
        line
        for line in normalised.splitlines()
        if _FACT_LINE_RE.match(line)
        and not (set(CITE_RE.findall(line)) & seed_ids)
    ]
    return sorted(invalid_ids), uncited_fact_lines


def _clean_citations(
    text: str, seed_ids: set[str], notes: list[str]
) -> str:
    normalised = text.translate(ANSWER_CHAR_MAP)
    unsupported, _ = _citation_state(normalised, seed_ids)
    for evidence_id in unsupported:
        normalised = normalised.replace(evidence_id, "")
        notes.append(f"Removed unsupported evidence ID: {evidence_id}")

    updated_lines = []
    for line in normalised.splitlines():
        if (
            _FACT_LINE_RE.match(line)
            and not (set(CITE_RE.findall(line)) & seed_ids)
        ):
            line = _FACT_LINE_RE.sub("[Uncertain]", line, count=1)
            notes.append("Changed a [Fact] line without a valid seed ID to [Uncertain].")
        updated_lines.append(line)
    return "\n".join(updated_lines)


async def generate_lesson(
    db: Any,
    attempt_id: str | None = None,
    project_id: str | None = None,
    chat_fn: Callable[..., Any] | None = None,
    save: bool = True,
) -> dict:
    """Generate a lesson for a saved interview attempt."""
    if attempt_id is None:
        if not project_id:
            raise LessonGenerationError(
                "A project_id is required when no attempt_id is provided."
            )
        attempt = await _find_newest_attempt(db, project_id)
        if attempt is None:
            raise LessonGenerationError(
                f"No saved interview attempts found for project '{project_id}'."
            )
    else:
        query: dict[str, str] = {"attempt_id": attempt_id}
        if project_id is not None:
            query["project_id"] = project_id
        attempt = await _await_if_needed(db.interview_attempts.find_one(query, {"_id": 0}))
        if attempt is None:
            raise LessonGenerationError(f"Interview attempt not found: {attempt_id}")

    attempt_id = attempt["attempt_id"]
    question = await _await_if_needed(
        db.interview_questions.find_one(
            {"question_id": attempt.get("question_id")}, {"_id": 0}
        )
    )
    if question is None:
        raise LessonGenerationError(
            f"Question not found for interview attempt '{attempt_id}'."
        )

    evaluation = attempt.get("evaluation") or {}
    evaluation_points = evaluation.get("points", [])
    taught_points = [
        point
        for point in evaluation_points
        if point.get("status") in ("missing", "partial")
        and not (
            _IMPORT_USAGE_RE.search(point.get("point", ""))
            and _FILENAME_RE.search(point.get("point", ""))
        )
    ]

    seed_claims = (question.get("seed") or {}).get("claims", [])
    seed_ids = {
        evidence_id
        for claim in seed_claims
        for evidence_id in (claim.get("evidence") or [])
        if isinstance(evidence_id, str)
    }
    taught_lines = (
        "\n".join(
            f"- [{point.get('kind', 'general')}] {point.get('point', '')}"
            for point in taught_points
        )
        or "No specific rubric point; teach the idea behind the question."
    )
    seed_lines = "\n".join(
        f"{index}. [{str(claim.get('tag', '')).capitalize()}] "
        f"{claim.get('claim', '')} (evidence: {', '.join(claim.get('evidence') or [])})"
        for index, claim in enumerate(seed_claims, start=1)
    ) or "None"
    difficulty = attempt.get("difficulty", question.get("difficulty", 1))
    concept = attempt.get("concept", question.get("concept", ""))
    user_message = (
        f"Question: {question.get('question', '')}\n"
        f"Difficulty: {difficulty}\n"
        f"Concept: {concept}\n\n"
        f"Candidate's answer:\nANSWER START\n{attempt.get('answer', '')}\nANSWER END\n\n"
        f"Taught points:\n{taught_lines}\n\n"
        f"Seed facts:\n{seed_lines}"
    )
    base_messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    _chat = chat_fn if chat_fn is not None else chat
    reply: LLMReply = await _chat(base_messages, tools=None)
    text = (reply.content or "").translate(ANSWER_CHAR_MAP)
    notes: list[str] = []

    if not all(label in text for label in _LABELS):
        messages = [
            *base_messages,
            {"role": "assistant", "content": reply.content or ""},
            {"role": "user", "content": "Use exactly the four labelled parts."},
        ]
        reply = await _chat(messages, tools=None)
        text = (reply.content or "").translate(ANSWER_CHAR_MAP)

    unsupported, uncited_facts = _citation_state(text, seed_ids)
    if unsupported or uncited_facts:
        ids_to_report = ", ".join(unsupported) if unsupported else (
            "[Fact] line without a valid seed evidence ID"
        )
        messages = [
            *base_messages,
            {"role": "assistant", "content": reply.content or ""},
            {
                "role": "user",
                "content": f"These ids are not in the seed: {ids_to_report}",
            },
        ]
        reply = await _chat(messages, tools=None)
        text = (reply.content or "").translate(ANSWER_CHAR_MAP)

        unsupported, uncited_facts = _citation_state(text, seed_ids)
        if unsupported or uncited_facts:
            text = _clean_citations(text, seed_ids, notes)

    usage = getattr(reply, "usage", {}) or {}
    tokens = usage.get("total_tokens", 0) or (
        usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    )
    model = getattr(reply, "model", None) or getattr(
        get_settings(), "llm_model", None
    )
    lesson = {
        "lesson_id": str(uuid.uuid4()),
        "attempt_id": attempt_id,
        "question_id": question["question_id"],
        "project_id": attempt.get("project_id", project_id),
        "concept": concept,
        "text": text,
        "notes": notes,
        "model": model,
        "tokens": tokens,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if save:
        result = db.interview_lessons.insert_one(lesson)
        await _await_if_needed(result)
    return lesson
