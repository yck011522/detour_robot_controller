"""Main-thread robot logic: pick a transfer, claim zones, command the robot process, follow events.

Board identities live in `arrangement` (board -> zone letter, "gripper" or "spare"),
the same structure the startup questions confirm and persist.
"""

from .robot.worker import home_for


class Transfers:
    def __init__(self, zones, scheduler, arrangement, home, send, log=lambda *args, **kwargs: None,
                 on_change=lambda arrangement: None):
        self.zones, self.scheduler, self.home = zones, scheduler, home
        self.arrangement = dict(arrangement)
        self.send, self.log, self.on_change = send, log, on_change
        self.state = "idle"  # idle | waiting (for the closure echo) | busy | fault
        self.target = None  # zone being claimed or worked
        self.operation = None  # (id, kind, target)
        self.source = None  # Zone just picked from; never the placement target.
        self.finishing = False  # Quit requested: complete the held plate, start nothing else.
        self.fault = ""
        self.step = ""
        self.next_id = 1

    @property
    def held(self):
        return next((board for board, place in self.arrangement.items() if place == "gripper"), None)

    def occupied(self):
        return {place: board for board, place in self.arrangement.items() if place in tuple("ABCD")}

    @property
    def done(self):
        """Safe to exit: at home, nothing running and nothing in the gripper."""
        return self.state == "idle" and self.held is None

    def begin(self, kind, target):
        operation_id, self.next_id = self.next_id, self.next_id + 1
        self.operation = (operation_id, kind, target)
        self.state, self.step = "busy", ""
        self.send(("operation", operation_id, kind, target, self.held is not None))
        self.log("ROBOT_OPERATION", id=operation_id, kind=kind, target=target)

    def tick(self, now, running):
        """Advance one decision. `running` is false while the robot must stay paused."""
        if not running:
            return
        if self.state == "waiting":
            if self.zones.owned(self.target):
                self.begin("pick", self.target)
            return
        if self.state != "idle":
            return
        blocked = set(self.zones.tokens.values())
        if self.held:
            occupied = self.occupied()
            zone = self.scheduler.placement({z for z in "ABCD" if z not in occupied}, self.source, blocked)
            if zone is None:
                return
            if self.home != home_for(zone):
                return self.begin("travel", home_for(zone))
            if self.zones.claim(zone, now):
                self.begin("place", zone)
            return
        if self.finishing:
            return
        zone = self.scheduler.pickup(set(self.occupied()), blocked)
        if zone is None:
            return
        if self.home != home_for(zone):
            return self.begin("travel", home_for(zone))
        if self.zones.claim(zone, now):
            self.state, self.target = "waiting", zone
            self.log("ROBOT_CLAIM", zone=zone)

    def handle(self, event):
        name, operation_id, *values = event
        if self.operation is None or operation_id != self.operation[0]:
            self.log("ROBOT_EVENT_IGNORED", robot_event=event)
            return
        _, kind, target = self.operation
        self.log("ROBOT_EVENT", robot_event=event)
        if name == "step":
            self.step = values[0]
        elif name == "pickup_completed":
            board = self.occupied()[target]
            self.arrangement[board] = "gripper"
            self.zones.remove_board(target)
            self.scheduler.touch(target)
            self.source = target
            self.on_change(self.arrangement)
        elif name == "placement_completed":
            self.arrangement[self.held] = target
            self.zones.add_board(target)
            self.scheduler.placed(target)
            self.source = None
            self.on_change(self.arrangement)
        elif name == "high_clearance_reached":
            self.zones.release_claim(target)
        elif name == "home_reached":
            self.home = values[0]
            self.operation, self.state, self.step = None, "idle", ""
        elif name == "fault":
            # Claims stay held: the physical state is unknown until the operator reconciles it.
            self.state, self.fault = "fault", values[0]

    def status(self):
        if self.state == "fault":
            return f"Robot FAULT: {self.fault}"
        if self.state == "waiting":
            return f"Robot: waiting for cars to acknowledge zone {self.target} closure"
        if self.state == "busy":
            _, kind, target = self.operation
            return f"Robot: {kind} {target} - {self.step}" if self.step else f"Robot: {kind} {target}"
        return f"Robot: idle at {self.home}"
