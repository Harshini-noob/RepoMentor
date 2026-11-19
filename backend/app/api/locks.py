"""Shared concurrency lock for LLM model calls."""

from __future__ import annotations

import asyncio

_model_lock = asyncio.Lock()


def get_model_lock() -> asyncio.Lock:
    """Return the shared asyncio.Lock for model execution."""
    return _model_lock
