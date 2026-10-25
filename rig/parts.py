#!/usr/bin/env python3
"""Split Capri (one cutout frame of the float shot) into rig layers, all from the original pixels.

  .venv/bin/python rig/parts.py [--ref 5] [--grin 14]

Layers (full-canvas RGBA PNGs in rig/parts/, so they line up without offsets), back to front:
  under       her cel black where the head was, so a turned or moved head never shows a hole
  body        everything below the chin (neck, shoulders, the red and green panels)
  head        face mask and jaw, irises taken out (their sockets filled with the socket's own black)
  iris_l/_r   each eye's ring, pupil and highlight, to move inside its socket
  grin        the open grin (from the --grin frame), drawn over the jaw when she grins
  hair        everything else: the red mass, sparkles, violet edge, black underside, the strand across her face
parts.json gives the canvas size and the pivots (neck, head centre, eye centres) in canvas pixels.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common import BLACK, GREEN, PAL, RED, WHITE, labels  # noqa: E402

SRC = f"{os.path.dirname(HERE)}/mate-src/float"
FACE_AT = (960, 905)        # her face in the source frame (mate-anims.json)
PAD = 160                   # room around her for the hair to move into


def rgba(i):
    return np.asarray(Image.open(f"{SRC}/{i:03d}.png")).copy()


def save(a, name):
    Image.fromarray(a, "RGBA").save(f"{HERE}/parts/{name}.png")


def face_mask(lab, near):
    """The green face mask: the green regions around the face spot (the strand across her face splits it)."""
    n, cc, st, cen = cv2.connectedComponentsWithStats((lab == GREEN).astype(np.uint8), 8)
    keep = [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= 40 and np.hypot(*(cen[i] - near)) < 70]
    return np.isin(cc, keep)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", type=int, default=5, help="cutout frame with the closed mouth")
    ap.add_argument("--grin", type=int, default=14, help="cutout frame with the open grin")
    args = ap.parse_args()
    os.makedirs(f"{HERE}/parts", exist_ok=True)
    crop = json.load(open(f"{SRC}/cut.json"))["crop"]
    near = np.array([FACE_AT[0] - crop[0], FACE_AT[1] - crop[1]], float)

    a = rgba(args.ref)
    a = np.pad(a, ((PAD, PAD), (PAD, PAD), (0, 0)))
    near += PAD
    rgb, al = a[..., :3].astype(np.float32), a[..., 3]
    lab = labels(rgb)
    lab[al < 128] = -1
    H, W = al.shape

    # head: the face mask plus the black jaw and white teeth just under it
    green = face_mask(lab, near)
    ys, xs = np.nonzero(green)
    gx0, gy0, gx1, gy1 = xs.min(), ys.min(), xs.max(), ys.max()
    teeth = np.zeros_like(green)
    n, cc, st, _ = cv2.connectedComponentsWithStats((lab == WHITE).astype(np.uint8), 8)
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if gx0 - 5 <= x and x + w <= gx1 + 5 and gy1 - 2 <= y <= gy1 + 30 and area >= 8:
            teeth |= cc == i
    pts = np.column_stack(np.nonzero(green | teeth))[:, ::-1].astype(np.int32)
    hull = cv2.convexHull(pts)
    head = np.zeros((H, W), np.uint8)
    cv2.fillConvexPoly(head, hull, 1)
    # the jaw: black under the mask, down to the chin point
    jaw = np.zeros((H, W), np.uint8)
    chin_y = int(np.nonzero(teeth)[0].max()) + 16 if teeth.any() else gy1 + 26
    cx = (gx0 + gx1) // 2
    cv2.fillConvexPoly(jaw, np.array([[gx0 + 6, gy1], [gx1 - 6, gy1], [cx + 8, chin_y], [cx - 4, chin_y]], np.int32), 1)
    head = (cv2.dilate(head | jaw, np.ones((5, 5), np.uint8)) > 0) & (al > 0)
    head &= (lab == BLACK) | (lab == GREEN) | (lab == WHITE) | (lab == 4) | (lab == 6) | green | teeth
    head = cv2.morphologyEx(head.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
    strand = (cv2.dilate(head.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0) & (lab == RED)   # hair in front of her face

    # irises: rings found as circles inside the mask
    face_gray = cv2.cvtColor(np.ascontiguousarray(a[..., :3]), cv2.COLOR_RGB2GRAY)
    roi = face_gray[gy0:gy1, gx0:gx1]
    circles = cv2.HoughCircles(cv2.GaussianBlur(roi, (3, 3), 0), cv2.HOUGH_GRADIENT, dp=1, minDist=25, param1=80, param2=12,
                               minRadius=6, maxRadius=16)
    if circles is None or len(circles[0]) < 2:
        raise SystemExit("irises not found")
    cs = sorted(circles[0][:2], key=lambda c: c[0])
    eyes = []
    disks = np.zeros((H, W), bool)
    head_rgba = np.where(head[..., None], a, 0).astype(np.uint8)
    socket_black = np.array([*PAL[BLACK].astype(np.uint8), 255], np.uint8)
    for name, (ex, ey, r) in zip(("iris_l", "iris_r"), cs):
        ex, ey, r = gx0 + float(ex), gy0 + float(ey), float(r) + 1.5
        disk = np.zeros((H, W), np.uint8)
        cv2.circle(disk, (round(ex * 4), round(ey * 4)), round(r * 4), 1, -1, cv2.LINE_8, 2)
        disk = disk > 0
        iris = np.where(disk[..., None], a, 0).astype(np.uint8)
        save(iris, name)
        head_rgba[disk] = socket_black
        disks |= disk
        eyes.append(dict(name=name, center=[round(ex, 1), round(ey, 1)], r=round(r, 1)))
    save(head_rgba, "head")

    # the open grin, lined up on the mask of its own frame
    g = np.pad(rgba(args.grin), ((PAD, PAD), (PAD, PAD), (0, 0)))
    glab = labels(g[..., :3].astype(np.float32))
    glab[g[..., 3] < 128] = -1
    gm = face_mask(glab, near)
    gy, gx = np.nonzero(gm)
    dx, dy = int(round(gx0 - gx.min())), int(round(gy0 - gy.min()))
    g = np.roll(g, (dy, dx), (0, 1))
    mouth = np.zeros((H, W), bool)
    mouth[gy1 - 1:chin_y + 2, gx0:gx1 + 1] = True
    mouth &= head & (g[..., 3] > 0)
    save(np.where(mouth[..., None], g, 0).astype(np.uint8), "grin")

    # body: below the chin, around the neck
    body = np.zeros((H, W), bool)
    body[chin_y + 2:, gx0 - 70:gx1 + 70] = True
    body &= (al > 0) & ~head & ~disks
    n, cc, st, _ = cv2.connectedComponentsWithStats((al > 0).astype(np.uint8) & ~(head | body).astype(np.uint8), 8)
    save(np.where(body[..., None], a, 0).astype(np.uint8), "body")

    # hair: the rest, including the strand across her face
    hair = (al > 0) & ~head & ~body
    hair |= strand
    hair &= ~disks                                            # the eyes belong to the head, whole
    save(np.where(hair[..., None], a, 0).astype(np.uint8), "hair")

    # under: her black wherever the head is, slightly larger, so moving the head never opens a hole
    under = cv2.dilate((head | disks).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    under &= al > 0                                           # within her silhouette, as opaque as she is there
    u = np.zeros_like(a)
    u[under] = socket_black
    u[..., 3] = np.where(under, al, 0)
    save(u, "under")

    neck = [float(cx + 2), float(chin_y)]
    meta = dict(size=[W, H], ref=args.ref, grin=args.grin, pad=PAD, crop=crop,
                face_box=[int(gx0), int(gy0), int(gx1), int(gy1)], neck=neck,
                head_center=[float((gx0 + gx1) / 2), float((gy0 + gy1) / 2)], eyes=eyes,
                order=["under", "body", "head", "iris_l", "iris_r", "grin", "hair"])
    json.dump(meta, open(f"{HERE}/parts/parts.json", "w"), indent=1)
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
