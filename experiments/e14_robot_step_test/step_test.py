"""One supervised robot action per invocation; default action is read-only state."""

import argparse
import configparser
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/e10_tool_rs485"))
import tool_test as tool_protocol
from movement_settings import DEFAULT_CONFIG, force_completion, load_payload, load_poses, load_settings


class Finished(Exception):
    """Exit the action body while allowing cleanup to determine the final result."""


class Log:
    def __init__(self):
        directory = Path(__file__).resolve().parent / "results"
        directory.mkdir(exist_ok=True)
        self.path = directory / f"{datetime.now():%Y%m%d_%H%M%S_%f}.log"
        self.file = self.path.open("x", encoding="utf-8")
        print(f"Log: {self.path}")

    def write(self, event, **data):
        line = json.dumps(dict(time=datetime.now().astimezone().isoformat(), event=event, **data))
        self.file.write(line + "\n")
        self.file.flush()
        print(line, flush=True)


def settled_matches(joints, poses):
    return {name: math.degrees(max(abs(a-b) for a, b in zip(joints, q)))
            for name, q in poses.items()}


def stop_tool(tool):
    tool.command("STOP", "OK STOP")
    status = tool_protocol.status(tool)
    if status["states"] != ["IDLE", "IDLE"] or status.get("pwm_percent", 0) != 0:
        raise RuntimeError("Tool STOP unconfirmed")


def snapshot(receiver):
    result = dict(joints=list(receiver.getActualQ()),
                  joint_velocity=list(receiver.getActualQd()),
                  tcp=list(receiver.getActualTCPPose()),
                  force=list(receiver.getActualTCPForce()),
                  timestamp=receiver.getTimestamp(),
                  safety_mode=receiver.getSafetyMode(), robot_mode=receiver.getRobotMode())
    for key in ("joints", "joint_velocity", "tcp", "force"):
        if len(result[key]) != 6 or not all(math.isfinite(v) for v in result[key]):
            raise RuntimeError(f"Invalid {key} feedback")
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", nargs="?", default="state",
                        choices=("state", "move", "tighten", "withdraw", "loosen-compliant", "zero"))
    result.add_argument("--live", action="store_true", help="enable one control action")
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    result.add_argument("--pose", help="named destination for a single direct MoveJ")
    result.add_argument("--motor", type=int, choices=(1, 2))
    result.add_argument("--payload", choices=("tool_only", "tool_with_floor", "keep"),
                        help="required for control; choose actual supported load")
    result.add_argument("--force", type=float, help="explicit force override in N; default from INI")
    result.add_argument("--seconds", type=float, help="explicit duration override; default from INI")
    result.add_argument("--joint-speed-deg-s", type=float, help="MoveJ speed override for cross-home runner")
    result.add_argument("--profile", choices=("local", "cross_home"), default="local",
                        help="MoveJ settings profile; force actions always use local operation settings")
    return result


