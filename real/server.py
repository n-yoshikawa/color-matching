import time
import sys
import csv
import json
import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from math import isfinite
from PIL import Image as PILImage
from skimage import color as skimage_color
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.logging import LoggingMiddleware
from pydantic import BaseModel

from magician import MagicianController
from picus import PicusWired

from typing import Any, Dict
from datetime import datetime

import nimo

ROWS, COLS = 3, 4
SAMPLE_FRAC = 0.2
PIPETTE_CAPACITY_UL = 1000.0
MIX_WELL_CAPACITY_UL = 3000.0
# COLOR_WELLS_PATH = Path(__file__).resolve().parent.parent / "config" / "color_wells.json"
COLOR_WELLS_PATH = Path(__file__).resolve().parent / "config" / "color_wells.json"

with COLOR_WELLS_PATH.open(encoding="utf-8") as file:
    COLOR_WELLS = {int(well): color for well, color in json.load(file).items()}


def well_centers(cx, cy, p):
    co = (np.arange(COLS) - (COLS - 1) / 2) * p
    ro = (np.arange(ROWS) - (ROWS - 1) / 2) * p
    return np.array([(cx + c, cy + r) for r in ro for c in co])

class ColorRecipe(BaseModel):
    """Volumes used to produce one experimental color."""

    red: float
    yellow: float
    blue: float


class ExperimentResult(BaseModel):
    """The well and recipe committed by a successful experiment."""

    well: int
    recipe: ColorRecipe


