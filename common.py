"""Shared by the build scripts: her cel palette, and reading video frames through ffmpeg."""
import subprocess

import numpy as np

# Capri's flat colours, as drawn in the show
NAMES = ["red", "green", "violet", "black", "blue", "white"]
PAL = np.array([[250, 2, 69], [9, 236, 110], [137, 47, 209], [4, 2, 16], [30, 110, 220], [245, 245, 245]], np.float32)
RED, GREEN, VIOLET, BLACK, BLUE, WHITE, BG = 0, 1, 2, 3, 4, 5, 6
FPS = 20                     # frame numbers everywhere count at 20 fps


def labels(a):
    """Nearest palette colour per pixel (HxW ints), BG where nothing is close enough."""
    d = ((a[..., None, :] - PAL) ** 2).sum(-1)
    lab = d.argmin(-1)
    far = d.min(-1) > np.where(lab == BLACK, 34, 60) ** 2     # cel black is very uniform; dark rooms are not
    lab[far] = BG
    return lab


def video_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def video_frames(path, a=0, b=None, width=None, start=0.0):
    """Yield RGB uint8 frames a..b-1 of a video resampled to 20 fps, counting from `start` seconds in."""
    W, H = video_size(path)
    if width:
        W, H = width, round(H * width / W / 2) * 2
    vf = f"fps={FPS},trim=start_frame={a}" + (f":end_frame={b}" if b is not None else "") + f",scale={W}:{H}"
    cmd = ["ffmpeg", "-v", "error", "-nostdin"] + (["-ss", str(start)] if start else []) + \
        ["-i", path, "-an", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = W * H * 3
    try:
        while (buf := p.stdout.read(n)) and len(buf) == n:
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    finally:
        p.stdout.close()
        p.kill()
        p.wait()
