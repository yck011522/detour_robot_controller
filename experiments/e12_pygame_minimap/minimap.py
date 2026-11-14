"""E12: preview the 700 px cached minimap and four simulated car positions."""

import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

import pygame

# Every baked map layer shares this canvas and the same image origin.
NATIVE_SIZE = (4000, 4000)
FLOOR_ORDER = ("-1", "G", "1", "2", "3", "4", "5", "6", "7")
ASSET_DIRECTORY = Path(__file__).resolve().parents[2] / "asset"
# Preview dimensions are fixed to the selected production minimap size.
MINIMAP_SIZE = 700
# Generated sprites are shared root-level assets, separate from the original map PNGs.
CACHE_DIRECTORY = ASSET_DIRECTORY / "cache" / "minimap"
# Opacity multipliers from 0.0 (invisible) to 1.0 (fully visible), also used by zones.
CURRENT_FLOOR_OPACITY = 1.0
OTHER_FLOOR_OPACITY = 0.20


def load_tag_attributes(asset_directory):
    """Index the three visualization fields by tag number in slider order."""
    with (asset_directory / "map_attributes.csv").open(
        newline="", encoding="utf-8-sig"
    ) as attribute_file:
        return {
            int(row["tid"][1:]): {
                "tid": row["tid"],
                "floor": row["floor"],
                "track": row["track"],
            }
            for row in csv.DictReader(attribute_file)
        }


def load_minimap_assets(asset_directory):
    """Index asset paths; decode layers individually instead of retaining 620 sprites."""
    return {
        "floors": {
            floor: asset_directory / f"Floor{floor}.png" for floor in FLOOR_ORDER
        },
        "tracks": {
            (car_number, floor): path
            for car_number in range(1, 5)
            for floor in FLOOR_ORDER
            if (path := asset_directory / f"Track{car_number}Floor{floor}.png").exists()
        },
        "zones": {zone: asset_directory / f"Zone{zone}.png" for zone in "ABCD"},
        "cars": {
            int(path.stem[1:]): path
            for path in (asset_directory / "Cars").glob("T*.png")
        },
        "coins": {
            int(path.stem[1:]): path
            for path in (asset_directory / "Coins").glob("T*.png")
        },
        "backgrounds": {
            path.name: path
            for path in sorted((asset_directory / "test_background").glob("*.jpg"))
        },
    }


def load_cached_layers(canvas_sizes):
    """Load selected cache sizes and apply configured floor/zone opacity in memory."""
    manifest = json.loads(
        (CACHE_DIRECTORY / "manifest.json").read_text(encoding="utf-8")
    )
    layers = {
        key: (pygame.image.load(str(CACHE_DIRECTORY / key)), offset)
        for key, offset in manifest.items()
        if int(key.split("/", 1)[0]) in canvas_sizes
    }
    for cache_key, (image, offset) in list(layers.items()):
        canvas_size, opacity, relative_name = cache_key.split("/", 2)
        if Path(relative_name).stem.startswith(("Floor", "Zone")):
            for opacity_multiplier in (CURRENT_FLOOR_OPACITY, OTHER_FLOOR_OPACITY):
                configured_alpha = round(255 * opacity_multiplier)
                tinted_image = image.copy()
                # Premultiplied surfaces require scaling RGB and alpha together.
                tinted_image.fill(
                    (configured_alpha,) * 4, special_flags=pygame.BLEND_RGBA_MULT
                )
                layers[f"{canvas_size}/{configured_alpha}/{relative_name}"] = (
                    tinted_image,
                    offset,
                )
    return layers


