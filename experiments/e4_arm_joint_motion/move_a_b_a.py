"""Move the UR10e through joint positions A, B, and A using asynchronous moves."""

import configparser
import math
from pathlib import Path
import time

from rtde_control import RTDEControlInterface


# Locate the project configuration and the folder used for timestamped run logs.
experiment_directory = Path(__file__).resolve().parent
project_directory = experiment_directory.parents[1]
results_directory = experiment_directory / "results"
address_file = project_directory / "config" / "address.ini"

# Define the requested joint positions in degrees for easy editing.
point_a_degrees = [180, -70, -110, -105, 180, 180]
point_b_degrees = [180, -80, -80, -105, 180, 180]

# ur_rtde expects joint positions, speed, and acceleration in radians.
point_a_radians = [math.radians(angle_degrees) for angle_degrees in point_a_degrees]
point_b_radians = [math.radians(angle_degrees) for angle_degrees in point_b_degrees]
joint_speed_radians_per_second = math.radians(15)
joint_acceleration_radians_per_second_squared = math.radians(30)

# Read the robot address shared by the experiments and main controller.
address_configuration = configparser.ConfigParser()
address_configuration.read(address_file)
robot_host = address_configuration["robotic_arm"]["host"]

# Give each run its own log file and timestamp each entry from script startup.
results_directory.mkdir(exist_ok=True)
script_start_time = time.monotonic()
log_file = results_directory / time.strftime("move_%Y-%m-%d_%H-%M-%S.log")


def move_to_point(robot_control, point_name, joint_positions_radians):
    """Start one asynchronous joint move, wait for completion, and log arrival."""
    previous_progress = robot_control.getAsyncOperationProgress()
    robot_control.moveJ(
        joint_positions_radians,
        joint_speed_radians_per_second,
        joint_acceleration_radians_per_second_squared,
        True,
    )

    # A new negative progress value means the asynchronous move has finished.
    while True:
        current_progress = robot_control.getAsyncOperationProgress()
        if current_progress < 0 and current_progress != previous_progress:
            break
        time.sleep(0.01)

    elapsed_seconds = time.monotonic() - script_start_time
    log_message = f"{elapsed_seconds:8.3f} s | Reached point {point_name}"
    print(log_message, flush=True)
    with log_file.open("a", encoding="utf-8") as output_file:
        output_file.write(log_message + "\n")


# Connect, perform the requested A -> B -> A sequence, and close control cleanly.
robot_control = RTDEControlInterface(robot_host)
move_to_point(robot_control, "A", point_a_radians)
move_to_point(robot_control, "B", point_b_radians)
move_to_point(robot_control, "A", point_a_radians)
robot_control.stopScript()
robot_control.disconnect()

print(f"Log saved to {log_file}", flush=True)
