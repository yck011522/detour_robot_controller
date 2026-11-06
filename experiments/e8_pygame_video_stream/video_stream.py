"""Show one configured car camera in a 1920x1080 fullscreen Pygame window.

Run with the project's Python environment; FFmpeg must be on PATH.
Press Escape or Q to exit. See README.md beside this file for examples.
"""

import argparse
from collections import deque
import configparser
from pathlib import Path
import shutil
import subprocess
import threading

import pygame


SIZE = (1920, 1080)
FRAME_BYTES = SIZE[0] * SIZE[1] * 3  # One packed RGB24 frame.
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "address.ini"


def read_frames(pipe, latest_frame, finished):
    """Continuously drain FFmpeg; overwrite old frames instead of queuing them."""
    try:
        while True:
            # A pipe read may return less than one frame. Only publish whole frames.
            frame = bytearray(FRAME_BYTES)
            view = memoryview(frame)
            received = 0
            while received < FRAME_BYTES:
                count = pipe.readinto(view[received:])
                if not count:
                    return
                received += count
            latest_frame.append(frame)
    finally:
        finished.set()


def main(stop_event=None):
    """Select the screen/camera, launch the decoder, and render on the main thread."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--car", type=int, choices=range(1, 7), default=1)
    parser.add_argument("--list-displays", action="store_true",
                        help="print SDL display indexes and desktop sizes, then exit")
    args = parser.parse_args()

    process = None
    reader = None
    pygame.display.init()
    try:
        displays = pygame.display.get_desktop_sizes()
        for index, size in enumerate(displays):
            print(f"SDL display {index}: {size[0]}x{size[1]}")
        if args.list_displays:
            return

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("FFmpeg was not found. Add its bin directory to PATH.")
        config = configparser.ConfigParser(interpolation=None)
        with CONFIG_PATH.open(encoding="utf-8") as source:
            config.read_file(source)
        url = config[f"car_{args.car}_camera"]["rtsp_url"]
        # Each car uses its configured physical screen; SDL indexes start at zero.
        display_section = f"car_{args.car}_display"
        try:
            display_index = config.getint(display_section, "display_index")
        except ValueError as error:
            raise RuntimeError(
                f"[{display_section}] display_index must be an integer in {CONFIG_PATH}"
            ) from error
        if not 0 <= display_index < len(displays):
            raise RuntimeError(
                f"[{display_section}] display_index = {display_index} is unavailable; "
                f"connected SDL display indexes are 0 through {len(displays) - 1}."
            )

        screen = pygame.display.set_mode(SIZE, pygame.FULLSCREEN,
                                         display=display_index, vsync=0)
        pygame.display.set_caption(f"Car {args.car} camera — Escape to exit")
        screen.fill("black")
        pygame.display.flip()
        print(f"Opening car {args.car} on SDL display {display_index}: {url}")
        print("Waiting for video; Escape or Q exits. FFmpeg diagnostics appear here.")

        # Match the tested UDP/nobuffer setup. Disable RTP packet reordering too.
        # No audio, timestamp pacing, or output frame duplication is needed here:
        # render each newest frame as soon as it becomes available.
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-rtsp_transport", "udp", "-fflags", "nobuffer",
            "-flags", "low_delay", "-probesize", "32", "-analyzeduration", "0",
            "-max_delay", "0", "-reorder_queue_size", "0", "-i", url,
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", "scale=1920:1080:flags=fast_bilinear",
            "-fps_mode", "passthrough", "-pix_fmt", "rgb24",
            "-c:v", "rawvideo", "-f", "rawvideo", "pipe:1",
        ]
        # Inherit stderr so connection/decoder errors stay visible, without a
        # second pipe that could fill up and block the decoder.
        process = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=0)
        latest_frame = deque(maxlen=1)
        finished = threading.Event()
        reader = threading.Thread(target=read_frames,
                                  args=(process.stdout, latest_frame, finished),
                                  daemon=True)
        reader.start()
        clock = pygame.time.Clock()
        running = True
        while running and (stop_event is None or not stop_event.is_set()):
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN
                    and event.key in (pygame.K_ESCAPE, pygame.K_q)
                ):
                    running = False
            try:
                # deque append/popleft are thread-safe; the producer drops stale
                # frames automatically when this one-slot deque is full.
                frame = latest_frame.popleft()
            except IndexError:
                if finished.is_set():
                    raise RuntimeError("Video stream ended. Check FFmpeg diagnostics above.")
            else:
                surface = pygame.image.frombuffer(frame, SIZE, "RGB")
                screen.blit(surface, (0, 0))
                pygame.display.flip()
            clock.tick(240)  # Short event-poll wait; no intentional video buffering.
    finally:
        # Terminating FFmpeg unblocks the reader even if the camera is silent.
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if reader is not None:
                reader.join(timeout=3)
            process.stdout.close()
        pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, configparser.Error, KeyError, pygame.error) as error:
        raise SystemExit(str(error)) from error
