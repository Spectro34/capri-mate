#!/usr/bin/env python3
"""Capri, desktop mate: she floats around the screen, small and background-less, out of the way of your work.

  python3 mate.py [--monitors all|DP-2,...] [--layer top|overlay|bottom] [--opacity 1]
  python3 mate.py ctl hide|show|toggle|quiet [MIN]|hop|blink|pose|visit|turn|pet|poke|look|bored|status
  python3 mate.py ctl talk [WORDS...]       open her speech bubble, or say WORDS to her

A GTK 4 layer-shell surface (gtk4-layer-shell, preloaded by re-exec) on the `top` layer: above windows, under
fullscreen ones. It takes no keyboard focus. Only a small spot on her face takes the mouse; everything else clicks
through her. Sprites come from mate/ (build_mate.py): original frames, background removed.

She never floats about: like in the show she holds still in one place, and goes anywhere by zapping there.
Behaviour, after the open-source desktop pets (Shimeji-ee, wl_shimeji, WindowPet, VPet), minus gravity and travel:
  drift    every 10-30 s zaps to a free spot, mostly in the outer quarters and off the window you're using
  rest     stays put for a while, in her idle
  pose     glitch-cuts to another shot of herself for a few seconds
  hop      tears apart into glitch slices, is gone a beat, glitches back in somewhere else (sometimes the other
           monitor); blink: the same over a short distance
  turn     a sharp head turn, the hair swinging after
  perch    zaps onto an edge of the window you're using
  visit    when the cursor has been still for a while, she zaps over beside it
  shy      when the cursor comes near she fades to a ghost and zaps off; point at her face and she stays
  hidden   while the window you're using is fullscreen, or `quiet`
She glances toward the cursor when it moves nearby and rests in her idle (long holds, eyes snapping aside). Rig
actions are drawn the way she moves in the show: held drawings on 2s and 3s at 24 fps, snapped poses, glitch smears
between them. Her hair's sparkles twinkle; green energy streaks flash when she acts.
Mouse, on her face: hover = she perks up (and grins if you stay), click = her hair flares, or she blinks off,
drag = carry her (also to the other monitor; she stays where you drop her), fling her = she zaps off that way,
double-click = she hops, right-click = quiet 30 min, middle-click = talk to her. More active when you're away
(cursor still for 3 min): bored.
Talking (optional, talk.py): with an OpenAI key in ~/.config/capri-mate/openai-key, a speech bubble opens beside her;
type, or toggle the mic and speak. She answers out loud, her mouth moving with her voice, and the bubble shows her
words. Without a key the bubble says how to turn it on.
"""
import argparse
import glob
import json
import math
import os
import random
import signal
import socket
import sys
import time

RUN = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
CTL = f"{RUN}/capri-mate.sock"

if len(sys.argv) > 1 and sys.argv[1] == "ctl":           # control client: no GTK needed
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(CTL)
            s.sendall(" ".join(sys.argv[2:] or ["status"]).encode())
            print(s.recv(4096).decode())
    except OSError as e:
        sys.exit(f"capri-mate is not running ({e})")
    sys.exit(0)

LAYER_LIB = next(iter(sorted(glob.glob("/usr/lib*/libgtk4-layer-shell.so*") +                  # Fedora, openSUSE, Arch
                             glob.glob("/usr/lib/*-linux-gnu/libgtk4-layer-shell.so*"))), None)  # Debian, Ubuntu
if LAYER_LIB and LAYER_LIB not in os.environ.get("LD_PRELOAD", ""):
    # gtk4-layer-shell must be loaded before libwayland-client
    os.environ["LD_PRELOAD"] = f"{LAYER_LIB} {os.environ.get('LD_PRELOAD', '')}".strip()
    os.execv(sys.executable, sys.orig_argv)

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import GLib, GLibUnix, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fx as fx_  # noqa: E402
import talk  # noqa: E402
TICK_MS = 16              # ~60 Hz movement; animation frames keep their own rate
EDGE = 12                 # px she keeps from the screen edges
TILT = 0.16               # rad, the most she leans into a drift or a throw
FACE_SPOT = (70, 56)      # px, the only part of her that takes the mouse
AWAY = 180                # s without cursor movement: you're away, she gets livelier
VISIT_AFTER = 45          # s of a still cursor before she may come over
ZAP_ROLES = ("poke", "turn", "arrive", "pet")  # one-shots that flash green energy streaks around her


class Hypr:
    """Hyprland IPC (the request socket): cursor position and the active window."""

    def __init__(self):
        self.path = None

    def _paths(self):
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        # the session's own socket, then the newest one (the signature changes when Hyprland restarts)
        return ([self.path] if self.path else []) + ([f"{RUN}/hypr/{sig}/.socket.sock"] if sig else []) + \
            sorted(glob.glob(f"{RUN}/hypr/*/.socket.sock"), key=os.path.getmtime)[-1:]

    def ask(self, what):
        for p in self._paths():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.05)
                    s.connect(p)
                    s.sendall(f"j/{what}".encode())
                    buf = b""
                    while chunk := s.recv(65536):
                        buf += chunk
                self.path = p
                return json.loads(buf)
            except (OSError, ValueError):
                continue
        return None

    def cursor(self):
        c = self.ask("cursorpos")
        return (c["x"], c["y"]) if c and "x" in c else None

    def active(self):
        """Active window: (x, y, w, h, fullscreen) in layout pixels, or None."""
        w = self.ask("activewindow")
        if not w or "at" not in w:
            return None
        return (*w["at"], *w["size"], bool(w.get("fullscreen")))


