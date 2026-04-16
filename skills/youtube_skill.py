"""
skills/youtube_skill.py — YouTube Download Skill via yt-dlp
Unterstützt: Video-Download, Audio-Only, Info-Fetch (kein Download)
"""
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime

# yt-dlp Binary: venv > homebrew > PATH
def _find_ytdlp() -> str:
    # 1. Im aktuellen venv
    venv_bin = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.exists(venv_bin):
        return venv_bin
    # 2. Im PATH (shutil.which)
    found = shutil.which("yt-dlp")
    if found:
        return found
    # 3. Homebrew Fallback
    for p in ["/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"]:
        if os.path.exists(p):
            return p
    return "yt-dlp"  # letzter Versuch: direkt im PATH

YTDLP_BIN = _find_ytdlp()
DOWNLOADS_DIR = None  # wird in _get_downloads_dir() lazy gesetzt
_last_download_result: dict = {}  # letztes Download-Ergebnis (filepath etc.)

YT_TRIGGERS = re.compile(
    r"\b(youtube|youtu\.be|yt\.be)\b.*\b(download|lade|herunterladen|speicher|save|hol|fetch)\b|"
    r"\b(download|lade|herunterladen|speicher)\b.{0,40}\b(youtube|youtu\.be|video|clip)\b|"
    r"\b(yt-dlp|ytdlp)\b|"
    r"youtu(\.be|be\.com)/[^\s]+",
    re.IGNORECASE,
)

YT_URL_RX = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[^\s\"'<>]+",
    re.IGNORECASE,
)

YT_AUDIO_RX = re.compile(
    r"\b(audio|mp3|musik|sound|ton|nur.audio|audio.only)\b",
    re.IGNORECASE,
)

YT_INFO_RX = re.compile(
    r"\b(info|informationen|titel|beschreibung|dauer|länge|channel|kanal|wer.*hochgeladen|was.*video)\b",
    re.IGNORECASE,
)


def _get_downloads_dir() -> str:
    global DOWNLOADS_DIR
    if DOWNLOADS_DIR:
        return DOWNLOADS_DIR
    base = os.path.expanduser("~/Downloads/AgentClaw")
    os.makedirs(base, exist_ok=True)
    DOWNLOADS_DIR = base
    return base


def _get_base_args(use_cookies: bool = False) -> list[str]:
    """Standardargumente für yt-dlp. Cookies nur wenn explizit gewünscht."""
    # --remote-components ejs:github: lädt einmalig das JS-Challenge-Solver-Script
    # von GitHub (yt-dlp/ejs). Ohne das scheitern moderne YouTube-Videos mit
    # "Signature solving failed" / "Only images are available".
    args = ["--remote-components", "ejs:github"]
    # node.js Runtime für JS-Challenge-Solving (optional)
    for node_path in ["/opt/homebrew/bin/node", "/usr/local/bin/node",
                      shutil.which("node") or ""]:
        if node_path and os.path.exists(node_path):
            args += ["--extractor-args", f"youtube:player_client=default,web"]
            break
    if use_cookies:
        args += ["--cookies-from-browser", "chrome"]
    return args


def _run_yt_dlp(args: list[str], timeout: int = 120,
                use_cookies: bool = False) -> tuple[str, str, int]:
    """Führt yt-dlp aus. Versucht zuerst ohne Cookies, dann mit."""
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    cmd = [YTDLP_BIN] + _get_base_args(use_cookies=use_cookies) + args
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timeout nach {timeout}s", 1
    except FileNotFoundError:
        return "", f"yt-dlp nicht gefunden: {YTDLP_BIN}", 1


# [download]  45.2% of  232.45MiB at    8.45MiB/s ETA 01:23
_PROGRESS_RX = re.compile(
    r"\[download\]\s+(\d+\.?\d*)%\s+of\s+~?\s*([\d\.]+\s*[KMG]i?B)"
    r"(?:\s+at\s+([\d\.]+\s*[KMG]i?B/s))?"
    r"(?:\s+ETA\s+([\d:]+))?",
    re.IGNORECASE,
)


