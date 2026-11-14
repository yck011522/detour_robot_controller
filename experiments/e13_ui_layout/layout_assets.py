"""Load cached HUD artwork and typography without starting any windows or I/O adapters."""

import pygame

from layout_settings import (UI_ASSET_DIRECTORY, REFERENCE_BACKGROUND_PATH, REPOSITORY_DIRECTORY, FONT_PATHS, LAYOUT_SIZE, BACKGROUND_VEIL_ALPHA, INK, GRAY, ORANGE)


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
