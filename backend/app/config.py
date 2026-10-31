from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "repomentor"
    github_token: str | None = None

    # Generic OpenAI-compatible LLM provider (Gemini, Groq, etc.)
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "gemini-3.8-flash"
    llm_reasoning_effort: str = "low"
    llm_fallback_model: str | None = None

    # Legacy Groq fields — kept for backward compat; ignored when llm_* are set
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_reasoning_effort: str = "low"

    model_config = SettingsConfigDict(
        env_file=(ENV_PATH, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    # lru_cache ensures settings are read from disk/env once and reused
    return Settings()
