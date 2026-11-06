"""
Conftest fixtures for LLM tests.
Ensures that environment variables do not interfere with test expectations.
"""

import os
import pytest
from app.config import get_settings

@pytest.fixture(autouse=True)
def clear_llm_fallback(monkeypatch):
    """Force LLM_FALLBACK_MODEL to be empty for every test.
    Also clear the cached Settings to reload the env changes.
    """
    # Set env var to empty string (treated as falsy by settings)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "")
    # Clear cached settings before test
    get_settings.cache_clear()
    yield
    # Clear again after test to avoid leaking state
    get_settings.cache_clear()
