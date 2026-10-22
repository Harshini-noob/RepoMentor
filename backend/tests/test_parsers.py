import pytest

from app.ingestion.parsers import (
    Dependency,
    docker_compose,
    dockerfile,
    package_json,
    parse_manifest,
    pyproject,
    requirements,
)


# ---------------------------------------------------------------------------
# 1. package.json tests
# ---------------------------------------------------------------------------
def test_package_json_valid() -> None:
    content = """{
      "name": "sample-project",
      "dependencies": {
        "express": "^4.18.2",
        "cors": "~2.8.5"
      },
      "devDependencies": {
        "typescript": "^5.0.0",
        "jest": "29.0.0"
      }
    }"""
    deps = package_json.parse(content, source_file="package.json")
    assert len(deps) == 4

    names = {d.name: (d.version_spec, d.kind, d.ecosystem) for d in deps}
    assert names["express"] == ("^4.18.2", "runtime", "npm")
    assert names["cors"] == ("~2.8.5", "runtime", "npm")
    assert names["typescript"] == ("^5.0.0", "dev", "npm")
    assert names["jest"] == ("29.0.0", "dev", "npm")


def test_package_json_malformed() -> None:
    malformed = '{"name": "broken", "dependencies": { "express": "^4.0" '  # missing closing brace
    deps = package_json.parse(malformed, source_file="package.json")
    assert deps == []


# ---------------------------------------------------------------------------
# 2. requirements.txt tests
# ---------------------------------------------------------------------------
def test_requirements_valid() -> None:
    content = """
    # Core dependencies
    fastapi>=0.115.0
    uvicorn[standard]==0.30.0  # ASGI server
    -i https://pypi.org/simple
    requests
    pydantic>=2.0.0; python_version >= '3.11'
    """
    deps = requirements.parse(content, source_file="requirements.txt")
    assert len(deps) == 4

    names = {d.name: (d.version_spec, d.kind, d.ecosystem) for d in deps}
    assert names["fastapi"] == (">=0.115.0", "runtime", "pypi")
    assert names["uvicorn"] == ("==0.30.0", "runtime", "pypi")
    assert names["requests"] == (None, "runtime", "pypi")
    assert names["pydantic"] == (">=2.0.0", "runtime", "pypi")


def test_requirements_dev_filename() -> None:
    content = "pytest>=8.0.0\npytest-asyncio\n"
    deps = requirements.parse(content, source_file="requirements-dev.txt")
    assert len(deps) == 2
    assert all(d.kind == "dev" for d in deps)


def test_requirements_malformed_lines() -> None:
    content = "-----\n==invalid==\n   \n"
    deps = requirements.parse(content, source_file="requirements.txt")
    assert isinstance(deps, list)


# ---------------------------------------------------------------------------
# 3. pyproject.toml tests
# ---------------------------------------------------------------------------
def test_pyproject_pep621_valid() -> None:
    content = """
    [project]
    name = "repomentor"
    dependencies = [
        "fastapi>=0.115.0",
        "motor>=3.6.0; sys_platform != 'win32'",
    ]

    [project.optional-dependencies]
    dev = [
        "pytest>=8.0.0",
        "respx",
    ]
    """
    deps = pyproject.parse(content, source_file="pyproject.toml")
    assert len(deps) == 4

    names = {d.name: (d.version_spec, d.kind, d.ecosystem) for d in deps}
    assert names["fastapi"] == (">=0.115.0", "runtime", "pypi")
    assert names["motor"] == (">=3.6.0", "runtime", "pypi")
    assert names["pytest"] == (">=8.0.0", "dev", "pypi")
    assert names["respx"] == (None, "dev", "pypi")


def test_pyproject_poetry_valid() -> None:
    content = """
    [tool.poetry]
    name = "poetry-sample"

    [tool.poetry.dependencies]
    python = "^3.11"
    requests = "^2.31.0"
    pydantic = { version = ">=2.0" }

    [tool.poetry.group.dev.dependencies]
    black = "23.1.0"
    """
    deps = pyproject.parse(content, source_file="pyproject.toml")
    assert len(deps) == 3

    names = {d.name: (d.version_spec, d.kind, d.ecosystem) for d in deps}
    assert "python" not in names
    assert names["requests"] == ("^2.31.0", "runtime", "pypi")
    assert names["pydantic"] == (">=2.0", "runtime", "pypi")
    assert names["black"] == ("23.1.0", "dev", "pypi")


