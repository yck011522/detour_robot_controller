"""Zone tokens, robot claims, evacuation timers, scheduling and the transfer state machine."""

import unittest

from game_controller.app import observe_location
from game_controller.protocol import Telemetry
from game_controller.scheduler import Scheduler
from game_controller.state import Car
from game_controller.transfers import Transfers
from game_controller.zones import HOLD_SECONDS, INACTIVITY_SECONDS, Zones


class ZoneTests(unittest.TestCase):
    def test_tokens_need_the_zones_car_a_board_and_no_robot_claim(self):
        zones = Zones("ABC")
        self.assertTrue(zones.request_token(1, "A"))
        self.assertTrue(zones.request_token(1, "A"))  # Idempotent.
        self.assertFalse(zones.request_token(1, "B"))  # One token per car; never replaced.
        self.assertFalse(zones.request_token(2, "A"))  # Wrong car.
        self.assertFalse(zones.request_token(2, "D"))  # No board.
        self.assertFalse(zones.request_token(3, "C"))  # Cars 3 and 4 never hold tokens.
        self.assertTrue(zones.request_token(2, "C"))
        zones.release_token(1)
        self.assertTrue(zones.claim("B", 1.0))
        self.assertFalse(zones.request_token(1, "B"))  # Closed by the robot.

    def test_topology_mask_tracks_boards_claims_and_admin(self):
        zones = Zones("ABC")
        self.assertEqual(zones.mask(), 0b0111)
        self.assertEqual(zones.mask(admin=True), 0b10111)
        zones.claim("B", 0)
        self.assertEqual(zones.mask(), 0b0101)
        zones.remove_board("A")
        self.assertEqual(zones.mask(), 0b0100)
        zones.release_claim("B")
        zones.add_board("D")
        self.assertEqual(zones.mask(), 0b1110)

    def test_claim_waits_for_acknowledgement_unless_zone_is_empty(self):
        zones = Zones("AB")
        self.assertTrue(zones.claim("A", 5.0))
        self.assertFalse(zones.owned("A"))
        zones.settle(lambda zone, since: since > 9)
        self.assertFalse(zones.owned("A"))
        zones.settle(lambda zone, since: since == 5.0)
        self.assertTrue(zones.owned("A"))
        self.assertTrue(zones.claim("C", 6.0))  # Empty: already closed to cars.
        self.assertTrue(zones.owned("C"))
        self.assertFalse(zones.claim("A", 7.0))

    def test_a_car_token_blocks_the_robot_until_release(self):
        zones = Zones("AB")
        zones.request_token(1, "A")
        self.assertFalse(zones.claim("A", 0))
        zones.release_token(1)
        self.assertTrue(zones.claim("A", 0))

    def test_evacuation_timers(self):
        zones = Zones("A")
        zones.request_token(1, "A")
        for _ in range(int(INACTIVITY_SECONDS) - 1):
            zones.advance(1, False, 1)
        zones.advance(1, True, 1)  # Forward resets only the inactivity timer.
        self.assertFalse(zones.evacuating(1))
        for _ in range(int(INACTIVITY_SECONDS)):
            zones.advance(1, False, 1)
        self.assertTrue(zones.evacuating(1))
        zones.release_token(1)
        self.assertFalse(zones.evacuating(1))
        zones.request_token(1, "A")
        for _ in range(int(HOLD_SECONDS)):
            zones.advance(1, True, 1)
        self.assertTrue(zones.evacuating(1))

    def test_release_only_on_a_distinct_tag_with_neither_zone_attribute(self):
        zones = Zones("A")
        car = Car()
        packet = Telemetry(1, 1, 1, 0, b"\x01\x02\x03\x04", 0, 24, 0, 0, 0, 0, 12000)
        kinds = {"approach": "mapped", "inside": "mapped", "plain": "mapped", "unknown": "unknown",
                 "": "no_read", "ignored": "ignored"}
        tids = {"approach": 7, "inside": 8, "plain": 9}
        tags = {7: {"tid": "T7", "reservezone": "A", "safeexit": "left", "split": "left"},
                8: {"tid": "T8", "inzone": "A"}, 9: {"tid": "T9"}}

        class Resolver:
            def __init__(self):
                self.tags = tags

            def lookup(self, uid):
                return type("Match", (), {"kind": kinds[uid], "tid": tids.get(uid)})()

        class Coins:
            def observe(self, *args):
                return None

        def visit(uid, now):
            car.uid = uid
            observe_location(car, packet, now, Resolver(), Coins(), [None] * 4, zones)

        visit("approach", 0)
        zones.request_token(1, "A")
        visit("inside", 1)
        self.assertEqual(zones.token_zone(1), "A")
        visit("", 2)  # No-read never releases.
        visit("ignored", 3)
        self.assertEqual(zones.token_zone(1), "A")
        visit("approach", 4)  # Another approach tag does not release.
        self.assertEqual(zones.token_zone(1), "A")
        visit("unknown", 5)  # An observed unknown UID is a none/none tag.
        self.assertEqual(zones.token_zone(1), "")
        zones.request_token(1, "A")
        visit("plain", 6)
        self.assertEqual(zones.token_zone(1), "")


