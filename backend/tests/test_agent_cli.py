import pytest

from app.agent.state import AgentRun
from app.cli import _async_main


@pytest.mark.asyncio
async def test_daily_token_limit_is_printed_as_one_plain_line(monkeypatch, capsys) -> None:
    message = (
        "Daily token limit reached for model test-model. "
        "Try again later or change LLM_MODEL."
    )
    monkeypatch.setattr(
        "app.cli.run_agent",
        lambda *args, **kwargs: _completed_run(message),
    )

    exit_code = await _async_main("project-id", "inspect")

    assert exit_code == 2
    output_lines = capsys.readouterr().out.splitlines()
    assert output_lines[-1] == message
    assert "=== Final Answer ===" not in output_lines


async def _completed_run(message: str) -> AgentRun:
    return AgentRun(
        goal="inspect",
        project_id="project-id",
        final_answer=message,
        stop_reason="error",
    )