def test_pyproject_malformed() -> None:
    content = "this is not [ valid toml :::"
    deps = pyproject.parse(content, source_file="pyproject.toml")
    assert deps == []


# ---------------------------------------------------------------------------
# 4. Dockerfile tests
# ---------------------------------------------------------------------------
def test_dockerfile_valid_multistage() -> None:
    content = """
    # Stage 1: build
    FROM node:18-alpine AS builder
    WORKDIR /app
    RUN npm run build

    # Stage 2: production
    FROM --platform=linux/amd64 python:3.11-slim
    WORKDIR /server
    CMD ["uvicorn", "main:app"]
    """
    deps = dockerfile.parse(content, source_file="Dockerfile")
    assert len(deps) == 2

    assert deps[0].name == "node"
    assert deps[0].version_spec == "18-alpine"
    assert deps[0].kind == "dev"
    assert deps[0].ecosystem == "docker"

    assert deps[1].name == "python"
    assert deps[1].version_spec == "3.11-slim"
    assert deps[1].kind == "base_image"
    assert deps[1].ecosystem == "docker"


def test_dockerfile_malformed_or_no_from() -> None:
    content = "RUN npm install\nWORKDIR /app\n"
    deps = dockerfile.parse(content, source_file="Dockerfile")
    assert deps == []


# ---------------------------------------------------------------------------
# 5. docker-compose.yml tests
# ---------------------------------------------------------------------------
def test_docker_compose_valid() -> None:
    content = """
    version: '3.8'
    services:
      web:
        build: .
        ports:
          - "8000:8000"
      database:
        image: mongo:6.0.5
        restart: always
      cache:
        image: redis:alpine
    """
    deps = docker_compose.parse(content, source_file="docker-compose.yml")
    assert len(deps) == 3

    names = {d.name: (d.version_spec, d.kind, d.ecosystem) for d in deps}
    assert names["web"] == ("build", "dev", "docker")
    assert names["mongo"] == ("6.0.5", "runtime", "docker")
    assert names["redis"] == ("alpine", "runtime", "docker")


def test_docker_compose_malformed() -> None:
    content = ": invalid: yaml: {["
    deps = docker_compose.parse(content, source_file="docker-compose.yml")
    assert deps == []


# ---------------------------------------------------------------------------
# 6. parse_manifest dispatcher tests
# ---------------------------------------------------------------------------
def test_parse_manifest_dispatcher() -> None:
    # 1. package.json
    pkg_deps = parse_manifest("frontend/package.json", '{"dependencies": {"react": "^18.0.0"}}')
    assert len(pkg_deps) == 1
    assert pkg_deps[0].name == "react"
    assert pkg_deps[0].source_file == "frontend/package.json"

    # 2. requirements.txt and requirements-*.txt
    req_deps = parse_manifest("requirements.txt", "httpx>=0.28.0\n")
    assert len(req_deps) == 1
    assert req_deps[0].name == "httpx"

    req_dev_deps = parse_manifest("requirements-dev.txt", "pytest>=8.0.0\n")
    assert len(req_dev_deps) == 1
    assert req_dev_deps[0].name == "pytest"

    # Non-matching text files with 'requirement' or 'reqs' in name must be ignored
    assert parse_manifest("some-requirement-docs.txt", "httpx>=0.28.0\n") == []
    assert parse_manifest("reqs.txt", "httpx>=0.28.0\n") == []

    # 3. pyproject.toml
    pyproj_deps = parse_manifest("pyproject.toml", '[project]\ndependencies = ["fastapi"]\n')
    assert len(pyproj_deps) == 1
    assert pyproj_deps[0].name == "fastapi"

    # 4. Dockerfile
    docker_deps = parse_manifest("deploy/Dockerfile.prod", "FROM python:3.11\n")
    assert len(docker_deps) == 1
    assert docker_deps[0].name == "python"

    # 5. docker-compose
    compose_deps = parse_manifest("docker-compose.yml", "services:\n  db:\n    image: postgres:15\n")
    assert len(compose_deps) == 1
    assert compose_deps[0].name == "postgres"

    # 6. Unrecognized file
    unknown_deps = parse_manifest("README.md", "# Project Title\n")
    assert unknown_deps == []

    # 7. Empty content
    empty_deps = parse_manifest("package.json", "")
    assert empty_deps == []
