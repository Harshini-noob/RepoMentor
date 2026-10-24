from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from groq import APIStatusError, RateLimitError

from app.agent.llm import chat
from app.agent.state import (
    AgentRun,
    LLMReply,
    ToolCall,
    ToolResult,
    TraceStep,
)


def _make_mock_response(
    content: str | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
    prompt_tokens: int = 15,
    completion_tokens: int = 25,
) -> SimpleNamespace:
    """Helper to build a mock Groq ChatCompletion response."""
    msg = SimpleNamespace(
        content=content,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(message=msg)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


@pytest.mark.asyncio
async def test_chat_text_response() -> None:
    mock_resp = _make_mock_response(content="Hello! How can I help you?", prompt_tokens=10, completion_tokens=8)
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = mock_resp

    messages = [{"role": "user", "content": "Hi"}]
    reply = await chat(messages=messages, client=mock_client, model="llama-3.3-70b-versatile")

    assert reply.content == "Hello! How can I help you?"
    assert reply.tool_calls == []
    assert reply.usage == {"input_tokens": 10, "output_tokens": 8}
    mock_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_chat_valid_tool_call() -> None:
    tc = SimpleNamespace(
        id="call_123",
        function=SimpleNamespace(
            name="read_file",
            arguments='{"path": "backend/app/main.py", "start_line": 1}',
        ),
    )
    mock_resp = _make_mock_response(content=None, tool_calls=[tc], prompt_tokens=50, completion_tokens=20)
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = mock_resp

    messages = [{"role": "user", "content": "Read main.py"}]
    tools = [{"type": "function", "function": {"name": "read_file"}}]

    reply = await chat(messages=messages, tools=tools, client=mock_client)

    assert reply.content is None
    assert len(reply.tool_calls) == 1
    call = reply.tool_calls[0]
    assert call.id == "call_123"
    assert call.name == "read_file"
    assert call.arguments == {"path": "backend/app/main.py", "start_line": 1}
    assert call.error is None
    assert reply.usage == {"input_tokens": 50, "output_tokens": 20}


@pytest.mark.asyncio
async def test_chat_malformed_json_tool_call() -> None:
    # Arguments have broken JSON (unclosed curly brace)
    tc = SimpleNamespace(
        id="call_broken",
        function=SimpleNamespace(
            name="search_code",
            arguments='{"query": "def parse_manifest", ',
        ),
    )
    mock_resp = _make_mock_response(content=None, tool_calls=[tc])
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = mock_resp

    reply = await chat(messages=[{"role": "user", "content": "search"}], client=mock_client)

    assert len(reply.tool_calls) == 1
    call = reply.tool_calls[0]
    assert call.id == "call_broken"
    assert call.name == "search_code"
    # Must not raise, but store the error text
    assert call.error is not None
    assert "Malformed JSON" in call.error
    assert "error" in call.arguments
    assert call.arguments["raw"] == '{"query": "def parse_manifest", '


@pytest.mark.asyncio
async def test_chat_non_dict_json_tool_call() -> None:
    # Arguments are valid JSON but not an object (e.g. array or primitive)
    tc = SimpleNamespace(
        id="call_list",
        function=SimpleNamespace(
            name="echo",
            arguments='["not", "a", "dict"]',
        ),
    )
    mock_resp = _make_mock_response(content=None, tool_calls=[tc])
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = mock_resp

    reply = await chat(messages=[{"role": "user", "content": "echo"}], client=mock_client)

    call = reply.tool_calls[0]
    assert call.error is not None
    assert "expected JSON object" in call.error
    assert "error" in call.arguments


@pytest.mark.asyncio
async def test_chat_retry_on_rate_limit() -> None:
    req = httpx.Request("POST", "https://api.groq.com/chat")
    resp_429 = httpx.Response(429, request=req)
    rate_err = RateLimitError("Rate limit reached", response=resp_429, body={"error": "rate_limit"})

    success_resp = _make_mock_response(content="Recovered after rate limit")

    mock_client = AsyncMock()
    # Fails first, succeeds second
    mock_client.chat.completions.create.side_effect = [rate_err, success_resp]

    reply = await chat(messages=[{"role": "user", "content": "retry test"}], client=mock_client, backoff=0.01)

    assert reply.content == "Recovered after rate limit"
    assert mock_client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_chat_retry_on_5xx_server_error() -> None:
    req = httpx.Request("POST", "https://api.groq.com/chat")
    resp_503 = httpx.Response(503, request=req)
    server_err = APIStatusError("Service unavailable", response=resp_503, body={"error": "overloaded"})

    success_resp = _make_mock_response(content="Recovered from 503")

    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = [server_err, success_resp]

    reply = await chat(messages=[{"role": "user", "content": "5xx test"}], client=mock_client, backoff=0.01)

    assert reply.content == "Recovered from 503"
    assert mock_client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_chat_rate_limit_persistent_raises() -> None:
    req = httpx.Request("POST", "https://api.groq.com/chat")
    resp_429 = httpx.Response(429, request=req)
    rate_err = RateLimitError("Rate limit reached", response=resp_429, body={"error": "rate_limit"})

    mock_client = AsyncMock()
    # Fails on both attempts
    mock_client.chat.completions.create.side_effect = [rate_err, rate_err]

    with pytest.raises(RateLimitError):
        await chat(messages=[{"role": "user", "content": "fail test"}], client=mock_client, backoff=0.01)

    assert mock_client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_chat_non_retryable_error_raises_immediately() -> None:
    req = httpx.Request("POST", "https://api.groq.com/chat")
    resp_400 = httpx.Response(400, request=req)
    bad_req_err = APIStatusError("Bad request: invalid schema", response=resp_400, body={"error": "bad_request"})

    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = bad_req_err

    with pytest.raises(APIStatusError) as exc_info:
        await chat(messages=[{"role": "user", "content": "bad request"}], client=mock_client, backoff=0.01)

    assert exc_info.value.status_code == 400
    # Must NOT retry on 400
    assert mock_client.chat.completions.create.call_count == 1


def test_pydantic_state_models() -> None:
    # 1. ToolCall
    tc = ToolCall(id="call_1", name="find_symbol", arguments={"symbol": "ingest_project"})
    assert tc.id == "call_1"
    assert tc.arguments["symbol"] == "ingest_project"
    assert tc.error is None

    # 2. ToolResult
    tr = ToolResult(tool_call_id="call_1", content="found in pipeline.py:127", evidence_ids=["f_1", "f_2"])
    assert tr.tool_call_id == "call_1"
    assert len(tr.evidence_ids) == 2

    # 3. TraceStep
    step = TraceStep(
        step=1,
        thought_summary="Locating ingestion pipeline",
        tool="find_symbol",
        args={"symbol": "ingest_project"},
        result_preview="found in pipeline.py:127",
        evidence_ids=["f_1"],
        ms=42,
        tokens=150,
    )
    assert step.step == 1
    assert step.ms == 42
    assert step.tokens == 150

    # 4. AgentRun
    run = AgentRun(
        goal="Explain architecture of repo",
        project_id="proj_sample",
        steps=[step],
        final_answer="The project uses FastAPI and Motor.",
        stop_reason="complete",
        total_tokens=150,
    )
    assert run.project_id == "proj_sample"
    assert len(run.steps) == 1
    assert run.stop_reason == "complete"
    assert run.total_tokens == 150

    # 5. LLMReply
    reply = LLMReply(content="Done", tool_calls=[tc], usage={"input_tokens": 100, "output_tokens": 50})
    assert reply.content == "Done"
    assert len(reply.tool_calls) == 1
    assert reply.usage["input_tokens"] == 100
