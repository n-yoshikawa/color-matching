import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from server import MySDL, mcp


def test_filling_an_occupied_well_is_a_retryable_tool_error():
    sdl = MySDL()
    sdl.run_experiment(well=1, red=255, yellow=0, blue=0)

    with pytest.raises(ToolError, match="well 1 is already filled"):
        sdl.run_experiment(well=1, red=0, yellow=255, blue=0)


@pytest.mark.parametrize(
    ("red", "yellow", "blue"),
    [
        (-1, 0, 0),
        (0, 256, 0),
        (0, 0, float("inf")),
    ],
)
def test_run_experiment_rejects_color_volumes_outside_0_to_255(red, yellow, blue):
    sdl = MySDL()

    with pytest.raises(ToolError, match="between 0 and 255"):
        sdl.run_experiment(well=1, red=red, yellow=yellow, blue=blue)


def test_run_experiment_returns_the_filled_well_and_recipe_through_mcp():
    async def call_run_experiment():
        async with Client(mcp) as client:
            await client.call_tool("initialize")
            return await client.call_tool(
                "run_experiment",
                {
                    "well": 7,
                    "red": 12,
                    "yellow": 255,
                    "blue": 3,
                },
            )

    result = asyncio.run(call_run_experiment())

    assert result.structured_content == {
        "well": 7,
        "recipe": {
            "red": 12.0,
            "yellow": 255.0,
            "blue": 3.0,
        },
    }
