"""
RepoMentor Agent CLI:
Usage: python -m app.cli ask <project_id> "<question>"
       python -m app.cli chat <project_id> [--session <id>]
"""

import asyncio
import logging
import os
import sys
from typing import Any, Callable

from app.agent.loop import run_agent
from app.agent.state import AgentRun, TraceStep
from app.core.db import get_db
from app.mentor.chat import ask
from app.mentor.sessions import create_session, get_session


def print_usage() -> None:
    print('Usage: python -m app.cli ask <project_id> "<question>"')
    print('       python -m app.cli chat <project_id> [--session <id>]')
    print('Example: python -m app.cli ask proj_123 "What dependencies does this project use?"')
    print('         python -m app.cli chat proj_123')


def on_event(text: str) -> None:
    print(f"  ... {text}", flush=True)


def on_step(step: TraceStep) -> None:
    print(f"[Step {step.step}] {step.tool}  {step.args}", flush=True)
    preview = " ".join((step.result_preview or "").splitlines()).strip()
    print(f"  -> {preview[:150]}", flush=True)
    if step.evidence_ids:
        evidence = ", ".join(step.evidence_ids[:5])
        more = len(step.evidence_ids) - 5
        suffix = f" (+{more} more)" if more > 0 else ""
        print(f"  evidence: {evidence}{suffix}", flush=True)
    print(f"  {step.ms}ms | {step.tokens} tokens", flush=True)


async def _async_main(project_id: str, question: str) -> int:
    print("=== RepoMentor Agent ===")
    print(f"Project ID: {project_id}")
    print(f"Goal: {question}\n")

    run_result: AgentRun = await run_agent(
        question,
        project_id,
        max_steps=10,
        on_step=on_step,
        on_event=on_event,
    )

    if (
        run_result.stop_reason == "error"
        and run_result.final_answer
        and (
            "token limit" in run_result.final_answer.lower()
            or run_result.final_answer.startswith("Daily token limit reached for model ")
        )
    ):
        print(" ".join(run_result.final_answer.split()))
        return 2

    print("=== Final Answer ===")
    print(run_result.final_answer or "(No final answer produced)")
    print(f"Stop Reason: {run_result.stop_reason}")
    print(f"Total Tokens: {run_result.total_tokens}")

    return 0 if run_result.stop_reason == "answered" else 2


async def _async_chat(
    project_id: str,
    session_id: str | None = None,
    db: Any = None,
    ask_fn: Callable[..., Any] | None = None,
    input_fn: Callable[[], str] | None = None,
) -> int:
    database = db if db is not None else get_db()
    if session_id:
        session = await get_session(database, session_id)
        if session is None:
            print(f"Error: Session '{session_id}' not found.", file=sys.stderr)
            return 1
        if session.get("project_id") != project_id:
            print(
                f"Error: Session '{session_id}' belongs to project '{session.get('project_id')}', not '{project_id}'.",
                file=sys.stderr,
            )
            return 1
    else:
        session_id = await create_session(database, project_id)

    print(session_id)
    ask_caller = ask_fn if ask_fn is not None else ask

    while True:
        try:
            if input_fn is not None:
                line = input_fn()
            else:
                line = await asyncio.to_thread(input, "you> ")
        except (EOFError, KeyboardInterrupt):
            break

        if line == "/exit":
            break

        try:
            run_result: AgentRun = await ask_caller(
                database,
                project_id,
                session_id,
                line,
                on_step=on_step,
                on_event=on_event,
            )
        except KeyboardInterrupt:
            print("\nSession interrupted.")
            break

        print(run_result.final_answer or "")
        print(f"Stop Reason: {run_result.stop_reason}")
        print(f"Total Tokens: {run_result.total_tokens}")

    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO if os.getenv("REPOMENTOR_DEBUG") else logging.CRITICAL
    )
    args = sys.argv[1:]
    if not args:
        print_usage()
        sys.exit(1)

    cmd = args[0]
    if cmd == "ask":
        if len(args) < 3:
            print_usage()
            sys.exit(1)
        project_id = args[1]
        question = args[2]
        try:
            sys.exit(asyncio.run(_async_main(project_id, question)))
        except KeyboardInterrupt:
            print("Interrupted. Steps printed above were completed before you stopped it.")
            sys.exit(130)
        except Exception as exc:
            print(f"Execution error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "chat":
        if len(args) < 2:
            print_usage()
            sys.exit(1)
        project_id = args[1]
        session_id = None
        i = 2
        while i < len(args):
            if args[i] == "--session":
                if i + 1 < len(args):
                    session_id = args[i + 1]
                    i += 2
                else:
                    print("Error: --session requires an argument", file=sys.stderr)
                    sys.exit(1)
            elif args[i].startswith("--session="):
                session_id = args[i].split("=", 1)[1]
                i += 1
            else:
                print_usage()
                sys.exit(1)

        try:
            sys.exit(asyncio.run(_async_chat(project_id, session_id)))
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception as exc:
            print(f"Execution error: {exc}", file=sys.stderr)
            sys.exit(1)

    else:
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