def run(args):
    movement = load_settings(args.config)
    joint = movement["joint_motion"]
    runtime = movement["runtime"]
    if args.profile == "cross_home":
        if args.action != "move":
            raise ValueError("Cross-home profile is only valid for MoveJ")
        joint["speed_deg_s"] = movement["cross_home"]["speed_deg_s"]
        joint["timeout_seconds"] = movement["cross_home"]["timeout_seconds"]
    if args.joint_speed_deg_s is not None:
        if args.action != "move" or not math.isfinite(args.joint_speed_deg_s) or args.joint_speed_deg_s <= 0:
            raise ValueError("Joint speed override requires MoveJ and a finite positive value")
        joint["speed_deg_s"] = args.joint_speed_deg_s
    force_action = args.action in ("tighten", "withdraw", "loosen-compliant")
    if args.payload is None and force_action:
        args.payload = "keep"
    if args.force is None:
        args.force = (movement["tightening"]["downward_force_n"] if args.action == "tighten" else
                      movement["placement_release" if args.motor == 1 else "pickup_release"]["upward_force_n"]
                      if args.action == "withdraw" else 0.)
    if args.seconds is None:
        args.seconds = movement["tightening" if args.action == "tighten" else
                                "release" if force_action else "joint_motion"]["timeout_seconds"]
    poses = load_poses()
    active = args.action != "state"
    if active and (not args.live or not args.payload):
        raise ValueError("Control actions require --live and --payload")
    if args.action == "move" and args.pose not in poses:
        raise ValueError("Move requires --pose naming an existing taught pose")
    if args.action in ("tighten", "withdraw", "loosen-compliant") and args.motor is None:
        raise ValueError("Tighten/withdraw requires --motor 1 or 2")
    if not math.isfinite(args.force) or args.force < 0:
        raise ValueError("Force must be finite and nonnegative")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        raise ValueError("Seconds must be finite and positive")
    if force_action and args.payload != "keep":
        raise ValueError("Engaged motor actions require --payload keep; set payload separately when clear")
    log = Log()
    receiver = control = tool = None
    force_attempted = False
    failed = False
    try:
        from rtde_receive import RTDEReceiveInterface
        receiver = RTDEReceiveInterface(tool_protocol.host_from_config(), runtime["receive_hz"])
        time.sleep(joint["settle_seconds"])
        initial = snapshot(receiver)
        errors = settled_matches(initial["joints"], poses)
        log.write("STATE", **initial, joint_errors_deg=errors,
                  arrival_tolerance_deg=joint["arrival_tolerance_deg"],
                  matching_poses=[n for n, e in errors.items() if e <= joint["arrival_tolerance_deg"]])
        if not active:
            raise Finished()
        print("ONE ACTION ONLY:", vars(args))
        print("Move is a DIRECT joint leg, not an automatic route. Confirm its path.")
        print("M1: floor/tool connection. M2: floor/structure connection.")
        print("Ctrl+C stops the action. Keep pendant/tool power stop available.")
        if args.action == "zero":
            print("Zero requires no external contact and the correct supported payload.")
        if input("Confirm support, disengagement/path and payload; type RUN: ").strip() != "RUN":
            log.write("CANCELLED")
            failed = True
            raise Finished()
        initial = snapshot(receiver)
        if initial["safety_mode"] != 1 or initial["robot_mode"] != 7:
            raise RuntimeError("Restore robot NORMAL safety / RUNNING first")
        if max(map(abs, initial["joint_velocity"])) > joint["stationary_velocity_rad_s"]:
            raise RuntimeError("Robot must be stationary before the test")
        from rtde_control import RTDEControlInterface
        control = RTDEControlInterface(tool_protocol.host_from_config())
        addresses = configparser.ConfigParser()
        with (ROOT / "config/address.ini").open(encoding="utf-8") as source:
            addresses.read_file(source)
        tool = tool_protocol.LineClient(tool_protocol.host_from_config(),
                                        addresses.getint("robotic_arm", "tool_tcp_port"), log,
                                        timeout=runtime["tool_reply_timeout_seconds"])
        stop_tool(tool)
        if args.payload == "keep":
            log.write("PAYLOAD_PRESERVED", mass_kg=receiver.getPayload(),
                      cog=list(receiver.getPayloadCog()), note="No setPayload or FT zero command")
        else:
            mass, cog = load_payload(args.payload)
            if not control.setPayload(mass, cog):
                raise RuntimeError("Payload command rejected")
        if args.action == "zero":
            if not control.zeroFtSensor():
                raise RuntimeError("FT zero rejected")
            time.sleep(runtime["ft_zero_settle_seconds"])
            log.write("ZERO_COMPLETE", **snapshot(receiver))
            raise Finished()
        if not control.setWatchdog(runtime["watchdog_min_hz"]):
            raise RuntimeError("Watchdog setup failed")
        initial = snapshot(receiver)
        start_z = initial["tcp"][2]
        started = time.monotonic()
        last_stamp, fresh_at = initial["timestamp"], started
        settled_at = None
        def apply_force():
            nonlocal force_attempted
            force_attempted = True
            fz = (0. if args.action == "loosen-compliant" else
                  -args.force if args.action == "tighten" else args.force)
            if not control.forceMode([0.]*6, [1,1,1,0,0,0], [0,0,fz,0,0,0], 2,
                                     [movement["compliance"]["translation_speed_mm_s"] / 1000]*3 +
                                     [math.radians(movement["compliance"]["orientation_deviation_deg"])]*3):
                raise RuntimeError("Force mode rejected")
        if args.action == "move":
            if not control.moveJ(poses[args.pose], math.radians(joint["speed_deg_s"]),
                                 math.radians(joint["acceleration_deg_s2"]), True):
                raise RuntimeError("MoveJ rejected")
        else:
            # Engagement must never begin against a position-holding arm.
            apply_force()
            log.write("FORCE_MODE_STARTED", commanded_fz_N=0. if args.action == "loosen-compliant" else
                      -args.force if args.action == "tighten" else args.force)
            command = f"{'TIGHTEN' if args.action == 'tighten' else 'LOOSEN'} M{args.motor}"
            tool.command(command, "OK " + command)
            started = time.monotonic()
        log.write("ACTION_START", settings={**vars(args), "config": str(args.config)},
                  movement_settings=movement, start_z=start_z, start_tcp=initial["tcp"])
        last_report = 0.
        while True:
            if not control.kickWatchdog():
                raise RuntimeError("Watchdog update failed")
            data = snapshot(receiver)
            now = time.monotonic()
            if data["timestamp"] != last_stamp:
                last_stamp, fresh_at = data["timestamp"], now
            if now - fresh_at > runtime["feedback_timeout_seconds"] or data["safety_mode"] != 1 or data["robot_mode"] != 7 or not control.isProgramRunning():
                raise RuntimeError("Feedback stale or robot safety/mode/program changed")
            if now - started > args.seconds and args.action == "move":
                raise RuntimeError("Test window ended; action incomplete, inspect and retry deliberately")
            done = False
            completion_reason = None
            if args.action == "move":
                running = control.getAsyncOperationProgressEx().isAsyncOperationRunning()
                stationary = max(map(abs, data["joint_velocity"])) < joint["stationary_velocity_rad_s"]
                if not running and stationary:
                    if settled_at is None:
                        settled_at = now
                    if now - settled_at >= joint["settle_seconds"]:
                        error = settled_matches(data["joints"], {args.pose: poses[args.pose]})[args.pose]
                        if error > joint["arrival_tolerance_deg"]:
                            raise RuntimeError(f"Move ended {error:.3f} degrees from destination")
                        done = True
                        completion_reason = "joint_arrival"
                else:
                    settled_at = None
            else:
                dz = data["tcp"][2] - start_z
                # Check tightening overtravel before a potentially slow tool query.
                if args.action == "tighten":
                    force_completion(args.action, dz*1000, now-started, "TIGHTENING", movement, args.seconds)
                motor = tool_protocol.status(tool)
                own = motor["states"][args.motor - 1]
                if motor["states"][2 - args.motor] != "IDLE":
                    raise RuntimeError("Other motor unexpectedly active")
                expected = "TIGHTENING" if args.action == "tighten" else "LOOSENING"
                if own not in (expected, "STALLED"):
                    raise RuntimeError(f"Unexpected motor state: {own}")
                completion_reason = force_completion(args.action, dz*1000, now-started, own, movement, args.seconds)
                done = completion_reason is not None
                if not done:
                    apply_force()
            if now - last_report >= runtime["sample_interval_seconds"] or done:
                log.write("SAMPLE", **data, delta_z_mm=(data["tcp"][2]-start_z)*1000,
                          motor= motor if args.action != "move" else None)
                last_report = now
            if done:
                log.write("CRITERION_REACHED", action=args.action, reason=completion_reason)
                break
            time.sleep(runtime["loop_sleep_seconds"])
    except Finished:
        pass
    except (Exception, KeyboardInterrupt) as error:
        failed = True
        log.write("ACTION_INCOMPLETE", reason=str(error) or "Operator Ctrl+C")
    finally:
        # Attempt all cleanup operations even when an earlier stop fails.
        cleanup = []
        if tool:
            cleanup.append(("tool_stop", lambda: stop_tool(tool)))
        if control:
            cleanup.append(("motion_stop", lambda: control.stopJ(runtime["stop_joint_deceleration_rad_s2"])
                            if args.action == "move" else control.stopL(runtime["stop_linear_deceleration_m_s2"])))
            if force_attempted:
                cleanup.append(("force_stop", control.forceModeStop))
            cleanup.append(("script_stop", control.stopScript))
        for name, operation in cleanup:
            try:
                if operation() is False:
                    raise RuntimeError("Command returned false")
                log.write("CLEANUP_COMPLETE", step=name)
            except Exception as error:
                failed = True
                log.write("STOP_UNCONFIRMED", step=name, reason=str(error))
        if receiver:
            try:
                time.sleep(joint["settle_seconds"])
                final = snapshot(receiver)
                log.write("FINAL_STATE", **final)
                if active and max(map(abs, final["joint_velocity"])) > joint["stationary_velocity_rad_s"]:
                    failed = True
                    log.write("STOP_UNCONFIRMED", reason="Joint velocity remains nonzero")
            except Exception as error:
                failed = True
                log.write("FINAL_STATE_ERROR", reason=str(error))
        for connection in (tool, control, receiver):
            if connection:
                try:
                    (connection.close if connection is tool else connection.disconnect)()
                except Exception as error:
                    failed = True
                    log.write("DISCONNECT_ERROR", reason=str(error))
        log.write("END", outcome="INCOMPLETE" if failed else "FINISHED",
                  note="No board inventory or planner state was changed")
        log.file.close()
    return int(failed)


if __name__ == "__main__":
    arguments = parser().parse_args()
    try:
        raise SystemExit(run(arguments))
    except (ValueError, KeyError) as error:
        raise SystemExit(str(error))
