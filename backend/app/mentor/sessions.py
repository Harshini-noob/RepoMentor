import uuid
from datetime import datetime, timezone
from typing import Any


async def create_session(db: Any, project_id: str, mode: str = "mentor") -> str:
    """Create a new session document in the sessions collection."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    doc = {
        "session_id": session_id,
        "project_id": project_id,
        "mode": mode,
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "summary": "",
    }
    await db.sessions.insert_one(doc)
    return session_id


async def get_session(db: Any, session_id: str) -> dict[str, Any] | None:
    """Retrieve a session by session_id, excluding _id."""
    return await db.sessions.find_one({"session_id": session_id}, {"_id": 0})


async def append_turn(
    db: Any,
    session_id: str,
    user_text: str,
    answer_text: str,
    meta: dict[str, Any],
) -> None:
    """Append a user message and an assistant message to the session."""
    now = datetime.now(timezone.utc)
    user_msg = {
        "role": "user",
        "content": user_text,
        "ts": now,
        "meta": {},
    }
    assistant_msg = {
        "role": "assistant",
        "content": answer_text,
        "ts": now,
        "meta": meta or {},
    }
    await db.sessions.update_one(
        {"session_id": session_id},
        {
            "$push": {"messages": {"$each": [user_msg, assistant_msg]}},
            "$set": {"updated_at": now},
        },
    )


def recent_history(
    session: dict[str, Any] | None,
    n_exchanges: int = 3,
    max_chars: int = 1200,
) -> list[dict[str, str]]:
    """Return the last n exchanges as role/content dicts.

    Assistant answers are cut to max_chars with '...' appended when cut.
    Only plain user and assistant messages are included, never tool results.
    """
    if not session or n_exchanges <= 0:
        return []

    messages = session.get("messages") or []
    plain_messages = [
        msg for msg in messages if msg.get("role") in {"user", "assistant"}
    ]
    selected = plain_messages[-(2 * n_exchanges):]

    history: list[dict[str, str]] = []
    for msg in selected:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "assistant" and len(content) > max_chars:
            content = content[:max_chars] + "..."
        history.append({"role": role, "content": content})
    return history
