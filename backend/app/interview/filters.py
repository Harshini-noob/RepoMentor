"""Rubric point filters."""

from __future__ import annotations

import re

_IMPORT_USAGE_RE = re.compile(r"\b(imports?|imported|used in|used across)\b", re.I)
_FILENAME_RE = re.compile(r"\b[\w/.-]+\.(?:py|js|ts|md|toml|ya?ml|json)\b", re.I)


def is_listing_point(text: str) -> bool:
    """Return True if text merely lists imports or file usage."""
    if not text:
        return False
    return bool(_IMPORT_USAGE_RE.search(text) and _FILENAME_RE.search(text))
