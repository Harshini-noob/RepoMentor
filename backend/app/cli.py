"""
RepoMentor Agent CLI:
Usage: python -m app.cli ask <project_id> "<question>"
"""

import asyncio
import sys
from typing import Any

from app.agent.loop import run_agent
from app.agent.state import AgentRun


def print_usage() -> None:
    print('Usage: python -m app.cli ask <project_id> "<question>"')
    print('Example: python -m app.cli ask proj_123 "What dependencies does this project use?"')


async def _async_main(project_id: str, question: str) -> int:
    print(f"=== RepoMentor Agent ===")
    print(f"Project ID: {project_id}")
    print(f"Goal: {question}\n")

    run_result: AgentRun = await run_agent(
        goal=question,
        project_id=project_id,
        max_steps=10,
    )

    print("=== Execution Steps ===")
    if not run_result.steps:
        print("  (No tool calls made; answered directly)")
    else:
        for s in run_result.steps:
            thought = f" (Thought: {s.thought_summary})" if s.thought_summary else ""
            print(f"\n[Step {s.step}] Tool: {s.tool}{thought}")
            print(f"  Args: {s.args}")
            preview = s.result_preview.replace("\n", " ").strip() if s.result_preview else ""
            print(f"  Result Preview: {preview[:150]}...")
            if s.evidence_ids:
                print(f"  Evidence IDs: {', '.join(s.evidence_ids)}")
            print(f"  Duration: {s.ms}ms | Step Tokens: {s.tokens}")

    print("\n=== Final Answer ===")
    print(run_result.final_answer or "(No final answer produced)")
    print("\n====================")
    print(f"Stop Reason:  {run_result.stop_reason}")
    print(f"Total Tokens: {run_result.total_tokens}")

    return 0


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "ask":
        print_usage()
        sys.exit(1)

    project_id = args[1]
    question = args[2]

    try:
        exit_code = asyncio.run(_async_main(project_id, question))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nExecution error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
