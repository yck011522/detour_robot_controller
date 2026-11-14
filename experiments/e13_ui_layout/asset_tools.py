"""Authoring-only SVG/PNG generation; not needed by the live HUD renderer."""

import base64
import io
import math
import xml.etree.ElementTree as element_tree

import pygame

from layout_settings import (UI_ASSET_DIRECTORY, REFERENCE_BACKGROUND_PATH, REPOSITORY_DIRECTORY, FONT_PATHS, LAYOUT_SIZE, SPLIT_EXITS, EXIT_LABELS)


def prepare_layout_assets():
    """Generate paired SVG/PNG artwork from shared vector geometry, at 3x quality."""
    UI_ASSET_DIRECTORY.mkdir(parents=True, exist_ok=True)
    pygame.font.init()
    raster_scale = 3
    font_cache = {}

    def save_vector_asset(name, dimensions, shapes):
        """Render the same primitives to editable SVG and antialiased transparent PNG."""
        vector = element_tree.Element(
            "svg",
            xmlns="http://www.w3.org/2000/svg",
            width=str(dimensions[0]),
            height=str(dimensions[1]),
            viewBox=f"0 0 {dimensions[0]} {dimensions[1]}",
        )
        canvas = pygame.Surface(
            tuple(value * raster_scale for value in dimensions), pygame.SRCALPHA
        )
        for shape in shapes:
            kind, attributes = shape
            attributes = dict(attributes)
            font_name = attributes.pop("font", "english")
            content = attributes.pop("text", "")
            vector_attributes = {key: str(value) for key, value in attributes.items()}
            if kind == "text":
                vector_attributes["font-family"] = {
                    "chinese": "Microsoft JhengHei",
                    "english": "Segoe UI",
                    "orbitron": "Orbitron",
                    "orbitron_bold": "Orbitron",
                }[font_name]
                vector_attributes["dominant-baseline"] = "text-before-edge"
                if font_name in ("chinese", "orbitron_bold"):
                    vector_attributes["font-weight"] = "700"
            node = element_tree.SubElement(vector, kind, vector_attributes)
            node.text = content or None
            layer = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
            fill = attributes.get("fill", "none")
            stroke = attributes.get("stroke", "none")
            opacity = round(float(attributes.get("opacity", 1)) * 255)
            fill_color = (*pygame.Color(fill)[:3], opacity) if fill != "none" else None
            stroke_color = (
                (*pygame.Color(stroke)[:3], opacity) if stroke != "none" else None
            )
            stroke_width = round(
                float(attributes.get("stroke-width", 1)) * raster_scale
            )
            if kind == "rect":
                rectangle = pygame.Rect(
                    *(
                        round(float(attributes[key]) * raster_scale)
                        for key in ("x", "y", "width", "height")
                    )
                )
                radius = round(float(attributes.get("rx", 0)) * raster_scale)
                if fill_color:
                    pygame.draw.rect(layer, fill_color, rectangle, border_radius=radius)
                if stroke_color:
                    pygame.draw.rect(
                        layer,
                        stroke_color,
                        rectangle,
                        stroke_width,
                        border_radius=radius,
                    )
            elif kind in ("circle", "ellipse"):
                radius_x = float(attributes.get("rx", attributes.get("r", 0)))
                radius_y = float(attributes.get("ry", attributes.get("r", 0)))
                rectangle = pygame.Rect(
                    round((float(attributes["cx"]) - radius_x) * raster_scale),
                    round((float(attributes["cy"]) - radius_y) * raster_scale),
                    round(2 * radius_x * raster_scale),
                    round(2 * radius_y * raster_scale),
                )
                if fill_color:
                    pygame.draw.ellipse(layer, fill_color, rectangle)
                if stroke_color:
                    pygame.draw.ellipse(layer, stroke_color, rectangle, stroke_width)
            elif kind in ("polyline", "polygon"):
                points = [
                    tuple(
                        round(float(value) * raster_scale) for value in point.split(",")
                    )
                    for point in attributes["points"].split()
                ]
                if fill_color:
                    pygame.draw.polygon(layer, fill_color, points)
                if stroke_color:
                    pygame.draw.lines(
                        layer, stroke_color, kind == "polygon", points, stroke_width
                    )
                    # Rounded joins and caps match the line language of the reference.
                    for point in points:
                        pygame.draw.circle(
                            layer, stroke_color, point, stroke_width // 2
                        )
            elif kind == "text":
                font_size = int(attributes["font-size"])
                key = (font_name, font_size)
                if key not in font_cache:
                    font_cache[key] = pygame.font.Font(
                        str(FONT_PATHS[font_name]), font_size * raster_scale
                    )
                label = font_cache[key].render(content, True, fill_color)
                position = (
                    round(float(attributes["x"]) * raster_scale),
                    round(float(attributes["y"]) * raster_scale),
                )
                if attributes.get("text-anchor") == "middle":
                    position = (position[0] - label.get_width() // 2, position[1])
                layer.blit(label, position)
            canvas.blit(layer, (0, 0))
        element_tree.indent(vector)
        element_tree.ElementTree(vector).write(
            UI_ASSET_DIRECTORY / f"{name}.svg", encoding="utf-8", xml_declaration=True
        )
        reduced = pygame.transform.smoothscale(canvas, dimensions)
        pygame.image.save(reduced, str(UI_ASSET_DIRECTORY / f"{name}.png"))

    # Reconstruct the reference's frame as editable simple geometry, excluding all dummy data.
    frame_shapes = [
        (
            "rect",
            {
                "x": 58,
                "y": 19,
                "width": 1804,
                "height": 55,
                "rx": 25,
                "fill": "#f9fafc",
                "stroke": "#ffffff",
                "stroke-width": 2,
                "opacity": 0.6,
            },
        ),
        (
            "rect",
            {
                "x": 58,
                "y": 902,
                "width": 1804,
                "height": 99,
                "rx": 25,
                "fill": "#f9fafc",
                "stroke": "#ffffff",
                "stroke-width": 2,
                "opacity": 0.6,
            },
        ),
    ]
    for horizontal, start, end in (
        (313, 33, 58),
        (750, 33, 58),
        (1032, 33, 58),
        (624, 920, 987),
        (770, 920, 987),
        (1151, 920, 987),
        (1299, 920, 987),
    ):
        frame_shapes.append(
            (
                "polyline",
                {
                    "points": f"{horizontal},{start} {horizontal},{end}",
                    "stroke": "#ffffff",
                    "stroke-width": 3,
                    "opacity": 0.65,
                },
            )
        )
    for vertical in (37, 44, 51):
        frame_shapes.append(
            (
                "ellipse",
                {
                    "cx": 385,
                    "cy": vertical,
                    "rx": 21,
                    "ry": 10,
                    "stroke": "#676d76",
                    "stroke-width": 2,
                },
            )
        )
    for center_x, center_y, radius in ((829, 38, 13), (91, 154, 12)):
        for offset in (0, 6, 12):
            frame_shapes.append(
                (
                    "polyline",
                    {
                        "points": f"{center_x-radius},{center_y+offset} {center_x},{center_y+offset+7} {center_x+radius},{center_y+offset}",
                        "stroke": "#676d76",
                        "stroke-width": 1.7,
                    },
                )
            )
        frame_shapes.append(
            (
                "polyline",
                {
                    "points": f"{center_x-radius},{center_y} {center_x},{center_y-7} {center_x+radius},{center_y}",
                    "stroke": "#676d76",
                    "stroke-width": 1.7,
                },
            )
        )
    save_vector_asset("hud_frame", LAYOUT_SIZE, frame_shapes)

    for direction, arrow_points in (
        ("forward", "36,39 36,20 29,27 36,20 43,27"),
        ("left", "44,32 25,32 32,25 25,32 32,39"),
        ("right", "27,32 46,32 39,25 46,32 39,39"),
    ):
        for active in (False, True):
            save_vector_asset(
                f"input_{direction}_{int(active)}",
                (72, 64),
                [
                    (
                        "rect",
                        {
                            "x": 0,
                            "y": 0,
                            "width": 72,
                            "height": 64,
                            "rx": 12,
                            "fill": "#ff741f" if active else "#f9fafc",
                            "opacity": 1 if active else 0.8,
                        },
                    ),
                    (
                        "polyline",
                        {
                            "points": arrow_points,
                            "stroke": "#ffffff" if active else "#8b9199",
                            "stroke-width": 2.5,
                        },
                    ),
                ],
            )
    for split_type, exits in SPLIT_EXITS.items():
        for selected_exit in exits:
            shapes = [
                (
                    "rect",
                    {
                        "x": 0,
                        "y": 0,
                        "width": 266,
                        "height": 326,
                        "rx": 24,
                        "fill": "#f9fafc",
                        "opacity": 0.85,
                    },
                ),
                (
                    "polyline",
                    {
                        "points": "21,73 245,73",
                        "stroke": "#c5cbd1",
                        "stroke-width": 1.5,
                    },
                ),
                (
                    "text",
                    {
                        "x": 22,
                        "y": 12,
                        "font-size": 22,
                        "font": "chinese",
                        "fill": "#22262d",
                        "text": "前方分岔",
                    },
                ),
                (
                    "text",
                    {
                        "x": 22,
                        "y": 40,
                        "font-size": 12,
                        "fill": "#676d76",
                        "text": "APPROACHING SPLIT",
                    },
                ),
            ]
            for direction in [
                exit_name for exit_name in exits if exit_name != selected_exit
            ] + [selected_exit]:
                if direction == "straight":
                    points = [(133, 218), (133, 117)]
                    head = [(123, 127), (133, 117), (143, 127)]
                else:
                    sign = -1 if direction == "left" else 1
                    points = [(133, 218), (133, 165)]
                    for step in range(1, 25):
                        angle = step / 24 * math.pi / 2
                        points.append(
                            (
                                133 + sign * 20 * (1 - math.cos(angle)),
                                165 - 20 * math.sin(angle),
                            )
                        )
                    points.append((133 + sign * 56, 145))
                    head = [
                        (133 + sign * 46, 135),
                        (133 + sign * 56, 145),
                        (133 + sign * 46, 155),
                    ]
                color = "#ff741f" if direction == selected_exit else "#ffffff"
                for vertices in (points, head):
                    shapes.append(
                        (
                            "polyline",
                            {
                                "points": " ".join(
                                    f"{horizontal:.3f},{vertical:.3f}"
                                    for horizontal, vertical in vertices
                                ),
                                "stroke": color,
                                "stroke-width": 6,
                            },
                        )
                    )
            for column, direction in enumerate(exits):
                selected = direction == selected_exit
                center = 79 + column * 110
                shapes.extend(
                    [
                        (
                            "rect",
                            {
                                "x": 24 + column * 110,
                                "y": 247,
                                "width": 110,
                                "height": 63,
                                "rx": 12,
                                "fill": "#ff741f" if selected else "#ffffff",
                                "opacity": 1 if selected else 0.75,
                            },
                        ),
                        (
                            "text",
                            {
                                "x": center,
                                "y": 258,
                                "font-size": 17,
                                "font": "chinese",
                                "text-anchor": "middle",
                                "fill": "#ffffff" if selected else "#22262d",
                                "text": EXIT_LABELS[direction][0],
                            },
                        ),
                        (
                            "text",
                            {
                                "x": center,
                                "y": 282,
                                "font-size": 12,
                                "text-anchor": "middle",
                                "fill": "#ffffff" if selected else "#676d76",
                                "text": EXIT_LABELS[direction][1],
                            },
                        ),
                    ]
                )
            save_vector_asset(f"split_{split_type}_{selected_exit}", (266, 326), shapes)

    # Extract only the background photo from the SVG; never use the dummy minimap/HUD JPEG.
    reference = element_tree.parse(
        REPOSITORY_DIRECTORY / "reference/layout.svg"
    ).getroot()
    background_node = next(
        node for node in reference.iter() if node.attrib.get("id") == "Image_1"
    )
    embedded = background_node.attrib["{http://www.w3.org/1999/xlink}href"]
    photo = pygame.image.load(io.BytesIO(base64.b64decode(embedded.split(",", 1)[1])))
    photo = pygame.transform.smoothscale(photo, (1920, 1275))
    canvas = pygame.Surface(LAYOUT_SIZE)
    canvas.fill("white")
    photo.set_alpha(round(0.81 * 255))
    canvas.blit(photo, (0, -1))
    pygame.image.save(canvas, str(REFERENCE_BACKGROUND_PATH))
    print(
        f"Generated SVG/PNG HUD artwork and six split variants in {UI_ASSET_DIRECTORY}",
        flush=True,
    )
