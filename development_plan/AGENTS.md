# Agent instructions — deTour scaffolding game controller

## Mission
Build the **Windows PC game-controller application** for the deTour scaffolding exhibition. The PC coordinates one UR10e robot moving three floorboards among four zones; up to four NFC-localized, line-following toy cars; the single USB HID gamepad tested in E2; a USB-connected BLE modem; and four visitor displays. Implement incrementally against available real hardware, using focused dry-run or synthetic substitutes when necessary.

## Read in this order
1. `SYSTEM_SPEC.md` — agreed functional behavior and constraints.
2. `INTERFACES.md` — frozen interface policy, canonical source and approved topology-byte clarification.
3. `MILESTONES.md` — incremental implementation, test gates, available hardware.
4. Authoritative repository: https://github.com/yck011522/line_follower_control ; especially `docs/remote_control_payload.md` and `src/e15/car/`.

## Non-negotiable boundaries
- **Current transport is USB serial to a Bluetooth Low Energy (BLE) modem, then BLE to cars. NOT ESP-NOW.** Earlier ESP-NOW examples in the repository are obsolete experiments.
- **This project's implementation scope is the PC game controller and associated PC tools.** Do not edit, flash, rewrite, or extend the car firmware or modem firmware without the user's explicit approval.
- **Keep the tested E15 PC–modem–car protocol intact.** Preserve seven-byte BLE World State; 21-byte car telemetry; serial framing, widths, byte order, sequence/echo fields, and movement-byte semantics. Only the agreed semantic interpretation of the existing `topology_id` byte may change. Do not silently add a field, a message type, new acknowledgments, or reformat old packets.
- World State packing is `<HB4B` (7 bytes); E15 Robot State packing is `<BHHB4sHBBBhhH` (21 bytes). The three `B` fields after NFC age are line sensors, controller state and line-follower state. The earlier 20-byte Python format was a documentation typo, not a firmware or wire-format change.
- If a requested feature would require changing either firmware or the frozen wire interface (e.g. actual intersection enforcement or a dedicated wireless scanner), **stop that part, explain the conflict, propose the smallest change, and ask the user**. The user anticipates a future firmware iteration, but it is NOT authorized by this document.
- The tested E15 firmware is the implementation truth for current behavior; requirements describe the intended system. Label gaps clearly rather than claiming they are already implemented.
- No movement of the real UR robot without explicit operator authorization, validated poses, a verified clearance procedure, and an appropriate live-mode test plan. The robot's certified safety functions are separate from PC logic. Protective-stop auto-reset policy remains unresolved and must not be silently implemented.
- Do not command live motion using placeholder target positions. Do not treat development-mode screwdriver completion as proof of successful mechanical attachment.

## Working method
- **Before implementing each milestone:** inspect related existing code, identify assumptions, explain the concrete architecture and tests in a short plan, and obtain the user's approval to proceed. In particular, discuss any new firmware/interface expectations and the Rhino NFC registration workflow at its milestone.
- Keep business logic independent of physical I/O and display rendering. Prefer small replaceable adapters (real or synthetic) rather than a comprehensive physics simulator. Ensure one real car can be used while other cars are designated Not Present or fed synthetic test events.
- Preserve working subsystems; avoid unnecessary rewrites. Keep configuration in INI or clearly documented data files. The modem's fixed COM port is read from INI; failed initial opening is fatal, displaying configured and available ports. Reconnect the modem only to that configured port during normal running. These COM rules do not apply to the USB HID gamepad.
- Use Pygame's joystick API and E2's tested input handling. Read each car's `forward_button`, `left_button` and `right_button` from `[car_N_control]` in `config/address.ini`; do not invent a joystick serial protocol or use the legacy `[joystick]` COM metadata as HID identity. Final HID selection and reenable policy still need agreement.
- Keep control loops independent and nonblocking. **The agreed PC control loop polls the gamepad and generates/submits the shared World State together at 50 Hz (20 ms).** E2's 100 Hz experiment is not the production cadence. Display redraw remains independently paced at 30/60 fps; never wait for a display/video frame before processing control. Car telemetry is **20 Hz**; the car's internal motor-control loop is 100 Hz.
- Provide structured timestamped diagnostics; show effective car commands, topology byte, car telemetry age, world-sequence echoes, per-zone owner/reservation state, robot state, and decisions.
- Require observable acceptance checks with log evidence, not merely compilation. Where a physical test is inappropriate or unavailable, use a small synthetic event sequence and say so explicitly.
- Before changing any contract, compare the current published documentation with `src/e15/car` and report differences to the user for approval.

## Scope versus deferred work
The PC application includes device adapters, startup/admin procedures, car command filtering, topology/reservation management, board scheduler, robot task orchestration, video/HUD/minimap, and logging. Separate later workflows include Rhino/Grasshopper map export, NFC registration, and UR target teaching. Their high-level contracts are in the specifications; detailed UI/data structures must be reviewed with the user before implementation.

## Initial task
**M0 is review and planning only:** audit current source and interfaces, identify verified capabilities and intended-but-missing features, propose module boundaries and real-device test strategy, and request approval before implementing M1.
