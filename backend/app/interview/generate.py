"""
Grounded interview question generator.

Loads dossier claims, picks a seed, calls the LLM once, validates the
response, optionally retries, and persists the result.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from app.agent.llm import chat
from app.agent.state import LLMReply

from .concepts import CONCEPTS, DIFFICULTY, ROUNDS
from .schema import InterviewQuestion, KeyPoint

logger = logging.getLogger("repomentor.interview")

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "generate.md").read_text(
    encoding="utf-8"
)

# Tokens that look like source-code filenames
_FILENAME_RE = re.compile(r"\b[\w/.\-]+\.(?:py|js|ts|md|toml|ya?ml|json)\b")


class InterviewGenerationError(RuntimeError):
    """Raised when the LLM fails to produce a valid interview question."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_question(
    db: Any,
    project_id: str,
    round_name: str,
    difficulty: int,
    commit: str | None = None,
    chat_fn: Callable[..., Any] | None = None,
    save: bool = True,
) -> InterviewQuestion:
    """
    Generate one grounded interview question.

    Parameters
    ----------
    db:         AsyncIOMotorDatabase (or mongomock equivalent).
    project_id: Repository/project identifier.
    round_name: "history", "tech", or "architecture".
    difficulty: 1–5 (maps to DIFFICULTY).
    commit:     Force a specific commit sha7 (history round only).
    chat_fn:    Injectable LLM callable for testing; defaults to app.agent.llm.chat.
    save:       If False, skip DB insertion (dry-run).

    Returns
    -------
    InterviewQuestion Pydantic model (not yet persisted when save=False).
    """
    from .seeds import pick_seed

    _chat = chat_fn if chat_fn is not None else chat

    # 1. Load dossier (supports both motor async and mongomock sync cursors)
    raw = db.dossiers.find_one({"project_id": project_id}, {"_id": 0})
    import inspect as _inspect
    if _inspect.isawaitable(raw):
        dossier = await raw
    else:
        dossier = raw
    if not dossier:
        raise InterviewGenerationError(
            f"No dossier found for project '{project_id}'. "
            "run the dossier builder first."
        )

    # 2. Load earlier questions for this project (for deduplication in pick_seed)
    asked: list[dict] = []
    cursor = db.interview_questions.find({"project_id": project_id}, {"_id": 0})
    # Support both async (motor) and sync (mongomock) cursors
    if hasattr(cursor, "__aiter__"):
        async for doc in cursor:
            asked.append(doc)
    else:
        for doc in cursor:
            asked.append(doc)

    # 3. Pick seed
    seed = pick_seed(dossier, round_name, asked, commit=commit)

    # 4. Build user message
    difficulty_label = DIFFICULTY.get(difficulty, str(difficulty))
    concepts_str = ", ".join(CONCEPTS)
    seed_lines = "\n".join(
        f"[{c['tag'].capitalize()}] {c['claim']} (evidence: {', '.join(c['evidence'][:4])})"
        for c in seed.get("claims", [])
    )
    user_msg = (
        f"Round: {round_name}\n"
        f"Difficulty: {difficulty} — {difficulty_label}\n"
        f"Allowed concepts: {concepts_str}\n\n"
        f"Seed facts:\n{seed_lines}"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    # 5. First LLM call
    reply: LLMReply = await _chat(messages, tools=None)
    data, parse_ok = _parse_json(reply.content or "")

    if not parse_ok:
        # Retry once
        logger.warning("First LLM reply was not valid JSON — retrying once.")
        messages.append({"role": "assistant", "content": reply.content or ""})
        messages.append({"role": "user", "content": "Return only the JSON object."})
        reply2: LLMReply = await _chat(messages, tools=None)
        data, parse_ok = _parse_json(reply2.content or "")
        if not parse_ok:
            raise InterviewGenerationError(
                "LLM did not return valid JSON after retry."
            )
        reply = reply2

    # 6. Structural validation
    _validate_structure(data)

    # 7. Concept coercion
    if data.get("concept") not in CONCEPTS:
        logger.warning("Unknown concept %r — coercing to 'general'.", data.get("concept"))
        data["concept"] = "general"

    # 8. Name-grounding validation
    seed_text = " ".join(
        c.get("claim", "") + " " + " ".join(c.get("evidence") or [])
        for c in seed.get("claims", [])
    ).lower()

    bad_names = _find_ungrounded_names(data, seed_text)
    if bad_names:
        # Retry once with guidance
        logger.warning("Ungrounded names found: %s — retrying with guidance.", bad_names)
        guidance = (
            f"These names are not in the seed facts: {', '.join(sorted(bad_names))}. "
            "Rewrite without them."
        )
        messages_r = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": reply.content or ""},
            {"role": "user", "content": guidance},
        ]
        reply3: LLMReply = await _chat(messages_r, tools=None)
        data2, parse_ok2 = _parse_json(reply3.content or "")
        if parse_ok2:
            _validate_structure(data2)
            if data2.get("concept") not in CONCEPTS:
                data2["concept"] = "general"
            bad_names2 = _find_ungrounded_names(data2, seed_text)
            if bad_names2:
                raise InterviewGenerationError(
                    f"Generated question still contains names not in seed facts: "
                    f"{', '.join(sorted(bad_names2))}. Saving nothing."
                )
            data = data2
        else:
            raise InterviewGenerationError(
                f"Generated question still contains names not in seed facts: "
                f"{', '.join(sorted(bad_names))}. Saving nothing."
            )

    # 9. Build model
    iq = InterviewQuestion(
        project_id=project_id,
        round=round_name,
        difficulty=difficulty,
        concept=data["concept"],
        question=data["question"],
        key_points=[
            KeyPoint(point=kp["point"], kind=kp.get("kind", "general"))
            for kp in data["key_points"]
        ],
        common_mistakes=data.get("common_mistakes", []),
        seed=seed,
    )

    # 10. Persist
    if save:
        insert_coro = db.interview_questions.insert_one(iq.model_dump(mode="json"))
        import inspect as _inspect
        if _inspect.isawaitable(insert_coro):
            await insert_coro

    return iq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> tuple[dict, bool]:
    """Strip fences, extract {…}, attempt json.loads."""
    # Strip code fences
    text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n?```$", "", text.strip(), flags=re.MULTILINE)
    # Extract between first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}, False
    try:
        return json.loads(text[start : end + 1]), True
    except json.JSONDecodeError:
        return {}, False


def _validate_structure(data: dict) -> None:
    """Raise InterviewGenerationError if required fields are missing or out of range."""
    if not data.get("question"):
        raise InterviewGenerationError("LLM response missing 'question' field.")
    kps = data.get("key_points") or []
    if not (3 <= len(kps) <= 5):
        raise InterviewGenerationError(
            f"Expected 3–5 key_points, got {len(kps)}."
        )
    if not data.get("common_mistakes"):
        raise InterviewGenerationError("LLM response missing 'common_mistakes'.")


def _find_ungrounded_names(data: dict, seed_text_lower: str) -> set[str]:
    """Return names (backticked words + filenames) that don't appear in seed_text."""
    combined = data.get("question", "") + " " + " ".join(
        kp.get("point", "") if isinstance(kp, dict) else str(kp)
        for kp in (data.get("key_points") or [])
    )

    bad: set[str] = set()

    # Backtick tokens
    for token in re.findall(r"`([^`]+)`", combined):
        if token.lower() not in seed_text_lower:
            bad.add(token)

    # Filename-like tokens
    for token in _FILENAME_RE.findall(combined):
        if token.lower() not in seed_text_lower:
            bad.add(token)

    return bad
