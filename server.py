import numpy as np

from PIL import Image as PILImage
from fastmcp import FastMCP

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

class MySDL:

    def __init__(self):
        self._last_rgb: tuple[int, int, int] | None = None
        self.filled_wells = {}

    def run_experiment(self, well: int, red: float, green: float, blue: float) -> str:
        """
        Run a color mix experiment.
        Generates a 200x200 solid-color image from the given RGB values (0-255)
        and returns it.
        """

        if well in self.filled_wells.keys():
            return f"Failed to run experiment. The well {well} is already filled."
        else:
            self.filled_wells[well] = True
        
        r = int(np.clip(red, 0, 255))
        g = int(np.clip(green, 0, 255))
        b = int(np.clip(blue, 0, 255))

        self._last_rgb = (r, g, b)

        return f"Experiment succeeded. Well {well} is now filled."

    def get_color_diff(self, hex: str) -> float:
        """
        Get the negative CIE76 color difference between the target hex color
        and the last color set by run_experiment.
        A return value of 0.0 means a perfect match; more negative means further apart.

        Args:
            hex: Target color as a hex string (e.g. '#FF5733' or 'FF5733').
        """
        if self._last_rgb is None:
            raise ValueError("run_experiment has not been called yet.")

        hex_clean = hex.lstrip("#")
        if len(hex_clean) != 6:
            raise ValueError(f"Invalid hex color: '{hex}'. Expected 6 hex digits.")

        tr = int(hex_clean[0:2], 16)
        tg = int(hex_clean[2:4], 16)
        tb = int(hex_clean[4:6], 16)

        target_lab = rgb_to_lab(tr, tg, tb)
        experiment_lab = rgb_to_lab(*self._last_rgb)

        diff = cie76(target_lab, experiment_lab)

        return float(diff)


sdl = MySDL()

mcp = FastMCP("Self-driving laboratory controller")
mcp.tool(sdl.run_experiment)
mcp.tool(sdl.get_color_diff)

if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8001)
