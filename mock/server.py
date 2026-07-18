import numpy as np

from PIL import Image as PILImage
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Color conversion & difference utilities
# ---------------------------------------------------------------------------

def rgb_to_lab(r: int, g: int, b: int) -> np.ndarray:
    """Convert an RGB colour (0-255) to CIE L*a*b* using Pillow.

    Pillow's LAB stores L in [0, 255] and a, b in [0, 255] (128 = 0),
    so we rescale to standard ranges: L [0, 100], a/b [-128, 127].
    """
    pixel = PILImage.new("RGB", (1, 1), (r, g, b)).convert("LAB").getpixel((0, 0))
    L = pixel[0] / 255.0 * 100.0
    a = pixel[1] - 128.0
    b = pixel[2] - 128.0
    return np.array([L, a, b])


def cie76(lab1: np.ndarray, lab2: np.ndarray) -> float:
    """Compute the CIE76 (ΔE*ab) colour difference — Euclidean distance in L*a*b*."""
    return float(np.linalg.norm(lab1 - lab2))


# ---------------------------------------------------------------------------
# SDL class
# ---------------------------------------------------------------------------

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
        self.filled_wells = {}
    
    def initialize(self):
        self.filled_wells = {}


    def run_experiment(
        self,
        well: int,
        red: float,
        yellow: float,
        blue: float,
    ) -> ExperimentResult:
        """
        Run a color mix experiment.
        Simulates the experiment to add three kinds of colored waters.
        red, yellow, and blue represent added volumes between 0 and 255.
        """

        for color, volume in {"red": red, "yellow": yellow, "blue": blue}.items():
            if not 0 <= volume <= 255:
                raise ToolError(f"{color} must be between 0 and 255.")

        if well in self.filled_wells.keys():
            raise ToolError(f"The well {well} is already filled.")

        self.filled_wells[well] = (red, yellow, blue)
        return ExperimentResult(
            well=well,
            recipe=ColorRecipe(red=red, yellow=yellow, blue=blue),
        )

    def get_color_diff(self, well: int, hex: str) -> float:
        """
        Get the CIE color difference between the target hex color and the last color set
        by run_experiment. A return value of 0.0 means a perfect match; larger values
        mean the colors are further apart.

        Args:
            hex: Target color as a hex string (e.g. '#FF5733' or 'FF5733').
        """
        if well not in self.filled_wells.keys():
            raise ValueError(f"Error: well {well} is empty.")

        hex_clean = hex.lstrip("#")
        if len(hex_clean) != 6:
            raise ValueError(f"Invalid hex color: '{hex}'. Expected 6 hex digits.")

        tr = int(hex_clean[0:2], 16)
        tg = int(hex_clean[2:4], 16)
        tb = int(hex_clean[4:6], 16)

        target_lab = rgb_to_lab(tr, tg, tb)

        # Estimate color
        r, y, b = self.filled_wells[well]
        total = r + y + b
        if total == 0:
            rgb = (255, 255, 255)
        else:
            T = {'r': (0.90, 0.10, 0.15),
                 'y': (0.95, 0.85, 0.05),
                 'b': (0.10, 0.35, 0.85)}
            f = {'r': r / total, 'y': y / total, 'b': b / total}

            rgb = []
            for i in range(3):
                t = 1.0
                for k in T:
                    t *= T[k][i] ** f[k]
                rgb.append(round(t * 255))

        well_lab = rgb_to_lab(*rgb)

        diff = cie76(target_lab, well_lab)

        return float(diff)


sdl = MySDL()

mcp = FastMCP("Self-driving laboratory controller")
mcp.tool(sdl.run_experiment)
mcp.tool(sdl.get_color_diff)
mcp.tool(sdl.initialize)

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8001)
