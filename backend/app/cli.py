"""
RepoMentor Agent CLI:
Usage: python -m app.cli ask <project_id> "<question>"
       python -m app.cli chat <project_id> [--session <id>]
       python -m app.cli interview-question <project_id> --round <name> --difficulty <int> [--commit <sha>] [--dry-run] [--show-seed]
       python -m app.cli interview-answer <project_id> [--question <id> | --last] [--answer "text"] [--dry-run]
       python -m app.cli interview <project_id> [--questions N] [--difficulty D] [--session <id>]
       python -m app.cli interview-report <project_id> [--session <id>]
"""

import asyncio
import inspect
import logging
import os
import sys
from typing import Any, Callable

from app.agent.loop import run_agent
from app.agent.state import AgentRun, TraceStep
from app.core.db import get_db
from app.interview.evaluate import InterviewEvaluationError, evaluate_answer
from app.interview.generate import InterviewGenerationError, generate_question
from app.interview.profile import get_profile, rebuild as rebuild_profile
from app.interview.runner import print_interview_summary, run_interview
from app.interview.schema import InterviewAttempt, InterviewQuestion
from app.interview.session import (
    create_session as create_interview_session,
    get_session as get_interview_session,
)
from app.interview.summary import build_summary
from app.mentor.chat import ask
from app.mentor.sessions import create_session, get_session


def print_usage() -> None:
    print('Usage: python -m app.cli ask <project_id> "<question>"')
    print('       python -m app.cli chat <project_id> [--session <id>]')
    print('       python -m app.cli interview-question <project_id> --round <name> --difficulty <int> [--commit <sha>] [--dry-run] [--show-seed]')
    print('       python -m app.cli interview-answer <project_id> [--question <id> | --last] [--answer "text"] [--dry-run]')
    print('       python -m app.cli interview <project_id> [--questions N] [--difficulty D] [--session <id>]')
    print('       python -m app.cli interview-report <project_id> [--session <id>]')
    print('       python -m app.cli profile <project_id> [--rebuild]')
    print('Example: python -m app.cli ask proj_123 "What dependencies does this project use?"')
    print('         python -m app.cli chat proj_123')
    print('         python -m app.cli interview proj_123 --questions 5 --difficulty 2')
    print('         python -m app.cli profile proj_123')


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
    candidates = getattr(result, "candidate_concepts", None) or (getattr(result, "seed", {}) or {}).get("candidate_concepts")
    if candidates:
        print(f"Candidate concepts: {', '.join(candidates)}")
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
        if "rubric does not cover the question" in result.validation_notes:
            print("WARNING: rubric does not cover the question; regenerate")

    print(f"\nQuestion ID: {result.question_id}")
    print(f"answer it with: interview-answer {result.project_id} --last")


