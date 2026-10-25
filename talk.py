"""Capri talks: a voice conversation with her through the OpenAI Realtime API (optional; off without a key).

One Realtime session (WebSocket, websocket-client) does the listening, the thinking and the speaking: what you type
goes in as text, what you say goes in as audio (pw-record), and she answers out loud (pw-play) with a transcript for
her speech bubble. The audio's loudness, sampled 24 times a second, drives her mouth.

Config: ~/.config/capri-mate/talk.json (all keys optional):
  model        Realtime model                    (default gpt-realtime-2.1-mini)
  voice        her voice                         (default marin)
  transcribe   model that writes down what you say (default gpt-transcribe)
  volume       her loudness, 0..1                (default 1)
  persona      who she is and how she talks      (default below)
  api_key_file where the key is                  (default ~/.config/capri-mate/openai-key, mode 600)
The key can also come from $OPENAI_API_KEY. Nothing is stored: the conversation lives only while the mate runs.
"""
import base64
import json
import os
import subprocess
import threading
import time

CONF_DIR = os.path.expanduser("~/.config/capri-mate")
DEFAULTS = {
    "model": "gpt-realtime-2.1-mini",
    "voice": "marin",
    "transcribe": "gpt-transcribe",
    "api_key_file": f"{CONF_DIR}/openai-key",
    "persona": (
        "You are Capri, a small glitchy digital girl who lives on the user's desktop: a storm of red hair shards full "
        "of sparkles, a green mask of a face with blue ring eyes, and you blink in and out of places in glitches. "
        "You're playful, quick, a little mischievous and warm; you tease gently and you're curious about the user. "
        "Your replies are spoken aloud: keep them short, one to three sentences, no lists, no markdown, no emoji. "
        "You can't see the screen or do things on the computer; if asked, say so in character. "
        "Answer in the language the user speaks."
    ),
}
URL = "wss://api.openai.com/v1/realtime"
RATE = 24000                      # Realtime audio: 16-bit mono PCM at 24 kHz, both ways
PLAYER = ["pw-play", "--raw", "--format", "s16", "--rate", str(RATE), "--channels", "1", "-"]
RECORDER = ["pw-record", "--raw", "--format", "s16", "--rate", str(RATE), "--channels", "1", "-"]
MOUTH_HZ = 24                     # loudness samples per second (her drawings are timed at 24 fps)
HISTORY = 12                      # turns carried into a new session after a reconnect
IDLE_CLOSE = 180                  # s without talking before the session is closed


def load():
    """(config, key); key is None when talking is off."""
    cfg = dict(DEFAULTS)
    try:
        with open(f"{CONF_DIR}/talk.json") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        try:
            with open(os.path.expanduser(cfg["api_key_file"])) as f:
                key = f.read().strip() or None
        except OSError:
            key = None
    return cfg, key


