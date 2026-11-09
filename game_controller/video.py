"""E9's tested native YUV/GPU bridge, extracted without an experiment dependency.

M1 can redraw the cached video texture and a status panel without a new frame.
SDL resources remain on each display process's main thread.
"""

import ctypes as C
from pathlib import Path
import pygame
from pygame._sdl2.video import Window

SIZE = (1920, 1080)


def screen_renderer(sdl, display):
    """Find the screen's GPU through Windows; select the tested SDL backend."""
    class MonitorInfo(C.Structure):
        _fields_ = [("size", C.c_uint32), ("monitor", C.c_long * 4),
                    ("work", C.c_long * 4), ("flags", C.c_uint32),
                    ("device", C.c_wchar * 32)]

    class DisplayDevice(C.Structure):
        _fields_ = [("size", C.c_uint32), ("name", C.c_wchar * 32),
                    ("description", C.c_wchar * 128), ("flags", C.c_uint32),
                    ("device_id", C.c_wchar * 128), ("key", C.c_wchar * 128)]

    class Point(C.Structure):
        _fields_ = [("x", C.c_long), ("y", C.c_long)]

    bounds = (C.c_int * 4)()
    if sdl.SDL_GetDisplayBounds(display, C.byref(bounds)) != 0:
        raise RuntimeError("Cannot read screen bounds.")
    x, y, width, height = bounds
    user32 = C.WinDLL("user32", use_last_error=True)
    user32.MonitorFromPoint.argtypes = [Point, C.c_uint32]
    user32.MonitorFromPoint.restype = C.c_void_p
    user32.GetMonitorInfoW.argtypes = [C.c_void_p, C.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = C.c_int
    user32.EnumDisplayDevicesW.argtypes = [C.c_wchar_p, C.c_uint32,
                                         C.POINTER(DisplayDevice), C.c_uint32]
    user32.EnumDisplayDevicesW.restype = C.c_int
    monitor = user32.MonitorFromPoint(Point(x + width // 2, y + height // 2), 0)
    info = MonitorInfo(size=C.sizeof(MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, C.byref(info)):
        raise RuntimeError("Cannot identify the Windows monitor.")
    index = 0
    device = DisplayDevice(size=C.sizeof(DisplayDevice))
    while user32.EnumDisplayDevicesW(None, index, C.byref(device), 0):
        if device.name.casefold() == info.device.casefold():
            # SDL 2.28 D3D11 always uses DXGI adapter 0 (Intel here). D3D9
            # selects the screen adapter, so use it for NVIDIA's two screens.
            driver = "direct3d" if "nvidia" in device.description.lower() else "direct3d11"
            return driver, device.description
        index += 1
    raise RuntimeError(f"Cannot identify the GPU for {info.device}.")


class RendererInfo(C.Structure):
    _fields_ = [("name", C.c_char_p), ("flags", C.c_uint32),
                ("format_count", C.c_uint32), ("formats", C.c_uint32 * 16),
                ("max_width", C.c_int), ("max_height", C.c_int)]


class VideoWindow:
    """Upload native YUV planes to SDL, then blend an optional Pygame overlay."""

    def __init__(self, car, display):
        self.window = self.renderer = self.video = self.overlay = None
        self.video_format = None
        # Use Pygame's own SDL instance so native window IDs refer to its windows.
        self.sdl = C.CDLL(str(Path(pygame.__file__).parent / "SDL2.dll"))
        pointer, integer, uint = C.c_void_p, C.c_int, C.c_uint32
        signatures = {
            "SDL_GetError": (C.c_char_p, []),
            "SDL_GetDisplayBounds": (integer, [integer, pointer]),
            "SDL_GetWindowFromID": (pointer, [uint]),
            "SDL_GetNumRenderDrivers": (integer, []),
            "SDL_GetRenderDriverInfo": (integer, [integer, C.POINTER(RendererInfo)]),
            "SDL_CreateRenderer": (pointer, [pointer, integer, uint]),
            "SDL_RenderSetLogicalSize": (integer, [pointer, integer, integer]),
            "SDL_CreateTexture": (pointer, [pointer, uint, integer, integer, integer]),
            "SDL_SetTextureBlendMode": (integer, [pointer, integer]),
            "SDL_SetYUVConversionMode": (None, [integer]),
            "SDL_UpdateYUVTexture": (integer, [pointer, pointer, pointer, integer,
                                                pointer, integer, pointer, integer]),
            "SDL_UpdateTexture": (integer, [pointer, pointer, pointer, integer]),
            "SDL_RenderClear": (integer, [pointer]),
            "SDL_RenderCopy": (integer, [pointer, pointer, pointer, pointer]),
            "SDL_RenderPresent": (None, [pointer]),
            "SDL_DestroyTexture": (None, [pointer]),
            "SDL_DestroyRenderer": (None, [pointer]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.sdl, name)
            function.restype, function.argtypes = result, arguments
        try:
            driver, gpu = screen_renderer(self.sdl, display)
            driver_index = None
            for index in range(self.sdl.SDL_GetNumRenderDrivers()):
                info = RendererInfo()
                self.check(self.sdl.SDL_GetRenderDriverInfo(index, C.byref(info)))
                if info.name.decode() == driver:
                    driver_index = index
                    break
            if driver_index is None:
                raise RuntimeError(f"SDL renderer {driver} is unavailable.")
            position = 0x2FFF0000 | display  # SDL centered position on this screen.
            self.window = Window(f"Car {car} - M1 diagnostics", size=SIZE,
                                 position=(position, position), fullscreen_desktop=True)
            native = self.check(self.sdl.SDL_GetWindowFromID(self.window.id))
            # ACCELERATED=2; omit PRESENTVSYNC so the main loop controls pacing.
            self.renderer = self.check(self.sdl.SDL_CreateRenderer(native, driver_index, 2))
            self.check(self.sdl.SDL_RenderSetLogicalSize(self.renderer, *SIZE))
            print(f"Car {car}: SDL screen {display}, {gpu}, {driver}", flush=True)
        except BaseException:
            self.close()
            raise

    def check(self, result):
        """SDL returns null pointers or negative status codes on failure."""
        if result is None or result < 0:
            raise RuntimeError(self.sdl.SDL_GetError().decode(errors="replace"))
        return result

    def draw(self, frame, overlay=None):
        """Share PyAV plane pointers; honor their actual pitches/padded rows."""
        if frame is not None:
            if frame.format.name not in ("yuv420p", "yuvj420p"):
                frame = frame.reformat(format="yuv420p")
            mode = (0 if frame.color_range == 2 else 2 if frame.colorspace == 1
                    else 1 if frame.colorspace in (5, 6) else 3)
            self.sdl.SDL_SetYUVConversionMode(mode)  # Full range / BT.709 / BT.601 / auto.
            video_format = (frame.width, frame.height, mode)
            if video_format != self.video_format:
                if self.video:
                    self.sdl.SDL_DestroyTexture(self.video)
                    self.video = None
                self.video = self.check(self.sdl.SDL_CreateTexture(
                    self.renderer, int.from_bytes(b"IYUV", "little"), 1, frame.width, frame.height))
                self.video_format = video_format
            y, u, v = frame.planes
            self.check(self.sdl.SDL_UpdateYUVTexture(
                self.video, None, y.buffer_ptr, y.line_size,
                u.buffer_ptr, u.line_size, v.buffer_ptr, v.line_size))
        self.check(self.sdl.SDL_RenderClear(self.renderer))
        if self.video:
            self.check(self.sdl.SDL_RenderCopy(self.renderer, self.video, None, None))
        if overlay is not None:
            if self.overlay is None:
                # SDL ABGR8888 matches byte-ordered RGBA on little-endian Windows.
                self.overlay = self.check(self.sdl.SDL_CreateTexture(
                    self.renderer, 0x16762004, 1, *overlay.get_size()))
                self.check(self.sdl.SDL_SetTextureBlendMode(self.overlay, 1))
            rgba = pygame.image.tobytes(overlay, "RGBA")
            self.check(self.sdl.SDL_UpdateTexture(
                self.overlay, None, C.cast(C.c_char_p(rgba), C.c_void_p), overlay.get_width() * 4))
            destination = (C.c_int * 4)(0, 0, *overlay.get_size())
            self.check(self.sdl.SDL_RenderCopy(
                self.renderer, self.overlay, None, C.byref(destination)))
        self.sdl.SDL_RenderPresent(self.renderer)

    def close(self):
        """Release textures before their renderer, then destroy the window."""
        for texture in (self.overlay, self.video):
            if texture:
                self.sdl.SDL_DestroyTexture(texture)
        self.overlay = self.video = None
        if self.renderer:
            self.sdl.SDL_DestroyRenderer(self.renderer)
            self.renderer = None
        if self.window:
            self.window.destroy()
            self.window = None


