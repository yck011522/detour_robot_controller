"""Prepare and validate shared minimap sprites without importing any experiment."""

import argparse
import hashlib
import json
from pathlib import Path
import time

import pygame


# Changes to the preparation algorithm should increment this build format.
CACHE_FORMAT_VERSION = 1
ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "asset"
CACHE_DIRECTORY = ASSET_DIRECTORY / "cache" / "minimap"
NATIVE_SIZE = (4000, 4000)
DEFAULT_TARGET_SIZES = (700,)


def source_fingerprints(asset_directory):
    """Fingerprint map artwork only; CSV registrations and HUD files are independent."""
    source_paths = sorted({
        path
        for pattern in ("Floor*.png", "Track*Floor*.png", "Zone*.png", "Cars/T*.png", "Coins/T*.png")
        for path in asset_directory.glob(pattern)
    })
    return {
        path.relative_to(asset_directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_paths
    }


def prepare_cached_layers(asset_directory=ASSET_DIRECTORY, target_sizes=DEFAULT_TARGET_SIZES,
                          cache_directory=CACHE_DIRECTORY):
    """Rebuild cropped premultiplied sprites and record their source artwork hashes."""
    asset_directory, cache_directory = Path(asset_directory), Path(cache_directory)
    started = time.perf_counter()
    fingerprints = source_fingerprints(asset_directory)
    canvas_sizes = sorted({NATIVE_SIZE[0], *target_sizes})
    cache_directory.mkdir(parents=True, exist_ok=True)
    # Invalidate the build stamp before writing so an interrupted rebuild is retried.
    build_info_path = cache_directory / "build_info.json"
    build_info_path.unlink(missing_ok=True)
    manifest = {}
    for image_index, relative_name in enumerate(fingerprints):
        image_path = asset_directory / relative_name
        source_image = pygame.image.load(str(image_path))
        native_bounds = source_image.get_bounding_rect()
        cropped_image = source_image.subsurface(native_bounds).copy()
        # Floors/zones stay neutral; only ghost cars need a baked 20% variant.
        opacities = (51, 255) if image_path.parent.name == "Cars" else (255,)
        for opacity in opacities:
            native_image = cropped_image.copy()
            native_image.fill((255, 255, 255, opacity), special_flags=pygame.BLEND_RGBA_MULT)
            native_image = native_image.premul_alpha()
            for size in canvas_sizes:
                if size == NATIVE_SIZE[0]:
                    prepared_image, bounds = native_image, native_bounds
                else:
                    # Retain the original canvas sampling grid before cropping the result.
                    canvas = pygame.Surface(NATIVE_SIZE, pygame.SRCALPHA)
                    canvas.blit(native_image, native_bounds, special_flags=pygame.BLEND_PREMULTIPLIED)
                    reduced_image = pygame.transform.smoothscale(canvas, (size, size))
                    bounds = reduced_image.get_bounding_rect()
                    prepared_image = reduced_image.subsurface(bounds).copy()
                cache_key = f"{size}/{opacity}/{relative_name}"
                output_path = cache_directory / cache_key
                output_path.parent.mkdir(parents=True, exist_ok=True)
                pygame.image.save(prepared_image, str(output_path))
                manifest[cache_key] = [bounds.x, bounds.y]
        if image_index % 50 == 0:
            print(f"Prepared {image_index + 1}/{len(fingerprints)} source images", flush=True)
    (cache_directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    build_info = {"format_version": CACHE_FORMAT_VERSION, "native_size": list(NATIVE_SIZE),
                  "canvas_sizes": canvas_sizes, "source_hashes": fingerprints}
    build_info_path.write_text(json.dumps(build_info, indent=2), encoding="utf-8")
    duration = time.perf_counter() - started
    print(f"Minimap cache prepared in {duration:.2f} seconds: {cache_directory}", flush=True)
    return duration


def ensure_minimap_cache(asset_directory=ASSET_DIRECTORY, target_sizes=DEFAULT_TARGET_SIZES,
                         cache_directory=CACHE_DIRECTORY):
    """Rebuild missing/outdated/incomplete cache data; return whether rebuilding occurred."""
    asset_directory, cache_directory = Path(asset_directory), Path(cache_directory)
    manifest_path = cache_directory / "manifest.json"
    build_info_path = cache_directory / "build_info.json"
    reason = "missing cache manifest or build information"
    if manifest_path.is_file() and build_info_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reason = "invalid cache manifest or build information"
        else:
            fingerprints = source_fingerprints(asset_directory)
            canvas_sizes = sorted({NATIVE_SIZE[0], *target_sizes})
            expected_info = {"format_version": CACHE_FORMAT_VERSION, "native_size": list(NATIVE_SIZE),
                             "canvas_sizes": canvas_sizes, "source_hashes": fingerprints}
            expected_keys = {
                f"{size}/{opacity}/{name}"
                for name in fingerprints
                for opacity in ((51, 255) if Path(name).parent.name == "Cars" else (255,))
                for size in canvas_sizes
            }
            if build_info != expected_info:
                reason = "changed source artwork, cache format, or target size"
            elif not isinstance(manifest, dict) or set(manifest) != expected_keys:
                reason = "incomplete cache manifest"
            elif not all((cache_directory / key).is_file() for key in expected_keys):
                reason = "missing prepared sprites"
            else:
                return False
    print(f"Rebuilding minimap cache: {reason}. First preparation can take a few minutes.", flush=True)
    prepare_cached_layers(asset_directory, target_sizes, cache_directory)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild even when the cache is current")
    options = parser.parse_args()
    if options.force:
        prepare_cached_layers()
    elif not ensure_minimap_cache():
        print(f"Minimap cache is current: {CACHE_DIRECTORY}")
