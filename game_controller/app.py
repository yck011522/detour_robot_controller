"""M1 controller main thread: HID + admin intent + World State tick at 50 Hz.

This diagnostic application never imports UR RTDE or opens a UR/tool socket.
No zone or board claims are made; map filtering is deliberately deferred to M2.
"""

import argparse
from collections import deque
from datetime import datetime
import json
import math
import multiprocessing
import queue
import statistics
import threading
import time

import pygame

from .config import ROOT, load
from .display import offer, play
from .modem import Modem, open_modem
from .protocol import AUTO_STOP, CONTROLLERS, FOLLOWERS
from .state import Car


class Log:
    """Keep disk flushing outside the 50 Hz thread, with explicit overflow failure."""

    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}.log"
        self.file = self.path.open("x", encoding="utf-8")
        self.queue = queue.Queue(maxsize=8192)
        self.failed = False
        self.thread = threading.Thread(target=self.run, name="log", daemon=True)
        self.thread.start()
        self("START", outcome="INCOMPLETE")

    def __call__(self, event, **data):
        record = {"time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                  "monotonic": time.perf_counter(), "event": event, **data}
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.failed = True
        if event not in ("RX", "TX", "RX_REJECTED", "COMMAND"):
            print(json.dumps(record), flush=True)

    def run(self):
        try:
            while True:
                record = self.queue.get()
                if record is None:
                    break
                self.file.write(json.dumps(record) + "\n")
                self.file.flush()
        except Exception:
            self.failed = True
        finally:
            self.file.close()

    def close(self):
        try:
            self.queue.put(None, timeout=1)
        except queue.Full:
            self.failed = True
        self.thread.join(timeout=3)


def connect_gamepad(required):
    """E2's single-device/index-0 policy; no COM metadata or new identity scheme."""
    joystick = None
    try:
        if pygame.joystick.get_count():
            joystick = pygame.joystick.Joystick(0)
            if joystick.get_numbuttons() > required:
                return joystick
    except pygame.error:
        pass
    if joystick:
        disconnect_gamepad(joystick)
    return None


def disconnect_gamepad(joystick):
    """Removal can race a state read; cleanup must not break reconnection."""
    try:
        joystick.quit()
    except pygame.error:
        pass


def bindings_from_config(names):
    result = {}
    for name, value in names.items():
        # INI uses readable K_Q; Pygame names letter constants K_q.
        if len(value) == 3 and value.startswith("K_") and value[2].isalpha():
            value = "K_" + value[2].lower()
        if not value.startswith("K_") or not hasattr(pygame, value):
            raise ValueError(f"Unknown configured key: {value}")
        result[name] = getattr(pygame, value)
    if result["toggle"] in (result["quit"], result["cancel"]):
        raise ValueError("Admin toggle, cancel and quit keys must be distinct")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=0, help="timed run; 0 waits for Q/window close")
    parser.add_argument("--stop-only", action="store_true", help="never emit driving bits, regardless of keyboard/joystick input")
    parser.add_argument("--no-video", action="store_true", help="central diagnostics only; skip camera/display processes")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("--seconds must be finite and nonnegative")
    config = load()
    settings = config["settings"]
    log = Log(config["log_directory"])
    modem = joystick = screen = None
    players, channels = [], []
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    events = context.Queue(maxsize=1024)
    cars = [Car(present=i in config["present"]) for i in range(1, 5)]
    admin, selected = config["admin"], 1
    keyboard, keyboard_at = (False,) * 5, 0
    history, session = {}, 0
    last_commands, sequence, generated_total, skipped = None, 1, 0, 0
    tick_intervals, tick_work = deque(maxlen=15000), deque(maxlen=15000)
    last_tick, outcome = None, "FAIL"
    model = {"connected": False, "session": 0, "error": "", "invalid_lines": 0}
    videos = {i: {"status": "Starting", "at": 0} for i in range(1, 5)}
    focus_source = None
    last_buttons = None
    joystick_instance = None
    stop_confirmed = set()
    stop_sent_generation = 0
    capture_menu = False
    capture_car, capture_ui = 1, True
    capture_request = None
    capture_status = "Capture: off"
    quit_pending = False
    started = time.perf_counter()
    try:
        pygame.display.init()
        pygame.font.init()
        pygame.joystick.init()
        keys = bindings_from_config(config["bindings"])
        max_button = max(index for mapping in config["buttons"] for index in mapping)
        joystick = connect_gamepad(max_button)
        if joystick is None:
            names = [pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]
            raise RuntimeError(f"Gamepad index 0 needs button indexes 0-{max_button}; available gamepads: {names}")
        log("GAMEPAD_CONNECTED", name=joystick.get_name(), buttons=joystick.get_numbuttons())
        joystick_instance = joystick.get_instance_id()
        stream = open_modem(config["port"])  # Initial failure is fatal, before opening videos.
        modem = Modem(config["port"], stream, settings["command_expiry_s"], settings["modem_retry_s"], log)
        # Hidden SDL event owner for HID only; all visible controls use the four displays.
        screen = pygame.display.set_mode((1, 1), pygame.HIDDEN)
        if not args.no_video:
            from .monitors import resolve_serials
            indexes = resolve_serials([serial for _, _, serial in config["cameras"]])
            for (car, url, serial), display in zip(config["cameras"], indexes):
                log("MONITOR_RESOLVED", car=car, monitor_text_serial=serial, sdl_index=display)
                channel = context.Queue(maxsize=1)
                player = context.Process(target=play, args=(car, url, display, settings, channel, events, stop, keys), name=f"display-{car}")
                player.start()
                players.append(player)
                channels.append(channel)
        log("CONFIG", profile="M1 bench; UR/tool inactive", port=config["port"], settings=settings,
            cameras=config["cameras"], buttons=config["buttons"], present=sorted(config["present"]),
            bindings=config["bindings"], stop_only=args.stop_only, log_path=str(log.path))
        next_tick = next_ui = next_metrics = time.perf_counter()
        quitting = False
        logged_exits = set()

        def key_action(key):
            nonlocal admin, selected, quitting, keyboard, keyboard_at, quit_pending
            nonlocal capture_menu, capture_car, capture_ui, capture_request, capture_status
            if quit_pending:
                if key == keys["accept"]:
                    quitting = True
                    log("QUIT_CONFIRMED")
                elif key == keys["cancel"]:
                    quit_pending = False
                    keyboard, keyboard_at = (False,) * 5, 0
                    log("QUIT_CANCELLED")
                return
            if capture_menu:
                if key == keys["cancel"]:
                    capture_menu = False
                elif key == keys["accept"]:
                    if capture_request is not None:
                        capture_request = None
                        capture_status = "Capture: stopping"
                    elif args.no_video:
                        capture_status = "Capture unavailable with --no-video"
                    else:
                        path = ROOT / "logs/captures" / f"car_{capture_car}_{datetime.now():%Y%m%d_%H%M%S_%f}.mkv"
                        capture_request = {"car": capture_car, "ui": capture_ui, "path": str(path)}
                        capture_status = f"Capture: starting car {capture_car}"
                    capture_menu = False
                elif capture_request is None:
                    if key == keys["capture_ui"]:
                        capture_ui = not capture_ui
                    for car in range(1, 5):
                        if key == keys[f"car_{car}"]:
                            capture_car = car
                keyboard, keyboard_at = (False,) * 5, 0
                return
            if admin and key == keys["capture"]:
                capture_menu = True
                capture_car = selected
                keyboard, keyboard_at = (False,) * 5, 0
                return
            if key == keys["quit"]:
                quit_pending = True
                keyboard, keyboard_at = (False,) * 5, 0
                log("QUIT_REQUESTED")
            elif key == keys["toggle"]:
                admin = not admin
                log("ADMIN_MODE", active=admin)
            elif admin and key == keys["cancel"]:
                # Cancel current keyboard intent; Space remains the mode toggle.
                keyboard, keyboard_at = (False,) * 5, 0
                log("ADMIN_CANCEL")
            elif admin:
                for car in range(1, 5):
                    if key == keys[f"car_{car}"]:
                        selected = car
                        log("ADMIN_SELECTION", car=car)

        while not quitting:
            now = time.perf_counter()
            delay = next_tick - now
            if delay > 0:
                time.sleep(delay)
            tick_start = time.perf_counter()
            if last_tick is not None:
                tick_intervals.append(tick_start - last_tick)
            last_tick = tick_start
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    key_action(keys["quit"])
                elif event.type == pygame.JOYDEVICEREMOVED and joystick and event.instance_id == joystick_instance:
                    disconnect_gamepad(joystick)
                    joystick = None
                    joystick_instance = None
                    log("GAMEPAD_LOST")
                elif event.type == pygame.KEYDOWN and not getattr(event, "repeat", False):
                    key_action(event.key)
            if joystick is None:
                joystick = connect_gamepad(max_button)
                if joystick:
                    joystick_instance = joystick.get_instance_id()
                    log("GAMEPAD_CONNECTED", name=joystick.get_name())
            raw_buttons = (False,) * (max_button + 1)
            if joystick:
                try:
                    raw_buttons = tuple(bool(joystick.get_button(i)) for i in range(max_button + 1))
                except pygame.error:
                    disconnect_gamepad(joystick)
                    joystick = None
                    joystick_instance = None
                    log("GAMEPAD_LOST")
            if raw_buttons != last_buttons:
                log("GAMEPAD_INPUT", buttons=[int(value) for value in raw_buttons],
                    cars=[tuple(int(raw_buttons[b]) for b in mapping) for mapping in config["buttons"]])
                last_buttons = raw_buttons
            # Only the focused application window supplies admin keyboard intent.
            if pygame.key.get_focused():
                pressed = pygame.key.get_pressed()
                keyboard = tuple(bool(pressed[keys[k]]) for k in ("forward", "left", "right", "backward", "manual"))
                keyboard_at, focus_source = tick_start, 0
            try:
                for _ in range(512):
                    message = events.get_nowait()
                    kind, car, *values = message
                    if kind == "quit":
                        key_action(keys["quit"])
                    elif kind == "action" and admin and not quit_pending and not capture_menu:
                        if values[0] == "presence":
                            cars[car - 1].present = not cars[car - 1].present
                            log("PRESENCE", car=car, present=cars[car - 1].present)
                    elif kind == "key":
                        key_action(values[0])
                    elif kind == "input":
                        timestamp, focused, pressed = values
                        if focused and timestamp >= keyboard_at:
                            keyboard, keyboard_at, focus_source = pressed, timestamp, car
                        elif not focused and focus_source == car and timestamp >= keyboard_at:
                            keyboard, keyboard_at = (False,) * 5, timestamp
                    elif kind == "video":
                        timestamp, frame_at, decoded, corrupt, error = values
                        videos[car] = {"status": "Playing" if frame_at and timestamp - frame_at <= settings["video_stale_s"] else "Unavailable / stale",
                                       "at": timestamp, "frame_at": frame_at, "decoded": decoded, "corrupt": corrupt, "error": error}
                    elif kind == "capture_status":
                        path, status, error = values
                        log("CAPTURE", car=car, path=path, status=status, error=error)
                        if capture_request and capture_request["path"] == path:
                            capture_status = f"Capture: {status} car {car}" + (f" ({error})" if error else "")
                            if status in ("failed", "stopped"):
                                capture_request = None
                        elif capture_request is None:
                            capture_status = f"Capture: {status} car {car}" + (f" ({error})" if error else "")
                    elif kind == "display_error":
                        log("DISPLAY_FAILED", car=car, error=values[0])
            except queue.Empty:
                pass
            received, sent, model = modem.drain()
            if model["session"] != session:
                session = model["session"]
                history.clear()
                for car in cars:
                    car.echo_valid = False
                log("MODEM_SESSION", session=session)
            for sent_at, sent_session, number, generated, commands in sent:
                if sent_session == session:
                    history[number] = (sent_session, generated, sent_at)
            history = {number: record for number, record in history.items() if tick_start - record[2] < 5}
            for received_at, received_session, packet in received:
                if received_session != session:
                    continue
                car = cars[packet.car - 1]
                previous = (car.packet.controller, car.packet.follower, car.packet.sensors not in (0, 255)) if car.packet else None
                result = car.accept(packet, received_at, received_session, history, settings["telemetry_stale_s"])
                current = (packet.controller, packet.follower, packet.sensors not in (0, 255))
                if result not in ("duplicate", "stale") and current != previous:
                    log("CAR_STATE", car=packet.car, controller=packet.controller,
                        follower=packet.follower, sensors=packet.sensors,
                        wheels=[packet.left_mm_s, packet.right_mm_s], warning=car.warning())
                if result in ("uid", "stale"):
                    log("NFC_UID" if result == "uid" else "TELEMETRY_STALE", car=packet.car, uid=car.uid, sequence=packet.sequence)
            if model.get("overflow") or log.failed:
                raise RuntimeError("Diagnostic/event queue failed or overflowed; stopping driving")
            if tick_start - keyboard_at > settings["keyboard_stale_s"]:
                keyboard = (False,) * 5
            commands, states = [], []
            for index, car in enumerate(cars):
                raw = tuple(int(raw_buttons[button]) for button in config["buttons"][index])
                command = car.command(raw, tick_start, settings["telemetry_stale_s"], settings["echo_stale_s"],
                                      model["connected"], joystick is not None, admin, selected, index + 1,
                                      keyboard, args.stop_only or quitting or quit_pending or capture_menu)
                commands.append(command)
                packet = car.packet
                states.append({"car": index + 1, "admin": admin, "stop_only": args.stop_only,
                               "present": car.present, "selected": selected, "raw": raw,
                               "manual": bool(admin and keyboard[4]), "quit_pending": quit_pending,
                               "capture_menu": capture_menu, "capture_car": capture_car,
                               "capture_ui": capture_ui, "capture_request": capture_request,
                               "capture_status": capture_status,
                               "global_status": f"Modem {config['port']}: {'connected' if model['connected'] else 'reconnecting'} | Gamepad: {'connected' if joystick else 'missing'}",
                               "global_counts": f"World States: {generated_total} | Skipped ticks: {skipped} | Invalid lines: {model['invalid_lines']}",
                               "feedback": f"Fresh ({tick_start-car.received:.2f}s)" if car.feedback_ok(tick_start, settings["telemetry_stale_s"]) else "Missing / stale",
                               "echo": f"Fresh ({car.echo_age*1000:.0f} ms estimate)" if car.echo_ok(tick_start, settings["echo_stale_s"]) else "Waiting / stale",
                               "battery": f"{packet.battery_mv/1000:.2f} V" if packet else "--",
                               "reported": f"{CONTROLLERS.get(packet.controller, str(packet.controller))} / {FOLLOWERS.get(packet.follower, str(packet.follower))}" if packet else "No telemetry",
                               "uid": car.uid or "No tag yet", "tag_age": f"{packet.nfc_age_ms} ms" if packet else "--",
                               "sensors": f"Line: {packet.sensors:08b}" if packet else "",
                               "command": f"0x{command:02X}", "reason": car.last_reason, "warning": car.warning()})
            topology = 16 if admin else 0  # No zone availability asserted in M1.
            modem.submit(sequence, topology, commands)
            generated_total += 1
            sequence = (sequence + 1) & 65535
            if commands != last_commands:
                log("COMMAND", admin=admin, selected=selected, movements=commands,
                    reasons=[car.last_reason for car in cars])
                last_commands = commands
            if tick_start >= next_ui:
                for index, channel in enumerate(channels):
                    offer(channel, states[index])
                next_ui = tick_start + 1 / settings["ui_snapshot_hz"]
            if tick_start >= next_metrics:
                intervals = sorted(tick_intervals)
                log("METRICS", world_generated=generated_total, skipped_ticks=skipped,
                    tick_mean_ms=statistics.mean(intervals)*1000 if intervals else None,
                    tick_p95_ms=intervals[int((len(intervals)-1)*.95)]*1000 if intervals else None,
                    tick_max_ms=max(intervals)*1000 if intervals else None,
                    work_max_ms=max(tick_work)*1000 if tick_work else None,
                    cars=[{"car": i+1, "packets": car.telemetry_count, "duplicates": car.duplicates,
                           "stale": car.stale, "missing_estimate": car.missing, "resyncs": car.resets,
                           "echo_ok": car.echo_ok(tick_start, settings["echo_stale_s"])} for i, car in enumerate(cars)],
                    videos=videos)
                next_metrics = tick_start + settings["metrics_interval_s"]
            for player in players:
                if player.exitcode is not None and player.pid not in logged_exits:
                    log("DISPLAY_EXIT", name=player.name, exitcode=player.exitcode)
                    logged_exits.add(player.pid)
            if args.seconds and tick_start - started >= args.seconds:
                quitting = True
            tick_work.append(time.perf_counter() - tick_start)
            next_tick += 1 / settings["control_hz"]
            if next_tick < time.perf_counter():
                skipped += 1
                next_tick = time.perf_counter() + 1 / settings["control_hz"]
        outcome = "PASS"
    except KeyboardInterrupt:
        outcome = "INTERRUPTED"
    except Exception as error:
        log("FAILURE", error=str(error))
        if screen:
            try:
                from pygame._sdl2 import messagebox
                messagebox("M1 startup/runtime error", str(error), error=True)
            except Exception:
                pass
    finally:
        if modem:
            # Diagnostic profile has no robot ownership, so no HOME wait is
            # claimed. Attempt stop-only snapshots and observe reported wheels.
            stop_sent_generation = time.perf_counter()
            until = stop_sent_generation + settings["shutdown_stop_s"]
            while time.perf_counter() < until:
                modem.submit(sequence, 16 if admin else 0, [AUTO_STOP] * 4)
                sequence = (sequence + 1) & 65535
                received, sent, state = modem.drain()
                for sent_at, sent_session, number, generated, commands in sent:
                    history[number] = (sent_session, generated, sent_at)
                for received_at, received_session, packet in received:
                    record = history.get(packet.echo)
                    if record and record[0] == received_session and record[1] >= stop_sent_generation and abs(packet.left_mm_s) <= 5 and abs(packet.right_mm_s) <= 5:
                        stop_confirmed.add(packet.car)
                time.sleep(.02)
            # Reservation presence must not exclude a potentially driven car.
            required = {1, 2, 3, 4}
            log("SHUTDOWN_STOP", confirmed=sorted(stop_confirmed), unconfirmed=sorted(required-stop_confirmed),
                meaning="fresh stop-command receipt plus reported wheel speed <=5 mm/s; not physical inspection")
            if required - stop_confirmed:
                outcome = "FAIL"
            modem.close()
        stop.set()
        for player in players:
            player.join(timeout=14)  # Allow encoder flush plus camera cleanup.
            if player.is_alive():
                player.terminate()  # Only a video process; never the I/O owner.
                player.join(timeout=2)
        for channel in channels:
            channel.cancel_join_thread()
            channel.close()
        events.cancel_join_thread()
        events.close()
        pygame.quit()
        log("RESULT", outcome=outcome, generated=generated_total, skipped_ticks=skipped,
            profile="M1 bench; UR/tool untouched", stop_only=args.stop_only)
        log.close()
        print(f"{outcome}: {log.path}", flush=True)
    return int(outcome == "FAIL")
