"""Bounded background video encoding; renderer readback stays on its owner thread."""

from fractions import Fraction
from pathlib import Path
import queue
import threading
import time

import av


class Recorder:
    def __init__(self, path, fps):
        self.path, self.fps = Path(path), fps
        self.frames = queue.Queue(maxsize=4)
        self.stopping = threading.Event()
        self.error = ""
        self.dropped = 0
        self.started = time.perf_counter()
        self.thread = threading.Thread(target=self.run, name="capture", daemon=True)
        self.thread.start()

    def offer(self, pixels, width, height):
        try:
            self.frames.put_nowait((pixels, width, height, time.perf_counter()))
        except queue.Full:
            self.dropped += 1

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=6)
        if self.thread.is_alive():
            self.error = "Recording encoder did not finish within six seconds"

    def run(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Matroska remains playable if a process is interrupted before close.
            with av.open(str(self.path), "w", format="matroska") as output:
                stream = None
                last_pts = -1
                while not self.stopping.is_set() or not self.frames.empty():
                    try:
                        pixels, width, height, captured = self.frames.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if stream is None:
                        stream = output.add_stream("libx264", rate=self.fps)
                        stream.width, stream.height = width, height
                        stream.pix_fmt = "yuv420p"
                        stream.options = {"preset": "ultrafast", "crf": "23"}
                    frame = av.VideoFrame(width, height, "rgba")
                    # SDL readback rows are tightly packed; PyAV may pad rows.
                    plane = frame.planes[0]
                    if plane.line_size == width * 4:
                        plane.update(pixels)
                    else:
                        padded = bytearray(plane.buffer_size)
                        for y in range(height):
                            padded[y * plane.line_size:y * plane.line_size + width * 4] = pixels[y * width * 4:(y + 1) * width * 4]
                        plane.update(padded)
                    pts = round((captured - self.started) * self.fps)
                    if pts <= last_pts:
                        continue
                    frame.pts, frame.time_base = pts, Fraction(1, self.fps)
                    last_pts = pts
                    for packet in stream.encode(frame):
                        output.mux(packet)
                if stream is not None:
                    for packet in stream.encode():
                        output.mux(packet)
        except Exception as error:
            self.error = str(error)
