"""Constants for interview question generation."""

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