def _print_evaluation_result(attempt: "InterviewAttempt") -> None:
    ev = attempt.evaluation
    cov_pct = int(round(ev.coverage * 100))
    print(f"\nScore: {ev.score}/4 ({cov_pct}% coverage)")

    unverified = getattr(ev, "unverified", None) or []
    if unverified:
        print("\nCheck these against your repo:")
        for u in unverified:
            u_q = getattr(u, "quote", None) or (u.get("quote") if isinstance(u, dict) else "")
            u_w = getattr(u, "why", None) or (u.get("why") if isinstance(u, dict) else "")
            print(f'  - "{u_q}": {u_w}')

    print("\nRubric points:")
    for p in ev.points:
        quote_part = f' (quote: "{p.quote}")' if p.quote else ""
        note_part = f" [{p.note}]" if p.note else ""
        print(f"  [{p.id}] [{p.status}] [{p.kind}] {p.point}{quote_part}{note_part}")

    missed = [p for p in ev.points if p.status in ("missing", "partial")]
    if missed:
        print("\nWhat was missed:")
        for p in missed:
            print(f"  [{p.status}] [{p.kind}] {p.point}")

    if ev.misconceptions:
        print("\nMisconceptions:")
        for m in ev.misconceptions:
            contra = f" (contradicts repo seed line {m.seed_line})" if m.seed_line is not None else ""
            print(f'  - "{m.quote}": {m.why}{contra}')

    if ev.strengths:
        print("\nStrengths:")
        for s in ev.strengths:
            print(f"  - {s}")

    if ev.advice:
        print(f"\nAdvice:\n  {ev.advice}")

    if ev.notes:
        print("\nNotes:")
        for n in ev.notes:
            print(f"  - {n}")

    if attempt.tokens:
        print(f"\nTokens used: {attempt.tokens}")


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

    elif cmd == "interview-answer":
        if len(args) < 2:
            print_usage()
            sys.exit(1)

        project_id = args[1]
        question_id: str | None = None
        use_last = False
        answer_text: str | None = None
        dry_run = False
        i = 2

        try:
            while i < len(args):
                flag = args[i]
                if flag == "--question":
                    if i + 1 >= len(args):
                        print("Error: --question requires an argument", file=sys.stderr)
                        print_usage()
                        sys.exit(1)
                    question_id = args[i + 1]
                    i += 2
                elif flag.startswith("--question="):
                    question_id = flag.split("=", 1)[1]
                    i += 1
                elif flag == "--last":
                    use_last = True
                    i += 1
                elif flag == "--answer":
                    if i + 1 >= len(args):
                        print("Error: --answer requires an argument", file=sys.stderr)
                        print_usage()
                        sys.exit(1)
                    answer_text = args[i + 1]
                    i += 2
                elif flag.startswith("--answer="):
                    answer_text = flag.split("=", 1)[1]
                    i += 1
                elif flag == "--dry-run":
                    dry_run = True
                    i += 1
                else:
                    print(f"Error: unknown argument: {flag}", file=sys.stderr)
                    print_usage()
                    sys.exit(1)
        except Exception:
            print_usage()
            sys.exit(1)

        if not question_id and not use_last:
            print("Error: either --question <id> or --last is required", file=sys.stderr)
            print_usage()
            sys.exit(1)

        try:
            async def _run_eval() -> None:
                database = get_db()
                target_qid = question_id
                target_doc = None
                if use_last:
                    cursor = database.interview_questions.find({"project_id": project_id}).sort("created_at", -1)
                    if hasattr(cursor, "to_list"):
                        docs = await cursor.to_list(1)
                    elif inspect.isawaitable(cursor):
                        cur = await cursor
                        docs = list(cur)
                    else:
                        docs = list(cursor)
                    if not docs:
                        print(f"Error: No interview questions found for project {project_id}", file=sys.stderr)
                        sys.exit(1)
                    target_doc = docs[0]
                    target_qid = target_doc.get("question_id")
                else:
                    find_res = database.interview_questions.find_one({"question_id": target_qid})
                    if inspect.isawaitable(find_res):
                        target_doc = await find_res
                    else:
                        target_doc = find_res
                    if not target_doc:
                        print(f"Error: Question not found: {target_qid}", file=sys.stderr)
                        sys.exit(1)

                candidate_ans = answer_text
                if candidate_ans is None:
                    print(f"\nQuestion: {target_doc.get('question')}\n")
                    print("Type your answer below. End with a line containing only '.' or EOF:")
                    lines = []
                    try:
                        for line in sys.stdin:
                            if line.strip() == ".":
                                break
                            lines.append(line)
                    except EOFError:
                        pass
                    candidate_ans = "".join(lines).strip()

                attempt = await evaluate_answer(
                    database,
                    question_id=target_qid,
                    answer=candidate_ans,
                    save=not dry_run,
                )
                _print_evaluation_result(attempt)

            asyncio.run(_run_eval())
            sys.exit(0)
        except KeyboardInterrupt:
            print("Interrupted during interview evaluation.", file=sys.stderr)
            sys.exit(130)
        except InterviewEvaluationError as exc:
            print(f"Evaluation error: {exc}", file=sys.stderr)
            sys.exit(2)
        except Exception as exc:
            print(f"Evaluation error: {exc}", file=sys.stderr)
            sys.exit(2)

    elif cmd == "interview":
        if len(args) < 2:
            print_usage()
            sys.exit(1)

        project_id = args[1]
        questions_n = 5
        difficulty = 2
        session_id: str | None = None
        i = 2

        try:
            while i < len(args):
                flag = args[i]
                if flag in ("--questions", "--difficulty", "--session"):
                    if i + 1 >= len(args):
                        print(f"Error: {flag} requires an argument", file=sys.stderr)
                        print_usage()
                        sys.exit(1)
                    value = args[i + 1]
                    i += 2
                    if flag == "--questions":
                        questions_n = int(value)
                    elif flag == "--difficulty":
                        difficulty = int(value)
                    else:
                        session_id = value
                elif flag.startswith("--questions="):
                    questions_n = int(flag.split("=", 1)[1])
                    i += 1
                elif flag.startswith("--difficulty="):
                    difficulty = int(flag.split("=", 1)[1])
                    i += 1
                elif flag.startswith("--session="):
                    session_id = flag.split("=", 1)[1]
                    i += 1
                else:
                    print(f"Error: unknown argument: {flag}", file=sys.stderr)
                    print_usage()
                    sys.exit(1)
        except ValueError:
            print("Error: --questions and --difficulty must be integers", file=sys.stderr)
            print_usage()
            sys.exit(1)

        active_session_id = session_id
        try:
            async def _run() -> None:
                nonlocal active_session_id
                database = get_db()
                if active_session_id is None:
                    created = await create_interview_session(
                        database, project_id, n=questions_n, base_difficulty=difficulty
                    )
                    active_session_id = created["session_id"]

                print(f"Session ID: {active_session_id}")
                print(f"Resume with: python -m app.cli interview {project_id} --session {active_session_id}")

                await run_interview(
                    database,
                    project_id=project_id,
                    n=questions_n,
                    base_difficulty=difficulty,
                    session_id=active_session_id,
                )

            asyncio.run(_run())
            sys.exit(0)
        except KeyboardInterrupt:
            if active_session_id:
                print(f"\nSession saved. Resume with: python -m app.cli interview {project_id} --session {active_session_id}")
            else:
                print("\nInterrupted during interview session.", file=sys.stderr)
            sys.exit(130)
        except Exception as exc:
            print(f"Execution error: {exc}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "interview-report":
        if len(args) < 2:
            print_usage()
            sys.exit(1)

        project_id = args[1]
        session_id: str | None = None
        i = 2

        while i < len(args):
            flag = args[i]
            if flag == "--session":
                if i + 1 >= len(args):
                    print("Error: --session requires an argument", file=sys.stderr)
                    print_usage()
                    sys.exit(1)
                session_id = args[i + 1]
                i += 2
            elif flag.startswith("--session="):
                session_id = flag.split("=", 1)[1]
                i += 1
            else:
                print(f"Error: unknown argument: {flag}", file=sys.stderr)
                print_usage()
                sys.exit(1)

        try:
            async def _run_report() -> None:
                database = get_db()
                if session_id:
                    session_doc = await get_interview_session(database, session_id)
                    if session_doc is None:
                        print(f"Error: Session '{session_id}' not found.", file=sys.stderr)
                        sys.exit(1)
                    if session_doc.get("project_id") != project_id:
                        print(
                            f"Error: Session '{session_id}' belongs to project '{session_doc.get('project_id')}', not '{project_id}'.",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                else:
                    cursor = database.interview_sessions.find({"project_id": project_id}).sort("created_at", -1)
                    if hasattr(cursor, "to_list"):
                        docs = await cursor.to_list(1)
                    elif inspect.isawaitable(cursor):
                        docs = list(await cursor)
                    else:
                        docs = list(cursor)
                    if not docs:
                        print(f"Error: No interview sessions found for project {project_id}", file=sys.stderr)
                        sys.exit(1)
                    session_doc = docs[0]

                items = session_doc.get("items", [])
                q_ids = [it["question_id"] for it in items if it.get("question_id")]
                a_ids = [it["attempt_id"] for it in items if it.get("attempt_id")]

                questions = []
                if q_ids:
                    q_cur = database.interview_questions.find({"question_id": {"$in": q_ids}})
                    if hasattr(q_cur, "to_list"):
                        questions = await q_cur.to_list(len(q_ids))
                    elif inspect.isawaitable(q_cur):
                        questions = list(await q_cur)
                    else:
                        questions = list(q_cur)

                attempts = []
                if a_ids:
                    a_cur = database.interview_attempts.find({"attempt_id": {"$in": a_ids}})
                    if hasattr(a_cur, "to_list"):
                        attempts = await a_cur.to_list(len(a_ids))
                    elif inspect.isawaitable(a_cur):
                        attempts = list(await a_cur)
                    else:
                        attempts = list(a_cur)

                summary = build_summary(items, questions, attempts)
                print_interview_summary(summary)

            asyncio.run(_run_report())
            sys.exit(0)
        except KeyboardInterrupt:
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

    elif cmd == "profile":
        if len(args) < 2:
            print_usage()
            sys.exit(1)
        project_id = args[1]
        do_rebuild = False
        i = 2
        while i < len(args):
            if args[i] == "--rebuild":
                do_rebuild = True
                i += 1
            else:
                print(f"Error: unknown argument: {args[i]}", file=sys.stderr)
                print_usage()
                sys.exit(1)

        try:
            async def _run_profile() -> None:
                database = get_db()
                if do_rebuild:
                    await rebuild_profile(database, project_id)

                profile = await get_profile(database, project_id)
                if not profile or not profile.get("concepts"):
                    print("No attempts yet. Run an interview first.")
                    return

                concepts = profile["concepts"]
                print(f"\n{'Concept':<30} {'Status':<10} {'Score':>6} {'Attempts':>8} {'Last Seen':<12}")
                print("-" * 70)
                for name, info in sorted(concepts.items()):
                    if not isinstance(info, dict):
                        continue
                    status = info.get("status", "unrated")
                    score = info.get("score", 0.0)
                    attempts = info.get("attempts", 0)
                    last_seen_raw = info.get("last_seen", "")
                    last_seen = last_seen_raw[:10] if last_seen_raw else "N/A"
                    print(f"{name:<30} {status:<10} {score:>6.1f} {attempts:>8} {last_seen:<12}")

                print("\nMost missed:")
                for name, info in sorted(concepts.items()):
                    if not isinstance(info, dict):
                        continue
                    misses = info.get("misses") or []
                    if misses:
                        print(f"  {name}:")
                        for m in misses[:3]:
                            count = m.get("count", 0)
                            point = m.get("point", "")
                            print(f"    ({count}x) {point}")

                from app.interview.profile import weak_concepts as _weak_concepts
                weak = _weak_concepts(profile, k=3)
                if weak:
                    print(f"\nRevise first: {', '.join(weak)}")

            asyncio.run(_run_profile())
            sys.exit(0)
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
