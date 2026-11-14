"""Accepted 1080p player HUD: settings, artwork, and premultiplied composition."""

from pathlib import Path

import pygame

# Runtime artwork lives beside the production package.
REPOSITORY_DIRECTORY = Path(__file__).resolve().parents[1]
UI_ASSET_DIRECTORY = REPOSITORY_DIRECTORY / "asset" / "ui_layout"
# This photograph is for simulator previews, not for the live video path.
REFERENCE_BACKGROUND_PATH = REPOSITORY_DIRECTORY / "asset" / "test_background" / "e13_reference.png"
LAYOUT_SIZE = (1920, 1080)
MINIMAP_POSITION = (110, 170)
SPLIT_POSITION = (1599, 530)
BACKGROUND_VEIL_ALPHA = 194
VIDEO_BLINK_SECONDS = 0.5
ORANGE = (255, 116, 31)
INK = (34, 38, 45)
GRAY = (103, 109, 118)
# Sensor circles are drawn procedurally, then antialiased in premultiplied space.
LINE_SENSOR_RADIUS = 14
LINE_SENSOR_OUTLINE_WIDTH = 2
LINE_SENSOR_ANTIALIAS_SCALE = 4
FONT_DIRECTORY = REPOSITORY_DIRECTORY / "asset" / "Font" / "static"
FONT_PATHS = {
    "orbitron": FONT_DIRECTORY / "Orbitron-Regular.ttf",
    "orbitron_bold": FONT_DIRECTORY / "Orbitron-Bold.ttf",
    "english": Path("C:/Windows/Fonts/segoeui.ttf"),
    "chinese": Path("C:/Windows/Fonts/msjhbd.ttc"),
}
# CSV split names describe the optional turn(s); other splits include straight ahead.
SPLIT_EXITS = {
    "left": ("left", "straight"),
    "right": ("straight", "right"),
    "leftright": ("left", "right"),
}
EXIT_LABELS = {
    "left": ("左轉", "LEFT"),
    "straight": ("直行", "STRAIGHT"),
    "right": ("右轉", "RIGHT"),
}
SPLIT_MODES = {
    "Auto from tag": "auto",
    "Hidden": "",
    "Left / straight": "left",
    "Straight / right": "right",
    "Left / right": "leftright",
}

# The renderer knows floor order without importing an experiment or its Tkinter GUI.
FLOOR_ORDER = ("-1", "G", "1", "2", "3", "4", "5", "6", "7")


