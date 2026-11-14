"""Shared E13 geometry, colors, fonts, and split descriptions; edit these to tune the HUD."""

from pathlib import Path

# Runtime assets are root-owned; simulator results remain inside the experiment.
EXPERIMENT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_DIRECTORY = EXPERIMENT_DIRECTORY.parents[1]
UI_ASSET_DIRECTORY = REPOSITORY_DIRECTORY / "asset" / "ui_layout"
# This photograph is for simulator previews, not for the live video path.
REFERENCE_BACKGROUND_PATH = REPOSITORY_DIRECTORY / "asset" / "test_background" / "e13_reference.png"
RESULT_DIRECTORY = EXPERIMENT_DIRECTORY / "results"
LAYOUT_SIZE = (1920, 1080)
MINIMAP_POSITION = (110, 170)
SPLIT_POSITION = (1599, 530)
BACKGROUND_VEIL_ALPHA = 194
VIDEO_BLINK_SECONDS = 0.5
ORANGE = (255, 116, 31)
INK = (34, 38, 45)
GRAY = (103, 109, 118)
# Sensor circles are drawn procedurally, then antialiased in premultiplied space.
LINE_SENSOR_RADIUS = 14
LINE_SENSOR_OUTLINE_WIDTH = 2
LINE_SENSOR_ANTIALIAS_SCALE = 4
FONT_DIRECTORY = REPOSITORY_DIRECTORY / "asset" / "Font" / "static"
FONT_PATHS = {
    "orbitron": FONT_DIRECTORY / "Orbitron-Regular.ttf",
    "orbitron_bold": FONT_DIRECTORY / "Orbitron-Bold.ttf",
    "english": Path("C:/Windows/Fonts/segoeui.ttf"),
    "chinese": Path("C:/Windows/Fonts/msjhbd.ttc"),
}
# CSV split names describe the optional turn(s); other splits include straight ahead.
SPLIT_EXITS = {
    "left": ("left", "straight"),
    "right": ("straight", "right"),
    "leftright": ("left", "right"),
}
EXIT_LABELS = {
    "left": ("左轉", "LEFT"),
    "straight": ("直行", "STRAIGHT"),
    "right": ("右轉", "RIGHT"),
}
SPLIT_MODES = {
    "Auto from tag": "auto",
    "Hidden": "",
    "Left / straight": "left",
    "Straight / right": "right",
    "Left / right": "leftright",
}

# The renderer knows floor order without importing an experiment or its Tkinter GUI.
FLOOR_ORDER = ("-1", "G", "1", "2", "3", "4", "5", "6", "7")
