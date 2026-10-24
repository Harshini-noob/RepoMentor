import json
import os

from dotenv import load_dotenv
from groq import Groq

from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")
client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "openai/gpt-oss-120b"  # if Groq says it's deprecated, pick a current tool-capable model


# --- 1. The tools: plain Python functions ---------------------------------
def add(a: float, b: float) -> float:
    return a + b


def get_weather(city: str) -> str:
    fake_data = {"chennai": "34C, humid", "kottayam": "29C, rainy"}
    return fake_data.get(city.lower(), "unknown city")


TOOL_FUNCTIONS = {"add": add, "get_weather": get_weather}

# --- 2. The schemas: what the model is told about each tool ---------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Add two numbers.",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
]


# --- 3. The loop -----------------------------------------------------------
def run(question: str, max_steps: int = 6) -> str:
    messages = [
        {"role": "system", "content": "Use tools when you need them. Answer briefly."},
        {"role": "user", "content": question},
    ]
    for step in range(1, max_steps + 1):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto"
        )
        msg = response.choices[0].message

        if not msg.tool_calls:  # no tool requested -> this is the final answer
            print(f"[step {step}] FINAL: {msg.content}")
            return msg.content

        # keep the assistant's tool request in the history
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        # run each requested tool and feed the observation back
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
                result = TOOL_FUNCTIONS[tc.function.name](**args)
            except Exception as exc:  # the agent should see errors, not crash
                result = f"ERROR: {exc}"
            print(f"[step {step}] CALL {tc.function.name}({tc.function.arguments}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})

    print("Step budget reached without a final answer.")
    return ""


if __name__ == "__main__":
    run("What is 17+25, and what is the weather in Chennai?")
    print("-" * 40)
    run("Say hello.")