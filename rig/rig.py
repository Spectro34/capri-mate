"""Capri's rig in Blender: her original-pixel layers (parts.py) on bones, with actions written as keyframes.

  blender -b --python rig/rig.py -- [--actions neutral,idle,...] [--save rig/capri.blend]

Each layer is an unlit textured plane (exact colours: Standard view transform, emission only). The head, eyes,
grin and body hang rigidly on their bones; the hair is a fine grid bent by ten hair bones fanning out from her
head, weighted by direction and distance, so the spikes sway and stream while staying her shapes. Renders each
action to mate-src/rig_<action>/NNN.png (transparent, one PNG per drawing; cut.json lists each drawing's hold
at 24 fps) with a cut.json in the float shot's source coordinates, so build_mate.py places it exactly like the float
shot (`"like": "float"`).
"""
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
PARTS = f"{HERE}/parts"
OUT = f"{os.path.dirname(HERE)}/mate-src"
PX = 0.01                         # Blender units per pixel
HAIR_BONES = 10
FPS = 20

meta = json.load(open(f"{PARTS}/parts.json"))
W, H = meta["size"]


def to3(x, y, z=0.0):
    """Canvas pixel (x right, y down) to Blender (X right, Y up), camera looking down -Z."""
    return Vector(((x - W / 2) * PX, -(y - H / 2) * PX, z))


# ---- scene ---------------------------------------------------------------------------------------------
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"   # actions are sampled per frame already
    sc = bpy.context.scene
    try:
        sc.render.engine = "BLENDER_EEVEE"
    except TypeError:                                     # Blender 4.2-4.x calls it EEVEE Next
        sc.render.engine = "BLENDER_EEVEE_NEXT"
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.fps = FPS
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.image_settings.color_depth = "8"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0
    sc.view_settings.gamma = 1
    sc.eevee.taa_render_samples = 16
    sc.render.filter_size = 0.6
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(W, H) * PX
    cam.location = (0, 0, 10)
    sc.collection.objects.link(cam)
    sc.camera = cam
    return sc


def material(name, interp):
    img = bpy.data.images.load(f"{PARTS}/{name}.png")
    img.colorspace_settings.name = "sRGB"
    img.alpha_mode = "STRAIGHT"
    m = bpy.data.materials.new(name)
    m.surface_render_method = "BLENDED"
    nt = m.node_tree
    nt.nodes.clear()
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = interp
    tex.extension = "CLIP"
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    outn = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
    nt.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
    nt.links.new(clear.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emit.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], outn.inputs["Surface"])
    return m


def plane(name, z, interp, sub=0):
    """A full-canvas plane carrying one layer; `sub` > 0 makes it a grid that bones can bend."""
    n = max(1, sub)
    verts, faces, uvs = [], [], []
    for j in range(n + 1):
        for i in range(n + 1):
            verts.append(to3(W * i / n, H * j / n, z))
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            faces.append((a, a + n + 1, a + n + 2, a + 1))
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    uv = me.uv_layers.new(name="uv")
    for poly in me.polygons:
        for li in poly.loop_indices:
            v = verts[me.loops[li].vertex_index]
            uv.data[li].uv = (v.x / (W * PX) + 0.5, v.y / (H * PX) + 0.5)
    me.materials.append(material(name, interp))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# ---- PNG in and out with Blender's own loader (its bundled Python has numpy but no Pillow); byte-exact ------
def read_png(path):
    """RGBA uint8, top row first."""
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"            # the stored values, untouched
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return np.round(px.reshape(h, w, 4)[::-1] * 255).astype(np.uint8)


