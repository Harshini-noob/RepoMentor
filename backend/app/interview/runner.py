"""Interview session runner.

Executes a multi-question interview session, recording items, handling /skip and /quit,
and generating the final summary. Refactored to be a thin loop over app.interview.engine.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Any, Callable

from app.interview.engine import (
    EngineStateError,
    get_state,
    next_action,
    skip,
    start,
    submit_answer,
)
from app.interview.evaluate import evaluate_answer
from app.interview.filters import is_listing_point
from app.interview.generate import InterviewGenerationError, generate_question
from app.interview.seeds import find_seed_for_concept
from app.interview.session import (
    ROUNDS_ORDER,
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

    lessons_given = summary.get("lessons_given", 0)
    if lessons_given:
        out(f"Lessons given: {lessons_given}")

    before_after = summary.get("before_after", [])
    if before_after:
        out("\nTeaching cycle (before -> after):")
        for ba in before_after:
            if ba.get("copied"):
                out(f"  [{ba['concept']}] {ba['before']}/4 -> n/a (copied)")
                continue
            delta = ba.get("delta", 0)
            diff_str = f"+{delta}" if delta >= 0 else f"{delta}"
            out(f"  [{ba['concept']}] {ba['before']}/4 -> {ba['after']}/4 ({diff_str})")
        avg_imp = summary.get("average_improvement")
        if avg_imp is not None:
            imp_str = f"+{avg_imp:.2f}" if avg_imp >= 0 else f"{avg_imp:.2f}"
            out(f"Average improvement: {imp_str}")

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
    teach: bool = True,
    teach_threshold: int = 2,
    max_lessons: int = 2,
    generate_followup_fn: Callable[..., Any] | None = None,
    lesson_fn: Callable[..., Any] | None = None,
    adaptive: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute an interview session as a thin presentation loop over app.interview.engine."""
    teach_below = kwargs.get("teach_below", teach_threshold)

    # 1. Create or load the session
    if session_id is not None:
        session = await get_session(db, session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found.")
        if session.get("project_id") != project_id:
            raise ValueError(
                f"Session '{session_id}' belongs to project '{session.get('project_id')}', not '{project_id}'."
            )
        # Ensure engine fields exist if resuming an older session
        updates = {}
        if "stage" not in session:
            updates["stage"] = "need_question"
        if "teach" not in session:
            updates["teach"] = teach
        if "teach_below" not in session:
            updates["teach_below"] = teach_below
        if "max_lessons" not in session:
            updates["max_lessons"] = max_lessons
        if "lessons_given" not in session:
            updates["lessons_given"] = 0
        if updates:
            res = db.interview_sessions.update_one(
                {"session_id": session_id},
                {"$set": updates},
            )
            if inspect.isawaitable(res):
                await res
    else:
        session = await start(
            db,
            project_id,
            questions=n,
            difficulty=base_difficulty,
            adaptive=adaptive,
            teach=teach,
            teach_below=teach_below,
            max_lessons=max_lessons,
        )
        session_id = session["session_id"]

    async def _read_ans() -> str:
        if ask_fn is not None:
            ans_res = ask_fn()
            if inspect.isawaitable(ans_res):
                return await ans_res
            return ans_res
        return await asyncio.to_thread(_default_ask_fn)

    # 2. Main presentation loop
    while True:
        action = await next_action(
            db,
            session_id,
            generate_fn=generate_fn,
            lesson_fn=lesson_fn,
            generate_followup_fn=generate_followup_fn,
        )
        act_type = action.get("action")

        if act_type == "finished":
            print_interview_summary(action["summary"], out=out)
            latest = await get_session(db, session_id)
            return latest if latest is not None else session

        if act_type == "lesson":
            lesson_text = action.get("text", "")
            out(f"\n{lesson_text}\n")
            continue

        if act_type == "question":
            kind = action.get("kind", "initial")
            slot_num = action.get("slot")
            total = action.get("total")
            r = action.get("round")
            d = action.get("difficulty")
            text = action.get("text")
            qid = action.get("question_id")

            if kind == "initial":
                out(f"\nQuestion {slot_num} of {total}, {r}, difficulty {d}")
                out(f"\n{text}\n")
            elif kind == "followup":
                out(f"\n{text}\n")
            elif kind == "retry":
                out(f"\nNow the original question again:\n{text}\n")

            ans = await _read_ans()
            first_line = ans.strip().splitlines()[0].strip() if ans.strip().splitlines() else ans.strip()

            if first_line == "/quit":
                s_cur = await get_session(db, session_id)
                cycle = (s_cur or {}).get("cycle")
                if cycle and kind in ("followup", "retry"):
                    item = {
                        "question_id": cycle.get("original_question_id"),
                        "attempt_id": cycle.get("initial_attempt_id"),
                        "status": "answered",
                        "score": cycle.get("before"),
                        "lesson_id": cycle.get("lesson_id"),
                        "followup_question_id": cycle.get("followup_question_id"),
                        "followup_attempt_id": cycle.get("followup_attempt_id"),
                        "cycle_note": "cycle interrupted",
                    }
                    await record_item(db, session_id, item)
                    await advance(db, session_id)
                    res_up = db.interview_sessions.update_one(
                        {"session_id": session_id},
                        {"$set": {"cycle": None, "pending": None, "stage": "need_question"}},
                    )
                    if inspect.isawaitable(res_up):
                        await res_up
                out("Session saved. You can resume later.")
                latest = await get_session(db, session_id)
                return latest if latest is not None else session

            if first_line == "/skip":
                await skip(db, session_id)
                out("Question skipped.")
                continue

            # Check if rubric mismatch on followup question:
            s_cur = await get_session(db, session_id)
            cycle = (s_cur or {}).get("cycle") or {}
            fu_val_notes = cycle.get("followup_validation_notes") or []
            if not fu_val_notes:
                q_doc = db.interview_questions.find_one({"question_id": qid})
                if inspect.isawaitable(q_doc):
                    q_doc = await q_doc
                fu_val_notes = _get_val(q_doc, "validation_notes") or []

            rubric_mismatch = (
                kind == "followup"
                and "rubric may not match the question" in fu_val_notes
            )
            if rubric_mismatch:
                await skip(db, session_id)
                out("Follow-up skipped: rubric did not match the question")
                continue

            eval_res = await submit_answer(
                db,
                session_id,
                question_id=qid,
                answer=ans,
                evaluate_fn=evaluate_fn,
            )

            role = eval_res.get("role")
            if role == "retry":
                before = eval_res.get("before")
                after = eval_res.get("after")
                if eval_res.get("copied", False):
                    out(f"Before: {before}/4 -> After: copied from the lesson (not counted)")
                else:
                    diff = (after or 0) - (before or 0)
                    diff_str = f"+{diff}" if diff >= 0 else f"{diff}"
                    out(f"Before: {before}/4 -> After: {after}/4 ({diff_str})")

                pts = eval_res.get("points") or []
                still_missed = [p for p in pts if p.get("status") in ("missing", "partial")]
                if still_missed:
                    out("Still missing:")
                    for p in still_missed:
                        out(f"  [{p.get('kind')}] {p.get('point')}")
            else:
                score = eval_res.get("score")
                out(f"\nScore: {score}/4")

                unverified = eval_res.get("unverified") or []
                if unverified:
                    out("\nCheck these against your repo:")
                    for u in unverified:
                        out(f'  - "{u.get("quote")}": {u.get("why")}')

                pts = eval_res.get("points") or []
                missed = [p for p in pts if p.get("status") in ("missing", "partial")]
                if missed:
                    out("Missed / partial:")
                    for p in missed:
                        out(f"  [{p.get('kind')}] {p.get('point')}")

                misconceptions = eval_res.get("misconceptions") or []
                if misconceptions:
                    out("Misconceptions:")
                    for m in misconceptions:
                        contra = (
                            f" (contradicts repo seed line {m.get('seed_line')})"
                            if m.get("seed_line") is not None
                            else ""
                        )
                        out(f'  - "{m.get("quote")}": {m.get("why")}{contra}')

                advice = eval_res.get("advice")
                if advice:
                    out(f"Advice: {advice}")
