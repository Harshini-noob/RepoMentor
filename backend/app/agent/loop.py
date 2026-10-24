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
from app.agent.evidence import count_tags, find_unsupported_citations

from app.agent.guards import HISTORY_TOOLS, RepeatedCallGuard, is_why_question, should_warn_wrap_up
from app.agent.llm import LLMDailyLimitError, LLMOutputParseError, chat as default_chat
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


def _notify(callback: Callable[..., Any] | None, *args: Any) -> None:
    if callback is not None:
        try:
            callback(*args)
        except Exception:
            logger.debug("Progress callback failed", exc_info=True)


def _trim_old_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_message_indexes = [
        index for index, message in enumerate(messages) if message.get("role") == "tool"
    ]
    keep_indexes = set(tool_message_indexes[-2:])
    trimmed_messages: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        copied_message = dict(message)
        if message.get("role") == "tool" and index not in keep_indexes:
            content = message.get("content") or ""
            copied_message["content"] = (
                f"{content[:300]}\n"
                "[older result trimmed to save tokens; repeat the same call to see it again]"
            )
        trimmed_messages.append(copied_message)
    return trimmed_messages


def _format_evidence_ids(steps: list[TraceStep]) -> str:
    evidence_ids = sorted({eid for step in steps for eid in step.evidence_ids})
    if not evidence_ids:
        return "none"
    shown = ", ".join(evidence_ids[:10])
    remaining = len(evidence_ids) - 10
    return f"{shown} (+{remaining} more)" if remaining > 0 else shown


