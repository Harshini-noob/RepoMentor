from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.dossier_api import router as dossier_router
from app.api.evidence_api import router as evidence_router
from app.api.interview_api import router as interview_router
from app.api.mentor_api import router as mentor_router
from app.api.projects import router as projects_router
from app.config import get_settings
from app.core.db import get_db
from app.core.repositories import init_indexes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        db_func = app.dependency_overrides.get(get_db, get_db)
        db = db_func()
        await init_indexes(db)
    except Exception as exc:
        # Gracefully handle local dev when MongoDB server is not running
        print(f"Startup notice: unable to initialize Mongo indexes: {exc}")
    yield


app = FastAPI(title="RepoMentor API", lifespan=lifespan)

# CORS configuration
origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(analysis_router)
app.include_router(mentor_router)
app.include_router(dossier_router)
app.include_router(interview_router)
app.include_router(evidence_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
