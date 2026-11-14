"""Hardware-free checks of M1's wire compatibility, driving gates and stale I/O."""

from collections import deque
from dataclasses import replace
import struct
import threading
import time
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from contextlib import redirect_stdout, ExitStack
from pathlib import Path
import io
import json
import tempfile

from game_controller.config import load
from game_controller.modem import Modem
from game_controller.protocol import Telemetry, encode_world, movement
from game_controller.state import Car


def packet(sequence=1, echo=1, **values):
    sample = Telemetry(1, sequence, echo, 0, b"\x01\x02\x03\x04", 10, 24, 0, 0, 0, 0, 12000)
    return replace(sample, **values)


class ProtocolTests(unittest.TestCase):
    def test_published_vector_and_real_layout(self):
        self.assertEqual(encode_world(0x1234, 2, [1, 3, 5, 0]), b"W34120201030500\n")
        raw = struct.pack("<BHHB4sHBBBhhH", 1, 65535, 42, 3, b"abcd", 65535, 129, 12, 10, -50, 50, 11944)
        actual = Telemetry.decode(raw.hex().encode() + b"\r")
        self.assertEqual((actual.sensors, actual.controller, actual.follower), (129, 12, 10))
        self.assertEqual((actual.left_mm_s, actual.right_mm_s, actual.battery_mv), (-50, 50, 11944))
        for invalid in (b"boot message", raw.hex().encode()[:-1], b"x" * 42,
                        (b"\x00" + raw[1:]).hex().encode(), raw.hex().encode() + b" "):
            with self.assertRaises(ValueError):
                Telemetry.decode(invalid)

    def test_physical_conflicts_and_mode_bits(self):
        self.assertEqual(movement(), 1)
        self.assertEqual(movement(True, True, True), 3)
        self.assertEqual(movement(False, True, False, manual=True), 8)
        self.assertEqual(movement(False, False, True, manual=True, backward=True), 20)
        self.assertEqual(movement(True, True, False, manual=True, backward=True), 0)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.car = Car(present=True)
        self.car.accept(packet(), 10, 1, {}, .5)
        self.car.accept(packet(2, 2), 10.05, 1, {2: (1, 10.02, 10.025)}, .5)

    def command(self, now=10.1, **overrides):
        args = dict(raw=(1, 1, 0), now=now, telemetry_timeout=.5, echo_timeout=.5,
                    modem_ok=True, joystick_ok=True, admin=False,
                    selected=1, car_id=1, keyboard=(True, False, True, False, False))
        args.update(overrides)
        return self.car.command(**args)

    def test_echo_receipt_is_required_and_resets_need_new_snapshot(self):
        self.assertTrue(self.car.echo_ok(10.1, .5))
        self.car.accept(packet(0, 2), 11, 2, {2: (1, 10.02, 10.025)}, .5)
        self.assertEqual(self.command(now=11.01), 1)
        self.car.accept(packet(1, 3), 11.05, 2, {3: (2, 11.02, 11.025)}, .5)
        self.assertEqual(self.command(now=11.06), 3)

    def test_duplicates_and_old_telemetry_do_not_keep_driving_alive(self):
        self.assertEqual(self.car.accept(packet(2, 2), 10.3, 1, {}, .5), "duplicate")
        self.assertEqual(self.car.accept(packet(1, 2), 10.4, 1, {}, .5), "stale")
        self.assertEqual(self.command(now=10.6), 1)

    def test_wrap_and_distinct_uid(self):
        car = Car()
        car.accept(packet(65535), 1, 1, {}, .5)
        self.assertEqual(car.accept(packet(0), 1.05, 1, {}, .5), "accepted")
        self.assertEqual(car.accept(packet(1, uid=b"efgh"), 1.1, 1, {}, .5), "uid")

    def test_admin_and_held_input_recovery(self):
        self.assertEqual(self.command(), 3)
        self.assertEqual(self.command(admin=True, selected=2), 1)
        self.assertEqual(self.command(admin=True), 19)
        self.assertEqual(self.command(joystick_ok=False), 1)
        self.assertEqual(self.command(), 3)  # Same held forward input; ordinary road centers.
        self.assertEqual(self.command(admin=True, keyboard=(False, True, False, False, True)), 8)
        self.assertEqual(self.command(stop_only=True), 1)
        self.car.present = False
        self.assertEqual(self.command(), 3)

    def test_presence_does_not_gate_admin_auto_or_manual(self):
        for present in (True, False):
            self.car.present = present
            with self.subTest(present=present):
                self.assertEqual(self.command(admin=True), 19)
                self.assertEqual(self.command(admin=True, keyboard=(False, True, False, False, True)), 8)
                self.assertEqual(self.command(admin=True, now=11), 1)
                self.assertEqual(self.command(admin=True, stop_only=True), 1)
                self.assertEqual(self.command(admin=True, selected=2), 1)

    def test_car_recovery_state_suppresses_driving(self):
        self.car.packet = replace(self.car.packet, controller=13)
        self.assertEqual(self.command(), 1)
        self.car.packet = replace(self.car.packet, controller=0)
        self.assertEqual(self.command(), 3)

    def test_stopped_firmware_does_not_create_pc_rearm_deadlock(self):
        self.car.packet = replace(self.car.packet, controller=15, sensors=0)
        self.assertIn("possible", self.car.warning())
        self.assertEqual(self.command(raw=(1, 0, 0)), 3)
        self.assertEqual(self.command(admin=True, keyboard=(False, True, False, False, True)), 8)