def _run_yt_dlp_streaming(args: list[str], timeout: int = 600,
                          use_cookies: bool = False,
                          progress_cb=None) -> tuple[str, str, int]:
    """Wie _run_yt_dlp, aber liest stdout zeilenweise und ruft progress_cb.

    Erwartet `--newline` in args (wird automatisch ergänzt).
    Gibt (stdout, stderr_merged, returncode) zurück — stdout enthält
    nach dem Download die finalen Dateipfade (--print after_move:filepath).
    """
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    cmd = [YTDLP_BIN] + _get_base_args(use_cookies=use_cookies) + args
    if "--newline" not in cmd:
        cmd.append("--newline")

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env,
        )
    except FileNotFoundError:
        return "", f"yt-dlp nicht gefunden: {YTDLP_BIN}", 1

    # stderr in einem Thread drainen, sonst deadlock bei vielen Warnungen
    import threading
    stderr_buf: list[str] = []

    def _drain_err():
        for line in proc.stderr:
            stderr_buf.append(line)

    err_thread = threading.Thread(target=_drain_err, daemon=True)
    err_thread.start()

    stdout_lines: list[str] = []
    last_pct = -1

    try:
        for raw in proc.stdout:
            line = raw.rstrip()
            stdout_lines.append(line)
            if progress_cb is None:
                continue
            m = _PROGRESS_RX.search(line)
            if not m:
                continue
            pct = float(m.group(1))
            if int(pct) == last_pct:
                continue
            last_pct = int(pct)
            total = m.group(2) or "?"
            speed = m.group(3) or ""
            eta = m.group(4) or ""
            msg = f"⬇ {int(pct)}% · {total}"
            if speed:
                msg += f" · {speed}"
            if eta:
                msg += f" · ETA {eta}"
            try:
                progress_cb(msg)
            except Exception:
                pass

        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return "\n".join(stdout_lines), f"Timeout nach {timeout}s", 1

    err_thread.join(timeout=2)
    return "\n".join(stdout_lines), "".join(stderr_buf), rc


def _fetch_video_info(url: str) -> dict:
    """Holt Video-Metadaten ohne Download."""
    stdout, stderr, rc = _run_yt_dlp([
        "--dump-json", "--no-playlist",
        "--flat-playlist", "--no-warnings", url
    ], timeout=30, use_cookies=False)
    if rc != 0 or not stdout.strip():
        return {"error": stderr or "Keine Info erhalten"}
    import json
    try:
        data = json.loads(stdout.strip().splitlines()[0])
        return {
            "title": data.get("title", "?"),
            "channel": data.get("uploader") or data.get("channel", "?"),
            "duration": data.get("duration_string") or str(data.get("duration", "?")),
            "upload_date": data.get("upload_date", "?"),
            "view_count": data.get("view_count", "?"),
            "description": (data.get("description") or "")[:500],
            "url": url,
            "thumbnail": data.get("thumbnail", ""),
            "id": data.get("id", ""),
        }
    except Exception as e:
        return {"error": f"JSON-Parse-Fehler: {e}"}


