import logging
import re
from app.ingestion.parsers.models import Dependency

logger = logging.getLogger("repomentor.parsers.dockerfile")


def parse(content: str, source_file: str = "Dockerfile") -> list[Dependency]:
    """
    Parse base Docker images from Dockerfile FROM instructions.
    In multi-stage builds, intermediate stages are marked 'dev' and the final stage is 'runtime'.
    """
    lines = content.splitlines()
    raw_from_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Ignore comments and blank lines
        if not stripped or stripped.startswith("#"):
            continue
        # Check for FROM instruction
        if re.match(r"^FROM\s+", stripped, re.IGNORECASE):
            raw_from_lines.append(stripped)

    if not raw_from_lines:
        return []

    deps: list[Dependency] = []
    total_stages = len(raw_from_lines)

    for i, from_line in enumerate(raw_from_lines):
        try:
            # Strip FROM prefix
            parts = re.split(r"\s+", from_line, maxsplit=1)
            if len(parts) < 2:
                continue
            rest = parts[1].strip()

            # Ignore optional flags like --platform=linux/amd64
            while rest.startswith("--"):
                flag_parts = re.split(r"\s+", rest, maxsplit=1)
                if len(flag_parts) < 2:
                    rest = ""
                    break
                rest = flag_parts[1].strip()

            if not rest:
                continue

            # Split off AS <alias> if present
            as_match = re.split(r"\s+AS\s+", rest, maxsplit=1, flags=re.IGNORECASE)
            image_ref = as_match[0].strip()

            # Parse image name and tag/digest
            if "@" in image_ref:
                name, version_spec = image_ref.split("@", 1)
            elif ":" in image_ref:
                name, version_spec = image_ref.split(":", 1)
            else:
                name, version_spec = image_ref, None

            # Intermediate stages are build dependencies (dev); the final stage is marked base_image
            kind = "base_image" if i == total_stages - 1 else "dev"

            deps.append(
                Dependency(
                    name=name,
                    version_spec=version_spec,
                    ecosystem="docker",
                    kind=kind,
                    source_file=source_file,
                )
            )
        except Exception as exc:
            logger.warning("Error parsing Dockerfile line '%s' in %s: %s", from_line, source_file, exc)

    return deps
