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

from .concepts import CONCEPTS, DIFFICULTY, ROUNDS, candidate_concepts
from .schema import InterviewQuestion, KeyPoint

logger = logging.getLogger("repomentor.interview")

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "generate.md").read_text(
    encoding="utf-8"
)

# Tokens that look like source-code filenames
_FILENAME_RE = re.compile(r"\b[\w/.\-]+\.(?:py|js|ts|md|toml|ya?ml|json)\b")

# Effect words that should not be claimed as "repo" unless the seed line says so
_EFFECT_WORDS = {
    "latency", "faster", "slower", "speed", "cost", "cheaper",
    "scale", "scalab", "reliab", "throughput", "performance",
}

# Stop-words to exclude when checking content-word overlap
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "are", "was", "were", "has",
    "have", "had", "not", "but", "from", "you", "your", "its", "it", "in",
    "on", "at", "to", "of", "is", "as", "be", "or", "an", "a", "so", "if",
    "by", "do", "use", "used", "uses", "over", "per", "into", "than", "our",
    "their", "also", "when", "which", "can", "will", "each", "any", "all",
    "one", "two", "new", "just", "how", "what", "why",
}

# Phrases banned in question text; triggers one retry, then fails
_BANNED_QUESTION_PHRASES = (
    "the team",
    "recent",
    "recently",
    "did you consider",
    "you considered",
    "you decided",
    "you evaluated",
)


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
    seed_claims = seed.get("claims", [])

    # Seed text for candidates and name grounding
    seed_text = " ".join(
        c.get("claim", "") + " " + " ".join(c.get("evidence") or [])
        for c in seed_claims
    ).lower()

    candidates = candidate_concepts(seed_text)
    seed["candidate_concepts"] = candidates

    # 4. Build user message with numbered seed lines (1-based) and candidate concepts
    difficulty_label = DIFFICULTY.get(difficulty, str(difficulty))
    concepts_str = ", ".join(candidates)
    seed_lines_text = "\n".join(
        f"{i + 1}. [{c['tag'].capitalize()}] {c['claim']} (evidence: {', '.join(c['evidence'][:4])})"
        for i, c in enumerate(seed_claims)
    )
    user_msg = (
        f"Round: {round_name}\n"
        f"Difficulty: {difficulty} — {difficulty_label}\n"
        f"Allowed concepts: {concepts_str}\n\n"
        f"Seed facts:\n{seed_lines_text}"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    # 5. First LLM call
    reply: LLMReply = await _chat(messages, tools=None)
    data, parse_ok = _parse_json(reply.content or "")

    if not parse_ok:
        # Retry once on bad JSON
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

    # 7. Question-text validation: reject banned phrases, retry once, then fail
    banned_found = _check_banned_phrases(data.get("question", ""))
    if banned_found:
        logger.warning("Question contains banned phrase(s) %s — retrying.", banned_found)
        guidance = (
            f"The question contains forbidden phrases: {', '.join(repr(p) for p in banned_found)}. "
            "Rewrite the question avoiding those phrases."
        )
        messages_b = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": reply.content or ""},
            {"role": "user", "content": guidance},
        ]
        reply_b: LLMReply = await _chat(messages_b, tools=None)
        data_b, parse_ok_b = _parse_json(reply_b.content or "")
        if parse_ok_b:
            _validate_structure(data_b)
            banned_again = _check_banned_phrases(data_b.get("question", ""))
            if banned_again:
                raise InterviewGenerationError(
                    f"Generated question still contains forbidden phrases: "
                    f"{', '.join(repr(p) for p in banned_again)}. Saving nothing."
                )
            data = data_b
            reply = reply_b
        else:
            raise InterviewGenerationError(
                f"Generated question still contains forbidden phrases: "
                f"{', '.join(repr(p) for p in banned_found)}. Saving nothing."
            )

    # 8. Name-grounding validation
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

    # 9. Concept validation: if not in candidates, set to first candidate
    validation_notes: list[str] = []
    if data.get("concept") not in candidates:
        logger.warning(
            "Returned concept %r not in candidates %r — replacing with %r.",
            data.get("concept"), candidates, candidates[0],
        )
        data["concept"] = candidates[0]
        validation_notes.append("concept replaced by code")

    # 10. Key-point repo-grounding checks (after name validation, before saving)
    key_points, kp_notes = _validate_key_points(
        data.get("key_points", []), seed_claims
    )
    validation_notes.extend(kp_notes)

    # 10b. Key points count handling:
    # If fewer than 3, retry once with "Give 3 to 5 key points." and fail if still < 3
    if len(key_points) < 3:
        logger.warning("Fewer than 3 key points (%d) — retrying once.", len(key_points))
        messages_kp = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": reply.content or ""},
            {"role": "user", "content": "Give 3 to 5 key points."},
        ]
        reply_kp: LLMReply = await _chat(messages_kp, tools=None)
        data_kp, parse_ok_kp = _parse_json(reply_kp.content or "")
        if not parse_ok_kp:
            raise InterviewGenerationError("LLM did not return valid JSON after retry for key points.")
        _validate_structure(data_kp)
        data = data_kp
        reply = reply_kp
        if data.get("concept") not in candidates:
            data["concept"] = candidates[0]
            if "concept replaced by code" not in validation_notes:
                validation_notes.append("concept replaced by code")
        key_points, retry_kp_notes = _validate_key_points(data.get("key_points", []), seed_claims)
        validation_notes.extend(retry_kp_notes)
        if len(key_points) < 3:
            raise InterviewGenerationError(
                f"Expected 3–5 key_points, got {len(key_points)} after retry."
            )

    # 10c. Rubric check: at least 1 general key point at diff 2, at least 2 at diff >= 3
    min_general_req = 1 if difficulty == 2 else (2 if difficulty >= 3 else 0)
    if min_general_req > 0 and sum(1 for kp in key_points if kp.kind == "general") < min_general_req:
        logger.warning(
            "Difficulty %d requires >= %d general key points (got %d) — retrying once.",
            difficulty, min_general_req, sum(1 for kp in key_points if kp.kind == "general"),
        )
        messages_rubric = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": reply.content or ""},
            {
                "role": "user",
                "content": (
                    "The rubric lists facts but not what a good answer says. "
                    "Add general key points that answer the question (trade-offs, "
                    "alternatives, failure modes) and keep at most 2 repo points."
                ),
            },
        ]
        reply_rubric: LLMReply = await _chat(messages_rubric, tools=None)
        data_rubric, parse_ok_rubric = _parse_json(reply_rubric.content or "")
        if parse_ok_rubric:
            _validate_structure(data_rubric)
            data = data_rubric
            reply = reply_rubric
            if data.get("concept") not in candidates:
                data["concept"] = candidates[0]
                if "concept replaced by code" not in validation_notes:
                    validation_notes.append("concept replaced by code")
            key_points, retry_rubric_kp_notes = _validate_key_points(
                data.get("key_points", []), seed_claims
            )
            validation_notes.extend(retry_rubric_kp_notes)

        # If it is still below min_general_req after the retry, save the question anyway with the validation note
        if sum(1 for kp in key_points if kp.kind == "general") < min_general_req:
            validation_notes.append("rubric does not cover the question")

    # If there are more than 5 key points keep at most 5:
    # When trimming to 5 key points, keep at least min_general_req general points (drop surplus repo points first)
    if len(key_points) > 5:
        initial_kps_len = len(key_points)
        repo_points = [kp for kp in key_points if kp.kind == "repo"]
        general_points = [kp for kp in key_points if kp.kind != "repo"]
        if min_general_req > 0:
            min_general = min(min_general_req, len(general_points))
            max_repo = 5 - min_general
            kept_repo = repo_points[:max_repo]
            slots_for_general = 5 - len(kept_repo)
            kept_general = general_points[:slots_for_general]
            key_points = kept_repo + kept_general
        else:
            key_points = (repo_points + general_points)[:5]
        validation_notes.append(f"trimmed key points from {initial_kps_len} to 5")

    # 10c. Common mistakes count handling:
    # Retry once when there are none
    common_mistakes = list(data.get("common_mistakes") or [])
    if len(common_mistakes) == 0:
        logger.warning("No common mistakes — retrying once.")
        messages_cm = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": reply.content or ""},
            {"role": "user", "content": "Give 2 common mistakes."},
        ]
        reply_cm: LLMReply = await _chat(messages_cm, tools=None)
        data_cm, parse_ok_cm = _parse_json(reply_cm.content or "")
        if not parse_ok_cm:
            raise InterviewGenerationError("LLM did not return valid JSON after retry for common mistakes.")
        _validate_structure(data_cm)
        data = data_cm
        reply = reply_cm
        common_mistakes = list(data.get("common_mistakes") or [])
        if len(common_mistakes) == 0:
            raise InterviewGenerationError("LLM response missing 'common_mistakes' after retry.")

    # Keep the first 3 when there are more (with a note)
    if len(common_mistakes) > 3:
        initial_cm_len = len(common_mistakes)
        common_mistakes = common_mistakes[:3]
        validation_notes.append(f"trimmed common mistakes from {initial_cm_len} to 3")

    # 11. Build model
    iq = InterviewQuestion(
        project_id=project_id,
        round=round_name,
        difficulty=difficulty,
        concept=data["concept"],
        question=data["question"],
        key_points=key_points,
        common_mistakes=common_mistakes,
        seed=seed,
        candidate_concepts=candidates,
        validation_notes=validation_notes,
    )

    # 12. Persist
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
    """Raise InterviewGenerationError if required fields are missing."""
    if not data.get("question"):
        raise InterviewGenerationError("LLM response missing 'question' field.")
    if "key_points" not in data or not isinstance(data.get("key_points"), list):
        raise InterviewGenerationError("LLM response missing 'key_points' field.")
    if "common_mistakes" not in data or not isinstance(data.get("common_mistakes"), list):
        raise InterviewGenerationError("LLM response missing 'common_mistakes' field.")


