"""Read a UR arm's state over RTDE without uploading scripts or commanding motion."""

import configparser
from pathlib import Path
import time

from rtde_receive import RTDEReceiveInterface


# Resolve the shared address configuration independently of the working directory.
project_directory = Path(__file__).resolve().parents[2]
address_configuration = configparser.ConfigParser()
with (project_directory / "config" / "address.ini").open(encoding="utf-8") as configuration_file:
    address_configuration.read_file(configuration_file)

# Use the configured robotic arm IP for the read-only RTDE connection.
robot_host = address_configuration["robotic_arm"]["host"]
print(f"Connecting to {robot_host} using RTDE receive only...", flush=True)

# Receive robot state at 125 Hz; this interface does not upload a control script.
robot_receiver = RTDEReceiveInterface(robot_host, 125.0)
try:
    print(f"Connected: {robot_receiver.isConnected()}", flush=True)
    print(f"Controller timestamp (s): {robot_receiver.getTimestamp()}", flush=True)
    print(f"Robot mode (raw): {robot_receiver.getRobotMode()}", flush=True)
    print(f"Safety mode (raw): {robot_receiver.getSafetyMode()}", flush=True)
    print(f"Joint positions (rad): {robot_receiver.getActualQ()}", flush=True)
    print(f"TCP pose (m, rotation vector rad): {robot_receiver.getActualTCPPose()}", flush=True)

    # Read a later controller timestamp to demonstrate an updating state stream.
    time.sleep(0.2)
    print(f"Later controller timestamp (s): {robot_receiver.getTimestamp()}", flush=True)
finally:
    robot_receiver.disconnect()
    print("RTDE receive connection closed.", flush=True)
