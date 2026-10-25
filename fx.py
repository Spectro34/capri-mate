"""Capri's effects, drawn with cairo over her sprite (shared by mate.py and tools/make_demo.py)."""
import math

import cairo

GREEN = (9 / 255, 236 / 255, 110 / 255)       # her green, for the energy streaks
INK = (10 / 255, 6 / 255, 20 / 255)           # her cel black


def glint(cr, x, y, r, step, alpha=1.0):
    """A four-point glint on one of her hair's sparkles (radius r): small, big, small, one size per 2s step."""
    k = (0.55, 1.0, 0.6)[min(step, 2)]
    L = (2.5 + r * 1.6) * k
    w = L * 0.22
    cr.set_source_rgba(1, 0.55, 0.7, 0.35 * alpha)
    cr.arc(x, y, L * 0.45, 0, 2 * math.pi)
    cr.fill()
    cr.move_to(x, y - L)
    for px, py in ((w, -w), (L, 0), (w, w), (0, L), (-w, w), (-L, 0), (-w, -w), (0, -L)):
        cr.line_to(x + px, y + py)
    cr.close_path()
    cr.set_source_rgba(1, 0.96, 0.98, 0.95 * alpha)
    cr.fill()


def streaks(cr, rng, size, alpha=1.0):
    """Her green energy: jagged streaks flashing out around her face (the origin), edged in her black.
    `size` is her sprite's size; `rng` (random.Random) picks one set of streaks."""
    reach = max(size) * 0.5
    for _ in range(rng.randint(2, 4)):
        a = rng.uniform(0, 2 * math.pi)
        r = reach * rng.uniform(0.35, 0.7)
        x, y = math.cos(a) * r, math.sin(a) * r * 0.8 - size[1] * 0.12
        pts = [(x, y)]
        for _ in range(rng.randint(3, 5)):
            a += rng.uniform(-0.9, 0.9)
            step = reach * rng.uniform(0.08, 0.18)
            x, y = x + math.cos(a) * step, y + math.sin(a) * step
            pts.append((x, y))
        for color, width in ((INK, 4.5), (GREEN, 2.2)):
            cr.set_source_rgba(*color, alpha)
            cr.set_line_width(width)
            cr.set_line_join(cairo.LINE_JOIN_MITER)
            cr.move_to(*pts[0])
            for p in pts[1:]:
                cr.line_to(*p)
            cr.stroke()
