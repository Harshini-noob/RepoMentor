from abc import ABC, abstractmethod
from typing import Any, Type
from pydantic import BaseModel, Field, model_validator

MAX_OUTPUT_CHARS = 4000


class ToolOutput(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    truncated: bool = False

    @model_validator(mode="before")
    @classmethod
    def enforce_output_cap(cls, data: Any) -> Any:
        """Truncate text to MAX_OUTPUT_CHARS with visible indicator."""
        if isinstance(data, dict):
            text = str(data.get("text", ""))
            if len(text) > MAX_OUTPUT_CHARS:
                excess = len(text) - MAX_OUTPUT_CHARS
                data["text"] = f"{text[:MAX_OUTPUT_CHARS]}\n... [truncated, {excess} more chars]"
                data["truncated"] = True
        elif hasattr(data, "text"):
            text = str(data.text)
            if len(text) > MAX_OUTPUT_CHARS:
                excess = len(text) - MAX_OUTPUT_CHARS
                data.text = f"{text[:MAX_OUTPUT_CHARS]}\n... [truncated, {excess} more chars]"
                data.truncated = True
        return data


class Tool(ABC):
    name: str
    description: str
    args_schema: Type[BaseModel]

    def to_schema(self) -> dict[str, Any]:
        """Generate provider function-calling schema from Pydantic args_schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema.model_json_schema(),
            },
        }

    @abstractmethod
    async def run(self, project_id: str, args: Any) -> ToolOutput:
        """Execute the tool with validated arguments."""
        pass


# ---------------------------------------------------------------------------
# Evidence ID Format Helpers:
# file:<path>#L<start>-<end>, commit:<sha7>, pr:<number>, dep:<name>, fact:<kind>:<name>
# ---------------------------------------------------------------------------
def make_file_evidence(path: str, start: int, end: int) -> str:
    return f"file:{path}#L{start}-{end}"


def make_commit_evidence(sha: str) -> str:
    return f"commit:{sha[:7]}"


def make_pr_evidence(number: int) -> str:
    return f"pr:{number}"


def make_dep_evidence(name: str) -> str:
    return f"dep:{name}"


def make_fact_evidence(kind: str, name: str) -> str:
    return f"fact:{kind}:{name}"
