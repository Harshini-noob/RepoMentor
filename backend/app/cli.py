"""
RepoMentor Agent CLI:
Usage: python -m app.cli ask <project_id> "<question>"
       python -m app.cli chat <project_id> [--session <id>]
       python -m app.cli interview-question <project_id> --round <name> --difficulty <int> [--commit <sha>] [--dry-run] [--show-seed]
"""

import asyncio
import logging
import os
import sys
from typing import Any, Callable

from app.agent.loop import run_agent
from app.agent.state import AgentRun, TraceStep
from app.core.db import get_db
from app.interview.generate import InterviewGenerationError, generate_question
from app.mentor.chat import ask
from app.mentor.sessions import create_session, get_session


def print_usage() -> None:
    print('Usage: python -m app.cli ask <project_id> "<question>"')
    print('       python -m app.cli chat <project_id> [--session <id>]')
    print('       python -m app.cli interview-question <project_id> --round <name> --difficulty <int> [--commit <sha>] [--dry-run] [--show-seed]')
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


def _print_interview_result(result: "InterviewQuestion", show_seed: bool) -> None:
    if show_seed:
        seed = result.seed
        print("Seed:")
        print(f"  section: {seed.get('section')}")
        if seed.get("commit"):
            print(f"  commit:  {seed['commit']}")
        for c in seed.get("claims", []):
            tag = c.get("tag", "?")
            claim = c.get("claim", "")
            ev = c.get("evidence") or []
            ev_str = ", ".join(ev[:4])
            print(f"  [{tag}] {claim}  ({ev_str})")

    print(f"\nQuestion: {result.question}")
    print(f"Concept:  {result.concept}  (difficulty {result.difficulty})")
    print("\nKey points:")
    for kp in result.key_points:
        if kp.kind == "repo" and kp.support is not None:
            label = f"repo -> seed line {kp.support}"
        else:
            label = "general"
        print(f"  [{label}] {kp.point}")
    print("\nCommon mistakes:")
    for m in result.common_mistakes:
        print(f"  - {m}")
    if getattr(result, "validation_notes", None):
        print("\nValidation notes:")
        for vn in result.validation_notes:
            print(f"  {vn}")


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

    elif cmd == "interview-question":
        if len(args) < 2:
            print_usage()
            sys.exit(1)

        project_id = args[1]
        round_name: str | None = None
        difficulty: int | None = None
        commit: str | None = None
        dry_run = False
        show_seed = False
        i = 2

        try:
            while i < len(args):
                flag = args[i]
                if flag in ("--round", "--difficulty", "--commit"):
                    if i + 1 >= len(args):
                        print(f"Error: {flag} requires an argument", file=sys.stderr)
                        print_usage()
                        sys.exit(1)
                    value = args[i + 1]
                    i += 2
                    if flag == "--round":
                        round_name = value
                    elif flag == "--difficulty":
                        difficulty = int(value)
                    else:
                        commit = value
                elif flag.startswith("--round="):
                    round_name = flag.split("=", 1)[1]
                    i += 1
                elif flag.startswith("--difficulty="):
                    difficulty = int(flag.split("=", 1)[1])
                    i += 1
                elif flag.startswith("--commit="):
                    commit = flag.split("=", 1)[1]
                    i += 1
                elif flag == "--dry-run":
                    dry_run = True
                    i += 1
                elif flag == "--show-seed":
                    show_seed = True
                    i += 1
                else:
                    print(f"Error: unknown argument: {flag}", file=sys.stderr)
                    print_usage()
                    sys.exit(1)
        except ValueError:
            print("Error: --difficulty must be an integer", file=sys.stderr)
            print_usage()
            sys.exit(1)

        if not round_name:
            print("Error: --round is required", file=sys.stderr)
            print_usage()
            sys.exit(1)
        if difficulty is None:
            print("Error: --difficulty is required", file=sys.stderr)
            print_usage()
            sys.exit(1)

        try:
            async def _run_gen() -> None:
                database = get_db()
                result = await generate_question(
                    database,
                    project_id,
                    round_name=round_name,
                    difficulty=difficulty,
                    commit=commit,
                    save=not dry_run,
                )
                _print_interview_result(result, show_seed)
                usage = getattr(result, "_usage", None)
                if usage:
                    print(f"\nTokens used: {usage}")

            asyncio.run(_run_gen())
            sys.exit(0)
        except KeyboardInterrupt:
            print("Interrupted during interview generation.", file=sys.stderr)
            sys.exit(130)
        except InterviewGenerationError as exc:
            print(f"Generation error: {exc}", file=sys.stderr)
            sys.exit(2)
        except Exception as exc:
            print(f"Generation error: {exc}", file=sys.stderr)
            sys.exit(2)

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
