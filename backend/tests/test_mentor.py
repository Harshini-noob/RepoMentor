import asyncio
from mongomock_motor import AsyncMongoMockClient
import pytest

from app.agent.evidence import count_tags, find_uncited_facts
from app.agent.loop import run_agent
from app.agent.state import LLMReply
from app.cli import _async_chat
from app.mentor.chat import ask, build_mentor_prompt
from app.mentor.sessions import (
    append_turn,
    create_session,
    get_session,
    recent_history,
)


@pytest.mark.asyncio
async def test_create_and_get_session() -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]

    session_id = await create_session(db, "proj-123")
    assert isinstance(session_id, str)
    assert len(session_id) > 10

    doc = await get_session(db, session_id)
    assert doc is not None
    assert doc["session_id"] == session_id
    assert doc["project_id"] == "proj-123"
    assert doc["mode"] == "mentor"
    assert doc["messages"] == []
    assert doc["summary"] == ""
    assert "created_at" in doc
    assert "updated_at" in doc

    assert await get_session(db, "missing-id") is None


def test_recent_history_retention_and_trimming() -> None:
    long_answer = "B" * 1500
    session = {
        "messages": [
            {"role": "user", "content": "u1", "ts": "t1", "meta": {}},
            {"role": "assistant", "content": "a1", "ts": "t2", "meta": {}},
            {"role": "user", "content": "u2", "ts": "t3", "meta": {}},
            {"role": "assistant", "content": "a2", "ts": "t4", "meta": {}},
            {"role": "tool", "content": "tool output", "tool_call_id": "c1"},  # should be ignored
            {"role": "user", "content": "u3", "ts": "t5", "meta": {}},
            {"role": "assistant", "content": "a3", "ts": "t6", "meta": {}},
            {"role": "user", "content": "u4", "ts": "t7", "meta": {}},
            {"role": "assistant", "content": long_answer, "ts": "t8", "meta": {}},
            {"role": "user", "content": "u5", "ts": "t9", "meta": {}},
            {"role": "assistant", "content": "a5", "ts": "t10", "meta": {}},
        ]
    }

    history = recent_history(session, n_exchanges=3, max_chars=1200)

    # Last 3 exchanges = 6 messages: u3, a3, u4, a4, u5, a5
    assert len(history) == 6
    assert all(m["role"] in {"user", "assistant"} for m in history)
    assert history[0] == {"role": "user", "content": "u3"}
    assert history[1] == {"role": "assistant", "content": "a3"}
    assert history[2] == {"role": "user", "content": "u4"}

    # Assistant answer cut to 1200 chars + "..."
    cut_assistant = history[3]
    assert cut_assistant["role"] == "assistant"
    assert len(cut_assistant["content"]) == 1203
    assert cut_assistant["content"] == ("B" * 1200) + "..."

    assert history[4] == {"role": "user", "content": "u5"}
    assert history[5] == {"role": "assistant", "content": "a5"}

    assert recent_history(None) == []
    assert recent_history({}, n_exchanges=0) == []


@pytest.mark.asyncio
async def test_ask_saves_turn_and_sends_history() -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]
    session_id = await create_session(db, "proj-mentor")

    recorded_calls: list[dict] = []

    async def fake_chat(**kwargs):
        recorded_calls.append(kwargs)
        turn_num = len(recorded_calls)
        return LLMReply(
            content=f"[General] Answer {turn_num} for discussion.",
            usage={"input_tokens": 15, "output_tokens": 10},
        )

    # First turn
    run1 = await ask(
        db,
        "proj-mentor",
        session_id,
        "First question?",
        chat_fn=fake_chat,
    )
    assert run1.stop_reason == "answered"
    assert run1.final_answer == "[General] Answer 1 for discussion."

    # Check turn was saved to session
    sess = await get_session(db, session_id)
    assert len(sess["messages"]) == 2
    assert sess["messages"][0]["role"] == "user"
    assert sess["messages"][0]["content"] == "First question?"
    assert sess["messages"][1]["role"] == "assistant"
    assert sess["messages"][1]["content"] == "[General] Answer 1 for discussion."
    assert sess["messages"][1]["meta"]["tokens"] == 25
    assert sess["messages"][1]["meta"]["stop_reason"] == "answered"

    # Second turn
    run2 = await ask(
        db,
        "proj-mentor",
        session_id,
        "Second question?",
        chat_fn=fake_chat,
    )
    assert run2.stop_reason == "answered"
    assert len(recorded_calls) == 2

    second_call_messages = recorded_calls[1]["messages"]
    assert second_call_messages[0]["role"] == "system"
    assert "RepoMentor" in second_call_messages[0]["content"]
    assert second_call_messages[1] == {"role": "user", "content": "First question?"}
    assert second_call_messages[2] == {
        "role": "assistant",
        "content": "[General] Answer 1 for discussion.",
    }
    assert second_call_messages[3] == {"role": "user", "content": "Second question?"}

    sess_after = await get_session(db, session_id)
    assert len(sess_after["messages"]) == 4


