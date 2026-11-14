"""Synthetic intersection filtering, departure locking and driving gate checks."""

from dataclasses import replace
import unittest

from game_controller.navigation import Navigation, validate_tags
from game_controller.protocol import Telemetry
from game_controller.state import Car


def approach(split="leftright", **extra):
    return {"tid": "T1", "split": split, **extra}


class NavigationTests(unittest.TestCase):
    def test_all_split_choices_and_conflicting_input(self):
        inputs = ((True, False), (False, False), (False, True), (True, True))
        expected = {"left": ("left", "right", "right", "right"),
                    "right": ("left", "left", "right", "left"),
                    "leftright": ("left", "left", "right", "left")}
        for split, edges in expected.items():
            nav = Navigation()
            nav.observe("tag", 0, 0, approach(split))
            for buttons, edge in zip(inputs, edges):
                with self.subTest(split=split, buttons=buttons):
                    self.assertEqual(nav.steering(*buttons, 0), edge)
                    expected_exit = "straight" if (split, edge) in (
                        ("left", "right"), ("right", "left")) else edge
                    self.assertEqual(nav.selected_exit, expected_exit)

    def test_strict_reported_age_boundary_and_same_uid_never_unlocks(self):
        nav = Navigation()
        nav.observe("tag", 0, 0, approach())
        nav.steering(True, False, 0)
        nav.observe("tag", 100, 1, approach())
        self.assertFalse(nav.locked)
        self.assertEqual(nav.steering(False, True, 1), "right")
        nav.observe("tag", 101, 2, approach())
        self.assertTrue(nav.locked)
        self.assertEqual(nav.steering(True, False, 2), "right")
        nav.observe("tag", 0, 3, approach())
        self.assertEqual(nav.steering(True, False, 3), "right")
        self.assertTrue(nav.locked)
        nav.observe("second-approach", 0, 4, approach())
        self.assertFalse(nav.locked)
        self.assertEqual(nav.steering(True, False, 4), "left")

    def test_age_advances_between_packets_and_only_new_uid_clears(self):
        nav = Navigation()
        nav.observe("tag", 0, 0, approach())
        nav.steering(False, True, 0)
        self.assertEqual(nav.steering(False, True, .100), "right")
        self.assertFalse(nav.locked)
        self.assertEqual(nav.steering(True, False, .101), "right")
        self.assertTrue(nav.locked)
        nav.observe("", 65535, 1)
        self.assertEqual(nav.steering(True, False, 1), "right")
        nav.observe("unknown-but-observed", 0, 2)
        self.assertFalse(nav.locked)
        self.assertEqual(nav.steering(True, False, 2), "")

    def test_new_tag_replaces_complete_context_and_can_revisit(self):
        nav = Navigation()
        nav.observe("tag", 0, 0, approach())
        nav.steering(False, True, 0)
        nav.steering(True, False, 1)
        nav.observe("merge", 0, 2, {"tid": "T2", "mergeedge": "left"})
        self.assertEqual(nav.steering(False, True, 2), "left")
        self.assertEqual((nav.split, nav.selected_exit, nav.locked), ("", "", False))
        nav.observe("tag", 0, 3, approach())
        self.assertEqual(nav.steering(True, False, 3), "left")
        nav.observe("ordinary", 0, 4, {"tid": "T3"})
        self.assertEqual(nav.steering(True, False, 4), "")
        self.assertEqual(nav.mergeedge, "")

    def test_first_observation_already_old_uses_neutral_default(self):
        nav = Navigation()
        nav.observe("tag", 200, 0, approach("left"))
        self.assertEqual(nav.steering(True, False, 0), "right")
        self.assertTrue(nav.locked)

    def test_zone_approaches_force_safeexit_without_reservations(self):
        for safeexit in ("left", "right"):
            nav = Navigation()
            nav.observe("zone", 0, 0, approach(reservezone="A", safeexit=safeexit))
            self.assertEqual(nav.steering(True, False, 0), safeexit)
            self.assertEqual(nav.steering(False, True, 1), safeexit)

    def test_merge_forces_edge_and_ordinary_road_clears_turns(self):
        for edge in ("left", "right"):
            nav = Navigation()
            nav.observe("merge", 0, 0, {"tid": "T1", "mergeedge": edge})
            for left, right in ((True, False), (False, True), (False, False), (True, True)):
                self.assertEqual(nav.steering(left, right, 0), edge)
        self.assertEqual(Navigation().steering(True, False, 0), "")

    def test_map_validation_and_adjustable_lock_threshold(self):
        for tag in (approach(mergeedge="left"), approach("invalid"),
                    approach(reservezone="A"), approach(safeexit="invalid"),
                    approach(reservezone="E", safeexit="left")):
            with self.assertRaises(ValueError):
                validate_tags({1: tag})
        nav = Navigation(lock_age_ms=200)
        nav.observe("tag", 150, 0, approach())
        self.assertFalse(nav.locked)
        nav.steering(False, True, 0)
        self.assertEqual(nav.steering(True, False, .051), "right")
        self.assertTrue(nav.locked)


class FilterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.car = Car()
        self.packet = Telemetry(1, 1, 1, 0, b"abcd", 0, 24, 0, 0, 0, 0, 12000)
        self.car.accept(self.packet, 0, 1, {1: (1, 0, 0)}, .5)
        self.car.navigation.observe("61626364", 0, 0, approach())

    def command(self, now=0, **changes):
        options = dict(raw=(True, False, True), now=now, telemetry_timeout=.5,
                       echo_timeout=.5, modem_ok=True, joystick_ok=True,
                       admin=False, selected=1, car_id=1,
                       keyboard=(True, True, False, False, False))
        options.update(changes)
        return self.car.command(**options)

    def test_forward_independent_of_locked_edge_and_admin_bypasses_filter(self):
        self.assertEqual(self.command(), 19)
        self.assertEqual(self.command(.101, raw=(False, True, False)), 17)
        self.assertEqual(self.command(.102, raw=(True, True, False)), 19)
        self.assertEqual(self.command(.103, admin=True), 11)
        self.assertEqual(self.command(.104, admin=True,
                                     keyboard=(False, True, False, False, True)), 8)
        self.assertEqual(self.command(.105), 19)  # MANUAL did not clear the lock.

    def test_admin_auto_bypasses_center_merge_and_zone_forcing(self):
        cases = (({}, 3), ({"mergeedge": "right"}, 19),
                 (approach(reservezone="A", safeexit="right"), 19))
        for index, (tag, player_command) in enumerate(cases):
            with self.subTest(tag=tag):
                self.car.navigation.observe(str(index), 0, 0, tag)
                self.assertEqual(self.command(raw=(True, True, False)), player_command)
                self.assertEqual(self.command(admin=True), 11)
                self.assertEqual(self.command(admin=True,
                                             keyboard=(True, False, True, False, False)), 19)
                self.assertEqual(self.command(admin=True,
                                             keyboard=(True, True, True, False, False)), 3)

    def test_admin_keyboard_does_not_change_unlocked_player_selection(self):
        self.assertEqual(self.command(), 19)
        self.assertEqual(self.command(.05, admin=True), 11)
        self.assertEqual(self.car.navigation.edge, "right")
        self.assertEqual(self.command(.101, admin=True), 11)
        self.assertTrue(self.car.navigation.locked)
        self.assertEqual(self.command(.11, raw=(True, True, False)), 19)

    def test_all_stop_gates_override_navigation_without_clearing_choice(self):
        self.command()
        self.command(.101)
        for options in ({"stop_only": True}, {"modem_ok": False},
                        {"joystick_ok": False}, {"admin": True, "selected": 2},
                        {"now": 1}, {"echo_timeout": .01}):
            with self.subTest(options=options):
                self.assertEqual(self.command(.11, **options) if "now" not in options
                                 else self.command(**options), 1)
                self.assertEqual(self.car.navigation.edge, "right")
        self.car.packet = replace(self.car.packet, controller=13)
        self.assertEqual(self.command(.12), 1)
        self.car.packet = replace(self.car.packet, controller=0)
        self.assertEqual(self.command(.13), 19)
