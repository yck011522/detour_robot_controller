"""Queue coverage and controlled-stop acceptance tests; no robot/tool connections."""

from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import queue_cli
from movement_settings import DEFAULT_CONFIG, load_settings
from transfer_queue import ALL_TRANSFERS, TransferQueue, parse_order


def model(home="bd_home", first=None):
    order, _ = queue_cli.read_queue_settings(DEFAULT_CONFIG)
    if first:
        order.remove(first)
        order.insert(0, first)
    return TransferQueue(dict(a="P1", b="P2", c="P3", d=None), order, home)


class QueueTest(unittest.TestCase):
    def test_home_recognition_uses_max_per_joint_and_rejects_ambiguity(self):
        poses = {"ac_home": [0.]*6, "bd_home": [2.]*6}
        home, _ = queue_cli.recognize_home([math.radians(5)]*6, poses, 5)
        self.assertEqual(home, "ac_home")
        with self.assertRaises(ValueError):
            queue_cli.recognize_home([0.]*5+[math.radians(5.01)], poses, 5)
        with self.assertRaises(ValueError):
            queue_cli.recognize_home([0.]*6, {"ac_home": [0.]*6, "bd_home": [0.]*6}, 5)
        with self.assertRaises(ValueError):
            queue_cli.recognize_home([float("nan")]*6, poses, 5)

    def test_questionnaire_rejects_duplicate_locations_and_supports_held_plate(self):
        order, _ = queue_cli.read_queue_settings(DEFAULT_CONFIG)
        with patch("builtins.print"), patch("builtins.input", side_effect=["a", "a", "b", "c"]):
            q = queue_cli.ask_plate_layout("ac_home", order)
        self.assertEqual([z for z,p in q.zones.items() if p], list("abc"))
        with patch("builtins.print"), patch("builtins.input", side_effect=["a", "b", "g", "c"]):
            q = queue_cli.ask_plate_layout("bd_home", order)
        self.assertEqual(q.held, "plate_3")
        self.assertEqual(q.pending, ("c", "d"))
        self.assertIsNone(q.zones["c"])
        with patch("builtins.print"), patch("builtins.input", return_value="q"):
            self.assertIsNone(queue_cli.ask_plate_layout("bd_home", order))

    def test_pause_toggle_cancels_pending_flag(self):
        with patch("builtins.print"):
            controls = queue_cli.Controls(Path("unused"), .01)
            controls.handle_key("P")
            self.assertTrue(controls.paused.is_set())
            controls.handle_key("p")
            self.assertFalse(controls.paused.is_set())
            controls.handle_key("q")
            controls.handle_key("p")
            self.assertTrue(controls.requested.is_set())
            self.assertFalse(controls.paused.is_set())

    def test_pause_finishes_active_step_and_quit_overrides_pause_to_reach_home(self):
        for next_key in ("p", "q"):
            with self.subTest(next_key=next_key), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)/"session"
                controls = queue_cli.Controls(directory/"STOP", .01)
                reached_pause = threading.Event()
                started = []
                outcome = []

                def fake_steps(steps, config, settings, transcript, on_step, isolate_signals):
                    for index, (label, arguments) in enumerate(steps):
                        on_step("started", index, label, arguments, None)
                        started.append(arguments)
                        if len(started) == 3:
                            # Simulates P during active tightening, before its completion.
                            controls.handle_key("p")
                        on_step("finished", index, label, arguments, 0)
                    return 0

                with patch("builtins.print"), patch.object(queue_cli, "check_start"), \
                     patch.object(queue_cli.time, "sleep"), patch.object(queue_cli, "run_steps", side_effect=fake_steps):
                    session = queue_cli.Session(model(), load_settings(), DEFAULT_CONFIG, directory)
                    record = session.record

                    def record_and_signal(event, **data):
                        record(event, **data)
                        if event == "PAUSED":
                            reached_pause.set()

                    session.record = record_and_signal
                    worker = threading.Thread(target=lambda: outcome.append(session.run(controls, max_transfers=1)))
                    worker.start()
                    try:
                        self.assertTrue(reached_pause.wait(3), "Did not reach sub-step pause")
                        self.assertEqual(len(started), 3, "Release started despite pause")
                        saved = json.loads((directory/"state.json").read_text())
                        self.assertEqual(saved["status"], "paused")
                        self.assertIsNone(saved["model"]["held"])
                        controls.handle_key(next_key)
                        worker.join(3)
                        self.assertFalse(worker.is_alive())
                        self.assertEqual(outcome, [0])
                    finally:
                        controls.request("test cleanup")
                        worker.join(3)
                saved = json.loads((directory/"state.json").read_text())
                self.assertTrue(saved["home_verified"])
                self.assertEqual(saved["status"], "stopped_home")
                if next_key == "q":
                    self.assertEqual(len(started), 6)
                    self.assertIsNotNone(saved["model"]["held"])
                else:
                    self.assertEqual(len(started), 12)
                    self.assertIsNone(saved["model"]["held"])

    def test_all_layouts_pick_only_occupied_into_empty_and_do_not_reorder_on_selection(self):
        for empty in "abcd":
            q = model()
            q.zones = {z: None if z == empty else "P"+z for z in "abcd"}
            original = q.queue.copy()
            pair, skipped = q.choose()
            self.assertEqual(pair[1], empty)
            self.assertTrue(q.zones[pair[0]])
            self.assertEqual(q.queue, original)
            self.assertEqual(sum(q.counts.values()), 0)

    def test_inventory_and_complete_only_queue_rotation_over_200_transfers(self):
        q = model()
        for _ in range(200):
            before = q.queue.copy()
            pair, _ = q.choose()
            q.pickup_completed()
            self.assertEqual(sum(v is None for v in q.zones.values()), 2)
            q.validate_inventory()
            self.assertEqual(q.queue, before)
            q.placement_completed()
            self.assertEqual(q.queue, before)
            q.finish_transfer()
            self.assertEqual(q.queue, [p for p in before if p != pair] + [pair])
            self.assertEqual(sum(v is None for v in q.zones.values()), 1)
            q.validate_inventory()
        self.assertEqual(sum(q.counts.values()), 200)
        self.assertEqual({tuple(k.split(">")) for k,v in q.counts.items() if v}, ALL_TRANSFERS)

    def test_invalid_orders_and_inventory(self):
        for order in ("A>B", "A>A", "B>D,"*12):
            with self.assertRaises(ValueError):
                parse_order(order)
        q = model()
        q.zones["c"] = "P1"
        with self.assertRaises(ValueError):
            TransferQueue(**asdict(q))

    def test_resume_rejects_inconsistent_pending_pickup(self):
        q = model()
        q.choose()
        q.pickup_completed()
        q.placement_completed()
        # A completed placement must be committed, not resumed as a source pickup.
        with self.assertRaises(ValueError):
            TransferQueue(**asdict(q))
        q.finish_transfer()
        TransferQueue(**asdict(q))

    def test_transfer_order_allows_spaces_around_arrow(self):
        order, _ = queue_cli.read_queue_settings(DEFAULT_CONFIG)
        text = ", ".join(f"{a.upper()} > {b.upper()}" for a,b in order)
        self.assertEqual(parse_order(text), order)

    def test_stop_held_empty_and_during_loaded_transfer(self):
        for stop_at in ("pick", "place", "loaded_transfer"):
            with self.subTest(stop_at=stop_at), tempfile.TemporaryDirectory() as folder:
                q = model("ac_home", ("a", "d")) if stop_at == "loaded_transfer" else model()
                directory = Path(folder)/"session"
                controls = queue_cli.Controls(directory/"STOP", .1)
                calls = []

                def fake_steps(steps, config, settings, transcript, on_step, isolate_signals):
                    calls.append(transcript.name)
                    for index, (label, arguments) in enumerate(steps):
                        on_step("started", index, label, arguments, None)
                        if (stop_at == "pick" and arguments[:3] == ["tighten", "--motor", "1"] or
                            stop_at == "place" and arguments[:3] == ["withdraw", "--motor", "1"] or
                            stop_at == "loaded_transfer" and "cross_home" in arguments):
                            controls.request("offline test")
                        on_step("finished", index, label, arguments, 0)
                    return 0

                with patch("builtins.print"), patch.object(queue_cli, "check_start"), \
                     patch.object(queue_cli.time, "sleep"), patch.object(queue_cli, "run_steps", side_effect=fake_steps):
                    session = queue_cli.Session(q, load_settings(), DEFAULT_CONFIG, directory)
                    self.assertEqual(session.run(controls), 0)
                record = json.loads((directory/"state.json").read_text())
                self.assertEqual(record["status"], "stopped_home")
                self.assertTrue(record["home_verified"])
                restored = queue_cli.restored_model(directory/"state.json")
                if stop_at == "place":
                    self.assertIsNone(restored.held)
                    self.assertIsNone(restored.pending)
                    self.assertEqual(sum(restored.counts.values()), 1)
                    self.assertEqual(len(calls), 2)
                else:
                    self.assertIsNotNone(restored.held)
                    self.assertIsNotNone(restored.pending)
                    self.assertEqual(sum(restored.counts.values()), 0)
                    self.assertEqual(len(calls), 2 if stop_at == "loaded_transfer" else 1)

    def test_fault_blocks_following_operations_and_cannot_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)/"session"
            controls = queue_cli.Controls(directory/"STOP", .1)
            q = model()
            with patch("builtins.print"), patch.object(queue_cli, "check_start"), \
                 patch.object(queue_cli, "run_steps", return_value=1) as steps:
                session = queue_cli.Session(q, load_settings(), DEFAULT_CONFIG, directory)
                self.assertEqual(session.run(controls), 1)
                self.assertEqual(steps.call_count, 1)
            with self.assertRaises(ValueError):
                queue_cli.restored_model(directory/"state.json")
            self.assertEqual(sum(q.counts.values()), 0)

    def test_preview_does_not_open_hardware(self):
        with patch("builtins.print"), patch.object(queue_cli, "check_start") as check, \
             patch.object(queue_cli, "prepare_robot") as prepare:
            self.assertEqual(queue_cli.main(["--preview"]), 0)
            check.assert_not_called()
            prepare.assert_not_called()

    def test_live_default_prepares_robot_before_detecting_home(self):
        calls = []
        with patch("builtins.print"), \
             patch.object(queue_cli, "prepare_robot", side_effect=lambda: calls.append("prepare")), \
             patch.object(queue_cli, "engage_brakes", side_effect=lambda: calls.append("brakes")), \
             patch.object(queue_cli, "detect_home", side_effect=lambda *a: calls.append("detect") or "bd_home"), \
             patch.object(queue_cli, "ask_plate_layout", return_value=None):
            self.assertEqual(queue_cli.main([]), 1)
        self.assertEqual(calls, ["prepare", "detect", "brakes"])

    def test_brakes_engaged_after_fault_or_interrupted_startup(self):
        for startup_fault in (False, True):
            with patch.object(queue_cli, "prepare_robot", side_effect=KeyboardInterrupt if startup_fault else None), \
                 patch.object(queue_cli, "engage_brakes") as brakes:
                with self.assertRaises(KeyboardInterrupt):
                    with queue_cli.robot_power():
                        raise KeyboardInterrupt()
                brakes.assert_called_once_with()

    def test_brake_cleanup_powers_off_arm_without_controller_shutdown(self):
        import step_test
        from unittest.mock import Mock
        protocol = step_test.tool_protocol
        client = Mock()
        with patch("builtins.print"), patch.object(protocol, "Log"), \
             patch.object(protocol, "dashboard", return_value=client), \
             patch.object(protocol, "wait_mode") as wait:
            queue_cli.engage_brakes()
        self.assertEqual([call.args[0] for call in client.command.call_args_list], ["stop", "power off"])
        wait.assert_called_once_with(client, "POWER_OFF", 30)
        client.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
