from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, TEXT, ReturnDocument


async def init_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Create unique compound indexes for idempotent upserts and text search indexes.
    """
    # Unique compound / key indexes
    await db.projects.create_index([("repo_full_name", ASCENDING)], unique=True)
    await db.files.create_index([("project_id", ASCENDING), ("path", ASCENDING)], unique=True)
    await db.commits.create_index([("project_id", ASCENDING), ("sha", ASCENDING)], unique=True)
    await db.prs.create_index([("project_id", ASCENDING), ("number", ASCENDING)], unique=True)
    await db.issues.create_index([("project_id", ASCENDING), ("number", ASCENDING)], unique=True)
    await db.dependencies.create_index(
        [
            ("project_id", ASCENDING),
            ("ecosystem", ASCENDING),
            ("name", ASCENDING),
            ("source_file", ASCENDING),
        ],
        unique=True,
    )
    await db.derived_facts.create_index(
        [
            ("project_id", ASCENDING),
            ("kind", ASCENDING),
            ("name", ASCENDING),
            ("file_path", ASCENDING),
            ("line_start", ASCENDING),
        ],
        unique=True,
    )

    # Text search indexes
    # default_language="none" and language_override="none" prevent MongoDB from treating
    # our source code "language" field (e.g. Python, None) as natural language stemmers
    try:
        await db.files.create_index(
            [("content", TEXT)],
            default_language="none",
            language_override="none",
        )
    except Exception:
        await db.files.drop_index("content_text")
        await db.files.create_index(
            [("content", TEXT)],
            default_language="none",
            language_override="none",
        )
    await db.commits.create_index([("message", TEXT)])
    await db.prs.create_index([("title", TEXT), ("body", TEXT)])


async def upsert_project(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotently insert or update a project record by repo_full_name.
    Schema: repo_full_name, default_branch, status (queued|ingesting|ready|failed),
    ingest_cap, head_sha, analysed_at, error.
    """
    repo_full_name = data["repo_full_name"]
    result = await db.projects.find_one_and_update(
        {"repo_full_name": repo_full_name},
        {"$set": data},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result


async def upsert_file(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotently insert or update a file record by project_id and path.
    Schema: project_id, path, size, language, sha, content (nullable), is_config.
    """
    result = await db.files.find_one_and_update(
        {"project_id": data["project_id"], "path": data["path"]},
        {"$set": data},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result


async def upsert_commit(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotently insert or update a commit record by project_id and sha.
    Schema: project_id, sha, date, author, message, files_changed, additions, deletions, parents.
    """
    result = await db.commits.find_one_and_update(
        {"project_id": data["project_id"], "sha": data["sha"]},
        {"$set": data},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result


async def upsert_pr(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotently insert or update a pull request record by project_id and number.
    Schema: project_id, number, title, body, state, created_at, merged_at, files, comments.
    """
    result = await db.prs.find_one_and_update(
        {"project_id": data["project_id"], "number": data["number"]},
        {"$set": data},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result


async def upsert_issue(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotently insert or update an issue record by project_id and number.
    Schema: project_id, number, title, body, labels, comments.
    """
    result = await db.issues.find_one_and_update(
        {"project_id": data["project_id"], "number": data["number"]},
        {"$set": data},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result


async def upsert_dependency(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotently insert or update a dependency record by (project_id, ecosystem, name, source_file).
    Schema: project_id, name, version_spec, ecosystem, kind, source_file.
    """
    filter_query = {
        "project_id": data["project_id"],
        "ecosystem": data["ecosystem"],
        "name": data["name"],
        "source_file": data["source_file"],
    }
    result = await db.dependencies.find_one_and_update(
        filter_query,
        {"$set": data},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result


async def upsert_derived_fact(db: AsyncIOMotorDatabase, data: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotently insert or update a derived fact record by (project_id, kind, name, file_path, line_start).
    Schema: project_id, kind, name, file_path, line_start, line_end, details.
    """
    filter_query = {
        "project_id": data["project_id"],
        "kind": data["kind"],
        "name": data["name"],
        "file_path": data["file_path"],
        "line_start": data["line_start"],
    }
    result = await db.derived_facts.find_one_and_update(
        filter_query,
        {"$set": data},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result


