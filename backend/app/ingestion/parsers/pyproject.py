import logging
import re
import tomllib
from typing import Any
from app.ingestion.parsers.models import Dependency

logger = logging.getLogger("repomentor.parsers.pyproject")


def _parse_pep508_string(dep_str: str, kind: str, source_file: str) -> Dependency | None:
    # Strip comments and environment markers
    clean = dep_str.split("#", 1)[0].split(";", 1)[0].strip()
    if not clean:
        return None

    match = re.match(r"^([a-zA-Z0-9_.-]+(?:\[[^\]]+\])?)\s*(.*)$", clean)
    if not match:
        return None

    raw_name, raw_spec = match.groups()
    name = re.sub(r"\[.*\]", "", raw_name).strip()
    version_spec = raw_spec.strip() if raw_spec else None

    return Dependency(
        name=name,
        version_spec=version_spec if version_spec else None,
        ecosystem="pypi",
        kind=kind,
        source_file=source_file,
    )


def parse(content: str, source_file: str = "pyproject.toml") -> list[Dependency]:
    """
    Parse Python dependencies from pyproject.toml content.
    Supports both PEP 621 ([project.dependencies]) and Poetry ([tool.poetry.dependencies]).
    """
    try:
        data = tomllib.loads(content)
        if not isinstance(data, dict):
            logger.warning("pyproject.toml is not a valid table: %s", source_file)
            return []
    except Exception as exc:
        logger.warning("Failed to parse pyproject.toml (%s): %s", source_file, exc)
        return []

    deps: list[Dependency] = []

    # 1. PEP 621 Standard
    project_table = data.get("project", {})
    if isinstance(project_table, dict):
        # Runtime dependencies: project.dependencies = ["pkg>=1.0", ...]
        runtime_list = project_table.get("dependencies", [])
        if isinstance(runtime_list, list):
            for item in runtime_list:
                if isinstance(item, str):
                    dep = _parse_pep508_string(item, kind="runtime", source_file=source_file)
                    if dep:
                        deps.append(dep)

        # Dev / Optional dependencies: project.optional-dependencies = {dev = [...]}
        optional_dict = project_table.get("optional-dependencies", {})
        if isinstance(optional_dict, dict):
            for group_name, group_list in optional_dict.items():
                if isinstance(group_list, list):
                    for item in group_list:
                        if isinstance(item, str):
                            dep = _parse_pep508_string(item, kind="dev", source_file=source_file)
                            if dep:
                                deps.append(dep)

    # 2. Poetry Format: tool.poetry
    poetry_table = data.get("tool", {}).get("poetry", {})
    if isinstance(poetry_table, dict):
        # Runtime: tool.poetry.dependencies
        poetry_deps = poetry_table.get("dependencies", {})
        if isinstance(poetry_deps, dict):
            for pkg_name, spec in poetry_deps.items():
                if pkg_name.lower() == "python":
                    continue
                version_spec = spec if isinstance(spec, str) else spec.get("version") if isinstance(spec, dict) else None
                deps.append(
                    Dependency(
                        name=pkg_name,
                        version_spec=str(version_spec) if version_spec else None,
                        ecosystem="pypi",
                        kind="runtime",
                        source_file=source_file,
                    )
                )

        # Dev: tool.poetry.group.*.dependencies or tool.poetry.dev-dependencies
        legacy_dev = poetry_table.get("dev-dependencies", {})
        if isinstance(legacy_dev, dict):
            for pkg_name, spec in legacy_dev.items():
                version_spec = spec if isinstance(spec, str) else spec.get("version") if isinstance(spec, dict) else None
                deps.append(
                    Dependency(
                        name=pkg_name,
                        version_spec=str(version_spec) if version_spec else None,
                        ecosystem="pypi",
                        kind="dev",
                        source_file=source_file,
                    )
                )

        groups = poetry_table.get("group", {})
        if isinstance(groups, dict):
            for group_name, group_val in groups.items():
                if isinstance(group_val, dict):
                    group_deps = group_val.get("dependencies", {})
                    if isinstance(group_deps, dict):
                        for pkg_name, spec in group_deps.items():
                            version_spec = spec if isinstance(spec, str) else spec.get("version") if isinstance(spec, dict) else None
                            deps.append(
                                Dependency(
                                    name=pkg_name,
                                    version_spec=str(version_spec) if version_spec else None,
                                    ecosystem="pypi",
                                    kind="dev",
                                    source_file=source_file,
                                )
                            )

    return deps
