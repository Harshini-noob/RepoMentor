"""Constants for interview question generation."""

import re

CONCEPTS = [
    "database-choice",
    "data-modelling",
    "async-and-concurrency",
    "rate-limiting-and-fallback",
    "provider-abstraction",
    "llm-evaluation",
    "cost-and-routing",
    "api-design",
    "authentication",
    "configuration-and-secrets",
    "testing",
    "observability",
    "deployment-and-ci",
    "refactoring",
    "caching",
    "ui-and-dashboards",
    "general",
]

DIFFICULTY = {
    1: "recall: what does it do",
    2: "explain why it was done",
    3: "trade-offs and alternatives",
    4: "failure modes and scale",
    5: "redesign it",
}

ROUNDS = {
    "history": ["decisions"],
    "tech": ["technologies", "data"],
    "architecture": ["components", "apis", "features"],
}

CONCEPT_KEYWORDS = {
    "database-choice": ["supabase", "postgres", "database", "table", "sqlite", "mongo"],
    "data-modelling": ["table", "schema", "row", "column", "join"],
    "observability": ["log", "logging", "telemetry", "metric", "trace"],
    "llm-evaluation": ["quality", "judge", "score", "verification"],
    "cost-and-routing": ["cost", "budget", "routing", "route", "tier", "price"],
    "rate-limiting-and-fallback": ["rate limit", "fallback", "429", "quota", "retry"],
    "provider-abstraction": ["provider", "groq", "gemini", "openai client"],
    "async-and-concurrency": ["async", "await", "concurrent"],
    "api-design": ["endpoint", "route", "fastapi", "request", "response"],
    "authentication": ["auth", "api key", "token"],
    "testing": ["test", "pytest"],
    "configuration-and-secrets": ["env", "environment variable", "secret"],
    "deployment-and-ci": ["docker", "ci", "workflow"],
    "refactoring": ["refactor", "rename", "replace"],
    "caching": ["cache", "cached", "ttl"],
    "ui-and-dashboards": ["streamlit", "dashboard", "gradio", "ui", "frontend", "chart", "page"],
}


def candidate_concepts(seed_text: str, k: int = 3) -> list[str]:
    """Score each concept by keywords in seed_text, return top k (>0 score) + 'general'."""
    text_lower = seed_text.lower()
    scored: list[tuple[str, int]] = []

    for concept, keywords in CONCEPT_KEYWORDS.items():
        score = 0
        for kw in keywords:
            kw_clean = kw.lower()
            pattern = rf"\b{re.escape(kw_clean)}(?:s)?\b"
            if re.search(pattern, text_lower):
                score += 1
        if score > 0:
            scored.append((concept, score))

    # Stable sort by descending score (preserves order in CONCEPT_KEYWORDS on tie)
    scored.sort(key=lambda item: -item[1])

    candidates = [concept for concept, _ in scored[:k]]
    if "general" not in candidates:
        candidates.append("general")
    return candidates
