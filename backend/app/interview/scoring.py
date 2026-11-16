"""Pure scoring logic for interview question evaluations (no LLM)."""

from __future__ import annotations

from typing import Sequence, Any


def score_evaluation(points: Sequence[Any], misconceptions: Sequence[Any]) -> tuple[float, int]:
    """Compute (coverage, score) from rubric points and misconceptions.

    coverage = (covered*1 + partial*0.5) / number of rubric points.
    Base score:
      coverage >= 0.85 -> 4
      coverage >= 0.60 -> 3
      coverage >= 0.35 -> 2
      coverage >= 0.15 -> 1
      else 0
    Subtract 1 once if any misconception has contradicts_repo=True.
    Clamp to 0..4.
    """
    total = len(points)
    if total == 0:
        return 0.0, 0

    covered = 0
    partial = 0
    for p in points:
        status = getattr(p, "status", None)
        if status is None and isinstance(p, dict):
            status = p.get("status")
        if status == "covered":
            covered += 1
        elif status == "partial":
            partial += 1

    coverage = (covered * 1.0 + partial * 0.5) / total

    if coverage >= 0.85:
        score = 4
    elif coverage >= 0.60:
        score = 3
    elif coverage >= 0.35:
        score = 2
    elif coverage >= 0.15:
        score = 1
    else:
        score = 0

    has_contradiction = False
    for m in misconceptions:
        contradicts = getattr(m, "contradicts_repo", None)
        if contradicts is None and isinstance(m, dict):
            contradicts = m.get("contradicts_repo", False)
        if contradicts:
            has_contradiction = True
            break

    if has_contradiction:
        score -= 1

    score = max(0, min(4, score))
    return coverage, score
