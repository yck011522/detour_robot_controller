"""Supervised three-plate coverage queue with controlled home-boundary stopping."""

import argparse
import configparser
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import threading
import time

import home_travel
from movement_settings import DEFAULT_CONFIG, load_poses, load_settings
from transfer_queue import TransferQueue, parse_order
from zone_flow import HERE, ROOT, build_steps, check_start, home_for, run_steps, validate_steps


class ControlledStop(Exception):
    pass


def read_queue_settings(path):
    config = configparser.ConfigParser(interpolation=None)
    with Path(path).open(encoding="utf-8") as source:
        config.read_file(source)
    order = parse_order(config["queue_test"]["transfer_order"])
    interval = config.getfloat("queue_test", "keyboard_poll_seconds")
    if not 0 < interval <= 1:
        raise ValueError("Keyboard poll interval must be in (0, 1] seconds")
    return order, interval


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_home_tolerance(path):
    config = configparser.ConfigParser(interpolation=None)
    with Path(path).open(encoding="utf-8") as source:
        config.read_file(source)
    tolerance = config.getfloat("queue_test", "startup_home_tolerance_deg")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Startup home tolerance must be finite and positive")
    return tolerance


def recognize_home(joints, poses, tolerance):
    if len(joints) != 6 or not all(map(math.isfinite, joints)):
        raise ValueError("Invalid starting joints")
    errors = {home: math.degrees(max(abs(a-b) for a,b in zip(joints, poses[home])))
              for home in ("ac_home", "bd_home")}
    matches = [home for home,error in errors.items() if error <= tolerance + 1e-9]
    if len(matches) != 1:
        raise ValueError(f"Starting pose must match exactly one home within {tolerance} deg/joint: {errors}")
    return matches[0], errors


def detect_home(settings, tolerance):
    import step_test
    from rtde_receive import RTDEReceiveInterface
    receiver = RTDEReceiveInterface(step_test.tool_protocol.host_from_config(), settings["runtime"]["receive_hz"])
    try:
        stamp = receiver.getTimestamp()
        time.sleep(settings["joint_motion"]["settle_seconds"])
        state = step_test.snapshot(receiver)
        if state["timestamp"] <= stamp:
            raise RuntimeError("Starting feedback is not updating")
        if state["safety_mode"] != 1 or state["robot_mode"] != 7:
            raise RuntimeError("Restore NORMAL safety/RUNNING before starting")
        if max(map(abs, state["joint_velocity"])) > settings["joint_motion"]["stationary_velocity_rad_s"]:
            raise RuntimeError("Robot must be stationary for home detection")
        home, errors = recognize_home(state["joints"], load_poses(), tolerance)
        print(f"DETECTED HOME: {home}; joint errors (degrees): {errors}")
        return home
    finally:
        receiver.disconnect()


def prepare_robot():
    """Power on and release brakes using the shared, checked Dashboard sequence."""
    import step_test
    protocol = step_test.tool_protocol
    log = protocol.Log("queue_power")
    try:
        client = protocol.dashboard(protocol.host_from_config(), log)
        try:
            protocol.prepare_robot(client)
        finally:
            client.close()
    finally:
        log.close()


def engage_brakes():
    """Power off only the arm; leave the controller and Dashboard server on."""
    import step_test
    protocol = step_test.tool_protocol
    log = protocol.Log("queue_brakes")
    try:
        client = protocol.dashboard(protocol.host_from_config(), log)
        try:
            client.command("stop", "Stopped")
            client.command("power off", "Powering off")
            protocol.wait_mode(client, "POWER_OFF", 30)
            print("Arm powered off; brakes engaged. Controller remains on.", flush=True)
        finally:
            client.close()
    finally:
        log.close()


@contextmanager
def robot_power():
    try:
        prepare_robot()
        yield
    finally:
        try:
            engage_brakes()
        except BaseException:
            print("EXIT CLEANUP FAILED: arm power-off/brake engagement is unconfirmed. Check the pendant.", flush=True)
            raise