def compose_minimap(
    tag_attributes,
    assets,
    layers,
    active_car,
    car_tags,
    reserved_zones,
    grouped_coins,
    floor_cache,
):
    """Compose cached static floor stacks and four cars at the fixed minimap size."""
    canvas = pygame.Surface((MINIMAP_SIZE, MINIMAP_SIZE), pygame.SRCALPHA)
    active_floor = tag_attributes[car_tags[active_car - 1]]["floor"]

    def draw_layer(destination, image_path, opacity=255):
        """Blit a prepared sprite at its original map offset."""
        relative_name = image_path.relative_to(ASSET_DIRECTORY).as_posix()
        image, offset = layers[f"{MINIMAP_SIZE}/{opacity}/{relative_name}"]
        destination.blit(image, offset, special_flags=pygame.BLEND_PREMULTIPLIED)

    for floor in FLOOR_ORDER:
        floor_alpha = round(
            255
            * (CURRENT_FLOOR_OPACITY if floor == active_floor else OTHER_FLOOR_OPACITY)
        )
        floor_key = (
            active_car,
            floor,
            floor_alpha,
            tuple(reserved_zones) if floor == "G" else (),
        )
        if floor_key not in floor_cache:
            static_surface = pygame.Surface(
                (MINIMAP_SIZE, MINIMAP_SIZE), pygame.SRCALPHA
            )
            draw_layer(static_surface, assets["floors"][floor], floor_alpha)
            if floor == "G":
                for zone in "ABCD":
                    if zone in reserved_zones:
                        draw_layer(static_surface, assets["zones"][zone], floor_alpha)
            if (active_car, floor) in assets["tracks"]:
                draw_layer(static_surface, assets["tracks"][(active_car, floor)])
            coin_image, coin_offset = grouped_coins[(active_car, floor)]
            static_surface.blit(
                coin_image, coin_offset, special_flags=pygame.BLEND_PREMULTIPLIED
            )
            floor_cache[floor_key] = static_surface
        canvas.blit(
            floor_cache[floor_key], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED
        )
        # Draw the active car last on its floor so it remains visible in overlaps.
        car_order = [number for number in range(1, 5) if number != active_car] + [
            active_car
        ]
        for car_number in car_order:
            tag_number = car_tags[car_number - 1]
            if tag_attributes[tag_number]["floor"] == floor:
                draw_layer(
                    canvas,
                    assets["cars"][tag_number],
                    255 if car_number == active_car else 51,
                )
    return canvas


