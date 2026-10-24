import asyncio
import inspect
import json
import logging
from typing import Any

from groq import APIStatusError, AsyncGroq, RateLimitError

from app.agent.state import LLMReply, ToolCall
from app.config import get_settings

logger = logging.getLogger("repomentor.agent.llm")

_client: AsyncGroq | None = None


def get_groq_client() -> AsyncGroq:
    """Retrieve or initialize the AsyncGroq client singleton using config."""
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not configured in settings or backend/.env")
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an error is a rate-limit (429) or transient 5xx server error."""
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", 0)
        return status == 429 or status >= 500
    # Also check generic status_code attribute if raised by custom wrappers
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    return False


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    client: Any = None,
    model: str | None = None,
    backoff: float = 1.0,
) -> LLMReply:
    """
    Execute a chat completion with the Groq API.

    Retries once on 429 (rate-limit) or 5xx server errors with backoff.
    Parses tool-call arguments with json.loads and, if malformed,
    returns the error text in the ToolCall object instead of crashing.
    """
    settings = get_settings()
    selected_model = model or settings.groq_model
    groq_client = client or get_groq_client()

    kwargs: dict[str, Any] = {
        "model": selected_model,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    response = None
    for attempt in range(2):
        try:
            res = groq_client.chat.completions.create(**kwargs)
            if inspect.isawaitable(res):
                response = await res
            else:
                response = res
            break
        except Exception as exc:
            if attempt == 0 and _is_retryable_error(exc):
                logger.warning(
                    "Groq API call encountered transient error (%s). Retrying in %.2fs...",
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                continue
            raise

    if response is None:
        raise RuntimeError("No response received from Groq API")

    # Extract choices and message
    choice = response.choices[0]
    msg = choice.message
    content = getattr(msg, "content", None)

    # Parse tool calls
    parsed_tool_calls: list[ToolCall] = []
    raw_tool_calls = getattr(msg, "tool_calls", None) or []

    for tc in raw_tool_calls:
        tc_id = getattr(tc, "id", "") or ""
        fn = getattr(tc, "function", None)
        fn_name = getattr(fn, "name", "") if fn else ""
        raw_args = getattr(fn, "arguments", "") if fn else ""

        parsed_args: dict[str, Any] = {}
        err_msg: str | None = None

        if isinstance(raw_args, dict):
            parsed_args = raw_args
        elif isinstance(raw_args, str):
            trimmed = raw_args.strip()
            if not trimmed:
                parsed_args = {}
            else:
                try:
                    val = json.loads(trimmed)
                    if isinstance(val, dict):
                        parsed_args = val
                    else:
                        err_msg = (
                            f"Malformed tool call arguments: expected JSON object, "
                            f"got {type(val).__name__}: {raw_args}"
                        )
                        parsed_args = {"error": err_msg, "raw": raw_args}
                except json.JSONDecodeError as j_exc:
                    err_msg = f"Malformed JSON in tool call arguments: {j_exc}"
                    parsed_args = {"error": err_msg, "raw": raw_args}
        else:
            err_msg = f"Malformed arguments type: {type(raw_args).__name__}"
            parsed_args = {"error": err_msg, "raw": str(raw_args)}

        parsed_tool_calls.append(
            ToolCall(
                id=tc_id,
                name=fn_name,
                arguments=parsed_args,
                error=err_msg,
            )
        )

    # Token usage
    usage_info = {"input_tokens": 0, "output_tokens": 0}
    resp_usage = getattr(response, "usage", None)
    if resp_usage:
        input_tokens = getattr(resp_usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(resp_usage, "completion_tokens", 0) or 0
        usage_info = {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }

    return LLMReply(
        content=content,
        tool_calls=parsed_tool_calls,
        usage=usage_info,
    )