def ask_plate_layout(home, order, seed=None, resume=False):
    """Ask about every plate. Saved/CLI values are only visible prefills."""
    defaults = {plate: z for z,plate in seed.zones.items() if plate} if seed else {}
    if seed and seed.held:
        defaults[seed.held] = "g"
    plates = list(defaults) if seed else ["plate_1", "plate_2", "plate_3"]
    assigned = {}
    print("Confirm all three plate locations: A/B/C/D or G (gripper). Q cancels startup.")
    for plate in plates:
        while True:
            default = defaults.get(plate)
            answer = input(f"{plate} location [A/B/C/D/G]{' ('+default.upper()+')' if default else ''}: ").strip().lower()
            if answer == "q":
                return None
            answer = answer or default
            if answer not in tuple("abcdg") or answer in assigned.values():
                print("Choose a valid unused location; each zone/gripper holds at most one plate.")
                continue
            assigned[plate] = answer
            break
    zones = {z: next((plate for plate,location in assigned.items() if location == z), None) for z in "abcd"}
    held = next((plate for plate,location in assigned.items() if location == "g"), None)
    if resume:
        if zones != seed.zones or held != seed.held:
            raise ValueError("Confirmed layout differs from saved pending state. Start a fresh session to reconcile it.")
        seed.home = home
        return seed
    pending = pending_plate = None
    if held:
        while True:
            source = input("Held plate's known pickup source [A/B/C/D; Q cancels]: ").strip().lower()
            if source == "q":
                return None
            if source in tuple("abcd") and zones[source] is None:
                break
            print("The pickup source must be one of the empty zones.")
        pending = next(pair for pair in order if pair[0] == source and zones[pair[1]] is None)
        pending_plate = held
        print(f"Held plate will complete {pending[0].upper()} -> {pending[1].upper()}.")
    return TransferQueue(zones, order, home, held=held, pending=pending, pending_plate=pending_plate)


class Controls:
    def __init__(self, stop_file, interval):
        self.requested = threading.Event()
        self.paused = threading.Event()
        self.closed = threading.Event()
        self.stop_file, self.interval = stop_file, interval
        self.reason = None
        self.requested_at = None

    def request(self, reason):
        if not self.requested.is_set():
            self.reason = reason
            self.requested_at = datetime.now().astimezone().isoformat()
            self.requested.set()
            print("\nSTOP REQUESTED: finishing the active operation and returning home.", flush=True)

    def poll(self):
        while not self.closed.is_set():
            if self.stop_file.exists():
                self.request("STOP file")
            if os.name == "nt":
                import msvcrt
                if msvcrt.kbhit():
                    key = msvcrt.getwch().lower()
                    self.handle_key(key)
            self.closed.wait(self.interval)

    def handle_key(self, key):
        if key.lower() == "p" and not self.requested.is_set():
            if self.paused.is_set():
                self.paused.clear()
                print("\nPAUSE FLAG CLEARED: progression may resume.", flush=True)
            else:
                self.paused.set()
                print("\nPAUSE REQUESTED: finishing the active sub-step before waiting.", flush=True)
        elif key.lower() in ("q", "\x03"):
            self.request("keyboard")


