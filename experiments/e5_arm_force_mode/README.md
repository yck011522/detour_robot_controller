# E5: downward force mode

`soft_move_down.py` performs this sequence:

1. Move to point A: `180, -70, -110, -90, 90, 180` degrees.
2. Zero the built-in force/torque sensor while stationary at point A and wait 0.2 seconds.
3. Record the TCP pose at point A.
4. Apply 2 kg-force, approximately 19.61 N, along negative Z of the robot base frame.
5. Keep TCP rotation position-controlled while X, Y, and Z translations are compliant.
6. Stop force mode when downward travel reaches 20 mm, radial XY travel reaches
   10 mm, or force-mode duration reaches 30 seconds.
7. Return to point A using `moveJ`.

Run it from the project root:

```powershell
.\.venv\Scripts\python.exe experiments/e5_arm_force_mode/soft_move_down.py
```

The task frame is the robot base frame and force-frame type 2 leaves that frame
unchanged. The selection vector `[1, 1, 1, 0, 0, 0]` makes translation compliant
and keeps all three rotation axes position-controlled. Zero requested X/Y force
allows lateral compliance. Because `forceMode` limits compliant-axis speed rather
than total displacement, the script separately measures displacement from the
starting TCP pose. The 10 mm XY condition uses radial distance
`sqrt(delta_x^2 + delta_y^2)`.

Compliant X/Y translation speed is limited to 10 mm/s and compliant Z translation
speed is limited to 1 mm/s. These values are maximum speeds rather than exact
commanded speeds. The final three force-mode limits allow up to 0.05 rad of
deviation on the position-controlled rotation axes; they do not make those axes
compliant.

Running this file commands physical robot motion. It has not been run as part of
creating this experiment. Negative base-frame Z represents global downward motion
only when the robot's base coordinate system has Z pointing upward in its actual
mounting.