class Sprites:
    def __init__(self, path):
        meta = json.load(open(f"{path}/mate.json"))
        self.anims = meta["anims"]
        self.frames = {n: [cairo.ImageSurface.create_from_png(f"{path}/{n}/{i:03d}.png") for i in range(a["n"])]
                       for n, a in self.anims.items()}
        for a in self.anims.values():                       # frame -> drawing (rig actions hold each drawing a while)
            a["seq"] = [i for i, h in enumerate(a.get("hold", [1] * a["n"])) for _ in range(h)]
        self.by_role = {}
        for n, a in self.anims.items():
            self.by_role.setdefault(a["role"], []).append(n)
        # one canvas that fits every frame, leaning either way, with her face at the same point (L, U)
        L = U = R = D = 0.0
        for a in self.anims.values():
            w, h = a["size"]
            for fx, fy in a["face"]:
                for t in (-TILT, 0.0, TILT):
                    c, s = math.cos(t), math.sin(t)
                    for x, y in ((-fx, -fy), (w - fx, -fy), (-fx, h - fy), (w - fx, h - fy)):
                        rx, ry = x * c - y * s, x * s + y * c
                        L, U, R, D = max(L, -rx), max(U, -ry), max(R, rx), max(D, ry)
        pad = 4
        self.L, self.U = math.ceil(L) + pad, math.ceil(U) + pad
        self.W, self.H = self.L + math.ceil(R) + pad, self.U + math.ceil(D) + pad


class Bubble:
    """Her speech bubble: a second layer-shell surface beside her with her words, what you said, a line to type in
    and a mic toggle. It takes the keyboard only when you open it (middle-click) or click into it, never when it pops
    up by itself with her answer; Escape closes it."""
    W = 340

    def __init__(self, capri):
        self.c = capri
        self.win = win = Gtk.Window(application=capri.win.get_application())
        win.set_decorated(False)
        LS.init_for_window(win)
        LS.set_namespace(win, "capri-talk")
        LS.set_layer(win, LS.Layer.TOP)
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)
        LS.set_exclusive_zone(win, -1)
        LS.set_anchor(win, LS.Edge.TOP, True)
        LS.set_anchor(win, LS.Edge.LEFT, True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=self.W)
        box.add_css_class("capri-bubble")
        self.you = Gtk.Label(wrap=True, xalign=0, max_width_chars=40, visible=False)
        self.you.add_css_class("you")
        self.her = Gtk.Label(wrap=True, xalign=0, max_width_chars=40, selectable=False)
        self.her.add_css_class("her")
        row = Gtk.Box(spacing=6)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="say something…")
        self.entry.connect("activate", self._send)
        self.mic = Gtk.ToggleButton(label="mic", tooltip_text="speak (click again to send)")
        self.mic.connect("toggled", self._mic)
        close = Gtk.Button(label="×", tooltip_text="close (Esc)")
        close.connect("clicked", lambda *_: self.hide())
        for w in (self.entry, self.mic, close):
            row.append(w)
        for w in (self.you, self.her, row):
            box.append(w)
        win.set_child(box)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, *_: kv == 0xff1b and (self.hide() or True))   # Escape
        win.add_controller(keys)
        touch = Gtk.GestureClick()
        touch.connect("pressed", lambda *_: self._keyboard(True))
        box.add_controller(touch)
        self.visible = False
        self.hide_at = None
        if not capri.talk:
            self.her.set_text(f"(I can't talk yet. Put an OpenAI API key in {capri.talk_cfg['api_key_file']} "
                              "and restart me: systemctl --user restart capri-mate)")
            self.entry.set_sensitive(False)
            self.mic.set_sensitive(False)
        else:
            self.her.set_text("hm? ✦")

    def show(self, focus=False):
        LS.set_monitor(self.win, self.c.mon)
        self.place()
        self._keyboard(focus)
        self.win.present()
        self.visible, self.hide_at = True, None

    def _keyboard(self, on):
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.ON_DEMAND if on else LS.KeyboardMode.NONE)
        if on:
            self.entry.grab_focus()

    def hide(self):
        if self.mic.get_active():
            self.mic.set_active(False)
        self.win.set_visible(False)
        self.visible = False

    def place(self):
        g = self.c.mon.get_geometry()
        x, y = self.c.pos
        a = self.c.sp.anims[self.c.hover]
        right, left = a["size"][0] - a["face"][0][0], a["face"][0][0]     # her reach either side of her face
        bx = x + right + 6 if x + right + 6 + self.W < g.width - 8 else x - left - 6 - self.W
        by = min(max(8, y - 170), g.height - 200)
        LS.set_margin(self.win, LS.Edge.LEFT, round(max(8, bx)))
        LS.set_margin(self.win, LS.Edge.TOP, round(by))

    def _send(self, *_):
        text = self.entry.get_text().strip()
        if text and self.c.talk:
            self.entry.set_text("")
            self.you.set_text(f"you: {text}")
            self.you.set_visible(True)
            self.c.talk.say(text)

    def _mic(self, b):
        if not self.c.talk:
            return
        if b.get_active():
            b.set_label("● send")
            self.her.set_text("(listening…)")
            self.c.talk.mic_start()
        else:
            b.set_label("mic")
            self.c.talk.mic_stop()

    def on(self, kind, text):
        """Events from the talk session (main thread)."""
        if kind == "thinking":
            self.her.set_text("…")
            self.hide_at = None
        elif kind == "heard":
            self.you.set_text(f"you: {text}")
            self.you.set_visible(True)
        elif kind in ("text", "said"):
            self.her.set_text(text)
        elif kind == "done":
            self.hide_at = time.monotonic() + 30
        elif kind == "error":
            self.her.set_text(f"(static… {text})")
            self.hide_at = time.monotonic() + 30
        if kind in ("thinking", "heard", "text") and not self.visible:
            self.show()


