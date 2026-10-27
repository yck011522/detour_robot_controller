# E4: asynchronous A → B → A joint motion

`move_a_b_a.py` moves the UR10e through these joint-space positions:

| Point | J1 | J2 | J3 | J4 | J5 | J6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 180° | -70° | -110° | -105° | 180° | 180° |
| B | 180° | -80° | -80° | -105° | 180° | 180° |

Run it from the project root:

```powershell
.\.venv\Scripts\python.exe experiments/e4_arm_joint_motion/move_a_b_a.py
```

The script converts the editable degree values to radians and calls asynchronous
`moveJ` commands at 15°/s with 30°/s² acceleration. It waits for each asynchronous
operation to finish before issuing the next command, preserving the A → B → A
sequence. Each arrival is printed and written to a timestamped file in `results`;
line timestamps are elapsed seconds since the script started.

Running this file commands physical robot motion. It has not been run as part of
creating this experiment.
