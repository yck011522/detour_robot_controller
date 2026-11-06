"""Power up a UR robot, release the brakes, wait, then stop and power off."""

import configparser
from pathlib import Path
import socket
import time


address_configuration = configparser.ConfigParser()
PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
with (PROJECT_DIRECTORY / "config" / "address.ini").open(encoding="utf-8") as file:
    address_configuration.read_file(file)

HOST = address_configuration["robotic_arm"]["host"]
DASHBOARD_PORT = 29999
COMMAND_TIMEOUT_SECONDS = 5
POWER_TIMEOUT_SECONDS = 60
STARTUP_IDLE_TIMEOUT_SECONDS = 15
BRAKE_TIMEOUT_SECONDS = 60
MODE_POLL_INTERVAL_SECONDS = 1
STABLE_MODE_POLLS = 3
READY_WAIT_SECONDS = 5


class DashboardRejected(RuntimeError):
    """A Dashboard command was explicitly rejected by the controller."""


class RobotModeTimeout(TimeoutError):
    """The expected robot mode was not observed within the polling deadline."""


def send_command(stream, command):
    """Send one Dashboard Server command and return its response line."""
    stream.write(f"{command}\n".encode("ascii"))
    stream.flush()
    response = stream.readline().decode("utf-8", errors="replace").strip()
    if not response:
        raise TimeoutError(f"No response to Dashboard Server command: {command}")
    print(f"> {command}\n< {response}", flush=True)
    if response.lower().startswith(("command is not allowed", "failed", "unable")):
        raise DashboardRejected(f"Dashboard Server rejected {command!r}: {response}")
    return response


def wait_for_robot_mode(stream, connection, expected_mode, timeout_seconds, polls=1):
    """Require consecutive matching Dashboard reports before accepting a mode."""
    deadline = time.monotonic() + timeout_seconds
    consecutive_matches = 0
    original_timeout = connection.gettimeout()
    try:
        while (remaining := deadline - time.monotonic()) > 0:
            connection.settimeout(min(COMMAND_TIMEOUT_SECONDS, remaining))
            response = send_command(stream, "robotmode")
            if time.monotonic() >= deadline:
                break
            if response.lower() == f"robotmode: {expected_mode}".lower():
                consecutive_matches += 1
                if consecutive_matches >= polls:
                    return
            else:
                consecutive_matches = 0
            time.sleep(max(0, min(MODE_POLL_INTERVAL_SECONDS, deadline - time.monotonic())))
    finally:
        connection.settimeout(original_timeout)
    raise RobotModeTimeout(
        f"Robot did not report {expected_mode} for {polls} "
        f"consecutive polls within {timeout_seconds} seconds"
    )


def main():
    """Run the automatic power-up, brake-release, stop, and power-off sequence."""
    print(f"Connecting to {HOST}:{DASHBOARD_PORT}", flush=True)
    with socket.create_connection(
        (HOST, DASHBOARD_PORT), timeout=COMMAND_TIMEOUT_SECONDS
    ) as connection:
        connection.settimeout(COMMAND_TIMEOUT_SECONDS)
        with connection.makefile("rwb") as stream:
            greeting = stream.readline().decode("utf-8", errors="replace").strip()
            print(f"< {greeting}", flush=True)
            if not greeting:
                raise TimeoutError("No greeting from the Dashboard Server")

            remote_control = send_command(stream, "is in remote control")
            if remote_control.lower() != "true":
                raise RuntimeError("Robot is not in Remote Control mode")

            current_mode = send_command(stream, "robotmode").partition(":")[2].strip()
            if current_mode == "POWER_OFF":
                send_command(stream, "power on")
                current_mode = "POWER_ON"
            if current_mode not in ("POWER_ON", "BOOTING", "IDLE", "RUNNING"):
                raise RuntimeError(f"Cannot start from robot mode {current_mode!r}")

            if current_mode != "RUNNING":
                print("Waiting for stable IDLE before brake release.", flush=True)
                wait_for_robot_mode(
                    stream, connection, "IDLE", STARTUP_IDLE_TIMEOUT_SECONDS, STABLE_MODE_POLLS
                )
                send_command(stream, "brake release")

            wait_for_robot_mode(
                stream, connection, "RUNNING", BRAKE_TIMEOUT_SECONDS, STABLE_MODE_POLLS
            )

            print(
                f"Robot operational (RUNNING confirmed for {STABLE_MODE_POLLS} "
                f"polls). Waiting {READY_WAIT_SECONDS} seconds.",
                flush=True,
            )
            time.sleep(READY_WAIT_SECONDS)
            send_command(stream, "stop")
            send_command(stream, "power off")
            wait_for_robot_mode(stream, connection, "POWER_OFF", POWER_TIMEOUT_SECONDS)
            print("Robot stopped and powered off.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        print(f"Startup/shutdown failed: {error}", flush=True)
        raise SystemExit(1) from error