def _download_video(url: str, audio_only: bool = False, progress_cb=None) -> dict:
    """Lädt Video oder Audio herunter. progress_cb(msg) wird pro %-Schritt aufgerufen."""
    dl_dir = _get_downloads_dir()
    uid = uuid.uuid4().hex[:8]

    # --progress erzwingt Fortschrittsausgabe auch wenn --print gesetzt ist
    # (sonst schweigt yt-dlp bis zum Schluss und wir haben keine Updates).
    if audio_only:
        # Dateiname: nur uid — keine Sonderzeichen aus Videotitel
        out_template = os.path.join(dl_dir, f"{uid}.%(ext)s")
        args = [
            "--no-playlist",
            "-f", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "-o", out_template,
            "--print", "after_move:filepath",
            "--progress",
            url,
        ]
    else:
        out_template = os.path.join(dl_dir, f"{uid}.%(ext)s")
        args = [
            "--no-playlist",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", out_template,
            "--print", "after_move:filepath",
            "--progress",
            url,
        ]

    print(f"[YouTube] Starte {'Audio' if audio_only else 'Video'}-Download: {url[:60]}", flush=True)
    print(f"[YouTube] yt-dlp Binary: {YTDLP_BIN}", flush=True)

    if progress_cb:
        try:
            progress_cb("⬇ Starte Download …")
        except Exception:
            pass

    # Erster Versuch: ohne Cookies (schneller, kein Keychain-Dialog)
    stdout, stderr, rc = _run_yt_dlp_streaming(
        args, timeout=600, use_cookies=False, progress_cb=progress_cb
    )

    # Zweiter Versuch: mit Chrome-Cookies (bei Bot-Detection)
    if rc != 0 and ("Sign in" in stderr or "bot" in stderr.lower()
                    or "cookies" in stderr.lower() or "403" in stderr):
        print("[YouTube] Retry mit Chrome-Cookies...", flush=True)
        if progress_cb:
            try:
                progress_cb("🔒 YouTube verlangt Login — retry mit Chrome-Cookies …")
            except Exception:
                pass
        stdout, stderr, rc = _run_yt_dlp_streaming(
            args, timeout=600, use_cookies=True, progress_cb=progress_cb
        )

    if rc != 0:
        return {"error": f"Download fehlgeschlagen: {stderr[:400]}"}

    # Dateipath aus stdout extrahieren
    filepath = ""
    for line in stdout.strip().splitlines():
        if line and os.path.exists(line.strip()):
            filepath = line.strip()
            break

    if not filepath:
        # Fallback: neueste Datei im Download-Verzeichnis suchen
        try:
            files = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir) if f.startswith(uid)]
            if files:
                filepath = max(files, key=os.path.getmtime)
        except Exception:
            pass

    if not filepath or not os.path.exists(filepath):
        return {"error": f"Download scheinbar abgeschlossen aber Datei nicht gefunden.\nOutput: {stdout[:200]}"}

    size_mb = os.path.getsize(filepath) / 1024 / 1024
    filename = os.path.basename(filepath)

    result = {
        "filepath": filepath,
        "filename": filename,
        "size_mb": round(size_mb, 1),
        "type": "audio" if audio_only else "video",
        "url": url,
    }
    global _last_download_result
    _last_download_result = result
    return result


def run_youtube(message: str, progress_cb=None) -> str:
    """Hauptfunktion: Parst die Message und führt die passende Aktion aus."""
    url_match = YT_URL_RX.search(message)
    if not url_match:
        return (
            "❌ Keine YouTube-URL gefunden.\n\n"
            "Beispiele:\n"
            "- `Lade https://youtu.be/xxxx herunter`\n"
            "- `Info zu https://youtube.com/watch?v=xxxx`\n"
            "- `MP3 von https://youtu.be/xxxx`"
        )

    url = url_match.group(0).rstrip(".,;)")
    audio_only = bool(YT_AUDIO_RX.search(message))
    info_only = bool(YT_INFO_RX.search(message)) and not audio_only

    if info_only:
        print(f"[YouTube] Info-Fetch: {url[:60]}", flush=True)
        info = _fetch_video_info(url)
        if "error" in info:
            return f"❌ Info-Fehler: {info['error']}"
        return (
            f"🎬 **{info['title']}**\n\n"
            f"**Kanal:** {info['channel']}\n"
            f"**Dauer:** {info['duration']}\n"
            f"**Aufrufe:** {info.get('view_count', '?'):,}\n"
            f"**Hochgeladen:** {info['upload_date']}\n\n"
            f"**Beschreibung:**\n{info['description']}\n\n"
            f"**URL:** {url}"
        )

    result = _download_video(url, audio_only=audio_only, progress_cb=progress_cb)
    if "error" in result:
        return f"❌ {result['error']}"

    icon = "🎵" if audio_only else "🎬"
    return (
        f"{icon} **Download abgeschlossen!**\n\n"
        f"**Datei:** `{result['filename']}`\n"
        f"**Größe:** {result['size_mb']} MB\n"
        f"**Typ:** {'Audio (MP3)' if audio_only else 'Video (MP4)'}\n"
        f"**Pfad:** `{result['filepath']}`\n"
        f"**Quelle:** {url}"
    )


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult


class YouTubeSkill(BaseSkill):
    id = "youtube"
    name = "YouTube"
    icon = "smart_display"
    description = "Downloads and processes YouTube videos/audio."
    triggers = [
        r"youtu\.?be",
        r"\b(youtube|yt\.com|ytdl|yt-dlp)\b",
        r"\b(download|lade.*runter|herunterladen)\b.{0,30}\b(video|audio|youtube|yt)\b",
    ]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        try:
            progress_cb = context.get("progress_cb")
            result = run_youtube(message, progress_cb=progress_cb)
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