class FakeSerial:
    def __init__(self):
        self.input = bytearray()
        self.writes = []
        self.lock = threading.Lock()
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.input)

    def read(self, count):
        with self.lock:
            value = bytes(self.input[:count])
            del self.input[:count]
            return value

    def write(self, line):
        with self.lock:
            self.writes.append(line)
        return len(line)

    def close(self):
        self.closed = True


class WorkerTests(unittest.TestCase):
    def test_runtime_failure_reconnects_only_to_configured_port(self):
        import serial as serial_library
        broken, replacement = FakeSerial(), FakeSerial()
        broken.read = lambda count: (_ for _ in ()).throw(serial_library.SerialException("unplugged"))
        with patch("game_controller.modem.open_modem", return_value=replacement) as opened:
            modem = Modem("COM_TEST", broken, .1, .01, lambda *a, **k: None)
            try:
                deadline = time.perf_counter() + .5
                while modem.session < 2 and time.perf_counter() < deadline:
                    time.sleep(.002)
                self.assertEqual(modem.session, 2)
                self.assertTrue(modem.connected)
                opened.assert_called_with("COM_TEST")
                self.assertTrue(broken.closed)
            finally:
                modem.close()

    def test_stalled_controller_does_not_replay_driving_and_split_rx_is_ordered(self):
        serial = FakeSerial()
        modem = Modem("fake", serial, .03, .1, lambda *a, **k: None)
        try:
            modem.submit(1, 0, [3, 1, 1, 1])
            deadline = time.monotonic() + .5
            while not serial.writes and time.monotonic() < deadline:
                time.sleep(.002)
            self.assertTrue(serial.writes)
            initial = len(serial.writes)
            time.sleep(.06)
            self.assertEqual(len(serial.writes), initial)  # Not repeatedly refreshed.
            first = struct.pack("<BHHB4sHBBBhhH", 1, 1, 1, 0, b"abcd", 0, 1, 0, 0, 0, 0, 12000).hex().encode()
            second = struct.pack("<BHHB4sHBBBhhH", 1, 2, 1, 0, b"efgh", 0, 1, 0, 0, 0, 0, 12000).hex().encode()
            with serial.lock:
                serial.input.extend(b"debug\n" + first[:15])
            time.sleep(.01)
            with serial.lock:
                serial.input.extend(first[15:] + b"\n" + second + b"\n")
            received = []
            deadline = time.monotonic() + .5
            while len(received) < 2 and time.monotonic() < deadline:
                batch, _, _ = modem.drain()
                received.extend(batch)
                time.sleep(.002)
            self.assertEqual([p.uid for _, _, p in received], [b"abcd", b"efgh"])
            self.assertEqual(modem.invalid, 1)
        finally:
            modem.close()
        self.assertTrue(serial.closed)


