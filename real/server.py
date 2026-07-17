import time
import sys
import csv
import os
import shutil
import sys

import cv2
import numpy as np
from PIL import Image as PILImage
from skimage import color as skimage_color
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from magician import MagicianController
from picus import PicusWired

from typing import Any, Dict
from datetime import datetime

import nimo

ROWS, COLS = 3, 4
SAMPLE_FRAC = 0.27   # 平均を取る円の半径 / ピッチ

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
        self.z_aspirate = 40
        self.z_dispense = 55
        self.z_home = 70
        self.home_pose = (200, 0, self.z_home)
        self.dobot = MagicianController()
        self.picus = PicusWired("47381939")
        self.cap = cv2.VideoCapture(1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        # ウェルグリッドのキャリブレーション値(calibrate.py で決めた値に置き換える)
        self.cx = 640
        self.cy = 360
        self.p = 117
    
    def initialize(self):
        self.return_home()

    def return_home(self) -> str:
        """
        Move the robot arm to its predefined home position.
        """
        try:
            self.dobot.move_arm(self.home_pose[0], self.home_pose[1], self.home_pose[2])
            return "robot returned home successfully"
        except Exception as e:
            print(e)
            return f"failed to return home, error: {e}"

    def move_color_well(self, i: int) -> str:
        """
        Move the robot on the color well specified by an integer (0-indexed).
        """
        x = 217 - 39 * (i // 3)
        y = -112 - 39 * (i % 3)
        try:
            self.dobot.move_arm(z=self.z_home)
            self.dobot.move_arm(x=x, y=y)
            return f"robot moved to color well {i}"
        except Exception as e:
            return f"failed to move to color well, error: {e}"
    
    def move_mix_well(self, i) -> str:
        """
        Move the robot on the mix well specified by an integer (0-indexed).
        """
        x = 217 - 25 * (i // 4)
        y = 189 - 25 * (i % 4)
        try:
            self.dobot.move_arm(z=self.z_home)
            joints = self.dobot.get_current_joints()
            self.dobot.move_arm(x=x, y=y)
            return f"robot moved to mix well {i}"
        except Exception as e:
            print(e)
            return f"failed to move to mix well, error: {e}"

    def aspirate(self, volume: float) -> str:
        """
        Aspirate the specified liquid volume.

        The arm moves to aspirate height, performs aspiration,
        and returns to the home Z position.

        Args:
          volume: Liquid volume to aspirate.
        """
        try:
            self.dobot.move_arm(z=self.z_aspirate)
            self.picus.aspirate(volume)
            self.dobot.move_arm(z=self.z_home)
            return f"robot aspirated {volume} uL"
        except Exception as e:
            print(e)
            return f"robot failed to aspirate {volume} uL, error: {e}"

    def dispense(self, volume: float) -> str:
        """
        Dispense the specified liquid volume.

        The arm moves to dispense height, performs dispensing,
        and returns to the home Z position.

        Args:
          volume: Liquid volume to dispense.
        """
        try:
            self.dobot.move_arm(z=self.z_dispense)
            self.picus.dispense(volume)
            self.dobot.move_arm(z=self.z_home)
            self.picus.blow_out()
            return f"robot dispensed {volume} uL"
        except Exception as e:
            print(e)
            return f"robot failed to dispense {volume} uL, error: {e}"
    
    def wash_tip(self) -> str:
        """
        Wash the pipette tip.

        Call this function after dispensing in mix well.
        """
        vol = 1000
        try:
            self.dobot.move_arm(z=self.z_home)
            self.return_home()
            self.dobot.move_arm(z=self.z_aspirate)
            for _ in range(1):
                self.picus.aspirate(vol)
                self.picus.dispense(vol)
            self.dobot.move_arm(z=self.z_home)
            self.picus.blow_out()
            return "robot washed the pipette tip successfully"
        except Exception as e:
            print(e)
            return f"robot failed to wash the pipette tip, error: {e}" 
        
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
            self.dobot.move_arm(z=self.z_aspirate+1.0)
            self.picus.aspirate(1000)
            self.picus.dispense(1000)
            self.dobot.move_arm(z=self.z_home)
            self.picus.blow_out()
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
        """与えられたフレームから、i番目(0..11)のウェルの平均色を(R,G,B)で返す。"""
        fx, fy = well_centers(self.cx, self.cy, self.p)[i]
        x, y = int(round(fx)), int(round(fy))
        rs = max(2, int(SAMPLE_FRAC * self.p))
        mask = np.zeros(frame.shape[:2], np.uint8)
        cv2.circle(mask, (x, y), rs, 255, -1)
        b, g, r, _ = cv2.mean(frame, mask=mask)   # cv2.mean は BGR 順
        return (int(round(r)), int(round(g)), int(round(b)))

    def _draw_debug(self, frame):
        """フレーム上に全ウェルのサンプリング円と平均色の矩形を描いた画像を返す。"""
        vis = frame.copy()
        rs = max(2, int(SAMPLE_FRAC * self.p))
        for k, (fx, fy) in enumerate(well_centers(self.cx, self.cy, self.p)):
            x, y = int(round(fx)), int(round(fy))
            r, g, b = self.get_well_color(frame, k)
            # i=黄, target=赤, その他=緑 で円を描き分け
            col = (0, 255, 0)# if k == i else (0, 0, 255) if k == target else (0, 255, 0)
            cv2.circle(vis, (x, y), rs, col, 2)
            # 取得した平均色をウェルの近く(円の右上)に矩形で表示
            x0, y0 = x + rs + 10, y - rs - 10
            cv2.rectangle(vis, (x0, y0), (x0 + 30, y0 + 30), (b, g, r), -1)  # 塗りはBGR順
            cv2.rectangle(vis, (x0, y0), (x0 + 30, y0 + 30), (255, 255, 255), 1)
        return vis

    def get_color_diff(self, well:int, hex: str) -> float:
        """Get the CIEDE 20000 color difference between the target hex color and the specified well.
        A return value of 0.0 means a perfect match; larger values mean the colors are further apart."""
        target = 11
        ret, frame = self.cap.read()
        filename = datetime.now().strftime("%Y%m%d-%H%M%S.jpg")
        debug = self._draw_debug(frame)
        cv2.imwrite(filename, debug)
        if not ret:
            raise RuntimeError("Failed to read frame from camera")
        mean_rgb   = np.array(self.get_well_color(frame, well), dtype=float)
        # target_rgb = np.array(self.get_well_color(frame, target), dtype=float)
        hex_clean = hex.lstrip("#")
        if len(hex_clean) != 6:
            raise ToolError(f"Invalid hex color: '{hex}'. Expected 6 hex digits.")

        target_rgb = np.array((int(hex_clean[0:2], 16), int(hex_clean[2:4], 16), int(hex_clean[4:6], 16)), dtype=float)
        
        diff = skimage_color.deltaE_ciede2000(
            skimage_color.rgb2lab(target_rgb.reshape(1, 1, 3) / 255.0),
            skimage_color.rgb2lab(mean_rgb.reshape(1, 1, 3) / 255.0),
        )[0, 0]

        return float(diff)

if __name__ == "__main__":
    sdl = MySDL()

    # sdl.run_experiment(10, 0.1, 0.2, 0.3)
    
    mcp = FastMCP("Self-driving laboratory controller")
    mcp.tool(sdl.initialize)
    mcp.tool(sdl.run_experiment)
    mcp.tool(sdl.get_color_diff)
    mcp.run(transport="http", host="0.0.0.0", port=8001)
