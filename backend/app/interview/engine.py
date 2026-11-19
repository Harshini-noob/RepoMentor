"""Interview state machine engine.

Encapsulates all interview progression, teaching cycle states, and answer evaluations
as a pure, non-printing state machine.
"""

from __future__ import annotations

import inspect
import sys
from typing import Any, Callable

from app.interview.evaluate import evaluate_answer
from app.interview.filters import is_listing_point
from app.interview.generate import InterviewGenerationError, generate_question
from app.interview.lesson import generate_lesson
from app.interview.seeds import find_seed_for_concept as _seeds_find_seed
from app.interview.session import (
    ROUNDS_ORDER,
    advance,
    create_session,
    finish,
    get_session,
    record_item,
)
from app.interview.summary import build_summary


class EngineStateError(RuntimeError):
    """Raised when an operation is invalid for the current engine state."""


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _find_seed_fn() -> Callable[..., Any]:
    runner_mod = sys.modules.get("app.interview.runner")
    if runner_mod and hasattr(runner_mod, "find_seed_for_concept"):
        return getattr(runner_mod, "find_seed_for_concept")
    return _seeds_find_seed


async def _update_session(db: Any, session_id: str, updates: dict[str, Any]) -> None:
    res = db.interview_sessions.update_one(
        {"session_id": session_id},
        {"$set": updates},
    )
    if inspect.isawaitable(res):
        await res


async def _build_summary_for_session(db: Any, session: dict[str, Any]) -> dict[str, Any]:
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

    return build_summary(items, questions, attempts)


async def start(
    db: Any,
    project_id: str,
    questions: int = 5,
    difficulty: int = 2,
    adaptive: bool = True,
    teach: bool = True,
    teach_below: int = 2,
    max_lessons: int = 2,
) -> dict[str, Any]:
    """Start a new interview session."""
    return await create_session(
        db,
        project_id,
        n=questions,
        base_difficulty=difficulty,
        adaptive=adaptive,
        teach=teach,
        teach_below=teach_below,
        max_lessons=max_lessons,
    )


