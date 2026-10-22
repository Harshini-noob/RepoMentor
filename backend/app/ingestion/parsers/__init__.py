import logging
import os
from app.ingestion.parsers import (
    docker_compose,
    dockerfile,
    package_json,
    pyproject,
    requirements,
)
from app.ingestion.parsers.models import Dependency

logger = logging.getLogger("repomentor.parsers")

__all__ = ["Dependency", "parse_manifest"]


def parse_manifest(path: str, content: str) -> list[Dependency]:
    """
    Detect manifest parser by filename and extract dependencies.
    Returns an empty list for unknown files or malformed content, logging any errors without raising.
    """
    if not path or not content:
        return []

    filename = os.path.basename(path).lower()

    try:
        if filename == "package.json":
            return package_json.parse(content, source_file=path)

        if filename == "pyproject.toml":
            return pyproject.parse(content, source_file=path)

        if filename == "requirements.txt" or (filename.startswith("requirements-") and filename.endswith(".txt")):
            return requirements.parse(content, source_file=path)

        if filename == "dockerfile" or filename.startswith("dockerfile.") or filename.endswith(".dockerfile"):
            return dockerfile.parse(content, source_file=path)

        if filename.startswith("docker-compose") or filename in {"compose.yaml", "compose.yml"}:
            return docker_compose.parse(content, source_file=path)

        # Unrecognized manifest file type
        return []

    except Exception as exc:
        logger.warning("Unexpected error parsing manifest '%s': %s", path, exc)
        return []
