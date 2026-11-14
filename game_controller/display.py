"""Independent per-screen video/status UI, with bounded controller communication."""

from collections import deque
import queue
import threading
import time

import av
import pygame

from .video import VideoWindow
from .capture import Recorder
from .ui_layout import LAYOUT_SIZE, VIDEO_BLINK_SECONDS, load_layout_assets, compose_layout_overlay
from .minimap import ASSET_DIRECTORY, load_tag_attributes, load_minimap_assets, compose_minimap


class Camera:
    """Own PyAV on the decoder thread; retry only this camera after failure."""

    def __init__(self, url, stop, reconnect_seconds):
        self.url, self.stop, self.reconnect_seconds = url, stop, reconnect_seconds
        self.latest = deque(maxlen=1)
        self.count, self.corrupt, self.last_frame = 0, 0, 0
        self.error = "Connecting"
        self.thread = threading.Thread(target=self.run, name="camera", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                with av.open(self.url, timeout=(3, 2), options={"rtsp_transport": "tcp",
                             "probesize": "65536", "analyzeduration": "100000"}) as container:
                    stream = container.streams.video[0]
                    if stream.codec_context.name != "h264":
                        raise RuntimeError("Configure camera for H.264")
                    stream.codec_context.thread_count = 2
                    stream.codec_context.thread_type = "FRAME"
                    for packet in container.demux(stream):
                        if self.stop.is_set():
                            return
                        try:
                            frames = packet.decode()
                        except av.error.InvalidDataError:
                            self.corrupt += 1
                            continue
                        for frame in frames:
                            if frame.is_corrupt:
                                self.corrupt += 1
                                continue
                            self.latest.append(frame)
                            self.count += 1
                            self.last_frame = time.perf_counter()
                            self.error = ""
                if not self.stop.is_set():
                    self.error = "Stream ended"
            except Exception as error:
                self.error = str(error)
            self.stop.wait(self.reconnect_seconds)


def offer(channel, value):
    """Replace snapshots rather than ever blocking control/rendering on a queue."""
    try:
        channel.put_nowait(value)
        return True
    except queue.Full:
        try:
            channel.get_nowait()
        except queue.Empty:
            pass
        try:
            channel.put_nowait(value)
            return True
        except queue.Full:
            return False


def panel_theme(state):
    """Only the selected admin car gets orange/red; all others are paused grey."""
    if state.get("quit_pending"):
        return "Confirm quit", (60, 62, 67, 240)
    if not state.get("admin"):
        return "Player control", (12, 17, 25, 215)
    if state.get("selected") != state.get("car"):
        return "Paused by Admin", (65, 67, 72, 235)
    if state.get("manual"):
        return "Controlling by Admin ? Direct Control", (105, 22, 25, 240)
    return "Controlling by Admin", (112, 54, 10, 240)


def panel_action(position, state):
    """Mouse controls live on each car's own admin overlay, never a fifth panel."""
    if not state.get("admin") or state.get("quit_pending") or state.get("capture_menu"):
        return None
    x, y = position
    if 300 <= y < 342:
        if 20 <= x < 260:
            return "presence"
    return None


def draw_panel(surface, state, camera, video_stale, font, small):
    """Show only player health in normal mode; admin diagnostics stay on top."""
    surface.fill((0, 0, 0, 0))
    now = time.perf_counter()
    active = camera.last_frame and now - camera.last_frame <= video_stale
    title, background = panel_theme(state)
    admin = state.get("admin") or state.get("quit_pending")
    pygame.draw.rect(surface, background, (0, 0, surface.get_width(), 365 if admin else 100))
    white, dim = (240, 240, 245), (200, 207, 215)

    def line(message, y, color=white, large=False):
        surface.blit((font if large else small).render(message, True, color), (20, y))

    if state.get("quit_pending"):
        line("Quit application?", 15, large=True)
        line("Enter: confirm quit     Escape: cancel and return", 60, large=True)
        line("All car driving is paused while this confirmation is open.", 108)
        line(state.get("global_status", ""), 152, dim)
        line(f"Car {state.get('car', '?')}: {state.get('feedback', 'Waiting for feedback')}", 190)
        return
    if state.get("capture_menu"):
        line("Video capture", 15, large=True)
        request = state.get("capture_request")
        if request:
            line(f"Recording car {request['car']} | {'With UI' if request['ui'] else 'Without UI'}", 65)
            line("Enter: stop recording     Escape: cancel menu", 110)
        else:
            line(f"Car: {state.get('capture_car', 1)} (press 1-4)", 65)
            line(f"{'With UI' if state.get('capture_ui', True) else 'Without UI'} (press U to toggle)", 110)
            line("Enter: start recording     Escape: cancel menu", 155)
        line(state.get("capture_status", ""), 210)
        line("Driving is paused while this menu is open.", 250, dim)
        return
    video = "PLAYING" if active else "UNAVAILABLE / STALE"
    if not admin:
        line(f"CAR {state.get('car', '?')} | Video: {video} | Feedback: {state.get('feedback', 'Waiting')} | Echo: {state.get('echo', 'Waiting')}", 12)
        line(f"Battery: {state.get('battery', '--')} | {state.get('reported', '')}", 46, dim)
        status = state.get("capture_status", "Capture: off")
        if state.get("warning"):
            line(f"{state['warning']} | {status}", 73, (255, 180, 90))
        else:
            line(status, 73, dim)
        return
    line(f"CAR {state.get('car', '?')} | {title}", 12, large=True)
    line(state.get("global_status", "Starting diagnostics"), 52, dim)
    line(f"Selected car: {state.get('selected', 1)} | {'STOP-ONLY TEST' if state.get('stop_only') else 'Keyboard controls selected car'}", 84)
    line(f"Video: {video} | Feedback: {state.get('feedback', 'Waiting')} | Echo: {state.get('echo', 'Waiting')}", 116)
    line(f"{'Present' if state.get('present') else 'Not Present'} | Battery: {state.get('battery', '--')} | {state.get('reported', '')}", 148)
    line(f"UID: {state.get('uid', '--')} | Tag age: {state.get('tag_age', '--')} | {state.get('sensors', '')}", 180, dim)
    line(f"Input F/L/R: {state.get('raw', (0, 0, 0))} | Sent: {state.get('command', '0x01')} | {state.get('reason', '')}", 212)
    line(state.get("warning") or state.get("global_counts", ""), 244, (255, 180, 90) if state.get("warning") else dim)
    line("C: capture | Space: admin | 1-4: select | arrows: drive | LCtrl: direct | Q: quit | Enter/Esc: confirm/cancel", 274, dim)
    checkbox = pygame.Rect(24, 307, 28, 28)
    pygame.draw.rect(surface, (35, 39, 45), checkbox)
    pygame.draw.rect(surface, white, checkbox, width=2)
    if state.get("present"):
        pygame.draw.lines(surface, (110, 235, 145), False,
                          [(30, 321), (37, 328), (47, 314)], width=3)
    surface.blit(small.render("Car Present", True, white), (64, 311))
    surface.blit(small.render(state.get("capture_status", "Capture: off"), True, white), (300, 311))


def play(car, url, display_index, settings, snapshots, events, stop, bindings):
    """One SDL owner per screen. A camera failure never sets the group stop flag."""
    av.logging.set_level(av.logging.ERROR)
    pygame.display.init()
    pygame.font.init()
    window = camera = recorder = None
    capture_path = None
    failed_capture = None
    local_stop = threading.Event()
    state = {"car": car}
    try:
        # Pygame artwork conversion needs a pixel format; this window stays hidden.
        pygame.display.set_mode((1, 1), pygame.HIDDEN)
        layout_assets = load_layout_assets()
        tag_attributes = load_tag_attributes(ASSET_DIRECTORY)
        map_assets = load_minimap_assets(ASSET_DIRECTORY)
        previous_map_tags = previous_overlay_key = None
        map_surface = layout_surface = None
        overlay = pygame.Surface(LAYOUT_SIZE, pygame.SRCALPHA)
        video_wash = pygame.Surface(LAYOUT_SIZE, pygame.SRCALPHA)
        video_wash.fill((128, 128, 128, settings["video_wash_alpha"]))
        video_wash = video_wash.premul_alpha()
        window = VideoWindow(car, display_index)
        surface = pygame.Surface((1920, 365), pygame.SRCALPHA)
        font, small = pygame.font.Font(None, 32), pygame.font.Font(None, 26)
        camera = Camera(url, local_stop, settings["camera_retry_s"])
        clock = pygame.time.Clock()
        key_names = ["forward", "left", "right", "backward", "manual"]
        key_codes = [bindings[key] for key in key_names]
        report_at = 0
        previous_panel_visible = False
        while not stop.is_set():
            for event in pygame.event.get():
                if event.type in (pygame.QUIT, pygame.WINDOWCLOSE):
                    events.put_nowait(("quit", car))
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    action = panel_action(event.pos, state)
                    if action:
                        events.put_nowait(("action", car, action))
                elif event.type == pygame.KEYDOWN and not getattr(event, "repeat", False):
                    events.put_nowait(("key", car, event.key))
            try:
                while True:
                    state = snapshots.get_nowait()
            except queue.Empty:
                pass
            now = time.perf_counter()
            if now >= report_at:
                focused = pygame.key.get_focused()
                pressed = pygame.key.get_pressed()
                events.put_nowait(("input", car, now, focused, tuple(bool(pressed[k]) for k in key_codes)))
                events.put_nowait(("video", car, now, camera.last_frame, camera.count, camera.corrupt, camera.error))
                report_at = now + .1
            try:
                frame = camera.latest.popleft()
            except IndexError:
                frame = None
            request = state.get("capture_request")
            desired = request if request and request["car"] == car else None
            desired_path = desired["path"] if desired else None
            if recorder and (desired_path != capture_path or recorder.error):
                recorder.close()
                error = recorder.error
                events.put_nowait(("capture_status", car, capture_path, "failed" if error else "stopped", error))
                if error:
                    failed_capture = capture_path
                recorder = None
                capture_path = None
            if desired and recorder is None and desired_path != failed_capture:
                capture_path = desired_path
                recorder = Recorder(capture_path, settings["display_fps"])
                events.put_nowait(("capture_status", car, capture_path, "recording", ""))
            # Coin respawns can change the map while all cars are stationary.
            hud = state.get("hud")
            overlay_changed = False
            if hud is not None:
                hud["video_present"] = bool(camera.last_frame and now - camera.last_frame <= settings["video_stale_s"])
                map_key = (hud["car_tags"], hud.get("hidden_coins", ()))
                if map_key != previous_map_tags:
                    map_surface = compose_minimap(tag_attributes, map_assets, car, hud["car_tags"],
                                                  hidden_coins=hud.get("hidden_coins", ()))
                    previous_map_tags = map_key
                overlay_key = (tuple(hud.items()),
                               int(now / VIDEO_BLINK_SECONDS) % 2 if hud["video_present"] else 0)
                overlay_changed = overlay_key != previous_overlay_key
                if overlay_changed:
                    layout_surface = compose_layout_overlay(layout_assets, hud, map_surface, now)
                    previous_overlay_key = overlay_key
            # Admin/menu text is drawn over the visitor HUD, including pause artwork.
            panel_visible = bool(state.get("admin") or state.get("quit_pending") or state.get("capture_menu"))
            overlay_changed = overlay_changed or panel_visible or previous_panel_visible != panel_visible
            if overlay_changed:
                overlay.fill((0, 0, 0, 0))
                overlay.blit(video_wash, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
                if layout_surface is not None:
                    overlay.blit(layout_surface, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
                if panel_visible:
                    draw_panel(surface, state, camera, settings["video_stale_s"], font, small)
                    overlay.blit(surface.premul_alpha(), (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
            previous_panel_visible = panel_visible
            try:
                window.draw(frame, overlay, recorder, desired.get("ui", True) if desired else True,
                            overlay_changed=overlay_changed)
            except Exception as error:
                if recorder is None:
                    raise
                recorder.close()
                events.put_nowait(("capture_status", car, capture_path, "failed", str(error)))
                failed_capture, capture_path, recorder = capture_path, None, None
            clock.tick(settings["display_fps"])
    except Exception as error:
        try:
            events.put_nowait(("display_error", car, str(error)))
        except queue.Full:
            pass
    finally:
        if recorder:
            recorder.close()
        local_stop.set()
        if camera:
            camera.thread.join(timeout=6)
        if window:
            window.close()
        pygame.quit()
        events.cancel_join_thread()
