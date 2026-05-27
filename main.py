import os
import sys
import time
import ctypes
import asyncio
import datetime
import threading
import tomllib
import tkinter as tk
from tkinter import simpledialog
import requests
import syncedlyrics
from dotenv import load_dotenv
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False

# --- Paths ---

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENV_PATH    = os.path.join(BASE_DIR, ".env")
CONFIG_PATH = os.path.join(BASE_DIR, "config.toml")

# --- Config ---

DEFAULT_CONFIG = """\
[discord]
# Supports placeholders: {lyric}, {song}, {artist}
status_format = "♪ {lyric}"
paused_status = "⏸ paused"
idle_status = ""

[lyrics]
providers = ["LRCLIB", "Lyricsify", "NetEase"]

[app]
update_interval = 0.1
# Seconds of frozen playback position before treating as paused
pause_threshold_seconds = 2.0

[hotkeys]
toggle_pause = "p"
quit = "q"
"""


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


config       = load_config()
_dcfg        = config.get("discord", {})
_lcfg        = config.get("lyrics", {})
_acfg        = config.get("app", {})
_hcfg        = config.get("hotkeys", {})

STATUS_FORMAT   = _dcfg.get("status_format", "♪ {lyric}")
PAUSED_STATUS   = _dcfg.get("paused_status", "⏸ paused")
IDLE_STATUS     = _dcfg.get("idle_status", "")
PROVIDERS       = _lcfg.get("providers", ["LRCLIB", "Lyricsify", "NetEase"])
UPDATE_INTERVAL = float(_acfg.get("update_interval", 0.1))
PAUSE_THRESHOLD = float(_acfg.get("pause_threshold_seconds", 2.0))
HOTKEY_TOGGLE   = _hcfg.get("toggle_pause", "p")
HOTKEY_QUIT     = _hcfg.get("quit", "q")

# --- Token ---


def ask_for_token() -> str | None:
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    token = simpledialog.askstring(
        "LyricPresence Setup",
        "Paste your Discord token below.\n\n"
        "How to get it:\n"
        "1. Open Discord in your browser\n"
        "2. Press F12 → Network tab → reload (Ctrl+R)\n"
        "3. Filter by 'science', click any result\n"
        "4. Headers → Authorization — copy that value",
        show="*",
        parent=root,
    )
    root.destroy()
    return token.strip() if token else None


load_dotenv(ENV_PATH)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    token = ask_for_token()
    if not token:
        sys.exit("No token provided.")
    with open(ENV_PATH, "w") as f:
        f.write(f"DISCORD_TOKEN={token}\n")
    DISCORD_TOKEN = token

# --- App state ---

console    = Console()
quit_event = threading.Event()
app_paused = False  # user-toggled via hotkey

ui: dict = {
    "song":           None,
    "artist":         None,
    "position_ms":    0,
    "lyric":          None,
    "source":         None,
    "spotify_paused": False,
    "app_paused":     False,
}

# --- Hotkeys ---


def setup_hotkeys() -> None:
    if not HAS_KEYBOARD:
        return

    def on_toggle():
        global app_paused
        app_paused = not app_paused

    def on_quit():
        quit_event.set()

    try:
        keyboard.add_hotkey(HOTKEY_TOGGLE, on_toggle, suppress=False)
        keyboard.add_hotkey(HOTKEY_QUIT, on_quit, suppress=False)
    except Exception:
        pass

# --- UI ---


