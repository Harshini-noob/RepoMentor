"""
Loop execution guards:
1. Max steps budget limit.
2. Repeated-call detector: identical (tool_name, args) called consecutively or previously.
3. Wrap-up notice injected at >= 70% budget.
4. Stop reason validator: 'answered', 'max_steps', 'error'.
"""

import json
from typing import Any


class RepeatedCallGuard:
    """Tracks tool calls made during a run and flags duplicate calls."""

    def __init__(self) -> None:
        self._seen_calls: set[str] = set()

    def _call_key(self, tool_name: str, args: dict[str, Any]) -> str:
        # Canonical JSON string representation of arguments
        try:
            sorted_args = json.dumps(args, sort_keys=True)
        except Exception:
            sorted_args = str(sorted(args.items()))
        return f"{tool_name}::{sorted_args}"

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
