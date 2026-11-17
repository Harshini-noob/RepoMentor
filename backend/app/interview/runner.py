"""Interview session runner.

Executes a multi-question interview session, recording items, handling /skip and /quit,
and generating the final summary.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Any, Callable

from app.interview.evaluate import evaluate_answer
from app.interview.generate import InterviewGenerationError, generate_question
from app.interview.session import (
    advance,
    create_session,
    finish,
    get_session,
    record_item,
)
from app.interview.summary import build_summary


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _default_ask_fn() -> str:
    lines = []
    try:
        for line in sys.stdin:
            if line.strip() == ".":
                break
            lines.append(line)
    except EOFError:
        pass
    return "".join(lines)


def print_interview_summary(
    summary: dict[str, Any],
    out: Callable[[str], None] = print,
) -> None:
    """Format and print an interview summary."""
    avg = summary.get("average_score")
    avg_str = f"{avg:.2f}/4" if avg is not None else "N/A"
    out("\n=== Interview Summary ===")
    out(f"Average score: {avg_str} ({summary.get('answered', 0)} answered, {summary.get('skipped', 0)} skipped)")

    rows = summary.get("rows", [])
    if rows:
        out("\nQuestions:")
        for idx, r in enumerate(rows, start=1):
            r_round = _get_val(r, "round") or "unknown"
            r_diff = _get_val(r, "difficulty") or "?"
            r_concept = _get_val(r, "concept") or "N/A"
            r_score = _get_val(r, "score")
            sc_str = f"{r_score}/4" if r_score is not None else "skipped"
            out(f"  {idx}. [{r_round}] (diff {r_diff}) {r_concept}: {sc_str}")

    weakest = summary.get("weakest_concepts", [])
    if weakest:
        out("\nWeakest concepts:")
        for wc in weakest:
            out(f"  - {wc}")

    revise = summary.get("revise", [])
    if revise:
        out("\nTopics to revise:")
        for rev in revise:
            pt = rev.get("point", "")
            kind = rev.get("kind", "general")
            concept = rev.get("concept", "")
            concept_str = f" ({concept})" if concept else ""
            out(f"  - [{kind}]{concept_str} {pt}")


async def run_interview(
    db: Any,
    project_id: str,
    n: int = 5,
    base_difficulty: int = 2,
    session_id: str | None = None,
    ask_fn: Callable[..., Any] | None = None,
    out: Callable[[str], None] = print,
    generate_fn: Callable[..., Any] | None = None,
    evaluate_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute an interview session."""
    # 1. Create or load the session
    if session_id is not None:
        session = await get_session(db, session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found.")
        if session.get("project_id") != project_id:
            raise ValueError(
                f"Session '{session_id}' belongs to project '{session.get('project_id')}', not '{project_id}'."
            )
    else:
        session = await create_session(db, project_id, n=n, base_difficulty=base_difficulty)

    plan = session.get("plan", [])
    _gen = generate_fn if generate_fn is not None else generate_question
    _eval = evaluate_fn if evaluate_fn is not None else evaluate_answer

    # 2. Main slot loop
    while session.get("current", 0) < len(plan):
        idx = session["current"]
        slot = plan[idx]
        slot_num = idx + 1
        r = slot.get("round", "")
        d = slot.get("difficulty", 1)

        out(f"\nQuestion {slot_num} of {len(plan)}, {r}, difficulty {d}")

        # Generate question with single retry on InterviewGenerationError
        q = None
        try:
            q = await _gen(db, project_id, round_name=r, difficulty=d, save=True)
        except InterviewGenerationError:
            try:
                q = await _gen(db, project_id, round_name=r, difficulty=d, save=True)
            except InterviewGenerationError as exc:
                item = {
                    "question_id": None,
                    "attempt_id": None,
                    "status": "no_question",
                    "score": None,
                }
                await record_item(
                    db,
                    session["session_id"],
                    item,
                    note=f"Generation failed for slot {slot_num}: {exc}",
                )
                await advance(db, session["session_id"])
                session["current"] += 1
                session.setdefault("items", []).append(item)
                out(f"Could not generate question for slot {slot_num}.")
                continue

        question_text = _get_val(q, "question")
        qid = _get_val(q, "question_id")

        # Print the question text only (not the rubric)
        out(f"\n{question_text}\n")

        # Read the candidate's answer
        if ask_fn is not None:
            ans_res = ask_fn()
            if inspect.isawaitable(ans_res):
                ans = await ans_res
            else:
                ans = ans_res
        else:
            ans = await asyncio.to_thread(_default_ask_fn)

        first_line = ans.strip().splitlines()[0].strip() if ans.strip().splitlines() else ans.strip()

        if first_line == "/quit":
            out("Session saved. You can resume later.")
            return session

        if first_line == "/skip":
            item = {
                "question_id": qid,
                "attempt_id": None,
                "status": "skipped",
                "score": None,
            }
            await record_item(db, session["session_id"], item)
            await advance(db, session["session_id"])
            session["current"] += 1
            session.setdefault("items", []).append(item)
            out("Question skipped.")
            continue

        # Evaluate the answer
        try:
            attempt = await _eval(
                db,
                question_id=qid,
                answer=ans,
                save=True,
                session_id=session["session_id"],
            )
        except TypeError:
            attempt = await _eval(
                db,
                question_id=qid,
                answer=ans,
                save=True,
            )

        ev = _get_val(attempt, "evaluation")
        score = _get_val(ev, "score") if ev else None
        out(f"\nScore: {score}/4")

        unverified = _get_val(ev, "unverified") or []
        if unverified:
            out("\nCheck these against your repo:")
            for u in unverified:
                u_q = _get_val(u, "quote")
                u_w = _get_val(u, "why")
                out(f'  - "{u_q}": {u_w}')

        pts = _get_val(ev, "points") or []
        missed = [p for p in pts if _get_val(p, "status") in ("missing", "partial")]
        if missed:
            out("Missed / partial:")
            for p in missed:
                p_text = _get_val(p, "point")
                p_kind = _get_val(p, "kind")
                out(f"  [{p_kind}] {p_text}")

        misconceptions = _get_val(ev, "misconceptions") or []
        if misconceptions:
            out("Misconceptions:")
            for m in misconceptions:
                quote = _get_val(m, "quote")
                why = _get_val(m, "why")
                s_line = _get_val(m, "seed_line")
                contra = f" (contradicts repo seed line {s_line})" if s_line is not None else ""
                out(f'  - "{quote}": {why}{contra}')

        advice = _get_val(ev, "advice")
        if advice:
            out(f"Advice: {advice}")

        attempt_id = _get_val(attempt, "attempt_id")
        item = {
            "question_id": qid,
            "attempt_id": attempt_id,
            "status": "answered",
            "score": score,
        }
        await record_item(db, session["session_id"], item)
        await advance(db, session["session_id"])
        session["current"] += 1
        session.setdefault("items", []).append(item)

    # 3. Finish session and print summary
    await finish(db, session["session_id"])
    session["status"] = "finished"

    # Fetch questions and attempts to build summary
    items = session.get("items", [])
    q_ids = [it["question_id"] for it in items if it.get("question_id")]
    a_ids = [it["attempt_id"] for it in items if it.get("attempt_id")]

    questions: list[Any] = []
    if q_ids:
        q_cur = db.interview_questions.find({"question_id": {"$in": q_ids}})
        if hasattr(q_cur, "to_list"):
            questions = await q_cur.to_list(len(q_ids))
        elif inspect.isawaitable(q_cur):
            questions = list(await q_cur)
        else:
            questions = list(q_cur)

    attempts: list[Any] = []
    if a_ids:
        a_cur = db.interview_attempts.find({"attempt_id": {"$in": a_ids}})
        if hasattr(a_cur, "to_list"):
            attempts = await a_cur.to_list(len(a_ids))
        elif inspect.isawaitable(a_cur):
            attempts = list(await a_cur)
        else:
            attempts = list(a_cur)

    summary = build_summary(items, questions, attempts)
    print_interview_summary(summary, out=out)
    return session
