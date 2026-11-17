"""Least-recently-touched pickup order. Never picks up the plate just placed."""


class Scheduler:
    def __init__(self, order="ABCD"):
        self.order = list(order)
        self.last_placed = None

    def touch(self, zone):
        self.order.remove(zone)
        self.order.append(zone)

    def placed(self, zone):
        self.touch(zone)
        self.last_placed = zone

    def pickup(self, occupied, blocked=()):
        """First occupied zone in queue order, skipping car-held and just-placed zones.

        Returns None to wait: skipped zones keep their queue position, and the
        just-placed plate is never taken even when nothing else is eligible.
        """
        for zone in self.order:
            if zone in occupied and zone not in blocked and zone != self.last_placed:
                return zone
        return None

    def placement(self, empty, source=None, blocked=()):
        """First empty zone in queue order other than the one just picked from."""
        for zone in self.order:
            if zone in empty and zone != source and zone not in blocked:
                return zone
        return None
