"""
Agent execution loop:
Orchestrates autonomous multi-step reasoning, tool invocations, loop guards,
and persistence to agent_traces collection in MongoDB.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.guards import RepeatedCallGuard, should_warn_wrap_up
from app.agent.llm import chat as default_chat
from app.agent.state import AgentRun, LLMReply, ToolCall, TraceStep
from app.agent.tools import build_registry
from app.agent.tools.base import ToolOutput
from app.agent.tools.registry import ToolRegistry
from app.core.db import get_db
from app.core.logging import redact

logger = logging.getLogger("repomentor.agent.loop")

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "investigator.md"


def load_system_prompt() -> str:
    """Load default investigator system prompt."""
    if PROMPT_FILE.exists():
        try:
            return PROMPT_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return (
        "You are an expert software investigator and code architecture assistant. "
        "Investigate repository structures, dependencies, history, and source code using the provided tools."
    )


async def run_agent(
    goal: str,
    project_id: str,
    max_steps: int = 10,
    db: AsyncIOMotorDatabase | None = None,
    registry: ToolRegistry | None = None,
    chat_fn: Callable[..., Any] | None = None,
) -> AgentRun:
    """
    Execute autonomous agent investigation loop up to max_steps:
    1. Initialize system and user goal messages.
    2. Step iteratively with chat_fn.
    3. If no tool calls -> return final answer (stop_reason='answered').
    4. If tool calls -> execute with repeated-call guard, record TraceStep, append messages.
    5. At >= 70% budget -> append wrap-up guidance.
    6. On max_steps reached -> ask model once for final summary of evidence.
    7. Save trace to agent_traces collection.
    """
    database = db if db is not None else get_db()
    reg = registry if registry is not None else build_registry(db=database)
    llm_chat = chat_fn if chat_fn is not None else default_chat

    # Setup prompt and message history
    system_prompt = load_system_prompt()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": goal},
    ]

    steps: list[TraceStep] = []
    total_tokens = 0
    stop_reason = "answered"
    final_answer: str | None = None
    repeated_guard = RepeatedCallGuard()
    wrap_up_warned = False

    schemas = reg.schemas()

    try:
        step_idx = 0
        while step_idx < max_steps:
            step_idx += 1
            t_start = time.perf_counter()

            # Check 70% wrap-up guard
            if not wrap_up_warned and should_warn_wrap_up(step_idx, max_steps):
                messages.append({
                    "role": "system",
                    "content": (
                        f"Notice: You have used {step_idx}/{max_steps} steps (70% budget reached). "
                        "Please synthesize your findings and provide your final answer now."
                    ),
                })
                wrap_up_warned = True

            # Call LLM
            reply: LLMReply = await llm_chat(messages=messages, tools=schemas)

            call_tokens = (reply.usage.get("input_tokens", 0) + reply.usage.get("output_tokens", 0)) if reply.usage else 0
            total_tokens += call_tokens

            # If no tool calls produced, model delivered final answer
            if not reply.tool_calls:
                final_answer = reply.content or ""
                stop_reason = "answered"
                break

            # Process tool calls
            # 1. Append assistant tool-calls message to history
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": reply.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else str(tc.arguments),
                        },
                    }
                    for tc in reply.tool_calls
                ],
            }
            messages.append(assistant_msg)

            # 2. Execute each tool call and collect results
            for tc in reply.tool_calls:
                args = tc.arguments if isinstance(tc.arguments, dict) else {}
                tool_output: ToolOutput

                if repeated_guard.is_repeated(tc.name, args):
                    tool_output = ToolOutput(
                        text=(
                            f"ERROR: You already called tool '{tc.name}' with identical arguments {args} "
                            "in this session. Please analyze the previous result or choose a different query."
                        ),
                        evidence_ids=[],
                        truncated=False,
                    )
                else:
                    tool_output = await reg.execute(tc.name, args, project_id)

                elapsed_ms = int((time.perf_counter() - t_start) * 1000)

                # Record TraceStep
                step_record = TraceStep(
                    step=step_idx,
                    thought_summary=reply.content[:160] if reply.content else None,
                    tool=tc.name,
                    args=args,
                    result_preview=tool_output.text[:200] if tool_output.text else "",
                    evidence_ids=tool_output.evidence_ids,
                    ms=elapsed_ms,
                    tokens=call_tokens,
                )
                steps.append(step_record)

                # Append tool result message to conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": tool_output.text,
                })

        # Check if max_steps reached without final answer
        if step_idx >= max_steps and final_answer is None:
            stop_reason = "max_steps"
            # Ask the model once for a final answer from evidence gathered so far
            final_prompt_msg = {
                "role": "user",
                "content": (
                    "Budget limit reached. Based on the evidence gathered so far, "
                    "provide your final answer to the original goal now. "
                    "Explicitly state what you could and could not determine."
                ),
            }
            messages.append(final_prompt_msg)
            final_reply: LLMReply = await llm_chat(messages=messages, tools=None)
            final_tokens = (final_reply.usage.get("input_tokens", 0) + final_reply.usage.get("output_tokens", 0)) if final_reply.usage else 0
            total_tokens += final_tokens
            final_answer = final_reply.content or "Could not determine final answer within step budget."

    except Exception as exc:
        logger.exception("Agent run encountered unhandled error: %s", exc)
        stop_reason = "error"
        if final_answer is None:
            final_answer = f"Agent execution aborted due to error: {redact(str(exc))}"

    run_record = AgentRun(
        goal=goal,
        project_id=project_id,
        steps=steps,
        final_answer=final_answer,
        stop_reason=stop_reason,
        total_tokens=total_tokens,
    )

    # Persist run to agent_traces collection
    try:
        await database.agent_traces.insert_one(run_record.model_dump())
    except Exception as save_exc:
        logger.warning("Could not persist agent run to agent_traces: %s", save_exc)

    return run_record
