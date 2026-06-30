import asyncio
import random

from fastmcp import Client

def propose_next_experiment():
    return [random.randint(0, 255) for _ in range(3)]

async def main():
    async with Client("http://127.0.0.1:8001/mcp") as client:
        # Example workflow
        for i in range(12):
          r, g, b = propose_next_experiment()
          result = await client.call_tool("run_experiment", {"well": i, "red": r, "green": g, "blue": b})
          print(result.data)
          result = await client.call_tool("get_color_diff", {"hex": "#00FF00"})
          print(result.data)
asyncio.run(main())