class ConfigTests(unittest.TestCase):
    def test_configured_uppercase_letter_key_is_resolved(self):
        from game_controller.app import bindings_from_config
        import pygame
        self.assertEqual(bindings_from_config({"toggle": "K_SPACE", "cancel": "K_ESCAPE", "quit": "K_Q"})["quit"], pygame.K_q)

    def test_current_config_and_single_car_profile(self):
        config = load()
        self.assertEqual(config["present"], {1, 2, 3, 4})
        self.assertEqual(config["settings"]["control_hz"], 50)
        self.assertEqual(len({serial.casefold() for _, _, serial in config["cameras"]}), 4)
        self.assertTrue(all(serial for _, _, serial in config["cameras"]))
        self.assertEqual(config["bindings"]["toggle"], "K_SPACE")


class StartupTests(unittest.TestCase):
    def run_failure(self, gamepad, modem_error=None):
        from game_controller import app
        config = load()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            config["log_directory"] = Path(directory)
            store_class = app.CoinStore
            stack.enter_context(patch.object(app, "CoinStore", side_effect=lambda path:
                                             store_class(Path(directory) / "coin_count.ini")))
            stack.enter_context(patch.object(app, "load", return_value=config))
            stack.enter_context(patch("sys.argv", ["m1", "--stop-only", "--no-video"]))
            for method in ("display.init", "font.init", "joystick.init", "quit"):
                stack.enter_context(patch("game_controller.app.pygame." + method))
            stack.enter_context(patch.object(app.pygame.joystick, "get_count", return_value=0))
            stack.enter_context(patch.object(app, "connect_gamepad", return_value=gamepad))
            opened = stack.enter_context(patch.object(app, "open_modem", side_effect=modem_error))
            with redirect_stdout(io.StringIO()):
                result = app.main()
            rows = [json.loads(s) for p in Path(directory).glob("*.log") for s in p.read_text().splitlines()]
            self.assertEqual(result, 1)
            self.assertEqual(rows[-1]["outcome"], "FAIL")
            return rows, opened.called

    def test_missing_gamepad_quits_without_opening_modem(self):
        rows, opened = self.run_failure(None)
        self.assertFalse(opened)
        failure = next(r for r in rows if r["event"] == "FAILURE")
        self.assertIn("Gamepad index 0", failure["error"])

    def test_missing_modem_quits_with_configured_port_diagnostic(self):
        gamepad = Mock()
        gamepad.get_name.return_value = "Test gamepad"
        gamepad.get_numbuttons.return_value = 12
        gamepad.get_instance_id.return_value = 1
        rows, opened = self.run_failure(gamepad, RuntimeError("Cannot open configured modem COM11. Available: COM99"))
        self.assertTrue(opened)
        self.assertIn("Available: COM99", next(r for r in rows if r["event"] == "FAILURE")["error"])


class OverlayTests(unittest.TestCase):
    def test_selected_car_color_and_hidden_controls(self):
        from game_controller.display import panel_action, panel_theme
        paused = {"admin": True, "car": 2, "selected": 1}
        selected = {**paused, "car": 1}
        self.assertEqual(panel_theme(paused)[0], "Paused by Admin")
        self.assertEqual(panel_theme(selected)[0], "Controlling by Admin")
        self.assertIn("Direct Control", panel_theme({**selected, "manual": True})[0])
        self.assertEqual(panel_action((25, 310), selected), "presence")
        self.assertIsNone(panel_action((290, 310), selected))
        self.assertIsNone(panel_action((25, 310), {**selected, "admin": False}))
        self.assertIsNone(panel_action((25, 310), {**selected, "quit_pending": True}))

    def test_normal_mode_removes_admin_panel_pixels(self):
        import pygame
        from game_controller.display import draw_panel
        pygame.font.init()
        surface = pygame.Surface((1920, 365), pygame.SRCALPHA)
        font = pygame.font.Font(None, 26)
        camera = type("Camera", (), {"last_frame": 0})()
        draw_panel(surface, {"car": 1, "admin": True}, camera, 1, font, font)
        self.assertGreater(surface.get_at((1000, 350)).a, 0)
        draw_panel(surface, {"car": 1, "admin": False}, camera, 1, font, font)
        self.assertEqual(surface.get_at((1000, 350)).a, 0)
        draw_panel(surface, {"car": 1, "quit_pending": True}, camera, 1, font, font)
        self.assertGreater(surface.get_at((1000, 350)).a, 0)


if __name__ == "__main__":
    unittest.main()
