# E3: robotic arm RTDE connection test

Run from the project root using the virtual environment:

```powershell
.\.venv\Scripts\python.exe experiments/e3_arm_connection/test_connection.py
```

The test reads `[robotic_arm] host` from `config/address.ini` and opens an
`RTDEReceiveInterface` connection. It prints connection status, robot and safety
mode numbers, joint angles, TCP pose, and two controller timestamps to confirm
that state data is updating, then disconnects.

This test sends no motion commands, uploads no control script, and does not
start or stop a robot program. Success verifies RTDE state reception; it does
not verify motion-command access through `RTDEControlInterface`.

## Result on 2026-10-27

Captured test output: [connection log](results/2026-10-27_connection.log).

Successfully connected from the development computer to `192.168.0.10`.
Controller timestamps advanced from `8496.845` to `8497.045` seconds, and
joint positions and TCP pose were received. The reported raw robot mode was
`7` and raw safety mode was `1`. The connection closed successfully.
