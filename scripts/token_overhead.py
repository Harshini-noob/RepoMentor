"""Estimate system-prompt and tool-schema token overhead using characters / 4."""

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.agent.loop import load_system_prompt
from app.agent.tools import build_registry
from app.mentor.chat import build_mentor_prompt


def main() -> None:
    system_prompt = load_system_prompt()
    schemas_json = json.dumps(
        build_registry().schemas(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    prompt_tokens = len(system_prompt) / 4
    tool_tokens = len(schemas_json) / 4
    mentor_prompt = build_mentor_prompt()
    mentor_tokens = len(mentor_prompt) / 4
    total_mentor_overhead = mentor_tokens + tool_tokens

    print(f"System prompt: {prompt_tokens:.2f} estimated tokens")
    print(f"Tool schemas: {tool_tokens:.2f} estimated tokens")
    print(f"Mentor prompt (mentor.md): {mentor_tokens:.2f} estimated tokens")
    print(f"Total mentor-mode overhead: {total_mentor_overhead:.2f} estimated tokens")


if __name__ == "__main__":
    main()
