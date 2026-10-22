import pytest
from mongomock_motor import AsyncMongoMockClient

from app.core.repositories import init_indexes, upsert_dependency
from app.ingestion.dependencies import build_dependencies


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["test_dependencies_db"]


@pytest.mark.asyncio
async def test_dependencies_unique_index(mock_db) -> None:
    await init_indexes(mock_db)

    idx_info = await mock_db.dependencies.index_information()
    expected_index = "project_id_1_ecosystem_1_name_1_source_file_1"
    assert expected_index in idx_info
    assert idx_info[expected_index].get("unique") is True


@pytest.mark.asyncio
async def test_upsert_dependency_idempotent(mock_db) -> None:
    await init_indexes(mock_db)

    # Initial insert
    dep1 = await upsert_dependency(
        mock_db,
        {
            "project_id": "proj_123",
            "name": "fastapi",
            "version_spec": ">=0.115.0",
            "ecosystem": "pypi",
            "kind": "runtime",
            "source_file": "requirements.txt",
        },
    )
    assert dep1["name"] == "fastapi"
    assert dep1["version_spec"] == ">=0.115.0"

    # Idempotent update
    dep2 = await upsert_dependency(
        mock_db,
        {
            "project_id": "proj_123",
            "name": "fastapi",
            "version_spec": ">=0.116.0",
            "ecosystem": "pypi",
            "kind": "runtime",
            "source_file": "requirements.txt",
        },
    )
    assert dep2["version_spec"] == ">=0.116.0"

    # Count must remain 1
    count = await mock_db.dependencies.count_documents(
        {
            "project_id": "proj_123",
            "ecosystem": "pypi",
            "name": "fastapi",
            "source_file": "requirements.txt",
        }
    )
    assert count == 1


@pytest.mark.asyncio
async def test_build_dependencies(mock_db) -> None:
    await init_indexes(mock_db)

    project_id = "proj_test_manifests"

    # Seed files collection with multiple manifests and a non-manifest code file
    package_json_content = """{
      "dependencies": { "express": "^4.18.2" },
      "devDependencies": { "jest": "29.0.0" }
    }"""

    dockerfile_content = """
    FROM python:3.11-slim
    WORKDIR /app
    """

    code_content = "def hello():\n    return 'world'\n"

    await mock_db.files.insert_many([
        {
            "project_id": project_id,
            "path": "frontend/package.json",
            "content": package_json_content,
            "is_config": True,
        },
        {
            "project_id": project_id,
            "path": "Dockerfile",
            "content": dockerfile_content,
            "is_config": True,
        },
        {
            "project_id": project_id,
            "path": "app/main.py",
            "content": code_content,
            "is_config": False,
        },
        {
            "project_id": project_id,
            "path": "binary_asset.png",
            "content": None,
            "is_config": False,
        },
    ])

    # Run build_dependencies
    results = await build_dependencies(project_id, db=mock_db)
    assert len(results) == 3

    # Check stored dependencies in MongoDB
    docs = await mock_db.dependencies.find({"project_id": project_id}).to_list(length=10)
    assert len(docs) == 3

    by_name = {d["name"]: (d["ecosystem"], d["kind"], d["source_file"]) for d in docs}
    assert by_name["express"] == ("npm", "runtime", "frontend/package.json")
    assert by_name["jest"] == ("npm", "dev", "frontend/package.json")
    assert by_name["python"] == ("docker", "base_image", "Dockerfile")

    # Second execution must be strictly idempotent
    second_run = await build_dependencies(project_id, db=mock_db)
    assert len(second_run) == 3
    final_count = await mock_db.dependencies.count_documents({"project_id": project_id})
    assert final_count == 3


@pytest.mark.asyncio
async def test_build_dependencies_stale_cleanup(mock_db) -> None:
    await init_indexes(mock_db)
    project_id = "proj_stale_cleanup"

    # Step 1: Ingest manifest with 3 dependencies
    content_3_deps = "fastapi==0.115.0\nuvicorn==0.30.0\nrequests==2.31.0\n"
    await mock_db.files.insert_one({
        "project_id": project_id,
        "path": "requirements.txt",
        "content": content_3_deps,
        "is_config": True,
    })

    initial_results = await build_dependencies(project_id, db=mock_db)
    assert len(initial_results) == 3
    assert await mock_db.dependencies.count_documents({"project_id": project_id}) == 3

    # Step 2: Re-run with 2 dependencies (uvicorn removed)
    content_2_deps = "fastapi==0.115.0\nrequests==2.31.0\n"
    await mock_db.files.update_one(
        {"project_id": project_id, "path": "requirements.txt"},
        {"$set": {"content": content_2_deps}},
    )

    second_results = await build_dependencies(project_id, db=mock_db)
    assert len(second_results) == 2

    # Assert exactly 2 remain and uvicorn was removed
    docs = await mock_db.dependencies.find({"project_id": project_id}).to_list(length=10)
    assert len(docs) == 2
    names = {d["name"] for d in docs}
    assert names == {"fastapi", "requests"}


@pytest.mark.asyncio
async def test_build_dependencies_failure_leaves_old_records(mock_db, monkeypatch) -> None:
    await init_indexes(mock_db)
    project_id = "proj_mid_run_failure"

    # Seed with 2 existing dependencies
    content_initial = "fastapi==0.115.0\nrequests==2.31.0\n"
    await mock_db.files.insert_one({
        "project_id": project_id,
        "path": "requirements.txt",
        "content": content_initial,
        "is_config": True,
    })
    initial_results = await build_dependencies(project_id, db=mock_db)
    assert len(initial_results) == 2
    assert await mock_db.dependencies.count_documents({"project_id": project_id}) == 2

    # Now update manifest to 3 dependencies
    content_updated = "fastapi==0.115.0\nuvicorn==0.30.0\nrequests==2.31.0\n"
    await mock_db.files.update_one(
        {"project_id": project_id, "path": "requirements.txt"},
        {"$set": {"content": content_updated}},
    )

    # Monkeypatch upsert_dependency to fail mid-run on the second record
    call_count = 0
    from app.ingestion import dependencies as deps_module
    original_upsert = deps_module.upsert_dependency

    async def failing_upsert(db, data):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Database connection dropped mid-run")
        return await original_upsert(db, data)

    monkeypatch.setattr(deps_module, "upsert_dependency", failing_upsert)

    with pytest.raises(RuntimeError, match="Database connection dropped mid-run"):
        await build_dependencies(project_id, db=mock_db)

    # Assert the old records still exist and were not purged
    surviving_docs = await mock_db.dependencies.find({"project_id": project_id}).to_list(length=10)
    assert len(surviving_docs) >= 2
    surviving_names = {d["name"] for d in surviving_docs}
    assert "fastapi" in surviving_names
    assert "requests" in surviving_names
