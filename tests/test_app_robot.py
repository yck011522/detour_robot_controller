"""The main loop with a synthetic robot process (threads) and synthetic car context."""

from contextlib import ExitStack, redirect_stdout
import functools
import io
import itertools
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from game_controller import app
from game_controller.boards import BoardStartup, load_board_state
from game_controller.robot import worker
from game_controller.state import Car

LAYOUT = {"P": "C", "Q": "A", "R": "B", "S": "spare"}


class FakeQueue(queue.Queue):
    def cancel_join_thread(self):
        pass

    def close(self):
        pass


class FakeProcess:
    def __init__(self, target, args=(), name="", daemon=False):
        self.thread = threading.Thread(target=target, args=args, daemon=True)
        self.exitcode, self.pid = None, id(self)

    def start(self):
        self.thread.start()

    def is_alive(self):
        return self.thread.is_alive()

    def join(self, timeout=None):
        self.thread.join(timeout)

    def terminate(self):
        pass


class FakeContext:
    Queue, Event, Process = FakeQueue, threading.Event, FakeProcess


def key(code):
    return Mock(type=app.pygame.KEYDOWN, key=code, repeat=False)


class Pressed:
    def __init__(self, *codes):
        self.codes = set(codes)

    def __getitem__(self, code):
        return code in self.codes


def run_app(directory, argv, frames, make_car=lambda car: car, command=None, held=None, patches=()):
    """Run main() headless. frames(n, startup) returns the pygame events for tick n;
    held(n) returns the key codes held down at tick n."""
    config = app.load()
    config["log_directory"] = Path(directory)
    config["settings"]["shutdown_stop_s"] = .001
    robot_mode = argv[argv.index("--robot") + 1] if "--robot" in argv else "live"
    startup = BoardStartup(Path(directory) / "board_state.ini", robot=robot_mode != "off")
    startup.draft = dict(LAYOUT)
    counter = [0]
    topologies, modem = [], Mock()

    def events():
        counter[0] += 1
        return frames(counter[0], startup)

    modem.drain.return_value = ([], [], {"session": 0, "connected": True, "invalid_lines": 0})
    modem.submit.side_effect = lambda sequence, topology, commands: topologies.append((topology, tuple(commands)))
    joystick = Mock()
    joystick.get_button.return_value = False
    joystick.get_numbuttons.return_value = 12
    joystick.get_name.return_value = "Synthetic gamepad"
    joystick.get_instance_id.return_value = 1
    real_run = worker.run
    store_class = app.CoinStore
    with ExitStack() as stack:
        stack.enter_context(patch.object(app, "load", return_value=config))
        stack.enter_context(patch.object(app, "BoardStartup", side_effect=lambda path, robot=True: startup))
        stack.enter_context(patch.object(app, "CoinStore", side_effect=lambda path:
                                         store_class(Path(directory) / "coin_count.ini")))
        stack.enter_context(patch.object(app, "ask_boards_console"))
        stack.enter_context(patch("sys.argv", ["controller", "--no-video", *argv]))
        for method in ("display.init", "font.init", "joystick.init", "display.set_mode", "quit"):
            stack.enter_context(patch("game_controller.app.pygame." + method))
        stack.enter_context(patch.object(app.pygame.event, "get", side_effect=events))
        stack.enter_context(patch.object(app.pygame.key, "get_focused", return_value=held is not None))
        if held is not None:
            stack.enter_context(patch.object(app.pygame.key, "get_pressed",
                                             side_effect=lambda: Pressed(*held(counter[0]))))
        stack.enter_context(patch.object(app, "connect_gamepad", return_value=joystick))
        stack.enter_context(patch.object(app, "open_modem", return_value=Mock()))
        stack.enter_context(patch.object(app, "Modem", return_value=modem))
        stack.enter_context(patch.object(app.multiprocessing, "get_context", return_value=FakeContext()))
        stack.enter_context(patch.object(app.robot_worker, "run", functools.partial(real_run, dry_step_seconds=0)))
        stack.enter_context(patch.object(worker.signal, "signal"))  # Only valid in a main thread.
        stack.enter_context(patch.object(app, "Car", side_effect=lambda present: make_car(Car(present=present))))
        if command is not None:
            stack.enter_context(patch.object(Car, "command", new=command))
        for item in patches:
            stack.enter_context(item)
        with redirect_stdout(io.StringIO()):
            app.main()
    return startup, topologies


