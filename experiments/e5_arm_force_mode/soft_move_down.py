"""Apply downward base-frame force with translation and time travel limits."""

import configparser
import math
from pathlib import Path
import time

from rtde_control import RTDEControlInterface
from rtde_receive import RTDEReceiveInterface

# Locate the shared robot address and this experiment's result directory.
experiment_directory = Path(__file__).resolve().parent
project_directory = experiment_directory.parents[1]
results_directory = experiment_directory / "results"
address_file = project_directory / "config" / "address.ini"

# Define point A in degrees and convert it to the radians required by ur_rtde.
point_a_degrees = [180, -70, -110, -90, 90, 180]
point_a_radians = [math.radians(angle_degrees) for angle_degrees in point_a_degrees]
joint_speed_radians_per_second = math.radians(15)
joint_acceleration_radians_per_second_squared = math.radians(30)

# Configure base-frame force mode with compliant X, Y, and Z translation.
task_frame = [0, 0, 0, 0, 0, 0]
compliant_axes = [1, 1, 1, 0, 0, 0]
downward_force_newtons = -(1.0 * 9.80665)
target_wrench = [0, 0, downward_force_newtons, 0, 0, 0]
force_frame_type = 2

# Limit lateral speed to 10 mm/s, downward speed to 1 mm/s, and orientation deviation.
force_mode_limits = [0.005, 0.005, 0.005, 0.05, 0.05, 0.05]
maximum_downward_travel_metres = 0.10
maximum_xy_travel_metres = 0.10
maximum_force_time_seconds = 30.0

# Read the configured UR10e address.
address_configuration = configparser.ConfigParser()
address_configuration.read(address_file)
robot_host = address_configuration["robotic_arm"]["host"]

# Create a separate elapsed-time log for this run.
results_directory.mkdir(exist_ok=True)
script_start_time = time.monotonic()
log_file = results_directory / time.strftime("soft_move_%Y-%m-%d_%H-%M-%S.log")


def log_event(event_description):
    """Print an event and append it to the run log with elapsed time."""
    elapsed_seconds = time.monotonic() - script_start_time
    log_message = f"{elapsed_seconds:8.3f} s | {event_description}"
    print(log_message, flush=True)
    with log_file.open("a", encoding="utf-8") as output_file:
        output_file.write(log_message + "\n")


# Move to point A before recording the force-mode reference position.
robot_control = RTDEControlInterface(robot_host)
robot_receiver = RTDEReceiveInterface(robot_host)
robot_control.moveJ(
    point_a_radians,
    joint_speed_radians_per_second,
    joint_acceleration_radians_per_second_squared,
)

# Zero the built-in force/torque sensor while stationary at point A.
robot_control.zeroFtSensor()
time.sleep(0.2)
log_event("Zeroed the force/torque sensor at point A")

starting_tcp_pose = robot_receiver.getActualTCPPose()
log_event(f"Reached point A; starting TCP pose: {starting_tcp_pose}")

# Reapply force mode each control cycle until one requested travel limit is met.
force_start_time = time.monotonic()
while True:
    control_period_start = robot_control.initPeriod()
    actual_tcp_pose = robot_receiver.getActualTCPPose()
    x_travel_metres = actual_tcp_pose[0] - starting_tcp_pose[0]
    y_travel_metres = actual_tcp_pose[1] - starting_tcp_pose[1]
    xy_travel_metres = math.hypot(x_travel_metres, y_travel_metres)
    downward_travel_metres = starting_tcp_pose[2] - actual_tcp_pose[2]
    force_elapsed_seconds = time.monotonic() - force_start_time

    if downward_travel_metres >= maximum_downward_travel_metres:
        stop_reason = "downward TCP travel reached 20 mm"
        break
    if xy_travel_metres >= maximum_xy_travel_metres:
        stop_reason = "radial XY travel reached 10 mm"
        break
    if force_elapsed_seconds >= maximum_force_time_seconds:
        stop_reason = "force-mode time reached 30 seconds"
        break

    robot_control.forceMode(
        task_frame,
        compliant_axes,
        target_wrench,
        force_frame_type,
        force_mode_limits,
    )
    robot_control.waitPeriod(control_period_start)

# End compliance before commanding the joint-space return to point A.
robot_control.forceModeStop()
log_event(
    f"Stopped force mode: {stop_reason}; "
    f"down={downward_travel_metres * 1000:.2f} mm, "
    f"XY={xy_travel_metres * 1000:.2f} mm"
)
robot_control.moveJ(
    point_a_radians,
    joint_speed_radians_per_second,
    joint_acceleration_radians_per_second_squared,
)
log_event("Returned to point A")

# Close both robot connections after the sequence completes.
robot_control.stopScript()
robot_control.disconnect()
robot_receiver.disconnect()
print(f"Log saved to {log_file}", flush=True)
