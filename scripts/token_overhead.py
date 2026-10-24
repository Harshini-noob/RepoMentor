"""Estimate system-prompt and tool-schema token overhead using characters / 4."""

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.agent.loop import load_system_prompt
from app.agent.tools import build_registry


def main() -> None:
    system_prompt = load_system_prompt()
    schemas_json = json.dumps(
        build_registry().schemas(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    print(f"System prompt: {len(system_prompt) / 4:.2f} estimated tokens")
    print(f"Tool schemas: {len(schemas_json) / 4:.2f} estimated tokens")


if __name__ == "__main__":
    main()
