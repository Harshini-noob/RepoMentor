from pydantic import BaseModel


class Dependency(BaseModel):
    name: str
    version_spec: str | None = None
    ecosystem: str  # "npm" | "pypi" | "docker"
    kind: str  # "runtime" | "dev"
    source_file: str = ""
