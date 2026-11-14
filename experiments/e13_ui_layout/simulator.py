"""E13 simulator controls, example exports, and benchmark harness; no production device access."""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import statistics
import sys
import time
import tkinter as tk
from tkinter import ttk

import pygame

from layout_settings import (REPOSITORY_DIRECTORY, RESULT_DIRECTORY, LAYOUT_SIZE, SPLIT_EXITS, SPLIT_MODES, VIDEO_BLINK_SECONDS)
from game_controller.ui_layout import load_layout_assets, compose_layout_overlay


# Cache preparation is production-owned and shared with the future controller UI.
sys.path.insert(0, str(REPOSITORY_DIRECTORY))
from game_controller.minimap_cache import ensure_minimap_cache


from game_controller import minimap


def create_control_window(tag_attributes, background_names):
    """Create independent car/tag/counter controls and simulator-only HUD inputs."""
    window = tk.Tk()
    window.title("E13 - Layout simulator")
    window.geometry("1280x780+30+50")
    window.columnconfigure(1, weight=1)
    controls = {
        "active_car": tk.IntVar(value=1),
        "tag_indices": [],
        "coins": [],
        # Pause flags belong to each car, independently of the active display selection.
        "battery_low": [tk.BooleanVar(value=False) for car_number in range(4)],
        "telemetry_lost": [tk.BooleanVar(value=False) for car_number in range(4)],
        "line_lost": [tk.BooleanVar(value=False) for car_number in range(4)],
        "tag_numbers": sorted(tag_attributes),
        "tag_labels": [],
        "video_present": tk.BooleanVar(value=True),
        "telemetry_fresh": tk.BooleanVar(value=True),
        "forward": tk.BooleanVar(value=True),
        "left": tk.BooleanVar(value=False),
        "right": tk.BooleanVar(value=True),
        "left_speed": tk.DoubleVar(value=0.18),
        "right_speed": tk.DoubleVar(value=0.18),
        "battery": tk.DoubleVar(value=10.2),
        "sensors": [tk.BooleanVar(value=index in (3, 4)) for index in range(8)],
        "split_mode": tk.StringVar(value="Left / right"),
        "selected_exit": tk.StringVar(value="right"),
        "background": tk.StringVar(value="Reference image"),
        "animate_background": tk.BooleanVar(value=False),
        "timing": tk.StringVar(value="Preparing layout..."),
        "map_status": tk.StringVar(value="Preparing minimap..."),
    }
    ttk.Label(window, text="Active display").grid(row=0, column=0, padx=12, pady=12)
    ttk.Label(window, text="Tag location (independent of own track)").grid(
        row=0, column=1, sticky="w"
    )
    ttk.Label(window, text="Coins collected").grid(row=0, column=3, padx=12)
    ttk.Label(window, text="Low battery").grid(row=0, column=5, padx=8)
    ttk.Label(window, text="Telemetry lost").grid(row=0, column=6, padx=8)
    ttk.Label(window, text="Line lost").grid(row=0, column=7, padx=8)
    initial_tags = (101, 33, 154, 262)
    for car_number in range(1, 5):
        tag_index = tk.IntVar(
            value=controls["tag_numbers"].index(initial_tags[car_number - 1])
        )
        counter = tk.IntVar(value=1250 if car_number == 1 else 0)
        description = tk.StringVar()
        controls["tag_indices"].append(tag_index)
        controls["coins"].append(counter)
        controls["tag_labels"].append(description)
        ttk.Radiobutton(
            window,
            text=f"Car {car_number:02d}",
            variable=controls["active_car"],
            value=car_number,
        ).grid(row=car_number, column=0)
        tk.Scale(
            window,
            from_=0,
            to=len(controls["tag_numbers"]) - 1,
            resolution=1,
            showvalue=False,
            orient=tk.HORIZONTAL,
            variable=tag_index,
            length=480,
        ).grid(row=car_number, column=1, sticky="ew", padx=8)
        ttk.Label(window, textvariable=description, width=22).grid(
            row=car_number, column=2
        )
        ttk.Spinbox(window, from_=0, to=99999999, textvariable=counter, width=9).grid(
            row=car_number, column=3, padx=8
        )
        ttk.Button(
            window,
            text="Scan +1",
            command=lambda value=counter: value.set(value.get() + 1),
        ).grid(row=car_number, column=4, padx=8)
        ttk.Checkbutton(window, variable=controls["battery_low"][car_number - 1]).grid(
            row=car_number, column=5
        )
        ttk.Checkbutton(window, variable=controls["telemetry_lost"][car_number - 1]).grid(
            row=car_number, column=6
        )
        ttk.Checkbutton(window, variable=controls["line_lost"][car_number - 1]).grid(
            row=car_number, column=7
        )
    reservation_frame = ttk.LabelFrame(window, text="Robot-reserved zones")
    reservation_frame.grid(row=5, column=0, columnspan=8, sticky="ew", padx=12, pady=10)
    controls["zones"] = {zone: tk.BooleanVar(value=zone in "AC") for zone in "ABCD"}
    for column, (zone, variable) in enumerate(controls["zones"].items()):
        ttk.Checkbutton(reservation_frame, text=f"Zone {zone}", variable=variable).grid(
            row=0, column=column, padx=20, pady=8
        )
    status_frame = ttk.LabelFrame(window, text="Simulated signal and input state")
    status_frame.grid(row=6, column=0, columnspan=8, sticky="ew", padx=12, pady=6)
    for column, (key, caption) in enumerate(
        (
            ("video_present", "Video present (blink)"),
            ("telemetry_fresh", "Telemetry fresh"),
            ("forward", "Forward input"),
            ("left", "Left input"),
            ("right", "Right input"),
        )
    ):
        ttk.Checkbutton(status_frame, text=caption, variable=controls[key]).grid(
            row=0, column=column, padx=12, pady=10
        )
    telemetry_frame = ttk.LabelFrame(
        window, text="Telemetry values - accepted only while fresh"
    )
    telemetry_frame.grid(row=7, column=0, columnspan=8, sticky="ew", padx=12, pady=6)
    for column, (key, caption, lower, upper, increment) in enumerate(
        (
            ("left_speed", "Left wheel m/s", -2, 2, 0.01),
            ("right_speed", "Right wheel m/s", -2, 2, 0.01),
            ("battery", "Battery V", 0, 16, 0.1),
        )
    ):
        ttk.Label(telemetry_frame, text=caption).grid(
            row=0, column=column * 2, padx=12, pady=8
        )
        ttk.Spinbox(
            telemetry_frame,
            from_=lower,
            to=upper,
            increment=increment,
            textvariable=controls[key],
            width=8,
        ).grid(row=0, column=column * 2 + 1, padx=8)
    sensor_frame = ttk.LabelFrame(
        window, text="Line sensors - bit 7 at left; stale retains last accepted pattern"
    )
    sensor_frame.grid(row=8, column=0, columnspan=8, sticky="ew", padx=12, pady=6)
    for index, variable in enumerate(controls["sensors"]):
        ttk.Checkbutton(sensor_frame, text=str(7 - index), variable=variable).grid(
            row=0, column=index, padx=18, pady=8
        )
    split_frame = ttk.LabelFrame(window, text="Approaching split")
    split_frame.grid(row=9, column=0, columnspan=8, sticky="ew", padx=12, pady=6)
    ttk.Combobox(
        split_frame,
        values=list(SPLIT_MODES),
        textvariable=controls["split_mode"],
        state="readonly",
        width=22,
    ).grid(row=0, column=0, padx=12, pady=10)
    ttk.Label(split_frame, text="Selected exit").grid(row=0, column=1, padx=12)
    controls["exit_chooser"] = ttk.Combobox(
        split_frame,
        values=SPLIT_EXITS["leftright"],
        textvariable=controls["selected_exit"],
        state="readonly",
        width=15,
    )
    controls["exit_chooser"].grid(row=0, column=2, padx=12)
    ttk.Label(window, text="Background").grid(row=10, column=0, padx=12, pady=12)
    ttk.Combobox(
        window,
        values=list(background_names),
        textvariable=controls["background"],
        state="readonly",
        width=24,
    ).grid(row=10, column=1, sticky="w", padx=8)
    ttk.Checkbutton(
        window,
        text="Animate test background (not a live stream)",
        variable=controls["animate_background"],
    ).grid(row=10, column=2, columnspan=3, sticky="w")
    ttk.Label(window, textvariable=controls["map_status"]).grid(
        row=11, column=0, columnspan=8, sticky="w", padx=12, pady=8
    )
    ttk.Entry(
        window, textvariable=controls["timing"], state="readonly", width=110
    ).grid(row=12, column=0, columnspan=8, sticky="ew", padx=12, pady=8)
    ttk.Label(
        window,
        text="F12 in preview: save screenshot   |   Escape / close either window: exit   |   Simulator counters are not persisted.",
    ).grid(row=13, column=0, columnspan=8, padx=12, pady=8)
    return window, controls