def _check_banned_phrases(question: str) -> list[str]:
    """Return banned phrases found in the question text."""
    q_lower = question.lower()
    return [p for p in _BANNED_QUESTION_PHRASES if p in q_lower]


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


def _content_words(text: str) -> set[str]:
    """Return lowercase words of 4+ letters that are not stopwords."""
    return {
        w for w in re.findall(r"[a-z]{4,}", text.lower())
        if w not in _STOPWORDS
    }


_FILE_REGEX = re.compile(r"\b[\w/.-]+\.(?:py|js|ts|md|toml|ya?ml|json)\b", re.IGNORECASE)
_BACKTICK_REGEX = re.compile(r"`([^`]+)`")
_UNDERSCORE_REGEX = re.compile(r"\b[A-Za-z0-9]+_[A-Za-z0-9_]*\b")


def _extract_seed_repo_names(seed_claims: list[dict]) -> set[str]:
    """Extract file names and identifiers (backticks, snake_case, CamelCase) from seed claims."""
    seed_text = " ".join(
        (c.get("claim") or "") + " " + " ".join(c.get("evidence") or [])
        for c in seed_claims
    )
    names: set[str] = set()
    for m in _FILE_REGEX.finditer(seed_text):
        fname = m.group(0)
        names.add(fname)
        base = fname.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        names.add(base)
    for m in _BACKTICK_REGEX.finditer(seed_text):
        names.add(m.group(1))
    for m in _UNDERSCORE_REGEX.finditer(seed_text):
        names.add(m.group(0))
    for m in re.finditer(r"\b[A-Za-z]*[a-z][A-Z][a-zA-Z0-9]*\b", seed_text):
        names.add(m.group(0))
    return {n for n in names if len(n) >= 3}


