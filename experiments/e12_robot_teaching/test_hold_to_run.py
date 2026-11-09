"""Offline route/pose checks; never connect to a robot or start a UI window."""

import math
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import tempfile
import time

import hold_to_run as teaching
from hold_to_run import Route, TARGET, HOME, HIGH, TAUGHT_TCP, LOW_OFFSET_M, pose_close, finite_six


class TeachingTests(unittest.TestCase):
    def test_round_trip_only_final_segment_is_linear(self):
        route = Route(0)
        expected = [(1, "J"), (2, "J"), (3, "L"), (2, "L"), (1, "J"), (0, "J")]
        for direction, leg in zip((1, 1, 1, -1, -1, -1), expected):
            self.assertEqual(route.leg(direction), leg)
            route.arrived(leg[0])
        self.assertIsNone(route.leg(-1))

    def test_interrupted_linear_motion_reverses_to_low_not_high(self):
        route = Route(2)
        self.assertEqual(route.leg(1), (3, "L"))
        self.assertEqual(route.leg(-1), (2, "L"))
        self.assertEqual(route.leg(1), (3, "L"))

    def test_interrupted_joint_motion_retraces_edge(self):
        route = Route(0)
        self.assertEqual(route.leg(1), (1, "J"))
        self.assertEqual(route.leg(-1), (0, "J"))
        route.arrived(0)
        self.assertIsNone(route.leg(-1))

    def test_initial_home_approach_blocks_route_until_arrival(self):
        route = Route(None)
        self.assertIsNone(route.leg(1))
        self.assertIsNone(route.leg(-1))
        self.assertEqual(route.leg(-2), (0, "J"))
        self.assertIsNone(route.leg(1))  # Interrupted homing remains unanchored.
        route.arrived(0)
        self.assertIsNone(route.leg(-2))
        self.assertEqual(route.leg(1), (1, "J"))

    def test_equivalent_pi_rotation_vectors(self):
        a = [0, 0, 0, math.pi, 0, 0]
        b = [0, 0, 0, -math.pi, 0, 0]
        self.assertTrue(pose_close(a, b))
        self.assertFalse(pose_close(a, [0, 0, 0, 0, 0, 0]))

    def test_supplied_values_and_base_z_offset(self):
        for q in (TARGET, HOME, HIGH):
            self.assertTrue(finite_six(q))
        target = TAUGHT_TCP["TARGET"]
        low = target[:]
        low[2] += LOW_OFFSET_M
        self.assertAlmostEqual(low[2], .19139757)
        self.assertEqual(low[:2] + low[3:], target[:2] + target[3:])
        self.assertFalse(finite_six([float("nan")] * 6))

    def check_release(self, start, direction, expected_move, expected_stop):
        """Exercise the real worker with fake RTDE and Dashboard, never hardware."""
        calls = []
        class Receiver:
            def __init__(self, *args): pass
            def getActualQ(self):
                return [v + .01 for v in HOME] if start == "NEAR_HOME" else HOME[:] if start == "HOME" else TARGET[:]
            def getActualTCPPose(self): return TAUGHT_TCP["HOME" if start == "NEAR_HOME" else start][:]
            def getActualQd(self): return [0] * 6
            def getRobotMode(self): return 7
            def getSafetyMode(self): return 1
            def getTimestamp(self): return time.perf_counter()
            def disconnect(self): pass
        class Control:
            def __init__(self, *args): pass
            def getTCPOffset(self): return [0] * 6
            def getForwardKinematics(self, q, tcp):
                if not any(tcp):
                    raise AssertionError("FK must use the twelve-double TCP recipe")
                return TAUGHT_TCP[{tuple(HOME): "HOME", tuple(HIGH): "HIGH", tuple(TARGET): "TARGET"}[tuple(q)]][:]
            def getInverseKinematicsHasSolution(self, *args): return True
            def getInverseKinematics(self, *args): return [v + .001 for v in TARGET]
            def isJointsWithinSafetyLimits(self, q): return True
            def isPoseWithinSafetyLimits(self, p): return True
            def setWatchdog(self, *args): return True
            def kickWatchdog(self): return True
            def isProgramRunning(self): return True
            def getAsyncOperationProgressEx(self): return SimpleNamespace(isAsyncOperationRunning=lambda: True)
            def moveJ(self, *args): calls.append("moveJ"); return True
            def moveL(self, *args): calls.append("moveL"); return True
            def stopJ(self, *args): calls.append("stopJ")
            def stopL(self, *args): calls.append("stopL")
            def stopScript(self): calls.append("stopScript")
            def disconnect(self): pass
        class Socket:
            def __init__(self):
                self.replies = iter([b"Connected\n", b"true\n", b"Program running: false\n", b"Robotmode: RUNNING\n", b"Safetystatus: NORMAL\n"])
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, *args): pass
            def makefile(self, *args, **kwargs): return self
            def readline(self): return next(self.replies)
            def write(self, *args): pass
        class Config:
            def __init__(self, **kwargs): pass
            def read(self, *args): pass
            def __getitem__(self, key): return {"host": "fake"}
        modules = {"rtde_receive": SimpleNamespace(RTDEReceiveInterface=Receiver),
                   "rtde_control": SimpleNamespace(RTDEControlInterface=Control)}
        with tempfile.TemporaryDirectory() as directory, patch.object(teaching, "HERE", Path(directory)), \
                patch.dict("sys.modules", modules), patch.object(teaching.socket, "create_connection", return_value=Socket()), \
                patch.object(teaching.configparser, "ConfigParser", Config):
            experiment = teaching.Experiment(True)
            def wait_for(predicate):
                deadline = time.perf_counter() + 2
                while not predicate() and time.perf_counter() < deadline:
                    with experiment.lock:
                        experiment.heartbeat = time.perf_counter()
                    time.sleep(.005)
                self.assertTrue(predicate(), experiment.status)
            try:
                experiment.heartbeat = time.perf_counter()
                experiment.connect()
                wait_for(lambda: experiment.ready)
                time.sleep(.04)
                self.assertFalse(any(c.startswith("move") for c in calls))
                with experiment.lock:
                    experiment.allowed, experiment.direction = True, direction
                wait_for(lambda: expected_move in calls)
                with experiment.lock:
                    experiment.direction = 0
                wait_for(lambda: expected_stop in calls)
            finally:
                experiment.close.set()
                experiment.thread.join(timeout=3)
            self.assertIn("stopScript", calls)

    def test_joint_release_requests_stopj_without_connect_motion(self):
        self.check_release("HOME", 1, "moveJ", "stopJ")

    def test_linear_release_requests_stopl_without_connect_motion(self):
        self.check_release("TARGET", -1, "moveL", "stopL")

    def test_initial_home_hold_and_release(self):
        self.check_release("NEAR_HOME", -2, "moveJ", "stopJ")


if __name__ == "__main__":
    unittest.main()
