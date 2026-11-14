"""Verify exported artwork and scripted HUD transitions without desktop automation."""

import csv
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as element_tree

# SDL's dummy driver creates in-memory surfaces without opening a desktop window.
os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame

import simulator as ui_layout
import layout_settings


def verify_layout():
    """Check all split states, image regions, telemetry retention, and simulator transitions."""
    asset_directory = ui_layout.REPOSITORY_DIRECTORY / "asset"
    # Rebuildable cache outputs may change on first launch; original artwork must not.
    source_paths = [path for path in asset_directory.rglob("*")
                    if path.is_file() and not path.is_relative_to(asset_directory / "cache")]
    source_paths += [ui_layout.REPOSITORY_DIRECTORY / "reference" / name for name in ("layout.svg", "layout.jpg")]
    source_hashes = {path: hashlib.sha256(path.read_bytes()).digest() for path in source_paths}
    checks = []
    ui_layout.run_experiment(snapshot=True)
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    assets = ui_layout.load_layout_assets()

    def orange_pixel(image, position):
        """Recognize the reference's orange accent independently of exact edge antialiasing."""
        red, green, blue, alpha = image.get_at(position)
        return red > 240 and 100 < green < 135 and blue < 50 and alpha > 200

    arrow_tips = {"left": (77, 145), "straight": (133, 118), "right": (189, 145)}
    for split_type, exits in ui_layout.SPLIT_EXITS.items():
        examples = []
        for selected_exit in exits:
            name = f"split_{split_type}_{selected_exit}"
            vector = element_tree.parse(layout_settings.UI_ASSET_DIRECTORY / f"{name}.svg").getroot()
            assert vector.attrib["viewBox"] == "0 0 266 326"
            panel = assets["images"][name]
            assert panel.get_size() == (266, 326)
            assert panel.get_at((0, 0)).a == 0
            assert orange_pixel(panel, arrow_tips[selected_exit]), name
            other_exit = next(direction for direction in exits if direction != selected_exit)
            assert not orange_pixel(panel, arrow_tips[other_exit]), name
            example = pygame.image.load(str(ui_layout.RESULT_DIRECTORY / f"layout_{split_type}_{selected_exit}.png"))
            assert example.get_size() == (1920, 1080)
            examples.append(example)
        assert pygame.image.tobytes(examples[0], "RGB") != pygame.image.tobytes(examples[1], "RGB")
    checks.append("Six valid SVG/PNG split variants, selected arrows, transparency, and 1080p exports")

    # SVG font geometry and runtime font metrics must stay inside the reference header.
    title = assets["label"]("Construction Imagined Otherwise", "orbitron", 20, (0, 0, 0))
    assert 1424 + title.get_width() < 1862
    checks.append("Orbitron exhibit title fits within the top bar")

    attributes = ui_layout.minimap.load_tag_attributes(ui_layout.minimap.ASSET_DIRECTORY)
    tag_numbers = sorted(attributes)

    def variable(initial_value):
        """Provide only the Tk variable interface consumed by the refresh loop."""
        result = SimpleNamespace(value=initial_value)
        result.get = lambda: result.value
        result.set = lambda value: setattr(result, "value", value)
        return result

    controls = {
        "active_car": variable(1), "tag_numbers": tag_numbers,
        "tag_indices": [variable(tag_numbers.index(number)) for number in (101, 33, 154, 262)],
        "coins": [variable(1250), variable(0), variable(0), variable(0)],
        "battery_low": [variable(False) for car_number in range(4)],
        "telemetry_lost": [variable(False) for car_number in range(4)],
        "line_lost": [variable(False) for car_number in range(4)],
        "tag_labels": [variable("") for number in range(4)],
        "zones": {zone: variable(zone in "AC") for zone in "ABCD"},
        "video_present": variable(True), "telemetry_fresh": variable(True),
        "forward": variable(True), "left": variable(False), "right": variable(True),
        "left_speed": variable(.18), "right_speed": variable(.18), "battery": variable(10.2),
        "sensors": [variable(index in (3, 4)) for index in range(8)],
        "split_mode": variable("Left / right"), "selected_exit": variable("right"),
        "exit_chooser": SimpleNamespace(configure=lambda **values: None),
        "background": variable("Reference image"), "animate_background": variable(False),
        "map_status": variable(""), "timing": variable(""),
    }
    callbacks = {}
    rendered_states = []
    rendered_images = []
    original_composer = ui_layout.compose_layout_overlay

    def capture_overlay(layout_assets, state, map_surface, elapsed):
        """Record actual refresh-loop output while retaining the real renderer."""
        image = original_composer(layout_assets, state, map_surface, elapsed)
        rendered_states.append(state.copy())
        rendered_images.append(image.copy())
        return image

    def scripted_mainloop():
        """Exercise the real refresh callback with deterministic values and no OS input."""
        assert rendered_states[-1]["sensor_mask"] == 0b00011000
        fresh_image = rendered_images[-1]
        controls["telemetry_fresh"].set(False)
        controls["battery"].set(12.6)
        controls["left_speed"].set(.77)
        for sensor in controls["sensors"]:
            sensor.set(False)
        controls["sensors"][0].set(True)
        callbacks["refresh"]()
        stale_state = rendered_states[-1]
        stale_image = rendered_images[-1]
        assert not stale_state["telemetry_fresh"]
        assert stale_state["battery"] == 10.2
        assert stale_state["sensor_mask"] == 0b00011000
        retained_with_pause = fresh_image.copy()
        retained_with_pause.blit(assets["pause_overlays"]["telemetry_lost"], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
        for rectangle in ((790, 950, 355, 40), (1350, 945, 115, 48)):
            assert pygame.image.tobytes(retained_with_pause.subsurface(rectangle), "RGBA") == pygame.image.tobytes(stale_image.subsurface(rectangle), "RGBA")
        wheel_rectangle = (650, 947, 85, 45)
        assert pygame.image.tobytes(fresh_image.subsurface(wheel_rectangle), "RGBA") != pygame.image.tobytes(stale_image.subsurface(wheel_rectangle), "RGBA")
        checks.append("Stale wheel-speed dashes, pixel-identical retained sensor pattern and battery")

        controls["battery"].set(7.2)
        controls["sensors"][7].set(True)
        callbacks["refresh"]()
        assert rendered_states[-1]["battery"] == 10.2
        assert rendered_states[-1]["sensor_mask"] == 0b00011000
        controls["telemetry_fresh"].set(True)
        callbacks["refresh"]()
        assert rendered_states[-1]["battery"] == 7.2
        assert rendered_states[-1]["sensor_mask"] == 0b10000001
        checks.append("Edits while stale are ignored; fresh telemetry accepts the latest values")

        controls["split_mode"].set("Auto from tag")
        with (ui_layout.minimap.ASSET_DIRECTORY / "map_attributes.csv").open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        for split_type in ui_layout.SPLIT_EXITS:
            row = next(row for row in rows if row["split"] == split_type)
            controls["tag_indices"][0].set(tag_numbers.index(int(row["tid"][1:])))
            callbacks["refresh"]()
            assert rendered_states[-1]["split"] == split_type
            assert rendered_states[-1]["selected_exit"] in ui_layout.SPLIT_EXITS[split_type]
        checks.append("CSV-driven previews for all three split types and valid exit selection")

        for car_number in range(1, 5):
            controls["active_car"].set(car_number)
            controls["coins"][car_number - 1].set(100 + car_number)
            callbacks["refresh"]()
            assert rendered_states[-1]["active_car"] == car_number
            assert rendered_states[-1]["coins"] == 100 + car_number
        checks.append("Four active car views with independent tag positions and counters")
        controls["active_car"].set(1)
        controls["battery_low"][0].set(True)
        callbacks["refresh"]()
        assert rendered_states[-1]["battery_low"] and rendered_states[-1]["telemetry_fresh"]
        assert rendered_images[-1].get_at((960, 400)).a > 0
        controls["active_car"].set(2)
        callbacks["refresh"]()
        assert not rendered_states[-1]["battery_low"] and rendered_states[-1]["telemetry_fresh"]
        assert rendered_images[-1].get_at((960, 400)).a == 0
        controls["active_car"].set(1)
        controls["telemetry_lost"][0].set(True)
        callbacks["refresh"]()
        assert not rendered_states[-1]["telemetry_fresh"]
        controls["battery_low"][0].set(False)
        controls["telemetry_lost"][0].set(False)
        callbacks["refresh"]()
        assert rendered_states[-1]["telemetry_fresh"]
        checks.append("Per-car low-battery and telemetry-lost controls, isolated to the selected display")
        controls["line_lost"][0].set(True)
        callbacks["refresh"]()
        assert rendered_states[-1]["line_lost"] and rendered_states[-1]["telemetry_fresh"]
        assert rendered_images[-1].get_at((960, 400)).a > 0
        controls["active_car"].set(2)
        callbacks["refresh"]()
        assert not rendered_states[-1]["line_lost"]
        assert rendered_images[-1].get_at((960, 400)).a == 0
        controls["line_lost"][0].set(False)
        checks.append("Reported line-lost overlay is independent of sensor dots and isolated to its car")
        controls["split_mode"].set("Hidden")
        controls["video_present"].set(False)
        callbacks["refresh"]()
        assert rendered_states[-1]["split"] == ""
        assert not rendered_states[-1]["video_present"]
        assert "Map:" in controls["timing"].get() and "Draw:" in controls["timing"].get()
        checks.append("Hidden split, video-off state, and redraw timing updates")
        callbacks["WM_DELETE_WINDOW"]()
        callbacks["refresh"]()

    # This substitutes in-memory controls, not an existing Tk or desktop window.
    simulated_window = SimpleNamespace(
        protocol=lambda name, callback: callbacks.update({name: callback}),
        after=lambda delay, callback: callbacks.update(refresh=callback),
        mainloop=scripted_mainloop, quit=lambda: None, destroy=lambda: None,
    )
    with patch.object(ui_layout, "create_control_window", return_value=(simulated_window, controls)), \
            patch.object(ui_layout, "compose_layout_overlay", side_effect=capture_overlay):
        ui_layout.run_experiment()

    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    last_state = rendered_states[-1].copy()
    last_state["video_present"] = True
    first_phase = original_composer(assets, last_state, None, 0)
    second_phase = original_composer(assets, last_state, None, layout_settings.VIDEO_BLINK_SECONDS)
    assert first_phase.get_at((85, 49)) != second_phase.get_at((85, 49))
    last_state["video_present"] = False
    first_phase = original_composer(assets, last_state, None, 0)
    second_phase = original_composer(assets, last_state, None, layout_settings.VIDEO_BLINK_SECONDS)
    assert first_phase.get_at((85, 49)) == second_phase.get_at((85, 49))
    checks.append("Video dot blinks only while simulated video is present")
    last_state["sensor_mask"] = 0b00011000
    sensor_overlay = original_composer(assets, last_state, None, 0)
    assert tuple(sensor_overlay.get_at((938, 970))) == (*layout_settings.GRAY, 255)
    assert sensor_overlay.get_at((812, 970)).a < 255
    assert not any(name.startswith("sensor_") for name in assets["images"])
    sensor_region = sensor_overlay.subsurface((795, 953, 328, 34))
    light_preview = pygame.Surface(sensor_region.get_size())
    light_preview.fill("white")
    light_preview.blit(sensor_region, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
    for horizontal in range(light_preview.get_width()):
        for vertical in range(light_preview.get_height()):
            pixel = light_preview.get_at((horizontal, vertical))
            assert all(pixel[channel] >= layout_settings.GRAY[channel] - 1 for channel in range(3))
    pygame.image.save(light_preview, str(ui_layout.RESULT_DIRECTORY / "line_sensors.png"))
    checks.append("Procedural gray sensor circles, hollow inactive centers, and no dark antialias fringe on white")
    last_state.update(telemetry_fresh=False, battery_low=True)
    both_pauses = original_composer(assets, last_state, None, 0)
    last_state["battery_low"] = False
    telemetry_only = original_composer(assets, last_state, None, 0)
    assert pygame.image.tobytes(both_pauses, "RGBA") == pygame.image.tobytes(telemetry_only, "RGBA")
    last_state["line_lost"] = True
    all_pauses = original_composer(assets, last_state, None, 0)
    assert pygame.image.tobytes(all_pauses, "RGBA") == pygame.image.tobytes(telemetry_only, "RGBA")
    last_state.update(telemetry_fresh=True, battery_low=True)
    battery_and_line = original_composer(assets, last_state, None, 0)
    last_state["line_lost"] = False
    battery_only = original_composer(assets, last_state, None, 0)
    assert pygame.image.tobytes(battery_and_line, "RGBA") == pygame.image.tobytes(battery_only, "RGBA")
    for reason, image in assets["pause_overlays"].items():
        assert image.get_size() == layout_settings.LAYOUT_SIZE
        assert image.get_at((0, 0)).a == 0
    checks.append("Native-resolution transparent pause PNGs; telemetry lost takes priority when both conditions apply")
    pygame.quit()
    assert all(hashlib.sha256(path.read_bytes()).digest() == digest for path, digest in source_hashes.items())
    checks.append(f"All {len(source_hashes)} source asset/reference hashes unchanged")
    report = {"passed": True, "method": "In-memory SDL dummy driver, exported image assertions, scripted refresh callbacks; no desktop input",
              "checks": checks}
    (ui_layout.RESULT_DIRECTORY / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    verify_layout()
