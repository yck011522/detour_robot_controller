"""Small, hardware-free checks for automatic minimap cache preparation."""

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import pygame

from game_controller import minimap_cache


class MinimapCacheTests(unittest.TestCase):
    """Use tiny artwork and isolated temporary caches to exercise rebuild decisions."""

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        # Workspace-owned fixtures inherit writable ACLs in the Windows sandbox.
        test_root = Path(__file__).resolve().parents[1] / "asset/cache"
        temporary_directory = (test_root / f"minimap-test-{uuid.uuid4().hex}").resolve()
        self.assertTrue(temporary_directory.is_relative_to(test_root.resolve()))
        temporary_directory.mkdir(parents=True)
        self.resources.callback(shutil.rmtree, temporary_directory)
        self.resources.enter_context(patch.object(minimap_cache, "NATIVE_SIZE", (16, 16)))
        self.resources.enter_context(redirect_stdout(io.StringIO()))
        self.source_directory = Path(temporary_directory) / "asset"
        self.cache_directory = self.source_directory / "cache/minimap"
        for relative_name in ("FloorG.png", "Track1FloorG.png", "ZoneA.png", "Cars/T1.png", "Coins/T1.png"):
            image_path = self.source_directory / relative_name
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image = pygame.Surface((16, 16), pygame.SRCALPHA)
            pygame.draw.rect(image, (255, 160, 20, 200), (4, 4, 8, 8))
            pygame.image.save(image, str(image_path))
        self.arguments = {"asset_directory": self.source_directory, "target_sizes": (8,),
                          "cache_directory": self.cache_directory}

    def test_missing_cache_builds_and_unchanged_cache_is_reused(self):
        """First launch creates sprites; the next launch performs no preparation."""
        self.assertTrue(minimap_cache.ensure_minimap_cache(**self.arguments))
        manifest = json.loads((self.cache_directory / "manifest.json").read_text())
        self.assertIn("8/51/Cars/T1.png", manifest)
        self.assertIn("16/255/FloorG.png", manifest)
        with patch.object(minimap_cache, "prepare_cached_layers") as prepare:
            self.assertFalse(minimap_cache.ensure_minimap_cache(**self.arguments))
            prepare.assert_not_called()

    def test_reexported_added_and_removed_artwork_rebuilds(self):
        """Pixel changes and source-list changes both invalidate prepared images."""
        minimap_cache.ensure_minimap_cache(**self.arguments)
        floor_path = self.source_directory / "FloorG.png"
        image = pygame.image.load(str(floor_path))
        image.fill((0, 200, 255, 255))
        pygame.image.save(image, str(floor_path))
        self.assertTrue(minimap_cache.ensure_minimap_cache(**self.arguments))
        added_path = self.source_directory / "Coins/T2.png"
        pygame.image.save(image, str(added_path))
        self.assertTrue(minimap_cache.ensure_minimap_cache(**self.arguments))
        added_path.unlink()
        self.assertTrue(minimap_cache.ensure_minimap_cache(**self.arguments))

    def test_missing_sprite_manifest_and_build_stamp_are_repaired(self):
        """A partial cache is automatically rebuilt before any renderer loads it."""
        minimap_cache.ensure_minimap_cache(**self.arguments)
        for relative_name in ("8/255/FloorG.png", "manifest.json", "build_info.json"):
            (self.cache_directory / relative_name).unlink()
            self.assertTrue(minimap_cache.ensure_minimap_cache(**self.arguments))
            self.assertTrue((self.cache_directory / relative_name).is_file())

    def test_invalid_manifest_changed_sizes_and_format_rebuild(self):
        """Preparation settings and malformed manifests invalidate the cache."""
        minimap_cache.ensure_minimap_cache(**self.arguments)
        (self.cache_directory / "manifest.json").write_text("not JSON")
        self.assertTrue(minimap_cache.ensure_minimap_cache(**self.arguments))
        resized_arguments = {**self.arguments, "target_sizes": (10,)}
        self.assertTrue(minimap_cache.ensure_minimap_cache(**resized_arguments))
        self.assertTrue((self.cache_directory / "10/255/FloorG.png").is_file())
        with patch.object(minimap_cache, "CACHE_FORMAT_VERSION", 2):
            self.assertTrue(minimap_cache.ensure_minimap_cache(**resized_arguments))

    def test_registration_and_hud_changes_do_not_rebuild_map_sprites(self):
        """Only the map PNGs influence cache freshness, not registrations or other UI files."""
        minimap_cache.ensure_minimap_cache(**self.arguments)
        (self.source_directory / "map_attributes.csv").write_text("tid,nfc_uid\nT1,01234567\n")
        ui_directory = self.source_directory / "ui_layout"
        ui_directory.mkdir()
        (ui_directory / "hud_frame.png").write_bytes(b"unrelated artwork")
        self.assertFalse(minimap_cache.ensure_minimap_cache(**self.arguments))


if __name__ == "__main__":
    unittest.main()
