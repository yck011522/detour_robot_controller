"""Game controller main thread: HID + admin intent + World State tick at 50 Hz.

By default the real robot runs in its own process and moves the three boards
around; zone tokens, the closure echo and evacuation live here. Use --robot off
for static boards without robot connections, or --robot dry-run for simulation.
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
from .coins import CoinGame, CoinStore
from .display import offer, play
from .modem import Modem, open_modem
from .protocol import AUTO_STOP, CONTROLLERS, FOLLOWERS
from .state import Car
from .minimap import ASSET_DIRECTORY, load_tag_attributes
from .minimap_cache import ensure_minimap_cache
from .navigation import validate_tags
from .boards import BoardStartup, TagResolver, ask_boards_console
from .robot import worker as robot_worker
from .scheduler import Scheduler
from .transfers import Transfers
from .zones import CAR_ZONES, Zones


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


def build_display_snapshot(car_number, car, raw_input, car_tags, tag_attributes,
                           now, telemetry_timeout, coins=0, hidden_coins=()):
    """Map accepted telemetry to HUD values without changing driving eligibility."""
    packet = car.packet  # Last accepted values remain visible during telemetry loss.
    tag_number = car_tags[car_number - 1]
    location = tag_attributes[tag_number] if tag_number is not None else None
    return {
        "active_car": car_number, "present": car.present, "car_tags": tuple(car_tags),
        "floor": location["floor"] if location else "--",
        "tid": location["tid"] if location else "--",
        "forward": bool(raw_input[0]), "left": bool(raw_input[1]), "right": bool(raw_input[2]),
        "telemetry_fresh": car.feedback_ok(now, telemetry_timeout),
        "battery": packet.battery_mv / 1000 if packet else None,
        "sensor_mask": packet.sensors if packet else 0,
        "left_speed": packet.left_mm_s / 1000 if packet else 0,
        "right_speed": packet.right_mm_s / 1000 if packet else 0,
        "battery_low": bool(packet and packet.controller == 14),
        "line_lost": bool(packet and packet.follower == 10),
        "coins": coins, "hidden_coins": tuple(hidden_coins),
        "split": car.navigation.split,
        "selected_exit": car.navigation.selected_exit,
        "split_locked": car.navigation.locked, "video_present": False,
    }


def observe_location(car, packet, now, resolver, coins, car_tags, zones=None):
    """One accepted UID interpretation for navigation, marker and coin scoring."""
    match = resolver.lookup(car.uid)
    if match.kind == "ignored":
        car.navigation.advance(now)
        return match, None  # No context transition, marker move or coin visit.
    if match.kind == "mapped":
        car_tags[packet.car - 1] = match.tid
    nav = car.navigation
    changed = nav.observe(car.uid, packet.nfc_age_ms, now, resolver.tags.get(match.tid))
    # A distinct tag with neither inzone nor reservezone (including an unknown
    # UID) releases the zone token. No-read and ignored tags never do.
    if changed and zones is not None and not nav.inzone and not nav.reservezone:
        zones.release_token(packet.car)
    return match, coins.observe(packet.car, car.uid, now)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=0, help="timed run; 0 waits for Q/window close")
    parser.add_argument("--stop-only", action="store_true", help="never emit driving bits, regardless of keyboard/joystick input")
    parser.add_argument("--no-video", action="store_true", help="central diagnostics only; skip camera/display processes")
    parser.add_argument("--robot", choices=("off", "dry-run", "live"), default="live",
                        help="off: boards never move; dry-run: simulated robot; live: real UR10e (default), "
                             "which moves only after the operator confirms the startup questions")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("--seconds must be finite and nonnegative")
    config = load()
    settings = config["settings"]
    log = Log(config["log_directory"])
    modem = joystick = screen = None
    coin_store = None
    board_startup = None
    players, channels = [], []
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    events = context.Queue(maxsize=1024)
    # The robot runs in its own process; only commands and events cross.
    robot_process = robot_commands = robot_events = None
    robot_phase = "detecting" if args.robot != "off" else "disabled"
    robot_text, robot_home, robot_jog_at = "", None, 0
    zones, transfers = Zones(), None
    robot_paused = shutting_down = False
    # Shared display locations are separate from control state.
    last_known_tags = [None] * 4
    cars = [Car(present=i in config["present"]) for i in range(1, 5)]
    for car in cars:
        car.navigation.lock_age_ms = settings["split_lock_age_ms"]
    navigation_states = [None] * 4
    admin, selected = config["admin"], 1
    keyboard, keyboard_at = (False,) * 5, 0
    history, session = {}, 0
    last_commands, sequence, generated_total, skipped = None, 1, 0, 0
    tick_intervals, tick_work = deque(maxlen=15000), deque(maxlen=15000)
    last_tick, outcome = None, "FAIL"
    model = {"connected": False, "session": 0, "error": "", "invalid_lines": 0}
    videos = {i: {"status": "Starting", "at": 0} for i in range(1, 5)}
    coin_clips = [None] * 4
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
        tag_attributes = load_tag_attributes(ASSET_DIRECTORY)
        validate_tags(tag_attributes)
        resolver = TagResolver(tag_attributes, ROOT / "config/dynamic_nfc.ini")
        board_startup = BoardStartup(ROOT / "config/board_state.ini", robot=args.robot != "off")
        coin_store = CoinStore(ROOT / "config/coin_count.ini")
        coin_game = CoinGame(resolver.tags, coin_store.totals,
                             silver_points=settings["silver_points"],
                             gold_points=settings["gold_points"],
                             respawn_seconds=settings["respawn_seconds"], resolver=resolver)
        log("COIN_TOTALS_LOADED", totals=coin_game.totals,
            path=str(coin_store.path), availability="all coins available")
        if not args.no_video:
            ensure_minimap_cache(target_sizes=(700,))
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

        def start_robot():
            nonlocal robot_process, robot_commands, robot_events, robot_phase
            robot_commands, robot_events = context.Queue(), context.Queue()
            robot_process = context.Process(target=robot_worker.run, args=(robot_commands, robot_events, args.robot),
                                            name="robot", daemon=True)
            robot_process.start()
            robot_commands.put(("detect",))
            robot_phase = "detecting"
            log("ROBOT_STARTED", mode=args.robot)

        def redetect():
            nonlocal robot_phase
            if robot_process.is_alive():
                robot_commands.put(("detect",))
                robot_phase = "detecting"
            else:
                start_robot()

        def disable_robot():
            nonlocal robot_phase
            if robot_process.is_alive():
                robot_commands.put(("stop",))
            board_startup.robot = False
            robot_phase = "disabled"
            log("ROBOT_DISABLED")

        def on_robot_event(event):
            nonlocal robot_phase, robot_text, robot_home
            name = event[0]
            if name == "detected":
                robot_home, robot_text = event[1], event[2]
                robot_phase = "ready" if robot_home else "away"
                log("ROBOT_DETECTED", home=robot_home, text=robot_text)
            elif name == "unavailable":
                robot_text, robot_phase = event[1], "unavailable"
                log("ROBOT_UNAVAILABLE", reason=robot_text)
            elif name == "jog_ended":
                redetect()
            elif name in ("powered_off", "power_off_failed"):
                log("ROBOT_" + name.upper(), detail=event[1:] or None)
            elif transfers is not None:
                transfers.handle(event)

        def drain_robot_events():
            if robot_events is None:
                return
            try:
                while True:
                    on_robot_event(robot_events.get_nowait())
            except queue.Empty:
                pass

        def on_inventory(arrangement):
            resolver.assign(arrangement)
            saved = board_startup.persist(arrangement)
            saved.add_done_callback(lambda done: done.exception() and
                                    log("BOARD_STATE_SAVE_FAILED", error=str(done.exception())))

        def acknowledged(zone, since):
            """Closure receipt: the zone's car echoed a snapshot generated after the claim.

            An absent or telemetry-silent car owes no echo (the agreed last-known-state exception).
            """
            car = cars[CAR_ZONES[zone] - 1]
            now = time.perf_counter()
            if not car.present or not car.feedback_ok(now, settings["telemetry_stale_s"]):
                return True
            return car.echo_ok(now, settings["echo_stale_s"]) and car.echo_generated >= since

        def robot_prompt():
            if robot_phase == "detecting":
                return ["Preparing the robot and checking its position...", robot_text]
            if robot_phase == "jogging":
                return ["Moving the robot. Keep holding the key; release it to stop.", robot_text]
            footer = "R: check again   Enter: run without the robot   Escape: quit"
            if robot_phase == "unavailable":
                return ["Robot unavailable", robot_text, footer]
            return ["The robot is not at a home position", robot_text,
                    "Hold LEFT: move to AC home   Hold RIGHT: move to BD home",
                    "Watch the path yourself and release the key to stop.", footer]

        if args.robot != "off":
            start_robot()
        if args.no_video:
            # No visible windows in this diagnostic mode; no driving commands
            # have been submitted while its console questionnaire is open.
            if args.robot != "off":
                deadline = time.perf_counter() + 90
                while robot_phase in ("detecting", "jogging") and time.perf_counter() < deadline:
                    drain_robot_events()
                    time.sleep(.05)
                if robot_phase not in ("ready", "disabled"):
                    # The keyboard startup prompt in the main loop still applies.
                    print(f"Robot is not ready ({robot_text}); answer with R / Enter / Escape.", flush=True)
            if robot_phase in ("ready", "disabled"):
                ask_boards_console(board_startup)
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
        log("CONFIG", profile=f"robot={args.robot}", port=config["port"], settings=settings,
            cameras=config["cameras"], buttons=config["buttons"], present=sorted(config["present"]),
            bindings=config["bindings"], stop_only=args.stop_only, log_path=str(log.path))
        next_tick = next_ui = next_metrics = time.perf_counter()
        quitting = False
        logged_exits = set()

        def key_action(key):
            nonlocal admin, selected, quitting, keyboard, keyboard_at, quit_pending, shutting_down
            nonlocal capture_menu, capture_car, capture_ui, capture_request, capture_status
            if quit_pending:
                if key == keys["accept"]:
                    quit_pending = False
                    if transfers is None or transfers.state == "fault" or shutting_down:
                        quitting = True  # Nothing to home, a blocked robot, or a second explicit confirmation.
                        log("QUIT_CONFIRMED")
                    else:
                        shutting_down, transfers.finishing = True, True
                        log("QUIT_WAITING_FOR_ROBOT", state=transfers.state)
                elif key == keys["cancel"]:
                    quit_pending = False
                    keyboard, keyboard_at = (False,) * 5, 0
                    log("QUIT_CANCELLED")
                return
            if robot_phase not in ("ready", "disabled"):
                if key == keys["quit"]:
                    quit_pending = True
                elif key == keys["cancel"]:
                    quitting = True
                elif key == keys["retry"]:
                    redetect()
                elif key == keys["accept"] and robot_phase in ("unavailable", "away"):
                    disable_robot()
                return
            if shutting_down and key == keys["cancel"]:
                shutting_down, transfers.finishing = False, False
                log("QUIT_CANCELLED")
                return
            if not board_startup.confirmed:
                keyboard, keyboard_at = (False,) * 5, 0
                if key == keys["quit"]:
                    quit_pending = True
                elif board_startup.future is None:
                    if key == keys["accept"]:
                        board_startup.confirm()
                    elif key == keys["left"]:
                        board_startup.cycle(-1)
                    elif key == keys["right"]:
                        board_startup.cycle(1)
                    elif key in (keys["forward"], keys["backward"]):
                        board_startup.selected = (board_startup.selected +
                                                  (-1 if key == keys["forward"] else 1)) % 4
                    else:
                        for index in range(4):
                            if key == keys[f"car_{index + 1}"]:
                                board_startup.selected = index
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
                    elif kind == "action" and admin and board_startup.confirmed and not quit_pending and not capture_menu:
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
                    elif kind == "coin_clip_done":
                        request, error = values
                        if coin_clips[car - 1] == request:
                            coin_clips[car - 1] = None
                        if error:
                            log("COIN_CLIP_FAILED", car=car, video_id=request[1], error=error)
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
            drain_robot_events()
            if board_startup.poll():
                robot_enabled = board_startup.robot
                resolver.assign(board_startup.saved, robot_enabled)
                zones.boards = set(resolver.board_zones)
                if robot_enabled:
                    transfers = Transfers(zones, Scheduler(), board_startup.saved, robot_home,
                                          robot_commands.put, log, on_inventory)
                keyboard, keyboard_at = (False,) * 5, 0
                log("BOARDS_CONFIRMED", arrangement=board_startup.saved,
                    path=str(board_startup.path), bindings=resolver.by_uid,
                    ignored_uids=sorted(resolver.ignored_uids), robot=robot_enabled)
            if robot_phase in ("away", "jogging") and tick_start >= robot_jog_at and (keyboard[1] or keyboard[2]):
                robot_commands.put(("jog", "ac_home" if keyboard[1] else "bd_home"))
                robot_jog_at = tick_start + .1
                robot_phase = "jogging"
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
                if result not in ("duplicate", "stale") and board_startup.confirmed:
                    match, award = observe_location(car, packet, received_at, resolver,
                                                    coin_game, last_known_tags, zones)
                    if match.kind == "ignored" and result == "uid":
                        log("NFC_IGNORED", car=packet.car, uid=car.uid,
                            reason="known board tag without a slot in the confirmed layout")
                    if award is not None:
                        coin_store.submit(coin_game.totals)
                        log("COIN_COLLECTED", **award)
                        video_id = resolver.tags[match.tid]["video_id"]
                        if video_id and coin_clips[packet.car - 1] is None:
                            coin_clips[packet.car - 1] = (received_at, video_id)
                current = (packet.controller, packet.follower, packet.sensors not in (0, 255))
                if result not in ("duplicate", "stale") and current != previous:
                    log("CAR_STATE", car=packet.car, controller=packet.controller,
                        follower=packet.follower, sensors=packet.sensors,
                        wheels=[packet.left_mm_s, packet.right_mm_s], warning=car.warning())
                if result in ("uid", "stale"):
                    log("NFC_UID" if result == "uid" else "TELEMETRY_STALE", car=packet.car, uid=car.uid, sequence=packet.sequence)
            if model.get("overflow") or log.failed:
                raise RuntimeError("Diagnostic/event queue failed or overflowed; stopping driving")
            for error, totals in coin_store.drain_errors():
                log("COIN_SAVE_FAILED", error=error, totals=totals, action="skipped; game continues")
            if tick_start - keyboard_at > settings["keyboard_stale_s"]:
                keyboard = (False,) * 5
            if board_startup.confirmed:
                # Order: token grants first, then the robot's claim, so a car and the
                # robot never both win the same zone within one tick.
                for index, car in enumerate(cars):
                    nav = car.navigation
                    if nav.reservezone and not nav.inzone:
                        zones.request_token(index + 1, nav.reservezone)
                    nav.granted_zone = zones.token_zone(index + 1)
                    if not admin:
                        zones.advance(index + 1, raw_buttons[config["buttons"][index][0]],
                                      1 / settings["control_hz"])
                if transfers:
                    # Admin pauses the robot at its next waypoint; a quit lets it finish and go home.
                    pause = admin and not transfers.finishing
                    if pause != robot_paused:
                        robot_commands.put(("pause", pause))
                        robot_paused = pause
                    zones.settle(acknowledged)
                    transfers.tick(time.perf_counter(), not pause)
                    if shutting_down and transfers.done:
                        quitting = True
                        log("QUIT_ROBOT_HOME", home=transfers.home)
            # Presence hides markers on every screen, without discarding their locations.
            visible_tags = tuple(tag if car.present else None
                                 for tag, car in zip(last_known_tags, cars))
            robot_status = (transfers.status() if transfers else
                            "Robot: " + ("disabled" if robot_phase == "disabled" else robot_text))
            if shutting_down:
                robot_status = "QUITTING: waiting for the robot to finish and reach home. " + robot_status
            startup_snapshot = board_startup.snapshot()
            startup_snapshot["robot"] = None if robot_phase in ("ready", "disabled") else robot_prompt()
            commands, states = [], []
            unavailable_zones = tuple(zone for zone in "ABCD" if not zones.available(zone))
            for index, car in enumerate(cars):
                raw = tuple(int(raw_buttons[button]) for button in config["buttons"][index])
                drive = raw
                nav = car.navigation
                # A stalled display must release its driving lock. Healthy displays
                # report every 100 ms even when their camera is disconnected.
                evacuating = zones.evacuating(index + 1)
                if (admin or quit_pending or capture_menu or shutting_down or args.no_video
                        or evacuating or tick_start - videos[index + 1]["at"] > 2):
                    coin_clips[index] = None
                if not admin and evacuating:
                    # As if the visitor pushed forward toward the safe exit; the normal
                    # filter and branch lock still apply afterwards.
                    left, right = nav.exit_input() if nav.reservezone and not nav.inzone else (False, False)
                    drive = (1, int(left), int(right))
                command = car.command(drive, tick_start, settings["telemetry_stale_s"], settings["echo_stale_s"],
                                      model["connected"], joystick is not None, admin, selected, index + 1,
                                      keyboard, args.stop_only or quitting or quit_pending or capture_menu
                                      or shutting_down or not board_startup.confirmed
                                      or coin_clips[index] is not None)
                commands.append(command)
                navigation_state = (nav.uid, nav.edge, nav.locked)
                if navigation_state != navigation_states[index]:
                    log("NAVIGATION", car=index + 1, uid=nav.uid, tid=nav.tid,
                        split=nav.split, mergeedge=nav.mergeedge, edge=nav.edge,
                        selected_exit=nav.selected_exit, locked=nav.locked,
                        zone_token=nav.granted_zone, evacuating=zones.evacuating(index + 1),
                        zone_safeexit=bool(nav.reservezone and nav.reservezone != nav.granted_zone))
                    navigation_states[index] = navigation_state
                packet = car.packet
                hud = build_display_snapshot(index + 1, car, raw, visible_tags,
                                             tag_attributes, tick_start, settings["telemetry_stale_s"],
                                             coin_game.totals[index], coin_game.hidden(index + 1, tick_start))
                hud["unavailable_zones"] = unavailable_zones
                states.append({"hud": hud,
                               "car": index + 1, "coin_clip": coin_clips[index],
                               "admin": admin, "stop_only": args.stop_only,
                               "present": car.present, "selected": selected, "raw": raw,
                               "manual": bool(admin and keyboard[4]), "quit_pending": quit_pending,
                               "capture_menu": capture_menu, "capture_car": capture_car,
                               "capture_ui": capture_ui, "capture_request": capture_request,
                               "capture_status": capture_status,
                               "board_startup": startup_snapshot, "shutting_down": shutting_down,
                               "global_status": f"Modem {config['port']}: {'connected' if model['connected'] else 'reconnecting'} | Gamepad: {'connected' if joystick else 'missing'} | {robot_status}",
                               "global_counts": f"World States: {generated_total} | Skipped ticks: {skipped} | Invalid lines: {model['invalid_lines']}",
                               "feedback": f"Fresh ({tick_start-car.received:.2f}s)" if car.feedback_ok(tick_start, settings["telemetry_stale_s"]) else "Missing / stale",
                               "echo": f"Fresh ({car.echo_age*1000:.0f} ms estimate)" if car.echo_ok(tick_start, settings["echo_stale_s"]) else "Waiting / stale",
                               "battery": f"{packet.battery_mv/1000:.2f} V" if packet else "--",
                               "reported": f"{CONTROLLERS.get(packet.controller, str(packet.controller))} / {FOLLOWERS.get(packet.follower, str(packet.follower))}" if packet else "No telemetry",
                               "uid": car.uid or "No tag yet", "tag_age": f"{packet.nfc_age_ms} ms" if packet else "--",
                               "sensors": f"Line: {packet.sensors:08b}" if packet else "",
                               "command": f"0x{command:02X}", "reason": car.last_reason, "warning": car.warning()})
            modem.submit(sequence, zones.mask(admin), commands)
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
            # Stop-only snapshots; observe reported wheels. Robot homing already
            # completed (or was explicitly abandoned) before the loop ended.
            stop_sent_generation = time.perf_counter()
            until = stop_sent_generation + settings["shutdown_stop_s"]
            while time.perf_counter() < until:
                modem.submit(sequence, zones.mask(admin), [AUTO_STOP] * 4)
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
        if robot_process is not None:
            robot_commands.put(("stop",))
            # Startup can still be finishing (15s IDLE + 60s RUNNING), followed
            # by the independent Dashboard power-off verification (60s).
            robot_process.join(timeout=150 if args.robot == "live" else 5)
            if robot_process.is_alive():
                log("ROBOT_PROCESS_TERMINATED", state=transfers.state if transfers else robot_phase)
                robot_process.terminate()
                outcome = "FAIL"
                print("Robot shutdown timed out; arm power-off is unconfirmed. Check the pendant.", flush=True)
            elif robot_process.exitcode not in (None, 0):
                outcome = "FAIL"
                print("Robot process failed; arm power-off may be unconfirmed. Check the pendant.", flush=True)
            drain_robot_events()
            robot_commands.cancel_join_thread()
            robot_events.cancel_join_thread()
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
        if board_startup:
            board_startup.close()
        if coin_store:
            try:
                coin_store.close()
            except Exception as error:
                log("COIN_SAVE_FAILED", error=str(error))
            for error, totals in coin_store.drain_errors():
                log("COIN_SAVE_FAILED", error=error, totals=totals, action="skipped")
        log("RESULT", outcome=outcome, generated=generated_total, skipped_ticks=skipped,
            profile=f"robot={args.robot}", stop_only=args.stop_only)
        log.close()
        print(f"{outcome}: {log.path}", flush=True)
    return int(outcome == "FAIL")