def load_layout_assets(include_test_backgrounds=False):
    """Load premultiplied artwork, cache static typography, and prepare background choices."""
    pygame.font.init()
    assets = {
        "images": {
            path.stem: pygame.image.load(str(path)).convert_alpha().premul_alpha()
            for path in UI_ASSET_DIRECTORY.glob("*.png")
        },
        "fonts": {},
        "text": {},
        "backgrounds": {},
    }
    # Load the supplied full-screen transparent pause artwork once, without resizing.
    assets["pause_overlays"] = {
        "battery_low": pygame.image.load(
            str(REPOSITORY_DIRECTORY / "asset/Game Paused Battery Low.png")
        ).convert_alpha().premul_alpha(),
        "telemetry_lost": pygame.image.load(
            str(REPOSITORY_DIRECTORY / "asset/Game Paused Telemetry Lost.png")
        ).convert_alpha().premul_alpha(),
        "line_lost": pygame.image.load(
            str(REPOSITORY_DIRECTORY / "asset/Game Paused Line Lost.png")
        ).convert_alpha().premul_alpha(),
    }

    def label(message, font_name, size, color):
        """Share font objects and immutable rendered strings between HUD frames."""
        font_key = (font_name, size)
        if font_key not in assets["fonts"]:
            assets["fonts"][font_key] = pygame.font.Font(
                str(FONT_PATHS[font_name]), size
            )
        key = (message, font_name, size, tuple(color))
        if key not in assets["text"]:
            assets["text"][key] = (
                assets["fonts"][font_key]
                .render(message, True, color)
                .convert_alpha()
                .premul_alpha()
            )
        return assets["text"][key]

    assets["label"] = label
    static = assets["images"]["hud_frame"].copy()
    static_labels = [
        ("機械人", "chinese", 16, INK, (133, 25)),
        ("Robot", "english", 13, INK, (138, 48)),
        ("收集到的金幣", "chinese", 16, INK, (425, 25)),
        ("Coins Collected", "english", 13, INK, (425, 48)),
        ("樓層", "chinese", 16, INK, (860, 25)),
        ("LEVEL", "english", 13, GRAY, (860, 48)),
        ("營造異想 ·", "chinese", 24, INK, (1298, 31)),
        ("Construction Imagined Otherwise", "orbitron", 20, (0, 0, 0), (1424, 35)),
        ("樓層地圖", "chinese", 20, INK, (116, 146)),
        ("AXONOMETRIC MAP", "english", 11, GRAY, (310, 159)),
        ("左輪速", "chinese", 15, INK, (657, 912)),
        ("L WHEEL SPEED", "english", 12, GRAY, (657, 932)),
        ("循線感測", "chinese", 15, INK, (793, 912)),
        ("LINE SENSORS", "english", 12, GRAY, (793, 932)),
        ("右輪速", "chinese", 15, INK, (1172, 912)),
        ("R WHEEL SPEED", "english", 12, GRAY, (1172, 932)),
        ("電壓", "chinese", 15, INK, (1356, 912)),
        ("BATTERY", "english", 12, GRAY, (1356, 932)),
        ("m/s", "english", 13, GRAY, (736, 968)),
        ("m/s", "english", 13, GRAY, (1258, 968)),
        ("V", "english", 16, GRAY, (1440, 963)),
        ("FORWARD", "english", 10, ORANGE, (160, 983)),
        ("LEFT", "english", 10, GRAY, (1688, 983)),
        ("RIGHT", "english", 10, GRAY, (1781, 983)),
    ]
    for message, font_name, size, color, position in static_labels:
        static.blit(
            label(message, font_name, size, color),
            position,
            special_flags=pygame.BLEND_PREMULTIPLIED,
        )
    assets["static"] = static
    # Production HUD loading does not allocate simulator photographs or moving backgrounds.
    if include_test_backgrounds:
        background_paths = {
            "Reference image": REFERENCE_BACKGROUND_PATH,
            **{
                path.name: path
                for path in sorted(
                    (REPOSITORY_DIRECTORY / "asset/test_background").glob("*.jpg")
                )
            },
        }
        veil = pygame.Surface(LAYOUT_SIZE, pygame.SRCALPHA)
        veil.fill((128, 128, 128, BACKGROUND_VEIL_ALPHA))
        for name, path in background_paths.items():
            original = pygame.image.load(str(path)).convert()
            scale = max(1920 / original.get_width(), 1080 / original.get_height())
            resized = pygame.transform.smoothscale(
                original,
                (round(original.get_width() * scale), round(original.get_height() * scale)),
            )
            background = pygame.Surface(LAYOUT_SIZE)
            background.blit(resized, resized.get_rect(center=(960, 540)))
            background.blit(veil, (0, 0))
            assets["backgrounds"][name] = (
                background,
                pygame.transform.smoothscale(background, (2080, 1170)),
            )
    return assets


