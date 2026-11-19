"""Mentor chat API router."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.state import TraceStep
from app.api.locks import get_model_lock
from app.api.models import (
    ChatMessageItem,
    ChatRequest,
    ChatSessionCreateResponse,
    ChatSessionDetailResponse,
    ChatSessionSummaryItem,
)
from app.core.db import get_db
from app.mentor.chat import ask
from app.mentor.sessions import create_session, get_session

logger = logging.getLogger("repomentor.api.mentor")

router = APIRouter(prefix="/projects/{project_id}", tags=["mentor"])

_chat_lock = get_model_lock()


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


async def _get_ready_project_or_409(project_id: str, db: Any) -> dict[str, Any]:
    project = await _get_project_or_404(project_id, db)
    if project.get("status") != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project '{project_id}' is not ready yet (current status: '{project.get('status')}')",
        )
    return project


@router.post(
    "/chat/sessions",
    response_model=ChatSessionCreateResponse,
    status_code=status.HTTP_200_OK,
)
async def create_chat_session(
    project_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ChatSessionCreateResponse:
    """Create a new chat session for a project. 404 if unknown, 409 if not ready."""
    await _get_ready_project_or_409(project_id, db)
    session_id = await create_session(db, project_id, mode="mentor")
    return ChatSessionCreateResponse(session_id=session_id)


@router.get(
    "/chat/sessions/{session_id}",
    response_model=ChatSessionDetailResponse,
)
async def get_chat_session(
    project_id: str,
    session_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ChatSessionDetailResponse:
    """Retrieve details and messages of a chat session. 404 if missing or another project."""
    await _get_project_or_404(project_id, db)
    session = await get_session(db, session_id)
    if not session or session.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found for project '{project_id}'",
        )
    messages = [
        ChatMessageItem(
            role=m.get("role", ""),
            content=m.get("content", ""),
            ts=m.get("ts"),
        )
        for m in (session.get("messages") or [])
    ]
    return ChatSessionDetailResponse(
        session_id=session_id,
        created_at=session.get("created_at"),
        messages=messages,
    )


@router.get(
    "/chat/sessions",
    response_model=list[ChatSessionSummaryItem],
)
async def list_chat_sessions(
    project_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[ChatSessionSummaryItem]:
    """Retrieve the newest 20 chat sessions for a project."""
    await _get_project_or_404(project_id, db)
    cur = db.sessions.find({"project_id": project_id}).sort("created_at", -1).limit(20)
    if hasattr(cur, "to_list"):
        docs = await cur.to_list(20)
    elif inspect.isawaitable(cur):
        docs = list(await cur)
    else:
        docs = list(cur)

    results: list[ChatSessionSummaryItem] = []
    for doc in docs:
        msgs = doc.get("messages") or []
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        first_q = user_msgs[0].get("content", "") if user_msgs else ""
        results.append(
            ChatSessionSummaryItem(
                session_id=doc.get("session_id"),
                created_at=doc.get("created_at"),
                updated_at=doc.get("updated_at") or doc.get("created_at"),
                turns=len(user_msgs),
                first_question=first_q,
            )
        )
    return results


@router.post("/chat")
async def chat_stream(
    project_id: str,
    payload: ChatRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> StreamingResponse:
    """Stream mentor chat events via SSE."""
    await _get_ready_project_or_409(project_id, db)

    if payload.session_id is None:
        session_id = await create_session(db, project_id, mode="mentor")
    else:
        session_id = payload.session_id
        session = await get_session(db, session_id)
        if not session or session.get("project_id") != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session '{session_id}' not found for project '{project_id}'",
            )

    if _chat_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="busy: another request is using the model",
        )

    await _chat_lock.acquire()

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def on_event(text: str) -> None:
        queue.put_nowait({"type": "event", "text": text})

    def on_step(step: TraceStep) -> None:
        step_data = {
            "type": "step",
            "step": step.step,
            "tool": step.tool,
            "args": step.args,
            "preview": (step.result_preview or "")[:300],
            "evidence": (step.evidence_ids or [])[:8],
            "ms": step.ms,
            "tokens": step.tokens,
        }
        queue.put_nowait(step_data)

    async def _run_ask() -> None:
        try:
            run_res = await ask(
                db=db,
                project_id=project_id,
                session_id=session_id,
                message=payload.message,
                on_step=on_step,
                on_event=on_event,
            )
            clean_answer = (run_res.final_answer or "").replace("\u202f", " ").replace("\u00a0", " ")
            await queue.put({
                "type": "answer",
                "text": clean_answer,
                "stop_reason": run_res.stop_reason,
                "tokens": run_res.total_tokens,
            })
        except Exception:
            logger.exception("Error during mentor ask")
            await queue.put({
                "type": "error",
                "message": "An error occurred while processing the request.",
            })
        finally:
            await queue.put({"type": "done"})
            await queue.put(None)

    try:
        task = asyncio.create_task(_run_ask())
    except Exception:
        if _chat_lock.locked():
            _chat_lock.release()
        raise

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if _chat_lock.locked():
                _chat_lock.release()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
