"""Independent per-screen video/status UI, with bounded controller communication."""

from collections import deque
import queue
import threading
import time

import av
import pygame

from .video import VideoWindow


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
    if not state.get("admin") or state.get("quit_pending"):
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
    video = "PLAYING" if active else "UNAVAILABLE / STALE"
    if not admin:
        line(f"CAR {state.get('car', '?')} | Video: {video} | Feedback: {state.get('feedback', 'Waiting')} | Echo: {state.get('echo', 'Waiting')}", 12)
        line(f"Battery: {state.get('battery', '--')} | {state.get('reported', '')}", 46, dim)
        if state.get("warning"):
            line(state["warning"], 73, (255, 180, 90))
        return
    line(f"CAR {state.get('car', '?')} | {title}", 12, large=True)
    line(state.get("global_status", "Starting diagnostics"), 52, dim)
    line(f"Selected car: {state.get('selected', 1)} | {'STOP-ONLY TEST' if state.get('stop_only') else 'Keyboard controls selected car'}", 84)
    line(f"Video: {video} | Feedback: {state.get('feedback', 'Waiting')} | Echo: {state.get('echo', 'Waiting')}", 116)
    line(f"{'Present' if state.get('present') else 'Not Present'} | Battery: {state.get('battery', '--')} | {state.get('reported', '')}", 148)
    line(f"UID: {state.get('uid', '--')} | Tag age: {state.get('tag_age', '--')} | {state.get('sensors', '')}", 180, dim)
    line(f"Input F/L/R: {state.get('raw', (0, 0, 0))} | Sent: {state.get('command', '0x01')} | {state.get('reason', '')}", 212)
    line(state.get("warning") or state.get("global_counts", ""), 244, (255, 180, 90) if state.get("warning") else dim)
    line("Space: admin | 1-4: select | arrows: drive | LCtrl: direct | Q: quit | Enter/Esc: confirm/cancel", 274, dim)
    for rect, label in (((20, 300, 240, 42), "Mark Not Present" if state.get("present") else "Mark Present"),):
        pygame.draw.rect(surface, (35, 39, 45), rect, border_radius=5)
        surface.blit(small.render(label, True, white), (rect[0] + 12, rect[1] + 10))


def play(car, url, display_index, settings, snapshots, events, stop, bindings):
    """One SDL owner per screen. A camera failure never sets the group stop flag."""
    av.logging.set_level(av.logging.ERROR)
    pygame.display.init()
    pygame.font.init()
    window = camera = None
    local_stop = threading.Event()
    state = {"car": car}
    try:
        window = VideoWindow(car, display_index)
        surface = pygame.Surface((1920, 365), pygame.SRCALPHA)
        font, small = pygame.font.Font(None, 32), pygame.font.Font(None, 26)
        camera = Camera(url, local_stop, settings["camera_retry_s"])
        clock = pygame.time.Clock()
        key_names = ["forward", "left", "right", "backward", "manual"]
        key_codes = [bindings[key] for key in key_names]
        report_at = 0
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
            draw_panel(surface, state, camera, settings["video_stale_s"], font, small)
            window.draw(frame, surface)  # Cached video/placeholder still gets a fresh status overlay.
            clock.tick(settings["display_fps"])
    except Exception as error:
        try:
            events.put_nowait(("display_error", car, str(error)))
        except queue.Full:
            pass
    finally:
        local_stop.set()
        if camera:
            camera.thread.join(timeout=6)
        if window:
            window.close()
        pygame.quit()
        events.cancel_join_thread()
