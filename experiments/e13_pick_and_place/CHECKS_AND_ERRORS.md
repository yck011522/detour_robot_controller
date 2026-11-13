# E13 checks and errors

Scope: pick_and_place.py and the E10 TCP/Dashboard/STATUS helpers it calls.
Controller safety errors and native ur_rtde/network exceptions can also propagate.
This is a software check inventory, not a certified safety specification.

## Motion and completion checks

| Check | Condition / setting | Effect |
| --- | --- | --- |
| Connect | Remote control, RUNNING robot, NORMAL safety, no running program | Reject connection on unexpected reply |
| Initial stationarity | Finite six joint velocities; max absolute velocity <= 0.01 rad/s | Reject preparation |
| Taught poses | FK within 1 mm and 1 degree of saved TCP; finite vectors | Reject preparation |
| LOW solution | TARGET +10 mm base Z; IK exists, within 15 degrees per joint of TARGET; controller joint/pose limits pass | Reject preparation |
| Payload | Positive finite mass and finite CoG; setPayload accepted | Fault on invalid/rejected command |
| HOME recognition | Each joint within 0.25 degrees of HOME | Otherwise only direct HOME permitted |
| UI intent | Heartbeat <250 ms old; confirmation, focus and held mouse over button | Release/focus loss pauses; stale heartbeat faults |
| Robot feedback | Safety mode 1, robot mode 7, control program running, finite Q/Qd/TCP; timestamp changes within 200 ms | Fault |
| RTDE watchdog | Minimum 3 Hz; worker normally cycles every 20 ms | Controller may stop on lost input; fault UI continues kicks |
| Joint completion | Within 0.25 degrees per joint; async operation finished; max velocity <0.005 rad/s | Complete only when all hold |
| Z travel | Between min(start Z, target Z)-10 mm and max(start Z, target Z)+10 mm | Z overtravel fault, either direction |
| XY travel | Distance from action start <=10 mm | Temporarily DISABLED by CHECK_XY_DRIFT=False |
| Orientation | Rotation difference from action start <=3 degrees | Fault |
| Z arrival | Withdrawal absolute Z error <=0.5 mm | Completes withdrawal only; no hold-drift check on tightening |
| Force command | +/-10 N base Z, 5 mm/s on compliant translations; 2 degrees deviation on held rotations | Controller command rejection faults; these are command settings, not measured-force thresholds |
| Motor status | Other motor IDLE; own motor expected TIGHTENING/LOOSENING or permitted tightening STALLED | Unexpected states fault; loosening stall faults |
| Tightening completion | Fresh STALLED observation; TARGET Z is not required | No fixed tightening timeout; stall survives pause/resume |
| Withdrawal completion | LOW Z within 0.5 mm | Height is the release proxy; no independent disengagement sensor |
| Initial M1 grip | TCP stays within 1 mm / 1 degree of starting pose; fresh stall | Fault on drift; no force or movement enabled |
| STOP | Tool IDLE/IDLE and PWM 0; stop robot before forceModeStop; 3 stationary velocity samples, fresh finite feedback, within 3 seconds | Aggregate failures as STOP UNCONFIRMED; attempt robot stop even if tool stop fails |
| Recovery | 3 fresh stationary samples within 2 seconds; NORMAL/RUNNING; restart script only if stopped; halt again and restore watchdog | Retry/Skip require operator request; neither starts movement |
| Sequence gates | Fresh click after completion; complete paused tool action before changing direction | Block progression with UI message |
| Direct HOME | Both confirmations and held button | Abandons routine; restart required to reset attachment state |
| Operator Skip | Selected operation required; recovery checks and payload update | Advances by operator override, not measured completion |

The 10 mm Z bound permits limited travel opposite the requested direction too.
Resistance could pull the robot below its start; the old log cannot establish
whether that occurred. The bound remains active until explicitly changed.
No measured TCP-force cutoff or overall force-operation timeout is implemented.
Native blocking calls/OS delays can still miss a watchdog deadline; the fault-loop
fix addresses the demonstrated intentional gap, not all communication failures.

## Fault handling

