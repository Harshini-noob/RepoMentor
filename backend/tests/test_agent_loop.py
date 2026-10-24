import pytest
import mongomock_motor
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.agent.guards import RepeatedCallGuard
from app.agent.llm import LLMDailyLimitError, LLMOutputParseError
from app.agent.loop import _trim_old_tool_results, run_agent
from app.agent.state import AgentRun, LLMReply, ToolCall
from app.agent.tools import SearchCodeTool, build_registry


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
            content="[Fact] This repository is a Python application that prints hello\u200bagent\u200c.\u200d\ufeff",
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
    assert run.final_answer == "[Fact] This repository is a Python application that prints helloagent."
    assert len(run.steps) == 0
    assert run.total_tokens == 65

    # Check saved trace in DB
    trace_doc = await mock_db.agent_traces.find_one({"project_id": seed_project})
    assert trace_doc is not None
    assert trace_doc["stop_reason"] == "answered"


@pytest.mark.asyncio
async def test_malformed_citation_is_added_to_unverified_note(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    async def fake_chat(messages, tools=None):
        return LLMReply(content="Citation [pr:abc]. [Uncertain]", tool_calls=[], usage={})

    run = await run_agent(
        goal="Test malformed citation",
        project_id="proj_malformed_citation",
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.final_answer == (
        "Citation [pr:abc]. [Uncertain]\n\n"
        "[Unverified citations, not backed by any tool result in this run: "
        "malformed citation: pr:abc]"
    )


@pytest.mark.asyncio
async def test_untagged_answer_is_nudged_then_tagged_answer_is_accepted(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0
    nudge_text = (
        "Rewrite your answer with every claim tagged [Fact] (a tool result shows it), "
        "[Inferred] (reasoned from several observations) or [Uncertain] (not supported), "
        "keeping the same citations. Do not call tools unless needed."
    )

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMReply(content="The project uses Python.", tool_calls=[])
        assert any(
            message.get("role") == "user" and message.get("content") == nudge_text
            for message in messages
        )
        return LLMReply(content="[Fact] The project uses Python.", tool_calls=[])

    run = await run_agent(
        goal="What language does the project use?",
        project_id=project_id,
        max_steps=3,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.final_answer == "[Fact] The project uses Python."
    assert call_count == 2
    assert [step.tool for step in run.steps] == ["policy_nudge"]


@pytest.mark.asyncio
async def test_tagged_answer_does_not_receive_tag_nudge(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        return LLMReply(content="[Inferred] This is a Python project.", tool_calls=[])

    run = await run_agent(
        goal="Summarize the project.",
        project_id=project_id,
        max_steps=3,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.final_answer == "[Inferred] This is a Python project."
    assert call_count == 1
    assert run.steps == []


@pytest.mark.asyncio
async def test_second_untagged_answer_is_accepted_with_note(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        return LLMReply(content=f"Untagged answer {call_count}.", tool_calls=[])

    run = await run_agent(
        goal="Summarize the project.",
        project_id=project_id,
        max_steps=3,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.final_answer == "Untagged answer 2.\n\n[No claim tags were produced]"
    assert call_count == 2
    assert [step.tool for step in run.steps] == ["policy_nudge"]


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
                content="[Fact] src/main.py defines a script that outputs 'hello agent'.",
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
                content="[Fact] Done after seeing repeated call notice.",
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
    assert run.steps[1].result_preview.startswith(
        "(repeated call, returning the earlier result)"
    )
    assert "already called tool 'list_tree'" not in run.steps[1].result_preview
    assert run.steps[0].result_preview[:80] in run.steps[1].result_preview


@pytest.mark.asyncio
async def test_agent_nudges_once_after_second_repeated_call_result(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    seed_project = await seed_project_data(mock_db)
    call_count = 0
    nudge_counts: list[int] = []
    nudge_text = (
        "Searches are not producing new evidence. Answer now from what you already have; "
        "mark anything unsupported [Uncertain] and say what you could not check."
    )

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        nudge_counts.append(
            sum(
                message.get("role") == "user" and message.get("content") == nudge_text
                for message in messages
            )
        )
        if call_count < 5:
            return LLMReply(
                content="Repeating list_tree.",
                tool_calls=[
                    ToolCall(
                        id=f"repeat_{call_count}",
                        name="list_tree",
                        arguments={"path": "", "depth": 1},
                    )
                ],
                usage={},
            )
        return LLMReply(content="[Fact] Final answer.", tool_calls=[], usage={})

    run = await run_agent(
        goal="Test repeated searches",
        project_id=seed_project,
        max_steps=6,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert nudge_counts == [0, 0, 0, 1, 1]
    assert "policy_nudge" in [step.tool for step in run.steps]


def test_repeated_call_guard_drops_default_arguments_only() -> None:
    guard = RepeatedCallGuard()
    assert not guard.is_repeated("get_commit", {"sha": "a1b2c3d", "path": "src/main.py"})
    assert guard.is_repeated(
        "get_commit",
        {"sha": "a1b2c3d", "path": "src/main.py", "offset": 0},
    )

    assert not guard.is_repeated("search_code", {"query": "needle"})
    assert guard.is_repeated(
        "search_code",
        {"query": "needle", "limit": 10, "path_glob": ""},
    )
    assert not guard.is_repeated("search_code", {"query": "needle", "limit": 15})


@pytest.mark.asyncio
async def test_empty_search_results_trigger_stagnation_nudge_once(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0
    nudge_text = (
        "Searches are not producing new evidence. Answer now from what you already have; "
        "mark anything unsupported [Uncertain] and say what you could not check."
    )
    nudge_seen: list[bool] = []

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        nudge_seen.append(any(
            message.get("role") == "user" and message.get("content") == nudge_text
            for message in messages
        ))
        if call_count <= 2:
            return LLMReply(
                content="Searching.",
                tool_calls=[
                    ToolCall(
                        id=f"search_{call_count}",
                        name="search_code",
                        arguments={"query": f"missing_{call_count}"},
                    )
                ],
            )
        return LLMReply(content="[Uncertain] Nothing was found.", tool_calls=[])

    run = await run_agent(
        goal="Find the missing details.",
        project_id=project_id,
        max_steps=5,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert nudge_seen == [False, False, True]
    assert [step.tool for step in run.steps].count("policy_nudge") == 1
    assert run.final_answer == "[Uncertain] Nothing was found."


@pytest.mark.asyncio
async def test_unverified_quote_note_uses_tool_results(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    await mock_db.files.update_one(
        {"project_id": project_id, "path": "src/main.py"},
        {"$set": {"content": "the configured timeout is 30 seconds\n"}},
    )
    call_count = 0

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMReply(
                content="Reading the file.",
                tool_calls=[
                    ToolCall(
                        id="read_file",
                        name="read_file",
                        arguments={"path": "src/main.py", "start": 1, "end": 1},
                    )
                ],
            )
        return LLMReply(
            content=(
                '[Fact] The file says "the configured timeout is 30 seconds"; '
                'it does not say "fabricated setting value number 1", '
                '"fabricated setting value number 2", "fabricated setting value number 3", '
                '"fabricated setting value number 4", "fabricated setting value number 5", '
                'or "fabricated setting value number 6".'
            ),
            tool_calls=[],
        )

    run = await run_agent(
        goal="What timeout is configured?",
        project_id=project_id,
        max_steps=4,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.final_answer is not None
    assert '"the configured timeout is 30 seconds"' in run.final_answer
    quote_note = run.final_answer.split("[Quotes not found in any tool result: ", 1)[1]
    assert quote_note.count('"fabricated setting value') == 5
    assert quote_note.endswith("(+1 more)]")


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
    assert len(run.steps) == 5
    assert [step.tool for step in run.steps].count("list_tree") == 4
    assert [step.tool for step in run.steps].count("policy_nudge") == 1
    assert wrap_up_seen_in_messages is True
    assert "Reached step budget" in (run.final_answer or "")
    assert "could not complete deep search" in (run.final_answer or "")


# ---------------------------------------------------------------------------
# Phase 3 Fix Tests: Output parse error recovery & guards
# ---------------------------------------------------------------------------

# (a) One parse error then a normal tool call, so the run continues
@pytest.mark.asyncio
async def test_agent_recovers_from_single_parse_error(mock_db: AsyncIOMotorDatabase) -> None:
    seed_project = await seed_project_data(mock_db)
    call_idx = 0
    received_recovery_msg = False

    async def fake_chat(messages, tools=None):
        nonlocal call_idx, received_recovery_msg
        call_idx += 1

        for m in messages:
            if m.get("role") == "user" and "Your previous reply was invalid" in str(m.get("content")):
                received_recovery_msg = True

        if call_idx == 1:
            # Raise parse error on first attempt
            raise LLMOutputParseError("Failed to parse output", failed_generation="<invalid tool call>")
        elif call_idx == 2:
            # Successfully call list_tree
            return LLMReply(
                content="Calling list_tree after recovery prompt.",
                tool_calls=[ToolCall(id="call_rec_1", name="list_tree", arguments={"path": ""})],
                usage={"input_tokens": 100, "output_tokens": 20},
            )
        else:
            # Provide final answer
            return LLMReply(
                content="Final Answer: [Fact] Found src/main.py [file:src/main.py#L1].",
                tool_calls=[],
                usage={"input_tokens": 120, "output_tokens": 25},
            )

    run: AgentRun = await run_agent(
        goal="Test parse error recovery",
        project_id=seed_project,
        max_steps=5,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert received_recovery_msg is True
    assert len(run.steps) == 2  # step 1: error record, step 2: list_tree
    assert run.steps[0].tool == "error"
    assert run.steps[1].tool == "list_tree"
    assert "Found src/main.py" in (run.final_answer or "")


# (b) Three parse errors, so the run finalizes with a non-empty answer
@pytest.mark.asyncio
async def test_agent_three_parse_errors_finalizes(mock_db: AsyncIOMotorDatabase) -> None:
    seed_project = await seed_project_data(mock_db)
    parse_calls = 0

    async def fake_chat(messages, tools=None):
        nonlocal parse_calls
        if tools is None:
            # Final synthesis call without tools
            return LLMReply(
                content="Final Answer: [Uncertain] Could not inspect repository due to repeated malformed model replies.",
                tool_calls=[],
                usage={"input_tokens": 150, "output_tokens": 30},
            )

        parse_calls += 1
        raise LLMOutputParseError(f"Bad generation #{parse_calls}", failed_generation="bad text")

    run: AgentRun = await run_agent(
        goal="Test 3 parse errors",
        project_id=seed_project,
        max_steps=6,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert parse_calls == 3
    assert run.stop_reason == "error"
    assert run.final_answer is not None
    assert "[Uncertain]" in run.final_answer


# (c) The fallback answer when the finalization call also fails
@pytest.mark.asyncio
async def test_agent_fallback_answer_when_finalization_fails(mock_db: AsyncIOMotorDatabase) -> None:
    seed_project = await seed_project_data(mock_db)
    extra_files = [
        {
            "project_id": seed_project,
            "path": f"src/extra_{index}.py",
            "content": f"value = {index}\n",
            "size": 10,
        }
        for index in range(11)
    ]
    await mock_db.files.insert_many(extra_files)
    call_idx = 0

    async def fake_chat(messages, tools=None):
        nonlocal call_idx
        call_idx += 1

        if tools is not None and call_idx == 1:
            # First call succeeds and gathers evidence
            return LLMReply(
                content="Inspecting src/main.py",
                tool_calls=[
                    ToolCall(
                        id=f"c{index}",
                        name="read_file",
                        arguments={"path": path, "start": 1, "end": 2},
                    )
                    for index, path in enumerate(
                        ["src/main.py", *(file["path"] for file in extra_files)]
                    )
                ],
                usage={"input_tokens": 80, "output_tokens": 20},
            )
        # All subsequent calls (including tools=None finalization) fail completely
        raise RuntimeError("Network timeout to model provider")

    run: AgentRun = await run_agent(
        goal="Test fallback answer",
        project_id=seed_project,
        max_steps=3,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "error"
    assert run.final_answer is not None
    assert "Investigation terminated before completion" in run.final_answer
    assert "read_file" in run.final_answer
    evidence_summary = run.final_answer.split("Evidence IDs gathered: ", 1)[1].split(
        ". Could not determine", 1
    )[0]
    assert evidence_summary.count("file:") == 10
    assert evidence_summary.endswith("(+2 more)")
    assert "Could not determine a definitive final answer" in run.final_answer


@pytest.mark.asyncio
async def test_daily_token_limit_finishes_with_provider_message(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    message = (
        "Daily token limit reached for model test-model. "
        "Try again later or change LLM_MODEL."
    )

    async def fake_chat(messages, tools=None):
        raise LLMDailyLimitError(message)

    run = await run_agent(
        goal="Inspect the repository",
        project_id=project_id,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "error"
    assert run.final_answer == message


# (d) The guard treats differently-cased searches and None keys as the same call
def test_repeated_call_guard_normalizes_case_and_none() -> None:
    guard = RepeatedCallGuard()

    # First call: lowercase 'supabase'
    first = guard.is_repeated("search_code", {"query": "supabase", "limit": 10})
    assert first is False

    # Second call: uppercase 'Supabase' with path_glob=None and whitespace
    second = guard.is_repeated(
        "search_code",
        {"query": "  Supabase  ", "path_glob": None},
    )
    assert second is True


# (e) search_code finds "Supabase" with the query "supabase"
@pytest.mark.asyncio
async def test_search_code_case_insensitive(mock_db: AsyncIOMotorDatabase) -> None:
    pid = "proj_case_test"
    await mock_db.files.insert_one({
        "project_id": pid,
        "path": "config.py",
        "size": 120,
        "language": "Python",
        "content": "Supabase client = create_client(SUPABASE_URL, SUPABASE_KEY)\n",
    })

    tool = SearchCodeTool(db=mock_db)
    out = await tool.run(pid, tool.args_schema(query="supabase"))

    assert "Found" in out.text
    assert "config.py:1: Supabase client = create_client" in out.text
    assert "file:config.py#L1-1" in out.evidence_ids


@pytest.mark.asyncio
async def test_agent_notifies_steps_in_order_and_ignores_callback_errors(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0
    notified_steps = []

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise LLMOutputParseError("Malformed tool output")
        if call_count == 2:
            return LLMReply(
                content="Inspecting the repository",
                tool_calls=[ToolCall(id="call_1", name="list_tree", arguments={"path": "", "depth": 1})],
                usage={"input_tokens": 5, "output_tokens": 3},
            )
        return LLMReply(content="[Fact] The repository was inspected.", tool_calls=[])

    def on_step(step) -> None:
        notified_steps.append(step)
        raise RuntimeError("Progress display failed")

    run = await run_agent(
        goal="Inspect this repository",
        project_id=project_id,
        max_steps=5,
        db=mock_db,
        chat_fn=fake_chat,
        on_step=on_step,
    )

    assert run.stop_reason == "answered"
    assert [step.tool for step in notified_steps] == ["error", "list_tree"]
    assert notified_steps == run.steps


@pytest.mark.asyncio
async def test_agent_emits_thinking_event_before_first_llm_call(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    order = []

    async def fake_chat(messages, tools=None, on_event=None):
        order.append("llm")
        return LLMReply(content="Done.", tool_calls=[])

    def on_event(text: str) -> None:
        order.append(text)

    await run_agent(
        goal="Answer directly",
        project_id=project_id,
        max_steps=10,
        db=mock_db,
        chat_fn=fake_chat,
        on_event=on_event,
    )

    assert order[:2] == ["thinking... (step 1/10)", "llm"]


@pytest.mark.asyncio
async def test_agent_supports_chat_callback_without_on_event_parameter(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)

    async def fake_chat(messages, tools=None):
        return LLMReply(content="Done.", tool_calls=[])

    run = await run_agent(
        goal="Answer directly",
        project_id=project_id,
        max_steps=1,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"


@pytest.mark.asyncio
async def test_why_question_nudges_history_check_then_accepts_answer(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0
    nudge_seen = False

    async def fake_chat(messages, tools=None):
        nonlocal call_count, nudge_seen
        call_count += 1
        nudge_seen = nudge_seen or any(
            message.get("role") == "user"
            and "Before answering a why-question" in str(message.get("content"))
            for message in messages
        )
        if call_count == 1:
            return LLMReply(content="I think it was chosen for simplicity.", tool_calls=[])
        if call_count == 2:
            assert nudge_seen
            return LLMReply(
                content="Checking commit history.",
                tool_calls=[
                    ToolCall(
                        id="history_call",
                        name="search_commits",
                        arguments={"query": "database"},
                    )
                ],
            )
        return LLMReply(
            content="[Fact] The code uses this database. [Inferred] It was chosen for simplicity.",
            tool_calls=[],
        )

    run = await run_agent(
        goal="Why did this project choose its database?",
        project_id=project_id,
        max_steps=5,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert nudge_seen
    assert run.stop_reason == "answered"
    assert [step.tool for step in run.steps] == ["policy_nudge", "search_commits"]
    assert "[Fact]" in (run.final_answer or "")


@pytest.mark.asyncio
async def test_non_why_question_does_not_nudge_history_check(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    seen_nudge = False

    async def fake_chat(messages, tools=None):
        nonlocal seen_nudge
        seen_nudge = seen_nudge or any(
            "Before answering a why-question" in str(message.get("content"))
            for message in messages
        )
        return LLMReply(content="[Fact] This is a Python project.", tool_calls=[])

    run = await run_agent(
        goal="What language does this project use?",
        project_id=project_id,
        max_steps=3,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert not seen_nudge
    assert run.steps == []


@pytest.mark.asyncio
async def test_why_question_with_prior_history_tool_does_not_nudge(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0
    seen_nudge = False

    async def fake_chat(messages, tools=None):
        nonlocal call_count, seen_nudge
        call_count += 1
        seen_nudge = seen_nudge or any(
            "Before answering a why-question" in str(message.get("content"))
            for message in messages
        )
        if call_count == 1:
            return LLMReply(
                content="Checking history.",
                tool_calls=[
                    ToolCall(
                        id="history_call",
                        name="search_commits",
                        arguments={"query": "database"},
                    )
                ],
            )
        return LLMReply(content="[Fact] The commit history explains the change.", tool_calls=[])

    run = await run_agent(
        goal="Why was the database choice made?",
        project_id=project_id,
        max_steps=3,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert [step.tool for step in run.steps] == ["search_commits"]
    assert not seen_nudge


@pytest.mark.asyncio
async def test_why_question_history_nudge_is_sent_at_most_once(
    mock_db: AsyncIOMotorDatabase,
) -> None:
    project_id = await seed_project_data(mock_db)
    call_count = 0
    nudge_counts: list[int] = []

    async def fake_chat(messages, tools=None):
        nonlocal call_count
        call_count += 1
        nudge_counts.append(sum(
            1
            for message in messages
            if message.get("role") == "user"
            and "Before answering a why-question" in str(message.get("content"))
        ))
        return LLMReply(content=f"[Uncertain] Draft answer {call_count}.", tool_calls=[])

    run = await run_agent(
        goal="Why is this project structured this way?",
        project_id=project_id,
        max_steps=4,
        db=mock_db,
        chat_fn=fake_chat,
    )

    assert run.stop_reason == "answered"
    assert nudge_counts == [0, 1]
    assert [step.tool for step in run.steps] == ["policy_nudge"]


def test_old_tool_results_are_trimmed_but_recent_two_are_preserved() -> None:
    messages = [
        {"role": "tool", "content": "a" * 400},
        {"role": "tool", "content": "recent one"},
        {"role": "tool", "content": "recent two"},
    ]

    trimmed = _trim_old_tool_results(messages)

    assert trimmed[0]["content"] == (
        "a" * 300
        + "\n[older result trimmed to save tokens; repeat the same call to see it again]"
    )
    assert trimmed[1]["content"] == "recent one"
    assert trimmed[2]["content"] == "recent two"
    assert messages[0]["content"] == "a" * 400
