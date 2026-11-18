"""Interview session runner.

Executes a multi-question interview session, recording items, handling /skip and /quit,
and generating the final summary.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from typing import Any, Callable

_IMPORT_USAGE_RE = re.compile(r"\b(imports?|imported|used in|used across)\b", re.I)
_FILENAME_RE = re.compile(r"\b[\w/.-]+\.(?:py|js|ts|md|toml|ya?ml|json)\b", re.I)

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

    lessons_given = summary.get("lessons_given", 0)
    if lessons_given:
        out(f"Lessons given: {lessons_given}")

    before_after = summary.get("before_after", [])
    if before_after:
        out("\nTeaching cycle (before -> after):")
        for ba in before_after:
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
    lessons_given = 0

    async def _read_ans() -> str:
        if ask_fn is not None:
            ans_res = ask_fn()
            if inspect.isawaitable(ans_res):
                return await ans_res
            return ans_res
        return await asyncio.to_thread(_default_ask_fn)

    # 2. Main slot loop
    while session.get("current", 0) < len(plan):
        idx = session["current"]
        slot = plan[idx]
        slot_num = idx + 1
        r = slot.get("round", "")
        d = slot.get("difficulty", 1)

        out(f"\nQuestion {slot_num} of {len(plan)}, {r}, difficulty {d}")

        # Generate question with single retry on InterviewGenerationError
        async def _call_gen(*a, **k):
            res = _gen(*a, **k)
            return (await res) if inspect.isawaitable(res) else res

        q = None
        try:
            q = await _call_gen(db, project_id, round_name=r, difficulty=d, save=True)
        except InterviewGenerationError:
            try:
                q = await _call_gen(db, project_id, round_name=r, difficulty=d, save=True)
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
        ans = await _read_ans()
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
            att_res = _eval(
                db,
                question_id=qid,
                answer=ans,
                save=True,
                session_id=session["session_id"],
                role="initial",
            )
        except TypeError:
            try:
                att_res = _eval(
                    db,
                    question_id=qid,
                    answer=ans,
                    save=True,
                    session_id=session["session_id"],
                )
            except TypeError:
                att_res = _eval(
                    db,
                    question_id=qid,
                    answer=ans,
                    save=True,
                )
        attempt = (await att_res) if inspect.isawaitable(att_res) else att_res

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

        # Teaching cycle if score <= teach_threshold, teach is True, and lessons_given < max_lessons
        if score is not None and score <= teach_threshold and teach and lessons_given < max_lessons:
            # a. lesson = generate_lesson(attempt) and print it. If it fails, record cycle_note and continue to the next question.
            lesson_doc = None
            try:
                if lesson_fn is not None:
                    try:
                        lesson_res = lesson_fn(attempt)
                    except TypeError:
                        lesson_res = lesson_fn(db, attempt_id=attempt_id)
                else:
                    from app.interview.lesson import generate_lesson
                    lesson_res = generate_lesson(db, attempt_id=attempt_id, project_id=project_id)
                if inspect.isawaitable(lesson_res):
                    lesson_doc = await lesson_res
                else:
                    lesson_doc = lesson_res
            except Exception as exc:
                item["cycle_note"] = f"lesson failed: {exc}"
                await record_item(db, session["session_id"], item)
                await advance(db, session["session_id"])
                session["current"] += 1
                session.setdefault("items", []).append(item)
                continue

            lesson_text = _get_val(lesson_doc, "text") or ""
            out(f"\n{lesson_text}\n")
            item["lesson_id"] = _get_val(lesson_doc, "lesson_id")

            # Filter focus points: missed or partial rubric points that are not import lists
            focus_points = [
                _get_val(p, "point")
                for p in missed
                if not (
                    _IMPORT_USAGE_RE.search(_get_val(p, "point") or "")
                    and _FILENAME_RE.search(_get_val(p, "point") or "")
                )
            ]

            # b. follow-up
            _gen_fu = generate_followup_fn if generate_followup_fn is not None else _gen
            followup_q = None
            try:
                fu_res = _gen_fu(
                    db,
                    project_id,
                    round_name=r,
                    difficulty=max(1, d - 1),
                    seed=_get_val(q, "seed"),
                    focus_points=focus_points,
                    avoid_question=question_text,
                    parent_question_id=qid,
                    concept=_get_val(q, "concept"),
                    save=True,
                )
                if inspect.isawaitable(fu_res):
                    followup_q = await fu_res
                else:
                    followup_q = fu_res
            except InterviewGenerationError:
                item["cycle_note"] = "no follow-up question"
                followup_q = None

            if followup_q is not None:
                fu_qid = _get_val(followup_q, "question_id")
                fu_qtext = _get_val(followup_q, "question")
                item["followup_question_id"] = fu_qid
                out(f"\n{fu_qtext}\n")

                fu_ans = await _read_ans()
                fu_first = fu_ans.strip().splitlines()[0].strip() if fu_ans.strip().splitlines() else fu_ans.strip()

                if fu_first == "/quit":
                    item["cycle_note"] = "cycle interrupted"
                    await record_item(db, session["session_id"], item)
                    await advance(db, session["session_id"])
                    session["current"] += 1
                    session.setdefault("items", []).append(item)
                    out("Session saved. You can resume later.")
                    return session

                if fu_first == "/skip":
                    out("Question skipped.")
                else:
                    try:
                        fu_att_res = _eval(
                            db,
                            question_id=fu_qid,
                            answer=fu_ans,
                            save=True,
                            session_id=session["session_id"],
                            role="followup",
                            parent_attempt_id=attempt_id,
                        )
                    except TypeError:
                        fu_att_res = _eval(
                            db,
                            question_id=fu_qid,
                            answer=fu_ans,
                            save=True,
                            role="followup",
                            parent_attempt_id=attempt_id,
                        )
                    fu_attempt = (await fu_att_res) if inspect.isawaitable(fu_att_res) else fu_att_res
                    item["followup_attempt_id"] = _get_val(fu_attempt, "attempt_id")

                    fu_ev = _get_val(fu_attempt, "evaluation")
                    fu_score = _get_val(fu_ev, "score") if fu_ev else None
                    out(f"\nScore: {fu_score}/4")
                    fu_pts = _get_val(fu_ev, "points") or []
                    fu_missed = [p for p in fu_pts if _get_val(p, "status") in ("missing", "partial")]
                    if fu_missed:
                        out("Missed / partial:")
                        for p in fu_missed:
                            out(f"  [{_get_val(p, 'kind')}] {_get_val(p, 'point')}")

            # c. retry: print "Now the original question again:" and the original question text
            out(f"\nNow the original question again:\n{question_text}\n")
            ret_ans = await _read_ans()
            ret_first = ret_ans.strip().splitlines()[0].strip() if ret_ans.strip().splitlines() else ret_ans.strip()

            if ret_first == "/quit":
                item["cycle_note"] = "cycle interrupted"
                await record_item(db, session["session_id"], item)
                await advance(db, session["session_id"])
                session["current"] += 1
                session.setdefault("items", []).append(item)
                out("Session saved. You can resume later.")
                return session

            if ret_first == "/skip":
                out("Question skipped.")
            else:
                try:
                    ret_att_res = _eval(
                        db,
                        question_id=qid,
                        answer=ret_ans,
                        save=True,
                        session_id=session["session_id"],
                        role="retry",
                        parent_attempt_id=attempt_id,
                    )
                except TypeError:
                    ret_att_res = _eval(
                        db,
                        question_id=qid,
                        answer=ret_ans,
                        save=True,
                        role="retry",
                        parent_attempt_id=attempt_id,
                    )
                retry_attempt = (await ret_att_res) if inspect.isawaitable(ret_att_res) else ret_att_res
                item["retry_attempt_id"] = _get_val(retry_attempt, "attempt_id")

                retry_ev = _get_val(retry_attempt, "evaluation")
                after_score = _get_val(retry_ev, "score") if retry_ev else 0
                diff = after_score - (score or 0)
                diff_str = f"+{diff}" if diff >= 0 else f"{diff}"
                out(f"Before: {score}/4 -> After: {after_score}/4 ({diff_str})")

                retry_pts = _get_val(retry_ev, "points") or []
                retry_missed = [p for p in retry_pts if _get_val(p, "status") in ("missing", "partial")]
                if retry_missed:
                    out("Still missing:")
                    for p in retry_missed:
                        out(f"  [{_get_val(p, 'kind')}] {_get_val(p, 'point')}")

                # d. record before and after, call record_teaching, store all ids in the item, and count one lesson.
                item["before"] = score
                item["after"] = after_score
                try:
                    from app.interview.profile import record_teaching
                    rec_res = record_teaching(db, project_id, _get_val(q, "concept"), score, after_score)
                    if inspect.isawaitable(rec_res):
                        await rec_res
                except Exception:
                    pass

            lessons_given += 1

        await record_item(db, session["session_id"], item)
        await advance(db, session["session_id"])
        session["current"] += 1
        session.setdefault("items", []).append(item)

    # 3. Finish session and print summary
    await finish(db, session["session_id"])
    session["status"] = "finished"

    # Fetch questions and attempts to build summary
    items = session.get("items", [])
    q_ids_set: set[str] = set()
    a_ids_set: set[str] = set()
    for it in items:
        for k in ("question_id", "followup_question_id"):
            val = it.get(k)
            if val:
                q_ids_set.add(val)
        for k in ("attempt_id", "followup_attempt_id", "retry_attempt_id"):
            val = it.get(k)
            if val:
                a_ids_set.add(val)

    q_ids = list(q_ids_set)
    a_ids = list(a_ids_set)

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
