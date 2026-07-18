from copy import deepcopy
from math import isfinite
from typing import Any

import numpy as np
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from PIL import Image as PILImage
from pydantic import BaseModel


PIPETTE_CAPACITY_UL = 1000.0
MIX_WELL_CAPACITY_UL = 3000.0
INITIAL_COLOR_VOLUME_UL = 8000.0
COLOR_WELLS = {
    0: "blue",
    1: "yellow",
    2: "red",
    3: "blue",
    4: "yellow",
    5: "red",
}


class RGB(BaseModel):
    red: int
    green: int
    blue: int


def rgb_to_lab(r: int, g: int, b: int) -> np.ndarray:
    """Convert RGB to CIE LAB representation using Pillow."""
    # Pillow's LAB stores L in [0, 255] and a, b in [0, 255] (128 = 0),
    # so we rescale to standard ranges: L [0, 100], a/b [-128, 127].
    pixel = PILImage.new("RGB", (1, 1), (r, g, b)).convert("LAB").getpixel((0, 0))
    return np.array(
        [
            pixel[0] / 255.0 * 100.0,
            pixel[1] - 128.0,
            pixel[2] - 128.0,
        ]
    )


def cie76(lab1: np.ndarray, lab2: np.ndarray) -> float:
    # Compute the CIE76 (ΔE*ab) colour difference — Euclidean distance in L*a*b*
    return float(np.linalg.norm(lab1 - lab2))


def _volume(contents: dict[str, float]) -> float:
    return sum(contents.values())


def _add_contents(target: dict[str, float], addition: dict[str, float]) -> None:
    for component, amount in addition.items():
        target[component] = target.get(component, 0.0) + amount


def _take_contents(contents: dict[str, float], volume_ul: float) -> dict[str, float]:
    """Remove and return a proportional aliquot from a homogeneous liquid."""
    total = _volume(contents)
    fraction = volume_ul / total
    aliquot: dict[str, float] = {}

    for component, amount in list(contents.items()):
        removed = amount * fraction
        aliquot[component] = removed
        remaining = amount - removed
        if remaining < 1e-9:
            del contents[component]
        else:
            contents[component] = remaining

    return aliquot