async def next_action(
    db: Any,
    session_id: str,
    generate_fn: Callable[..., Any] | None = None,
    lesson_fn: Callable[..., Any] | None = None,
    generate_followup_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Return the next action required for the session."""
    session = await get_session(db, session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found.")

    stage = session.get("stage", "need_question")
    project_id = session.get("project_id", "")
    plan = session.get("plan", [])

    # 1. Finished stage
    if stage == "finished":
        if session.get("status") != "finished":
            await finish(db, session_id)
            session["status"] = "finished"
        summary = await _build_summary_for_session(db, session)
        return {"action": "finished", "summary": summary}

    # 2. Awaiting answer stage: return the pending question without calling model
    if stage == "awaiting_answer":
        pending = session.get("pending")
        if not pending or not pending.get("question_id"):
            raise EngineStateError("In awaiting_answer stage, but no pending question is stored.")
        qid = pending["question_id"]
        q_doc = db.interview_questions.find_one({"question_id": qid})
        if inspect.isawaitable(q_doc):
            q_doc = await q_doc

        idx = session.get("current", 0)
        slot_info = plan[idx] if idx < len(plan) else {}
        return {
            "action": "question",
            "kind": pending.get("kind", "initial"),
            "question_id": qid,
            "text": _get_val(q_doc, "question", ""),
            "slot": idx + 1,
            "total": len(plan),
            "round": _get_val(q_doc, "round") or slot_info.get("round", ""),
            "difficulty": _get_val(q_doc, "difficulty") or slot_info.get("difficulty", 1),
            "concept": _get_val(q_doc, "concept") or slot_info.get("concept", ""),
            "reason": slot_info.get("reason", ""),
        }

    # 3. Need lesson stage
    if stage == "need_lesson":
        cycle = session.get("cycle") or {}
        initial_aid = cycle.get("initial_attempt_id")
        initial_att = db.interview_attempts.find_one({"attempt_id": initial_aid})
        if inspect.isawaitable(initial_att):
            initial_att = await initial_att

        lesson_doc = None
        _lesson_gen = lesson_fn if lesson_fn is not None else generate_lesson
        try:
            try:
                lesson_res = _lesson_gen(db, attempt_id=initial_aid, project_id=project_id)
            except TypeError:
                try:
                    lesson_res = _lesson_gen(db, project_id, initial_aid)
                except TypeError:
                    lesson_res = _lesson_gen(initial_att)
            if inspect.isawaitable(lesson_res):
                lesson_doc = await lesson_res
            else:
                lesson_doc = lesson_res
        except Exception as exc:
            # Lesson failed: record item and advance to next question
            item = {
                "question_id": cycle.get("original_question_id"),
                "attempt_id": initial_aid,
                "status": "answered",
                "score": cycle.get("before"),
                "cycle_note": f"lesson failed: {exc}",
            }
            await record_item(db, session_id, item)
            await advance(db, session_id)
            session["current"] += 1
            is_finished = session["current"] >= len(plan)
            new_stage = "finished" if is_finished else "need_question"
            await _update_session(db, session_id, {
                "stage": new_stage,
                "cycle": None,
                "pending": None,
            })
            if is_finished:
                await finish(db, session_id)
                summary = await _build_summary_for_session(db, session)
                return {"action": "finished", "summary": summary}
            return await next_action(
                db, session_id,
                generate_fn=generate_fn,
                lesson_fn=lesson_fn,
                generate_followup_fn=generate_followup_fn,
            )

        lesson_id = _get_val(lesson_doc, "lesson_id")
        cycle["lesson_id"] = lesson_id
        await _update_session(db, session_id, {
            "stage": "need_followup",
            "cycle": cycle,
        })
        return {
            "action": "lesson",
            "text": _get_val(lesson_doc, "text", ""),
            "notes": _get_val(lesson_doc, "notes", []),
            "concept": _get_val(lesson_doc, "concept", "") or _get_val(initial_att, "concept", ""),
        }

    # 4. Need follow-up stage
    if stage == "need_followup":
        cycle = session.get("cycle") or {}
        original_qid = cycle.get("original_question_id")
        initial_aid = cycle.get("initial_attempt_id")

        orig_q = db.interview_questions.find_one({"question_id": original_qid})
        if inspect.isawaitable(orig_q):
            orig_q = await orig_q

        initial_att = db.interview_attempts.find_one({"attempt_id": initial_aid})
        if inspect.isawaitable(initial_att):
            initial_att = await initial_att

        idx = session.get("current", 0)
        slot_info = plan[idx] if idx < len(plan) else {}
        r = _get_val(orig_q, "round") or slot_info.get("round", "tech")
        d = _get_val(orig_q, "difficulty") or slot_info.get("difficulty", 2)
        question_text = _get_val(orig_q, "question", "")
        orig_concept = _get_val(orig_q, "concept") or slot_info.get("concept", "")

        ev = _get_val(initial_att, "evaluation")
        pts = _get_val(ev, "points") or []
        missed = [p for p in pts if _get_val(p, "status") in ("missing", "partial")]

        focus_point = next(
            (
                _get_val(point, "point")
                for point in missed
                if _get_val(point, "kind") == "repo"
                and _get_val(point, "point")
                and not is_listing_point(_get_val(point, "point") or "")
            ),
            None,
        )
        if focus_point is None and missed:
            focus_point = _get_val(missed[0], "point")
        focus_points = [focus_point] if focus_point else []

        _gen_fu = generate_followup_fn if generate_followup_fn is not None else generate_fn if generate_fn is not None else generate_question
        followup_q = None
        try:
            fu_kwargs: dict[str, Any] = {
                "round_name": r,
                "difficulty": max(1, d - 1),
                "seed": _get_val(orig_q, "seed"),
                "focus_points": focus_points,
                "avoid_question": question_text,
                "parent_question_id": original_qid,
                "concept": orig_concept,
                "save": True,
            }
            try:
                fu_res = _gen_fu(db, project_id, **fu_kwargs)
            except TypeError:
                fu_res = _gen_fu(db, project_id, round_name=r, difficulty=max(1, d - 1))
            if inspect.isawaitable(fu_res):
                followup_q = await fu_res
            else:
                followup_q = fu_res
        except Exception:
            followup_q = None

        if followup_q is None:
            cycle["cycle_note"] = "no follow-up question"
            stage = "need_retry"
            await _update_session(db, session_id, {"stage": "need_retry", "cycle": cycle})
            # Proceed directly to need_retry
            pending = {"kind": "retry", "question_id": original_qid}
            await _update_session(db, session_id, {"stage": "awaiting_answer", "pending": pending})
            return {
                "action": "question",
                "kind": "retry",
                "question_id": original_qid,
                "text": question_text,
                "slot": idx + 1,
                "total": len(plan),
                "round": r,
                "difficulty": d,
                "concept": orig_concept,
                "reason": slot_info.get("reason", ""),
            }

        fu_qid = _get_val(followup_q, "question_id")
        fu_text = _get_val(followup_q, "question")
        cycle["followup_question_id"] = fu_qid
        cycle["followup_validation_notes"] = _get_val(followup_q, "validation_notes") or []
        pending = {"kind": "followup", "question_id": fu_qid}
        await _update_session(db, session_id, {
            "stage": "awaiting_answer",
            "pending": pending,
            "cycle": cycle,
        })
        return {
            "action": "question",
            "kind": "followup",
            "question_id": fu_qid,
            "text": fu_text,
            "slot": idx + 1,
            "total": len(plan),
            "round": r,
            "difficulty": max(1, d - 1),
            "concept": orig_concept,
            "reason": slot_info.get("reason", ""),
        }

    # 5. Need retry stage
    if stage == "need_retry":
        cycle = session.get("cycle") or {}
        original_qid = cycle.get("original_question_id")
        orig_q = db.interview_questions.find_one({"question_id": original_qid})
        if inspect.isawaitable(orig_q):
            orig_q = await orig_q

        idx = session.get("current", 0)
        slot_info = plan[idx] if idx < len(plan) else {}
        pending = {"kind": "retry", "question_id": original_qid}
        await _update_session(db, session_id, {
            "stage": "awaiting_answer",
            "pending": pending,
        })
        return {
            "action": "question",
            "kind": "retry",
            "question_id": original_qid,
            "text": _get_val(orig_q, "question", ""),
            "slot": idx + 1,
            "total": len(plan),
            "round": _get_val(orig_q, "round") or slot_info.get("round", ""),
            "difficulty": _get_val(orig_q, "difficulty") or slot_info.get("difficulty", 1),
            "concept": _get_val(orig_q, "concept") or slot_info.get("concept", ""),
            "reason": slot_info.get("reason", ""),
        }

    # 6. Need question stage
    if stage == "need_question":
        _gen = generate_fn if generate_fn is not None else generate_question
        find_seed = _find_seed_fn()

        async def _call_gen(*a, **k):
            try:
                res = _gen(*a, **k)
            except TypeError as te:
                if "unexpected keyword argument" in str(te):
                    res = _gen(a[0], a[1], round_name=k.get("round_name"), difficulty=k.get("difficulty"))
                else:
                    raise
            return (await res) if inspect.isawaitable(res) else res

        while session.get("current", 0) < len(plan):
            idx = session["current"]
            slot = plan[idx]
            slot_num = idx + 1
            concept = slot.get("concept")
            r = slot.get("round")
            d = slot.get("difficulty", 1)

            seed = None
            gen_concept = None
            if concept:
                raw_dossier = db.dossiers.find_one({"project_id": project_id}, {"_id": 0})
                dossier = await raw_dossier if inspect.isawaitable(raw_dossier) else raw_dossier

                asked: list[dict] = []
                cur = db.interview_questions.find({"project_id": project_id}, {"_id": 0})
                if hasattr(cur, "to_list"):
                    asked = await cur.to_list(None)
                elif inspect.isawaitable(cur):
                    asked = list(await cur)
                else:
                    asked = list(cur)

                seed_result = find_seed(dossier, concept, asked) if dossier else None
                if seed_result is not None:
                    seed, seed_round = seed_result
                    r = seed_round
                    gen_concept = concept
                else:
                    if not r:
                        r = ROUNDS_ORDER[idx % len(ROUNDS_ORDER)]
                    note = f"no seed for {concept}, used round order"
                    session.setdefault("notes", []).append(note)
                    note_res = db.interview_sessions.update_one(
                        {"session_id": session_id},
                        {"$push": {"notes": note}},
                    )
                    if inspect.isawaitable(note_res):
                        await note_res
            else:
                if not r:
                    r = ROUNDS_ORDER[idx % len(ROUNDS_ORDER)]

            gen_kwargs = {
                "round_name": r,
                "difficulty": d,
                "save": True,
            }
            if seed is not None:
                gen_kwargs["seed"] = seed
            if gen_concept is not None:
                gen_kwargs["concept"] = gen_concept

            q = None
            try:
                q = await _call_gen(db, project_id, **gen_kwargs)
            except InterviewGenerationError:
                try:
                    q = await _call_gen(db, project_id, **gen_kwargs)
                except InterviewGenerationError as exc:
                    item = {
                        "question_id": None,
                        "attempt_id": None,
                        "status": "no_question",
                        "score": None,
                    }
                    await record_item(
                        db,
                        session_id,
                        item,
                        note=f"Generation failed for slot {slot_num}: {exc}",
                    )
                    await advance(db, session_id)
                    session["current"] += 1
                    session.setdefault("items", []).append(item)
                    continue

            qid = _get_val(q, "question_id")
            question_text = _get_val(q, "question")
            q_concept = _get_val(q, "concept") or concept or ""
            q_round = _get_val(q, "round") or r

            pending = {"kind": "initial", "question_id": qid}
            await _update_session(db, session_id, {
                "stage": "awaiting_answer",
                "pending": pending,
            })
            return {
                "action": "question",
                "kind": "initial",
                "question_id": qid,
                "text": question_text,
                "slot": slot_num,
                "total": len(plan),
                "round": q_round,
                "difficulty": d,
                "concept": q_concept,
                "reason": slot.get("reason", ""),
            }

        # Plan exhausted
        await _update_session(db, session_id, {"stage": "finished", "pending": None})
        session["stage"] = "finished"
        await finish(db, session_id)
        session["status"] = "finished"
        summary = await _build_summary_for_session(db, session)
        return {"action": "finished", "summary": summary}

    raise EngineStateError(f"Unknown engine stage: '{stage}'")


async def submit_answer(
    db: Any,
    session_id: str,
    question_id: str,
    answer: str,
    evaluate_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Submit an answer to the current pending question."""
    session = await get_session(db, session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found.")

    if session.get("stage") != "awaiting_answer":
        raise EngineStateError(f"Cannot submit answer in stage '{session.get('stage')}'.")

    pending = session.get("pending")
    if not pending or pending.get("question_id") != question_id:
        expected_id = pending.get("question_id") if pending else None
        raise EngineStateError(
            f"Question ID '{question_id}' does not match pending question '{expected_id}'."
        )

    kind = pending.get("kind", "initial")
    cycle = session.get("cycle") or {}
    project_id = session.get("project_id", "")
    plan = session.get("plan", [])

    role = kind
    parent_attempt_id = cycle.get("initial_attempt_id") if kind in ("followup", "retry") else None

    _eval = evaluate_fn if evaluate_fn is not None else evaluate_answer
    try:
        att_res = _eval(
            db,
            question_id=question_id,
            answer=answer,
            save=True,
            session_id=session_id,
            role=role,
            parent_attempt_id=parent_attempt_id,
        )
    except TypeError:
        try:
            att_res = _eval(
                db,
                question_id=question_id,
                answer=answer,
                save=True,
                session_id=session_id,
                role=role,
            )
        except TypeError:
            try:
                att_res = _eval(
                    db,
                    question_id=question_id,
                    answer=answer,
                    save=True,
                    role=role,
                    parent_attempt_id=parent_attempt_id,
                )
            except TypeError:
                att_res = _eval(
                    db,
                    question_id=question_id,
                    answer=answer,
                    save=True,
                )
    attempt = (await att_res) if inspect.isawaitable(att_res) else att_res

    ev = _get_val(attempt, "evaluation")
    score = _get_val(ev, "score") if ev else 0
    coverage = _get_val(ev, "coverage") if ev else 0.0
    pts = _get_val(ev, "points") or []
    misconceptions = _get_val(ev, "misconceptions") or []
    unverified = _get_val(ev, "unverified") or []
    strengths = _get_val(ev, "strengths") or []
    advice = _get_val(ev, "advice") or ""
    notes = _get_val(ev, "notes") or []
    copied = _get_val(attempt, "copied", False)
    attempt_id = _get_val(attempt, "attempt_id")

    next_step: str
    before_val: int | None = None
    after_val: int | None = None

    if kind == "initial":
        teach = session.get("teach", True)
        teach_below = session.get("teach_below", 2)
        max_lessons = session.get("max_lessons", 2)
        lessons_given = session.get("lessons_given", 0)

        if score is not None and score <= teach_below and teach and lessons_given < max_lessons:
            next_step = "lesson"
            stage = "need_lesson"
            new_cycle = {
                "original_question_id": question_id,
                "initial_attempt_id": attempt_id,
                "lesson_id": None,
                "followup_question_id": None,
                "followup_attempt_id": None,
                "before": score,
            }
            await _update_session(db, session_id, {
                "stage": stage,
                "cycle": new_cycle,
                "lessons_given": lessons_given + 1,
                "pending": None,
            })
        else:
            item = {
                "question_id": question_id,
                "attempt_id": attempt_id,
                "status": "answered",
                "score": score,
            }
            await record_item(db, session_id, item)
            await advance(db, session_id)
            session["current"] = session.get("current", 0) + 1
            if session["current"] >= len(plan):
                stage = "finished"
                next_step = "finished"
            else:
                stage = "need_question"
                next_step = "question"
            await _update_session(db, session_id, {
                "stage": stage,
                "pending": None,
                "cycle": None,
            })

    elif kind == "followup":
        cycle["followup_attempt_id"] = attempt_id
        stage = "need_retry"
        next_step = "retry"
        await _update_session(db, session_id, {
            "stage": stage,
            "cycle": cycle,
            "pending": None,
        })

    elif kind == "retry":
        before_val = cycle.get("before")
        after_val = score
        original_qid = cycle.get("original_question_id")
        initial_aid = cycle.get("initial_attempt_id")

        try:
            from app.interview.profile import record_teaching
            orig_q = db.interview_questions.find_one({"question_id": original_qid})
            if inspect.isawaitable(orig_q):
                orig_q = await orig_q
            orig_concept = _get_val(orig_q, "concept")
            rec_res = record_teaching(db, project_id, orig_concept, before_val, after_val)
            if inspect.isawaitable(rec_res):
                await rec_res
        except Exception:
            pass

        item = {
            "question_id": original_qid,
            "attempt_id": initial_aid,
            "status": "answered",
            "score": before_val,
            "lesson_id": cycle.get("lesson_id"),
            "followup_question_id": cycle.get("followup_question_id"),
            "followup_attempt_id": cycle.get("followup_attempt_id"),
            "retry_attempt_id": attempt_id,
            "before": before_val,
            "after": after_val,
        }
        if copied:
            item["copied"] = True
        if cycle.get("cycle_note"):
            item["cycle_note"] = cycle["cycle_note"]

        await record_item(db, session_id, item)
        await advance(db, session_id)
        session["current"] = session.get("current", 0) + 1
        if session["current"] >= len(plan):
            stage = "finished"
            next_step = "finished"
        else:
            stage = "need_question"
            next_step = "question"
        await _update_session(db, session_id, {
            "stage": stage,
            "cycle": None,
            "pending": None,
        })

    else:
        raise EngineStateError(f"Unknown pending kind: '{kind}'")

    points_out = [
        {
            "point": _get_val(p, "point"),
            "kind": _get_val(p, "kind"),
            "status": _get_val(p, "status"),
            "quote": _get_val(p, "quote"),
        }
        for p in pts
    ]
    misconceptions_out = [
        m.model_dump(mode="json") if hasattr(m, "model_dump") else dict(m)
        for m in misconceptions
    ]
    unverified_out = [
        u.model_dump(mode="json") if hasattr(u, "model_dump") else dict(u)
        for u in unverified
    ]

    result: dict[str, Any] = {
        "score": score,
        "coverage": coverage,
        "points": points_out,
        "misconceptions": misconceptions_out,
        "unverified": unverified_out,
        "strengths": strengths,
        "advice": advice,
        "notes": notes,
        "role": role,
        "next": next_step,
    }
    if copied is not None:
        result["copied"] = copied
    if kind == "retry":
        result["before"] = before_val
        result["after"] = after_val
    return result


async def skip(db: Any, session_id: str) -> dict[str, Any]:
    """Skip the current pending question."""
    session = await get_session(db, session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found.")

    if session.get("stage") != "awaiting_answer":
        raise EngineStateError(f"Cannot skip in stage '{session.get('stage')}'.")

    pending = session.get("pending") or {}
    kind = pending.get("kind", "initial")
    qid = pending.get("question_id")
    plan = session.get("plan", [])

    if kind == "initial":
        item = {
            "question_id": qid,
            "attempt_id": None,
            "status": "skipped",
            "score": None,
        }
        await record_item(db, session_id, item)
        await advance(db, session_id)
        session["current"] = session.get("current", 0) + 1
        is_finished = session["current"] >= len(plan)
        stage = "finished" if is_finished else "need_question"
        await _update_session(db, session_id, {
            "stage": stage,
            "pending": None,
            "cycle": None,
        })
        return {"action": "skipped", "status": "skipped", "stage": stage}

    if kind == "followup":
        cycle = session.get("cycle") or {}
        cycle["cycle_note"] = "followup skipped"
        stage = "need_retry"
        await _update_session(db, session_id, {
            "stage": stage,
            "cycle": cycle,
            "pending": None,
        })
        return {"action": "skipped", "status": "skipped", "stage": stage}

    if kind == "retry":
        cycle = session.get("cycle") or {}
        item = {
            "question_id": cycle.get("original_question_id"),
            "attempt_id": cycle.get("initial_attempt_id"),
            "status": "answered",
            "score": cycle.get("before"),
            "lesson_id": cycle.get("lesson_id"),
            "followup_question_id": cycle.get("followup_question_id"),
            "followup_attempt_id": cycle.get("followup_attempt_id"),
            "retry_attempt_id": None,
            "cycle_note": "retry skipped",
        }
        await record_item(db, session_id, item)
        await advance(db, session_id)
        session["current"] = session.get("current", 0) + 1
        is_finished = session["current"] >= len(plan)
        stage = "finished" if is_finished else "need_question"
        await _update_session(db, session_id, {
            "stage": stage,
            "pending": None,
            "cycle": None,
        })
        return {"action": "skipped", "status": "skipped", "stage": stage}

    raise EngineStateError(f"Unknown pending kind: '{kind}'")


async def get_state(db: Any, session_id: str) -> dict[str, Any]:
    """Retrieve the current state of an interview session."""
    session = await get_session(db, session_id)
    if not session:
        raise ValueError(f"Session '{session_id}' not found.")

    plan = session.get("plan", [])
    total = len(plan)
    slot = session.get("current", 0) + 1

    clean_items: list[dict[str, Any]] = []
    for it in session.get("items", []):
        it_clean = dict(it)
        for banned in ("key_points", "common_mistakes", "seed"):
            it_clean.pop(banned, None)
        clean_items.append(it_clean)

    pending_obj = None
    pending = session.get("pending")
    if pending and pending.get("question_id"):
        qid = pending["question_id"]
        q_doc = db.interview_questions.find_one({"question_id": qid})
        if inspect.isawaitable(q_doc):
            q_doc = await q_doc
        pending_obj = {
            "kind": pending["kind"],
            "question_id": qid,
            "text": _get_val(q_doc, "question", ""),
        }

    return {
        "session_id": session_id,
        "status": session.get("status", "active"),
        "stage": session.get("stage", "need_question"),
        "pending": pending_obj,
        "cycle": session.get("cycle"),
        "slot": slot,
        "total": total,
        "lessons_given": session.get("lessons_given", 0),
        "max_lessons": session.get("max_lessons", 2),
        "plan": plan,
        "items": clean_items,
    }
