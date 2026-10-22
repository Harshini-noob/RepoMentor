import re


def redact(text: str | None) -> str:
    """
    Scrub sensitive credentials (tokens, passwords, keys) from strings, error messages, and logs.
    """
    if not text:
        return ""

    # Redact GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_, github_pat_)
    text = re.sub(r"(gh[pousr]_[A-Za-z0-9_]{36,255}|github_pat_[A-Za-z0-9_]{80,255})", "[REDACTED_GITHUB_TOKEN]", text)

    # Redact standard Authorization: Bearer tokens
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}", r"\1[REDACTED]", text, flags=re.IGNORECASE)

    # Redact credentials in connection URLs (e.g. mongodb://user:pass@host:port)
    text = re.sub(r"://([^:]+):([^@]+)@", r"://\1:[REDACTED]@", text)

    return text