@pytest.mark.asyncio
async def test_turn_ending_in_error_is_not_saved() -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]
    session_id = await create_session(db, "proj-mentor")

    async def failing_chat(**kwargs):
        raise RuntimeError("Model unreachable")

    run = await ask(
        db,
        "proj-mentor",
        session_id,
        "This will fail",
        chat_fn=failing_chat,
    )
    assert run.stop_reason == "error"

    sess = await get_session(db, session_id)
    # Failed turn must NOT be saved
    assert len(sess["messages"]) == 0


@pytest.mark.asyncio
async def test_run_agent_with_history_and_system_prompt() -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]

    recorded_calls: list[dict] = []

    async def fake_chat(**kwargs):
        recorded_calls.append(kwargs)
        return LLMReply(content="[General] Done.")

    custom_sys = "You are a custom test agent."
    history = [
        {"role": "user", "content": "past question"},
        {"role": "assistant", "content": "past answer"},
    ]

    await run_agent(
        goal="current goal",
        project_id="proj-custom",
        system_prompt=custom_sys,
        history=history,
        chat_fn=fake_chat,
        db=db,
    )

    msgs = recorded_calls[0]["messages"]
    assert len(msgs) == 4
    assert msgs[0] == {"role": "system", "content": custom_sys}
    assert msgs[1] == {"role": "user", "content": "past question"}
    assert msgs[2] == {"role": "assistant", "content": "past answer"}
    assert msgs[3] == {"role": "user", "content": "current goal"}

    # With both None, behaves as before
    recorded_calls.clear()
    await run_agent(
        goal="goal alone",
        project_id="proj-custom",
        system_prompt=None,
        history=None,
        chat_fn=fake_chat,
        db=db,
    )
    msgs_default = recorded_calls[0]["messages"]
    assert len(msgs_default) == 2
    assert msgs_default[0]["role"] == "system"
    assert "investigator" in msgs_default[0]["content"].lower() or "github" in msgs_default[0]["content"].lower()
    assert msgs_default[1] == {"role": "user", "content": "goal alone"}


def test_count_tags_counts_general() -> None:
    text = "[General] General knowledge. [general] another one. [Fact] fact 1 file:test.py#L1-10."
    counts = count_tags(text)
    assert counts["General"] == 2
    assert counts["Fact"] == 1
    assert counts["Inferred"] == 0
    assert counts["Uncertain"] == 0

    # Answer made only of [General] lines is not treated as untagged
    general_only = "[General] No repo evidence needed here."
    assert any(count_tags(general_only).values()) is True

    # find_uncited_facts must still look only at [Fact] lines
    mixed = (
        "[General] General explanation without citation.\n\n"
        "[Fact] A fact without citation.\n\n"
        "[Fact] A cited fact file:foo.py#L1-5."
    )
    uncited = find_uncited_facts(mixed)
    assert len(uncited) == 1
    assert uncited[0] == "[Fact] A fact without citation."


@pytest.mark.asyncio
async def test_cli_chat_flow_and_validation(capsys) -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]

    # 1. Invalid session id
    code = await _async_chat("p1", session_id="nonexistent-id", db=db)
    assert code == 1

    # 2. Mismatched project id
    other_session_id = await create_session(db, "other-project")
    code = await _async_chat("p1", session_id=other_session_id, db=db)
    assert code == 1

    # 3. Successful chat session with exit command
    inputs = ["why use mongodb?", "/exit"]
    input_iter = iter(inputs)

    async def mock_ask(db, project_id, session_id, message, on_step=None, on_event=None):
        return await ask(
            db,
            project_id,
            session_id,
            message,
            chat_fn=lambda **kw: asyncio.sleep(0, result=LLMReply(content="[General] Good choice.")),
        )

    code = await _async_chat(
        "p1",
        db=db,
        ask_fn=mock_ask,
        input_fn=lambda: next(input_iter),
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "[General] Good choice." in out
    assert "Stop Reason: answered" in out


from app.agent.llm import LLMDailyLimitError, LLMRateLimitError, chat
from app.agent.tools.get_dossier import DossierArgs, DossierTool
from openai import RateLimitError
import httpx


@pytest.mark.asyncio
async def test_get_dossier_query_ordering_and_no_match() -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]
    project_id = "proj-supabase"

    await db.dossiers.insert_one({
        "project_id": project_id,
        "version": 1,
        "generated_from_sha": "abc123456789",
        "sections": {
            "decisions": [
                {
                    "claim": "Use Supabase database",
                    "tag": "fact",
                    "evidence": ["file:src/supabase.py#L1-10"],
                    "details": {"commit": "111222333444"},
                }
            ],
            "technologies": [
                {
                    "claim": "Supabase client configured",
                    "tag": "fact",
                    "evidence": ["file:src/tech.py#L1-5"],
                }
            ],
            "components": [
                {
                    "claim": "Database wrapper module",
                    "tag": "inferred",
                    "evidence": ["file:src/supabase_client.py#L1-20"],
                }
            ],
            "quality": [
                {
                    "claim": "Comprehensive test suite",
                    "tag": "fact",
                    "evidence": ["file:tests/test_all.py#L1"],
                }
            ],
        },
    })

    tool = DossierTool(db=db)

    # Search for "supabase"
    out = await tool.run(project_id, DossierArgs(query="supabase"))
    lines = out.text.splitlines()
    assert lines[0] == "3 claims match 'supabase' across all sections"
    assert len(lines) == 4

    # Decisions claim has score 1.5 (word in claim + word in evidence), so it comes first
    assert lines[1].startswith("decisions | [Fact] Use Supabase database [file:src/supabase.py#L1-10] (commit 1112223)")
    # Technologies claim has score 1.0 (word in claim)
    assert lines[2].startswith("technologies | [Fact] Supabase client configured [file:src/tech.py#L1-5]")
    # Components claim has score 0.5 (word in evidence only)
    assert lines[3].startswith("components | [Inferred] Database wrapper module [file:src/supabase_client.py#L1-20]")

    # Check evidence_ids
    assert "file:src/supabase.py#L1-10" in out.evidence_ids
    assert "file:src/tech.py#L1-5" in out.evidence_ids
    assert "file:src/supabase_client.py#L1-20" in out.evidence_ids

    # Query with section specified
    out_sec = await tool.run(project_id, DossierArgs(query="supabase", section="technologies"))
    assert "1 claims match 'supabase' across all sections" in out_sec.text
    assert "technologies |" in out_sec.text
    assert "decisions |" not in out_sec.text

    # No match query
    no_match = await tool.run(project_id, DossierArgs(query="nonexistent_keyword"))
    assert no_match.text == (
        "No dossier claims match 'nonexistent_keyword'. "
        "Call get_dossier() to see the sections, or use search_code."
    )