class Capri:
    def __init__(self, app, args):
        self.args = args
        self.rng = random.Random()
        self.hypr = Hypr()
        self.sp = Sprites(args.sprites)
        self.hover = self.sp.by_role["hover"][0]
        self.talk_cfg, key = talk.load()
        self.talk = talk.Talk(self.talk_cfg, key, lambda k, t: GLib.idle_add(self._on_talk, k, t)) if key else None
        self.bubble = None
        self.talk_draw, self.next_mouth, self.tip = 0, 0.0, 0
        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_decorated(False)
        self.win.set_default_size(self.sp.W, self.sp.H)
        LS.init_for_window(self.win)
        LS.set_namespace(self.win, "capri-mate")
        LS.set_layer(self.win, {"top": LS.Layer.TOP, "overlay": LS.Layer.OVERLAY, "bottom": LS.Layer.BOTTOM}[args.layer])
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)
        LS.set_exclusive_zone(self.win, -1)                  # margins count from the monitor's real edge
        LS.set_anchor(self.win, LS.Edge.TOP, True)
        LS.set_anchor(self.win, LS.Edge.LEFT, True)
        self.area = Gtk.DrawingArea(content_width=self.sp.W, content_height=self.sp.H)
        self.area.set_draw_func(self.draw)
        self.win.set_child(self.area)
        css = Gtk.CssProvider()
        css.load_from_string("""
            window, window.background { background: transparent; box-shadow: none; }
            .capri-bubble { background: rgba(10, 6, 20, 0.93); border: 2px solid #fa0245; border-left: 5px solid #09ec6e;
                            border-radius: 12px; padding: 10px 12px; font-family: monospace; font-size: 13px; color: #ececf4; }
            .capri-bubble .her { color: #ffd9e4; font-size: 14px; }
            .capri-bubble .you { color: #8a8aa3; }
            .capri-bubble entry { background: #14141c; color: #ececf4; border: 1px solid #3a2a4a; border-radius: 6px; }
            .capri-bubble button { background: #14141c; color: #09ec6e; border: 1px solid #3a2a4a; border-radius: 6px;
                                   min-width: 28px; padding: 2px 8px; }
            .capri-bubble button:checked { background: #fa0245; color: #0a0614; }
        """)
        Gtk.StyleContext.add_provider_for_display(self.win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.touchable = True
        self.win.connect("map", lambda *_: self._input(self.touchable, force=True))
        self._mouse()

        mons = list(self.win.get_display().get_monitors())
        want = None if args.monitors == "all" else args.monitors.split(",")
        self.monitors = [m for m in mons if want is None or m.get_connector() in want] or mons[:1]
        focused = next((m["name"] for m in self.hypr.ask("monitors") or [] if m.get("focused")), None)
        self.mon = next((m for m in self.monitors if m.get_connector() == focused), self.monitors[0])
        LS.set_monitor(self.win, self.mon)

        now = time.monotonic()
        self.t0 = self.last = now
        self.mode = "float"                                 # float (in place) | drag (carried)
        self.act = "drift"
        self.cur = None                                     # cursor, monitor-local
        self.cur_g = None                                   # cursor, layout
        self.last_move = now
        self.next_poll = self.next_win = 0.0
        self.active_win = None
        self.pos = list(self._spot())
        self.placed = list(self.pos)
        self.vel = [0.0, 0.0]
        self.tilt = 0.0
        self.target = None
        self.wait_until = now + 2
        self.anim, self.fi, self.fdir = self.hover, 0.0, 1
        self.pose_until = None
        self.act_until = 0.0
        self.glitch = 0
        self.after_glitch = None
        self.after_anim = None
        self.gone = False                                   # between teleporting out and in
        self.petted = False
        self.next_look = now + 5
        self.next_step, self.shown_tilt = 0.0, 0.0
        self.glints, self.next_glint = [], now + 1.0        # her hair's sparkles twinkling
        self.zap = None                                     # (start, seed): green energy streaks flashing
        self.alpha, self.alpha_to = 0.0, args.opacity        # fades in at start
        self.shy = False
        self.face_hover = False
        self.hover_since = 0.0
        self.trust_until = 0.0                              # pointed at her face: not shy for a while
        self.quiet_until = 0.0
        self.hidden = False
        self.dragged = False
        self.grab = (0.0, 0.0)
        self.next_act = now + self.rng.uniform(*args.act_every) * 0.6
        self._serve()
        self._place()
        self.win.present()
        GLib.timeout_add(TICK_MS, self.tick)

    # ---- input -----------------------------------------------------------------------------------------
    def _input(self, on, force=False):
        """Mouse only on her face spot (on), or nowhere (off: hidden, so she never catches an invisible click)."""
        if on == self.touchable and not force:
            return
        self.touchable = on
        surf = self.win.get_surface()
        if not surf:
            return
        if on:
            w, h = FACE_SPOT
            surf.set_input_region(cairo.Region(cairo.RectangleInt(self.sp.L - w // 2, self.sp.U - h // 2, w, h)))
        else:
            surf.set_input_region(cairo.Region())

    def _mouse(self):
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_enter)
        motion.connect("leave", lambda *_: setattr(self, "face_hover", False))
        self.area.add_controller(motion)
        click = Gtk.GestureClick(button=0)
        click.connect("released", self._on_click)
        self.area.add_controller(click)
        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

    def _on_enter(self, *_):
        now = time.monotonic()
        self.face_hover = True
        self.hover_since = now
        self.trust_until = now + 6
        self.petted = False
        self.target = None
        self.wait_until = now + 3

    def _on_click(self, gesture, n, x, y):
        if self.dragged:
            return
        b = gesture.get_current_button()
        if b == 3:
            self._quiet(30 * 60)
        elif b == 2:
            self._chat()
        elif b == 1 and n == 2:
            self._log("double-click: hop")
            self._teleport()
        elif b == 1 and n == 1:
            self._flinch()

    def _on_drag_begin(self, *_):
        self.dragged = False

    def _on_drag_update(self, g, dx, dy):
        if not self.dragged and math.hypot(dx, dy) > 6:
            self.dragged = True
            self.mode = "drag"
            if self.cur:
                self.grab = (self.pos[0] - self.cur[0], self.pos[1] - self.cur[1])
            self.vel = [0.0, 0.0]
            self._log("picked up")
        if self.dragged and not self.cur_g:
            # no compositor cursor (not Hyprland): the drag offset is how far the pointer is from where it grabbed
            # her, in the window as last placed, so put her that much further
            self.pos = list(self._clamp(self.placed[0] + dx, self.placed[1] + dy))

    def _on_drag_end(self, *_):
        if self.mode == "drag":
            sp = math.hypot(*self.vel)
            vel = tuple(self.vel)
            self.mode, self.vel = "float", [0.0, 0.0]
            self._log(f"let go ({sp:.0f} px/s)")
            self.target = None
            self.wait_until = time.monotonic() + self.rng.uniform(8, 20)
            if sp > 700:                                    # flung: she zaps off that way
                self._teleport(near=vel)
        GLib.timeout_add(50, lambda: setattr(self, "dragged", False))

    def _flinch(self):
        """Poked: her hair flares where she is, or she blinks off, away from the cursor."""
        self._log("poked")
        dx, dy = (self.pos[0] - self.cur[0], self.pos[1] - self.cur[1]) if self.cur else (1, 0)
        if self.rng.random() < 0.4:
            self._teleport(near=(dx, dy) if math.hypot(dx, dy) > 2 else True)
        elif not self._play("poke"):
            self.glitch = 2

    # ---- control socket (mate.py ctl ...) --------------------------------------------------------------
    def _serve(self):
        try:
            os.unlink(CTL)
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(CTL)
        os.chmod(CTL, 0o600)
        self.srv.listen(4)
        self.srv.setblocking(False)
        GLib.io_add_watch(self.srv.fileno(), GLib.IO_IN, self._on_ctl)

    def _on_ctl(self, *_):
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return True
        with conn:
            conn.settimeout(1)
            try:
                words = conn.recv(256).decode().split()
                conn.sendall(self._command(words).encode())
            except OSError:
                pass
        return True

    def _command(self, words):
        cmd, arg = (words + ["status"])[0], words[1:]
        now = time.monotonic()
        if cmd == "hide":
            self._quiet(10 ** 9)
        elif cmd == "show":
            self.quiet_until = 0.0
        elif cmd == "toggle":
            if self.quiet_until > now:
                self.quiet_until = 0.0
            else:
                self._quiet(10 ** 9)
        elif cmd == "quiet":
            self._quiet(float(arg[0]) * 60 if arg else 30 * 60)
        elif cmd == "hop":
            self._teleport()
        elif cmd == "blink":
            self._teleport(near=True)
        elif cmd in ("turn", "pet", "poke", "look", "bored"):
            self._play(cmd)
        elif cmd == "talk":
            self._chat(" ".join(arg) or None)              # with WORDS (a script) it never takes the keyboard
        elif cmd == "pose":
            self._pose(now)
        elif cmd == "visit":
            self._visit(now)
        elif cmd != "status":
            return "commands: hide show toggle quiet [MIN] hop blink pose visit turn pet poke look bored talk [WORDS] status"
        q = f", quiet for {(self.quiet_until - now) / 60:.0f} min" if self.quiet_until > now else ""
        return (f"{self.act} ({self.mode}) on {self.mon.get_connector()} at {self.pos[0]:.0f},{self.pos[1]:.0f}"
                f"{', hidden' if self.hidden else ''}{q}")

    def _chat(self, text=None, focus=True):
        """Open (or close) her speech bubble, ready to type into; with `text`, say it to her."""
        if self.bubble is None:
            self.bubble = Bubble(self)
        if text:
            self.bubble.show(focus=False)
            self.bubble.you.set_text(f"you: {text}")
            self.bubble.you.set_visible(True)
            if self.talk:
                self.talk.say(text)
        elif self.bubble.visible:
            self.bubble.hide()
        else:
            self._log("talk")
            self.bubble.show(focus=focus)

    def _on_talk(self, kind, text):
        if self.bubble is None:
            self.bubble = Bubble(self)
        self.bubble.on(kind, text)
        if kind in ("said", "error"):
            self._log(f"talk {kind}: {text[:80]}")
        return False

    def _chatting_now(self):
        return bool(self.talk and (self.talk.speaking() or self.talk.busy))

    def _chatting(self):
        return bool(self.bubble and self.bubble.visible) or bool(self.talk and self.talk.speaking())

    def _speak(self, now):
        """While her voice plays: the talk drawings, mouth open on the loud bits (on 2s), head tipped now and then."""
        m = self.talk.mouth(now) if self.talk else None
        names = self.sp.by_role.get("talk", [])
        if not names:
            return
        if m is None:
            if self.anim in names:
                self._set_anim(self.hover)
            return
        if self.anim not in names:
            self._set_anim(names[0])
            self._zap()
        if now >= self.next_mouth:
            self.next_mouth = now + 1 / 12
            if self.rng.random() < 0.06:
                self.tip ^= 2
            self.talk_draw = (1 if m > 0.045 else 0) + self.tip

    def _quiet(self, secs):
        self.quiet_until = time.monotonic() + secs
        self._log(f"quiet for {secs / 60:.0f} min" if secs < 10 ** 8 else "hidden")

    # ---- geometry ----------------------------------------------------------------------------------------
    def _bounds(self):
        g = self.mon.get_geometry()
        m = EDGE
        sp = self.sp
        return (sp.L + m, sp.U + m, g.width - (sp.W - sp.L) - m, g.height - (sp.H - sp.U) - m), g

    def _local_win(self):
        """The active window in this monitor's coordinates, if it is on this monitor."""
        if not self.active_win:
            return None
        x, y, w, h, fs = self.active_win
        g = self.mon.get_geometry()
        x, y = x - g.x, y - g.y
        if x + w <= 0 or y + h <= 0 or x >= g.width or y >= g.height:
            return None
        return x, y, w, h, fs

    def _her_box(self, x, y):
        f = self.sp.anims[self.hover]["size"]
        return x - f[0] * 0.5, y - f[1] * 0.8, x + f[0] * 0.5, y + f[1] * 0.2

    def _free(self, x, y, win):
        if not win:
            return True
        wx, wy, ww, wh, _ = win
        x0, y0, x1, y1 = self._her_box(x, y)
        return x1 < wx - 10 or x0 > wx + ww + 10 or y1 < wy - 10 or y0 > wy + wh + 10

    def _spot(self, anywhere=False):
        """A place to go: off the window you're using if there's room, else mostly the outer quarters."""
        (x0, y0, x1, y1), g = self._bounds()
        win = self._local_win() if hasattr(self, "active_win") else None
        best = None
        for _ in range(24):
            if anywhere or self.rng.random() < 0.2:
                x = self.rng.uniform(x0, x1)
            else:
                q = (x1 - x0) * 0.25
                x = self.rng.uniform(x0, x0 + q) if self.rng.random() < 0.5 else self.rng.uniform(x1 - q, x1)
            y = self.rng.uniform(y0, y1)
            best = best or (x, y)
            if self._free(x, y, win):
                return x, y
        return best

    def _clamp(self, x, y):
        (x0, y0, x1, y1), _ = self._bounds()
        return min(x1, max(x0, x)), min(y1, max(y0, y))

    def _place(self):
        """Move the window (only ever a jump: a zap, or being carried, which follows the cursor on 1s)."""
        now = time.monotonic()
        if self.mode != "drag" and now < self.next_step:
            return
        self.next_step = now + 1 / 24
        self.shown_tilt = self.tilt
        self.placed = list(self.pos)
        LS.set_margin(self.win, LS.Edge.LEFT, round(self.pos[0] - self.sp.L))
        LS.set_margin(self.win, LS.Edge.TOP, round(self.pos[1] - self.sp.U))
        if self.bubble and self.bubble.visible:
            self.bubble.place()

    def _switch_monitor(self, mon):
        self.win.set_visible(False)
        self.mon = mon
        LS.set_monitor(self.win, mon)
        self.win.set_visible(True)

    # ---- behaviour ---------------------------------------------------------------------------------------
    def _log(self, what):
        print(what, flush=True)                             # one journal line per act

    def _cut(self, then):
        """Edgerunners-style glitch cut, then `then()`."""
        self.glitch = self.rng.randint(3, 5)
        self.after_glitch = then

    def _set_anim(self, name, then=None):
        """Play `name`; when a one-shot ends it goes back to the hover loop, then calls `then`."""
        self.anim, self.fi, self.fdir = name, 0.0, 1
        self.after_anim = then
        self.glints = []

    def _play(self, role, then=None):
        """A one-shot of this role (a rig action), if there is one."""
        names = self.sp.by_role.get(role, [])
        if not names:
            return False
        name = self.rng.choice(names)
        self._log(f"{role} {name}")
        self._set_anim(name, then)
        if role in ZAP_ROLES:
            self._zap()
        return True

    def _zap(self):
        self.zap = (time.monotonic(), self.rng.randrange(1 << 30))

    def _twinkle(self, now):
        """Now and then one of the sparkles in her hair (in the drawing on screen) flares into a glint."""
        self.glints = [g for g in self.glints if now - g[3] < 0.25 and g[4] == self.anim]
        if now < self.next_glint or self.glitch or self.gone:
            return
        self.next_glint = now + self.rng.uniform(0.1, 0.4)
        a = self.sp.anims[self.anim]
        sp = a.get("sparkles") or []
        i = a["seq"][min(int(self.fi), len(a["seq"]) - 1)] if sp else 0
        if sp and sp[i]:
            x, y, r = self.rng.choice(sp[i][:12] if self.rng.random() < 0.7 else sp[i])
            self.glints.append((x, y, r, now, self.anim))

    def _busy(self):
        """Teleporting, or a one-shot (reaction, entrance) is playing: let it finish."""
        return self.gone or self.glitch > 0 or self.sp.anims[self.anim]["role"] == "talk" \
            or (self.sp.anims[self.anim]["loop"] == "once" and self.anim != self.hover)

    def _ambient(self, now):
        """The loop for what she's doing: hair streaming while she drifts along, her idle while resting, glances."""
        if self._busy() or self.pose_until:
            return
        want = self.hover
        vx = self.vel[0]
        cur_trail = self.sp.anims[self.anim]["role"] == "trail"
        if self.mode != "drag" and (abs(vx) > 20 or (cur_trail and abs(vx) > 10)):
            side = "_r" if vx > 0 else "_l"
            want = next((n for n in self.sp.by_role.get("trail", []) if n.endswith(side)), want)
        elif self.act == "rest" and self.mode == "float":
            want = next(iter(self.sp.by_role.get("rest", [])), want)
        if want == self.anim == self.hover and self.cur and self.mode == "float" and not self.shy \
                and now >= self.next_look and now - self.last_move < 0.3:
            dx = self.cur[0] - self.pos[0]
            if 120 < math.hypot(dx, self.cur[1] - self.pos[1]) < 700:
                self.next_look = now + self.rng.uniform(8, 20)
                side = "_r" if dx > 0 else "_l"
                look = next((n for n in self.sp.by_role.get("look", []) if n.endswith(side)), None)
                if look:
                    self._set_anim(look)
                    return
        if want != self.anim:
            self._set_anim(want)

    def _teleport(self, near=None, to=None):
        """Tears apart and is gone, then glitches in elsewhere: `to` a spot, `near` (True, or a direction to blink
        off in), or anywhere (maybe the other monitor)."""
        if self.gone or self.sp.anims[self.anim]["role"] == "leave":
            return                                          # already on her way out
        self._log("zap" if to else "blink" if near else "teleport")
        if self.sp.by_role.get("leave"):
            self._play("leave", then=lambda: self._gone(near, to))
        else:
            self._cut(lambda: self._hop(near, to))

    def _gone(self, near, to):
        self.gone = True
        GLib.timeout_add(int(self.rng.uniform(150, 600)), lambda: self._hop(near, to) or False)

    def _pose(self, now):
        poses = self.sp.by_role.get("pose", [])
        if poses:
            name = self.rng.choice(poses)
            self._log(f"pose {name}")
            self.pose_until = now + self.rng.uniform(5, 12)
            self._cut(lambda: self._set_anim(name))

    def _perch(self, now):
        """Settle on an edge of the window you're using: on its top edge, or beside it if it fills the height."""
        win = self._local_win()
        if not win or win[4]:
            return False
        wx, wy, ww, wh, _ = win
        a = self.sp.anims[self.hover]
        below = a["size"][1] - a["face"][0][1]              # face to the bottom of her sprite
        side = self.rng.choice((-1, 1))
        spots = [(wx + ww * (0.82 if side > 0 else 0.18), wy - below + 18),                     # on top
                 (wx + ww + a["size"][0] * 0.35, wy + wh * self.rng.uniform(0.2, 0.7)),         # right side
                 (wx - a["size"][0] * 0.35, wy + wh * self.rng.uniform(0.2, 0.7))]               # left side
        (x0, y0, x1, y1), _ = self._bounds()
        for x, y in spots:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.target = (x, y)
                self.act, self.act_until = "perch", now + self.rng.uniform(20, 45)
                self._log("perch on the active window")
                return True
        return False

    def _visit(self, now):
        """Zap over beside the (still) cursor, on the side toward the middle of the screen."""
        if not self.cur:
            return False
        g = self.mon.get_geometry()
        side = 1 if self.cur[0] < g.width / 2 else -1
        self.target = self._clamp(self.cur[0] + side * 230, self.cur[1] - 40)
        self.act, self.act_until = "visit", now + self.rng.uniform(10, 18)
        self._log("visit the cursor")
        return True

    def _act(self, now, away):
        idle = now - self.last_move
        r = self.rng.random()
        if idle > VISIT_AFTER and r < 0.3 and self._visit(now):
            return
        r = self.rng.random()
        if r < 0.3:
            self._pose(now)
        elif r < 0.4:
            self._teleport()
        elif r < 0.5:
            self._teleport(near=True)
        elif r < 0.58 and self._play("turn"):
            pass
        elif r < 0.72 and not away and self._perch(now):
            pass
        else:
            self.act, self.act_until = "rest", now + self.rng.uniform(15, 40) * (0.4 if away else 1)
            self.target = None
            self._log("rest")
            if away and self.rng.random() < 0.6:
                self._play("bored")

    def _hop(self, near=None, to=None):
        """Back somewhere else: `to` a spot (keeping what she's doing: perch, visit), a short way off for a blink,
        or anywhere (now and then on another monitor)."""
        act = self.act
        if to:
            self.pos = list(self._clamp(*to))
        elif near:
            a = math.atan2(near[1], near[0]) + self.rng.uniform(-0.6, 0.6) if isinstance(near, tuple) \
                else self.rng.uniform(0, 2 * math.pi)
            d = self.rng.uniform(220, 420)
            self.pos = list(self._clamp(self.pos[0] + math.cos(a) * d, self.pos[1] + math.sin(a) * d))
        else:
            if len(self.monitors) > 1 and self.rng.random() < 0.3:
                self._switch_monitor(self.rng.choice([m for m in self.monitors if m != self.mon]))
            self.pos = list(self._spot(anywhere=time.monotonic() - self.last_move > AWAY))
        self.gone = False
        self.next_step = 0.0
        self.vel = [0.0, 0.0]
        self.mode, self.act, self.target = "float", act if to else "drift", None
        self.wait_until = time.monotonic() + self.rng.uniform(10, 30)
        self._log(f"hop to {self.mon.get_connector()} {self.pos[0]:.0f},{self.pos[1]:.0f}")
        ins = self.sp.by_role.get("arrive", []) + self.sp.by_role.get("enter", [])   # the rig's, or the show's glitch-in
        self._set_anim(self.rng.choice(ins) if ins else self.hover)
        self._zap()
        if not ins:
            self.glitch = 3
        self.after_glitch = None

    def _sense(self, now):
        """Poll the cursor (10 Hz, 60 Hz while carried) and the active window (2 Hz)."""
        if now >= self.next_poll or self.mode == "drag":
            self.next_poll = now + 0.1
            c = self.hypr.cursor()
            if c:
                if self.cur_g and math.hypot(c[0] - self.cur_g[0], c[1] - self.cur_g[1]) > 3:
                    self.last_move = now
                self.cur_g = c
                g = self.mon.get_geometry()
                self.cur = (c[0] - g.x, c[1] - g.y)
        if now >= self.next_win:
            self.next_win = now + 0.5
            self.active_win = self.hypr.active()

    def _shy(self, now):
        near = False
        if self.cur and self.mode == "float" and now >= self.trust_until and self.act != "visit" and not self._chatting():
            g = self.mon.get_geometry()
            cx, cy = self.cur
            dx, dy = self.pos[0] - cx, self.pos[1] - cy
            reach = max(self.sp.anims[self.hover]["size"]) * 0.9
            near = 0 <= cx < g.width and 0 <= cy < g.height and math.hypot(dx, dy) < reach
            if near and not self.shy and not self._busy() and self.rng.random() < 0.3:
                self._teleport(near=(dx, dy))                # blink off, away from the cursor
            elif near and not self.shy:                     # drift off, away from the cursor
                d = math.hypot(dx, dy) or 1.0
                self.target = self._clamp(self.pos[0] + dx / d * 420, self.pos[1] + dy / d * 300)
                self.act = "drift"
        if near != self.shy:
            self._log("shy" if near else "calm")
        self.shy = near

    def tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self.last)
        self.last = now
        self._sense(now)
        away = now - self.last_move > AWAY

        # hidden: quiet, or the window you're using is fullscreen (a game, a video)
        win = self._local_win()
        hidden = now < self.quiet_until or bool(win and win[4])
        if hidden != self.hidden:
            self._log("hide" if hidden else "back")
            self.hidden = hidden
        self._input(not hidden and not self.gone and self.alpha_to > 0.05)
        self._shy(now)
        if hidden:
            self.alpha_to = 0.0
        elif self.face_hover or self.mode == "drag":
            self.alpha_to = self.args.opacity
        else:
            self.alpha_to = self.args.opacity * (0.18 if self.shy else 1.0)

        if self.face_hover and not self.petted and now - self.hover_since > 1.5 and not self._busy():
            self.petted = True                              # pointed at for a while: she grins
            self._play("pet")
        if not hidden and self.mode == "float" and not self._busy() and now >= self.next_act and not self.shy \
                and not self.face_hover and not self._chatting():
            self._act(now, away)
            lo, hi = self.args.act_every
            self.next_act = now + self.rng.uniform(lo, hi) * (0.3 if away else 1.0)
        if self.pose_until and now >= self.pose_until and self.glitch == 0:
            self.pose_until = None
            self._cut(lambda: self._set_anim(self.hover))
        if self.act in ("rest", "perch", "visit") and now >= self.act_until:
            self.act, self.target = "drift", None

        self._move(now, dt, away)
        self._speak(now)
        self._ambient(now)
        self._frames(dt)
        self._twinkle(now)
        if self.bubble and self.bubble.visible and self.bubble.hide_at and now >= self.bubble.hide_at \
                and not self.bubble.entry.has_focus() and not self._chatting_now():
            self.bubble.hide()
        self.alpha += (self.alpha_to - self.alpha) * min(1.0, dt * 4)
        self._place()
        self.area.queue_draw()
        return True

    def _move(self, now, dt, away):
        g = self.mon.get_geometry()
        if self.mode == "drag" and not self.cur_g:
            pass                                            # carried without a compositor cursor: the drag moves her
        elif self.mode == "drag":
            # carried: follow the cursor, onto another monitor if it goes there
            for m in self.monitors:
                mg = m.get_geometry()
                if m != self.mon and mg.x <= self.cur_g[0] < mg.x + mg.width and mg.y <= self.cur_g[1] < mg.y + mg.height:
                    self._switch_monitor(m)
                    g = mg
                    self.cur = (self.cur_g[0] - g.x, self.cur_g[1] - g.y)
                    break
            nx, ny = self.cur[0] + self.grab[0], self.cur[1] + self.grab[1]
            if dt > 0:
                k = min(1.0, dt * 12)
                self.vel = [self.vel[0] + ((nx - self.pos[0]) / dt - self.vel[0]) * k,
                            self.vel[1] + ((ny - self.pos[1]) / dt - self.vel[1]) * k]
            self.pos = [nx, ny]
        else:
            # she doesn't travel: she holds still, and goes somewhere by zapping there
            self.vel = [0.0, 0.0]
            if self.target is None and self.act == "drift" and now >= self.wait_until:
                self.target = self._spot(anywhere=away)
            if self.target and not self._busy() and not self._chatting():
                to, self.target = self.target, None
                self.wait_until = now + self.rng.uniform(10, 30) * (0.4 if away else 1)
                if math.hypot(to[0] - self.pos[0], to[1] - self.pos[1]) > 3:
                    self._teleport(to=to)
        # carried: she leans into the motion
        want = max(-TILT, min(TILT, self.vel[0] * 0.0022))
        self.tilt += (want - self.tilt) * min(1.0, dt * 3)

    def _frames(self, dt):
        a = self.sp.anims[self.anim]
        n = len(a["seq"])
        self.fi += dt * a["fps"] * self.fdir
        if a["loop"] == "loop":
            self.fi %= n
        elif a["loop"] == "pingpong":
            if (self.fi >= n - 1 or self.fi < 0) and n > 1:
                self.fdir = -self.fdir
                self.fi = min(n - 1, max(0.0, self.fi))
        elif self.fi >= n:                                  # once (an entrance, a reaction): back to the hover loop
            then = self.after_anim
            self._set_anim(self.hover)
            if then:
                then()
        if self.glitch:
            self.glitch -= 1
            if self.glitch == 0 and self.after_glitch:
                f, self.after_glitch = self.after_glitch, None
                f()

    def draw(self, area, cr, w, h):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        if self.alpha < 0.01 or self.gone:
            return
        if self.glitch:
            name, i = "glitch", self.rng.randrange(self.sp.anims["glitch"]["n"])
        else:
            a = self.sp.anims[self.anim]
            name, i = self.anim, a["seq"][min(int(self.fi), len(a["seq"]) - 1)]
            if a["role"] == "talk":
                i = min(self.talk_draw, a["n"] - 1)
        a = self.sp.anims[name]
        fx, fy = a["face"][i]
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.translate(self.sp.L, self.sp.U)
        if abs(self.shown_tilt) > 0.004:                    # upright: drawn 1:1, no resampling
            cr.rotate(self.shown_tilt)
        cr.set_source_surface(self.sp.frames[name][i], -fx, -fy)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint_with_alpha(self.alpha)
        now = time.monotonic()
        for x, y, r, t0, _ in self.glints:
            fx_.glint(cr, x - fx, y - fy, r, int((now - t0) * 12), self.alpha)
        if self.zap and not self.gone:
            k = int((now - self.zap[0]) * 12)               # on 2s, a new set of streaks each drawing
            if k < 4:
                fx_.streaks(cr, random.Random(self.zap[1] + k), a["size"], self.alpha)
            else:
                self.zap = None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sprites", default=f"{HERE}/mate")
    ap.add_argument("--monitors", default="all", help="all, or connectors like DP-2,HDMI-A-1")
    ap.add_argument("--layer", choices=["top", "overlay", "bottom"], default="top")
    ap.add_argument("--opacity", type=float, default=1.0)
    ap.add_argument("--act-every", type=lambda v: tuple(float(x) for x in v.split(",")), default=(20.0, 60.0),
                    metavar="MIN,MAX", help="seconds between acts (pose, hop, rest, perch, visit)")
    args = ap.parse_args()
    app = Gtk.Application(application_id="io.github.Spectro34.CapriMate")
    app.connect("activate", lambda a: setattr(a, "capri", Capri(a, args)))
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, app.quit)
    app.run([])
    try:
        os.unlink(CTL)
    except OSError:
        pass


if __name__ == "__main__":
    main()
