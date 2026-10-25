#!/usr/bin/env python3
"""Cut Capri out of a shot with a transparent background, for the desktop mate.

  .venv/bin/python cutout.py SRC A B OUT_DIR [--start SECONDS] [--width PX] [--frame N] [--pos X,Y ...] [--neg X,Y ...]

SRC is a video; frames are numbered at 20 fps from --start seconds in (and scaled to --width px, if given). SAM 2 (facebook/sam2.1-hiera-base-plus,
via transformers, CPU) tracks her through frames A..B-1 from prompts it places itself on the prompt frame (the
frame with the most of her green face, or --frame): a click on her face, clicks spread over her red hair and body,
and a box around her. --neg adds negative clicks (prompt-frame pixels) on anything it must leave out; --box
overrides the box.

SAM's mask is soft at the scale of her hair spikes, so the edge is taken from the art itself: inside the SAM mask
(shrunk a little) everything is kept; in a thin band around its edge only pixels in her cel colours are kept. Pixels
are the original frame's; only the background becomes transparent. Where the camera frame cut her off, the alpha
fades out over FADE px instead of ending in a straight line. Writes OUT_DIR/NNN.png (RGBA, cropped to the
shot's union box) and OUT_DIR/cut.json.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BLACK, BLUE, GREEN, RED, VIOLET, WHITE, labels, video_frames  # noqa: E402

MODEL = "facebook/sam2.1-hiera-base-plus"
BAND = 7          # px around SAM's edge where the cel colours decide
FADE = 36         # px: where the camera frame cut her off, the cut edge fades out instead of ending in a straight line


def prompts(a, rng):
    """Clicks and a box on the prompt frame. Face: the green region with blue irises in it. Hair and body: points
    spread by farthest-point sampling over her red, closed so the sparkles don't break it up. Box: her colours'
    connected bulk (red, green, violet, blue), padded."""
    lab = labels(a.astype(np.float32))
    k9 = np.ones((9, 9), np.uint8)
    pts = []
    green = (lab == GREEN).astype(np.uint8)
    blue = (lab == BLUE).astype(np.uint8)
    n, cc, st, cen = cv2.connectedComponentsWithStats(green)

    def iris(i):                                              # iris-blue pixels inside this green region's box
        x, y, w, h = st[i, :4]
        return int(blue[y:y + h, x:x + w].sum())
    faces = [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= 30 and iris(i) > 0]
    if faces:
        k = max(faces, key=iris)
        ys, xs = np.nonzero(cc == k)
        i = np.argmin((xs - cen[k][0]) ** 2 + (ys - cen[k][1]) ** 2)    # a face pixel nearest its centre
        pts.append([int(xs[i]), int(ys[i])])
    red = cv2.morphologyEx((lab == RED).astype(np.uint8), cv2.MORPH_CLOSE, k9)
    red = cv2.erode(red, np.ones((5, 5), np.uint8))
    ys, xs = np.nonzero(red)
    if len(xs):
        cand = np.stack([xs, ys], 1)[rng.choice(len(xs), min(6000, len(xs)), replace=False)]
        ref = np.array(pts or [cand[0]], np.float64)
        chosen = [] if pts else [cand[0]]
        for _ in range(6):
            d = np.min(((cand[:, None, :] - ref[None]) ** 2).sum(-1), 1)
            chosen.append(cand[int(np.argmax(d))])
            ref = np.vstack([ref, chosen[-1]])
        pts += [[int(x), int(y)] for x, y in chosen]
    her = cv2.morphologyEx(np.isin(lab, [RED, GREEN, VIOLET, BLUE]).astype(np.uint8), cv2.MORPH_CLOSE, k9, iterations=2)
    n, cc, st, _ = cv2.connectedComponentsWithStats(her)
    big = st[1:, cv2.CC_STAT_AREA].max() if n > 1 else 0
    keep = np.isin(cc, [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= big * 0.05])
    ys, xs = np.nonzero(keep)
    H, W = lab.shape
    box = [max(0, int(xs.min()) - 10), max(0, int(ys.min()) - 10), min(W - 1, int(xs.max()) + 10), min(H - 1, int(ys.max()) + 10)]
    return pts, box


def refine(a, sam):
    lab = labels(a.astype(np.float32))
    k = np.ones((3, 3), np.uint8)
    inner = cv2.erode(sam, k, iterations=BAND)
    outer = cv2.dilate(sam, k, iterations=BAND)
    # in the band only her cel colours count: a dark room reads as "black" too, a lit window as "white"
    m = inner | (outer & np.isin(lab, [RED, GREEN, VIOLET, BLUE]).astype(np.uint8))
    # holes inside her silhouette that are her cel black (hair underside, body against a black background) go back in
    H, W = m.shape
    n, cc, st, _ = cv2.connectedComponentsWithStats((1 - m).astype(np.uint8), 4)
    black = lab == BLACK
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if x == 0 or y == 0 or x + w == W or y + h == H or area > H * W * 0.01:
            continue                                          # open to the frame edge, or too big to be part of her
        if black[cc == i].mean() >= 0.6:
            m[cc == i] = 1
    # big white shapes on her outline are the background (lit windows, an edit's white bars); her own whites
    # (teeth, eye highlights, sparkles) sit inside her black and red
    outside = cv2.dilate((1 - m).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    n, cc, st, _ = cv2.connectedComponentsWithStats(((lab == WHITE) & (m > 0)).astype(np.uint8), 8)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] > 150 and outside[cc == i].any():
            m[cc == i] = 0
    n, cc, st, _ = cv2.connectedComponentsWithStats(m)       # drop specks the band picked up from the background
    if n > 1:
        big = st[1:, cv2.CC_STAT_AREA].max()
        m = np.isin(cc, [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= max(80, big * 0.01)]).astype(np.uint8)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("a", type=int)
    ap.add_argument("b", type=int)
    ap.add_argument("out")
    ap.add_argument("--start", type=float, default=0.0, help="seconds into the video where frame 0 is")
    ap.add_argument("--width", type=int, help="scale frames to this width first (clicks are in these pixels)")
    ap.add_argument("--frame", type=int, help="prompt frame (absolute index)")
    ap.add_argument("--pos", nargs="*", default=[], help="extra positive clicks X,Y on the prompt frame (e.g. her face)")
    ap.add_argument("--neg", nargs="*", default=[], help="negative clicks X,Y on the prompt frame")
    ap.add_argument("--box", help="X0,Y0,X1,Y1 on the prompt frame instead of the automatic box")
    args = ap.parse_args()
    from transformers import Sam2VideoModel, Sam2VideoProcessor

    frames = list(video_frames(args.src, args.a, args.b, width=args.width, start=args.start))
    H, W = frames[0].shape[:2]
    if args.frame is not None:
        p = args.frame - args.a
    else:
        g = [(np.abs(f[::4, ::4].astype(np.int16) - [9, 236, 110]).sum(-1) < 90).sum() for f in frames]
        p = int(np.argmax(g))
    pts, box = prompts(frames[p], np.random.default_rng(0))
    if args.box:
        box = [int(v) for v in args.box.split(",")]
    pts = [[int(v) for v in s.split(",")] for s in args.pos] + pts
    neg = [[int(v) for v in s.split(",")] for s in args.neg]
    print(f"{len(frames)} frames {W}x{H}, prompt frame {args.a + p}: {len(pts)} clicks {pts}, box {box}, {len(neg)} negative", flush=True)

    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 2))
    model = Sam2VideoModel.from_pretrained(MODEL).eval()
    proc = Sam2VideoProcessor.from_pretrained(MODEL)
    sess = proc.init_video_session(video=[Image.fromarray(f) for f in frames], inference_device="cpu", dtype=torch.float32)
    proc.add_inputs_to_inference_session(inference_session=sess, frame_idx=p, obj_ids=1,
                                         input_points=[[pts + neg]], input_labels=[[[1] * len(pts) + [0] * len(neg)]],
                                         input_boxes=[[box]])
    masks = {}
    with torch.inference_mode():
        model(inference_session=sess, frame_idx=p)
        for rev in (False, True):
            for o in model.propagate_in_video_iterator(sess, start_frame_idx=p, reverse=rev):
                m = proc.post_process_masks([o.pred_masks], original_sizes=[[H, W]], binarize=False)[0]
                masks[o.frame_idx] = (m[0, 0].numpy() > 0).astype(np.uint8)
                print(f"\rsegmented {len(masks)}/{len(frames)}", end="", flush=True)
    print()

    cut = [refine(f, masks[i]) for i, f in enumerate(frames)]
    union = np.max(np.stack(cut), 0)
    ys, xs = np.nonzero(union)
    x0, y0, x1, y1 = max(0, xs.min() - 4), max(0, ys.min() - 4), min(W, xs.max() + 5), min(H, ys.max() + 5)
    os.makedirs(args.out, exist_ok=True)
    edge = dict(top=bool(union[0].any()), bottom=bool(union[-1].any()), left=bool(union[:, 0].any()), right=bool(union[:, -1].any()))
    ramp = np.ones((H, W), np.float32)
    r = np.clip(np.arange(max(H, W)) / FADE, 0, 1)
    if edge["top"]:
        ramp *= r[:H, None]
    if edge["bottom"]:
        ramp *= r[:H][::-1, None]
    if edge["left"]:
        ramp *= r[None, :W]
    if edge["right"]:
        ramp *= r[:W][None, ::-1]
    for i, (f, m) in enumerate(zip(frames, cut)):
        alpha = cv2.GaussianBlur(m.astype(np.float32), (0, 0), 0.6) * ramp * 255    # anti-aliased edge, faded cuts
        rgba = np.dstack([f, np.clip(alpha, 0, 255).astype(np.uint8)])[y0:y1, x0:x1]
        Image.fromarray(rgba, "RGBA").save(f"{args.out}/{i:03d}.png")
    json.dump(dict(src=os.path.basename(args.src), start=args.start, a=args.a, b=args.b, n=len(frames), crop=[int(x0), int(y0), int(x1), int(y1)],
                   prompt_frame=args.a + p, clicks=pts, box=box, neg=neg, touches_edge=edge), open(f"{args.out}/cut.json", "w"), indent=1)
    print(f"{args.out}: {len(frames)} RGBA frames {x1 - x0}x{y1 - y0}, touches frame edge: {[k for k, v in edge.items() if v]}")


if __name__ == "__main__":
    main()
