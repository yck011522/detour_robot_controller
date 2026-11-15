# E14 three-plate validation, 2026-11-15

These are supervised experiment results, not acceptance of game integration or
car/robot zone reservations. No car token manager participates in E14.

The latest reviewed session, `20261115_144326_507178_queue`, ran from 14:43:26
to 15:35:56 Asia/Hong_Kong: 52.50 minutes, including two brief pauses. It used
three plates, all four zones, the 12-directed-transfer queue, 5 deg/s local and
10 deg/s cross-home commands, 0.5-second dwells, -10 N tightening, +5 N M1
placement release and +15 N M2 pickup release. XYZ compliance preceded engaged
motor actuation. Payload/reference were preserved through release and changed
afterwards before retreat. Movement endpoints were not blended.

## Recorded outcome

- Nine completed transfers: B>D, D>B, A>D, C>A, A>C, D>A, C>D, B>C, C>B.
- 27 home-to-home operations and 162 successful actions: 126 joint moves,
  18 tightening actions, 18 releases. All releases reached the +20 mm target;
  none used the +10 mm minimum-height timeout fallback.
- No logged action failures, unconfirmed stops or disconnect errors. Three-plate
  conservation, first-eligible selection and completion-only queue rotation held.
- Two pauses lasted 4.36 and 3.00 seconds. No sub-step started while paused.
  Q overrode the second pause and allowed retreat, then stopped empty at bd_home.
- Final software layout: A=plate_1, B=plate_2, C=empty, D=plate_3; no pending move.
- A>B, B>A and D>C had not run when the session ended. This is nine transfers,
  not a full 12-transfer coverage result or a statistical reliability estimate.

## Numerical observations

| Measurement | Recorded range |
|---|---|
| Tightening duration, excluding the outlier below | 1.55-2.88 s |
| Pickup release duration | 4.20-4.96 s |
| Placement release duration | 4.07-4.18 s |
| Downward tightening depth at stall | 3.35-5.06 mm |
| Upward release displacement after stopping | 20.24-20.49 mm |
| Maximum final joint-position error | 0.00290 deg |
| Maximum orientation change during force actions | 0.0483 deg |

No clear overall timing or depth drift was observed, with only two or three
repetitions per zone. Later placement releases included lateral movement of
6.91 mm in C and 5.39 mm in B. XYZ compliance permits such displacement; its
mechanical significance needs observation rather than a new threshold inferred
from these samples. Force/pose logging was about 5 Hz and may miss brief peaks.

One B placement is an outlier: M2 tightening at 15:34:36 took 23.998 seconds
(`20261115_153435_574772.log`) versus 2.043 seconds for the earlier B placement
with the same test plate. Z progressed slowly around 1-2 mm while the motor
rotated at roughly 60-90 mA, then descended and stalled at the usual ~4.99 mm
depth. Lateral forces oscillated during the prolonged engagement. This is
consistent with delayed engagement/seating, but does not establish its cause.
It preceded the pauses and completed within the configured 30-second limit.

Observed median peak joint speed was 2.58 deg/s for local 5 deg/s commands and
5.17 deg/s for cross-home 10 deg/s commands. External scaling near half speed is
plausible, but the speed-scaling value was not logged. No temperature was logged.

## Reproducing the review

Raw logs, session state, snapshots and generated PNG/CSV/JSON reports are local
artifacts under `results/`, now ignored for new files. Previously committed
evidence is retained. To regenerate the review with the original local logs:

```powershell
.\.venv\Scripts\python.exe -m pip install -r experiments/e14_robot_step_test/requirements-analysis.txt
.\.venv\Scripts\python.exe experiments/e14_robot_step_test/analyze_session.py experiments/e14_robot_step_test/results/20261115_144326_507178_queue
```

The source remains a supervised test utility. Future game integration still needs
the real reservation manager, admin coordination and interrupted-operation
reconciliation. The reviewed evidence does not justify tightening the current
timeouts or automatically recovering from a fault.