def run_experiment(snapshot=False, benchmark=False):
    """Run one windowed layout, or export deterministic examples without a simulator."""
    ensure_minimap_cache(target_sizes=(minimap.MINIMAP_SIZE,))
    pygame.display.init()
    screen = pygame.display.set_mode(
        LAYOUT_SIZE, pygame.HIDDEN if snapshot or benchmark else 0
    )
    pygame.display.set_caption("E13 - UI layout experiment")
    assets = load_layout_assets(include_test_backgrounds=True)
    tag_attributes = minimap.load_tag_attributes(minimap.ASSET_DIRECTORY)
    map_assets = minimap.load_minimap_assets(minimap.ASSET_DIRECTORY)
    floor_cache = map_assets["floor_cache"]
    previous_static_state = None

    def compose_map(active_car, tags, zones):
        """Measure map rebuilds on the worker; retain only the current static configuration."""
        nonlocal previous_static_state
        started = time.perf_counter()
        static_state = (
            active_car,
            tag_attributes[tags[active_car - 1]]["floor"],
            zones,
        )
        if static_state != previous_static_state:
            floor_cache.clear()
            previous_static_state = static_state
        image = minimap.compose_minimap(
            tag_attributes,
            map_assets,
            active_car,
            tags,
            zones,
        )
        return image, (time.perf_counter() - started) * 1000

    state = {
        "present": True,
        "active_car": 1,
        "floor": "1",
        "tid": "T101",
        "coins": 1250,
        "video_present": True,
        "telemetry_fresh": True,
        "battery_low": False,
        "line_lost": False,
        "forward": True,
        "left": False,
        "right": True,
        "left_speed": 0.18,
        "right_speed": 0.18,
        "battery": 10.2,
        "sensor_mask": 0b00011000,
        "split": "leftright",
        "selected_exit": "right",
    }
    initial_tags = (101, 33, 154, 262)
    map_surface, composition_ms = compose_map(1, initial_tags, ("A", "C"))
    state["floor"] = tag_attributes[initial_tags[0]]["floor"]
    RESULT_DIRECTORY.mkdir(exist_ok=True)
    background = assets["backgrounds"]["Reference image"][0]
    if snapshot or benchmark:
        # A compact contact sheet makes all six generated panel states reviewable together.
        contact_sheet = pygame.Surface((582, 1050))
        contact_sheet.fill((150, 150, 150))
        for row, (split_type, exits) in enumerate(SPLIT_EXITS.items()):
            for column, selected_exit in enumerate(exits):
                contact_sheet.blit(
                    assets["images"][f"split_{split_type}_{selected_exit}"],
                    (15 + column * 286, 12 + row * 350),
                    special_flags=pygame.BLEND_PREMULTIPLIED,
                )
                state.update(split=split_type, selected_exit=selected_exit)
                overlay = compose_layout_overlay(assets, state, map_surface, 0)
                screen.blit(background, (0, 0))
                screen.blit(overlay, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
                pygame.image.save(
                    screen,
                    str(RESULT_DIRECTORY / f"layout_{split_type}_{selected_exit}.png"),
                )
        pygame.image.save(contact_sheet, str(RESULT_DIRECTORY / "split_variants.png"))
        state.update(split="leftright", selected_exit="right", telemetry_fresh=False)
        overlay = compose_layout_overlay(assets, state, map_surface, 0)
        screen.blit(background, (0, 0))
        screen.blit(overlay, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
        pygame.image.save(screen, str(RESULT_DIRECTORY / "layout_stale.png"))
        for pause_reason in ("battery_low", "telemetry_lost", "line_lost"):
            state.update(telemetry_fresh=pause_reason != "telemetry_lost", battery_low=pause_reason == "battery_low", line_lost=pause_reason == "line_lost")
            overlay = compose_layout_overlay(assets, state, map_surface, 0)
            screen.blit(background, (0, 0))
            screen.blit(overlay, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
            pygame.image.save(screen, str(RESULT_DIRECTORY / f"layout_paused_{pause_reason}.png"))
        state.update(telemetry_fresh=True, battery_low=False, line_lost=False)
        overlay = compose_layout_overlay(assets, state, map_surface, 0)
        if benchmark:
            results = {}
            for rebuild in (False, True):
                durations = []
                state["telemetry_fresh"] = True
                for index in range(330):
                    started = time.perf_counter()
                    if rebuild:
                        state["coins"] = 1250 + index % 2
                        overlay = compose_layout_overlay(
                            assets, state, map_surface, index / 60
                        )
                    screen.blit(background, (0, 0))
                    screen.blit(
                        overlay, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED
                    )
                    pygame.display.flip()
                    if index >= 30:
                        durations.append((time.perf_counter() - started) * 1000)
                results["rebuild_hud_every_frame" if rebuild else "reuse_hud"] = {
                    "median_ms": statistics.median(durations),
                    "p95_ms": sorted(durations)[284],
                    "max_ms": max(durations),
                    "samples": len(durations),
                }
            results["scope"] = (
                "Single hidden 1920x1080 software surface; no decoding, scanout, or map rebuild in timed loop"
            )
            (RESULT_DIRECTORY / "benchmark.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )
            print(json.dumps(results, indent=2), flush=True)
        pygame.quit()
        return

    window, controls = create_control_window(tag_attributes, assets["backgrounds"])
    executor = ThreadPoolExecutor(max_workers=1)
    pending_map = None
    submitted_map_state = displayed_map_state = (1, initial_tags, ("A", "C"))
    previous_overlay_key = None
    overlay = None
    last_telemetry = (0.18, 0.18, 10.2, 0b00011000)
    started_at = time.perf_counter()
    overlay_ms = 0.0
    running = True

    def close_windows():
        """Let the shared loop release both windows and finish any in-flight map work."""
        nonlocal running
        running = False

    def refresh():
        """Snapshot Tk values, accept fresh telemetry, and reuse unchanged map/HUD layers."""
        nonlocal pending_map, submitted_map_state, displayed_map_state, map_surface, composition_ms
        nonlocal previous_overlay_key, overlay, last_telemetry, overlay_ms
        frame_started = time.perf_counter()
        elapsed = frame_started - started_at
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
            ):
                close_windows()
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F12:
                pygame.image.save(
                    screen, str(RESULT_DIRECTORY / "interactive_preview.png")
                )
        if not running:
            window.quit()
            return
        car_number = controls["active_car"].get()
        tags = tuple(
            controls["tag_numbers"][variable.get()]
            for variable in controls["tag_indices"]
        )
        for description, tag_number in zip(controls["tag_labels"], tags):
            attributes = tag_attributes[tag_number]
            description.set(
                f"T{tag_number} | Floor {attributes['floor']} | Track {attributes['track'] or '-'}"
            )
        zones = tuple(
            zone for zone, variable in controls["zones"].items() if variable.get()
        )
        requested_map_state = (car_number, tags, zones)
        if pending_map is not None and pending_map.done():
            map_surface, composition_ms = pending_map.result()
            displayed_map_state = submitted_map_state
            pending_map = None
            previous_overlay_key = None
        if pending_map is None and requested_map_state != displayed_map_state:
            submitted_map_state = requested_map_state
            pending_map = executor.submit(compose_map, *requested_map_state)
        fresh = controls["telemetry_fresh"].get() and not controls["telemetry_lost"][car_number - 1].get()
        if fresh:
            last_telemetry = (
                controls["left_speed"].get(),
                controls["right_speed"].get(),
                controls["battery"].get(),
                sum(
                    (1 << (7 - index))
                    for index, variable in enumerate(controls["sensors"])
                    if variable.get()
                ),
            )
        tag_number = tags[car_number - 1]
        selected_split = SPLIT_MODES[controls["split_mode"].get()]
        split_type = (
            next(row["split"] for row in csv_rows if row["tid"] == f"T{tag_number}")
            if selected_split == "auto"
            else selected_split
        )
        exits = SPLIT_EXITS[split_type] if split_type else ()
        controls["exit_chooser"].configure(values=exits)
        selected_exit = controls["selected_exit"].get()
        if exits and selected_exit not in exits:
            selected_exit = exits[0]
            controls["selected_exit"].set(selected_exit)
        state.update(
            active_car=car_number,
            floor=tag_attributes[tag_number]["floor"],
            tid=f"T{tag_number}",
            coins=controls["coins"][car_number - 1].get(),
            video_present=controls["video_present"].get(),
            telemetry_fresh=fresh,
            battery_low=controls["battery_low"][car_number - 1].get(),
            line_lost=controls["line_lost"][car_number - 1].get(),
            forward=controls["forward"].get(),
            left=controls["left"].get(),
            right=controls["right"].get(),
            left_speed=last_telemetry[0],
            right_speed=last_telemetry[1],
            battery=last_telemetry[2],
            sensor_mask=last_telemetry[3],
            split=split_type,
            selected_exit=selected_exit,
        )
        blink_phase = (
            int(elapsed / VIDEO_BLINK_SECONDS) % 2 if state["video_present"] else 0
        )
        overlay_key = (tuple(state.items()), blink_phase)
        if overlay_key != previous_overlay_key:
            overlay_started = time.perf_counter()
            overlay = compose_layout_overlay(assets, state, map_surface, elapsed)
            overlay_ms = (time.perf_counter() - overlay_started) * 1000
            previous_overlay_key = overlay_key
        draw_started = time.perf_counter()
        still, moving = assets["backgrounds"][controls["background"].get()]
        (
            screen.blit(moving, (-80 + round(70 * math.sin(elapsed * 0.3)), -45))
            if controls["animate_background"].get()
            else screen.blit(still, (0, 0))
        )
        screen.blit(overlay, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
        pygame.display.flip()
        draw_ms = (time.perf_counter() - draw_started) * 1000
        frame_ms = (time.perf_counter() - frame_started) * 1000
        controls["map_status"].set(
            "Composing minimap..."
            if pending_map
            else f"Ready - Car {car_number:02d} - Floor {state['floor']} - 700 px minimap"
        )
        controls["timing"].set(
            f"Map: {composition_ms:.2f} ms | HUD rebuild: {overlay_ms:.2f} ms | Draw: {draw_ms:.2f} ms | Loop work: {frame_ms:.2f} ms"
        )
        window.after(max(0, round(1000 / 60 - frame_ms)), refresh)

    # CSV split fields are used only for the optional auto-preview of approaching splits.
    import csv

    with (minimap.ASSET_DIRECTORY / "map_attributes.csv").open(
        encoding="utf-8-sig", newline=""
    ) as attribute_file:
        csv_rows = list(csv.DictReader(attribute_file))
    window.protocol("WM_DELETE_WINDOW", close_windows)
    try:
        refresh()
        window.mainloop()
    finally:
        executor.shutdown(wait=True)
        window.destroy()
        pygame.quit()
