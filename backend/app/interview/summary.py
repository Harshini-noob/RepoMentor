"""Pure summary calculation for interview sessions (no LLM)."""

from __future__ import annotations

from collections import defaultdict, namedtuple
from typing import Any, Sequence


class ItemRow(namedtuple("ItemRow", ["round", "difficulty", "concept", "score"])):
    """Namedtuple representing a per-item summary row with dict-like key access."""

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, str):
            return getattr(self, item)
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_map(obj: Any, key_field: str) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    elif isinstance(obj, (list, tuple, set)):
        res: dict[str, Any] = {}
        for item in obj:
            k = _get_val(item, key_field)
            if k is not None:
                res[k] = item
        return res
    return {}


def build_summary(
    items: Sequence[Any],
    questions: Any,
    attempts: Any,
) -> dict[str, Any]:
    """Build an interview session summary from items, questions, and attempts.

    Returns:
    - answered: count of answered items
    - skipped: count of skipped items
    - average_score: average score over answered items (float, or None if none)
    - rows: per-item rows (round, difficulty, concept, score)
    - per_concept: per-concept average score and count
    - weakest_concepts: lowest average, at most 3 (only concepts with >=1 answered item)
    - revise: up to 6 rubric points (missing or partial), repo points first, each with concept and kind, no duplicates
    """
    questions_map = _normalize_map(questions, "question_id")
    attempts_map = _normalize_map(attempts, "attempt_id")

    answered_count = 0
    skipped_count = 0
    scores: list[int] = []
    rows: list[ItemRow] = []

    concept_scores: dict[str, list[int]] = defaultdict(list)

    repo_points: list[dict[str, Any]] = []
    general_points: list[dict[str, Any]] = []
    seen_points: set[str] = set()

    for it in items:
        status = _get_val(it, "status")
        score = _get_val(it, "score")
        qid = _get_val(it, "question_id")
        aid = _get_val(it, "attempt_id")

        q = questions_map.get(qid) if qid else None
        round_name = _get_val(q, "round")
        difficulty = _get_val(q, "difficulty")
        concept = _get_val(q, "concept")

        if status == "answered":
            answered_count += 1
            if score is not None:
                scores.append(score)
                if concept:
                    concept_scores[concept].append(score)

            # Process rubric points for revision
            attempt = attempts_map.get(aid) if aid else None
            if attempt:
                ev = _get_val(attempt, "evaluation")
                pts = _get_val(ev, "points") or []
                for p in pts:
                    p_status = _get_val(p, "status")
                    if p_status in ("missing", "partial"):
                        p_text = _get_val(p, "point") or ""
                        p_kind = _get_val(p, "kind") or "general"
                        if not p_text or p_text in seen_points:
                            continue
                        seen_points.add(p_text)
                        entry = {
                            "point": p_text,
                            "kind": p_kind,
                            "concept": concept or "",
                        }
                        if p_kind == "repo":
                            repo_points.append(entry)
                        else:
                            general_points.append(entry)

        elif status == "skipped":
            skipped_count += 1

        rows.append(
            ItemRow(
                round=round_name,
                difficulty=difficulty,
                concept=concept,
                score=score,
            )
        )

    average_score = (sum(scores) / len(scores)) if scores else None

    # Per-concept statistics
    per_concept: dict[str, dict[str, Any]] = {}
    for c, c_scs in concept_scores.items():
        per_concept[c] = {
            "average": sum(c_scs) / len(c_scs),
            "count": len(c_scs),
        }

    # Weakest concepts (lowest average, at most 3; only concepts with >=1 answered item)
    sorted_concepts = sorted(
        per_concept.keys(),
        key=lambda c: (per_concept[c]["average"], c),
    )
    weakest_concepts = sorted_concepts[:3]

    # Revise list: repo points first, at most 6
    revise = (repo_points + general_points)[:6]

    return {
        "answered": answered_count,
        "skipped": skipped_count,
        "average_score": average_score,
        "rows": rows,
        "per_concept": per_concept,
        "concept_scores": per_concept,
        "weakest_concepts": weakest_concepts,
        "revise": revise,
    }