class Session:
    def __init__(self, model, settings, config_path, directory, startup_tolerance=None):
        self.model, self.settings, self.directory = model, settings, directory
        directory.mkdir(parents=True, exist_ok=False)
        self.status, self.phase, self.home_verified = "starting", "home", True
        self.operation_number = 0
        self.controls = None
        self.startup_tolerance = startup_tolerance
        self.events = (directory / "events.jsonl").open("x", encoding="utf-8")
        inputs = {"movement.ini": Path(config_path), "robot_pose.ini": ROOT / "config/robot_pose.ini",
                  "game_config.ini": ROOT / "config/game_config.ini", "address.ini": ROOT / "config/address.ini"}
        self.digests = {}
        for name, path in inputs.items():
            content = path.read_bytes()
            (directory / name).write_bytes(content)
            self.digests[str(path.resolve())] = hashlib.sha256(content).hexdigest()
        self.config_path = directory / "movement.ini"
        self.record("SESSION_STARTED", settings=settings, input_sha256=self.digests)

    def record(self, event, **data):
        record = dict(time=datetime.now().astimezone().isoformat(), event=event, phase=self.phase,
                      status=self.status, home_verified=self.home_verified,
                      pause_requested=self.controls.paused.is_set() if self.controls else False,
                      model=asdict(self.model), **data)
        self.events.write(json.dumps(record) + "\n")
        self.events.flush()
        atomic_json(self.directory / "state.json", record)

    def verify_inputs(self):
        for path, expected in self.digests.items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"Input changed during test: {path}; reconcile before restarting")

    def show(self):
        model = self.model
        layout = " | ".join(f"{z.upper()}: {model.zones[z] or 'empty'}" for z in "abcd")
        print(f"\n{layout} | GRIPPER: {model.held or 'empty'} | HOME: {model.home}", flush=True)
        print("QUEUE: " + ", ".join(a.upper()+">"+b.upper() for a,b in model.queue), flush=True)
        print("COMPLETED: " + ", ".join(k.upper()+":"+str(v) for k,v in sorted(model.counts.items())), flush=True)

    def wait_if_paused(self):
        controls = self.controls
        if not controls or not controls.paused.is_set() or controls.requested.is_set():
            return
        self.status = "paused"
        self.record("PAUSED")
        print("PAUSED at a completed sub-step. P: resume; Q: finish operation and quit at home.", flush=True)
        while controls.paused.is_set() and not controls.requested.is_set():
            controls.requested.wait(controls.interval)
        self.verify_inputs()
        self.status = "running"
        self.record("PAUSE_ENDED", reason="controlled quit" if controls.requested.is_set() else "P toggled off")

    def operation(self, kind, zone=None, destination_home=None):
        """A normal stop request never interrupts this complete home-to-home unit."""
        self.verify_inputs()
        self.wait_if_paused()
        if self.controls and self.controls.requested.is_set():
            raise ControlledStop()
        payload = "tool_with_floor" if self.model.held else "tool_only"
        check_start(self.model.home, payload, self.settings, home_tolerance_deg=self.startup_tolerance)
        self.wait_if_paused()
        if self.controls and self.controls.requested.is_set():
            raise ControlledStop()
        if kind == "transfer_home":
            direction = "bd-to-ac" if destination_home == "ac_home" else "ac-to-bd"
            steps = home_travel.build_steps(direction)
        else:
            steps = build_steps(kind, zone)
        validate_steps(steps)
        self.operation_number += 1
        transcript = self.directory / f"operation_{self.operation_number:04d}_{kind}_{zone or destination_home}.txt"
        self.phase, self.home_verified = kind, False
        self.record("OPERATION_STARTED", kind=kind, zone=zone, destination_home=destination_home, transcript=str(transcript))

        def on_step(event, index, label, arguments, returncode):
            if event == "started":
                self.wait_if_paused()
                self.verify_inputs()
            self.phase = f"{kind}: {label}"
            self.record("STEP_"+event.upper(), index=index+1, label=label, arguments=arguments, returncode=returncode)
            # The fourth step's release is the physical inventory transition.
            # A later retreat fault must not erase that observed completion.
            if event == "finished" and returncode == 0 and index == 3:
                if kind == "pick":
                    self.model.pickup_completed()
                    self.record("PICKUP_COMPLETED", plate=self.model.held)
                elif kind == "place":
                    self.model.placement_completed()
                    self.record("PLACEMENT_COMPLETED", zone=zone)

        code = run_steps(steps, self.config_path, self.settings, transcript, on_step=on_step, isolate_signals=True)
        if code:
            raise RuntimeError(f"{kind} incomplete; inspect {transcript}")
        self.model.home = destination_home if kind == "transfer_home" else home_for(zone)
        self.startup_tolerance = None
        self.home_verified, self.phase = True, "home"
        self.record("HOME_REACHED", kind=kind)
        time.sleep(self.settings["joint_motion"]["step_dwell_seconds"])

    def stop_at_home(self, controls):
        if not controls.requested.is_set():
            return False
        self.status = "stopped_home"
        self.record("CONTROLLED_STOP", reason=controls.reason, requested_at=controls.requested_at)
        self.show()
        print("STOPPED AT HOME. Pending transfer is retained; resume requires operator confirmation.", flush=True)
        return True

    def run(self, controls, max_transfers=0):
        self.status = "running"
        self.controls = controls
        initial_count = sum(self.model.counts.values())
        try:
            while True:
                if self.stop_at_home(controls):
                    return 0
                self.wait_if_paused()
                if self.stop_at_home(controls):
                    return 0
                self.show()
                if not self.model.pending:
                    pair, skipped = self.model.choose()
                    self.record("TRANSFER_SELECTED", transfer=pair, skipped=skipped)
                    for entry in skipped:
                        print(f"Skip {entry['transfer'].upper()}: {entry['reason']}", flush=True)
                    print(f"SELECT {pair[0].upper()} -> {pair[1].upper()} ({self.model.pending_plate})", flush=True)
                source, destination = self.model.pending
                if not self.model.held:
                    needed_home = home_for(source)
                    if self.model.home != needed_home:
                        self.operation("transfer_home", destination_home=needed_home)
                        if self.stop_at_home(controls):
                            return 0
                    self.operation("pick", source)
                    if self.stop_at_home(controls):
                        return 0
                needed_home = home_for(destination)
                if self.model.home != needed_home:
                    self.operation("transfer_home", destination_home=needed_home)
                    if self.stop_at_home(controls):
                        return 0
                self.operation("place", destination)
                completed = self.model.finish_transfer()
                self.record("TRANSFER_COMPLETED", transfer=completed, plate=self.model.zones[destination])
                with (self.directory / "transfers.csv").open("a", encoding="utf-8") as stream:
                    stream.write(f"{datetime.now().astimezone().isoformat()},{completed[0]},{completed[1]},{self.model.zones[destination]}\n")
                print(f"TRANSFER COMPLETE: {source.upper()} -> {destination.upper()}", flush=True)
                if max_transfers and sum(self.model.counts.values()) - initial_count >= max_transfers:
                    controls.request("configured transfer limit")
                if self.stop_at_home(controls):
                    return 0
                time.sleep(self.settings["joint_motion"]["step_dwell_seconds"])
        except ControlledStop:
            self.stop_at_home(controls)
            return 0
        except (Exception, KeyboardInterrupt) as error:
            self.status = "fault"
            self.record("FAULT", reason=str(error) or "Unexpected interruption", reconciliation_required=True)
            print(f"FAULT: {error}. No automatic retry/home movement. Inspect {self.directory}", flush=True)
            return 1
        finally:
            controls.closed.set()
            self.events.close()


