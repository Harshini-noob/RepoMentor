import logging
import yaml
from app.ingestion.parsers.models import Dependency

logger = logging.getLogger("repomentor.parsers.docker_compose")


def parse(content: str, source_file: str = "docker-compose.yml") -> list[Dependency]:
    """
    Parse Docker service images and dependencies from docker-compose.yml content.
    Extracts services.image (runtime) and local service builds (dev).
    """
    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            logger.warning("docker-compose content is not a valid YAML mapping: %s", source_file)
            return []
    except Exception as exc:
        logger.warning("Failed to parse docker-compose YAML (%s): %s", source_file, exc)
        return []

    services = data.get("services", {})
    if not isinstance(services, dict):
        return []

    deps: list[Dependency] = []

    for service_name, config in services.items():
        if not isinstance(config, dict):
            continue

        image_str = config.get("image")
        if image_str and isinstance(image_str, str):
            image_ref = image_str.strip()
            if "@" in image_ref:
                name, version_spec = image_ref.split("@", 1)
            elif ":" in image_ref:
                name, version_spec = image_ref.split(":", 1)
            else:
                name, version_spec = image_ref, None

            deps.append(
                Dependency(
                    name=name,
                    version_spec=version_spec,
                    ecosystem="docker",
                    kind="runtime",
                    source_file=source_file,
                )
            )
        elif "build" in config:
            # Custom locally built service
            deps.append(
                Dependency(
                    name=str(service_name),
                    version_spec="build",
                    ecosystem="docker",
                    kind="dev",
                    source_file=source_file,
                )
            )

    return deps
