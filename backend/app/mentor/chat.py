from pathlib import Path
from typing import Any, Callable

from app.agent.loop import run_agent
from app.agent.state import AgentRun, TraceStep
from app.agent.tools.registry import ToolRegistry
from app.mentor.sessions import append_turn, get_session, recent_history

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agent" / "prompts"
MENTOR_PROMPT_FILE = PROMPTS_DIR / "mentor.md"


def build_mentor_prompt() -> str:
    """Build mentor system prompt: mentor.md contents ONLY."""
    if MENTOR_PROMPT_FILE.exists():
        return MENTOR_PROMPT_FILE.read_text(encoding="utf-8").strip()
    return ""


async def ask(
    db: Any,
    project_id: str,
    session_id: str,
    message: str,
    on_step: Callable[[TraceStep], None] | None = None,
    on_event: Callable[[str], None] | None = None,
    chat_fn: Callable[..., Any] | None = None,
    registry: ToolRegistry | None = None,
    **kwargs: Any,
) -> AgentRun:
    """Execute a mentor turn within a session.

    Builds the mentor system prompt, loads recent history from the session,
    calls run_agent with max_steps=6, and saves the turn with append_turn
    only when stop_reason == 'answered'.
    """
    system_prompt = build_mentor_prompt()
    session = await get_session(db, session_id)
    history = recent_history(session) if session else []

    run_result = await run_agent(
        goal=message,
        project_id=project_id,
        max_steps=6,
        db=db,
        registry=registry,
        chat_fn=chat_fn,
        on_step=on_step,
        on_event=on_event,
        system_prompt=system_prompt,
        history=history,
        **kwargs,
    )

    if run_result.final_answer:
        run_result.final_answer = (
            run_result.final_answer.replace("\u202f", " ").replace("\u00a0", " ")
        )

    if run_result.stop_reason == "answered":
        tools_used = sorted(list({s.tool for s in run_result.steps if s.tool and s.tool != "error"}))
        meta = {
            "tokens": run_result.total_tokens,
            "tools": tools_used,
            "tools_used": tools_used,
            "stop_reason": run_result.stop_reason,
        }
        await append_turn(
            db=db,
            session_id=session_id,
            user_text=message,
            answer_text=run_result.final_answer or "",
            meta=meta,
        )

    return run_result