def create_control_window(tag_attributes, assets):
    """Build four radio/slider rows and shared zone and background controls."""
    control_window = tk.Tk()
    control_window.title("E12 — Minimap controls")
    control_window.geometry("860x470+40+60")
    control_window.columnconfigure(1, weight=1)
    # Tk variables form the editable preview state; tag values are slider indices.
    tag_numbers = sorted(tag_attributes)
    active_car = tk.IntVar(value=1)
    car_tag_indices = []
    car_descriptions = []
    reserved_zones = {zone: tk.BooleanVar(value=False) for zone in "ABCD"}
    background_name = tk.StringVar(value=next(iter(assets["backgrounds"])))
    target_size = tk.IntVar(value=MINIMAP_SIZE)
    # Work time is measured separately from the scheduled 60 Hz refresh interval.
    redraw_time = tk.StringVar(value="Waiting for first redraw")
    rendering_status = tk.StringVar(value="Preparing preview…")

    ttk.Label(control_window, text="Active car").grid(row=0, column=0, padx=12, pady=12)
    ttk.Label(
        control_window, text="Simulated scanned tag (independent of car track)"
    ).grid(row=0, column=1, sticky="w")
    for car_number in range(1, 5):
        # Start each car at the first tag assigned to its own track.
        first_tag = next(
            number
            for number in tag_numbers
            if tag_attributes[number]["track"] == str(car_number)
        )
        tag_index = tk.IntVar(value=tag_numbers.index(first_tag))
        description = tk.StringVar()
        car_tag_indices.append(tag_index)
        car_descriptions.append(description)
        ttk.Radiobutton(
            control_window,
            text=f"Car {car_number}",
            variable=active_car,
            value=car_number,
        ).grid(row=car_number, column=0, padx=12)
        tk.Scale(
            control_window,
            from_=0,
            to=len(tag_numbers) - 1,
            resolution=1,
            orient=tk.HORIZONTAL,
            showvalue=False,
            variable=tag_index,
            length=500,
        ).grid(row=car_number, column=1, sticky="ew", padx=8, pady=8)
        ttk.Label(control_window, textvariable=description, width=25).grid(
            row=car_number, column=2, padx=12
        )

    reservation_frame = ttk.LabelFrame(control_window, text="Zones reserved by robot")
    reservation_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=12)
    for column, zone in enumerate("ABCD"):
        ttk.Checkbutton(
            reservation_frame, text=f"Zone {zone}", variable=reserved_zones[zone]
        ).grid(row=0, column=column, padx=20, pady=8)
    ttk.Label(control_window, text="Background").grid(row=6, column=0, padx=12)
    ttk.Combobox(
        control_window,
        textvariable=background_name,
        state="readonly",
        values=list(assets["backgrounds"]),
    ).grid(row=6, column=1, sticky="w", padx=8)
    ttk.Label(control_window, text="Minimap size").grid(row=7, column=0, padx=12)
    ttk.Label(control_window, text=f"{MINIMAP_SIZE} × {MINIMAP_SIZE}").grid(
        row=7, column=1, sticky="w", padx=8, pady=12
    )
    ttk.Label(control_window, textvariable=rendering_status).grid(
        row=8, column=0, columnspan=3, sticky="w", padx=12, pady=8
    )
    ttk.Label(control_window, text="Redraw time").grid(row=9, column=0, padx=12)
    ttk.Entry(
        control_window, textvariable=redraw_time, state="readonly", width=55
    ).grid(row=9, column=1, columnspan=2, sticky="ew", padx=8, pady=8)
    return control_window, {
        "active_car": active_car,
        "car_tag_indices": car_tag_indices,
        "car_descriptions": car_descriptions,
        "tag_numbers": tag_numbers,
        "reserved_zones": reserved_zones,
        "background_name": background_name,
        "target_size": target_size,
        "rendering_status": rendering_status,
        "redraw_time": redraw_time,
    }


