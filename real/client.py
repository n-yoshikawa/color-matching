import asyncio

from fastmcp import Client

async def main():
    async with Client("http://127.0.0.1:8001/mcp") as client:
        await client.call_tool("initialize")
        for i in [0, 5]:
            for j in [2, 8]:
                result = await client.call_tool("move_color_well", {"well": i})
                print(result.data)
                result = await client.call_tool("aspirate", {"volume_ul": 200})
                print(result.data)
                result = await client.call_tool("move_mix_well", {"well": j})
                print(result.data)
                result = await client.call_tool("dispense", {"volume_ul": 200})
                print(result.data)
                result = await client.call_tool("move_wash_station")
                result = await client.call_tool("aspirate", {"volume_ul": 1000})
                result = await client.call_tool("dispense", {"volume_ul": 1000})
            result = await client.call_tool("get_color_diff", {"well": j, "hex": "#00ff00"})
asyncio.run(main())