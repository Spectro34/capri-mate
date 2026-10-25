#!/usr/bin/env python3
"""Turn the SAM 2 cutouts (mate-src/<shot>/) into the desktop mate's sprites (mate/).

  .venv/bin/python build_mate.py [--height 260] [--glow 0.5]

mate-anims.json lists the shots and their roles. Each shot is scaled to --height px (times its `size`) and
anchored on her face, so cutting from one shot to another keeps her face where it was. A shot with `like` (same
camera framing as another, e.g. her glitching in) takes that shot's scale and face spot. `face_at` gives the face
spot by hand (source-frame pixels) where her irises aren't blue enough to find it. Resampling is premultiplied (no dark fringes). It also writes a few `glitch` frames (displaced slices + a
red/green split, the LCD's cut effect) from the first hover frame. Pixels stay the original frames'.
Rig actions (rig/rig.py, src rig_<action>) are one PNG per drawing; their cut.json `hold` (frames each drawing is
held, at its `fps`) goes into mate.json, and their empty margins are cropped off.
Each sprite gets a soft glow in her red around her outline (--glow, 0 = none; the glow lies under her, her pixels stay
as they are), and mate.json lists each frame's biggest hair sparkles so the mate can make them twinkle. Face anchors
are whole pixels, so an upright frame is drawn 1:1 with no resampling blur.
Writes mate/<anim>/NNN.png and mate/mate.json.
"""
import argparse
import json
import os
import random
import shutil

import cv2
import numpy as np
from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
GREEN = np.array([9, 236, 110])
BLUE = np.array([30, 110, 220])


def face(rgba):
    """Centre and width of her face: the green region with her blue irises in it (None if not visible)."""
    a = rgba[..., :3].astype(np.int16)
    on = rgba[..., 3] > 128
    g = (np.abs(a - GREEN).sum(-1) < 120) & on
    b = (np.abs(a - BLUE).sum(-1) < 120) & on
    ys, xs = np.nonzero(b)
    if len(xs) < 6:
        return None
    cx, cy = np.median(xs), np.median(ys)
    gy, gx = np.nonzero(g)
    near = (np.abs(gx - cx) < 120) & (np.abs(gy - cy) < 80)
    if near.sum() < 20:
        return None
    gx, gy = gx[near], gy[near]
    return float(np.median(gx)), float(np.median(gy)), float(np.percentile(gx, 95) - np.percentile(gx, 5))


GLOW = (250, 2, 69)        # her red
GPAD = 8                   # px around each sprite for the glow


def glow(im, strength):
    """Her red, softly, just outside her outline, under her."""
    a = np.asarray(im)
    pad = np.pad(a, ((GPAD, GPAD), (GPAD, GPAD), (0, 0)))
    al = pad[..., 3].astype(np.float32) / 255
    halo = np.maximum(cv2.GaussianBlur(al, (0, 0), 2.2), cv2.GaussianBlur(al, (0, 0), 5) * 0.8) * strength
    out = np.zeros_like(pad, np.float32)
    out[..., :3] = GLOW
    out[..., 3] = np.clip(halo, 0, 1) * 255
    base = Image.fromarray(out.astype(np.uint8), "RGBA")
    base.alpha_composite(Image.fromarray(pad, "RGBA"))
    return base


def sparkles(im, most=28):
    """Her hair's sparkles: the biggest white specks inside her red, as [x, y, r] (sprite px)."""
    a = np.asarray(im).astype(np.int16)
    white = (a[..., 3] > 200) & (a[..., 0] > 205) & (a[..., 1] > 165) & (a[..., 2] > 170)
    red = ((a[..., 0] > 180) & (a[..., 1] < 90)).astype(np.uint8)
    near_red = cv2.dilate(red, np.ones((5, 5), np.uint8)) > 0
    n, cc, st, cen = cv2.connectedComponentsWithStats((white & near_red).astype(np.uint8), 8)
    if n <= 1:
        return []
    order = np.argsort(-st[1:, cv2.CC_STAT_AREA])[:most] + 1
    return [[int(round(cen[i][0])), int(round(cen[i][1])), round(float(np.sqrt(st[i, cv2.CC_STAT_AREA])), 1)] for i in order]


def resize(im, scale):
    w, h = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    return im.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")


