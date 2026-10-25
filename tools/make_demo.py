#!/usr/bin/env python3
"""Render the README's demo GIF from the built sprites (mate/), on a mock desktop: no screen recording, so nothing
private ends up in it. Her pixels are the sprites'; the glints and streaks are fx.py's, like the live mate.

  .venv/bin/python tools/make_demo.py [--out docs/demo.gif] [--width 640]

Needs cairo (pycairo) and ffmpeg (for the GIF palette). 12 fps: she's drawn on 2s, so that's one drawing per frame.
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile

import cairo

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import fx  # noqa: E402

FPS = 12
W, H = 640, 360
SCALE = 0.55                 # sprites are built 260 px tall; she's ~145 px on the 640 px mock screen
RED, GREEN, INK, TEXT, DIM = (250, 2, 69), (9, 236, 110), (10, 6, 20), (236, 236, 244), (138, 138, 163)


def rgb(c, a=1.0):
    return (c[0] / 255, c[1] / 255, c[2] / 255, a)


class Sprites:
    def __init__(self, path):
        self.meta = json.load(open(f"{path}/mate.json"))["anims"]
        self.img = {n: [cairo.ImageSurface.create_from_png(f"{path}/{n}/{i:03d}.png") for i in range(a["n"])]
                    for n, a in self.meta.items()}

    def frames(self, name, secs=None):
        """Drawing indices for `name` at 12 fps: its holds (at 24 fps) halved, or its own fps; loops to fill `secs`."""
        a = self.meta[name]
        if "hold" in a:
            seq24 = [i for i, h in enumerate(a["hold"]) for _ in range(h)]
            seq = seq24[::2] or [0]
        else:
            n = a["n"]
            ping = list(range(n)) + list(range(n - 2, 0, -1)) if a.get("loop") == "pingpong" else list(range(n))
            seq = [ping[int(k * a["fps"] / FPS) % len(ping)] for k in range(int(len(ping) * FPS / a["fps"]))]
        if secs is None:
            return seq
        want = int(secs * FPS)
        return (seq * (want // len(seq) + 1))[:want]


def desktop(cr):
    g = cairo.LinearGradient(0, 0, W, H)
    g.add_color_stop_rgb(0, 0.05, 0.06, 0.09)
    g.add_color_stop_rgb(1, 0.10, 0.07, 0.17)
    cr.set_source(g)
    cr.paint()
    cr.set_source_rgba(1, 1, 1, 0.035)
    for x in range(10, W, 24):
        for y in range(10, H, 24):
            cr.rectangle(x, y, 1.2, 1.2)
    cr.fill()
    # a mock editor window
    x, y, w, h = 250, 60, 300, 190
    rounded(cr, x, y, w, h, 8)
    cr.set_source_rgba(0.08, 0.09, 0.13, 0.96)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.08)
    cr.set_line_width(1)
    cr.stroke()
    for i, c in enumerate(((0.98, 0.37, 0.34), (0.99, 0.74, 0.18), (0.16, 0.79, 0.25))):
        cr.arc(x + 14 + i * 14, y + 13, 4, 0, 2 * math.pi)
        cr.set_source_rgb(*c)
        cr.fill()
    rng = random.Random(3)
    for i in range(9):
        indent = rng.choice((0, 0, 14, 28))
        for j, (ww, col) in enumerate(((rng.randint(30, 70), (0.55, 0.62, 0.98)), (rng.randint(40, 150), (0.75, 0.77, 0.83)))):
            cr.set_source_rgba(*col, 0.55)
            rounded(cr, x + 16 + indent + (0 if j == 0 else 80), y + 34 + i * 16, ww, 6, 3)
            cr.fill()


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def caption(cr, text):
    cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(14)
    tw = cr.text_extents(text).x_advance
    x, y = 16, H - 44
    rounded(cr, x, y, tw + 28, 30, 7)
    cr.set_source_rgba(*rgb(INK, 0.9))
    cr.fill()
    cr.rectangle(x, y + 4, 4, 22)
    cr.set_source_rgba(*rgb(RED))
    cr.fill()
    cr.move_to(x + 16, y + 20)
    cr.set_source_rgba(*rgb(TEXT))
    cr.show_text(text)
    cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(11)
    cr.move_to(16, 22)
    cr.set_source_rgba(*rgb(DIM, 0.9))
    cr.show_text("capri-mate")


def bubble(cr, x, y, you, her):
    """Her speech bubble, as mate.py's CSS draws it."""
    w = 236
    cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    lines = [("you: " + you, DIM, 11)] + [(ln, (255, 217, 228), 12) for ln in wrap(cr, her, w - 28, 12)]
    h = 18 + 16 * len(lines) + 30
    rounded(cr, x, y, w, h, 10)
    cr.set_source_rgba(*rgb(INK, 0.94))
    cr.fill_preserve()
    cr.set_source_rgba(*rgb(RED))
    cr.set_line_width(2)
    cr.stroke()
    cr.rectangle(x + 1, y + 8, 4, h - 16)
    cr.set_source_rgba(*rgb(GREEN))
    cr.fill()
    ty = y + 22
    for text, col, size in lines:
        cr.set_font_size(size)
        cr.set_source_rgba(*rgb(col))
        cr.move_to(x + 14, ty)
        cr.show_text(text)
        ty += 16
    rounded(cr, x + 12, ty - 4, w - 84, 22, 5)
    cr.set_source_rgba(0.08, 0.08, 0.11, 1)
    cr.fill()
    cr.set_font_size(11)
    cr.set_source_rgba(*rgb(DIM, 0.7))
    cr.move_to(x + 20, ty + 11)
    cr.show_text("say something…")
    for i, lab in enumerate(("mic", "×")):
        bx = x + w - 66 + i * 32
        rounded(cr, bx, ty - 4, 28, 22, 5)
        cr.set_source_rgba(0.08, 0.08, 0.11, 1)
        cr.fill()
        cr.set_source_rgba(*rgb(GREEN))
        cr.move_to(bx + (5 if i == 0 else 10), ty + 11)
        cr.show_text(lab)


