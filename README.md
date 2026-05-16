# LyricPresence

Displays your Spotify lyrics in real-time on your Discord custom status and CMD title bar.

---

## How it works

LyricPresence reads your current Spotify playback directly from Windows, fetches synced lyrics, and updates your Discord status line-by-line as the song plays.

---

## Requirements

- Windows 10 or 11
- Python 3.10+
- Spotify desktop app
- A Discord account

---

## Setup

### 1. Clone the repo

```
git clone https://github.com/yourusername/LyricPresence.git
cd LyricPresence
```

### 2. Install dependencies

```
pip install -r requirements.txt
```

### 3. Get your Discord token

> **Warning:** Your Discord token is like a password. Never share it publicly or commit it to GitHub.

1. Open Discord in your **browser** (discord.com)
2. Press `F12` to open DevTools
3. Go to the **Network** tab
4. Press `Ctrl+R` to reload
5. In the filter box type `science`
6. Click on any result that appears
7. Go to **Headers** → scroll down to **Request Headers**
8. Find `Authorization` — that value is your token

### 4. Add your token to `.env`

Create a file called `.env` in the project folder with this content:

```
DISCORD_TOKEN=your_token_here
```

Replace `your_token_here` with the token you copied above.

---

## Running

Make sure Spotify is open and playing a song, then run:

```
python main.py
```

You'll see a live display in your terminal:

```
╭─ ♫  Spotify → Discord Lyrics ──────────────────╮
│                                                 │
│  Song:                Princess Bubblegum        │
│  Author:              Dominic Fike              │
│  Song progress:       1:53                      │
│  Current lyrics:      i think i like you        │
│  Lyrics fetched from: LRCLIB                    │
│                                                 │
╰─────────────────────────────────────────────────╯
```

The CMD title bar will also update with the current lyric in real-time.

Press `Ctrl+C` to stop — your Discord status will be cleared automatically.

---

## Notes

- Lyrics are sourced from LRCLIB, Lyricsify, and NetEase (tried in that order)
- If no synced lyrics are found for a song, the status won't update for that track
- LyricPresence only reads your Spotify session — it does not control playback

---

## Troubleshooting

**Nothing shows up / "Not listening"**
- Make sure Spotify is open and a song is actively playing (not paused)
- Check that Spotify appears as a running process on your PC

**"ERROR: Invalid Discord token"**
- Your token may have expired or been reset. Repeat step 3 above to get a fresh one.

**Lyrics not found**
- Try a more popular track — obscure songs may not have synced lyrics available