def format_time(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def set_console_title(text: str) -> None:
    ctypes.windll.kernel32.SetConsoleTitleW(text)


def build_panel() -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", min_width=22)
    table.add_column()

    table.add_row("Song:",          ui["song"] or "Not listening")
    table.add_row("Author:",        ui["artist"] or "Not listening")
    table.add_row("Song progress:", format_time(ui["position_ms"]) if ui["song"] else "0:00")

    if ui["spotify_paused"] and ui["song"]:
        lyric_cell = "[yellow]⏸ Spotify paused[/yellow]"
    else:
        lyric_cell = ui["lyric"] or "Not available"
    table.add_row("Current lyrics:", lyric_cell)
    table.add_row("Lyrics fetched from:", ui["source"] or "Not fetched")

    if not HAS_KEYBOARD:
        hint = "[dim]install 'keyboard' to enable hotkeys[/dim]"
    elif ui["app_paused"]:
        hint = (
            f"[yellow bold]⏸ Updates paused[/yellow bold]  "
            f"[dim]{HOTKEY_TOGGLE.upper()}: resume  ·  {HOTKEY_QUIT.upper()}: quit[/dim]"
        )
    else:
        hint = f"[dim]{HOTKEY_TOGGLE.upper()}: pause updates  ·  {HOTKEY_QUIT.upper()}: quit[/dim]"
    table.add_row("", hint)

    return Panel(
        table,
        title="[bold green]♫  LyricPresence[/bold green]",
        subtitle="[yellow]⏸ Updates paused[/yellow]" if ui["app_paused"] else None,
        box=box.ROUNDED,
        padding=(1, 2),
    )

# --- Media ---


async def get_media_state() -> dict | None:
    try:
        manager  = await MediaManager.request_async()
        sessions = manager.get_sessions()

        session = None
        for s in sessions:
            if s.source_app_user_model_id == "Spotify.exe":
                session = s
                break
        if session is None:
            return None

        timeline = session.get_timeline_properties()
        props    = await session.try_get_media_properties_async()
        if props is None or not props.title:
            return None

        raw_position_ms = int(timeline.position.total_seconds() * 1000)
        position_ms     = raw_position_ms
        try:
            last_updated = timeline.last_updated_time
            if last_updated:
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                if last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=datetime.timezone.utc)
                elapsed_ms = (now_utc - last_updated).total_seconds() * 1000
                position_ms += int(max(0, elapsed_ms))
        except Exception:
            pass

        return {
            "song":            props.title,
            "artist":          props.artist,
            "position_ms":     position_ms,
            "raw_position_ms": raw_position_ms,
        }
    except Exception:
        return None

# --- Pause detection ---


class PauseDetector:
    """Detects paused state by watching whether the raw SMTC position advances."""

    def __init__(self, threshold_s: float) -> None:
        self.threshold = threshold_s
        self._history: list[tuple[float, int]] = []

    def reset(self) -> None:
        self._history.clear()

    def update(self, raw_position_ms: int) -> bool:
        now = time.monotonic()
        self._history.append((now, raw_position_ms))
        cutoff = now - self.threshold
        self._history = [(t, p) for t, p in self._history if t >= cutoff]

        if len(self._history) < 2:
            return False
        span = self._history[-1][0] - self._history[0][0]
        if span < self.threshold * 0.9:
            return False
        return abs(self._history[-1][1] - self._history[0][1]) < 500

# --- Lyrics ---


def parse_lrc(lrc_text: str) -> list[tuple[int, str]]:
    lines = []
    for line in lrc_text.strip().split("\n"):
        if not (line.startswith("[") and "]" in line):
            continue
        tag_end   = line.index("]")
        timestamp = line[1:tag_end]
        lyric     = line[tag_end + 1:].strip()
        try:
            parts = timestamp.split(":")
            ms    = int((int(parts[0]) * 60 + float(parts[1])) * 1000)
            lines.append((ms, lyric))
        except (ValueError, IndexError):
            pass
    return sorted(lines, key=lambda x: x[0])


def get_current_lyric(lyrics_lines: list[tuple[int, str]], position_ms: int) -> str:
    current = ""
    for timestamp, lyric in lyrics_lines:
        if timestamp <= position_ms:
            current = lyric
        else:
            break
    return current

# --- Discord ---


