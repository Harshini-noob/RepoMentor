"""
RepoMentor Agent CLI:
Usage: python -m app.cli ask <project_id> "<question>"
"""

import asyncio
import logging
import os
import sys

from app.agent.loop import run_agent
from app.agent.state import AgentRun, TraceStep


def print_usage() -> None:
    print('Usage: python -m app.cli ask <project_id> "<question>"')
    print('Example: python -m app.cli ask proj_123 "What dependencies does this project use?"')


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

    print("=== Final Answer ===")
    print(run_result.final_answer or "(No final answer produced)")
    print(f"Stop Reason: {run_result.stop_reason}")
    print(f"Total Tokens: {run_result.total_tokens}")

    return 0 if run_result.stop_reason == "answered" else 2


def main() -> None:
    logging.basicConfig(
        level=logging.INFO if os.getenv("REPOMENTOR_DEBUG") else logging.CRITICAL
    )
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "ask":
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


if __name__ == "__main__":
    main()
