"""Incremental single-instance benchmarks; generated files stay outside asset/."""

import argparse
import ctypes
from functools import partial
import json
from pathlib import Path
import statistics
import time

import pygame

from minimap import (
    ASSET_DIRECTORY, CURRENT_FLOOR_OPACITY, OTHER_FLOOR_OPACITY,
    FLOOR_ORDER, MINIMAP_SIZE, NATIVE_SIZE, load_cached_layers,
    load_minimap_assets, load_tag_attributes,
)


# Separate output directories preserve the source artwork and original experiment.
EXPERIMENT_DIRECTORY = Path(__file__).resolve().parent
CACHE_DIRECTORY = EXPERIMENT_DIRECTORY / "cache"
RESULT_DIRECTORY = EXPERIMENT_DIRECTORY / "results"
SCENE_TAGS = (11, 63, 140, 310)
SCENE_ZONES = ("A", "C")


def prepare_cached_layers(assets, target_sizes):
    """Decode once, crop native images, and save premultiplied size/opacity variants."""
    started = time.perf_counter()
    manifest_path = CACHE_DIRECTORY / "manifest.json"
    # Cache construction is explicit via --prepare; normal runs load prepared files.
    manifest = {}
    source_paths = sorted({path for category in ("floors", "tracks", "zones", "cars", "coins")
                           for path in assets[category].values()})
    for image_index, image_path in enumerate(source_paths):
        source_image = pygame.image.load(str(image_path))
        native_bounds = source_image.get_bounding_rect()
        cropped_image = source_image.subsurface(native_bounds).copy()
        relative_name = image_path.relative_to(ASSET_DIRECTORY).as_posix()
        # Floors/zones keep neutral alpha so tuning opacity needs no disk-cache rebuild.
        opacities = (51, 255) if image_path.parent.name == "Cars" else (255,)
        for opacity in opacities:
            native_image = cropped_image.copy()
            native_image.fill((255, 255, 255, opacity), special_flags=pygame.BLEND_RGBA_MULT)
            native_image = native_image.premul_alpha()
            for size in (4000, *target_sizes):
                if size == 4000:
                    prepared_image, bounds = native_image, native_bounds
                else:
                    # Scale on the original canvas to preserve the exact sampling grid.
                    canvas = pygame.Surface(NATIVE_SIZE, pygame.SRCALPHA)
                    canvas.blit(native_image, native_bounds, special_flags=pygame.BLEND_PREMULTIPLIED)
                    reduced_image = pygame.transform.smoothscale(canvas, (size, size))
                    bounds = reduced_image.get_bounding_rect()
                    prepared_image = reduced_image.subsurface(bounds).copy()
                cache_key = f"{size}/{opacity}/{relative_name}"
                output_path = CACHE_DIRECTORY / cache_key
                output_path.parent.mkdir(parents=True, exist_ok=True)
                pygame.image.save(prepared_image, str(output_path))
                manifest[cache_key] = [bounds.x, bounds.y]
        if image_index % 50 == 0:
            print(f"Prepared {image_index + 1}/{len(source_paths)} source images", flush=True)
    CACHE_DIRECTORY.mkdir(exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    duration = time.perf_counter() - started
    print(f"Cache preparation: {duration:.2f} seconds", flush=True)
    return duration


def compose_cached_minimap(tag_attributes, assets, layers, active_car, car_tags,
                           reserved_zones, target_size, technique, grouped_coins,
                           floor_cache):
    """Preserve floor interleaving while comparing one additional cache at a time."""
    canvas_size = 4000 if technique == "native_cached" else target_size
    canvas = pygame.Surface((canvas_size, canvas_size), pygame.SRCALPHA)
    active_floor = tag_attributes[car_tags[active_car - 1]]["floor"]

    def draw_layer(destination, path, opacity=255):
        """Draw a cached cropped image at its original canvas offset."""
        relative_name = path.relative_to(ASSET_DIRECTORY).as_posix()
        image, offset = layers[f"{canvas_size}/{opacity}/{relative_name}"]
        destination.blit(image, offset, special_flags=pygame.BLEND_PREMULTIPLIED)

    for floor in FLOOR_ORDER:
        floor_alpha = round(255 * (CURRENT_FLOOR_OPACITY if floor == active_floor else OTHER_FLOOR_OPACITY))
        floor_key = (target_size, active_car, floor, floor_alpha,
                     tuple(reserved_zones) if floor == "G" else ())
        if technique == "floor_cached" and floor_key in floor_cache:
            canvas.blit(floor_cache[floor_key], (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
        else:
            static_surface = (pygame.Surface((canvas_size, canvas_size), pygame.SRCALPHA)
                              if technique == "floor_cached" else canvas)
            draw_layer(static_surface, assets["floors"][floor], floor_alpha)
            if floor == "G":
                for zone in "ABCD":
                    if zone in reserved_zones:
                        draw_layer(static_surface, assets["zones"][zone], floor_alpha)
            if (active_car, floor) in assets["tracks"]:
                draw_layer(static_surface, assets["tracks"][(active_car, floor)])
            if technique in ("coins_grouped", "floor_cached"):
                coin_image, coin_offset = grouped_coins[(target_size, active_car, floor)]
                static_surface.blit(coin_image, coin_offset, special_flags=pygame.BLEND_PREMULTIPLIED)
            else:
                for tag_number, attributes in tag_attributes.items():
                    if attributes["floor"] == floor and attributes["track"] == str(active_car):
                        draw_layer(static_surface, assets["coins"][tag_number])
            if technique == "floor_cached":
                floor_cache[floor_key] = static_surface
                canvas.blit(static_surface, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
        car_order = [number for number in range(1, 5) if number != active_car] + [active_car]
        for car_number in car_order:
            tag_number = car_tags[car_number - 1]
            if tag_attributes[tag_number]["floor"] == floor:
                draw_layer(canvas, assets["cars"][tag_number], 255 if car_number == active_car else 51)
    return pygame.transform.smoothscale(canvas, (target_size, target_size)) if canvas_size == 4000 else canvas


def benchmark_composition(tag_attributes, assets, target_size, samples):
    """Time changing-tag scenes for one instance; report median, p95, and maximum."""
    started = time.perf_counter()
    layers = load_cached_layers((target_size, 4000))
    load_seconds = time.perf_counter() - started
    layer_bytes = sum(image.get_pitch() * image.get_height() for image, offset in layers.values())
    grouped_coins = {}
    for active_car in range(1, 5):
        for floor in FLOOR_ORDER:
            combined = pygame.Surface((target_size, target_size), pygame.SRCALPHA)
            for tag_number, attributes in tag_attributes.items():
                if attributes["floor"] == floor and attributes["track"] == str(active_car):
                    image, offset = layers[f"{target_size}/255/Coins/T{tag_number}.png"]
                    combined.blit(image, offset, special_flags=pygame.BLEND_PREMULTIPLIED)
            coin_bounds = combined.get_bounding_rect()
            grouped_coins[(target_size, active_car, floor)] = (
                combined.subsurface(coin_bounds).copy(), coin_bounds.topleft
            )
    results = {"target_size": target_size, "cache_load_seconds": load_seconds,
               "loaded_layer_mib": layer_bytes / 1024**2, "techniques": {}}
    floor_cache = {}
    for technique in ("native_cached", "size_cached", "coins_grouped", "floor_cached"):
        # Exercise all active cars, cross-track placements, no-track tags, and floor changes.
        scenes = [(active_car, tags, zones) for active_car in range(1, 5)
                  for tags in (SCENE_TAGS, (140, 11, 310, 63), (1, 33, 154, 262))
                  for zones in (SCENE_ZONES, ())]
        preparation_started = time.perf_counter()
        for active_car, tags, zones in scenes:
            compose_cached_minimap(tag_attributes, assets, layers, active_car, tags, zones,
                                   target_size, technique, grouped_coins, floor_cache)
        warmup_seconds = time.perf_counter() - preparation_started
        elapsed_samples = []
        sample_count = 12 if technique == "native_cached" else samples
        for sample_index in range(sample_count):
            active_car, tags, zones = scenes[sample_index % len(scenes)]
            sample_started = time.perf_counter_ns()
            compose_cached_minimap(tag_attributes, assets, layers, active_car, tags, zones,
                                   target_size, technique, grouped_coins, floor_cache)
            elapsed_samples.append((time.perf_counter_ns() - sample_started) / 1e6)
        ordered_samples = sorted(elapsed_samples)
        results["techniques"][technique] = {
            "samples": sample_count, "median_ms": statistics.median(elapsed_samples),
            "p95_ms": ordered_samples[int((len(ordered_samples) - 1) * .95)],
            "max_ms": max(elapsed_samples), "warmup_seconds": warmup_seconds,
        }
        for active_car in (1, 4):
            image = compose_cached_minimap(tag_attributes, assets, layers, active_car, SCENE_TAGS,
                                           SCENE_ZONES, target_size, technique, grouped_coins, floor_cache)
            pygame.image.save(image, str(RESULT_DIRECTORY / f"{technique}_car{active_car}_{target_size}.png"))
        print(target_size, technique, results["techniques"][technique], flush=True)
    # Compare a floor-cache miss with a hit; preparation must not hide state-change cost.
    miss_samples = []
    for sample_index in range(24):
        active_car, tags, zones = scenes[sample_index]
        sample_started = time.perf_counter_ns()
        compose_cached_minimap(tag_attributes, assets, layers, active_car, tags, zones,
                               target_size, "floor_cached", grouped_coins, {})
        miss_samples.append((time.perf_counter_ns() - sample_started) / 1e6)
    results["floor_cache_cold_median_ms"] = statistics.median(miss_samples)
    results["floor_cache_mib"] = sum(surface.get_pitch() * surface.get_height()
                                      for surface in floor_cache.values()) / 1024**2
    frame_composer = partial(compose_cached_minimap, tag_attributes, assets, layers,
                             target_size=target_size, technique="floor_cached",
                             grouped_coins=grouped_coins, floor_cache=floor_cache)
    return results, frame_composer


def benchmark_video_overlay(minimap_surface, samples, frame_composer):
    """Measure one 1080p synthetic YUV stream with cached versus per-frame GPU uploads."""
    from pygame._sdl2.video import Window

    # Match the production SDL upload/render path, using a windowed single instance.
    native_library = ctypes.CDLL(str(Path(pygame.__file__).parent / "SDL2.dll"))
    pointer, integer = ctypes.c_void_p, ctypes.c_int
    signatures = {
        "SDL_GetWindowFromID": (pointer, [ctypes.c_uint32]),
        "SDL_CreateRenderer": (pointer, [pointer, integer, ctypes.c_uint32]),
        "SDL_GetRendererInfo": (integer, [pointer, pointer]),
        "SDL_CreateTexture": (pointer, [pointer, ctypes.c_uint32, integer, integer, integer]),
        "SDL_UpdateYUVTexture": (integer, [pointer, pointer, pointer, integer, pointer, integer, pointer, integer]),
        "SDL_UpdateTexture": (integer, [pointer, pointer, pointer, integer]),
        "SDL_SetTextureBlendMode": (integer, [pointer, integer]),
        "SDL_ComposeCustomBlendMode": (integer, [integer] * 6),
        "SDL_RenderCopy": (integer, [pointer, pointer, pointer, pointer]),
        "SDL_RenderPresent": (None, [pointer]),
        "SDL_DestroyTexture": (None, [pointer]),
        "SDL_DestroyRenderer": (None, [pointer]),
        "SDL_GetError": (ctypes.c_char_p, []),
    }
    for name, (return_type, argument_types) in signatures.items():
        native_function = getattr(native_library, name)
        native_function.restype, native_function.argtypes = return_type, argument_types
    window = Window("E12 single-instance GPU benchmark", size=(1920, 1080))
    renderer = native_library.SDL_CreateRenderer(native_library.SDL_GetWindowFromID(window.id), -1, 2)
    if not renderer:
        raise RuntimeError(native_library.SDL_GetError().decode())
    # SDL's renderer name identifies the measured backend without assuming a GPU.
    class RendererInformation(ctypes.Structure):
        """Match SDL_RendererInfo for reporting the actual selected backend."""
        _fields_ = [("name", ctypes.c_char_p), ("flags", ctypes.c_uint32),
                    ("format_count", ctypes.c_uint32), ("formats", ctypes.c_uint32 * 16),
                    ("max_width", integer), ("max_height", integer)]

    renderer_information = RendererInformation()
    native_library.SDL_GetRendererInfo(renderer, ctypes.byref(renderer_information))
    video_texture = native_library.SDL_CreateTexture(renderer, int.from_bytes(b"IYUV", "little"), 1, 1920, 1080)
    overlay_texture = native_library.SDL_CreateTexture(renderer, 0x16762004, 1, *minimap_surface.get_size())
    # ONE, ONE_MINUS_SRC_ALPHA, ADD for both RGB and alpha: premultiplied blending.
    premultiplied_blend = native_library.SDL_ComposeCustomBlendMode(2, 6, 1, 2, 6, 1)
    blend_result = native_library.SDL_SetTextureBlendMode(overlay_texture, premultiplied_blend)
    if blend_result != 0:
        raise RuntimeError(native_library.SDL_GetError().decode())
    overlay_pixels = pygame.image.tobytes(minimap_surface, "RGBA")
    native_library.SDL_UpdateTexture(overlay_texture, None, ctypes.c_char_p(overlay_pixels), minimap_surface.get_width() * 4)
    # Alternate prepared frames to exercise full YUV uploads without decoder/network noise.
    luma_frames = [ctypes.create_string_buffer(bytes([level]) * (1920 * 1080)) for level in (70, 110, 150, 190)]
    chroma_plane = ctypes.create_string_buffer(bytes([128]) * (960 * 540))
    destination = (integer * 4)(1920 - minimap_surface.get_width(), 1080 - minimap_surface.get_height(), *minimap_surface.get_size())
    results = {"renderer": renderer_information.name.decode(),
               "scope": "1920x1080 synthetic YUV uploads; no decode/network; unpaced present timings"}
    try:
        for upload_overlay in (True, False):
            durations = []
            for sample_index in range(samples + 60):
                pygame.event.pump()
                started = time.perf_counter_ns()
                native_library.SDL_UpdateYUVTexture(video_texture, None, luma_frames[sample_index % 4], 1920,
                                                   chroma_plane, 960, chroma_plane, 960)
                if upload_overlay:
                    # Include the production code's RGBA serialization cost.
                    current_pixels = pygame.image.tobytes(minimap_surface, "RGBA")
                    native_library.SDL_UpdateTexture(overlay_texture, None, ctypes.c_char_p(current_pixels), minimap_surface.get_width() * 4)
                native_library.SDL_RenderCopy(renderer, video_texture, None, None)
                native_library.SDL_RenderCopy(renderer, overlay_texture, None, ctypes.byref(destination))
                native_library.SDL_RenderPresent(renderer)
                if sample_index >= 60:
                    durations.append((time.perf_counter_ns() - started) / 1e6)
            results["upload_every_frame" if upload_overlay else "upload_on_change"] = {
                "median_ms": statistics.median(durations),
                "p95_ms": sorted(durations)[int((len(durations) - 1) * .95)], "max_ms": max(durations),
                "samples": samples,
            }
        # Include composition and upload on every frame, paced to 60 Hz for ten seconds.
        durations = []
        frame_budget = 1 / 60
        deadline_misses = 0
        run_started = time.perf_counter()
        for frame_number in range(600):
            deadline = run_started + frame_number * frame_budget
            time.sleep(max(0, deadline - time.perf_counter()))
            pygame.event.pump()
            started = time.perf_counter()
            current_minimap = frame_composer(active_car=1,
                                             car_tags=(11 + frame_number % 10, 63, 140, 310),
                                             reserved_zones=SCENE_ZONES)
            current_pixels = pygame.image.tobytes(current_minimap, "RGBA")
            native_library.SDL_UpdateTexture(overlay_texture, None, ctypes.c_char_p(current_pixels), minimap_surface.get_width() * 4)
            native_library.SDL_UpdateYUVTexture(video_texture, None, luma_frames[frame_number % 4], 1920,
                                               chroma_plane, 960, chroma_plane, 960)
            native_library.SDL_RenderCopy(renderer, video_texture, None, None)
            native_library.SDL_RenderCopy(renderer, overlay_texture, None, ctypes.byref(destination))
            native_library.SDL_RenderPresent(renderer)
            finished = time.perf_counter()
            durations.append((finished - started) * 1000)
            deadline_misses += finished > deadline + frame_budget
        results["paced_60hz_with_tag_change_every_frame"] = {
            "frames": 600, "elapsed_seconds": time.perf_counter() - run_started,
            "median_work_ms": statistics.median(durations),
            "p95_work_ms": sorted(durations)[int((len(durations) - 1) * .95)],
            "max_work_ms": max(durations), "deadline_misses": deadline_misses,
        }
    finally:
        native_library.SDL_DestroyTexture(overlay_texture)
        native_library.SDL_DestroyTexture(video_texture)
        native_library.SDL_DestroyRenderer(renderer)
        window.destroy()
    return results


def save_visual_comparisons(target_size, assets):
    """Save normal-background and amplified difference views without extra packages."""
    results = {}
    background = pygame.image.load(str(assets["backgrounds"]["bg1.jpg"]))
    background = pygame.transform.smoothscale(background, (target_size, target_size))
    for active_car in (1, 4):
        reference = pygame.image.load(str(RESULT_DIRECTORY / f"reference_car{active_car}_{target_size}.png"))
        reference_preview = background.copy()
        reference_preview.blit(reference, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
        pygame.image.save(reference_preview, str(RESULT_DIRECTORY / f"reference_preview_car{active_car}_{target_size}.png"))
        for technique in ("native_cached", "size_cached", "coins_grouped", "floor_cached"):
            candidate = pygame.image.load(str(RESULT_DIRECTORY / f"{technique}_car{active_car}_{target_size}.png"))
            reference_rgba = pygame.image.tobytes(reference, "RGBA")
            candidate_rgba = pygame.image.tobytes(candidate, "RGBA")
            candidate_preview = background.copy()
            candidate_preview.blit(candidate, (0, 0), special_flags=pygame.BLEND_PREMULTIPLIED)
            reference_bytes = pygame.image.tobytes(reference_preview, "RGB")
            candidate_bytes = pygame.image.tobytes(candidate_preview, "RGB")
            differences = [abs(first - second) for first, second in zip(reference_bytes, candidate_bytes)]
            results[f"{technique}_car{active_car}"] = {
                "rgba_identical": reference_rgba == candidate_rgba,
                "mean_absolute_rgb_error": sum(differences) / len(differences),
                "max_channel_error": max(differences),
                "pixels_with_channel_error_over_8_percent": 100 * sum(
                    max(differences[index:index + 3]) > 8 for index in range(0, len(differences), 3)
                ) / (target_size**2),
            }
            heatmap = pygame.image.frombytes(bytes(min(255, value * 8) for value in differences),
                                            (target_size, target_size), "RGB")
            comparison = pygame.Surface((target_size * 3, target_size))
            comparison.blit(reference_preview, (0, 0))
            comparison.blit(candidate_preview, (target_size, 0))
            comparison.blit(heatmap, (target_size * 2, 0))
            pygame.image.save(comparison, str(RESULT_DIRECTORY / f"comparison_{technique}_car{active_car}_{target_size}.png"))
    return results


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--prepare", action="store_true", help="Rebuild generated caches from originals")
    argument_parser.add_argument("--prepare-only", action="store_true", help="Rebuild caches without running benchmarks")
    argument_parser.add_argument("--samples", type=int, default=240)
    argument_parser.add_argument("--skip-video", action="store_true")
    arguments = argument_parser.parse_args()
    RESULT_DIRECTORY.mkdir(exist_ok=True)
    pygame.display.init()
    tag_attributes = load_tag_attributes(ASSET_DIRECTORY)
    assets = load_minimap_assets(ASSET_DIRECTORY)
    preparation_seconds = prepare_cached_layers(assets, (MINIMAP_SIZE,)) if arguments.prepare or arguments.prepare_only else None
    if arguments.prepare_only:
        pygame.quit()
        raise SystemExit(0)
    all_results = {"scope": "one instance; no four-display test", "preparation_seconds": preparation_seconds,
                   "pygame": pygame.version.ver, "sdl": pygame.get_sdl_version(),
                   "desktop_sizes": pygame.display.get_desktop_sizes(), "sizes": []}
    try:
        for target_size in (MINIMAP_SIZE,):
            for active_car in (1, 4):
                reference_path = RESULT_DIRECTORY / f"reference_car{active_car}_{target_size}.png"
                if arguments.prepare or not reference_path.exists():
                    started = time.perf_counter()
                    reference = compose_cached_minimap(
                        tag_attributes, assets, load_cached_layers((4000,)), active_car,
                        SCENE_TAGS, SCENE_ZONES, target_size, "native_cached", {}, {}
                    )
                    pygame.image.save(reference, str(reference_path))
                    print(f"Reference {active_car}/{target_size}: {time.perf_counter() - started:.2f}s", flush=True)
            size_results, frame_composer = benchmark_composition(tag_attributes, assets, target_size, arguments.samples)
            size_results["visual_errors"] = save_visual_comparisons(target_size, assets)
            if not arguments.skip_video:
                minimap = pygame.image.load(str(RESULT_DIRECTORY / f"floor_cached_car1_{target_size}.png"))
                size_results["synthetic_yuv_overlay"] = benchmark_video_overlay(minimap, arguments.samples, frame_composer)
                print("Video overlay", target_size, size_results["synthetic_yuv_overlay"], flush=True)
            all_results["sizes"].append(size_results)
            (RESULT_DIRECTORY / "benchmark.json").write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    finally:
        pygame.quit()