Ordinary operation exceptions retain the selected step, stop tool/robot and display
a red fault. Retry rebuilds observations and bounds from the current pose. Skip
advances the selected step after recovery and applies its payload convention.
The fault screen keeps watchdog communication alive if the script is running;
it never clears protective stops. A failed fault-screen kick is logged once as
FAULT_WATCHDOG_ERROR. Session exit attempts halt and then stops/disconnects the
control script. Exceptions may be reported as FAULT, STOP_UNCONFIRMED or CLOSE_ERROR.

C207A0 is a controller error, not the Python Z bound. UR documents missed RTDE
watchdog updates as a cause:
[UR remote operation guide](https://s3-eu-west-1.amazonaws.com/ur-support-site/123978/Remote%20Operation%20Guide%20V1.1.pdf).

## Explicit E13 exceptions

The following inventory is extracted from the current source. Braces represent
values inserted at runtime. File read/configuration errors and native library
exceptions are additional possible failures.

| Function | Exception expression |
| --- | --- |
| connect | `RuntimeError('Robot must be stationary')` |
| connect | `RuntimeError('Taught TCP mismatch: ' + name)` |
| connect | `RuntimeError('LOW has no IK solution')` |
| connect | `RuntimeError('LOW IK branch mismatch')` |
| connect | `RuntimeError('Controller limit check failed: ' + name)` |
| connect | `RuntimeError('Invalid initial joint feedback')` |
| connect | `RuntimeError('Invalid payload calibration')` |
| connect | `RuntimeError('Cannot enable robot watchdog')` |
| payload | `RuntimeError('Payload command rejected')` |
| stop_tool | `RuntimeError('Tool STOP unconfirmed')` |
| maintain_watchdog | `RuntimeError('Watchdog failed')` |
| check | `RuntimeError('Robot mode/safety/program changed')` |
| check | `RuntimeError('Watchdog failed')` |
| check | `RuntimeError('Robot feedback stale')` |
| check | `RuntimeError('Invalid robot feedback')` |
| halt | `RuntimeError('forceModeStop rejected')` |
| halt | `RuntimeError('Cannot confirm stop from stale/invalid feedback')` |
| halt | `RuntimeError('Robot stop unconfirmed')` |
| halt | `RuntimeError('STOP UNCONFIRMED: ' + '; '.join(errors))` |
| recover | `RuntimeError('Robot is still moving; recovery not ready')` |
| recover | `RuntimeError('Robot feedback is not updating')` |
| recover | `RuntimeError('Restore NORMAL safety/RUNNING on the pendant first')` |
| recover | `RuntimeError('Control script restart failed')` |
| recover | `RuntimeError('Cannot restore watchdog')` |
| start | `RuntimeError('moveJ rejected')` |
| force | `RuntimeError('forceMode rejected')` |
| tick | `RuntimeError('XY travel bound exceeded')` |
| tick | `RuntimeError(f'Z overtravel: actual {pose[2] * 1000:.3f} mm, allowed [{lower * 1000:.3f}, {upper * 1000:.3f}] mm')` |
| tick | `RuntimeError('Orientation drift')` |
| tick | `RuntimeError('Other motor unexpectedly active')` |
| tick | `RuntimeError('Loosening motor stalled before withdrawal completed')` |
| tick | `RuntimeError('Unexpected motor state: ' + own)` |
| tick | `RuntimeError('Robot drifted during initial grip confirmation')` |
| tick | `RuntimeError('Z drifted out of holding tolerance')` |
| worker | `RuntimeError('No selected step to skip; choose a direction first')` |
| worker | `RuntimeError('UI heartbeat expired')` |

## E10 helper failures reachable from E13

- TCP connect/read/write errors, response deadline (120 ms for tool replies),
  connection closed, overlong reply, robot/tool error reply, unexpected expected reply.
- Unexpected Dashboard greeting or unexpected response to preparation checks.
- Malformed STATUS (five tokens, OK prefix, recognized motor states), invalid
  integer values, current outside [-1500,1500] mA or PWM outside [0,100].
- Tool STOP confirmation additionally requires both motors IDLE and PWM 0.

E13 does not call E10's standalone power/brake/configuration, voltage-setting,
serial or diagnostic-test routines; their unrelated exceptions are excluded.

Live UI and OPERATION_SAMPLE logs now expose Z error, goal, requested/feedback Fz and last polled motor state. The Z-hold transition and its drift exception have been removed.
