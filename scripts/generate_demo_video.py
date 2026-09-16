"""Regenerates assets/demo_camera.mp4 - a synthetic, reproducible clip standing
in for a fixed security camera.

Why synthetic rather than downloaded footage: no licensing question, no
network dependency for a reviewer re-running the build, and - the actual
reason - we control ground truth. We know exactly when motion happens (three
passes, at specific second-ranges within a 40s loop) so the detector's output
can be checked against a known-correct answer instead of eyeballing real
footage.

Written directly with OpenCV rather than ffmpeg's drawbox filter: ffmpeg 9.0.1
has a bug where drawbox's `x`/`y` position expressions never re-evaluate when
they reference `t` or `n` (confirmed by testing - a constant expression
renders, one that reads the time variable silently draws nothing, every
frame, for the entire clip). Generating frames as numpy arrays sidesteps it
entirely, and costs nothing here: this footage is only ever read back by our
own OpenCV worker, never embedded in a browser <video> tag, so there is no
reason to round-trip through ffmpeg or care about H.264 compatibility.
"""

from __future__ import annotations

import cv2
import numpy as np

OUT = "assets/demo_camera.mp4"
WIDTH, HEIGHT = 640, 360
FPS = 15
DURATION_S = 40
BACKGROUND_BGR = (34, 26, 21)  # a dim loading-dock gray, BGR order
NOISE_SIGMA = 4.0  # camera grain - also what proves the detector needs a threshold

# (start_s, end_s, y, height, box_width, x_start, x_end, color_bgr)
PASSES = [
    (3.0, 7.0, 140, 140, 34, 40, 600, (212, 202, 194)),
    (15.0, 20.0, 115, 155, 36, 600, 40, (204, 192, 183)),
    (26.0, 34.0, 150, 128, 40, 50, 590, (196, 179, 170)),
]


def render_frame(t: float, rng: np.random.Generator) -> np.ndarray:
    frame = np.full((HEIGHT, WIDTH, 3), BACKGROUND_BGR, dtype=np.float32)
    frame += rng.normal(0, NOISE_SIGMA, frame.shape)

    for start, end, y, h, w, x_start, x_end, color in PASSES:
        if start <= t < end:
            progress = (t - start) / (end - start)
            x = int(x_start + progress * (x_end - x_start))
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, -1)

    return np.clip(frame, 0, 255).astype(np.uint8)


def main() -> None:
    rng = np.random.default_rng(seed=20260917)  # deterministic grain
    writer = cv2.VideoWriter(
        OUT, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        raise RuntimeError("cv2.VideoWriter failed to open for writing")

    total_frames = DURATION_S * FPS
    for frame_idx in range(total_frames):
        writer.write(render_frame(frame_idx / FPS, rng))
    writer.release()

    import os

    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1024:.0f} KiB, {total_frames} frames)")


if __name__ == "__main__":
    main()
