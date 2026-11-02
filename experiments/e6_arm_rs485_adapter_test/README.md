# E6: UR tool RS-485 adapter forwarding test

This experiment measures both directions through the RS-485 URCap forwarder:

1. A latency sweep sends payloads of 1, 8, 32, and 64 bytes.
2. A sending-rate sweep sends 8-byte payloads unpaced and at 10, 25, 50, and
   100 frames per second.
3. Each point runs in both directions. The script reports matched frames,
   median, p95, and maximum send-to-receive delay, and achieved frame rate.

The test uses the robot address in `config/address.ini`, TCP port `54321`,
`COM18`, and serial settings of 115200 baud, 8 data bits, no parity, and one
stop bit by default. Configure the robot's Tool I/O RS-485 settings to match.
The test does not send a robot motion command or communicate with the RTDE
control interface.

The test transmits short printable `E6` marker bytes. Ensure the attached tool
ignores these bytes. The USB adapter must be wired to the robot tool-flange
RS-485 bus with the correct polarity and common reference. The URCap TCP
forwarder must be running, and port 54321 must be reachable from this computer.

## Run

Install project requirements if needed, then run from the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe experiments/e6_arm_rs485_adapter_test/test_rs485_forwarding.py
```

Override the serial port, baud rate, timeout, or samples per point when needed:

```powershell
.\.venv\Scripts\python.exe experiments/e6_arm_rs485_adapter_test/test_rs485_forwarding.py --port COM18 --baudrate 115200 --timeout 2 --rounds 20
```

Latency is measured from the local send call until the destination receives the
bytes in the indicated direction. It includes local USB/network handling and is
not a pure wire-latency measurement. Each run writes its timestamped result to
`results/`. A short rate sweep is a repeatable load test, not a guarantee of
sustained capacity under arbitrary traffic or tool protocols.
