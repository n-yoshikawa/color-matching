import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mock.server import DigitalTwin, mcp


def test_atomic_actions_transfer_liquid_from_source_to_mix_well():
    twin = DigitalTwin()

    twin.move_color_well(2)
    twin.aspirate(200)
    twin.move_mix_well(3)
    twin.dispense(200)

    state = twin.get_state()
    assert state["color_wells"][2] == {
        "volume_ul": 5800.0,
        "contents": {"red": 5800.0},
    }
    assert state["pipette"]["volume_ul"] == 0
    assert state["mix_wells"][3]["volume_ul"] == 200
    assert state["mix_wells"][3]["contents"] == {"red": 200.0}


def test_multiple_dispenses_can_build_a_recipe_in_the_same_well():
    twin = DigitalTwin()

    for color_well, volume_ul in [(2, 200), (1, 300), (0, 500)]:
        twin.move_color_well(color_well)
        twin.aspirate(volume_ul)
        twin.move_mix_well(4)
        twin.dispense(volume_ul)

    well = twin.get_state()["mix_wells"][4]
    assert well["volume_ul"] == 1000
    assert well["contents"] == {
        "blue": 500.0,
        "red": 200.0,
        "yellow": 300.0,
    }
def test_dispense_requires_the_entire_pipette_volume():
    twin = DigitalTwin()
    twin.move_color_well(2)
    twin.aspirate(200)
    twin.move_mix_well(0)

    with pytest.raises(ToolError, match="DispenseMustEmptyPipette"):
        twin.dispense(150)

    state = twin.get_state()
    assert state["pipette"]["contents"] == {"red": 200.0}
    assert state["mix_wells"][0]["volume_ul"] == 0


def test_wash_station_dispense_includes_blow_out():
    twin = DigitalTwin()

    twin.move_wash_station()
    twin.aspirate(1000)
    result = twin.dispense(1000)

    assert result["pipette_volume_ul"] == 0
    assert twin.get_state()["pipette"]["volume_ul"] == 0
    assert twin.get_action_trace()[-1]["arguments"] == {"volume_ul": 1000}


def test_initialize_only_returns_home_without_resetting_liquids_or_trace():
    twin = DigitalTwin()
    twin.move_color_well(2)
    twin.aspirate(200)
    trace_length_before = len(twin.get_action_trace())

    twin.initialize()

    state = twin.get_state()
    assert state["location"] == {"kind": "home"}
    assert state["pipette"]["contents"] == {"red": 200.0}
    assert state["color_wells"][2]["contents"] == {"red": 5800.0}
    assert len(twin.get_action_trace()) == trace_length_before + 1
    assert twin.get_action_trace()[-1]["action"] == "initialize"


def test_reset_simulation_restores_initial_state_and_restarts_trace():
    twin = DigitalTwin()
    twin.move_color_well(2)
    twin.aspirate(200)

    twin.reset_simulation()

    state = twin.get_state()
    assert state["location"] == {"kind": "home"}
    assert state["pipette"]["volume_ul"] == 0
    assert state["color_wells"][2]["contents"] == {"red": 6000.0}
    assert all(well["volume_ul"] == 0 for well in state["mix_wells"].values())
    assert [event["action"] for event in twin.get_action_trace()] == [
        "reset_simulation"
    ]


def test_home_is_not_treated_as_a_liquid_location():
    twin = DigitalTwin()

    with pytest.raises(ToolError, match="liquid-containing location"):
        twin.aspirate(100)


def test_invalid_actions_raise_tool_errors_without_changing_liquid_state():
    twin = DigitalTwin()
    twin.move_mix_well(0)

    with pytest.raises(ToolError, match="SourceEmpty"):
        twin.aspirate(100)

    twin.move_color_well(2)
    twin.aspirate(800)
    source_before = twin.get_state()["color_wells"][2]

    with pytest.raises(ToolError, match="PipetteOverflow"):
        twin.aspirate(300)

    assert twin.get_state()["color_wells"][2] == source_before


def test_action_trace_records_successful_actions_in_order():
    twin = DigitalTwin()
    twin.move_color_well(2)
    twin.aspirate(100)
    twin.move_mix_well(1)
    twin.dispense(100)

    assert [event["action"] for event in twin.get_action_trace()] == [
        "initialize",
        "move_color_well",
        "aspirate",
        "move_mix_well",
        "dispense",
    ]


def test_mcp_exposes_atomic_actions_but_not_composite_protocols():
    async def list_tool_names():
        async with Client(mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    tool_names = asyncio.run(list_tool_names())

    assert tool_names == {
        "reset_simulation",
        "initialize",
        "return_home",
        "move_color_well",
        "move_mix_well",
        "move_wash_station",
        "aspirate",
        "dispense",
        "get_state",
        "get_labware_config",
        "get_color_diff",
    }


def test_atomic_actions_execute_through_mcp():
    async def transfer_liquid():
        async with Client(mcp) as client:
            await client.call_tool("initialize")
            await client.call_tool("move_color_well", {"well": 2})
            await client.call_tool("aspirate", {"volume_ul": 125})
            await client.call_tool("move_mix_well", {"well": 6})
            await client.call_tool("dispense", {"volume_ul": 125})
            return await client.call_tool("get_state")

    result = asyncio.run(transfer_liquid())

    assert result.data["pipette"]["volume_ul"] == 0
    assert result.data["mix_wells"]["6"]["contents"] == {"red": 125.0}