class SchedulerTests(unittest.TestCase):
    def test_least_recently_touched_order_and_blocked_zones_keep_their_place(self):
        scheduler = Scheduler()
        self.assertEqual(scheduler.pickup("ABC"), "A")
        self.assertEqual(scheduler.pickup("ABC", blocked={"A"}), "B")
        self.assertEqual(scheduler.order, list("ABCD"))
        scheduler.touch("A")
        self.assertEqual(scheduler.pickup("ABC"), "B")

    def test_the_plate_just_placed_is_never_picked_even_if_it_is_the_only_one_eligible(self):
        scheduler = Scheduler()
        scheduler.placed("C")
        self.assertIsNone(scheduler.pickup("ABC", blocked={"A", "B"}))
        self.assertEqual(scheduler.pickup("ABC", blocked={"A"}), "B")
        scheduler.placed("B")
        self.assertEqual(scheduler.pickup("ABC", blocked={"A"}), "C")  # Eligible again after another placement.

    def test_placement_takes_an_empty_zone_other_than_the_source(self):
        scheduler = Scheduler()
        self.assertEqual(scheduler.placement({"A", "C"}, source="A"), "C")
        self.assertEqual(scheduler.placement({"A", "C"}, source="C"), "A")
        self.assertIsNone(scheduler.placement({"A"}, source="A"))
        scheduler.touch("A")
        self.assertEqual(scheduler.placement({"A", "C"}), "C")  # Held start: least recently touched.


class Harness:
    """A synthetic robot that answers each operation with its events immediately."""

    def __init__(self, arrangement, home="bd_home"):
        self.zones = Zones(zone for zone in arrangement.values() if zone in tuple("ABCD"))
        self.sent, self.saved = [], []
        self.transfers = Transfers(self.zones, Scheduler(), arrangement, home, self.sent.append,
                                   on_change=lambda arrangement: self.saved.append(dict(arrangement)))
        self.running = True
        self.ack = True

    def step(self, now=0.0):
        self.zones.settle(lambda zone, since: self.ack)
        self.transfers.tick(now, self.running)

    def advance_to_operation(self):
        for _ in range(4):  # idle -> claim -> operation
            self.step()
            if self.transfers.state == "busy":
                return self.sent[-1]
        raise AssertionError("No operation started")

    def answer(self):
        """Complete the outstanding operation as the robot process would."""
        _, operation_id, kind, target, loaded = self.sent[-1]
        events = {"pick": ["pickup_completed", "high_clearance_reached", "home_reached"],
                  "place": ["placement_completed", "high_clearance_reached", "home_reached"],
                  "travel": ["home_reached"]}[kind]
        home = target if kind == "travel" else ("ac_home" if target in "AC" else "bd_home")
        for name in events:
            self.transfers.handle((name, operation_id, home) if name == "home_reached" else (name, operation_id))

    def inventory_ok(self):
        active = [place for place in self.transfers.arrangement.values() if place != "spare"]
        return len(active) == 3 and len(set(active)) == 3