def logged(directory, name):
    records = []
    for path in Path(directory).glob("*.log"):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record["event"] == name:
                records.append(record)
    return records


def script(events, quit_at=100):
    """Key presses by frame number; every test ends itself with Q then Enter."""
    scheduled = {**events, quit_at: app.pygame.K_q, quit_at + 2: app.pygame.K_RETURN}
    return lambda frame, startup: [key(scheduled[frame])] if frame in scheduled else []


def confirm_then_leave_admin(quit_at=100):
    return script({2: app.pygame.K_RETURN, 4: app.pygame.K_SPACE}, quit_at)


class RobotStartupAndQuitTests(unittest.TestCase):
    def test_unavailable_robot_can_be_skipped_and_the_game_runs_with_static_boards(self):
        K = app.pygame
        with tempfile.TemporaryDirectory() as directory:
            startup, topologies = run_app(
                directory, ["--robot", "dry-run", "--seconds", "30"],
                script({5: K.K_RETURN, 8: K.K_RETURN, 12: K.K_SPACE}, 40),
                patches=[patch.object(worker.DryRobot, "detect", side_effect=RuntimeError("no robot"))])
            self.assertFalse(startup.robot)
            self.assertEqual(load_board_state(startup.path), LAYOUT)
            self.assertEqual({topology & 15 for topology, _ in topologies} - {0}, {0b0111})
            self.assertEqual(len(logged(directory, "ROBOT_DISABLED")), 1)
            self.assertEqual(logged(directory, "ROBOT_OPERATION"), [])

    def test_retry_finds_the_robot_and_then_it_runs(self):
        K = app.pygame
        results = itertools.chain([RuntimeError("no robot")], itertools.repeat(("bd_home", "Dry run at bd_home")))
        with tempfile.TemporaryDirectory() as directory:
            startup, _ = run_app(
                directory, ["--robot", "dry-run", "--seconds", "30"],
                script({5: K.K_r, 15: K.K_RETURN, 30: K.K_RETURN, 40: K.K_SPACE}, 120),
                patches=[patch.object(worker.DryRobot, "detect", side_effect=results)])
            self.assertTrue(startup.robot)
            self.assertNotEqual(load_board_state(startup.path), LAYOUT)
            self.assertEqual([r["home"] for r in logged(directory, "ROBOT_DETECTED")], ["bd_home"])
            self.assertEqual(len(logged(directory, "ROBOT_UNAVAILABLE")), 1)

    def test_holding_a_key_jogs_to_a_home_and_release_rechecks_the_position(self):
        K = app.pygame
        away = functools.partial(worker.DryRobot, start_home=None)
        with tempfile.TemporaryDirectory() as directory:
            startup, _ = run_app(
                directory, ["--robot", "dry-run", "--seconds", "30"],
                script({80: K.K_RETURN, 90: K.K_RETURN, 100: K.K_SPACE}, 170),
                held=lambda frame: [K.K_LEFT] if 5 <= frame <= 15 else [],
                patches=[patch.object(worker, "DryRobot", away)])
            self.assertTrue(startup.robot)
            self.assertEqual([r["home"] for r in logged(directory, "ROBOT_DETECTED")], [None, "ac_home"])
            self.assertTrue(logged(directory, "ROBOT_OPERATION"))

    def test_quit_lets_the_robot_finish_the_transfer_and_exits_empty_handed(self):
        with tempfile.TemporaryDirectory() as directory:
            startup, _ = run_app(directory, ["--robot", "dry-run", "--seconds", "30"],
                                 confirm_then_leave_admin(60))
            self.assertNotIn("gripper", load_board_state(startup.path).values())
            self.assertEqual(len(logged(directory, "QUIT_ROBOT_HOME")), 1)
            self.assertEqual(logged(directory, "QUIT_CONFIRMED"), [])  # Waited for the robot instead.


