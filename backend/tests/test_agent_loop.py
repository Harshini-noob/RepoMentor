import pytest
import mongomock_motor
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.loop import run_agent
from app.agent.state import AgentRun, LLMReply, ToolCall
from app.agent.tools import build_registry


@pytest.fixture
def mock_db() -> AsyncIOMotorDatabase:
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_agent_loop_db"]


async def seed_project_data(mock_db: AsyncIOMotorDatabase) -> str:
    pid = "proj_agent_test"
    await mock_db.projects.insert_one({
        "_id": pid,
        "repo_full_name": "test/repo",
        "description": "Agent loop test repo",
        "default_branch": "main",
        "status": "ready",
    })
    await mock_db.files.insert_one({
        "project_id": pid,
        "path": "src/main.py",
        "size": 100,
        "language": "Python",
        "content": "print('hello agent')\n",
    })
    return pid


# (a) Answers immediately without tool calls
@pytest.mark.asyncio
async def test_agent_answers_immediately(mock_db: AsyncIOMotorDatabase) -> None:
    seed_project = await seed_project_data(mock_db)
    async def fake_chat(messages, tools=None):
        return LLMReply(
            content="This repository is a Python application that prints hello agent.",
            tool_calls=[],
            usage={"input_tokens": 50, "output_tokens": 15},
        )

    run: AgentRun = await run_agent(
        goal="What does this repository do?",
        project_id=seed_project,
        max_steps=5,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert "hello agent" in (run.final_answer or "")
    assert len(run.steps) == 0
    assert run.total_tokens == 65

    # Check saved trace in DB
    trace_doc = await mock_db.agent_traces.find_one({"project_id": seed_project})
    assert trace_doc is not None
    assert trace_doc["stop_reason"] == "answered"


# (b) Calls two tools then answers
@pytest.mark.asyncio
async def test_agent_calls_two_tools_then_answers(mock_db: AsyncIOMotorDatabase) -> None:
    seed_project = await seed_project_data(mock_db)
    step = 0

    async def fake_chat(messages, tools=None):
        nonlocal step
        step += 1
        if step == 1:
            return LLMReply(
                content="I will list the repository tree to see the structure.",
                tool_calls=[ToolCall(id="call_1", name="list_tree", arguments={"path": "", "depth": 2})],
                usage={"input_tokens": 100, "output_tokens": 20},
            )
        elif step == 2:
            return LLMReply(
                content="Now I will read src/main.py.",
                tool_calls=[ToolCall(id="call_2", name="read_file", arguments={"path": "src/main.py", "start": 1, "end": 10})],
                usage={"input_tokens": 150, "output_tokens": 25},
            )
        else:
            return LLMReply(
                content="src/main.py defines a script that outputs 'hello agent'.",
                tool_calls=[],
                usage={"input_tokens": 200, "output_tokens": 30},
            )

    run: AgentRun = await run_agent(
        goal="Explain src/main.py",
        project_id=seed_project,
        max_steps=5,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert len(run.steps) == 2
    assert run.steps[0].tool == "list_tree"
    assert run.steps[1].tool == "read_file"
    assert "file:src/main.py#L1-1" in run.steps[1].evidence_ids
    assert "outputs 'hello agent'" in (run.final_answer or "")
    assert run.total_tokens == 525


# (c) Repeats the same call -> guard intercepts and informs model
@pytest.mark.asyncio
async def test_agent_repeats_same_call_blocked_by_guard(mock_db: AsyncIOMotorDatabase) -> None:
    seed_project = await seed_project_data(mock_db)
    step = 0
    received_tool_responses = []

    async def fake_chat(messages, tools=None):
        nonlocal step
        step += 1
        # Track tool messages sent back
        for m in messages:
            if m.get("role") == "tool":
                received_tool_responses.append(m.get("content"))

        if step in (1, 2):
            # Propose the exact same tool call twice
            return LLMReply(
                content="Calling list_tree again.",
                tool_calls=[ToolCall(id=f"call_{step}", name="list_tree", arguments={"path": "", "depth": 2})],
                usage={"input_tokens": 80, "output_tokens": 15},
            )
        else:
            return LLMReply(
                content="Done after seeing repeated call notice.",
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 20},
            )

    run: AgentRun = await run_agent(
        goal="Test repeated call",
        project_id=seed_project,
        max_steps=5,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert len(run.steps) == 2
    # The second step must contain the guard's repeat message in the preview
    assert "already called tool 'list_tree'" in run.steps[1].result_preview


# (d) Never stops -> hits max_steps, triggers 70% wrap-up warning and final synthesis call
@pytest.mark.asyncio
async def test_agent_never_stops_hits_max_steps(mock_db: AsyncIOMotorDatabase) -> None:
    seed_project = await seed_project_data(mock_db)
    call_count = 0
    wrap_up_seen_in_messages = False

    async def fake_chat(messages, tools=None):
        nonlocal call_count, wrap_up_seen_in_messages
        call_count += 1
        # Check if 70% wrap-up system notice was injected into history
        for m in messages:
            if m.get("role") == "system" and "70% budget reached" in str(m.get("content")):
                wrap_up_seen_in_messages = True

        if tools is None:
            # Final synthesis question asked when budget is exhausted
            return LLMReply(
                content="Final Answer: Reached step budget. Determined file exists, but could not complete deep search.",
                tool_calls=[],
                usage={"input_tokens": 120, "output_tokens": 35},
            )

        # Perpetually call a new path so repeated-call guard doesn't trigger
        return LLMReply(
            content=f"Searching step {call_count}",
            tool_calls=[ToolCall(id=f"call_{call_count}", name="list_tree", arguments={"path": f"dir_{call_count}", "depth": 1})],
            usage={"input_tokens": 50, "output_tokens": 10},
        )

    run: AgentRun = await run_agent(
        goal="Run forever",
        project_id=seed_project,
        max_steps=4,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "max_steps"
    assert len(run.steps) == 4
    assert wrap_up_seen_in_messages is True
    assert "Reached step budget" in (run.final_answer or "")
    assert "could not complete deep search" in (run.final_answer or "")
