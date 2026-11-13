from pathlib import Path
import tempfile
import unittest

import av

from game_controller.capture import Recorder


class CaptureTests(unittest.TestCase):
    def test_close_flushes_a_playable_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.mkv"
            recorder = Recorder(path, 30)
            recorder.offer(bytes([20, 140, 220, 255]) * (64 * 48), 64, 48)
            recorder.close()
            self.assertFalse(recorder.thread.is_alive())
            self.assertEqual(recorder.error, "")
            with av.open(str(path)) as source:
                frames = list(source.decode(video=0))
            self.assertEqual(len(frames), 1)
            self.assertEqual((frames[0].width, frames[0].height), (64, 48))

    def test_encoder_error_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "file"
            blocker.write_text("not a directory")
            recorder = Recorder(blocker / "capture.mkv", 30)
            recorder.close()
            self.assertTrue(recorder.error)
