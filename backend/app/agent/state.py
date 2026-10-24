from typing import Any
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    content: str
    error: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TraceStep(BaseModel):
    step: int
    thought_summary: str | None = None
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    result_preview: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    ms: int = 0
    tokens: int = 0


class AgentRun(BaseModel):
    goal: str
    project_id: str
    steps: list[TraceStep] = Field(default_factory=list)
    final_answer: str | None = None
    stop_reason: str = "complete"
    total_tokens: int = 0


class LLMReply(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
