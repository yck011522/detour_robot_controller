"""Simulator paths and the production HUD settings used by authoring tools."""

from pathlib import Path
import sys

REPOSITORY_DIRECTORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_DIRECTORY))
from game_controller.ui_layout import (UI_ASSET_DIRECTORY, REFERENCE_BACKGROUND_PATH,
    FONT_PATHS, LAYOUT_SIZE, SPLIT_EXITS, EXIT_LABELS, SPLIT_MODES,
    VIDEO_BLINK_SECONDS, GRAY)

RESULT_DIRECTORY = Path(__file__).resolve().parent / "results"
