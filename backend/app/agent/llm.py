"""
LLM provider wrapper — OpenAI-compatible client.

Why a wrapper?
--------------
All LLM providers (Gemini via v1beta/openai, Groq, OpenAI, etc.) expose the
same OpenAI-compatible REST API. Routing through this single wrapper means:
  * The rest of the codebase never imports a provider SDK directly.
  * Switching providers requires only changing LLM_API_KEY / LLM_BASE_URL /
    LLM_MODEL in .env — no code changes.
  * Retry logic, token accounting, parse-error recovery, and redaction live in
    one place and are tested once.
"""

import asyncio
import json
import logging
import re
from typing import Any, Callable

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    BadRequestError,
    RateLimitError,
)

from app.agent.state import LLMReply, ToolCall
from app.config import get_settings
from app.core.logging import redact

logger = logging.getLogger("repomentor.agent.llm")

REQUEST_TIMEOUT_S = 60.0
MAX_TOTAL_BACKOFF_S = 75.0
MAX_TRANSIENT_RETRIES = 2


class LLMOutputParseError(Exception):
    """Raised when the LLM outputs malformed tool calls or unparseable output after retries."""

    def __init__(self, message: str, failed_generation: str | None = None) -> None:
        super().__init__(message)
        self.failed_generation = failed_generation


class LLMDailyLimitError(Exception):
    """Raised when the provider reports that the model's daily token quota is exhausted."""


class LLMRateLimitError(Exception):
    """Raised when a per-minute rate limit is reached and retries are exhausted or wait is too long."""


_client: AsyncOpenAI | None = None


