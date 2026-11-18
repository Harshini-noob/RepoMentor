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
    unverified_count = 0
    scores: list[int] = []
    rows: list[ItemRow] = []

    concept_scores: dict[str, list[int]] = defaultdict(list)

    repo_points: list[dict[str, Any]] = []
    general_points: list[dict[str, Any]] = []
    seen_points: set[str] = set()

    lessons_given = 0
    before_after: list[dict[str, Any]] = []
    deltas: list[int] = []

    for it in items:
        status = _get_val(it, "status")
        score = _get_val(it, "score")
        qid = _get_val(it, "question_id")
        aid = _get_val(it, "attempt_id")

        if _get_val(it, "lesson_id"):
            lessons_given += 1

        b = _get_val(it, "before")
        a = _get_val(it, "after")
        if b is not None and a is not None:
            q_for_ba = questions_map.get(qid) if qid else None
            concept_ba = _get_val(q_for_ba, "concept") or ""
            q_text_ba = _get_val(q_for_ba, "question") or ""
            delta = a - b
            deltas.append(delta)
            before_after.append({
                "question_id": qid,
                "question": q_text_ba,
                "concept": concept_ba,
                "before": b,
                "after": a,
                "delta": delta,
            })

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

            # Check if points were recovered (covered) in retry attempt
            retry_aid = _get_val(it, "retry_attempt_id")
            retry_attempt = attempts_map.get(retry_aid) if retry_aid else None
            covered_in_retry: set[str] = set()
            if retry_attempt:
                ret_ev = _get_val(retry_attempt, "evaluation")
                ret_pts = _get_val(ret_ev, "points") or []
                for rp in ret_pts:
                    if _get_val(rp, "status") == "covered":
                        covered_in_retry.add(_get_val(rp, "point") or "")

            # Process rubric points for revision and count unverified
            attempt = attempts_map.get(aid) if aid else None
            if attempt:
                ev = _get_val(attempt, "evaluation")
                unverified_pts = _get_val(ev, "unverified") or []
                unverified_count += len(unverified_pts)
                pts = _get_val(ev, "points") or []
                for p in pts:
                    p_status = _get_val(p, "status")
                    if p_status in ("missing", "partial"):
                        p_text = _get_val(p, "point") or ""
                        p_kind = _get_val(p, "kind") or "general"
                        if not p_text or p_text in seen_points:
                            continue
                        # Exclude points recovered in the retry attempt of the same question
                        if p_text in covered_in_retry:
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
    average_improvement = (sum(deltas) / len(deltas)) if deltas else None

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
        "unverified_count": unverified_count,
        "unverified": unverified_count,
        "lessons_given": lessons_given,
        "before_after": before_after,
        "average_improvement": average_improvement,
    }
