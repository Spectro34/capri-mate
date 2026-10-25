#!/usr/bin/env bash
# Capri desktop mate: one-command setup. Safe to re-run: finished steps are skipped.
#
#   ./setup.sh [--video FILE] [--no-rig] [--no-service] [--height PX]
#
#   --video FILE   use your own copy of youtu.be/Nli_SZ-DBco instead of downloading it
#   --no-rig       skip the Blender rig (she keeps her two footage loops, without reactions, zaps or talking mouth)
#   --no-service   don't install the systemd user service; run `python3 mate.py` yourself
#   --height PX    her height on screen (default 260)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
REPO=$PWD
VIDEO="" RIG=1 SERVICE=1 HEIGHT=260
URL="https://youtu.be/Nli_SZ-DBco"
START=240.01                     # where her scene starts in that video (s); frame numbers count from here at 20 fps

while [ $# -gt 0 ]; do
    case "$1" in
        --video) VIDEO=$(readlink -f "$2"); shift 2 ;;
        --no-rig) RIG=0; shift ;;
        --no-service) SERVICE=0; shift ;;
        --height) HEIGHT=$2; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)"; exit 2 ;;
    esac
done

step() { printf '\n\033[1;38;2;250;2;69m▌\033[0m \033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ---- 1. what the mate runs on: system Python with GTK 4 + gtk4-layer-shell ---------------------------------
step "1/6 system packages"
runtime_ok() {
    python3 - <<'EOF' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gtk4LayerShell  # noqa: F401
import cairo  # noqa: F401
EOF
}
if runtime_ok && command -v ffmpeg >/dev/null; then
    note "GTK 4, gtk4-layer-shell, pycairo and ffmpeg found"
else
    . /etc/os-release 2>/dev/null || true
    case " ${ID:-} ${ID_LIKE:-} " in
        *" opensuse"*|*" suse "*|*" sles "*) CMD="sudo zypper install python3-gobject python3-gobject-cairo python3-pycairo typelib-1_0-Gtk-4_0 typelib-1_0-Gtk4LayerShell-1_0 libgtk4-layer-shell0 ffmpeg python3-websocket-client pipewire-tools" ;;
        *" fedora "*|*" rhel "*) CMD="sudo dnf install python3-gobject python3-cairo gtk4 gtk4-layer-shell ffmpeg-free python3-websocket-client pipewire-utils" ;;
        *" arch "*) CMD="sudo pacman -S --needed python-gobject python-cairo gtk4 gtk4-layer-shell ffmpeg python-websocket-client pipewire" ;;
        *" debian "*|*" ubuntu "*) CMD="sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-gtk4layershell-1.0 ffmpeg python3-websocket pipewire-bin" ;;
        *) CMD="" ;;
    esac
    [ -n "$CMD" ] || die "Install GTK 4 + PyGObject, gtk4-layer-shell (with its GObject typelib), pycairo and ffmpeg, then re-run."
    note "Missing runtime packages. For ${PRETTY_NAME:-your system}:"
    note "  $CMD"
    read -r -p "  Run it now? [y/N] " yes
    [ "${yes,,}" = y ] || die "Install them, then re-run ./setup.sh"
    $CMD
    runtime_ok || die "Still can't load Gtk 4 / Gtk4LayerShell from python3."
fi
python3 -c "import websocket" 2>/dev/null || note "(optional) python3 websocket-client is missing: needed only for talking to her"
command -v pw-play >/dev/null || note "(optional) pw-play/pw-record are missing: needed only for her voice and your mic"

# ---- 2. build tools in a venv: SAM 2 (via transformers, CPU torch), OpenCV, yt-dlp ---------------------------
step "2/6 build tools (.venv, about 2 GB, once)"
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import torch, transformers, cv2, PIL" 2>/dev/null; then
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
    .venv/bin/pip install -q -r requirements-build.txt
fi
note "$(.venv/bin/python -c 'import torch, transformers; print(f"torch {torch.__version__}, transformers {transformers.__version__}")')"

# ---- 3. the source clip ---------------------------------------------------------------------------------------
step "3/6 the clip she comes from"
if [ -z "$VIDEO" ]; then
    VIDEO=$(ls media/source.* 2>/dev/null | head -1 || true)
fi
if [ -z "$VIDEO" ]; then
    mkdir -p media
    JS=()
    if command -v deno >/dev/null; then :; elif command -v node >/dev/null; then JS=(--js-runtimes node); else
        die "yt-dlp needs a JavaScript runtime for YouTube (install deno or node), or pass --video FILE"
    fi
    note "downloading $URL (video only, 1080p, about 55 MB)"
    .venv/bin/yt-dlp -q --no-warnings "${JS[@]}" -f "bv*[height=1080][ext=mp4]/bv*[height=1080]" \
        -o "media/source.%(ext)s" "$URL"
    VIDEO=$(ls media/source.* | head -1)
fi
note "$VIDEO"

# ---- 4. cut her out (SAM 2 on CPU, a few minutes) ---------------------------------------------------------------
step "4/6 cutting her out of the background"
cut() {   # name a b extra-args...
    local name=$1 a=$2 b=$3; shift 3
    if [ -f "mate-src/$name/cut.json" ]; then note "$name: done before"; return; fi
    .venv/bin/python cutout.py "$VIDEO" "$a" "$b" "mate-src/$name" --start "$START" --width 1920 "$@"
}
cut float 34 67 --frame 50 --pos 960,905 960,1040 --neg 560,1040 300,300
cut glitchin 417 429 --frame 428

# ---- 5. the rig: her layers on Blender bones, actions drawn on 2s and 3s like the show --------------------------
step "5/6 rig (Blender)"
if [ "$RIG" = 1 ]; then
    BLENDER=$(command -v blender || ls /usr/bin/blender-[0-9]* 2>/dev/null | sort -V | tail -1 || true)   # openSUSE: blender-5.2
    BV=$([ -n "$BLENDER" ] && "$BLENDER" --version 2>/dev/null | sed -n 's/^Blender \([0-9]*\.[0-9]*\).*/\1/p' | head -1 || true)
    if [ -z "$BLENDER" ] || [ "$(printf '%s\n4.2\n' "${BV:-0}" | sort -V | head -1)" != 4.2 ]; then
        note "Blender 4.2 or newer not found (tested with 5.2): skipping the rig. Install it and re-run for her reactions."
    else
        .venv/bin/python rig/parts.py >/dev/null
        "$BLENDER" -b --python rig/rig.py 2>&1 | grep -E "^rendered|Error" || true
    fi
else
    note "skipped (--no-rig)"
fi

# ---- 6. sprites, and the service ---------------------------------------------------------------------------------
step "6/6 sprites"
.venv/bin/python build_mate.py --height "$HEIGHT" | tail -1

if [ "$SERVICE" = 1 ]; then
    UNIT="$HOME/.config/systemd/user/capri-mate.service"
    mkdir -p "$(dirname "$UNIT")"
    sed "s|@REPO@|$REPO|g" capri-mate.service > "$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable capri-mate.service >/dev/null
    if systemctl --user is-active -q graphical-session.target; then
        systemctl --user restart capri-mate.service
        note "running: systemctl --user status capri-mate"
    else
        note "installed, but your session doesn't start graphical-session.target."
        note "Start her from your compositor instead, e.g. Hyprland: exec-once = systemctl --user start capri-mate"
    fi
else
    note "run her with: python3 $REPO/mate.py"
fi

printf '\n\033[1;38;2;9;236;110m✓ Capri is set up.\033[0m Middle-click her face to talk (needs an OpenAI key, see README).\n'