def get_llm_client() -> AsyncOpenAI:
    """Retrieve or initialize the AsyncOpenAI-compatible client singleton using config."""
    global _client
    if _client is None:
        settings = get_settings()
        api_key = getattr(settings, "llm_api_key", None) or getattr(
            settings, "groq_api_key", None
        )
        if not api_key:
            raise ValueError(
                "No LLM API key configured. Set LLM_API_KEY (or GROQ_API_KEY) in backend/.env"
            )
        base_url = getattr(settings, "llm_base_url", None)  # None → uses OpenAI default
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=REQUEST_TIMEOUT_S,
            max_retries=0,
        )
    return _client


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an error is a rate-limit (429) or transient 5xx server error."""
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", 0)
        return status == 429 or status >= 500
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    return False


def _retry_after_seconds(exc: Exception, default: float) -> float:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            return float(headers.get("retry-after", default))
        except (TypeError, ValueError):
            pass
    return default


def _extract_parse_failed_generation(exc: BadRequestError) -> tuple[bool, str | None]:
    """
    Check if a BadRequestError represents an output_parse_failed or tool_use_failed error.
    Returns (is_parse_error, failed_generation).
    """
    body = getattr(exc, "body", None)
    err_obj = body.get("error", {}) if isinstance(body, dict) else {}
    code = err_obj.get("code")
    err_type = err_obj.get("type")
    msg = str(err_obj.get("message", "") or str(exc))

    failed_gen = err_obj.get("failed_generation")

    is_parse = (
        code in ("output_parse_failed", "tool_use_failed")
        or err_type in ("output_parse_failed", "tool_use_failed")
        or "output_parse_failed" in msg
        or "tool_use_failed" in msg
    )
    return is_parse, failed_gen


async def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    client: Any = None,
    model: str | None = None,
    backoff: float = 1.0,
    on_event: Callable[[str], None] | None = None,
) -> LLMReply:
    """
    Execute a chat completion via the OpenAI-compatible API.

    Retries transient API errors with bounded backoff.
    Fails over to LLM_FALLBACK_MODEL on long 429 wait times (> 5s).
    Raises LLMRateLimitError or LLMDailyLimitError on rate limit exhaustion.
    Retries up to 2 times on BadRequestError with 'output_parse_failed' or 'tool_use_failed',
    raising LLMOutputParseError if all retries fail.
    Parses tool-call arguments with json.loads and, if malformed,
    returns the error text in the ToolCall object instead of crashing.
    """
    settings = get_settings()
    primary_model = (
        model
        or getattr(settings, "llm_model", None)
        or getattr(settings, "groq_model", None)
    )
    fallback_model = getattr(settings, "llm_fallback_model", None) or ""
    current_model = primary_model
    fallback_used = False

    llm_client = client or get_llm_client()

    def emit(text: str) -> None:
        if on_event is not None:
            try:
                on_event(text)
            except Exception:
                pass

    kwargs: dict[str, Any] = {
        "model": current_model,
        "messages": messages,
    }
    if current_model and current_model.startswith("openai/gpt-oss"):
        configured_fields = getattr(settings, "model_fields_set", None)
        llm_effort = getattr(settings, "llm_reasoning_effort", None)
        if llm_effort and (
            configured_fields is None or "llm_reasoning_effort" in configured_fields
        ):
            reasoning_effort = llm_effort
        else:
            reasoning_effort = (
                getattr(settings, "groq_reasoning_effort", None)
                or llm_effort
                or "low"
            )
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    response = None
    parse_retry_count = 0
    max_parse_retries = 2
    retries_so_far = 0
    total_wait = 0.0

    while True:
        try:
            kwargs["model"] = current_model
            response = await llm_client.chat.completions.create(**kwargs)
            break
        except BadRequestError as b_exc:
            is_parse_err, failed_gen = _extract_parse_failed_generation(b_exc)
            if is_parse_err:
                gen_preview = redact(str(failed_gen or ""))[:300]
                logger.warning(
                    "LLM BadRequestError: output_parse_failed (attempt %d/%d). Failed generation: %s",
                    parse_retry_count + 1,
                    max_parse_retries + 1,
                    gen_preview,
                )
                if parse_retry_count < max_parse_retries:
                    parse_retry_count += 1
                    emit(f"model output was malformed, retrying ({parse_retry_count}/2)")
                    await asyncio.sleep(0.5)
                    continue
                raise LLMOutputParseError(
                    f"LLM API failed with output_parse_failed after {parse_retry_count} retries: {b_exc}",
                    failed_generation=failed_gen,
                ) from b_exc
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            error_text = f"{exc} {getattr(exc, 'body', '')}".lower()
            is_429 = (status == 429 or isinstance(exc, RateLimitError))
            is_daily = is_429 and (
                "tokens per day" in error_text or re.search(r"\btpd\b", error_text)
            )

            would_wait = (
                float("inf")
                if is_daily
                else _retry_after_seconds(exc, backoff * 2**retries_so_far)
            )

            # Failover: if 429 would wait > 5s and a different fallback model is set
            if (
                is_429
                and would_wait > 5.0
                and fallback_model
                and fallback_model != current_model
                and not fallback_used
            ):
                fallback_used = True
                emit(f"switching to {fallback_model} (rate limit on {current_model})")
                logger.warning(
                    "Switching to %s (rate limit on %s)", fallback_model, current_model
                )
                current_model = fallback_model
                if current_model.startswith("openai/gpt-oss"):
                    kwargs["extra_body"] = {"reasoning_effort": "low"}
                elif "extra_body" in kwargs and not current_model.startswith("openai/gpt-oss"):
                    kwargs.pop("extra_body", None)
                continue

            if is_daily:
                raise LLMDailyLimitError(
                    f"Daily token limit reached for model {current_model}. "
                    "Try again later or change LLM_MODEL."
                ) from exc

            if not _is_retryable_error(exc) or retries_so_far >= MAX_TRANSIENT_RETRIES:
                if is_429:
                    raise LLMRateLimitError("per-minute token limit reached") from exc
                raise

            wait = would_wait
            if total_wait + wait > MAX_TOTAL_BACKOFF_S:
                emit(f"rate limit wait of {wait:g}s is too long, giving up")
                if is_429:
                    raise LLMRateLimitError("per-minute token limit reached") from exc
                raise

            emit(f"waiting {wait:g}s before retry ({type(exc).__name__})")
            logger.warning(
                "LLM API call encountered transient error (%s). Retrying in %.2fs...",
                exc,
                wait,
            )
            await asyncio.sleep(wait)
            total_wait += wait
            retries_so_far += 1

    if response is None:
        raise RuntimeError("No response received from LLM API")

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