def glitch(im, rng):
    out = im.copy()
    w, h = im.size
    for _ in range(7):
        y = rng.randrange(0, max(1, h - 8))
        sh = rng.randrange(3, max(4, h // 10))
        dx = rng.randrange(-w // 6, w // 6)
        strip = im.crop((0, y, w, y + sh))
        out.paste((0, 0, 0, 0), (0, y, w, y + sh))
        out.paste(strip, (dx, y))
    r, g, b, a = out.split()
    k = rng.randrange(3, 8)
    return Image.merge("RGBA", (ImageChops.offset(r, k, 0), ImageChops.offset(g, -k, 0), b, a))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--height", type=int, default=260, help="her height on screen, px")
    ap.add_argument("--glow", type=float, default=0.5, help="strength of the red glow around her (0 = none)")
    args = ap.parse_args()
    spec = json.load(open(f"{HERE}/mate-anims.json"))
    out = f"{HERE}/mate"
    shutil.rmtree(out, ignore_errors=True)

    meta = {"height": args.height, "anims": {}}
    done = {}                                    # name -> (scale, face anchor in source-frame coordinates)
    for an in spec["anims"]:
        d = f"{HERE}/mate-src/{an['src']}"
        if not os.path.exists(f"{d}/cut.json"):
            print(f"{an['name']}: skipped, {d} not built (rig actions need Blender: setup.sh)")
            continue
        cut = json.load(open(f"{d}/cut.json"))
        x0, y0 = cut["crop"][:2]
        files = sorted(f for f in os.listdir(d) if f.endswith(".png"))[an.get("a", 0):an.get("b")]
        ims = [Image.open(f"{d}/{f}") for f in files]
        if "hold" in cut:
            # a rig action: trim the transparent margin around all its drawings
            box = [min(v) if i < 2 else max(v) for i, v in enumerate(zip(*(im.getchannel("A").getbbox() or (0, 0, 1, 1) for im in ims)))]
            ims = [im.crop(box) for im in ims]
            x0, y0 = x0 + box[0], y0 + box[1]
        if "like" in an:
            # same camera framing as another shot (e.g. the glitch-in of the float shot): its scale and face spot
            scale, (ax, ay) = done[an["like"]]
            fx = np.full(len(ims), ax - x0)
            fy = np.full(len(ims), ay - y0)
        elif "face_at" in an:
            # her face spot given by hand, in source-frame pixels (when her irises aren't blue enough to find)
            scale = args.height * an.get("size", 1.0) / max(im.height for im in ims)
            ax, ay = an["face_at"]
            fx = np.full(len(ims), float(ax - x0))
            fy = np.full(len(ims), float(ay - y0))
            done[an["name"]] = (scale, (float(ax), float(ay)))
        else:
            scale = args.height * an.get("size", 1.0) / max(im.height for im in ims)
            fs = [face(np.asarray(im)) for im in ims]
            idx = np.arange(len(fs))
            ok = np.array([f is not None for f in fs])
            if not ok.any():
                raise SystemExit(f"{an['name']}: no face found")
            # per-frame face anchor, gaps filled from neighbours; `steady` keeps one anchor for the whole shot
            fx = np.interp(idx, idx[ok], [f[0] for f in fs if f])
            fy = np.interp(idx, idx[ok], [f[1] for f in fs if f])
            if an.get("steady", True):
                fx[:], fy[:] = np.median(fx), np.median(fy)
            done[an["name"]] = (scale, (float(np.median(fx)) + x0, float(np.median(fy)) + y0))
        os.makedirs(f"{out}/{an['name']}")
        frames, spark = [], []
        for i, im in enumerate(ims):
            s = resize(im, scale)
            g = GPAD if args.glow > 0 else 0
            spark.append([[x + g, y + g, r] for x, y, r in sparkles(s)])
            s = glow(s, args.glow) if g else s
            s.save(f"{out}/{an['name']}/{i:03d}.png", optimize=True)
            frames.append([round(float(fx[i]) * scale) + g, round(float(fy[i]) * scale) + g])
        meta["anims"][an["name"]] = dict(role=an["role"], n=len(ims), fps=an.get("fps", cut.get("fps", 20)),
                                         loop=an.get("loop", cut.get("loop", "pingpong")), size=[s.width, s.height], face=frames,
                                         sparkles=spark)
        if "hold" in cut:
            meta["anims"][an["name"]]["hold"] = cut["hold"]
        print(f"{an['name']}: {len(ims)} frames, {s.width}x{s.height}, scale {scale:.3f}")

    hov = next(a for a in spec["anims"] if a["role"] == "hover")
    rng = random.Random(7)
    base = Image.open(f"{out}/{hov['name']}/000.png")
    os.makedirs(f"{out}/glitch")
    for i in range(6):
        glitch(base, rng).save(f"{out}/glitch/{i:03d}.png", optimize=True)
    h = meta["anims"][hov["name"]]
    meta["anims"]["glitch"] = dict(role="glitch", n=6, fps=20, loop="once", size=list(base.size), face=[h["face"][0]] * 6,
                                   sparkles=[[]] * 6)
    json.dump(meta, open(f"{out}/mate.json", "w"), indent=1)
    print(f"-> {out}/mate.json")


if __name__ == "__main__":
    main()