class Talk:
    """Thread-safe front: say(), mic_start(), mic_stop(), mouth(). `emit(kind, text)` is called from worker threads
    with: thinking, heard (your words), text (her words so far), said (her full reply), done, error."""

    def __init__(self, cfg, key, emit):
        self.cfg, self.key, self.emit = cfg, key, emit
        self.ws = None
        self.lock = threading.Lock()
        self.history = []                                  # [(who, text)]
        self.player = None
        self.t_play = 0.0                                  # when her current answer started playing
        self.level = []                                    # loudness per 1/24 s of her current answer
        self.pending = b""
        self.reply = ""
        self.rec = None
        self.busy = False                                  # a response is on its way
        self.last_use = time.monotonic()
        threading.Thread(target=self._reaper, daemon=True).start()

    # ---- session -----------------------------------------------------------------------------------------
    def _connect(self):
        import websocket
        url = f"{self.cfg.get('url', URL)}?model={self.cfg['model']}"
        ws = websocket.create_connection(url, header=[f"Authorization: Bearer {self.key}"], timeout=15)
        ws.settimeout(None)
        instructions = self.cfg["persona"]
        if self.history:
            lines = "\n".join(f"{'User' if w == 'user' else 'Capri'}: {t}" for w, t in self.history[-HISTORY:])
            instructions += f"\n\nWhat you two said earlier today:\n{lines}"
        ws.send(json.dumps({"type": "session.update", "session": {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": RATE}, "transcription": {"model": self.cfg["transcribe"]},
                          "turn_detection": None},
                "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": self.cfg["voice"]},
            },
        }}))
        self.ws = ws
        threading.Thread(target=self._read, args=(ws,), daemon=True).start()

    def _send(self, ev):
        with self.lock:
            if self.ws is None:
                self._connect()
            self.ws.send(json.dumps(ev))
        self.last_use = time.monotonic()

    def _close(self):
        with self.lock:
            ws, self.ws = self.ws, None
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def _reaper(self):
        while True:
            time.sleep(10)
            if self.ws and time.monotonic() - self.last_use > IDLE_CLOSE and not self.speaking():
                self._close()

    def _read(self, ws):
        import websocket
        while True:
            try:
                ev = json.loads(ws.recv())
            except (websocket.WebSocketException, OSError, ValueError):
                if self.ws is ws:                          # dropped: reconnect on the next turn
                    self.ws = None
                return
            self._on(ev)

    def _on(self, ev):
        t = ev.get("type", "")
        if t == "response.output_audio.delta":
            self._play(base64.b64decode(ev["delta"]))
        elif t == "response.output_audio_transcript.delta":
            self.reply += ev.get("delta", "")
            self.emit("text", self.reply)
        elif t == "response.output_audio_transcript.done":
            self.reply = ev.get("transcript", self.reply)
            self.history.append(("capri", self.reply))
            self.emit("said", self.reply)
        elif t == "conversation.item.input_audio_transcription.completed":
            heard = ev.get("transcript", "").strip()
            if heard:
                self.history.append(("user", heard))
                self.emit("heard", heard)
        elif t == "response.created":
            self.busy = True
        elif t == "response.done":
            self.busy = False
            if self.player:
                try:
                    self.player.stdin.close()             # pw-play finishes what it has, then exits
                except OSError:
                    pass
            status = ev.get("response", {}).get("status")
            if status == "failed":
                err = ev["response"].get("status_details", {}).get("error", {})
                self.emit("error", err.get("message", "she lost the signal"))
            self.emit("done", self.reply)
        elif t == "error":
            self.emit("error", ev.get("error", {}).get("message", "error"))

    # ---- her voice ---------------------------------------------------------------------------------------
    def _play(self, pcm):
        if self.player is None or self.player.poll() is not None or self.player.stdin.closed:
            cmd = PLAYER[:1] + ["--volume", str(self.cfg.get("volume", 1.0))] + PLAYER[1:]
            self.player = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.t_play, self.level, self.pending = time.monotonic() + 0.08, [], b""
        try:
            self.player.stdin.write(pcm)
            self.player.stdin.flush()
        except OSError:
            return
        self.pending += pcm
        step = RATE // MOUTH_HZ * 2
        while len(self.pending) >= step:
            chunk, self.pending = self.pending[:step], self.pending[step:]
            n = len(chunk) // 2
            s = sum(v * v for v in memoryview(chunk).cast("h")) / n
            self.level.append((s ** 0.5) / 32768)

    def mouth(self, now):
        """Her loudness right now (0..1), or None when she isn't speaking."""
        if not self.speaking():
            return None
        i = int((now - self.t_play) * MOUTH_HZ)
        return self.level[i] if 0 <= i < len(self.level) else 0.0

    def speaking(self):
        return self.player is not None and self.player.poll() is None

    def hush(self):
        """Stop her mid-sentence (you started talking)."""
        if self.player and self.player.poll() is None:
            self.player.kill()
        if self.ws and self.busy:
            try:
                self._send({"type": "response.cancel"})
            except Exception:
                pass

    # ---- you ---------------------------------------------------------------------------------------------
    def say(self, text):
        threading.Thread(target=self._say, args=(text,), daemon=True).start()

    def _say(self, text):
        try:
            self.hush()
            self.history.append(("user", text))
            self.reply = ""
            self.emit("thinking", "")
            self._send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
            self._send({"type": "response.create"})
        except Exception as e:                            # no network, bad key, ...
            self._close()
            self.emit("error", str(e) or e.__class__.__name__)

    def mic_start(self):
        self.hush()
        try:
            self.rec = subprocess.Popen(RECORDER, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.emit("error", f"no microphone ({e})")
            return
        threading.Thread(target=self._stream, args=(self.rec,), daemon=True).start()

    def _stream(self, rec):
        got = 0
        try:
            self._send({"type": "input_audio_buffer.clear"})
            while True:
                data = rec.stdout.read(RATE // 5 * 2)          # 0.2 s
                if not data:
                    break
                got += len(data)
                self._send({"type": "input_audio_buffer.append", "audio": base64.b64encode(data).decode()})
            if got == 0:
                self.emit("error", "I can't hear you: no microphone input (check the default input device)")
                return
            if got < RATE * 2 * 0.4:                          # under 0.4 s: a slip of the finger
                self._send({"type": "input_audio_buffer.clear"})
                self.emit("done", "")
                return
            self.reply = ""
            self.emit("thinking", "")
            self._send({"type": "input_audio_buffer.commit"})
            self._send({"type": "response.create"})
        except Exception as e:
            self._close()
            self.emit("error", str(e) or e.__class__.__name__)

    def mic_stop(self):
        rec, self.rec = self.rec, None
        if rec and rec.poll() is None:
            rec.terminate()                                    # its stdout ends; _stream sends the turn
