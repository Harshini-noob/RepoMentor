"""
Loop execution guards:
1. Max steps budget limit.
2. Repeated-call detector: identical (tool_name, args) called consecutively or previously.
3. Wrap-up notice injected at >= 70% budget.
4. Stop reason validator: 'answered', 'max_steps', 'error'.
"""

import json
from typing import Any

HISTORY_TOOLS = {"search_commits", "file_history", "get_commit", "search_prs", "get_pr"}


def is_why_question(goal: str) -> bool:
    lowered_goal = goal.lower()
    return any(
        phrase in lowered_goal
        for phrase in (
            "why did",
            "why do",
            "why was",
            "why is",
            "why use",
            "how come",
            "reason for",
            "what led to",
        )
    )


def normalize_arguments(val: Any) -> Any:
    """
    Normalise tool arguments before comparing for repeats:
    - Lowercase string values and strip leading/trailing whitespace.
    - Drop keys whose value is None.
    - Recursively normalize nested dicts and lists.
    - Sort dictionary keys.
    """
    if isinstance(val, dict):
        cleaned: dict[str, Any] = {}
        for k in sorted(val.keys()):
            v = val[k]
            if v is not None:
                cleaned[k] = normalize_arguments(v)
        return cleaned
    elif isinstance(val, list):
        return [normalize_arguments(item) for item in val]
    elif isinstance(val, str):
        return val.strip().lower()
    return val


class RepeatedCallGuard:
    """Tracks tool calls made during a run and flags duplicate calls."""

    def __init__(self) -> None:
        self._seen_calls: set[str] = set()

    def _call_key(self, tool_name: str, args: dict[str, Any]) -> str:
        # Canonical JSON string representation of normalized arguments
        repeat_args = {key: value for key, value in args.items() if key != "limit"}
        norm_args = normalize_arguments(repeat_args)
        try:
            sorted_args = json.dumps(norm_args, sort_keys=True)
        except Exception:
            sorted_args = str(norm_args)
        return f"{tool_name.strip().lower()}::{sorted_args}"

    def is_repeated(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Return True if (tool_name, args) was already invoked in this run."""
        key = self._call_key(tool_name, args)
        if key in self._seen_calls:
            return True
        self._seen_calls.add(key)
        return False


def should_warn_wrap_up(current_step: int, max_steps: int) -> bool:
    """Check if agent has consumed >= 70% of max_steps budget."""
    if max_steps <= 0:
        return False
    # e.g. for max_steps=10, 70% threshold is step >= 7
    threshold = int(0.7 * max_steps)
    return current_step >= threshold