def _validate_key_points(
    raw_kps: list[Any],
    seed_claims: list[dict],
) -> tuple[list[KeyPoint], list[str]]:
    """
    For each key point of kind "repo", apply three downgrade checks:
      (a) support missing or out of range → general
      (b) fewer than 2 content words shared with cited seed line → general
      (c) effect words in point but not in cited seed line → general

    For all 'general' key points: drop any point that mentions a file name or
    identifier from the seed text with note "general point mentions repo names".

    Returns (list[KeyPoint], list[str] validation_notes).
    """
    n_seeds = len(seed_claims)
    seed_repo_names = _extract_seed_repo_names(seed_claims)
    key_points: list[KeyPoint] = []
    notes: list[str] = []

    for raw in raw_kps:
        if not isinstance(raw, dict):
            pt = str(raw)
            if any(re.search(rf"\b{re.escape(rn)}\b", pt, re.IGNORECASE) for rn in seed_repo_names):
                notes.append("general point mentions repo names")
                continue
            key_points.append(KeyPoint(point=pt, kind="general"))
            continue

        point_text = raw.get("point", "")
        kind = raw.get("kind", "general")
        support = raw.get("support")  # may be int or None

        if kind != "repo":
            if any(re.search(rf"\b{re.escape(rn)}\b", point_text, re.IGNORECASE) for rn in seed_repo_names):
                notes.append("general point mentions repo names")
                continue
            key_points.append(KeyPoint(point=point_text, kind="general", support=None))
            continue

        # --- (a) support must be an int in 1..n_seeds ---
        if not isinstance(support, int) or not (1 <= support <= n_seeds):
            note = "no valid seed line"
            notes.append(f"Key point downgraded to general: {note!r} — {point_text!r}")
            # Downgraded repo points are always kept as general (name mentions expected)
            key_points.append(KeyPoint(point=point_text, kind="general", support=None, note=note))
            continue

        cited_claim = seed_claims[support - 1]
        cited_text = (
            cited_claim.get("claim", "") + " "
            + " ".join(cited_claim.get("evidence") or [])
        ).lower()

        # --- (b) content-word overlap ---
        point_words = _content_words(point_text)
        seed_words = _content_words(cited_text)
        overlap = point_words & seed_words
        if len(overlap) < 2:
            note = f"weak overlap with seed line {support}"
            notes.append(f"Key point downgraded to general: {note!r} — {point_text!r}")
            # Downgraded repo points are always kept as general (name mentions expected)
            key_points.append(KeyPoint(point=point_text, kind="general", support=support, note=note))
            continue

        # --- (c) effect words in point but not in cited seed ---
        point_lower = point_text.lower()
        effect_in_point = {
            ew for ew in _EFFECT_WORDS
            if ew in point_lower
        }
        effect_in_seed = {
            ew for ew in _EFFECT_WORDS
            if ew in cited_text
        }
        ungrounded_effects = effect_in_point - effect_in_seed
        if ungrounded_effects and effect_in_point:
            note = "effect not stated in the seed"
            notes.append(f"Key point downgraded to general: {note!r} — {point_text!r}")
            # Downgraded repo points are always kept as general (name mentions expected)
            key_points.append(KeyPoint(point=point_text, kind="general", support=support, note=note))
            continue

        # All checks passed — keep as repo
        key_points.append(KeyPoint(point=point_text, kind="repo", support=support))

    return key_points, notes
