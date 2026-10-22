from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI

from app.api.analysis import router as analysis_router
from app.api.projects import router as projects_router
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
app.include_router(projects_router)
app.include_router(analysis_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
