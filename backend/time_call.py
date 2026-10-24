import asyncio
import time

from app.agent.llm import chat
from app.agent.tools import build_registry


async def main():
    schemas = build_registry().schemas()
    t = time.time()
    reply = await chat(
        [{"role": "user", "content": "Why did I use Supabase? Use a tool if you need one."}],
        schemas,
    )
    print(f"{time.time() - t:.1f}s", reply.tool_calls, reply.usage)


asyncio.run(main())