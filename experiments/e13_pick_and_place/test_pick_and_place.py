"""Offline tests: fake feedback and transport; never import live RTDE clients."""
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import patch

import pick_and_place as e13


class FakeHardware:
    def __init__(self):
        self.calls = []
        self.pose = [0,0,.12,0,0,0]
        self.sample = dict(states=["IDLE", "IDLE"], pwm_percent=0, current_ma=0)
        self.poses = dict(TARGET=[0,0,.10,0,0,0], LOW=[0,0,.12,0,0,0], HOME=[0,0,.12,0,0,0])
        self.joints = dict(HOME=[0]*6)
        self.force_active = False
        outer = self
        class Control:
            def forceMode(self, *args): outer.calls.append(("force", args)); return True
            def moveL(self, *args): outer.calls.append(("moveL", args)); return True
        class Receiver:
            def getActualTCPPose(self): return outer.pose[:]
            def getActualTCPForce(self): return [0]*6
        class Tool:
            def command(self, *args): outer.calls.append(("command", args)); return "OK"
        class Log:
            def write(self, *args, **kwargs): outer.calls.append(("log", args, kwargs))
        self.control, self.receiver, self.tool, self.log = Control(), Receiver(), Tool(), Log()

    def payload(self, name): self.calls.append(("payload", name))
    def stop_tool(self): self.calls.append(("STOP",))