class TransferTests(unittest.TestCase):
    LAYOUT = {"P": "A", "Q": "B", "R": "C", "S": "spare"}

    def test_many_transfers_conserve_three_boards_and_never_repick_the_last_placed(self):
        harness = Harness(self.LAYOUT)
        last_destination = None
        for _ in range(120):
            _, _, kind, target, _ = harness.advance_to_operation()
            if kind == "pick":
                self.assertNotEqual(target, last_destination)
            harness.answer()
            if kind == "place":
                last_destination = target
            self.assertTrue(harness.inventory_ok())
            self.assertEqual(set(harness.zones.boards),
                             {z for z in harness.transfers.arrangement.values() if z in tuple("ABCD")})
            self.assertEqual(harness.zones.claims, {})  # Everything released at clearance.
        self.assertGreaterEqual(sum(1 for message in harness.sent if message[2] == "place"), 25)

    def test_travel_happens_only_when_the_zone_belongs_to_the_other_home(self):
        harness = Harness(self.LAYOUT, home="bd_home")
        harness.step()
        self.assertEqual(harness.sent[-1][2:4], ("travel", "ac_home"))  # First pickup: A.
        harness.answer()
        self.assertEqual(harness.advance_to_operation()[2:4], ("pick", "A"))

    def test_robot_waits_for_the_echo_and_does_not_start_while_paused(self):
        harness = Harness(self.LAYOUT, home="ac_home")
        harness.ack = False
        harness.step()
        self.assertEqual(harness.transfers.state, "waiting")
        self.assertFalse(harness.zones.available("A"))  # Closed at once; cars are denied.
        for _ in range(5):
            harness.step()
        self.assertEqual(harness.sent, [])
        harness.ack, harness.running = True, False
        harness.step()
        self.assertEqual(harness.sent, [])  # Admin pause holds the claim without moving.
        harness.running = True
        harness.step()
        self.assertEqual(harness.sent[-1][2:4], ("pick", "A"))

    def test_a_car_token_makes_the_robot_choose_another_zone(self):
        harness = Harness(self.LAYOUT, home="bd_home")
        harness.zones.request_token(1, "A")
        harness.step()
        self.assertEqual((harness.transfers.state, harness.transfers.target), ("waiting", "B"))
        self.assertFalse(harness.zones.request_token(1, "B"))  # Car 1 already holds A; B is closed anyway.

    def test_everything_blocked_waits_indefinitely_then_proceeds_when_a_zone_frees(self):
        harness = Harness({"P": "A", "Q": "C", "R": "B", "S": "spare"}, home="ac_home")
        harness.zones.request_token(1, "A")
        harness.zones.request_token(2, "C")
        harness.transfers.scheduler.placed("B")  # B holds the plate just placed.
        for _ in range(100):
            harness.step()
        self.assertEqual((harness.sent, harness.transfers.state), ([], "idle"))
        harness.zones.release_token(1)
        self.assertEqual(harness.advance_to_operation()[2:4], ("pick", "A"))

    def test_held_board_at_startup_is_placed_in_an_empty_zone_with_the_loaded_flag(self):
        harness = Harness({"P": "gripper", "Q": "B", "R": "C", "S": "spare"}, home="bd_home")
        message = harness.advance_to_operation()  # A is first in queue order and belongs to the other home.
        self.assertEqual((message[2], message[3], message[4]), ("travel", "ac_home", True))
        harness.answer()
        message = harness.advance_to_operation()
        self.assertEqual((message[2], message[3], message[4]), ("place", "A", True))
        harness.answer()
        self.assertIsNone(harness.transfers.held)
        self.assertEqual(harness.transfers.arrangement["P"], "A")

    def test_quit_finishes_the_held_board_and_only_then_reports_done(self):
        harness = Harness(self.LAYOUT, home="ac_home")
        harness.advance_to_operation()
        harness.answer()  # Picked A.
        self.assertIsNotNone(harness.transfers.held)
        harness.transfers.finishing = True
        self.assertFalse(harness.transfers.done)
        while harness.transfers.held:
            harness.advance_to_operation()
            harness.answer()
        self.assertTrue(harness.transfers.done)
        count = len(harness.sent)
        harness.step()
        self.assertEqual(len(harness.sent), count)  # Nothing new is started once empty-handed.

    def test_stale_events_are_ignored_and_a_fault_keeps_the_claim(self):
        harness = Harness(self.LAYOUT, home="ac_home")
        operation_id = harness.advance_to_operation()[1]
        harness.transfers.handle(("pickup_completed", operation_id + 5))
        self.assertIsNone(harness.transfers.held)
        harness.transfers.handle(("fault", operation_id, "tool stalled"))
        self.assertEqual(harness.transfers.state, "fault")
        self.assertIn("A", harness.zones.claims)
        self.assertFalse(harness.transfers.done)
        count = len(harness.sent)
        harness.step()
        self.assertEqual(len(harness.sent), count)

    def test_inventory_changes_are_reported_for_persistence_and_remapping(self):
        harness = Harness(self.LAYOUT, home="ac_home")
        harness.advance_to_operation()
        harness.answer()
        self.assertEqual(len(harness.saved), 1)
        self.assertEqual(harness.saved[0]["P"], "gripper")
        self.assertNotIn("A", harness.zones.boards)


if __name__ == "__main__":
    unittest.main()