@pytest.mark.asyncio
async def test_rate_limit_error_final_answer_and_no_org() -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]

    async def rate_limited_chat(**kwargs):
        raise LLMRateLimitError("per-minute token limit reached")

    run = await run_agent(
        goal="Test rate limit handling",
        project_id="proj-rl",
        chat_fn=rate_limited_chat,
        db=db,
    )

    assert run.stop_reason == "error"
    assert run.final_answer is not None
    assert "The model's token limit was reached before I could finish." in run.final_answer
    assert "Try again in a minute." in run.final_answer
    assert "org_" not in run.final_answer
    assert "gemini" not in run.final_answer.lower()
    assert "gpt" not in run.final_answer.lower()


@pytest.mark.asyncio
async def test_quote_check_skipped_for_fallback_text() -> None:
    client = AsyncMongoMockClient()
    db = client["repomentor_test"]

    async def rate_limited_chat(**kwargs):
        raise LLMRateLimitError("per-minute token limit reached")

    run = await run_agent(
        goal="Test quote check skipping",
        project_id="proj-skip",
        chat_fn=rate_limited_chat,
        db=db,
    )

    assert run.stop_reason == "error"
    # Even though words in the fallback text never appeared in tool outputs, quote checks are skipped
    assert "[Quotes not found in any tool result:" not in run.final_answer
    assert "[Unverified citations" not in run.final_answer
    assert "[No claim tags were produced]" not in run.final_answer


@pytest.mark.asyncio
async def test_chat_failover_on_long_429(monkeypatch) -> None:
    events = []
    call_models = []

    class FakeCompletions:
        async def create(self, **kwargs):
            m = kwargs["model"]
            call_models.append(m)
            if m == "primary-model":
                request = httpx.Request("POST", "http://localhost")
                response = httpx.Response(
                    429,
                    request=request,
                    headers={"retry-after": "10"},
                    json={"error": {"message": "Rate limit exceeded"}},
                )
                raise RateLimitError("Rate limit", response=response, body={"error": "rate limit"})
            elif m == "fallback-model":
                class Msg:
                    content = "[General] Fallback model reply"
                    tool_calls = None
                class Choice:
                    message = Msg()
                class Resp:
                    choices = [Choice()]
                    usage = None
                return Resp()
            raise RuntimeError(f"Unexpected model: {m}")

    class FakeClient:
        class chat:
            class completions:
                create = FakeCompletions().create

    async def fast_sleep(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_model", "primary-model")
    monkeypatch.setattr(settings, "llm_fallback_model", "fallback-model")

    reply = await chat(
        messages=[{"role": "user", "content": "hi"}],
        client=FakeClient(),
        on_event=events.append,
    )

    assert reply.content == "[General] Fallback model reply"
    assert call_models == ["primary-model", "fallback-model"]
    assert any("switching to fallback-model (rate limit on primary-model)" in e for e in events)

    # When no fallback is set:
    monkeypatch.setattr(settings, "llm_fallback_model", None)
    call_models.clear()
    events.clear()

    with pytest.raises(LLMRateLimitError):
        await chat(
            messages=[{"role": "user", "content": "hi"}],
            client=FakeClient(),
            on_event=events.append,
            backoff=10.0,
        )
    assert set(call_models) == {"primary-model"}
    assert not any("switching" in e for e in events)