def update_discord_status(text: str) -> bool:
    headers = {"Authorization": DISCORD_TOKEN, "Content-Type": "application/json"}
    payload = {
        "custom_status": {
            "text": text[:128] if text else "",
            "emoji_name": "musical_note" if text else None,
        }
    }
    try:
        r = requests.patch(
            "https://discord.com/api/v9/users/@me/settings",
            json=payload, headers=headers, timeout=2,
        )
        if r.status_code == 429:
            time.sleep(r.json().get("retry_after", 5))
        return r.status_code == 200
    except requests.RequestException:
        return False


async def fetch_lyrics(song: str, artist: str) -> tuple[list, str | None]:
    loop = asyncio.get_event_loop()
    for provider in PROVIDERS:
        lrc = await loop.run_in_executor(
            None, lambda p=provider: syncedlyrics.search(f"{song} {artist}", providers=[p])
        )
        if lrc:
            return parse_lrc(lrc), provider
    return [], None

# --- Main loop ---


async def main() -> None:
    current_track_key = None
    lyrics_lines:  list[tuple[int, str]] = []
    last_lyric:    str | None = None
    last_discord:  str | None = None  # last text actually sent to Discord
    prev_app_paused = False

    pause_detector = PauseDetector(threshold_s=PAUSE_THRESHOLD)
    setup_hotkeys()
    set_console_title("♫ LyricPresence")

    with Live(build_panel(), console=console, refresh_per_second=10, screen=True) as live:
        try:
            while not quit_event.is_set():
                state = await get_media_state()

                # Detect app-pause toggle: force Discord resync on resume
                just_unpaused   = prev_app_paused and not app_paused
                prev_app_paused = app_paused
                ui["app_paused"] = app_paused
                if just_unpaused:
                    last_discord = None

                desired_discord: str | None = None  # None = don't touch Discord this tick

                if not state:
                    if current_track_key is not None:
                        current_track_key = None
                        lyrics_lines      = []
                        last_lyric        = None
                        pause_detector.reset()
                        ui.update({
                            "song": None, "artist": None, "position_ms": 0,
                            "lyric": None, "source": None, "spotify_paused": False,
                        })
                        set_console_title("♫ LyricPresence")
                    if not app_paused:
                        desired_discord = IDLE_STATUS

                else:
                    song        = state["song"]
                    artist      = state["artist"]
                    position_ms = state["position_ms"]
                    raw_pos     = state["raw_position_ms"]
                    track_key   = f"{song}|{artist}"

                    ui["song"]        = song
                    ui["artist"]      = artist
                    ui["position_ms"] = position_ms

                    if track_key != current_track_key:
                        current_track_key = track_key
                        lyrics_lines      = []
                        last_lyric        = None
                        pause_detector.reset()
                        ui.update({"lyric": None, "source": "Fetching...", "spotify_paused": False})
                        live.update(build_panel())

                        lyrics_lines, provider = await fetch_lyrics(song, artist)
                        ui["source"] = provider or "Not found"

                    is_paused            = pause_detector.update(raw_pos)
                    ui["spotify_paused"] = is_paused

                    if not app_paused:
                        if is_paused:
                            desired_discord = PAUSED_STATUS
                        elif lyrics_lines:
                            lyric   = get_current_lyric(lyrics_lines, position_ms)
                            display = (
                                STATUS_FORMAT.format(
                                    lyric=lyric.lower() if lyric else "",
                                    song=song,
                                    artist=artist,
                                )
                                if lyric else ""
                            )
                            if lyric != last_lyric:
                                last_lyric   = lyric
                                ui["lyric"]  = lyric or None
                                set_console_title(display or "♫ LyricPresence")
                            desired_discord = display

                if desired_discord is not None and desired_discord != last_discord:
                    last_discord = desired_discord
                    threading.Thread(
                        target=update_discord_status,
                        args=(desired_discord,),
                        daemon=True,
                    ).start()

                live.update(build_panel())
                await asyncio.sleep(UPDATE_INTERVAL)

        except KeyboardInterrupt:
            pass

    console.print("\nStopping — clearing Discord status...")
    update_discord_status("")
    set_console_title("♫ LyricPresence")
    console.print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
