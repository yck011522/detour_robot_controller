"""The robot process: executes validated operations and reports events by queue.

Commands (tuples):
  ("detect",)                              -> ("detected", home or None, text) or ("unavailable", text)
  ("jog", pose)                            repeat while a key is held; ends with ("jog_ended",)
  ("pause", bool)                          applied before the next step; never interrupts a step
  ("operation", id, kind, target, loaded)  kind: pick | place (zone letter) | travel (home name)
  ("stop",)                                exit
Events carry the operation id: ("pickup_completed", id), ("placement_completed", id),
("high_clearance_reached", id), ("home_reached", id, home), ("fault", id, text).
"""

from collections import deque
from datetime import datetime
import io
import queue
import signal
import time

from .settings import ROOT

LOG_DIRECTORY = ROOT / "logs/robot"
JOG_HEARTBEAT_SECONDS = 0.5


def home_for(zone):
    return "ac_home" if zone.upper() in "AC" else "bd_home"


def other_home(home):
    return "bd_home" if home == "ac_home" else "ac_home"


def operation_steps(kind, target):
    """Return (label, step arguments, event emitted after the step) tuples."""
    if kind == "travel":
        prefix = "bd_ac" if target == "ac_home" else "ac_bd"
        names = [*(f"{prefix}_{i}" for i in range(1, 6)), target]
        return [(f"Travel to {name}", ["move", "--pose", name, "--guard", "--profile", "cross_home"],
                 "home_reached" if name == target else None) for name in names]
    zone = target.lower()
    tightening_motor, release_motor, payload = (2, 1, "tool_only") if kind == "place" else (1, 2, "tool_with_floor")
    previous = "tool_with_floor" if kind == "place" else "tool_only"
    high = f"{zone}_high"
    def prepare(before, after):
        return ["prepare-high", "--pose", high, "--previous", before, "--payload", after]

    return [
        (f"Approach {target.upper()} high", ["move", "--pose", high, "--guard"], None),
        ("Prepare payload at high", prepare(previous, previous), None),
        (f"Approach {target.upper()} target", ["move", "--pose", f"{zone}_target", "--guard"], None),
        ("Tighten with downward compliance", ["tighten", "--motor", str(tightening_motor), "--payload", "keep"], None),
        ("Loosen with upward compliance", ["withdraw", "--motor", str(release_motor), "--guard"],
         "pickup_completed" if kind == "pick" else "placement_completed"),
        ("Retreat to high", ["move", "--pose", high, "--guard"], None),
        ("Update payload at high", prepare(previous, payload),
         "high_clearance_reached"),
        ("Return to associated home", ["move", "--pose", home_for(target), "--guard"], "home_reached"),
    ]


class DryRobot:
    """Same interface as LiveRobot with no connections; each step only takes time."""

    def __init__(self, step_seconds=1.0, start_home="bd_home"):
        self.step_seconds, self.start_home = step_seconds, start_home

    def detect(self):
        if self.start_home:
            return self.start_home, f"Dry run at {self.start_home}"
        return None, "Dry run away from home"

    def shutdown(self):
        pass

    def check_start(self, pose, payload, tolerance=None):
        pass

    def run_step(self, arguments, output):
        output.write(f"DRY RUN {' '.join(arguments)}\n")
        time.sleep(self.step_seconds)
        return 0

    def dwell(self):
        pass

    def jog(self, pose, keep_going):
        while keep_going():
            time.sleep(0.05)
        self.start_home = pose