def write_png(path, a):
    import numpy as np
    h, w = a.shape[:2]
    img = bpy.data.images.new("out", w, h, alpha=True)
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "STRAIGHT"
    img.pixels.foreach_set((a[::-1].astype(np.float32) / 255).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


# ---- rig -----------------------------------------------------------------------------------------------
def hair_dirs():
    """Ten directions from her head to the far edge of her hair, spread over where the hair actually is."""
    import numpy as np
    a = read_png(f"{PARTS}/hair.png")[..., 3] > 0
    ys, xs = np.nonzero(a)
    hx, hy = meta["head_center"]
    ang = np.arctan2(ys - hy, xs - hx)
    dist = np.hypot(xs - hx, ys - hy)
    qs = np.quantile(ang, (np.arange(HAIR_BONES) + 0.5) / HAIR_BONES)
    dirs = []
    for q in qs:
        near = np.abs(np.angle(np.exp(1j * (ang - q)))) < 0.15
        dirs.append((float(q), float(np.quantile(dist[near], 0.9)) if near.any() else 150.0))
    return dirs


def build(interp):
    sc = bpy.context.scene
    arm = bpy.data.objects.new("rig", bpy.data.armatures.new("rig"))
    sc.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones
    nx, ny = meta["neck"]
    hx, hy = meta["head_center"]

    def bone(name, head, tail, parent=None):
        b = eb.new(name)
        b.head, b.tail = head, tail
        if parent:
            b.parent = eb[parent]
        return b

    bone("root", to3(nx, ny + 60), to3(nx, ny))
    bone("body", to3(nx, ny), to3(nx, ny + 120), "root")
    bone("head", to3(nx, ny), to3(hx, hy), "root")
    bone("hair", to3(hx, hy), to3(hx, hy - 40), "head")
    dirs = hair_dirs()
    for i, (a, r) in enumerate(dirs):
        bone(f"hair{i}", to3(hx, hy), to3(hx + math.cos(a) * r, hy + math.sin(a) * r), "hair")
    for e in meta["eyes"]:
        cx, cy = e["center"]
        bone(e["name"], to3(cx, cy), to3(cx, cy - 10), "head")
    bpy.ops.object.mode_set(mode="OBJECT")

    obs = {}
    for k, name in enumerate(meta["order"]):
        sub = 90 if name == "hair" else 0
        ob = plane(name, k * 0.01, interp, sub)
        obs[name] = ob
        if name == "hair":
            continue
        bone_name = {"under": "hair", "body": "body", "head": "head", "grin": "head"}.get(name, name)
        mw = ob.matrix_world.copy()
        ob.parent = arm
        ob.parent_type = "BONE"
        ob.parent_bone = bone_name
        bpy.context.view_layer.update()
        ob.matrix_world = mw

    # hair: weights by direction (gaussian over angle) and distance (inner hair stays with the head)
    hair = obs["hair"]
    groups = [hair.vertex_groups.new(name="hair")] + [hair.vertex_groups.new(name=f"hair{i}") for i in range(HAIR_BONES)]
    for v in hair.data.vertices:
        px = v.co.x / PX + W / 2
        py = -v.co.y / PX + H / 2
        dx, dy = px - hx, py - hy
        a = math.atan2(dy, dx)
        d = math.hypot(dx, dy)
        ws = []
        for i, (ba, r) in enumerate(dirs):
            da = math.atan2(math.sin(a - ba), math.cos(a - ba))
            ws.append(math.exp(-(da / 0.35) ** 2))
        reach = min(1.0, max(0.0, (d - 25) / 140))          # 0 at the head, 1 from 165 px out
        reach = reach * reach * (3 - 2 * reach)
        tot = sum(ws) or 1.0
        groups[0].add([v.index], 1 - reach, "REPLACE")
        for i, w in enumerate(ws):
            if w / tot * reach > 0.002:
                groups[i + 1].add([v.index], w / tot * reach, "REPLACE")
    mod = hair.modifiers.new("rig", "ARMATURE")
    mod.object = arm
    hair.parent = arm
    obs["grin"].hide_render = True
    return arm, obs, dirs


# ---- actions -------------------------------------------------------------------------------------------
# Her motion language, from the footage (motion_study.py on the Netflix shots): she is drawn on 2s and 3s (24 fps),
# on 1s only in fast bursts; poses snap, almost never ease; a move is a few held drawings, sometimes with one
# smeared glitch frame between them; her hair moves as rigid shards that jump to a new arrangement; long holds on
# one drawing are normal. So an action is a timeline of drawings, each held for a number of 24 fps frames.
FPS_DRAW = 24
SMEAR = "smear"


def shred(level):
    """A drawing of her torn apart by the glitch, 0 (whole) .. 1 (gone): for teleporting out and in."""
    return ("shred", level)


def pose(head=0.0, hx=0.0, hy=0.0, body=0.0, hair=0.0, flutter=None, droop=0.0, fan=0.0, eyes=(0.0, 0.0), grin=False, boil=0.0):
    """One drawing. Degrees and canvas px (+y down). hair: all hair bones bend this way; flutter: per-bone offsets;
    droop: the hair sinks (each side bends down); fan: the hair bursts out; boil: fresh small jitter of the shards."""
    return dict(head=head, hx=hx, hy=hy, body=body, hair=hair, flutter=flutter, droop=droop, fan=fan, eyes=eyes, grin=grin, boil=boil)


def key(arm, frame, bone, rot=None, loc=None, scale=None):
    pb = arm.pose.bones[bone]
    pb.rotation_mode = "XYZ"
    if rot is not None:
        pb.rotation_euler = (0, 0, math.radians(rot))
        pb.keyframe_insert("rotation_euler", frame=frame)
    if loc is not None:
        pb.location = loc
        pb.keyframe_insert("location", frame=frame)
    if scale is not None:
        pb.scale = (scale, scale, scale)
        pb.keyframe_insert("scale", frame=frame)


def draw(arm, obs, f, p, rng):
    """Key drawing `f` (bone-local axes: y along the bone, which points up; x across)."""
    key(arm, f, "root", rot=0, loc=(0, 0, 0), scale=1)
    key(arm, f, "head", rot=p["head"], loc=(p["hx"] * PX, -p["hy"] * PX, 0), scale=1)
    key(arm, f, "body", rot=p["body"], loc=(0, 0, 0), scale=1)
    key(arm, f, "hair", rot=0, loc=(0, 0, 0), scale=1)
    for i, (a, _) in enumerate(DIRS):
        r = p["hair"] + (p["flutter"][i] if p["flutter"] else 0.0)
        r += (8 if math.cos(a) > 0 else -8) * p["droop"]
        r += 6 * p["fan"] * math.sin(i * 2.3)
        r += rng.uniform(-1, 1) * p["boil"]
        key(arm, f, f"hair{i}", rot=r, loc=(0, 0, 0), scale=1 + 0.16 * p["fan"] - 0.05 * p["droop"])
    for e in meta["eyes"]:
        key(arm, f, e["name"], rot=0, loc=(p["eyes"][0] * PX, -p["eyes"][1] * PX, 0), scale=1)
    obs["grin"].hide_render = not p["grin"]
    obs["grin"].keyframe_insert("hide_render", frame=f)


B = 0.7          # boil: the shards' small jump between drawings (median hair change in the footage: 1.4%)


def act_neutral():
    return [(pose(), 1)], "once"


def act_idle():
    """Long holds, a shimmer on threes, eyes that snap to a side and back (loops)."""
    return [(pose(), 30), (pose(boil=B), 3), (pose(boil=B), 3), (pose(boil=B), 3),
            (pose(eyes=(2.5, 0)), 26), (pose(eyes=(2.5, 0), boil=B), 3), (pose(eyes=(-2, 1)), 20),
            (pose(boil=B), 3), (pose(), 24)], "loop"


def act_look(side):
    """Anticipation, a smear, the new pose overshot, settled, held."""
    to = dict(body=1.5 * side, eyes=(3.0 * side, 0.5))
    return [(pose(), 2), (pose(head=1.5 * side, eyes=(-0.5 * side, 0)), 2), (SMEAR, 1),
            (pose(head=-5.5 * side, hair=4 * side, **to), 2), (pose(head=-4 * side, hair=3 * side, **to), 3),
            (pose(head=-4 * side, hair=3 * side, boil=B, **to), 30)], "once"


def act_grin():
    """The grin pops open with the head up a beat, holds with the hair shimmering on threes, snaps shut."""
    return [(pose(), 3), (pose(grin=True, hy=-2.5), 2), (pose(grin=True, hy=-1, boil=B), 3),
            (pose(grin=True, boil=B), 3), (pose(grin=True, boil=B), 3), (pose(grin=True), 18), (pose(), 3)], "once"


def act_whip():
    """A fast head turn on ones, smeared; the hair lags, overshoots, and the pose holds before snapping back."""
    return [(pose(), 2), (pose(head=3), 1), (pose(head=-6, hair=4), 1), (SMEAR, 1),
            (pose(head=-14, hair=10, body=5), 1), (pose(head=-15, hair=-4, body=6), 2),
            (pose(head=-12, hair=-12, body=5), 2), (pose(head=-13, hair=-6, body=5), 3),
            (pose(head=-13, hair=-7, body=5, boil=B), 24), (SMEAR, 1), (pose(), 3)], "once"


def act_flare():
    """Startled: the hair snaps out, steps back down on threes."""
    return [(pose(), 1), (pose(fan=1, hy=-4, eyes=(0, -1.5)), 3), (pose(fan=0.7, hy=-3, eyes=(0, -1)), 3),
            (pose(fan=0.4, hy=-1, boil=B), 3), (pose(fan=0.25), 18), (pose(), 3)], "once"


def act_trail(side):
    """Floating along: the hair streams back (side = the way she's going) in four rigid arrangements on twos (loops)."""
    return [(pose(head=-3 * side, body=5 * side, eyes=(2.5 * side, 0), hair=-9 * side,
                  flutter=[2.5 * math.sin(k * math.pi / 2 + i * 1.3) for i in range(HAIR_BONES)]), 2) for k in range(4)], "loop"


def act_droop():
    """Bored: sinks in three held steps, holds, perks back up through a smear."""
    return [(pose(), 3), (pose(droop=0.35, hy=1.5, eyes=(0, 0.7)), 3), (pose(droop=0.7, hy=3.5, eyes=(0, 1.4)), 3),
            (pose(droop=1, hy=5, eyes=(0, 2)), 30), (pose(droop=1, hy=5, eyes=(0, 2), boil=B), 3),
            (SMEAR, 1), (pose(fan=0.2, hy=-2), 2), (pose(), 3)], "once"


def act_laugh():
    """Grin open, the head bounces on twos."""
    up, down = dict(head=3, hy=-2.5, grin=True, boil=B), dict(head=-2, hy=0, grin=True, boil=B)
    return [(pose(), 2)] + [(pose(**(up if k % 2 == 0 else down)), 2) for k in range(8)] + [(pose(grin=True), 4), (pose(), 3)], "once"


def act_vanish():
    """Teleporting out: she tears into sideways slices on ones and is gone."""
    return [(pose(), 1)] + [(shred(k), 1) for k in (0.3, 0.55, 0.8, 1.0)], "once"


def act_appear():
    """Teleporting in: the slices snap together into her, then a held beat."""
    return [(shred(k), 1) for k in (0.9, 0.65, 0.35)] + [(SMEAR, 1), (pose(), 6)], "once"


def act_talk():
    """Mouth shapes for speech; the mate picks the drawing from her voice's loudness: closed, open, and the same two
    with the head tipped (she switches every few words)."""
    tip = dict(head=-2.5, hy=-1)
    return [(pose(), 1), (pose(grin=True), 1), (pose(**tip), 1), (pose(grin=True, **tip), 1)], "loop"


ACTIONS = {
    "neutral": act_neutral, "idle": act_idle, "look_l": lambda: act_look(-1), "look_r": lambda: act_look(1),
    "grin": act_grin, "whip": act_whip, "flare": act_flare, "trail_l": lambda: act_trail(-1),
    "trail_r": lambda: act_trail(1), "droop": act_droop, "laugh": act_laugh, "vanish": act_vanish, "appear": act_appear,
    "talk": act_talk,
}
DIRS = []


def smear(prev, nxt, rng):
    """A glitch frame between two drawings: horizontal slices of both, pushed sideways, red and green split apart.
    Only their pixels, moved."""
    import numpy as np
    out = np.zeros_like(nxt)
    y = 0
    while y < H:
        h = rng.randrange(4, 26)
        src = nxt if rng.random() < 0.6 else prev
        dx = rng.choice((-1, 1)) * rng.randrange(8, 60) if rng.random() < 0.7 else 0
        out[y:y + h] = np.roll(src[y:y + h], dx, 1)
        y += h
    k = rng.randrange(3, 7)
    out[..., 0] = np.roll(out[..., 0], k, 1)
    out[..., 1] = np.roll(out[..., 1], -k, 1)
    return out


def tear(img, level, rng):
    """Shred `img` (her neutral drawing): more and wider slices pushed sideways, more of them dropped, the red and
    green split further apart as `level` goes 0 -> 1. Only her pixels, moved or removed."""
    import numpy as np
    out = np.zeros_like(img)
    y = 0
    while y < H:
        h = rng.randrange(3, 22)
        if rng.random() >= level ** 1.5:                      # this slice survives
            dx = round(rng.choice((-1, 1)) * rng.uniform(0.2, 1) * 140 * level)
            out[y:y + h] = np.roll(img[y:y + h], dx, 1)
        y += h
    k = round(3 + 14 * level)
    out[..., 0] = np.roll(out[..., 0], k, 1)
    out[..., 1] = np.roll(out[..., 1], -k, 1)
    return out


def render(name, interp):
    import random

    reset()
    arm, obs, dirs = build(interp)
    DIRS[:] = dirs
    steps, loop = ACTIONS[name]()
    rng = random.Random(name)
    hold = [h for _, h in steps]
    for f, (p, _) in enumerate(steps):
        draw(arm, obs, f, p if isinstance(p, dict) else pose(), rng)   # smear and shred slots: replaced below
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 0, len(steps) - 1
    out = f"{OUT}/rig_{name}"
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        os.remove(f"{out}/{f}")
    sc.render.filepath = f"{out}/###"
    bpy.ops.render.render(animation=True)
    whole = None
    for f, (p, _) in enumerate(steps):
        if p == SMEAR:
            a, b = (read_png(f"{out}/{g:03d}.png") for g in (f - 1, f + 1))
            write_png(f"{out}/{f:03d}.png", smear(a, b, rng))
        elif isinstance(p, tuple):
            whole = read_png(f"{out}/{f:03d}.png") if whole is None else whole   # rendered as pose()
            write_png(f"{out}/{f:03d}.png", tear(whole, p[1], rng))
    x0 = meta["crop"][0] - meta["pad"]
    y0 = meta["crop"][1] - meta["pad"]
    json.dump(dict(src="rig", action=name, n=len(steps), loop=loop, fps=FPS_DRAW, hold=hold, crop=[x0, y0, x0 + W, y0 + H]),
              open(f"{out}/cut.json", "w"))
    print(f"rendered {name}: {len(steps)} drawings, {sum(hold)} frames at {FPS_DRAW} fps ({loop})", flush=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    names = list(ACTIONS)
    save = None
    interp = "Linear"
    for i, v in enumerate(argv):
        if v == "--actions":
            names = argv[i + 1].split(",")
        if v == "--save":
            save = argv[i + 1]
        if v == "--exact":
            interp = "Closest"                            # 1:1 texels, for the fidelity check of `neutral`
    for n in names:
        render(n, interp)
    if save:
        bpy.ops.wm.save_as_mainfile(filepath=save)


main()
