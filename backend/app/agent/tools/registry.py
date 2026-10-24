import json
import logging
from typing import Any
from pydantic import ValidationError

from app.agent.tools.base import Tool, ToolOutput
from app.core.logging import redact

logger = logging.getLogger("repomentor.agent.tools.registry")

MAX_OUTPUT_CHARS = 4000


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool instance."""
        if tool.name in self._tools:
            logger.warning("Overwriting existing tool registration for '%s'", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """Return all registered tools in the provider's function-calling format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        raw_args: dict[str, Any] | str,
        project_id: str,
    ) -> ToolOutput:
        """
        Validate arguments and execute the requested tool.

        Unknown tool names and validation errors return an informative ERROR text
        wrapped in ToolOutput so the model can read it, never raising exceptions.
        """
        tool = self._tools.get(name)
        if not tool:
            available = ", ".join(sorted(self._tools.keys())) or "none"
            return ToolOutput(
                text=f"ERROR: Unknown tool '{name}'. Available tools: {available}",
                truncated=False,
            )

        # 1. Parse raw arguments if passed as JSON string
        args_dict: dict[str, Any]
        if isinstance(raw_args, str):
            trimmed = raw_args.strip()
            if not trimmed:
                args_dict = {}
            else:
                try:
                    parsed = json.loads(trimmed)
                    if isinstance(parsed, dict):
                        args_dict = parsed
                    else:
                        return ToolOutput(
                            text=f"ERROR: Tool arguments for '{name}' must be a JSON object, got {type(parsed).__name__}",
                            truncated=False,
                        )
                except Exception as exc:
                    return ToolOutput(
                        text=f"ERROR: Malformed JSON arguments for tool '{name}': {exc}",
                        truncated=False,
                    )
        elif isinstance(raw_args, dict):
            # Check if arguments dictionary already encapsulates a prior parsing error
            if "error" in raw_args and len(raw_args) <= 2:
                return ToolOutput(
                    text=f"ERROR: Invalid arguments for tool '{name}': {raw_args['error']}",
                    truncated=False,
                )
            args_dict = raw_args
        else:
            return ToolOutput(
                text=f"ERROR: Unsupported arguments format for tool '{name}': {type(raw_args).__name__}",
                truncated=False,
            )

        # 2. Validate arguments with Pydantic args_schema
        try:
            validated_args = tool.args_schema.model_validate(args_dict)
        except ValidationError as v_exc:
            error_details = []
            for err in v_exc.errors():
                loc = ".".join(str(p) for p in err.get("loc", []))
                msg = err.get("msg", "")
                error_details.append(f"Field '{loc}': {msg}")
            return ToolOutput(
                text=f"ERROR: Invalid arguments for tool '{name}': {'; '.join(error_details)}",
                truncated=False,
            )

        # 3. Execute tool safely
        try:
            output = await tool.run(project_id, validated_args)
            if len(output.text) > MAX_OUTPUT_CHARS:
                excess = len(output.text) - MAX_OUTPUT_CHARS
                capped_text = f"{output.text[:MAX_OUTPUT_CHARS]}\n[truncated, {excess} more chars]"
                if hasattr(output, "model_copy"):
                    output = output.model_copy(update={"text": capped_text, "truncated": True})
                else:
                    output = ToolOutput(
                        text=capped_text,
                        evidence_ids=getattr(output, "evidence_ids", []),
                        truncated=True,
                    )
            return output
        except Exception as exc:
            logger.exception("Error executing tool '%s': %s", name, exc)
            clean_err = redact(str(exc))[:300]
            return ToolOutput(
                text=f"ERROR: Tool '{name}' failed during execution: {clean_err}",
                truncated=False,
            )
