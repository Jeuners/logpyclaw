"""
backend/skills/youtube.py — YouTube Download/Info/Transkript via yt-dlp.

Modi (automatisch erkannt):
  - info:       "info zu https://youtu.be/..."
  - audio:      "mp3 von https://youtu.be/..."
  - transcript: "transkript https://youtu.be/..."
  - video:      Default (mp4 Download)

Download-Verzeichnis: ~/Downloads/AgentClaw/
"""
from __future__ import annotations

import json as _json
import os
import re
import shutil
import subprocess
import uuid

from backend.skills import Skill

# ── yt-dlp lokalisieren ────────────────────────────────────────────────────────

def _find_ytdlp() -> str:
    venv_bin = os.path.join(os.path.dirname(__import__("sys").executable), "yt-dlp")
    if os.path.exists(venv_bin):
        return venv_bin
    found = shutil.which("yt-dlp")
    if found:
        return found
    for p in ["/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"]:
        if os.path.exists(p):
            return p
    return "yt-dlp"


YTDLP_BIN = _find_ytdlp()
_DL_DIR   = os.path.expanduser("~/Downloads/AgentClaw")
_ENV      = {**os.environ, "PATH": f"/opt/homebrew/bin:/usr/local/bin:{os.environ.get('PATH','')}"}

_URL_RX        = re.compile(r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[^\s\"'<>]+", re.I)
_AUDIO_RX      = re.compile(r"\b(audio|mp3|musik|sound|ton|nur.audio|audio.only)\b", re.I)
_INFO_RX       = re.compile(r"\b(info|informationen|titel|beschreibung|dauer|länge|channel|kanal)\b", re.I)
_TRANSCRIPT_RX = re.compile(r"\b(transkript\w*|transcript\w*|untertitel|subtitle\w*|captions?)\b", re.I)


def _run(args: list[str], timeout: int = 120) -> tuple[str, str, int]:
    os.makedirs(_DL_DIR, exist_ok=True)
    cmd = [YTDLP_BIN, "--extractor-args", "youtube:player_client=android,ios"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_ENV)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timeout nach {timeout}s", 1
    except FileNotFoundError:
        return "", f"yt-dlp nicht gefunden: {YTDLP_BIN}", 1


class YouTubeSkill(Skill):
    skill_id = "youtube"
    description = "YouTube: Video-Download (MP4), Audio (MP3), Info oder Transkript via yt-dlp."

    async def execute(self, query: str) -> str:
        m = _URL_RX.search(query)
        if not m:
            return (
                "[YouTube] Keine YouTube-URL gefunden.\n"
                "Beispiel: `lade https://youtu.be/xxxxx herunter`"
            )
        url = m.group(0).rstrip(".,;)")

        try:
            if _TRANSCRIPT_RX.search(query):
                return await self._transcript(url)
            if _INFO_RX.search(query) and not _AUDIO_RX.search(query):
                return self._info(url)
            audio_only = bool(_AUDIO_RX.search(query))
            return await self._download(url, audio_only)
        except Exception as e:
            return f"[YouTube] Fehler: {e}"

    def _info(self, url: str) -> str:
        stdout, stderr, rc = _run(["--dump-json", "--no-playlist", "--no-warnings", url], timeout=30)
        if rc != 0 or not stdout.strip():
            return f"[YouTube] Info-Fehler: {stderr[:300]}"
        try:
            d = _json.loads(stdout.strip().splitlines()[0])
            return (
                f"🎬 **{d.get('title','?')}**\n\n"
                f"Kanal: {d.get('uploader') or d.get('channel','?')}\n"
                f"Dauer: {d.get('duration_string','?')}\n"
                f"Aufrufe: {d.get('view_count','?'):,}\n"
                f"Datum: {d.get('upload_date','?')}\n\n"
                f"{(d.get('description') or '')[:400]}"
            )
        except Exception as e:
            return f"[YouTube] JSON-Fehler: {e}"

    async def _download(self, url: str, audio_only: bool) -> str:
        os.makedirs(_DL_DIR, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        out = os.path.join(_DL_DIR, f"{uid}.%(ext)s")
        if audio_only:
            args = ["--no-playlist", "-f", "bestaudio/best", "--extract-audio",
                    "--audio-format", "mp3", "-o", out, "--print", "after_move:filepath", url]
        else:
            args = ["--no-playlist", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "--merge-output-format", "mp4", "-o", out, "--print", "after_move:filepath", url]

        stdout, stderr, rc = _run(args, timeout=600)
        if rc != 0:
            return f"[YouTube] Download fehlgeschlagen: {stderr[:300]}"

        filepath = next((ln.strip() for ln in stdout.splitlines() if ln.strip() and os.path.exists(ln.strip())), "")
        if not filepath:
            # Fallback: neueste uid-Datei
            files = [os.path.join(_DL_DIR, f) for f in os.listdir(_DL_DIR) if f.startswith(uid)]
            filepath = max(files, key=os.path.getmtime) if files else ""

        if not filepath or not os.path.exists(filepath):
            return f"[YouTube] Datei nicht gefunden nach Download.\n{stdout[:200]}"

        size_mb = round(os.path.getsize(filepath) / 1024 / 1024, 1)
        icon = "🎵" if audio_only else "🎬"
        return (
            f"{icon} **Download abgeschlossen**\n\n"
            f"Datei: `{os.path.basename(filepath)}`\n"
            f"Größe: {size_mb} MB\n"
            f"Pfad: `{filepath}`"
        )

    async def _transcript(self, url: str) -> str:
        os.makedirs(_DL_DIR, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        out = os.path.join(_DL_DIR, f"{uid}.%(ext)s")
        args = [
            "--no-playlist", "--skip-download",
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "de,en", "--sub-format", "vtt/best", "--convert-subs", "vtt",
            "-o", out, url,
        ]
        _run(args, timeout=60)

        vtt_files = sorted(
            [f for f in os.listdir(_DL_DIR) if f.startswith(uid) and f.endswith(".vtt")],
            key=lambda f: (not f.endswith(".de.vtt"), not f.endswith(".en.vtt"), f),
        )
        if not vtt_files:
            return "[YouTube] Keine Untertitel verfügbar."

        vtt_path = os.path.join(_DL_DIR, vtt_files[0])
        lang = vtt_files[0].split(".")[-2] if "." in vtt_files[0] else "?"
        plain = _vtt_to_plain(vtt_path)
        return (
            f"📝 **Transkript** ({lang}, {len(plain)} Zeichen)\n\n"
            f"{plain[:3000]}{'…' if len(plain) > 3000 else ''}"
        )


def _vtt_to_plain(path: str) -> str:
    try:
        raw = open(path, encoding="utf-8").read()
    except Exception:
        return ""
    lines, last = [], ""
    for line in raw.splitlines():
        s = line.strip()
        if not s or s == "WEBVTT" or "-->" in s or s.isdigit():
            continue
        s = re.sub(r"<[^>]+>", "", s).strip()
        if s and s != last:
            lines.append(s)
            last = s
    return "\n".join(lines)
