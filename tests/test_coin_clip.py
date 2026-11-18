from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import av

from game_controller.coin_clip import CoinClip


class CoinClipTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.window = Mock()
        self.clip = CoinClip(self.window, self.directory)
        self.addCleanup(self.clip.close)
        self.request = (10, "0")
        self.clock = self.enterContext(patch("game_controller.coin_clip.time.perf_counter"))

    def replace_clip(self, seconds):
        with av.open(str(self.directory / "Detour_Q01_1080p.mp4"), "w") as output:
            stream = output.add_stream("mpeg4", rate=4)
            stream.width = stream.height = 16
            for _ in range(seconds * 4):
                output.mux(stream.encode(av.VideoFrame(16, 16, "yuv420p")))
            output.mux(stream.encode())

    def draw(self, now, forward=False):
        self.clock.return_value = now
        return self.clip.draw(self.request, forward)

    def opacity(self):
        return self.window.sdl.SDL_SetTextureAlphaMod.call_args.args[1]

    def test_skip_uses_held_forward_after_four_seconds(self):
        self.replace_clip(9)
        for now, opacity in [(0, 0), (.5, 128), (3.99, 255), (4, 255), (4.5, 128)]:
            self.assertIsNone(self.draw(now, forward=True))
            self.assertEqual(self.opacity(), opacity)
        self.assertEqual(self.draw(5), "")
        self.assertIsNone(self.draw(6))

    def test_replacement_duration_and_minimum_lock(self):
        for start, duration in [(0, 7), (20, 2)]:
            self.replace_clip(duration)
            self.request = (start, "0")
            self.assertIsNone(self.draw(start))
            self.assertIsNone(self.draw(start + duration - .5))
            self.assertEqual(self.opacity(), 128)
            if duration < 4:
                self.assertIsNone(self.draw(start + duration))
            self.assertEqual(self.draw(start + max(duration, 4)), "")

    def test_missing_file_reports_once(self):
        self.assertIn("FileNotFoundError", self.draw(0))
        self.assertIsNone(self.draw(1))

    def test_cancelling_one_player_leaves_another_playing(self):
        self.replace_clip(9)
        other = CoinClip(Mock(), self.directory)
        self.addCleanup(other.close)
        self.assertIsNone(self.draw(0))
        self.assertIsNone(other.draw(self.request, False))
        self.assertIsNone(self.clip.draw(None, False))
        self.assertIsNone(self.clip.container)
        self.clock.return_value = 5
        self.assertIsNone(other.draw(self.request, False))
        self.assertIsNotNone(other.container)
