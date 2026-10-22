import logging
import os
import re
from app.ingestion.parsers.models import Dependency

logger = logging.getLogger("repomentor.parsers.requirements")


def parse(content: str, source_file: str = "requirements.txt") -> list[Dependency]:
    """
    Parse Python PyPI dependencies from requirements.txt content.
    Handles inline comments, pip flags, environment markers, and extras.
    """
    filename = os.path.basename(source_file).lower()
    is_dev = any(dev_kw in filename for dev_kw in ("dev", "test"))
    kind = "dev" if is_dev else "runtime"

    deps: list[Dependency] = []

    for line_num, raw_line in enumerate(content.splitlines(), start=1):
        try:
            # Strip comments and whitespace
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue

            # Skip pip options / flags (-r, -f, -i, --index-url, etc.)
            if line.startswith("-"):
                continue

            # Strip environment markers (e.g. '; python_version < "3.11"')
            line = line.split(";", 1)[0].strip()
            if not line:
                continue

            # Match package name (with optional extras) and version specifier
            match = re.match(r"^([a-zA-Z0-9_.-]+(?:\[[^\]]+\])?)\s*(.*)$", line)
            if not match:
                continue

            raw_name, raw_spec = match.groups()
            # Clean package name by separating bracketed extras
            name = re.sub(r"\[.*\]", "", raw_name).strip()
            version_spec = raw_spec.strip() if raw_spec else None

            deps.append(
                Dependency(
                    name=name,
                    version_spec=version_spec if version_spec else None,
                    ecosystem="pypi",
                    kind=kind,
                    source_file=source_file,
                )
            )
        except Exception as exc:
            logger.warning("Error parsing line %d in %s: %s", line_num, source_file, exc)

    return deps
