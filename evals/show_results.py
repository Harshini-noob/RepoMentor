"""Display selected questions from a saved eval run."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _format_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def show_results(results_path: Path, only: set[str] | None = None) -> None:
    with results_path.open(encoding="utf-8") as results_file:
        payload = json.load(results_file)

    results = payload.get("results", []) if isinstance(payload, dict) else []
    if not isinstance(results, list):
        results = []

    for result in results:
        if not isinstance(result, dict):
            continue
        question_id = result.get("id", "(missing id)")
        if only is not None and question_id not in only:
            continue

        print(f"ID: {question_id}")
        print(f"Question: {result.get('question', '(missing question)')}")
        print(f"Stop reason: {result.get('stop_reason', '(missing stop_reason)')}")

        traces = result.get("traces", [])
        if not isinstance(traces, list):
            traces = []
        print("Tools called:")
        found_tool = False
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            tool = trace.get("tool")
            if not tool or tool in {"policy_nudge", "error"}:
                continue
            found_tool = True
            args = trace.get("args", {})
            print(f"  {tool}: {_format_value(args)}")
        if not found_tool:
            print("  (none)")

        evidence_ids: set[str] = set()
        for trace in traces:
            if isinstance(trace, dict):
                ids = trace.get("evidence_ids", [])
                if isinstance(ids, list):
                    evidence_ids.update(str(evidence_id) for evidence_id in ids)
        print(f"Evidence IDs: {len(evidence_ids)}")

        print("Metrics:")
        metric_names = (
            "completed",
            "steps",
            "total_tokens",
            "seconds",
            "tool_recall",
            "tool_ok",
            "unnecessary_calls",
            "evidence_hit",
            "unverified_citations",
            "tagged",
            "content_ok",
        )
        printed_metric = False
        for name in metric_names:
            if name in result:
                print(f"  {name}: {_format_value(result[name])}")
                printed_metric = True
        if not printed_metric:
            print("  (none)")

        print("Final answer:")
        print(result.get("answer") or "(missing answer)")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_json", type=Path)
    parser.add_argument("--only", help="Comma-separated question IDs to display.")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    only = (
        {question_id.strip() for question_id in args.only.split(",") if question_id.strip()}
        if args.only is not None
        else None
    )
    show_results(args.results_json, only)


if __name__ == "__main__":
    main()
