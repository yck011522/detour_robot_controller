"""Cached 700 px minimap and CSV locations shared by the four player displays."""

import csv
import json
from pathlib import Path

import pygame

# Every baked map layer shares this canvas and the same image origin.
NATIVE_SIZE = (4000, 4000)
FLOOR_ORDER = ("-1", "G", "1", "2", "3", "4", "5", "6", "7")
ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "asset"
# Preview dimensions are fixed to the selected production minimap size.
MINIMAP_SIZE = 700
# Generated sprites are shared root-level assets, separate from the original map PNGs.
CACHE_DIRECTORY = ASSET_DIRECTORY / "cache" / "minimap"
# Opacity multipliers from 0.0 (invisible) to 1.0 (fully visible), also used by zones.
CURRENT_FLOOR_OPACITY = 1.0
OTHER_FLOOR_OPACITY = 0.20


def load_tag_attributes(asset_directory):
    """Read visual tags and registered hexadecimal NFC UIDs at startup."""
    with (asset_directory / "map_attributes.csv").open(
        newline="", encoding="utf-8-sig"
    ) as attribute_file:
        return {
            int(row["tid"][1:]): {
                "tid": row["tid"],
                "floor": row["floor"],
                "track": row["track"],
                "nfc_uid": row["nfc_uid"].strip().lower(),
            }
            for row in csv.DictReader(attribute_file)
        }


def load_minimap_assets(asset_directory):
    """Load cached sprites and group static coins for each car and floor."""
    assets = {
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
    }
    assets["layers"] = load_cached_layers((MINIMAP_SIZE,))
    # Static coins are grouped once; collection and respawning remain deferred.
    assets["grouped_coins"] = {}
    tag_attributes = load_tag_attributes(asset_directory)
    for car_number in range(1, 5):
        for floor in FLOOR_ORDER:
            combined = pygame.Surface((MINIMAP_SIZE, MINIMAP_SIZE), pygame.SRCALPHA)
            for tag_number, attributes in tag_attributes.items():
                if attributes["floor"] == floor and attributes["track"] == str(car_number):
                    image, offset = assets["layers"][f"{MINIMAP_SIZE}/255/Coins/T{tag_number}.png"]
                    combined.blit(image, offset, special_flags=pygame.BLEND_PREMULTIPLIED)
            bounds = combined.get_bounding_rect()
            assets["grouped_coins"][(car_number, floor)] = (
                combined.subsurface(bounds).copy(), bounds.topleft)
    assets["floor_cache"] = {}
    return assets



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
    active_car,
    car_tags,
    reserved_zones=(),
):
    """Compose cached static floor stacks and four cars at the fixed minimap size."""
    layers = assets["layers"]
    grouped_coins = assets["grouped_coins"]
    floor_cache = assets["floor_cache"]
    canvas = pygame.Surface((MINIMAP_SIZE, MINIMAP_SIZE), pygame.SRCALPHA)
    active_tag = car_tags[active_car - 1]
    active_floor = tag_attributes[active_tag]["floor"] if active_tag is not None else None

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
            if tag_number is not None and tag_attributes[tag_number]["floor"] == floor:
                draw_layer(
                    canvas,
                    assets["cars"][tag_number],
                    255 if car_number == active_car else 51,
                )
    return canvas


