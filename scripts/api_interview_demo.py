"""Interactive demo using only the RepoMentor Interview HTTP API."""

import argparse
import sys
import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive Interview Demo using the RepoMentor HTTP API.")
    parser.add_argument("project_pos", nargs="?", default=None, help="Project ID")
    parser.add_argument("--project", "-p", dest="project_opt", default=None, help="Project ID")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the RepoMentor API")
    args = parser.parse_args()

    project_id = args.project_opt or args.project_pos
    if not project_id:
        print("Error: project_id is required (pass as positional argument or --project).", file=sys.stderr)
        sys.exit(1)

    base_url = args.url.rstrip("/")
    client = httpx.Client(base_url=base_url, timeout=60.0)

    print(f"Creating 1-question interview session for project '{project_id}'...")
    create_resp = client.post(
        f"/projects/{project_id}/interview/sessions",
        json={"questions": 1},
    )
    if create_resp.status_code != 201:
        print(f"Error creating interview session ({create_resp.status_code}): {create_resp.text}", file=sys.stderr)
        sys.exit(1)

    data = create_resp.json()
    session_id = data["session_id"]
    print(f"Session started: {session_id}\n")

    while True:
        next_resp = client.post(f"/projects/{project_id}/interview/sessions/{session_id}/next")
        if next_resp.status_code != 200:
            print(f"Error getting next action ({next_resp.status_code}): {next_resp.text}", file=sys.stderr)
            sys.exit(1)

        action = next_resp.json()
        act = action.get("action")

        if act == "question":
            q_id = action["question_id"]
            kind = action.get("kind", "initial")
            slot = action.get("slot", 1)
            total = action.get("total", 1)
            print(f"\n[Question {slot}/{total}] ({kind})")
            print(f"{action.get('text', '')}")

            try:
                answer = input("\nYour answer (press Enter with empty line to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                sys.exit(0)

            if not answer:
                skip_resp = client.post(f"/projects/{project_id}/interview/sessions/{session_id}/skip")
                if skip_resp.status_code != 200:
                    print(f"Error skipping question ({skip_resp.status_code}): {skip_resp.text}", file=sys.stderr)
                else:
                    print("Question skipped.")
            else:
                ans_resp = client.post(
                    f"/projects/{project_id}/interview/sessions/{session_id}/answer",
                    json={"question_id": q_id, "answer": answer},
                )
                if ans_resp.status_code != 200:
                    print(f"Error submitting answer ({ans_resp.status_code}): {ans_resp.text}", file=sys.stderr)
                else:
                    ev = ans_resp.json()
                    print("\n--- Evaluation ---")
                    print(f"Score: {ev.get('score', 0)}/5")
                    missed = ev.get("missed_points") or []
                    if missed:
                        print("Missed points:")
                        for mp in missed:
                            print(f"  - {mp}")
                    if ev.get("advice"):
                        print(f"Advice: {ev['advice']}")
                    if ev.get("next"):
                        print(f"Next step: {ev['next']}")

        elif act == "lesson":
            print("\n=== Lesson ===")
            print(action.get("text", ""))
            notes = action.get("notes") or []
            if notes:
                print("\nNotes:")
                for n in notes:
                    print(f"  - {n}")

        elif act == "finished":
            print("\n=== Interview Summary ===")
            summary = action.get("summary") or {}
            print(f"Answered: {summary.get('answered', 0)}")
            print(f"Skipped: {summary.get('skipped', 0)}")
            print(f"Average score: {summary.get('average_score')}")
            sys.exit(0)

        else:
            print(f"Unknown action: {act}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
