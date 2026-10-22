import logging
import uuid
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.db import get_db
from app.core.repositories import upsert_dependency
from app.ingestion.parsers import parse_manifest

logger = logging.getLogger("repomentor.ingestion.dependencies")


async def build_dependencies(
    project_id: str,
    db: AsyncIOMotorDatabase | None = None,
) -> list[dict[str, Any]]:
    """
    Read manifest files from the 'files' collection for project_id,
    extract dependencies via parse_manifest, and idempotently upsert
    them into the 'dependencies' collection with a run_id.
    After all upserts succeed, removes stale dependencies where run_id != current.
    """
    if db is None:
        db = get_db()

    logger.info("[%s] Building dependencies from stored files", project_id)
    run_id = str(uuid.uuid4())

    # Query all stored files that have non-null content
    cursor = db.files.find(
        {
            "project_id": project_id,
            "content": {"$ne": None},
        }
    )

    upserted_records: list[dict[str, Any]] = []

    async for file_doc in cursor:
        path = file_doc.get("path", "")
        content = file_doc.get("content")
        if not path or not content:
            continue

        parsed_deps = parse_manifest(path, content)
        if not parsed_deps:
            continue

        for dep in parsed_deps:
            dep_data = {
                "project_id": project_id,
                "name": dep.name,
                "version_spec": dep.version_spec,
                "ecosystem": dep.ecosystem,
                "kind": dep.kind,
                "source_file": dep.source_file or path,
                "run_id": run_id,
            }
            saved = await upsert_dependency(db, dep_data)
            upserted_records.append(saved)

    # Only after all upserts succeed, safely remove stale records for this project
    await db.dependencies.delete_many(
        {
            "project_id": project_id,
            "run_id": {"$ne": run_id},
        }
    )

    logger.info("[%s] Built and stored %d dependencies", project_id, len(upserted_records))
    return upserted_records