class RobotLoopTests(unittest.TestCase):
    def test_default_starts_live_robot_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("game_controller.robot.live.LiveRobot", return_value=worker.DryRobot(0)) as live:
                startup, _ = run_app(directory, ["--seconds", "30"], confirm_then_leave_admin())
            live.assert_called_once_with()
            self.assertTrue(startup.robot)
            self.assertTrue(logged(directory, "ROBOT_OPERATION"))
            self.assertEqual(logged(directory, "ROBOT_STARTED")[0]["mode"], "live")

    def test_dry_run_robot_moves_boards_closes_zones_and_persists_the_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            startup, topologies = run_app(directory, ["--robot", "dry-run", "--seconds", "30"],
                                          confirm_then_leave_admin())
            masks = {topology & 15 for topology, _ in topologies}
            self.assertIn(0b0111, masks)  # Everything available before the robot starts.
            self.assertTrue(masks - {0, 0b0111}, "the robot never closed a zone")
            saved = load_board_state(startup.path)
            self.assertNotEqual(saved, LAYOUT)
            self.assertEqual(saved["S"], "spare")
            self.assertLessEqual(list(saved.values()).count("gripper"), 1)

    def test_robot_pauses_in_admin_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            # Never leaves admin mode, so the robot never starts.
            startup, topologies = run_app(directory, ["--robot", "dry-run", "--seconds", "30"],
                                          script({2: app.pygame.K_RETURN}, 60))
            self.assertEqual(load_board_state(startup.path), LAYOUT)
            self.assertEqual({topology & 15 for topology, _ in topologies} - {0}, {0b0111})
            self.assertTrue(all(topology & 16 for topology, _ in topologies))
            self.assertEqual(logged(directory, "ROBOT_OPERATION"), [])

    def test_robot_off_leaves_boards_alone_and_allows_four_boards(self):
        with tempfile.TemporaryDirectory() as directory:
            full = {"P": "A", "Q": "B", "R": "C", "S": "D"}
            keys_ = confirm_then_leave_admin(40)

            def frames(frame, startup):
                if frame == 1:
                    startup.draft = dict(full)
                return keys_(frame, startup)

            startup, topologies = run_app(directory, ["--robot", "off", "--seconds", "30"], frames)
            self.assertEqual(load_board_state(startup.path), full)
            self.assertEqual({topology & 15 for topology, _ in topologies} - {0}, {0b1111})

    def test_evacuation_pushes_forward_toward_the_safe_exit_in_player_mode_only(self):
        seen = []

        def car_at_approach(car):
            car.navigation.observe("approach", 0, 0, {"tid": "T1", "split": "left", "safeexit": "left",
                                                      "reservezone": "A"})
            return car

        created = []

        def make_car(car):
            created.append(car)
            return car_at_approach(car) if len(created) == 1 else car

        def command(car, raw, *args):
            seen.append((raw, args[5], car.navigation.granted_zone))  # raw, admin, token
            return 1

        with tempfile.TemporaryDirectory() as directory, patch("game_controller.zones.INACTIVITY_SECONDS", 0.0):
            run_app(directory, ["--robot", "off", "--seconds", "30"], confirm_then_leave_admin(40),
                    make_car=make_car, command=command)
        player = [entry for entry in seen if not entry[1] and entry[2] == "A"]
        self.assertTrue(player)
        self.assertEqual({entry[0] for entry in player[-3:]}, {(1, 1, 0)})
        self.assertTrue(all(entry[0] == (0, 0, 0) for entry in seen if entry[1] and entry[2] == "A"))


if __name__ == "__main__":
    unittest.main()
