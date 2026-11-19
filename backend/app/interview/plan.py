"""Adaptive interview plan generator."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

from app.interview.concepts import CONCEPTS
from app.interview.profile import next_difficulty

ROUNDS_ORDER = [
    "history",
    "tech",
    "architecture",
    "history",
    "tech",
    "architecture",
    "history",
    "tech",
]

DIFFICULTY_OFFSETS = [0, 0, 1, 1, 2]


def _fixed_ramp(n: int, base_difficulty: int) -> list[dict[str, Any]]:
    """Return fixed round and difficulty ramp slots."""
    plan: list[dict[str, Any]] = []
    for i in range(n):
        r = ROUNDS_ORDER[i % len(ROUNDS_ORDER)]
        offset = DIFFICULTY_OFFSETS[i] if i < len(DIFFICULTY_OFFSETS) else DIFFICULTY_OFFSETS[-1]
        diff = max(1, min(5, base_difficulty + offset))
        plan.append({
            "concept": None,
            "difficulty": diff,
            "round": r,
            "reason": "no history yet",
        })
    return plan


def build_plan(
    profile: dict | None,
    n: int,
    base_difficulty: int,
    rng: Any = None,
    dossier: Any = None,
    available_concepts: Any = None,
) -> list[dict[str, Any]]:
    """Build an adaptive question plan based on learner profile.

    Parameters
    ----------
    profile:         Learner profile document or None.
    n:               Number of question slots to generate.
    base_difficulty: Base difficulty level (1..5).
    rng:             Optional random.Random instance for deterministic selection.
    dossier:         Optional dossier to check candidate seed availability.
    available_concepts: Optional set/list of available unrated concepts.
    """
    if n <= 0:
        return []

    # 1. Check for rated concepts (exclude "general")
    concepts_data = (profile.get("concepts") or {}) if profile else {}
    rated: list[tuple[str, float, int]] = []
    for name, info in concepts_data.items():
        if name == "general" or not isinstance(info, dict):
            continue
        attempts = info.get("attempts", 0)
        if attempts >= 1:
            score = float(info.get("score", 0.0))
            rated.append((name, score, attempts))

    # If no profile or no rated concepts, fallback to fixed ramp
    if not rated:
        return _fixed_ramp(n, base_difficulty)

    # Sort rated concepts by score ascending (lowest score first), then name
    rated.sort(key=lambda x: (x[1], x[0]))
    rated_names = [x[0] for x in rated]

    # 2. Collect candidate unrated concepts from CONCEPTS (exclude "general" and rated)
    unrated_pool: list[str] = [
        c for c in CONCEPTS if c != "general" and c not in rated_names
    ]

    if available_concepts is not None:
        avail_set = set(available_concepts)
        unrated_pool = [c for c in unrated_pool if c in avail_set]
    elif dossier is not None:
        from app.interview.seeds import find_seed_for_concept
        unrated_pool = [
            c for c in unrated_pool
            if find_seed_for_concept(dossier, c, []) is not None
        ]

    unrated_queue = list(unrated_pool)

    # 3. Assemble slots
    slots: list[dict[str, Any]] = []
    use_counts: dict[str, int] = defaultdict(int)
    rated_idx = 0

    for slot_num in range(1, n + 1):
        # Every 3rd slot when n >= 3 targets an unrated concept if available
        if n >= 3 and slot_num % 3 == 0 and unrated_queue:
            concept = unrated_queue.pop(0)
            diff = next_difficulty(profile, concept, base_difficulty)
            slots.append({
                "concept": concept,
                "difficulty": diff,
                "round": None,
                "reason": "not asked yet",
            })
            continue

        # Cycle through rated concepts (at most 2 slots per concept)
        chosen_concept: str | None = None
        for _ in range(len(rated_names)):
            candidate = rated_names[rated_idx]
            rated_idx = (rated_idx + 1) % len(rated_names)
            if use_counts[candidate] < 2:
                chosen_concept = candidate
                use_counts[candidate] += 1
                break

        if chosen_concept is not None:
            info = concepts_data[chosen_concept]
            score = float(info.get("score", 0.0))
            attempts = info.get("attempts", 0)

            if score < 1.75:
                reason = f"weak ({score:.1f}), easier"
            elif score >= 3.0:
                reason = f"strong ({score:.1f}), harder"
            else:
                reason = f"partial ({score:.1f})"

            diff = next_difficulty(profile, chosen_concept, base_difficulty)
            slots.append({
                "concept": chosen_concept,
                "difficulty": diff,
                "round": None,
                "reason": reason,
            })
        elif unrated_queue:
            # If all rated concepts reached cap, use unrated concept if available
            concept = unrated_queue.pop(0)
            diff = next_difficulty(profile, concept, base_difficulty)
            slots.append({
                "concept": concept,
                "difficulty": diff,
                "round": None,
                "reason": "not asked yet",
            })
        else:
            # Fallback to fixed ramp for this slot index
            i = slot_num - 1
            r = ROUNDS_ORDER[i % len(ROUNDS_ORDER)]
            offset = DIFFICULTY_OFFSETS[i] if i < len(DIFFICULTY_OFFSETS) else DIFFICULTY_OFFSETS[-1]
            diff = max(1, min(5, base_difficulty + offset))
            slots.append({
                "concept": None,
                "difficulty": diff,
                "round": r,
                "reason": "no history yet",
            })

    return slots