class DigitalTwin:
    """A small stateful twin exposing atomic laboratory actions."""

    def __init__(self) -> None:
        self._reset_state()
        self.initialize()

    def reset_simulation(self) -> dict[str, Any]:
        """Reset all simulated liquids, position, pipette state, and action history."""
        self._reset_state()
        return self._success("reset_simulation", {})

    def _reset_state(self) -> None:
        self.location_kind = "home"
        self.location_index: int | None = None
        self.pipette_contents: dict[str, float] = {}
        self.color_wells = {
            index: {color: INITIAL_COLOR_VOLUME_UL}
            for index, color in COLOR_WELLS.items()
        }
        self.mix_wells = {index: {} for index in range(12)}
        self.action_trace: list[dict[str, Any]] = []

    def initialize(self) -> dict[str, Any]:
        """Move to home without resetting liquids or action history."""
        self.location_kind = "home"
        self.location_index = None
        return self._success("initialize", {})

    def return_home(self) -> dict[str, Any]:
        """Move the robot to its safe home position."""
        self.location_kind = "home"
        self.location_index = None
        return self._success("return_home", {})

    def move_color_well(self, well: int) -> dict[str, Any]:
        """Move safely above a color source well numbered 0 through 5."""
        if well not in COLOR_WELLS:
            raise ToolError("color well must be between 0 and 5")

        self.location_kind = "color_well"
        self.location_index = well
        return self._success("move_color_well", {"well": well})

    def move_mix_well(self, well: int) -> dict[str, Any]:
        """Move safely above a destination well numbered 0 through 11."""
        self._validate_mix_well(well)
        self.location_kind = "mix_well"
        self.location_index = well
        return self._success("move_mix_well", {"well": well})

    def move_wash_station(self) -> dict[str, Any]:
        """Move safely to the wash station."""
        self.location_kind = "wash_station"
        self.location_index = None
        return self._success("move_wash_station", {})

    def aspirate(self, volume_ul: float) -> dict[str, Any]:
        """Aspirate liquid at the current location, up to the 1000 uL capacity."""
        self._validate_volume(volume_ul)
        pipette_volume = _volume(self.pipette_contents)
        if pipette_volume + volume_ul > PIPETTE_CAPACITY_UL + 1e-9:
            raise ToolError("PipetteOverflow: requested volume exceeds 1000 uL capacity")

        if self.location_kind == "color_well":
            source = self.color_wells[self._require_location_index()]
            if _volume(source) + 1e-9 < volume_ul:
                raise ToolError("SourceEmpty: color well does not contain enough liquid")
            aliquot = _take_contents(source, volume_ul)
        elif self.location_kind == "mix_well":
            source = self.mix_wells[self._require_location_index()]
            if _volume(source) + 1e-9 < volume_ul:
                raise ToolError("SourceEmpty: mix well does not contain enough liquid")
            aliquot = _take_contents(source, volume_ul)
        elif self.location_kind == "wash_station":
            # The wash station currently shares the physical home position.
            aliquot = {"water": volume_ul}
        else:
            raise ToolError("InvalidLocation: aspirate requires a liquid-containing location")

        _add_contents(self.pipette_contents, aliquot)
        return self._success("aspirate", {"volume_ul": volume_ul})

    def dispense(self, volume_ul: float) -> dict[str, Any]:
        """Dispense all pipette liquid and blow out at a mix well or wash station."""
        self._validate_volume(volume_ul)
        pipette_volume = _volume(self.pipette_contents)
        if pipette_volume + 1e-9 < volume_ul:
            raise ToolError("PipetteEmpty: pipette does not contain the requested volume")
        if abs(pipette_volume - volume_ul) > 1e-9:
            raise ToolError(
                "DispenseMustEmptyPipette: volume_ul must equal the current pipette volume"
            )

        self._validate_dispense_destination(volume_ul)
        aliquot = _take_contents(self.pipette_contents, volume_ul)
        if self.location_kind == "mix_well":
            _add_contents(self.mix_wells[self._require_location_index()], aliquot)
        # Liquid dispensed at the wash station goes to waste. This includes blow out.

        return self._success(
            "dispense",
            {"volume_ul": volume_ul},
        )

    def get_state(self) -> dict[str, Any]:
        """Return the observable state of the robot, pipette, and labware."""
        return {
            "location": self._location(),
            "pipette": self._liquid_state(
                self.pipette_contents,
                capacity_ul=PIPETTE_CAPACITY_UL,
            ),
            "color_wells": {
                index: self._liquid_state(contents)
                for index, contents in self.color_wells.items()
            },
            "mix_wells": {
                index: self._liquid_state(
                    contents,
                    capacity_ul=MIX_WELL_CAPACITY_UL,
                )
                for index, contents in self.mix_wells.items()
            },
        }

    def get_action_trace(self) -> list[dict[str, Any]]:
        """Return successful atomic actions in execution order."""
        return deepcopy(self.action_trace)

    def get_labware_config(self) -> dict[str, Any]:
        """Describe source identities and capacities for protocol planning."""
        return {
            "units": "uL",
            "pipette_capacity_ul": PIPETTE_CAPACITY_UL,
            "mix_well_capacity_ul": MIX_WELL_CAPACITY_UL,
            "color_wells": COLOR_WELLS,
            "mix_wells": list(range(12)),
            "wash_station": True,
        }

    def read_well_color(self, well: int) -> RGB:
        """Return the predicted RGB color of a mix well."""
        self._validate_mix_well(well)
        red, green, blue = self._predict_rgb(self.mix_wells[well])
        return RGB(red=red, green=green, blue=blue)

    def get_color_diff(self, well: int, hex: str) -> float:
        """Return the simulated CIE76 difference from a target hex color."""
        self._validate_mix_well(well)
        if not self.mix_wells[well]:
            raise ToolError(f"mix well {well} is empty")

        target_rgb = self._parse_hex(hex)
        measured_rgb = self._predict_rgb(self.mix_wells[well])
        return cie76(rgb_to_lab(*target_rgb), rgb_to_lab(*measured_rgb))

    def _validate_mix_well(self, well: int) -> None:
        if well not in self.mix_wells:
            raise ToolError("mix well must be between 0 and 11")

    @staticmethod
    def _validate_volume(volume_ul: float) -> None:
        if not isfinite(volume_ul) or volume_ul <= 0:
            raise ToolError("volume_ul must be a finite number greater than 0")
        if volume_ul > PIPETTE_CAPACITY_UL:
            raise ToolError("volume_ul must not exceed the 1000 uL pipette capacity")

    def _validate_dispense_destination(self, additional_volume_ul: float) -> None:
        if self.location_kind == "wash_station":
            return
        if self.location_kind != "mix_well":
            raise ToolError("InvalidLocation: dispense requires a mix well or wash station")

        well = self._require_location_index()
        if _volume(self.mix_wells[well]) + additional_volume_ul > MIX_WELL_CAPACITY_UL + 1e-9:
            raise ToolError("WellOverflow: requested volume exceeds 3000 uL capacity")

    def _require_location_index(self) -> int:
        if self.location_index is None:
            raise RuntimeError("current location does not have an index")
        return self.location_index

    def _location(self) -> dict[str, Any]:
        location: dict[str, Any] = {"kind": self.location_kind}
        if self.location_index is not None:
            location["index"] = self.location_index
        return location

    @staticmethod
    def _liquid_state(
        contents: dict[str, float],
        capacity_ul: float | None = None,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "volume_ul": round(_volume(contents), 6),
            "contents": {
                component: round(amount, 6)
                for component, amount in sorted(contents.items())
            },
        }
        if capacity_ul is not None:
            state["capacity_ul"] = capacity_ul
        return state

    def _success(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._record(action, arguments)
        return {
            "action": action,
            "location": self._location(),
            "pipette_volume_ul": round(_volume(self.pipette_contents), 6),
        }

    def _record(self, action: str, arguments: dict[str, Any]) -> None:
        self.action_trace.append(
            {
                "sequence": len(self.action_trace) + 1,
                "action": action,
                "arguments": deepcopy(arguments),
                "location": self._location(),
                "pipette_volume_ul": round(_volume(self.pipette_contents), 6),
            }
        )

    @staticmethod
    def _parse_hex(value: str) -> tuple[int, int, int]:
        clean = value.lstrip("#")
        if len(clean) != 6:
            raise ToolError(f"Invalid hex color: '{value}'. Expected 6 hex digits.")
        try:
            return (
                int(clean[0:2], 16),
                int(clean[2:4], 16),
                int(clean[4:6], 16),
            )
        except ValueError as exc:
            raise ToolError(f"Invalid hex color: '{value}'. Expected 6 hex digits.") from exc

    @staticmethod
    def _predict_rgb(contents: dict[str, float]) -> tuple[int, int, int]:
        total = _volume(contents)
        if total == 0:
            return (255, 255, 255)

        transmittance = {
            "red": (0.90, 0.10, 0.15),
            "yellow": (0.95, 0.85, 0.05),
            "blue": (0.10, 0.35, 0.85),
            "water": (1.0, 1.0, 1.0),
        }
        rgb: list[int] = []
        for channel in range(3):
            value = 1.0
            for component, amount in contents.items():
                value *= transmittance[component][channel] ** (amount / total)
            rgb.append(round(value * 255))
        return tuple(rgb)  # type: ignore[return-value]


twin = DigitalTwin()

mcp = FastMCP("Color matching digital twin")
mcp.tool(twin.reset_simulation)
mcp.tool(twin.initialize)
mcp.tool(twin.return_home)
mcp.tool(twin.move_color_well)
mcp.tool(twin.move_mix_well)
mcp.tool(twin.move_wash_station)
mcp.tool(twin.aspirate)
mcp.tool(twin.dispense)
mcp.tool(twin.get_state)
mcp.tool(twin.get_action_trace)
mcp.tool(twin.get_labware_config)
mcp.tool(twin.read_well_color)
mcp.tool(twin.get_color_diff)


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