class Worker:
    def __init__(self, commands, events, robot, log_directory=None):
        """log_directory: where live step transcripts go; None keeps them in memory."""
        self.commands, self.events, self.robot = commands, events, robot
        self.log_directory = log_directory
        self.inbox = deque()
        self.paused = self.stopped = self.faulted = False
        self.home = None
        self.first_operation = True
        self.operations = 0

    def drain(self, timeout=0):
        """Apply pause/stop immediately and keep every other message in order."""
        while True:
            try:
                message = self.commands.get(timeout=timeout) if timeout else self.commands.get_nowait()
            except queue.Empty:
                return
            timeout = 0
            if message[0] == "pause":
                self.paused = bool(message[1])
            elif message[0] == "stop":
                self.stopped = True
            else:
                self.inbox.append(message)

    def wait_while_paused(self):
        self.drain()
        while self.paused and not self.stopped:
            self.drain(0.1)

    def serve(self):
        while not self.stopped:
            if not self.inbox:
                self.drain(0.1)
                continue
            message = self.inbox.popleft()
            try:
                if message[0] == "detect":
                    self.detect()
                elif message[0] == "jog":
                    self.jog(message[1])
                elif message[0] == "operation":
                    self.operate(*message[1:])
            except Exception as error:
                if message[0] == "operation":
                    self.faulted = True
                    self.events.put(("fault", message[1], str(error)))
                else:
                    self.events.put(("unavailable", str(error)))

    def detect(self):
        home, text = self.robot.detect()
        self.home, self.first_operation = home, True
        self.events.put(("detected", home, text))

    def jog(self, pose):
        last = time.monotonic()

        def keep_going():
            nonlocal last
            self.drain(0.05)
            while self.inbox and self.inbox[0] == ("jog", pose):
                self.inbox.popleft()
                last = time.monotonic()
            return not self.inbox and not self.stopped and time.monotonic() - last < JOG_HEARTBEAT_SECONDS

        try:
            self.robot.jog(pose, keep_going)
        finally:
            self.events.put(("jog_ended",))

    def operate(self, operation_id, kind, target, loaded):
        if self.faulted:
            raise RuntimeError("Robot is faulted; restart after reconciling the physical state")
        if kind not in ("pick", "place", "travel"):
            raise ValueError(f"Unknown operation: {kind}")
        start = other_home(target) if kind == "travel" else home_for(target)
        if self.home != start:
            raise RuntimeError(f"{kind} {target} requires the robot at {start}; it is at {self.home}")
        self.wait_while_paused()
        self.robot.check_start(start, "tool_with_floor" if loaded else "tool_only",
                               5.0 if self.first_operation else None)
        self.operations += 1
        steps = operation_steps(kind, target)
        self.home = None
        if self.log_directory:
            self.log_directory.mkdir(parents=True, exist_ok=True)
            transcript = self.log_directory / f"{datetime.now():%Y%m%d_%H%M%S}_operation_{self.operations:04d}_{kind}_{target}.txt"
            output = transcript.open("w", encoding="utf-8")
        else:
            transcript, output = "(memory)", io.StringIO()
        with output:
            for index, (label, arguments, event) in enumerate(steps):
                self.wait_while_paused()
                self.events.put(("step", operation_id, label))
                output.write(f"STEP {index + 1}/{len(steps)}: {label}\n")
                output.flush()
                code = self.robot.run_step(arguments, output)
                if code:
                    raise RuntimeError(f"{label} incomplete (exit {code}); inspect {transcript}")
                if event == "home_reached":
                    self.home = home_for(target) if kind != "travel" else target
                    self.first_operation = False
                    self.events.put((event, operation_id, self.home))
                elif event:
                    self.events.put((event, operation_id))
                if index < len(steps) - 1:
                    self.robot.dwell()


def run(commands, events, mode="live", dry_step_seconds=1.0):
    """Process entry point."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # The application owns shutdown.
    try:
        if mode == "live":
            from .live import LiveRobot
            robot, transcripts = LiveRobot(), LOG_DIRECTORY
        else:
            robot, transcripts = DryRobot(dry_step_seconds), None
    except Exception as error:
        events.put(("unavailable", f"Robot setup failed: {error}"))
        return
    try:
        Worker(commands, events, robot, transcripts).serve()
    finally:
        try:
            robot.shutdown()
            events.put(("powered_off",))
        except Exception as error:
            events.put(("power_off_failed", str(error)))
            raise
