"""Pure three-plate transfer queue; no hardware, files, or timing."""

from dataclasses import dataclass, field

ZONES = "abcd"
ALL_TRANSFERS = {(a, b) for a in ZONES for b in ZONES if a != b}


def parse_order(text):
    pairs = [tuple(endpoint.strip().lower() for endpoint in part.split(">")) for part in text.split(",")]
    if len(pairs) != 12 or set(pairs) != ALL_TRANSFERS:
        raise ValueError("Transfer order must contain each of the 12 directed pairs exactly once")
    return pairs


@dataclass
class TransferQueue:
    zones: dict
    queue: list
    home: str
    held: str | None = None
    pending: tuple | None = None
    pending_plate: str | None = None
    counts: dict = field(default_factory=dict)

    def __post_init__(self):
        self.queue = [tuple(pair) for pair in self.queue]
        self.pending = tuple(self.pending) if self.pending else None
        if set(self.zones) != set(ZONES) or self.home not in ("ac_home", "bd_home"):
            raise ValueError("Invalid zone layout or home")
        if len(self.queue) != 12 or set(self.queue) != ALL_TRANSFERS:
            raise ValueError("Queue must contain all directed pairs exactly once")
        self.counts = {a+">"+b: int(self.counts.get(a+">"+b, 0)) for a,b in self.queue}
        if any(value < 0 for value in self.counts.values()):
            raise ValueError("Transfer counts cannot be negative")
        self.validate_inventory()
        if self.pending:
            if self.pending not in ALL_TRANSFERS or not self.pending_plate:
                raise ValueError("Invalid pending transfer")
            source, destination = self.pending
            if self.held:
                if self.held != self.pending_plate or self.zones[source] or self.zones[destination]:
                    raise ValueError("Held plate does not match pending transfer")
            elif self.zones[source] != self.pending_plate or self.zones[destination] is not None:
                raise ValueError("Pending pickup requires its plate at source and an empty destination")
        elif self.held or self.pending_plate:
            raise ValueError("A held plate requires a pending transfer")

    def validate_inventory(self):
        plates = [value for value in self.zones.values() if value is not None]
        if self.held is not None:
            plates.append(self.held)
        if any(not isinstance(plate, str) or not plate.strip() for plate in plates):
            raise ValueError("Plate identities must be nonempty strings")
        if len(plates) != 3 or len(set(plates)) != 3:
            raise ValueError("Exactly three distinct plates must exist across zones and gripper")

    def choose(self):
        if self.held or self.pending:
            raise ValueError("Finish the pending transfer before selecting another")
        skipped = []
        for source, destination in self.queue:
            if not self.zones[source]:
                skipped.append(dict(transfer=source+">"+destination, reason="source empty"))
            elif self.zones[destination]:
                skipped.append(dict(transfer=source+">"+destination, reason="destination occupied"))
            else:
                self.pending = (source, destination)
                self.pending_plate = self.zones[source]
                return self.pending, skipped
        raise RuntimeError("No eligible transfer")

    def pickup_completed(self):
        if not self.pending or self.held:
            raise ValueError("No pickup is pending")
        source, destination = self.pending
        if self.zones[source] != self.pending_plate or self.zones[destination] is not None:
            raise ValueError("Pickup inventory changed")
        self.held = self.zones[source]
        self.zones[source] = None
        self.validate_inventory()

    def placement_completed(self):
        if not self.pending or self.held != self.pending_plate:
            raise ValueError("No held plate for pending placement")
        destination = self.pending[1]
        if self.zones[destination] is not None:
            raise ValueError("Placement destination occupied")
        self.zones[destination], self.held = self.held, None
        self.validate_inventory()

    def finish_transfer(self):
        if not self.pending or self.held or self.zones[self.pending[1]] != self.pending_plate:
            raise ValueError("Placement is not complete")
        pair = self.pending
        self.queue.remove(pair)
        self.queue.append(pair)
        self.counts[">".join(pair)] += 1
        self.pending = self.pending_plate = None
        return pair
