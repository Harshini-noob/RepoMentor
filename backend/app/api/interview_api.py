"""Interview API router."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.locks import get_model_lock
from app.api.models import (
    InterviewActionResponse,
    InterviewAnswerRequest,
    InterviewEvaluationResponse,
    InterviewPlanResponse,
    InterviewPlanSlot,
    InterviewReportResponse,
    InterviewSessionCreateRequest,
    InterviewSessionCreateResponse,
    InterviewSessionSummaryItem,
    InterviewSkipResponse,
    InterviewStateResponse,
    ProfileConceptItem,
    ProfileMissItem,
    ProfileResponse,
)
from app.core.db import get_db
from app.interview.engine import (
    EngineStateError,
    get_state,
    next_action,
    skip,
    start,
    submit_answer,
)
from app.interview.plan import build_plan as build_adaptive_plan
from app.interview.profile import get_profile, weak_concepts
from app.interview.seeds import find_seed_for_concept, pick_seed
from app.interview.session import ROUNDS_ORDER, get_session as get_interview_session
from app.interview.summary import build_summary

logger = logging.getLogger("repomentor.api.interview")

router = APIRouter(prefix="/projects/{project_id}", tags=["interview"])


async def _get_project_or_404(project_id: str, db: Any) -> dict[str, Any]:
    query = {"_id": ObjectId(project_id)} if ObjectId.is_valid(project_id) else {"_id": project_id}
    res = db.projects.find_one(query)
    project = await res if inspect.isawaitable(res) else res
    if not project:
        res2 = db.projects.find_one({"project_id": project_id})
        project = await res2 if inspect.isawaitable(res2) else res2
    if not project:
        res3 = db.projects.find_one({"id": project_id})
        project = await res3 if inspect.isawaitable(res3) else res3
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID '{project_id}' not found",
        )
    return project


async def _get_session_or_404(project_id: str, session_id: str, db: Any) -> dict[str, Any]:
    await _get_project_or_404(project_id, db)
    session = await get_interview_session(db, session_id)
    if not session or session.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Interview session '{session_id}' not found for project '{project_id}'",
        )
    return session



@router.get(
    "/profile",
    response_model=ProfileResponse,
)
async def get_learner_profile(
    project_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ProfileResponse:
    """Retrieve the learner profile for a project. 200 with concepts: [] when empty."""
    await _get_project_or_404(project_id, db)
    profile = await get_profile(db, project_id)

    if not profile or not profile.get("concepts"):
        return ProfileResponse(concepts=[], revise_first=[])

    concepts_data = profile.get("concepts") or {}
    concept_items: list[ProfileConceptItem] = []
    for name, info in concepts_data.items():
        if not isinstance(info, dict):
            continue
        misses_raw = info.get("misses") or []
        misses = [
            ProfileMissItem(
                point=m.get("point", ""),
                kind=m.get("kind", "general"),
                count=m.get("count", 1),
                last_seen=m.get("last_seen"),
            )
            for m in misses_raw[:3]
        ]
        concept_items.append(
            ProfileConceptItem(
                concept=name,
                status=info.get("status", "unrated"),
                score=float(info.get("score", 0.0)),
                attempts=int(info.get("attempts", 0)),
                last_seen=info.get("last_seen"),
                misses=misses,
            )
        )

    concept_items.sort(key=lambda x: (x.score, x.concept))
    revise = weak_concepts(profile, k=3)
    return ProfileResponse(concepts=concept_items, revise_first=revise)


@router.get(
    "/interview/plan",
    response_model=InterviewPlanResponse,
)
async def get_interview_plan(
    project_id: str,
    questions: int = Query(default=5, ge=1, le=20),
    difficulty: int = Query(default=2, ge=1, le=5),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> InterviewPlanResponse:
    """Generate an adaptive interview plan without running models or saving data."""
    await _get_project_or_404(project_id, db)
    profile = await get_profile(db, project_id)

    raw_d = db.dossiers.find_one({"project_id": project_id}, {"_id": 0})
    dossier = await raw_d if inspect.isawaitable(raw_d) else raw_d

    cur = db.interview_questions.find({"project_id": project_id}, {"_id": 0})
    if hasattr(cur, "to_list"):
        asked = await cur.to_list(None)
    elif inspect.isawaitable(cur):
        asked = list(await cur)
    else:
        asked = list(cur)

    plan = build_adaptive_plan(profile, n=questions, base_difficulty=difficulty, dossier=dossier)
    asked_copy = list(asked) if asked else []
    slots: list[InterviewPlanSlot] = []

    for idx, s in enumerate(plan, start=1):
        concept = s.get("concept")
        diff = s.get("difficulty")
        reason = s.get("reason", "")
        seed = None
        if dossier:
            if concept:
                seed_res = find_seed_for_concept(dossier, concept, asked_copy)
                if seed_res is not None:
                    seed = seed_res[0]
            if seed is None:
                r = s.get("round") or ROUNDS_ORDER[(idx - 1) % len(ROUNDS_ORDER)]
                try:
                    seed = pick_seed(dossier, r, asked_copy)
                except Exception:
                    seed = None

        first_claim = ""
        if seed:
            claims = seed.get("claims") or []
            if claims:
                first_claim = claims[0].get("claim", "")
            asked_copy.append({"seed": seed})

        slots.append(
            InterviewPlanSlot(
                slot=idx,
                concept=concept,
                difficulty=diff,
                reason=reason,
                first_seed_claim=first_claim,
                claim=first_claim,
            )
        )

    return InterviewPlanResponse(slots=slots)


@router.get(
    "/interview/sessions",
    response_model=list[InterviewSessionSummaryItem],
)
async def list_interview_sessions(
    project_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[InterviewSessionSummaryItem]:
    """Retrieve the newest 20 interview sessions for a project."""
    await _get_project_or_404(project_id, db)

    cur = db.interview_sessions.find({"project_id": project_id}).sort("created_at", -1).limit(20)
    if hasattr(cur, "to_list"):
        docs = await cur.to_list(20)
    elif inspect.isawaitable(cur):
        docs = list(await cur)
    else:
        docs = list(cur)

    results: list[InterviewSessionSummaryItem] = []
    for doc in docs:
        items = doc.get("items") or []
        answered_items = [it for it in items if it.get("status") == "answered"]
        answered_count = len(answered_items)
        scores = [it.get("score") for it in answered_items if it.get("score") is not None]
        avg_score = (sum(scores) / len(scores)) if scores else None
        results.append(
            InterviewSessionSummaryItem(
                session_id=doc.get("session_id"),
                status=doc.get("status", "active"),
                plan_mode=doc.get("plan_mode", "fixed"),
                created_at=doc.get("created_at"),
                answered=answered_count,
                answered_count=answered_count,
                average_score=avg_score,
            )
        )
    return results


@router.get(
    "/interview/sessions/{session_id}/report",
    response_model=InterviewReportResponse,
)
async def get_interview_session_report(
    project_id: str,
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> InterviewReportResponse:
    """Retrieve the report for an interview session as JSON (no model call)."""
    session = await _get_session_or_404(project_id, session_id, db)

    items = session.get("items", [])
    q_ids = [it["question_id"] for it in items if it.get("question_id")]
    a_ids = [it["attempt_id"] for it in items if it.get("attempt_id")]

    questions = []
    if q_ids:
        q_cur = db.interview_questions.find({"question_id": {"$in": q_ids}})
        if hasattr(q_cur, "to_list"):
            questions = await q_cur.to_list(len(q_ids))
        elif inspect.isawaitable(q_cur):
            questions = list(await q_cur)
        else:
            questions = list(q_cur)

    attempts = []
    if a_ids:
        a_cur = db.interview_attempts.find({"attempt_id": {"$in": a_ids}})
        if hasattr(a_cur, "to_list"):
            attempts = await a_cur.to_list(len(a_ids))
        elif inspect.isawaitable(a_cur):
            attempts = list(await a_cur)
        else:
            attempts = list(a_cur)

    summary = build_summary(items, questions, attempts)

    rows_dicts = []
    for r in summary.get("rows", []):
        rows_dicts.append({
            "round": r.round if hasattr(r, "round") else r.get("round"),
            "difficulty": r.difficulty if hasattr(r, "difficulty") else r.get("difficulty"),
            "concept": r.concept if hasattr(r, "concept") else r.get("concept"),
            "score": r.score if hasattr(r, "score") else r.get("score"),
        })

    return InterviewReportResponse(
        answered=summary["answered"],
        skipped=summary["skipped"],
        average_score=summary["average_score"],
        rows=rows_dicts,
        per_concept=summary.get("per_concept", {}),
        concept_scores=summary.get("concept_scores", {}),
        weakest_concepts=summary.get("weakest_concepts", []),
        revise=summary.get("revise", []),
        unverified_count=summary.get("unverified_count", 0),
        lessons_given=summary.get("lessons_given", 0),
        before_after=summary.get("before_after", []),
        average_improvement=summary.get("average_improvement"),
    )


@router.post(
    "/interview/sessions",
    response_model=InterviewSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_interview_session(
    project_id: str,
    payload: InterviewSessionCreateRequest = Body(default_factory=InterviewSessionCreateRequest),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> InterviewSessionCreateResponse:
    """Create a new interview session. Does not hold the model lock."""
    await _get_project_or_404(project_id, db)
    session = await start(
        db=db,
        project_id=project_id,
        questions=payload.questions,
        difficulty=payload.difficulty,
        adaptive=payload.adaptive,
        teach=payload.teach,
        teach_below=payload.teach_below,
        max_lessons=payload.max_lessons,
    )
    return InterviewSessionCreateResponse(
        session_id=session["session_id"],
        plan=session.get("plan", []),
        status=session.get("status", "active"),
    )


@router.get(
    "/interview/sessions/{session_id}/state",
    response_model=InterviewStateResponse,
)
async def get_interview_session_state(
    project_id: str,
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> InterviewStateResponse:
    """Retrieve the current state of an interview session."""
    await _get_session_or_404(project_id, session_id, db)
    state = await get_state(db, session_id)
    return InterviewStateResponse(**state)


@router.post(
    "/interview/sessions/{session_id}/next",
    response_model=InterviewActionResponse,
    response_model_exclude_none=True,
)
async def next_interview_action(
    project_id: str,
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> InterviewActionResponse:
    """Advance to the next action (question, lesson, or summary). Takes the model lock."""
    await _get_session_or_404(project_id, session_id, db)
    lock = get_model_lock()
    if lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="busy: another request is using the model",
        )
    await lock.acquire()
    try:
        action = await next_action(db, session_id)
        return InterviewActionResponse(**action)
    except EngineStateError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot advance: {e}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during interview next_action")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while generating the next action.",
        )
    finally:
        lock.release()


@router.post(
    "/interview/sessions/{session_id}/answer",
    response_model=InterviewEvaluationResponse,
    response_model_exclude_none=True,
)
async def submit_interview_answer(
    project_id: str,
    session_id: str,
    payload: InterviewAnswerRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> InterviewEvaluationResponse:
    """Submit an answer to the current pending question. Takes the model lock."""
    await _get_session_or_404(project_id, session_id, db)
    lock = get_model_lock()
    if lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="busy: another request is using the model",
        )
    await lock.acquire()
    try:
        evaluation = await submit_answer(
            db=db,
            session_id=session_id,
            question_id=payload.question_id,
            answer=payload.answer,
        )
        return InterviewEvaluationResponse(**evaluation)
    except EngineStateError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot advance: {e}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during interview submit_answer")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while evaluating the answer.",
        )
    finally:
        lock.release()


@router.post(
    "/interview/sessions/{session_id}/skip",
    response_model=InterviewSkipResponse,
)
async def skip_interview_question(
    project_id: str,
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> InterviewSkipResponse:
    """Skip the current pending question. Does not hold the model lock."""
    await _get_session_or_404(project_id, session_id, db)
    try:
        res = await skip(db, session_id)
        return InterviewSkipResponse(**res)
    except EngineStateError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot advance: {e}",
        )