class MySDL:
    def __init__(self):
        self.consumption = {"blue": 0, "yellow": 0, "red": 0}
        self.filled_wells = {}
        self.z_aspirate = 51
        self.z_dispense = 70
        self.z_home = 70
        self.home_pose = (225, 0, self.z_home)
        self.dobot = MagicianController()
        self.picus = PicusWired("46782479")
        self.cap = cv2.VideoCapture(1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        # camera setting values obtained from calibrate.py
        self.cx = 645
        self.cy = 356
        self.p = 115

        # Minimal commanded state used for validation and structured MCP results.
        self.location_kind = "home"
        self.location_index: int | None = None
        self.pipette_volume_ul = 0.0

    def initialize(self) -> dict[str, Any]:
        """Move to home without resetting physical experiment state."""
        try:
            self.dobot.clear_alarms()
            # Small movement to check if initialize is succeeded
            self.dobot.move_arm(self.home_pose[0], self.home_pose[1], self.home_pose[2]+20)
            self.dobot.move_arm(self.home_pose[0], self.home_pose[1], self.home_pose[2])
            if self.pipette_volume_ul > 0:
                self.picus.dispense(self.pipette_volume_ul)
        except Exception as exc:
            raise ToolError(f"failed to initialize: {exc}") from exc
        self.location_kind = "home"
        self.location_index = None
        return self._success("initialize")

    def return_home(self) -> dict[str, Any]:
        """
        Move the robot arm to its predefined home position.
        """
        if self.dobot.get_alarms() != 0:
            raise ToolError(f"robot is in alarm state. Please initialize.")            

        try:
            self.dobot.move_arm(self.home_pose[0], self.home_pose[1], self.home_pose[2])
        except Exception as exc:
            raise ToolError(f"failed to return home: {exc}") from exc
        self.location_kind = "home"
        self.location_index = None
        return self._success("return_home")

    def move_color_well(self, well: int) -> dict[str, Any]:
        """
        Move the robot on the color well specified by an integer (0-indexed).
        """
        if self.dobot.get_alarms() != 0:
            raise ToolError(f"robot is in alarm state. Please initialize.")
             
        if well not in COLOR_WELLS:
            raise ToolError("color well must be between 0 and 5")

        x = 230 - 39 * (well // 3)
        y = -90 - 39 * (well % 3)
        try:
            # self.dobot.move_joints(j1=0)
            self.dobot.move_arm(z=self.z_home)
            self.dobot.move_arm(x=x, y=y)
        except Exception as exc:
            raise ToolError(f"failed to move to color well {well}: {exc}") from exc

        self.location_kind = "color_well"
        self.location_index = well
        return self._success("move_color_well")

    def move_mix_well(self, well: int) -> dict[str, Any]:
        """
        Move the robot on the mix well specified by an integer (0-indexed).
        """
        if self.dobot.get_alarms() != 0:
            raise ToolError(f"robot is in alarm state. Please initialize.")
        
        self._validate_mix_well(well)

        x = 239 - 25 * (well // 4)
        y = 194 - 25 * (well % 4)
        try:
            # self.dobot.move_joints(j1=0)
            self.dobot.move_arm(z=self.z_home)
            joints = self.dobot.get_current_joints()
            self.dobot.move_arm(x=x, y=y)
        except Exception as exc:
            raise ToolError(f"failed to move to mix well {well}: {exc}") from exc

        self.location_kind = "mix_well"
        self.location_index = well
        return self._success("move_mix_well")

    def move_wash_station(self) -> dict[str, Any]:
        """Move to the wash station, which is physically the home position."""
        
        if self.dobot.get_alarms() != 0:
            raise ToolError(f"robot is in alarm state. Please initialize.")
        
        try:
            self.dobot.move_arm(self.home_pose[0], self.home_pose[1], self.home_pose[2])
        except Exception as exc:
            raise ToolError(f"failed to move to wash station: {exc}") from exc

        self.location_kind = "wash_station"
        self.location_index = None
        return self._success("move_wash_station")

    def aspirate(self, volume_ul: float) -> dict[str, Any]:
        """
        Aspirate the specified liquid volume.

        The arm moves to aspirate height, performs aspiration,
        and returns to the home Z position.

        Args:
          volume_ul: Liquid volume to aspirate.
        """
        self._validate_volume(volume_ul)
        if self.location_kind not in {"color_well", "mix_well", "wash_station"}:
            raise ToolError("aspirate requires a liquid-containing location")
        if self.pipette_volume_ul + volume_ul > PIPETTE_CAPACITY_UL:
            raise ToolError("requested volume exceeds the 1000 uL pipette capacity")

        try:
            self.dobot.move_arm(z=self.z_aspirate)
            self.picus.aspirate(volume_ul)
            self.dobot.move_arm(z=self.z_home)
        except Exception as exc:
            raise ToolError(f"failed to aspirate {volume_ul} uL: {exc}") from exc

        self.pipette_volume_ul += volume_ul
        return self._success("aspirate")

    def dispense(self, volume_ul: float) -> dict[str, Any]:
        """
        Dispense the specified liquid volume.

        The arm moves to dispense height, performs dispensing,
        and returns to the home Z position.

        Args:
          volume_ul: Liquid volume to dispense.

        Dispensing includes blow out and must empty the pipette.
        """
        self._validate_volume(volume_ul)
        if self.location_kind not in {"mix_well", "wash_station"}:
            raise ToolError("dispense requires a mix well or wash station")
        if volume_ul != self.pipette_volume_ul:
            raise ToolError("volume_ul must equal the current pipette volume")

        try:
            self.dobot.move_arm(z=self.z_dispense)
            self.picus.dispense(volume_ul)
            self.dobot.move_arm(z=self.z_home)
            self.picus.blow_out()
        except Exception as exc:
            raise ToolError(f"failed to dispense {volume_ul} uL: {exc}") from exc

        self.pipette_volume_ul = 0.0
        return self._success("dispense")

    def mix_well(self, volume_ul: float, cycles: int = 1) -> dict[str, Any]:
        """Mix liquid in the current mix well by repeated aspiration and dispensing."""
        self._validate_volume(volume_ul)
        if not 1 <= cycles <= 10:
            raise ToolError("cycles must be between 1 and 10")
        if self.location_kind != "mix_well":
            raise ToolError("mix_well requires the robot to be at a mix well")
        if self.pipette_volume_ul != 0:
            raise ToolError("mix_well requires an empty pipette")
        if self.dobot.get_alarms() != 0:
            raise ToolError("robot is in alarm state. Please initialize.")

        try:
            self.dobot.move_arm(z=self.z_aspirate + 1.0)
            for _ in range(cycles):
                self.picus.aspirate(volume_ul)
                self.picus.dispense(volume_ul)
            self.dobot.move_arm(z=self.z_home)
            self.picus.blow_out()
        except Exception as exc:
            raise ToolError(f"failed to mix current well: {exc}") from exc

        return self._success("mix_well")

    def wash_tip(
        self,
        volume_ul: float = 1000.0,
        cycles: int = 1,
    ) -> dict[str, Any]:
        """Move to the wash station and wash the empty pipette tip."""
        self._validate_volume(volume_ul)
        if not 1 <= cycles <= 10:
            raise ToolError("cycles must be between 1 and 10")
        if self.pipette_volume_ul != 0:
            raise ToolError("wash_tip requires an empty pipette")
        if self.dobot.get_alarms() != 0:
            raise ToolError("robot is in alarm state. Please initialize.")

        try:
            self.move_wash_station()
            self.dobot.move_arm(z=self.z_aspirate)
            for _ in range(cycles):
                self.picus.aspirate(volume_ul)
                self.picus.dispense(volume_ul)
            self.dobot.move_arm(z=self.z_home)
            self.picus.blow_out()
        except Exception as exc:
            raise ToolError(f"failed to wash pipette tip: {exc}") from exc

        return self._success("wash_tip")

    def add_color(self, color: str, well: int, volume: float, pipetting: bool = False) -> str:
        """
        Add specified amount (in mL) of color into 12-well plate.
        You can add blue, yellow, or red.
        You can specify the well by an integer between 0 and 11.
        The volume should be less than 3.
        """
        color_well = -1
        if color == "blue":
            color_well = 0
        elif color == "yellow":
            color_well = 1
        elif color == "red":
            color_well = 2
        else:
            return f"Failed to add color, because {color} is unavailable"

        if self.consumption[color] > 6000:
            color_well += 3
        
        remaining_volume = 1000 * volume
        while remaining_volume > 0:
            self.move_color_well(color_well)
            if remaining_volume > 1000:
                add_volume = 1000
            else:
                add_volume = remaining_volume
            self.aspirate(add_volume)
            self.move_mix_well(well)
            self.dispense(add_volume)
            self.consumption[color] += add_volume
            remaining_volume -= add_volume
        if pipetting:
            self.mix_well(1000)
        self.wash_tip()
        return f"robot added {volume} uL of {color} into well {well} successfully"

    def run_experiment(self, well: int, red: float, yellow: float, blue: float) -> ExperimentResult:
        """
        Run a color mix experiment.
        Simulates the experiment to add three kinds of colored waters.
        red, yellow, blue represents the added volume (mL) and they should be between 0 to 1.
        """

        for color, volume in {"red": red, "yellow": yellow, "blue": blue}.items():
            if not 0 <= volume <= 1:
                raise ToolError(f"{color} must be between 0 and 255.")

        if well in self.filled_wells.keys():
            raise ToolError(f"The well {well} is already filled.")

        if red > 0:
            self.add_color("red", well, red)
        if yellow > 0:
            self.add_color("yellow", well, yellow)
        if blue > 0:
            self.add_color("blue", well, blue, pipetting=True)

        self.filled_wells[well] = (red, yellow, blue)

        return ExperimentResult(
            well=well,
            recipe=ColorRecipe(red=red, yellow=yellow, blue=blue),
        )

    def get_image(self) -> Image:
        """
        Get image from camera
        """
        ret, frame = self.cap.read()
        # frame = cv2.resize(frame, (320, 180))
        # # frame = cv2.rotate(frame, cv2.ROTATE_180)
        # cv2.imwrite("camera.jpg", frame)
        # image_cv = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # image_pil = PILImage.fromarray(image_cv)
        # image_pil = image_pil.convert('RGB')

        vis = self._draw_debug(frame)
        disp = cv2.resize(vis, (320, 180))
        cv2.imwrite("average.jpg", frame)
        return Image(path="average.jpg")

    def get_well_color(self, frame, i):
        """Get average color of well i from given frame in RGB."""
        fx, fy = well_centers(self.cx, self.cy, self.p)[i]
        x, y = int(round(fx)), int(round(fy))
        rs = max(2, int(SAMPLE_FRAC * self.p))
        mask = np.zeros(frame.shape[:2], np.uint8)
        cv2.circle(mask, (x, y), rs, 255, -1)
        b, g, r, _ = cv2.mean(frame, mask=mask)
        return (int(round(r)), int(round(g)), int(round(b)))

    def _draw_debug(self, frame):
        """Returns debug image with sampling circle and rectangle in average color"""
        vis = frame.copy()
        rs = max(2, int(SAMPLE_FRAC * self.p))
        for k, (fx, fy) in enumerate(well_centers(self.cx, self.cy, self.p)):
            x, y = int(round(fx)), int(round(fy))
            r, g, b = self.get_well_color(frame, k)
            col = (0, 255, 0)
            cv2.circle(vis, (x, y), rs, col, 2)
            x0, y0 = x + rs + 10, y - rs - 10
            cv2.rectangle(vis, (x0, y0), (x0 + 30, y0 + 30), (b, g, r), -1)
            cv2.rectangle(vis, (x0, y0), (x0 + 30, y0 + 30), (255, 255, 255), 1)
        return vis

    def get_color_diff(self, well: int, hex: str) -> float:
        """Get the CIEDE 20000 color difference between the target hex color and the specified well.
        A return value of 0.0 means a perfect match; larger values mean the colors are further apart."""
        self._validate_mix_well(well)
        ret, frame = self.cap.read()
        filename = datetime.now().strftime("%Y%m%d-%H%M%S.jpg")
        debug = self._draw_debug(frame)
        cv2.imwrite(filename, debug)
        cv2.imwrite("original-" + filename, frame)
        if not ret:
            raise ToolError("Failed to read frame from camera")
        mean_rgb   = np.array(self.get_well_color(frame, well), dtype=float)
        hex_clean = hex.lstrip("#")
        if len(hex_clean) != 6:
            raise ToolError(f"Invalid hex color: '{hex}'. Expected 6 hex digits.")
        try:
            target_rgb = np.array(
                (
                    int(hex_clean[0:2], 16),
                    int(hex_clean[2:4], 16),
                    int(hex_clean[4:6], 16),
                ),
                dtype=float,
            )
        except ValueError as exc:
            raise ToolError(f"Invalid hex color: '{hex}'. Expected 6 hex digits.") from exc

        diff = skimage_color.deltaE_ciede2000(
            skimage_color.rgb2lab(target_rgb.reshape(1, 1, 3) / 255.0),
            skimage_color.rgb2lab(mean_rgb.reshape(1, 1, 3) / 255.0),
        )[0, 0]

        return float(diff)

    def get_state(self) -> dict[str, Any]:
        """Return the minimal commanded state; this is not sensor confirmation."""
        return {
            "location": self._location(),
            "pipette_volume_ul": self.pipette_volume_ul,
        }

    def get_labware_config(self) -> dict[str, Any]:
        """Return static labware information needed to plan a protocol."""
        return {
            "units": "uL",
            "pipette_capacity_ul": PIPETTE_CAPACITY_UL,
            "mix_well_capacity_ul": MIX_WELL_CAPACITY_UL,
            "color_wells": COLOR_WELLS,
            "mix_wells": list(range(12)),
            "wash_station": True,
        }

    def _validate_mix_well(self, well: int) -> None:
        if not 0 <= well <= 11:
            raise ToolError("mix well must be between 0 and 11")

    @staticmethod
    def _validate_volume(volume_ul: float) -> None:
        if not isfinite(volume_ul) or volume_ul <= 0:
            raise ToolError("volume_ul must be a finite number greater than 0")
        if volume_ul > PIPETTE_CAPACITY_UL:
            raise ToolError("volume_ul must not exceed the 1000 uL pipette capacity")

    def _location(self) -> dict[str, Any]:
        location: dict[str, Any] = {"kind": self.location_kind}
        if self.location_index is not None:
            location["index"] = self.location_index
        return location

    def _success(self, action: str) -> dict[str, Any]:
        return {
            "action": action,
            "location": self._location(),
            "pipette_volume_ul": self.pipette_volume_ul,
        }


if __name__ == "__main__":
    sdl = MySDL()

    # sdl.run_experiment(10, 0.1, 0.2, 0.3)

    mcp = FastMCP("Self-driving laboratory controller")
    mcp.tool(sdl.initialize)
    mcp.tool(sdl.return_home)
    mcp.tool(sdl.move_color_well)
    mcp.tool(sdl.move_mix_well)
    mcp.tool(sdl.move_wash_station)
    mcp.tool(sdl.aspirate)
    mcp.tool(sdl.dispense)
    mcp.tool(sdl.mix_well)
    mcp.tool(sdl.wash_tip)
    mcp.tool(sdl.get_state)
    mcp.tool(sdl.get_labware_config)
    mcp.tool(sdl.get_color_diff)
  

    # logging settings
    import logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    _formatter = logging.Formatter(
        fmt = "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setFormatter(_formatter)
    logger.addHandler(_stderr_handler)

    _file_handler = logging.FileHandler("mcp.log", mode="a", encoding="utf-8")
    _file_handler.setFormatter(_formatter)
    logger.addHandler(_file_handler)

    mcp.add_middleware(LoggingMiddleware(
        logger=logger,
        include_payloads=True,
    ))
    mcp.run(transport="http", host="0.0.0.0", port=8001)
