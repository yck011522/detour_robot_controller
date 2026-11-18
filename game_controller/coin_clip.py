"""Timestamp-paced playback of a local gold-coin clip on one SDL display."""

import time

import av


class CoinClip:
    def __init__(self, window, directory):
        self.window = window
        self.directory = directory
        self.container = self.texture = self.request = None

    def close(self):
        if self.texture:
            self.window.sdl.SDL_DestroyTexture(self.texture)
            self.texture = None
        if self.container is not None:
            self.container.close()
            self.container = None
            self.frames = self.pending = None

    def open(self, video_id):
        path = self.directory / f"Detour_Q{int(video_id) + 1:02d}_1080p.mp4"
        self.container = av.open(str(path))
        stream = self.container.streams.video[0]
        self.duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                         else self.container.duration / av.time_base)
        self.frames = self.container.decode(stream)
        self.pending = next(self.frames)
        self.origin = self.pending.time
        # SDL ARGB8888 is byte-ordered BGRA on Windows.
        self.texture = self.window.check(self.window.sdl.SDL_CreateTexture(
            self.window.renderer, 0x16362004, 1, self.pending.width, self.pending.height))
        self.window.check(self.window.sdl.SDL_SetTextureBlendMode(self.texture, 1))
        self.started = time.perf_counter()
        self.end = self.duration

    def draw(self, request, forward):
        """Return None while idle/playing or a completion message exactly once."""
        try:
            if request != self.request:
                self.close()
                self.request = request
                if request is not None:
                    self.open(request[1])
            if self.container is None:
                return None

            elapsed = time.perf_counter() - self.started
            if forward and elapsed >= 4:
                self.end = min(self.end, elapsed + 1)
            if elapsed >= self.end:
                if elapsed >= 4:
                    self.close()
                    return ""
                return None

            frame = None
            while self.pending is not None and self.pending.time - self.origin <= elapsed:
                frame = self.pending
                self.pending = next(self.frames, None)
            if frame is not None:
                rgb = frame.reformat(format="bgra")
                plane = rgb.planes[0]
                self.window.check(self.window.sdl.SDL_UpdateTexture(
                    self.texture, None, plane.buffer_ptr, plane.line_size))
            fade = min(1.0, self.duration / 2)
            opacity = min(1.0, elapsed / fade, (self.end - elapsed) / fade)
            self.window.check(self.window.sdl.SDL_SetTextureAlphaMod(self.texture, round(255 * opacity)))
            self.window.check(self.window.sdl.SDL_RenderCopy(self.window.renderer, self.texture, None, None))
        except Exception as error:
            self.close()
            return f"{type(error).__name__}: {error}"
        return None