def compose_layout_overlay(assets, state, map_surface, elapsed_seconds):
    """Compose a transparent premultiplied HUD from the supplied display snapshot."""
    overlay = assets["static"].copy()
    label = assets["label"]

    def draw_text(message, font_name, size, color, position):
        """Blit cached typography using the HUD's premultiplied alpha convention."""
        overlay.blit(
            label(message, font_name, size, color),
            position,
            special_flags=pygame.BLEND_PREMULTIPLIED,
        )

    car_number = state["active_car"]
    floor = state["floor"]
    if map_surface is not None:
        overlay.blit(
            map_surface, MINIMAP_POSITION, special_flags=pygame.BLEND_PREMULTIPLIED
        )
    active_dot = (
        state["video_present"] and int(elapsed_seconds / VIDEO_BLINK_SECONDS) % 2 == 0
    )
    pygame.draw.circle(overlay, ORANGE if active_dot else (137, 99, 96), (85, 49), 8)
    draw_text(f"{car_number:02d}", "orbitron_bold", 36, INK, (192, 23))
    draw_text(f"{state['coins']:05d}", "orbitron_bold", 36, ORANGE, (554, 23))
    draw_text(
        f"{floor}F" if floor not in ("G", "-1", "--") else floor,
        "orbitron_bold",
        36,
        INK,
        (934, 23),
    )
    for index, floor_name in enumerate(reversed(FLOOR_ORDER)):
        center_y = 270 + index * 60
        pygame.draw.line(overlay, (167, 172, 179), (57, center_y), (83, center_y), 1)
        if floor_name == floor:
            pygame.draw.rect(
                overlay, ORANGE, (90, center_y - 13, 37, 26), border_radius=7
            )
        floor_label = label(
            floor_name, "english", 15, (255, 255, 255) if floor_name == floor else INK
        )
        overlay.blit(
            floor_label,
            floor_label.get_rect(center=(108, center_y)),
            special_flags=pygame.BLEND_PREMULTIPLIED,
        )
    for direction, position in (
        ("forward", (150, 915)),
        ("left", (1664, 915)),
        ("right", (1765, 915)),
    ):
        overlay.blit(
            assets["images"][f"input_{direction}_{int(state[direction])}"],
            position,
            special_flags=pygame.BLEND_PREMULTIPLIED,
        )
    for key, position in (("left_speed", (657, 951)), ("right_speed", (1172, 951))):
        draw_text(
            f"{state[key]:.2f}" if state["telemetry_fresh"] else "--",
            "orbitron",
            28,
            GRAY,
            position,
        )
    draw_text(f"{state['battery']:.1f}" if state["battery"] is not None else "--", "orbitron", 30, GRAY, (1356, 951))
    # Premultiply before filtering to avoid dark fringes around transparent edges.
    sensor_scale = LINE_SENSOR_ANTIALIAS_SCALE
    sensor_strip = pygame.Surface(
        (328 * sensor_scale, 34 * sensor_scale), pygame.SRCALPHA
    )
    for sensor_index in range(8):
        sensor_on = bool(state["sensor_mask"] & (1 << (7 - sensor_index)))
        pygame.draw.circle(
            sensor_strip,
            (*GRAY, 255),
            ((17 + sensor_index * 42) * sensor_scale, 17 * sensor_scale),
            LINE_SENSOR_RADIUS * sensor_scale,
            width=0 if sensor_on else LINE_SENSOR_OUTLINE_WIDTH * sensor_scale,
        )
    sensor_strip = pygame.transform.smoothscale(sensor_strip.premul_alpha(), (328, 34))
    overlay.blit(sensor_strip, (795, 953), special_flags=pygame.BLEND_PREMULTIPLIED)
    if state["split"]:
        overlay.blit(
            assets["images"][f"split_{state['split']}_{state['selected_exit']}"],
            SPLIT_POSITION,
            special_flags=pygame.BLEND_PREMULTIPLIED,
        )
    feedback = (
        "FRESH"
        if state["telemetry_fresh"]
        else "STALE (last sensors / battery retained)"
    )
    footer = (
        f"CAR{car_number} | TID: {state['tid']} | TYPE: {state['split'] or 'NONE'} | "
        f"NAV: {state['selected_exit'].upper()} | TELEMETRY: {feedback}"
    )
    draw_text(footer, "english", 14, INK, (57, 1030))
    # Pause priority: telemetry lost, low battery, then the reported line-lost state.
    if not state["present"] or not state["telemetry_fresh"]:
        overlay.blit(assets["pause_overlays"]["telemetry_lost"], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
    elif state["battery_low"]:
        overlay.blit(assets["pause_overlays"]["battery_low"], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
    elif state["line_lost"]:
        overlay.blit(assets["pause_overlays"]["line_lost"], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
    return overlay