def restored_model(path):
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if record.get("status") != "stopped_home" or not record.get("home_verified"):
        raise ValueError("Only a controlled home stop can be resumed; fault/interrupted states require reconciliation")
    return TransferQueue(**record["model"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--occupied", nargs=3, choices=list("abcd"), help="questionnaire prefills, not physical confirmation")
    parser.add_argument("--home", choices=("ac_home", "bd_home"), default="bd_home",
                        help="preview-only home; live startup always autodetects")
    parser.add_argument("--resume", type=Path, help="state.json from a controlled home stop")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", dest="live", action="store_true", help="execute on hardware (default)")
    mode.add_argument("--preview", dest="live", action="store_false", help="preview without hardware connections")
    parser.set_defaults(live=True)
    parser.add_argument("--confirmed", action="store_true", help="skip final RUN prompt only; location questions still apply")
    parser.add_argument("--max-transfers", type=int, default=0, help="optional acceptance-test bound; 0 runs until stopped")
    args = parser.parse_args(argv)
    if args.max_transfers < 0 or (args.occupied and len(set(args.occupied)) != 3):
        parser.error("Choose three distinct occupied zones and a nonnegative transfer limit")
    settings = load_settings(args.config)
    order, interval = read_queue_settings(args.config)
    occupied = args.occupied or list("abc")
    seed = restored_model(args.resume) if args.resume else TransferQueue(
        {z: f"plate_{occupied.index(z)+1}" if z in occupied else None for z in "abcd"}, order, args.home)
    # Preflight all operation and directed travel poses without opening hardware.
    for zone in "abcd":
        for kind in ("pick", "place"):
            validate_steps(build_steps(kind, zone))
    for direction in ("bd-to-ac", "ac-to-bd"):
        validate_steps(home_travel.build_steps(direction))
    print("THREE-PLATE QUEUE TEST. P: toggle pause between sub-steps. Q / Ctrl+C: controlled quit at home.")
    print("This test does not acquire car-zone tokens. Keep cars outside the robot work area.")
    if not args.live:
        model = seed
        print("Preview layout (not physically confirmed):", asdict(model))
        print("PREVIEW ONLY: no hardware connections. Omit --preview for supervised execution.")
        if not model.pending:
            pair, skipped = model.choose()
            print("First eligible transfer:", pair, "Skipped:", skipped)
        return 0
    tolerance = read_home_tolerance(args.config)
    with robot_power():
        home = detect_home(settings, tolerance)
        model = ask_plate_layout(home, order, seed if args.occupied or args.resume else None, resume=bool(args.resume))
        if model is None:
            return 1
        print("Confirmed layout:", asdict(model))
        if not args.confirmed and input("Confirm physical layout, payload, home, clear routes; type RUN: ").strip() != "RUN":
            return 1
        check_start(model.home, "tool_with_floor" if model.held else "tool_only", settings, home_tolerance_deg=tolerance)
        directory = HERE / "results" / f"{datetime.now():%Y%m%d_%H%M%S_%f}_queue"
        session = Session(model, settings, args.config.resolve(), directory, startup_tolerance=tolerance)
        (directory / "transfers.csv").write_text("time,source,destination,plate\n", encoding="utf-8")
        latest = HERE / "results/latest_queue_session.txt"
        latest.write_text(str(directory.resolve()), encoding="utf-8")
        controls = Controls(directory / "STOP", interval)
        old_handler = signal.signal(signal.SIGINT, lambda signum, frame: controls.request("Ctrl+C"))
        threading.Thread(target=controls.poll, daemon=True).start()
        print(f"SESSION: {directory}\nAlternative controlled stop: create {directory / 'STOP'}", flush=True)
        try:
            result = session.run(controls, args.max_transfers)
        finally:
            signal.signal(signal.SIGINT, old_handler)
        print(f"Session ended; state and logs: {directory}", flush=True)
        return result


if __name__ == "__main__":
    raise SystemExit(main())
