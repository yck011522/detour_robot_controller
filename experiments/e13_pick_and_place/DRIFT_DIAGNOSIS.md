# Sideways drift: 20261109_231536_705205.log

The full cycle completed without a logged fault. Existing OPERATION_SAMPLE
records already include all six force/torque values (N and Nm), not just Fz.
The following are first-to-last sampled displacements in each named action;
they can include manual assistance, pauses and contact, not solely free motion.

| Action | dX mm | dY mm | dZ mm | Mean Fx N | Mean Fy N |
| --- | --- | --- | --- | --- | --- |
| Placement | 1.14 | 0.36 | -20.83 | 0.07 | -0.11 |
| Release M1 / withdraw | 13.94 | 15.45 | 20.43 | 2.76 | 3.54 |
| Pickup M1 | 1.36 | 0.52 | -21.08 | -14.84 | -3.48 |
| Uninstall M2 / withdraw | 4.14 | 14.88 | 20.68 | 0.14 | 0.29 |

Release M1 lateral displacement was about 20.8 mm. Pickup Fx ranged from
-57.42 to +4.41 N; Fy from -35.69 to +6.52 N. Its small net displacement
does not exclude drifting away and being pushed back. These are controller
force estimates, not an independent force sensor measurement of hole contact.

## Interpretation

The command uses task_frame=[0,0,0,0,0,0], type=2: a base-aligned force frame.
Requested Fx/Fy are zero, but X and Y are compliant. This permits lateral motion
to relieve sensed force; it does not constrain the TCP to the taught vertical
line. XY travel checking is currently disabled. Baseline/load/CoG error or
contact can therefore cause lateral drift. Manual guidance changes the readings.
There is not enough evidence to assign the cause to an incorrect Z direction.

A further suspect is payload timing: the software updates payload while the
tool can still be attached to the floor/structure, including just before
withdrawal. UR documents that updating payload resets the internal FT sensor.
This can incorporate contact into the force baseline. A HOME zero alone does
not exclude later baseline changes. No extra tare or payload timing changes
were made in this update; zeroing during contact would obscure the diagnosis.

Sources:
[UR force-mode semantics](https://www.universal-robots.com/manuals/EN/HTML/SW10_15/Content/prod-scriptmanual/all_scripts/force_mode_task_frame_selection_vector.htm)
and [UR payload and FT reset](https://www.universal-robots.com/manuals/EN/PDF/SW5_25/scriptmanualG5/script_directory_Poly5.pdf).

## Next run

LOW is now TARGET +10 mm base Z. Force-mode translation speed limits are
5 mm/s on all compliant axes, including XY; force stays +/-10 N. The ideal
10 mm motion at the speed cap takes 2 seconds; contact, force response and
motor stall can make it slower. Neither a two-second deadline nor a guaranteed
trajectory speed is imposed. The 10 mm overtravel margin remains separate.

Additional diagnostics:
- ACTION_BASELINE: measured pose/wrench and controller timestamp before starting
  a non-joint action's motor. This is context, not a calibration/tare operation.
- PAYLOAD_BEFORE: pose/wrench immediately before setting payload. The existing
  PAYLOAD event identifies the chosen mass and CoG; subsequent baseline/samples
  show the resulting readings with controller timestamps.
- OPERATION_SAMPLE: full wrench, requested wrench, signed XYZ displacement from
  the action start, TCP velocity, lateral force magnitude, task frame, selection
  vector, force type, motor current/PWM and existing limits at 5 Hz.
- UI: measured Fx/Fy versus zero request, and signed X/Y displacement.

Compare before-motor baseline to first moving samples and payload timestamps.
If lateral readings persist while stationary and clear of contact, investigate
load/CoG and sensor baseline. If they appear only on contact or motor operation,
inspect alignment and tool reaction. The log alone cannot separate a manual
push from fixture force; note those intervals when reviewing the next run.
