"""
skills/youtube_skill.py — YouTube Download Skill via yt-dlp
Unterstützt: Video-Download, Audio-Only, Info-Fetch (kein Download)
"""
import os
import re
import subprocess
import uuid
from datetime import datetime

YTDLP_BIN = "/opt/homebrew/bin/yt-dlp"
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


def _get_base_args() -> list[str]:
    """Standardargumente für alle yt-dlp Aufrufe: JS-Runtime + Cookies."""
    node_path = "/opt/homebrew/bin/node"
    args = []
    if os.path.exists(node_path):
        args += ["--js-runtimes", f"node:{node_path}"]
    # Chrome-Cookies für YouTube-Auth (Bot-Detection umgehen)
    args += ["--cookies-from-browser", "chrome"]
    return args


def _run_yt_dlp(args: list[str], timeout: int = 120) -> tuple[str, str, int]:
    """Führt yt-dlp aus und gibt (stdout, stderr, returncode) zurück."""
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    try:
        r = subprocess.run(
            [YTDLP_BIN] + _get_base_args() + args,
            capture_output=True, text=True, timeout=timeout, env=env
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "Timeout nach {}s".format(timeout), 1
    except FileNotFoundError:
        return "", "yt-dlp nicht gefunden unter " + YTDLP_BIN, 1


def _fetch_video_info(url: str) -> dict:
    """Holt Video-Metadaten ohne Download."""
    stdout, stderr, rc = _run_yt_dlp([
        "--dump-json", "--no-playlist",
        "--flat-playlist", url
    ], timeout=30)
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


def _download_video(url: str, audio_only: bool = False) -> dict:
    """Lädt Video oder Audio herunter."""
    dl_dir = _get_downloads_dir()
    uid = uuid.uuid4().hex[:8]

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
            url,
        ]

    print(f"[YouTube] Starte {'Audio' if audio_only else 'Video'}-Download: {url[:60]}", flush=True)
    stdout, stderr, rc = _run_yt_dlp(args, timeout=300)

    if rc != 0:
        return {"error": f"Download fehlgeschlagen: {stderr[:300]}"}

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


def run_youtube(message: str) -> str:
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

    result = _download_video(url, audio_only=audio_only)
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