def run_experiment():
    """Refresh at 60 Hz while a worker updates the cached minimap on state changes."""
    tag_attributes = load_tag_attributes(ASSET_DIRECTORY)
    assets = load_minimap_assets(ASSET_DIRECTORY)
    # Decode only the prepared 700 px images once, then assemble per-track coin layers.
    layers = load_cached_layers((MINIMAP_SIZE,))
    grouped_coins = {}
    for car_number in range(1, 5):
        for floor in FLOOR_ORDER:
            combined = pygame.Surface((MINIMAP_SIZE, MINIMAP_SIZE), pygame.SRCALPHA)
            for tag_number, attributes in tag_attributes.items():
                if attributes["floor"] == floor and attributes["track"] == str(
                    car_number
                ):
                    image, offset = layers[
                        f"{MINIMAP_SIZE}/255/Coins/T{tag_number}.png"
                    ]
                    combined.blit(
                        image, offset, special_flags=pygame.BLEND_PREMULTIPLIED
                    )
            coin_bounds = combined.get_bounding_rect()
            grouped_coins[(car_number, floor)] = (
                combined.subsurface(coin_bounds).copy(),
                coin_bounds.topleft,
            )
    floor_cache = {}
    current_static_state = None

    def compose_prepared_scene(active_car, car_tags, reserved_zones, target_size):
        """Time the composition, including rebuilding static layers when needed."""
        nonlocal current_static_state
        composition_started = time.perf_counter()
        static_state = (
            active_car,
            tag_attributes[car_tags[active_car - 1]]["floor"],
            reserved_zones,
        )
        if static_state != current_static_state:
            floor_cache.clear()
            current_static_state = static_state
        image = compose_minimap(
            tag_attributes,
            assets,
            layers,
            active_car,
            car_tags,
            reserved_zones,
            grouped_coins,
            floor_cache,
        )
        return image, (time.perf_counter() - composition_started) * 1000

    pygame.display.init()
    pygame.display.set_caption("E12 — Layered minimap preview")
    preview_size = (900, 800)
    preview_window = pygame.display.set_mode(preview_size)
    control_window, controls = create_control_window(tag_attributes, assets)
    # Only the worker touches composition surfaces; Tk and display stay on this thread.
    composition_worker = ThreadPoolExecutor(max_workers=1)
    pending_composition = None
    submitted_state = None
    displayed_state = None
    current_minimap = None
    last_composition_ms = 0.0
    current_background_name = None
    running = True

    def close_windows():
        """Exit the shared event loop when either window is closed."""
        nonlocal running
        running = False

    control_window.protocol("WM_DELETE_WINDOW", close_windows)

    def refresh_preview():
        """Read controls, submit the latest composition, and display completed work."""
        nonlocal pending_composition, submitted_state, displayed_state
        nonlocal current_minimap, current_background_name, background_surface, last_composition_ms
        frame_started = time.perf_counter()
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (
                event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
            ):
                close_windows()
        if not running:
            control_window.quit()
            return

        car_tags = tuple(
            controls["tag_numbers"][variable.get()]
            for variable in controls["car_tag_indices"]
        )
        for description, tag_number in zip(controls["car_descriptions"], car_tags):
            attributes = tag_attributes[tag_number]
            description.set(
                f"T{tag_number} | Floor {attributes['floor']} | Track {attributes['track'] or '—'}"
            )
        selected_zones = tuple(
            zone
            for zone, variable in controls["reserved_zones"].items()
            if variable.get()
        )
        requested_state = (
            controls["active_car"].get(),
            car_tags,
            selected_zones,
            controls["target_size"].get(),
        )
        if pending_composition is not None and pending_composition.done():
            current_minimap, last_composition_ms = pending_composition.result()
            displayed_state = submitted_state
            pending_composition = None
        if pending_composition is None and requested_state != displayed_state:
            submitted_state = requested_state
            pending_composition = composition_worker.submit(
                compose_prepared_scene, *requested_state
            )
        controls["rendering_status"].set(
            "Composing minimap layers…"
            if pending_composition
            else f"Ready — active car {displayed_state[0]} — {displayed_state[3]} × {displayed_state[3]}"
        )

        preview_draw_started = time.perf_counter()
        background_name = controls["background_name"].get()
        if background_name != current_background_name:
            original_background = pygame.image.load(
                str(assets["backgrounds"][background_name])
            )
            fill_scale = max(
                preview_size[0] / original_background.get_width(),
                preview_size[1] / original_background.get_height(),
            )
            background_surface = pygame.transform.smoothscale(
                original_background,
                (
                    round(original_background.get_width() * fill_scale),
                    round(original_background.get_height() * fill_scale),
                ),
            )
            current_background_name = background_name
        preview_window.blit(
            background_surface,
            background_surface.get_rect(center=preview_window.get_rect().center),
        )
        if current_minimap is not None:
            preview_window.blit(
                current_minimap,
                current_minimap.get_rect(center=preview_window.get_rect().center),
                special_flags=pygame.BLEND_PREMULTIPLIED,
            )
        pygame.display.flip()
        preview_draw_ms = (time.perf_counter() - preview_draw_started) * 1000
        controls["redraw_time"].set(
            f"Minimap: {last_composition_ms:.2f} ms | Preview: {preview_draw_ms:.2f} ms"
        )
        # Include drawing time in the requested frame interval.
        delay_ms = max(
            0, round(1000 * (1 / 60 - (time.perf_counter() - frame_started)))
        )
        control_window.after(delay_ms, refresh_preview)

    # Filled on the first refresh before the first background blit.
    background_surface = None
    try:
        refresh_preview()
        control_window.mainloop()
    finally:
        composition_worker.shutdown(wait=True)
        control_window.destroy()
        pygame.quit()


if __name__ == "__main__":
    run_experiment()
