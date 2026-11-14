"""Hardware-free checks of the production HUD, locations, and SDL capture ordering."""

import ast
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame

from game_controller.app import build_display_snapshot
from game_controller.minimap import ASSET_DIRECTORY, load_tag_attributes, load_minimap_assets, compose_minimap
from game_controller.protocol import Telemetry
from game_controller.state import Car
from game_controller.ui_layout import load_layout_assets, compose_layout_overlay
from game_controller.video import VideoWindow


class PlayerLayoutTests(unittest.TestCase):
    """Exercise real artwork with production telemetry rather than simulator values."""

    @classmethod
    def setUpClass(cls):
        """Load shared artwork once through an in-memory display."""
        pygame.display.init()
        pygame.display.set_mode((1, 1))
        cls.layout_assets = load_layout_assets()
        cls.tag_attributes = load_tag_attributes(ASSET_DIRECTORY)
        cls.map_assets = load_minimap_assets(ASSET_DIRECTORY)

    @classmethod
    def tearDownClass(cls):
        """Release the in-memory display after pixel checks."""
        pygame.quit()

    def setUp(self):
        """Create fresh, accepted feedback with signed wheel speeds."""
        packet = Telemetry(1, 1, 1, 0, bytes.fromhex("01020304"), 10,
                           0b10000001, 0, 0, -125, 250, 11944)
        self.car = Car(packet=packet, received=10, echo_valid=True, echo_at=10)
        self.snapshot = build_display_snapshot(1, self.car, (1, 0, 1),
                                               (101, 33, 154, 262), self.tag_attributes, 10, .5)

    def test_units_stale_values_and_unregistered_driving(self):
        """Display localization cannot affect the existing movement rules."""
        self.assertEqual((self.snapshot["left_speed"], self.snapshot["right_speed"]), (-.125, .25))
        self.assertEqual(self.snapshot["battery"], 11.944)
        self.assertEqual(self.snapshot["sensor_mask"], 129)
        stale = build_display_snapshot(1, self.car, (1, 0, 1),
                                       (101, None, None, None), self.tag_attributes, 11, .5)
        self.assertFalse(stale["telemetry_fresh"])
        self.assertEqual(stale["battery"], self.snapshot["battery"])
        self.assertEqual(stale["sensor_mask"], self.snapshot["sensor_mask"])
        self.assertEqual(stale["tid"], "T101")
        unknown = build_display_snapshot(1, self.car, (1, 0, 0),
                                         (None,) * 4, self.tag_attributes, 10, .5)
        self.assertEqual(unknown["floor"], "--")
        self.assertEqual(self.car.command((1, 0, 0), 10, .5, .5, True, True,
                                         False, 1, 1, (False,) * 5), 3)
        self.assertEqual((unknown["coins"], unknown["split"]), (0, ""))

    def test_unknown_battery_and_floor_render(self):
        """Before telemetry, placeholders render without invented simulator positions."""
        snapshot = build_display_snapshot(1, Car(), (0, 0, 0),
                                          (None,) * 4, self.tag_attributes, 10, .5)
        self.assertIsNone(snapshot["battery"])
        image = compose_layout_overlay(self.layout_assets, snapshot, None, 0)
        self.assertEqual(image.get_size(), (1920, 1080))
        self.assertIn(("--", "orbitron_bold", 36, (34, 38, 45)), self.layout_assets["text"])

    def test_pause_priority_and_presence_are_separate_from_freshness(self):
        """Not Present requests telemetry artwork even with fresh accepted feedback."""
        baseline = compose_layout_overlay(self.layout_assets, self.snapshot, None, 0)
        cases = [
            ({"present": False, "battery_low": True, "line_lost": True}, "telemetry_lost"),
            ({"telemetry_fresh": False, "battery_low": True}, "telemetry_lost"),
            ({"battery_low": True, "line_lost": True}, "battery_low"),
            ({"line_lost": True}, "line_lost"),
        ]
        for changes, overlay_name in cases:
            snapshot = {**self.snapshot, **changes}
            actual = compose_layout_overlay(self.layout_assets, snapshot, None, 0)
            # Compare a center region outside the dynamic HUD labels.
            expected = baseline.copy()
            expected.blit(self.layout_assets["pause_overlays"][overlay_name], (0, 0),
                          special_flags=pygame.BLEND_PREMULTIPLIED)
            region = pygame.Rect(820, 200, 750, 650)
            self.assertEqual(pygame.image.tobytes(actual.subsurface(region), "RGBA"),
                             pygame.image.tobytes(expected.subsurface(region), "RGBA"))
        self.car.present = False
        snapshot = build_display_snapshot(1, self.car, (0, 0, 0),
                                          (None,) * 4, self.tag_attributes, 10, .5)
        self.assertTrue(snapshot["telemetry_fresh"])
        self.assertFalse(snapshot["present"])
        self.car.packet = replace(self.car.packet, controller=14, follower=10)
        snapshot = build_display_snapshot(1, self.car, (0, 0, 0),
                                          (None,) * 4, self.tag_attributes, 10, .5)
        self.assertTrue(snapshot["battery_low"])
        self.assertTrue(snapshot["line_lost"])

    def test_four_maps_and_hidden_markers(self):
        """Each display highlights its car; unknown/absent markers are omitted."""
        tags = (101, 33, 154, 262)
        images = []
        for car_number in range(1, 5):
            visible = compose_minimap(self.tag_attributes, self.map_assets, car_number, tags)
            hidden = compose_minimap(self.tag_attributes, self.map_assets, car_number, (None,) * 4)
            self.assertEqual(visible.get_size(), (700, 700))
            images.append(pygame.image.tobytes(visible, "RGBA"))
            self.assertNotEqual(images[-1], pygame.image.tobytes(hidden, "RGBA"))
            # Removing another car must affect this car's minimap too.
            other_car = car_number % 4
            fewer_tags = list(tags)
            fewer_tags[other_car] = None
            fewer = compose_minimap(self.tag_attributes, self.map_assets, car_number, fewer_tags)
            self.assertNotEqual(images[-1], pygame.image.tobytes(fewer, "RGBA"))
        self.assertEqual(len(set(images)), 4)

    def test_csv_replacement_is_read_on_next_load(self):
        """Registrations come from the current CSV, with no baked test UID mappings."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "map_attributes.csv"
            source.write_text("tid,floor,track,nfc_uid\nT1,G,1,AABBCCDD\n", encoding="utf-8")
            self.assertEqual(load_tag_attributes(Path(directory))[1]["nfc_uid"], "aabbccdd")
            source.write_text("tid,floor,track,nfc_uid\nT1,2,1,01020304\n", encoding="utf-8")
            self.assertEqual(load_tag_attributes(Path(directory))[1]["floor"], "2")
            self.assertEqual(load_tag_attributes(Path(directory))[1]["nfc_uid"], "01020304")

    def test_collection_hides_coin_and_respawn_restores_map_without_movement(self):
        tags = (None,) * 4
        initial = compose_minimap(self.tag_attributes, self.map_assets, 1, tags)
        collected = compose_minimap(self.tag_attributes, self.map_assets, 1, tags,
                                    hidden_coins=(1,))
        self.assertNotEqual(pygame.image.tobytes(initial, "RGBA"),
                            pygame.image.tobytes(collected, "RGBA"))
        restored = compose_minimap(self.tag_attributes, self.map_assets, 1, tags)
        self.assertEqual(pygame.image.tobytes(initial, "RGBA"),
                         pygame.image.tobytes(restored, "RGBA"))
        hud = build_display_snapshot(1, self.car, (0, 0, 0), tags,
                                     self.tag_attributes, 10, .5, 123, (1,))
        self.assertEqual((hud["coins"], hud["hidden_coins"]), (123, (1,)))
        self.assertEqual(self.tag_attributes[6]["video_id"], "2")

    def test_production_imports_do_not_reach_experiments(self):
        """Inspect production imports and literal runtime paths without importing experiments."""
        for source in (ASSET_DIRECTORY.parent / "game_controller").glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("experiments", node.module or "")
                elif isinstance(node, ast.Import):
                    self.assertTrue(all("experiments" not in name.name for name in node.names))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("experiments/", node.value.replace("\\", "/"))


class OverlayUploadTests(unittest.TestCase):
    """Check native rendering order without a camera, monitor, or recording device."""

    def test_cached_upload_and_capture_with_and_without_ui(self):
        """Unchanged pixels are reused, while captures retain their requested UI mode."""
        window = VideoWindow.__new__(VideoWindow)
        window.sdl = Mock()
        window.sdl.SDL_CreateTexture.return_value = 3
        window.sdl.SDL_ComposeCustomBlendMode.return_value = 123
        for name in ("SDL_RenderClear", "SDL_RenderCopy", "SDL_UpdateTexture", "SDL_SetTextureBlendMode"):
            getattr(window.sdl, name).return_value = 0
        window.renderer, window.video, window.overlay = 1, 2, None
        window.capture = Mock(side_effect=lambda recorder: window.sdl.captured())
        overlay = pygame.Surface((1920, 1080), pygame.SRCALPHA)
        window.draw(None, overlay, object(), False)
        names = [call[0] for call in window.sdl.mock_calls]
        self.assertLess(names.index("captured"), names.index("SDL_UpdateTexture"))
        window.sdl.SDL_ComposeCustomBlendMode.assert_called_once_with(2, 6, 1, 2, 6, 1)
        window.sdl.reset_mock()
        window.draw(None, overlay, object(), True, overlay_changed=False)
        window.sdl.SDL_UpdateTexture.assert_not_called()
        names = [call[0] for call in window.sdl.mock_calls]
        self.assertGreater(names.index("captured"), max(i for i, name in enumerate(names) if name == "SDL_RenderCopy"))


if __name__ == "__main__":
    unittest.main()