async def run_agent(
    goal: str,
    project_id: str,
    max_steps: int = 10,
    db: AsyncIOMotorDatabase | None = None,
    registry: ToolRegistry | None = None,
    chat_fn: Callable[..., Any] | None = None,
    on_step: Callable[[TraceStep], None] | None = None,
    on_event: Callable[[str], None] | None = None,
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
    parse_error_count = 0
    tool_result_cache: dict[str, str] = {}
    history_nudge_sent = False
    tag_nudge_sent = False
    repeated_call_count = 0
    repeated_call_nudge_sent = False

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

            # Call LLM, catching LLMOutputParseError for in-loop recovery
            try:
                _notify(on_event, f"thinking... (step {step_idx}/{max_steps})")
                llm_kwargs: dict[str, Any] = {
                    "messages": _trim_old_tool_results(messages),
                    "tools": schemas,
                }
                if on_event is not None:
                    llm_kwargs["on_event"] = on_event
                reply: LLMReply = await llm_chat(**llm_kwargs)
            except LLMOutputParseError as p_exc:
                parse_error_count += 1
                elapsed_ms = int((time.perf_counter() - t_start) * 1000)
                # Record a trace step for the parse error
                steps.append(
                    TraceStep(
                        step=step_idx,
                        thought_summary="LLMOutputParseError encountered",
                        tool="error",
                        args={},
                        result_preview=f"Parse error #{parse_error_count}: {redact(str(p_exc))[:200]}",
                        evidence_ids=[],
                        ms=elapsed_ms,
                        tokens=0,
                    )
                )
                _notify(on_step, steps[-1])

                if parse_error_count < 3 and step_idx < max_steps:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous reply was invalid. Either call one of the available tools "
                            "with valid JSON arguments, or give your final answer now. "
                            "Do not write your reasoning as plain text."
                        ),
                    })
                    continue
                else:
                    # On third occurrence (or if max_steps reached), finalize
                    stop_reason = "error"
                    break

            call_tokens = (reply.usage.get("input_tokens", 0) + reply.usage.get("output_tokens", 0)) if reply.usage else 0
            total_tokens += call_tokens

            # If no tool calls produced, model delivered final answer
            if not reply.tool_calls:
                history_was_checked = any(step.tool in HISTORY_TOOLS for step in steps)
                if (
                    is_why_question(goal)
                    and not history_was_checked
                    and not history_nudge_sent
                    and step_idx < max_steps
                ):
                    history_nudge_sent = True
                    messages.append({"role": "assistant", "content": reply.content or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Before answering a why-question, check the project history: call "
                            "search_commits (and file_history for the most relevant file) for the key term. "
                            "Then answer, separating what the code shows from why it was chosen, and tag "
                            "each part Fact, Inferred or Uncertain."
                        ),
                    })
                    nudge_step = TraceStep(
                        step=step_idx,
                        thought_summary=reply.content[:160] if reply.content else None,
                        tool="policy_nudge",
                        args={},
                        result_preview="Requested a project history check before answering the why-question.",
                        evidence_ids=[],
                        ms=int((time.perf_counter() - t_start) * 1000),
                        tokens=call_tokens,
                    )
                    steps.append(nudge_step)
                    _notify(on_step, nudge_step)
                    continue
                if (
                    not tag_nudge_sent
                    and not any(count_tags(reply.content or "").values())
                    and step_idx < max_steps
                ):
                    tag_nudge_sent = True
                    messages.append({"role": "assistant", "content": reply.content or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Rewrite your answer with every claim tagged [Fact] (a tool result shows it), "
                            "[Inferred] (reasoned from several observations) or [Uncertain] (not supported), "
                            "keeping the same citations. Do not call tools unless needed."
                        ),
                    })
                    nudge_step = TraceStep(
                        step=step_idx,
                        thought_summary=reply.content[:160] if reply.content else None,
                        tool="policy_nudge",
                        args={},
                        result_preview="Requested claim tags for the final answer.",
                        evidence_ids=[],
                        ms=int((time.perf_counter() - t_start) * 1000),
                        tokens=call_tokens,
                    )
                    steps.append(nudge_step)
                    _notify(on_step, nudge_step)
                    continue
                final_answer = reply.content or ""
                if tag_nudge_sent and not any(count_tags(final_answer).values()):
                    final_answer += "\n\n[No claim tags were produced]"
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
                call_key = repeated_guard._call_key(tc.name, args)
                was_repeated = repeated_guard.is_repeated(tc.name, args)

                if was_repeated:
                    repeated_call_count += 1
                    cached_text = tool_result_cache[call_key]
                    tool_output = ToolOutput(
                        text="(repeated call, returning the earlier result)\n" + cached_text[:2000],
                        evidence_ids=[],
                        truncated=False,
                    )
                else:
                    tool_output = await reg.execute(tc.name, args, project_id)
                    tool_result_cache[call_key] = tool_output.text

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
                _notify(on_step, step_record)

                # Append tool result message to conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": tool_output.text,
                })
                if was_repeated and repeated_call_count >= 2 and not repeated_call_nudge_sent:
                    messages.append({
                        "role": "user",
                        "content": (
                            "You are repeating searches. Call a different tool (for example "
                            "get_commit with a path) or give your final answer now."
                        ),
                    })
                    repeated_call_nudge_sent = True

        # Determine if finalization call is needed (on max_steps, parse error cutoff, or missing answer)
        if final_answer is None:
            if stop_reason != "error":
                stop_reason = "max_steps"

            final_prompt_msg = {
                "role": "user",
                "content": (
                    "Based on the evidence gathered so far, provide your final answer to the original goal now. "
                    "Tag each substantive claim with [Fact] (seen in code/commit), [Inferred] (reasoned from observations), "
                    "or [Uncertain] (not supported). Explicitly state what you could and could not determine."
                ),
            }
            messages.append(final_prompt_msg)
            try:
                _notify(on_event, "writing final answer from the evidence so far...")
                final_llm_kwargs: dict[str, Any] = {
                    "messages": _trim_old_tool_results(messages),
                    "tools": None,
                }
                if on_event is not None:
                    final_llm_kwargs["on_event"] = on_event
                final_reply: LLMReply = await llm_chat(**final_llm_kwargs)
                final_tokens = (final_reply.usage.get("input_tokens", 0) + final_reply.usage.get("output_tokens", 0)) if final_reply.usage else 0
                total_tokens += final_tokens
                final_answer = final_reply.content or None
            except LLMDailyLimitError as limit_exc:
                stop_reason = "error"
                final_answer = str(limit_exc)
            except Exception as fin_exc:
                logger.warning("Finalization LLM call failed: %s", fin_exc)
                final_answer = None

            # If finalization call also failed or returned empty content, build deterministic fallback
            if not final_answer:
                stop_reason = "error"
                tools_used = sorted(list({s.tool for s in steps if s.tool and s.tool != "error"}))
                tools_str = ", ".join(tools_used) if tools_used else "none"
                ev_str = _format_evidence_ids(steps)
                final_answer = (
                    f"Investigation terminated before completion ({stop_reason}). "
                    f"Tools used: {tools_str}. Evidence IDs gathered: {ev_str}. "
                    "Could not determine a definitive final answer due to execution errors."
                )

    except LLMDailyLimitError as exc:
        logger.warning("Daily LLM token limit reached: %s", exc)
        stop_reason = "error"
        final_answer = str(exc)
    except Exception as exc:
        logger.exception("Agent run encountered unhandled error: %s", exc)
        stop_reason = "error"
        # Deterministic fallback answer listing tools and evidence
        tools_used = sorted(list({s.tool for s in steps if s.tool and s.tool != "error"}))
        tools_str = ", ".join(tools_used) if tools_used else "none"
        ev_str = _format_evidence_ids(steps)
        final_answer = (
            f"Investigation terminated before completion (error: {redact(str(exc))[:200]}). "
            f"Tools used: {tools_str}. Evidence IDs gathered: {ev_str}. "
            "Could not determine a definitive final answer due to execution errors."
        )
    if final_answer:
        final_answer = final_answer.translate(
            {ord(char): None for char in "\u200b\u200c\u200d\ufeff"}
        )
        collected = {eid for s in steps for eid in s.evidence_ids}
        unsupported = find_unsupported_citations(final_answer, collected)
        if unsupported:
            final_answer += (
                "\n\n[Unverified citations, not backed by any tool result in this run: "
                + ", ".join(unsupported)
                + "]"
            )
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
