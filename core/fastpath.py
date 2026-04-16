"""
core/fastpath.py — Tier-1 Dispatch: Deterministische Commands ohne LLM.

Eine Message, die auf einen FastPathCommand matcht, wird DIREKT an eine
Skill-Funktion geleitet — kein Router, kein A2A, keine Reformulierung,
keine Trigger-Regex in Skills.

Das ist der "power user" Pfad. Alles was nicht matcht, geht den normalen
LLM/A2A-Weg (Tier 2).

Neue Commands werden hier registriert — Skills exportieren ihre callable
Funktion und fastpath bindet sie an eine Command-Syntax.
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class FastPathResult:
    """Ergebnis eines Fastpath-Calls."""
    text: str | None = None
    image: str | None = None
    error: str | None = None
    skill_id: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class FastPathCommand:
    """
    Ein direkter Command → Skill-Call.

    pattern: regex, muss Gruppe `url` (oder andere named groups) haben
    handler: Callable(message, **groups) → FastPathResult
    skill_id: Skill-ID für Logging/History
    help: kurze Beschreibung für /help o.ä.
    """
    name: str
    pattern: re.Pattern
    handler: Callable[..., FastPathResult]
    skill_id: str
    help: str = ""


_COMMANDS: list[FastPathCommand] = []


def register(cmd: FastPathCommand) -> None:
    _COMMANDS.append(cmd)
    logger.debug("Fastpath registered: %s → %s", cmd.name, cmd.skill_id)


def match(message: str) -> Optional[tuple[FastPathCommand, dict]]:
    """
    Prüft alle registrierten Commands. Längster Match gewinnt.
    Gibt (command, groupdict) zurück oder None.
    """
    if not message:
        return None
    best: Optional[tuple[FastPathCommand, re.Match]] = None
    for cmd in _COMMANDS:
        m = cmd.pattern.search(message)
        if not m:
            continue
        if best is None or len(m.group(0)) > len(best[1].group(0)):
            best = (cmd, m)
    if not best:
        return None
    cmd, m = best
    return cmd, m.groupdict()


def dispatch(message: str) -> Optional[FastPathResult]:
    """Matcht und führt direkt aus. None wenn kein Command matcht."""
    hit = match(message)
    if not hit:
        return None
    cmd, groups = hit
    logger.info("Fastpath hit: %s → %s (groups=%s)", cmd.name, cmd.skill_id, groups)
    try:
        return cmd.handler(message, **groups)
    except Exception as e:
        logger.exception("Fastpath handler crashed: %s", cmd.name)
        return FastPathResult(error=f"Fastpath-Fehler ({cmd.name}): {e}", skill_id=cmd.skill_id)


def all_commands() -> list[FastPathCommand]:
    return list(_COMMANDS)


# ── Built-in Commands ─────────────────────────────────────────────────────────

YT_URL = r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[^\s\"'<>]+"


def _register_builtins() -> None:
    """Registriert die Standard-Fastpath-Commands."""
    from skills.youtube_skill import _download_transcript, _download_video

    # ── /ytsubs, transdownload — YouTube-Untertitel ─────────────────────────
    def _yt_transcript(message: str, url: str, **_) -> FastPathResult:
        t = _download_transcript(url)
        if "error" in t:
            return FastPathResult(error=t["error"], skill_id="youtube")
        preview = t["text"][:1500] + ("\n…" if len(t["text"]) > 1500 else "")
        text = (
            f"📝 **Transkript geladen** ({t['lang']}, {t['chars']} Zeichen)\n\n"
            f"**Datei:** `{t['filename']}`\n"
            f"**Pfad:** `{t.get('txt_path') or t['vtt_path']}`\n"
            f"**Quelle:** {url}\n\n"
            f"---\n{preview}"
        )
        return FastPathResult(text=text, skill_id="youtube", metadata={"lang": t["lang"]})

    register(FastPathCommand(
        name="ytsubs",
        pattern=re.compile(
            rf"^\s*(?:/ytsubs|transdownload|transcript|transkript|untertitel|subs)\s+(?P<url>{YT_URL})",
            re.IGNORECASE,
        ),
        handler=_yt_transcript,
        skill_id="youtube",
        help="/ytsubs <url> · transdownload <url> — YouTube-Untertitel direkt laden",
    ))

    # ── /ytdl — Video-Download (MP4) ─────────────────────────────────────────
    def _yt_video(message: str, url: str, **_) -> FastPathResult:
        r = _download_video(url, audio_only=False)
        if "error" in r:
            return FastPathResult(error=r["error"], skill_id="youtube")
        text = (
            f"🎬 **Download abgeschlossen**\n\n"
            f"**Datei:** `{r['filename']}`\n"
            f"**Größe:** {r['size_mb']} MB\n"
            f"**Pfad:** `{r['filepath']}`\n"
            f"**Quelle:** {url}"
        )
        return FastPathResult(text=text, skill_id="youtube", metadata=r)

    register(FastPathCommand(
        name="ytdl",
        pattern=re.compile(rf"^\s*/ytdl\s+(?P<url>{YT_URL})", re.IGNORECASE),
        handler=_yt_video,
        skill_id="youtube",
        help="/ytdl <url> — YouTube-Video (MP4) laden",
    ))

    # ── /ytmp3 — Audio-Only MP3 ──────────────────────────────────────────────
    def _yt_audio(message: str, url: str, **_) -> FastPathResult:
        r = _download_video(url, audio_only=True)
        if "error" in r:
            return FastPathResult(error=r["error"], skill_id="youtube")
        text = (
            f"🎵 **MP3 geladen**\n\n"
            f"**Datei:** `{r['filename']}`\n"
            f"**Größe:** {r['size_mb']} MB\n"
            f"**Pfad:** `{r['filepath']}`\n"
            f"**Quelle:** {url}"
        )
        return FastPathResult(text=text, skill_id="youtube", metadata=r)

    register(FastPathCommand(
        name="ytmp3",
        pattern=re.compile(rf"^\s*/ytmp3\s+(?P<url>{YT_URL})", re.IGNORECASE),
        handler=_yt_audio,
        skill_id="youtube",
        help="/ytmp3 <url> — YouTube-Audio (MP3) laden",
    ))

    # ── /tts — Transkription einer Datei (bare filename oder Pfad) ──────────
    def _transcribe(message: str, filename: str, **_) -> FastPathResult:
        from skills.transcription_skill import transcribe_file
        import os
        path = os.path.expanduser(filename.strip())
        if not os.path.isabs(path):
            candidate = os.path.expanduser(f"~/Downloads/AgentClaw/{path}")
            if os.path.exists(candidate):
                path = candidate
        if not os.path.exists(path):
            return FastPathResult(
                error=f"Datei nicht gefunden: {filename}",
                skill_id="transcription",
            )
        text = transcribe_file(path)
        return FastPathResult(text=text, skill_id="transcription")

    register(FastPathCommand(
        name="tts",
        pattern=re.compile(
            r"^\s*/(?:tts|transcribe|transkribiere)\s+(?P<filename>\S[^\n]*?)\s*$",
            re.IGNORECASE,
        ),
        handler=_transcribe,
        skill_id="transcription",
        help="/tts <file> — Audio/Video transkribieren (Whisper)",
    ))

    # ── /img — Bild-Generierung (ComfyUI) ────────────────────────────────────
    def _image_gen(message: str, prompt: str, **_) -> FastPathResult:
        from skills.comfyui import run_comfyui_sync
        prompt = prompt.strip()
        if not prompt:
            return FastPathResult(error="Kein Prompt angegeben.", skill_id="image_gen")
        image_b64 = run_comfyui_sync(prompt)
        if not image_b64 or (isinstance(image_b64, str) and image_b64.startswith("⚠")):
            return FastPathResult(
                error=str(image_b64) or "ComfyUI lieferte kein Bild",
                skill_id="image_gen",
            )
        return FastPathResult(
            text=f"🖼 Bild generiert — Prompt: {prompt[:200]}",
            image=image_b64,
            skill_id="image_gen",
        )

    register(FastPathCommand(
        name="img",
        pattern=re.compile(
            r"^\s*/img\s+(?P<prompt>\S[^\n]*?)\s*$",
            re.IGNORECASE,
        ),
        handler=_image_gen,
        skill_id="image_gen",
        help="/img <prompt> — Bild direkt via ComfyUI generieren",
    ))


_register_builtins()