def wrap(cr, text, width, size):
    cr.set_font_size(size)
    out, line = [], ""
    for word in text.split():
        t = (line + " " + word).strip()
        if cr.text_extents(t).x_advance > width and line:
            out.append(line)
            line = word
        else:
            line = t
    return out + ([line] if line else [])


def cursor(cr, x, y, click=0.0):
    if click:
        cr.arc(x, y, 6 + 14 * click, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.6 * (1 - click))
        cr.set_line_width(2)
        cr.stroke()
    cr.move_to(x, y)
    for px, py in ((0, 17), (4.5, 13), (7.5, 20), (10, 19), (7, 12.5), (12.5, 12.5)):
        cr.line_to(x + px, y + py)
    cr.close_path()
    cr.set_source_rgb(1, 1, 1)
    cr.fill_preserve()
    cr.set_source_rgb(0, 0, 0)
    cr.set_line_width(1.2)
    cr.stroke()


def script(sp):
    """The demo as a list of frames: (anim, drawing, her spot or None, zap step or None, caption, extras)."""
    A, B = (150, 235), (520, 215)
    out = []

    def add(name, drawings, pos, cap, zap_from=None, **extra):
        for k, i in enumerate(drawings):
            z = k - zap_from if zap_from is not None and 0 <= k - zap_from < 2 else None
            out.append(dict(anim=name, i=i, pos=pos, zap=z, cap=cap, **{key: (v(k) if callable(v) else v) for key, v in extra.items()}))

    cap = "Capri lives on your desktop"
    add("appear", sp.frames("appear"), A, cap, zap_from=0)
    add("float", sp.frames("float", 1.6), A, cap)
    cap = "she notices your cursor"
    path = lambda k: (W - 40 - (W - 40 - 320) * min(1, k / 10), 300 - 40 * min(1, k / 10))
    add("float", sp.frames("float", 0.9), A, cap, cur=path)
    add("look_r", sp.frames("look_r")[:14], A, cap, cur=(320, 260))
    cap = "poke her: hair flares (or she blinks off)"
    move = lambda k: (320 - (320 - A[0]) * min(1, k / 6), 260 - (260 - A[1] + 6) * min(1, k / 6))
    add("float", sp.frames("float", 0.6), A, cap, cur=move)
    add("flare", sp.frames("flare"), A, cap, zap_from=0, cur=(A[0], A[1] - 6), click=lambda k: k / 4 if k < 4 else 0.0)
    cap = "she never floats: she zaps"
    add("vanish", sp.frames("vanish"), A, cap, cur=(A[0] + 40, A[1] + 40))
    add("vanish", [None] * 4, None, cap, cur=(A[0] + 40, A[1] + 40))
    add("appear", sp.frames("appear"), B, cap, zap_from=0, cur=(A[0] + 40, A[1] + 40))
    add("float", sp.frames("float", 1.0), B, cap)
    cap = "talk to her: she answers out loud (optional)"
    said = "Always. Watching you work is my favourite show."
    rng = random.Random(5)
    mouth = [rng.random() < 0.55 for _ in range(60)]
    talk = sp.frames("talk")
    n = 50
    add("talk", [(1 if mouth[k] else 0) + (2 if (k // 14) % 2 else 0) if k < 36 else 0 for k in range(n)], B, cap, zap_from=0,
        bubble=lambda k: ("hey capri, busy?", said[:max(0, int(len(said) * min(1, k / 30)))]))
    del talk
    cap = "drawn on 2s and 3s, like the show"
    add("whip", sp.frames("whip")[:22], B, cap, zap_from=3)
    add("grin", sp.frames("grin")[:12], B, cap)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sprites", default=f"{HERE}/mate")
    ap.add_argument("--out", default=f"{HERE}/docs/demo.gif")
    ap.add_argument("--width", type=int, default=W)
    args = ap.parse_args()
    sp = Sprites(args.sprites)
    frames = script(sp)
    rng = random.Random(1)
    glints = []
    tmp = tempfile.mkdtemp()
    for n, f in enumerate(frames):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        cr = cairo.Context(surf)
        desktop(cr)
        if f["pos"] and f["i"] is not None:
            a = sp.meta[f["anim"]]
            i = min(f["i"], a["n"] - 1)
            fx_, fy_ = a["face"][i]
            cr.save()
            cr.translate(*f["pos"])
            cr.scale(SCALE, SCALE)
            cr.set_source_surface(sp.img[f["anim"]][i], -fx_, -fy_)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            sparks = a.get("sparkles", [[]] * a["n"])[i]
            glints = [g for g in glints if n - g[3] < 3]
            if sparks and rng.random() < 0.45:
                x, y, r = rng.choice(sparks[:12])
                glints.append((x, y, r, n))
            for x, y, r, t0 in glints:
                fx.glint(cr, x - fx_, y - fy_, r, n - t0)
            if f["zap"] is not None:
                fx.streaks(cr, random.Random(n * 7 + 3), a["size"])
            cr.restore()
        else:
            glints = []
        if f.get("bubble"):
            you, her = f["bubble"]
            bubble(cr, f["pos"][0] - 76 - 236, f["pos"][1] - 120, you, her or "…")
        caption(cr, f["cap"])
        if f.get("cur"):
            cursor(cr, *f["cur"], f.get("click") or 0.0)
        surf.write_to_png(f"{tmp}/{n:04d}.png")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    scale = f"scale={args.width}:-1:flags=lanczos," if args.width != W else ""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", f"{tmp}/%04d.png", "-vf",
                    f"{scale}split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
                    "-loop", "0", args.out], check=True)
    for name in os.listdir(tmp):
        os.remove(f"{tmp}/{name}")
    os.rmdir(tmp)
    print(f"{args.out}: {len(frames)} frames, {len(frames) / FPS:.1f} s, {os.path.getsize(args.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
