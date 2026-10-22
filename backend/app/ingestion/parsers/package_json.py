import json
import logging
from app.ingestion.parsers.models import Dependency

logger = logging.getLogger("repomentor.parsers.package_json")


def parse(content: str, source_file: str = "package.json") -> list[Dependency]:
    """
    Parse npm dependencies from package.json content.
    Extracts dependencies (runtime) and devDependencies (dev).
    Returns an empty list on malformed content.
    """
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            logger.warning("package.json is not a valid JSON object: %s", source_file)
            return []
    except Exception as exc:
        logger.warning("Failed to parse package.json (%s): %s", source_file, exc)
        return []

    deps: list[Dependency] = []

    # Runtime dependencies
    runtime_deps = data.get("dependencies", {})
    if isinstance(runtime_deps, dict):
        for name, ver in runtime_deps.items():
            deps.append(
                Dependency(
                    name=str(name),
                    version_spec=str(ver) if ver is not None else None,
                    ecosystem="npm",
                    kind="runtime",
                    source_file=source_file,
                )
            )

    # Dev dependencies
    dev_deps = data.get("devDependencies", {})
    if isinstance(dev_deps, dict):
        for name, ver in dev_deps.items():
            deps.append(
                Dependency(
                    name=str(name),
                    version_spec=str(ver) if ver is not None else None,
                    ecosystem="npm",
                    kind="dev",
                    source_file=source_file,
                )
            )

    return deps
