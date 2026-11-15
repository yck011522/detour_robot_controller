"""Zone ownership: car tokens, robot claims, evacuation timers and the availability byte.

Pure state with no device I/O. A zone is available to its car only when a board
is physically there and the robot has not claimed it. Car 1 competes for A/B
and Car 2 for C/D; Cars 3 and 4 never hold tokens.
"""

CAR_ZONES = {"A": 1, "B": 1, "C": 2, "D": 2}
INACTIVITY_SECONDS = 10.0  # No forward input while holding a token.
HOLD_SECONDS = 60.0  # Total time holding a token.


class Zones:
    def __init__(self, boards=()):
        self.boards = set(boards)
        self.tokens = {}  # car -> zone
        self.claims = {}  # zone -> {"time": claim time, "owned": closure acknowledged}
        self.holds = {}  # car -> {"idle": s, "total": s, "evacuating": bool}

    def available(self, zone):
        return zone in self.boards and zone not in self.claims

    def mask(self, admin=False):
        """Topology byte: bits 0-3 zone availability A-D, bit 4 admin mode."""
        return sum(1 << index for index, zone in enumerate("ABCD") if self.available(zone)) | (16 if admin else 0)

    def token_zone(self, car):
        return self.tokens.get(car, "")

    def request_token(self, car, zone):
        """Grant is idempotent; a car holds at most one token and never replaces it."""
        if self.tokens.get(car) == zone:
            return True
        if car in self.tokens or CAR_ZONES.get(zone) != car or not self.available(zone):
            return False
        self.tokens[car] = zone
        self.holds[car] = {"idle": 0.0, "total": 0.0, "evacuating": False}
        return True

    def release_token(self, car):
        self.holds.pop(car, None)
        return self.tokens.pop(car, None)

    def advance(self, car, forward, dt):
        """Run the evacuation timers; callers skip this in admin mode."""
        hold = self.holds.get(car)
        if hold is None:
            return
        hold["total"] += dt
        hold["idle"] = 0.0 if forward else hold["idle"] + dt
        if hold["idle"] >= INACTIVITY_SECONDS or hold["total"] >= HOLD_SECONDS:
            hold["evacuating"] = True

    def evacuating(self, car):
        return bool(self.holds.get(car, {}).get("evacuating"))

    def claim(self, zone, now):
        """Robot request. Denied while a car holds the token; an empty zone is owned at once."""
        if zone in self.claims or zone in self.tokens.values():
            return False
        self.claims[zone] = {"time": now, "owned": zone not in self.boards}
        return True

    def settle(self, acknowledged):
        """Grant pending claims once acknowledged(zone, claim_time) is true."""
        for zone, claim in self.claims.items():
            if not claim["owned"] and acknowledged(zone, claim["time"]):
                claim["owned"] = True

    def owned(self, zone):
        return bool(self.claims.get(zone, {}).get("owned"))

    def release_claim(self, zone):
        self.claims.pop(zone, None)

    def remove_board(self, zone):
        self.boards.discard(zone)

    def add_board(self, zone):
        self.boards.add(zone)
