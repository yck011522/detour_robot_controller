"""HUD composition only: explicit display state in, premultiplied RGBA surface out."""

import pygame

from layout_settings import (MINIMAP_POSITION, SPLIT_POSITION, VIDEO_BLINK_SECONDS, ORANGE, INK, GRAY, FLOOR_ORDER, LINE_SENSOR_RADIUS, LINE_SENSOR_OUTLINE_WIDTH, LINE_SENSOR_ANTIALIAS_SCALE)


def compose_layout_overlay(assets, state, map_surface, elapsed_seconds):
    """Compose a transparent premultiplied HUD from one immutable simulator snapshot."""
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
        f"{floor}F" if floor not in ("G", "-1") else floor,
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
    draw_text(f"{state['battery']:.1f}", "orbitron", 30, GRAY, (1356, 951))
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
        f"NAV: {state['selected_exit'].upper()} | TELEMETRY: {feedback} | SIMULATED DATA"
    )
    draw_text(footer, "english", 14, INK, (57, 1030))
    # Pause priority: telemetry lost, low battery, then the reported line-lost state.
    if not state["telemetry_fresh"]:
        overlay.blit(assets["pause_overlays"]["telemetry_lost"], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
    elif state["battery_low"]:
        overlay.blit(assets["pause_overlays"]["battery_low"], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
    elif state["line_lost"]:
        overlay.blit(assets["pause_overlays"]["line_lost"], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
    return overlay
