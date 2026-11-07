"""Supervised stationary-arm tool test. Run with --run; every action is prompted."""

import argparse
from pathlib import Path
import sys
import time

# Reuse E10's hardware-tested protocol checks; no new transport abstraction.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e10_tool_rs485"))
import tool_test as e10


def confirm(message, word="YES"):
    """Require an explicit operator response; any other input aborts."""
    if input(f"\n{message}\nType {word} to continue: ").strip() != word:
        raise RuntimeError("Operator cancelled")


def ready(control):
    """Observe the existing powered robot; never start a program or move the arm."""
    control.command("is in remote control", "true")
    control.command("safetystatus", "Safetystatus: NORMAL")
    control.command("running", "Program running: false")
    control.command("robotmode", "Robotmode: RUNNING")


def operate(tool, motor, action, seconds, log, expect_stall=False):
    """Bound one action; always STOP, including after stall, timeout or Ctrl+C."""
    e10.stop_and_verify(tool)
    expected = ["IDLE", "IDLE"]
    expected[motor - 1] = "TIGHTENING" if action == "TIGHTEN" else "LOOSENING"
    stalled, powered = False, False
    deadline = time.monotonic() + seconds
    try:
        command = f"{action} M{motor}"
        tool.command(command, f"OK {command}")
        while (remaining := deadline - time.monotonic()) > .015:
            sample = e10.status(tool, timeout=min(.3, remaining))
            log.write("MOTOR_SAMPLE", motor=motor, action=action, **sample)
            states = sample["states"]
            if states[1 if motor == 1 else 0] != "IDLE":
                raise RuntimeError("Other motor unexpectedly active")
            if states[motor - 1] == "STALLED":
                stalled = True
                break
            if states != expected:
                raise RuntimeError(f"Unexpected motor state: {states}")
            powered |= sample["pwm_percent"] > 0
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
        if expect_stall and not stalled:
            raise RuntimeError("No stall within time limit; stopped, no automatic retry")
        if not expect_stall and (stalled or not powered):
            raise RuntimeError("Loosen step stalled or had no running PWM evidence")
    finally:
        e10.stop_and_verify(tool)
    log.write("ACTION_COMPLETE", motor=motor, action=action, observed_stall=stalled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    e10.RESULTS = Path(__file__).resolve().parent / "results"
    log = e10.Log("supervised")
    tool = control = None
    outcome = "FAIL"
    try:
        confirm("Support all parts throughout. Arm remains stationary.\n"
                "Tightening is limited to 5 seconds; loosening to 1-second steps.\n"
                "Know how to cut tool power if communication fails. Ready?")
        host = e10.host_from_config()
        control = e10.dashboard(host, log)
        ready(control)
        tool = e10.connect_tool(host, 54321, log)
        sample = e10.status(tool)
        if sample["states"] != ["IDLE", "IDLE"]:
            raise RuntimeError("Tool must initially be idle")
        e10.stop_and_verify(tool)
        for motor in (1, 2):
            tool.command(f"GET CONFIG M{motor}")
        for motor, instruction in (
            (1, "Position and support the floor plate for M1 gripping."),
            (2, "Position/support the receiving structure through the held floor plate."),
        ):
            confirm(f"{instruction}\nStart M{motor} tighten until stall (maximum 5 s)?", "RUN")
            ready(control)
            operate(tool, motor, "TIGHTEN", 5, log, expect_stall=True)
            confirm(f"M{motor} reported stall and is stopped. Is the attachment physically secure?")
            log.write("OPERATOR_ATTACHMENT_CONFIRMED", motor=motor)
        # Reverse installation order. Disengagement is confirmed by the person,
        # not inferred from elapsed time, current, or a loosening stall.
        for motor in (2, 1):
            while True:
                response = input(f"\nSupport all parts. M{motor}: type LOOSEN for a 1 s step,\n"
                                 "DONE if physically disengaged, or Q to abort: ").strip()
                if response == "DONE":
                    confirm(f"Confirm M{motor} is disengaged and all released parts are supported.")
                    log.write("OPERATOR_DISENGAGEMENT_CONFIRMED", motor=motor)
                    break
                if response != "LOOSEN":
                    raise RuntimeError("Operator cancelled")
                ready(control)
                operate(tool, motor, "LOOSEN", 1, log)
        outcome = "PASS"
    except (KeyboardInterrupt, EOFError):
        outcome = "INTERRUPTED"
        log.write("INTERRUPTED")
    except Exception as error:
        log.write("FAILURE", error=str(error))
    finally:
        if tool:
            try:
                e10.stop_and_verify(tool)
            except Exception as error:
                outcome = "FAIL"
                log.write("TOOL_STOP_UNCONFIRMED", error=str(error), operator_intervention=True)
                print("STOP UNCONFIRMED: support parts and cut tool power immediately.", flush=True)
            finally:
                tool.close()
        if control:
            control.close()
        # Keep power unchanged: partially attached parts need operator handling.
        log.write("RESULT", outcome=outcome, power="unchanged",
                  scope="reported stalls plus operator-confirmed attachment/disengagement")
        log.close()
    return int(outcome != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