class SequenceTests(unittest.TestCase):
    def test_fault_retains_step_and_allows_retry_then_operator_skip(self):
        import threading
        commands, events = queue.Queue(), queue.Queue()
        real_log = e13.EventLog
        class FaultOnceLog(real_log):
            failed = False
            def write(self, event, **data):
                if event == "ACTION_SELECTED" and not self.failed:
                    self.failed = True
                    raise RuntimeError("Injected action fault")
                super().write(event, **data)
        with tempfile.TemporaryDirectory() as directory, patch.object(e13, "HERE", Path(directory)), \
                patch.object(e13, "EventLog", FaultOnceLog):
            thread = threading.Thread(target=e13.worker, args=(commands,events,False))
            thread.start()
            messages = []
            def pulse(direction, seconds=.15):
                end = time.monotonic()+seconds
                while time.monotonic()<end:
                    commands.put(("intent",direction,True,time.monotonic()))
                    while not events.empty(): messages.append(events.get())
                    time.sleep(.01)
            try:
                commands.put(("connect",))
                pulse(0)
                pulse(1)
                self.assertTrue(any(m["color"] == "fault" for m in messages))
                self.assertTrue(thread.is_alive())
                commands.put(("retry",))
                pulse(0)
                self.assertTrue(any(m["message"].startswith("Recovered") for m in messages))
                commands.put(("skip",))
                pulse(0)
                self.assertEqual(messages[-1]["state"],0)
                pulse(1,1.3)
                self.assertTrue(any(m["state"] == 1 and m["color"] == "complete" for m in messages))
            finally:
                commands.put(("quit",))
                thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_worker_keeps_watchdog_alive_while_waiting_in_fault(self):
        import threading
        commands, events = queue.Queue(), queue.Queue()
        kicks = []
        class Hardware:
            at_home = True
            def __init__(self, *args): pass
            def connect(self): pass
            def check(self): raise RuntimeError("Injected bound fault")
            def halt(self): pass
            def close(self): pass
            def maintain_watchdog(self): kicks.append(time.monotonic())
        with tempfile.TemporaryDirectory() as directory, patch.object(e13, "HERE", Path(directory)), \
                patch.object(e13, "Hardware", Hardware):
            thread = threading.Thread(target=e13.worker, args=(commands, events, True))
            thread.start()
            try:
                commands.put(("connect",))
                deadline = time.monotonic() + 2
                fault_seen = False
                while time.monotonic() < deadline:
                    try:
                        message = events.get(timeout=.1)
                        if message["color"] == "fault":
                            fault_seen = True
                            break
                    except queue.Empty:
                        pass
                self.assertTrue(fault_seen)
                time.sleep(.45)  # Longer than the 3 Hz watchdog interval.
                self.assertGreater(len(kicks), 10)
                self.assertTrue(thread.is_alive())
            finally:
                commands.put(("quit",))
                thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_forward_cycle_and_reverse_endpoint_mappings(self):
        state, seen = 0, []
        while state in e13.FORWARD:
            action = e13.FORWARD[state]
            seen.append(action)
            state = action.destination
        self.assertEqual(state, 12)
        self.assertEqual(len(seen), 12)
        self.assertEqual(e13.FORWARD[2].payload_after, "tool_only")
        self.assertEqual(e13.FORWARD[8].payload_after, "tool_with_floor")
        self.assertEqual(e13.REVERSE[9].destination, 8)
        self.assertEqual((e13.REVERSE[9].kind, e13.REVERSE[9].motor), ("UP", 1))
        for state in range(1,13):
            self.assertLess(e13.REVERSE[state].destination, state)

    def test_pickup_combines_descent_and_tightening(self):
        hw = FakeHardware()
        operation = e13.Operation(e13.FORWARD[8], hw)
        hw.sample["states"] = ["TIGHTENING", "IDLE"]
        with patch.object(e13.e10, "status", side_effect=lambda _: hw.sample):
            operation.start()
            self.assertIn(("command", ("TIGHTEN M1", "OK TIGHTEN M1")), hw.calls)
            self.assertFalse(operation.tick())
            hw.sample["states"] = ["STALLED", "IDLE"]
            operation.last_poll = 0
            self.assertFalse(operation.tick())
            hw.pose[2] = .10
            self.assertTrue(operation.tick())
        self.assertEqual(operation.action.payload_after, "tool_with_floor")

    def test_fault_watchdog_keeps_connected_script_alive(self):
        from types import SimpleNamespace
        hw = e13.Hardware.__new__(e13.Hardware)
        kicks = []
        hw.control = SimpleNamespace(isProgramRunning=lambda: True,
                                     kickWatchdog=lambda: kicks.append(1) or True)
        hw.maintain_watchdog()
        self.assertEqual(kicks, [1])
        hw.control.isProgramRunning = lambda: False
        hw.maintain_watchdog()
        self.assertEqual(kicks, [1])

    def test_reaching_z_first_holds_z_until_fresh_stall(self):
        hw = FakeHardware()
        operation = e13.Operation(e13.FORWARD[2], hw)
        with patch.object(e13.e10, "status", side_effect=lambda _: hw.sample):
            hw.sample["states"] = ["IDLE", "TIGHTENING"]
            operation.start()
            hw.pose[2] = .10
            self.assertFalse(operation.tick())
            force = next(call for call in hw.calls if call[0] == "force")
            self.assertEqual(force[1][1], [1,1,0,0,0,0])
            self.assertTrue(any(call[0] == "moveL" for call in hw.calls))
            hw.sample["states"] = ["IDLE", "STALLED"]
            operation.last_poll = 0
            self.assertTrue(operation.tick())
            self.assertTrue(operation.stalled)
            self.assertIn(("STOP",), hw.calls)

    def test_stall_first_latches_across_stop_and_resume(self):
        hw = FakeHardware()
        operation = e13.Operation(e13.FORWARD[2], hw)
        with patch.object(e13.e10, "status", return_value=dict(states=["IDLE", "STALLED"], pwm_percent=0)):
            operation.start()
            self.assertFalse(operation.tick())
            commands = sum(call[0] == "command" for call in hw.calls)
            operation.start()
            self.assertEqual(sum(call[0] == "command" for call in hw.calls), commands)
            hw.pose[2] = .10
            self.assertTrue(operation.tick())

    def test_withdrawal_height_completes_without_separate_release_signal(self):
        hw = FakeHardware()
        hw.pose[2] = .10
        operation = e13.Operation(e13.FORWARD[3], hw)
        with patch.object(e13.e10, "status", return_value=dict(states=["LOOSENING", "IDLE"], pwm_percent=50)):
            operation.start()
            self.assertEqual(hw.calls[0], ("payload", "tool_only"))
            self.assertFalse(operation.tick())
            force = next(call for call in hw.calls if call[0] == "force")
            self.assertEqual(force[1][2][2], 4.90)
            hw.pose[2] = .12
            self.assertTrue(operation.tick())

    def test_xy_and_z_bounds_fault(self):
        for xyz in ([.011,0,.11], [0,0,.089]):
            hw = FakeHardware()
            operation = e13.Operation(e13.Action("Descent bounds", 9, "DOWN", "TARGET"), hw)
            hw.pose[:3] = xyz
            with patch.object(e13, "CHECK_XY_DRIFT", True), self.assertRaises(RuntimeError):
                operation.tick()

    def test_temporary_xy_override_preserves_z_bound(self):
        hw = FakeHardware()
        operation = e13.Operation(e13.Action("Descent bounds", 9, "DOWN", "TARGET"), hw)
        hw.pose[0] = .025
        with patch.object(e13, "CHECK_XY_DRIFT", False):
            self.assertFalse(operation.tick())
            hw.pose[2] = .089
            with self.assertRaisesRegex(RuntimeError, "Z overtravel"):
                operation.tick()

    def test_loosen_stall_faults(self):
        hw = FakeHardware()
        hw.pose[2] = .10
        operation = e13.Operation(e13.FORWARD[3], hw)
        with patch.object(e13.e10, "status", return_value=dict(states=["STALLED", "IDLE"], pwm_percent=0)):
            operation.start()
            with self.assertRaisesRegex(RuntimeError, "Loosening"):
                operation.tick()

    def test_initial_grip_does_not_enable_force_or_move(self):
        hw = FakeHardware()
        operation = e13.Operation(e13.Action("Initial grip", 0, "INITIAL", "HOME", 1), hw)
        with patch.object(e13.e10, "status", return_value=dict(states=["STALLED", "IDLE"], pwm_percent=0)):
            operation.start()
            self.assertTrue(operation.tick())
        self.assertFalse(any(call[0] in ("force", "moveL") for call in hw.calls))

    def test_halt_releases_move_thread_before_force_stop_and_payload(self):
        from types import SimpleNamespace
        hw = e13.Hardware.__new__(e13.Hardware)
        calls, moving = [], [True]
        def stop_linear(deceleration, asynchronous):
            self.assertFalse(asynchronous)
            moving[0] = False
            calls.append("stopL joined")
        def force_stop():
            if moving[0]:
                raise RuntimeError("Another thread is already controlling the robot")
            calls.append("force stopped")
            return True
        hw.tool, hw.force_active, hw.motion_kind = None, True, "L"
        hw.last_timestamp, hw.fresh_at = 0, time.monotonic()
        hw.control = SimpleNamespace(stopL=stop_linear, forceModeStop=force_stop,
                                     isProgramRunning=lambda: True, kickWatchdog=lambda: True,
                                     setPayload=lambda *args: calls.append("payload") or True)
        hw.receiver = SimpleNamespace(getActualQd=lambda: [0]*6, getTimestamp=time.monotonic)
        hw.payloads = {"tool_only": (1.19, [-.003,.044,0])}
        hw.log = SimpleNamespace(write=lambda *args, **kwargs: None)
        hw.halt()
        hw.payload("tool_only")
        self.assertEqual(calls, ["stopL joined", "force stopped", "payload"])
        self.assertFalse(hw.force_active)

    def test_robot_stop_attempted_even_when_tool_stop_fails(self):
        from types import SimpleNamespace
        hw = e13.Hardware.__new__(e13.Hardware)
        calls = []
        hw.tool = object()
        hw.force_active, hw.motion_kind = True, "L"
        hw.control = SimpleNamespace(forceModeStop=lambda: calls.append("forceStop"),
                                     stopL=lambda *args: calls.append("stopL"))
        hw.receiver = SimpleNamespace(getActualQd=lambda: [0]*6)
        hw.check = lambda: None
        def failed_stop(): raise RuntimeError("Tool disconnected")
        hw.stop_tool = failed_stop
        with self.assertRaisesRegex(RuntimeError, "STOP UNCONFIRMED"):
            hw.halt()
        self.assertEqual(calls, ["stopL", "forceStop"])

    def test_actual_preview_worker_requires_new_click_after_completion(self):
        # A thread exercises the same process entry point using in-memory queues.
        import threading
        commands, events = queue.Queue(), queue.Queue()
        with tempfile.TemporaryDirectory() as directory, patch.object(e13, "HERE", Path(directory)), \
                patch.object(e13, "Hardware", side_effect=AssertionError("Preview accessed hardware")):
            thread = threading.Thread(target=e13.worker, args=(commands, events, False))
            thread.start()
            commands.put(("connect",))
            messages = []
            def pulse(direction, seconds):
                deadline = time.monotonic()+seconds
                while time.monotonic() < deadline:
                    commands.put(("intent", direction, True, time.monotonic()))
                    while not events.empty(): messages.append(events.get_nowait())
                    time.sleep(.02)
            try:
                pulse(0,.1)
                pulse(1,1.4)
                pulse(1,1.3)  # Still held: must not start the next action.
                completed = [m for m in messages if m["color"] == "complete"]
                self.assertEqual([m["state"] for m in completed], [0])
                pulse(0,.1)
                pulse(1,1.4)
                self.assertTrue(any(m["color"] == "complete" and m["state"] == 1 for m in messages))
                pulse(0,.1)
                pulse(2,1.4)
                self.assertTrue(any(m["color"] == "complete" and m["state"] == -2 for m in messages))
                pulse(0,.1)
                pulse(1,.2)
                self.assertEqual(messages[-1]["state"], -2)
            finally:
                commands.put(("quit",))
                thread.join(2)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
