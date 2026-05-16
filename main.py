import os
import time
import ctypes
import asyncio
import datetime
import threading
import requests
import syncedlyrics
from dotenv import load_dotenv
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

console = Console()

ui = {
    "song": None,
    "artist": None,
    "position_ms": 0,
    "lyric": None,
    "source": None,
}


def format_time(ms):
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def set_console_title(text):
    ctypes.windll.kernel32.SetConsoleTitleW(text)


def build_panel():
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", min_width=22)
    table.add_column()

    table.add_row("Song:", ui["song"] or "Not listening")
    table.add_row("Author:", ui["artist"] or "Not listening")
    table.add_row("Song progress:", format_time(ui["position_ms"]) if ui["song"] else "0:00")
    table.add_row("Current lyrics:", ui["lyric"] or "Not available")
    table.add_row("Lyrics fetched from:", ui["source"] or "Not fetched")

    return Panel(
        table,
        title="[bold green]♫  Spotify → Discord Lyrics[/bold green]",
        box=box.ROUNDED,
        padding=(1, 2),
    )


async def get_media_state():
    try:
        manager = await MediaManager.request_async()
        sessions = manager.get_sessions()

        session = None
        for s in sessions:
            if s.source_app_user_model_id == "Spotify.exe":
                session = s
                break
        if session is None:
            return None

        timeline = session.get_timeline_properties()
        props = await session.try_get_media_properties_async()
        if props is None or not props.title:
            return None

        position_ms = int(timeline.position.total_seconds() * 1000)
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

        return {"song": props.title, "artist": props.artist, "position_ms": position_ms}
    except Exception:
        return None


def parse_lrc(lrc_text):
    lines = []
    for line in lrc_text.strip().split("\n"):
        if not (line.startswith("[") and "]" in line):
            continue
        tag_end = line.index("]")
        timestamp = line[1:tag_end]
        lyric = line[tag_end + 1:].strip()
        try:
            parts = timestamp.split(":")
            ms = int((int(parts[0]) * 60 + float(parts[1])) * 1000)
            lines.append((ms, lyric))
        except (ValueError, IndexError):
            pass
    return sorted(lines, key=lambda x: x[0])


def get_current_lyric(lyrics_lines, position_ms):
    current = ""
    for timestamp, lyric in lyrics_lines:
        if timestamp <= position_ms:
            current = lyric
        else:
            break
    return current


def update_discord_status(text):
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


async def fetch_lyrics(song, artist):
    loop = asyncio.get_event_loop()
    for provider in ["LRCLIB", "Lyricsify", "NetEase"]:
        lrc = await loop.run_in_executor(
            None, lambda p=provider: syncedlyrics.search(f"{song} {artist}", providers=[p])
        )
        if lrc:
            return parse_lrc(lrc), provider
    return [], None


async def main():
    if not DISCORD_TOKEN:
        console.print("[red]ERROR: Missing DISCORD_TOKEN in .env[/red]")
        return

    current_track_key = None
    lyrics_lines = []
    last_lyric = None

    set_console_title("♫ Spotify → Discord Lyrics")

    with Live(build_panel(), console=console, refresh_per_second=10, screen=True) as live:
        try:
            while True:
                state = await get_media_state()

                if not state:
                    if current_track_key is not None:
                        current_track_key = None
                        lyrics_lines = []
                        last_lyric = None
                        ui.update({"song": None, "artist": None, "position_ms": 0, "lyric": None, "source": None})
                        set_console_title("♫ Spotify → Discord Lyrics")
                        threading.Thread(target=update_discord_status, args=("",), daemon=True).start()
                else:
                    song = state["song"]
                    artist = state["artist"]
                    position_ms = state["position_ms"]
                    track_key = f"{song}|{artist}"

                    ui["song"] = song
                    ui["artist"] = artist
                    ui["position_ms"] = position_ms

                    if track_key != current_track_key:
                        current_track_key = track_key
                        lyrics_lines = []
                        last_lyric = None
                        ui["lyric"] = None
                        ui["source"] = "Fetching..."
                        live.update(build_panel())

                        lyrics_lines, provider = await fetch_lyrics(song, artist)
                        ui["source"] = provider if provider else "Not found"

                    if lyrics_lines:
                        lyric = get_current_lyric(lyrics_lines, position_ms)
                        if lyric != last_lyric:
                            last_lyric = lyric
                            ui["lyric"] = lyric or None
                            display = f"♪ {lyric.lower()}" if lyric else ""
                            set_console_title(display if display else "♫ Spotify → Discord Lyrics")
                            threading.Thread(target=update_discord_status, args=(display,), daemon=True).start()

                live.update(build_panel())
                await asyncio.sleep(0.1)

        except KeyboardInterrupt:
            pass

    console.print("\nStopping — clearing Discord status...")
    update_discord_status("")
    set_console_title("♫ Spotify → Discord Lyrics")
    console.print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
