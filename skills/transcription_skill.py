"""
skills/transcription_skill.py — Video/Audio Transkription
Nutzt: ffmpeg (Audio-Extraktion) + Ollama gemma4 (Multimodal-Transkription/Analyse)
Fallback: Ollama whisper wenn verfügbar
"""
import os
import re
import subprocess
import uuid
import base64
import requests
from datetime import datetime

FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg"
FFPROBE_BIN = "/opt/homebrew/bin/ffprobe"
OLLAMA_URL = "http://localhost:11434"
WHISPER_CLI = "/opt/homebrew/bin/whisper-cli"
WHISPER_MODEL = os.path.expanduser("~/Downloads/AgentClaw/ggml-large-v3-turbo.bin")

# Bevorzugte Modelle (in Reihenfolge — erstes verfügbares wird genutzt)
TRANSCRIPTION_MODELS = ["gemma4:e4b", "gemma3:latest", "moondream:latest", "llava:latest"]
AUDIO_TRANSCRIPTION_MODELS = ["whisper:latest"]

TRANSCRIBE_TRIGGERS = re.compile(
    r"transkri\w+|transcri\w+|verschriftt?lich\w*|"
    r"text\s+aus\s+(?:dem\s+)?(?:video|audio)|video\s+zu\s+text|"
    r"was\s+\w*\s*(?:sagt|spricht|redet|sagen|sprechen|reden)|"
    r"audio\s+zu\s+text|speech.to.text|"
    r"analysier\w*\s+\w*\s*(?:video|datei)|(?:video|datei)\s+\w*\s*analysier\w*|"
    r"was\s+passiert\s+\w*\s*video|beschreib\w*\s+\w*\s*video|"
    r"(?:vollständige|komplette|ganze)\s+transkription|"
    r"transkription\s+(?:der|des|von|erstellen|machen)",
    re.IGNORECASE,
)


def _get_available_model() -> str | None:
    """Gibt das erste verfügbare Ollama-Modell zurück."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.ok:
            available = {m["name"] for m in r.json().get("models", [])}
            for model in TRANSCRIPTION_MODELS:
                if model in available:
                    return model
    except Exception:
        pass
    return None


def _get_whisper_model() -> str | None:
    """Gibt ein verfügbares Whisper-Modell zurück."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.ok:
            available = {m["name"] for m in r.json().get("models", [])}
            for model in AUDIO_TRANSCRIPTION_MODELS:
                if model in available:
                    return model
    except Exception:
        pass
    return None


def _transcribe_audio_whisper(filepath: str) -> str:
    """Transkribiert Audio via whisper-cli (whisper.cpp)."""
    if not os.path.exists(WHISPER_CLI):
        return "❌ whisper-cli nicht gefunden. Bitte: `brew install whisper-cpp`"

    if not os.path.exists(WHISPER_MODEL):
        return (
            f"❌ Whisper-Modell nicht gefunden: `{WHISPER_MODEL}`\n\n"
            "Modell wird heruntergeladen — bitte kurz warten und erneut versuchen."
        )

    # whisper-cli braucht WAV-Format
    wav_path = filepath
    needs_conversion = not filepath.lower().endswith(".wav")
    if needs_conversion:
        wav_path = f"/tmp/agentclaw_whisper_{uuid.uuid4().hex[:8]}.wav"
        try:
            r = subprocess.run(
                [FFMPEG_BIN, "-i", filepath, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path, "-y"],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0:
                return f"❌ Konvertierung zu WAV fehlgeschlagen: {r.stderr.decode()[:200]}"
        except Exception as e:
            return f"❌ Konvertierung fehlgeschlagen: {e}"

    print(f"[Transcription] whisper-cli startet für {os.path.basename(filepath)}", flush=True)
    out_base = f"/tmp/agentclaw_whisper_out_{uuid.uuid4().hex[:8]}"
    try:
        result = subprocess.run(
            [WHISPER_CLI, "-m", WHISPER_MODEL, "-f", wav_path,
             "-l", "auto", "--output-txt", "--output-file", out_base,
             "--no-prints"],  # verhindert Fortschrittsausgabe in stdout
            capture_output=True, text=True, timeout=600,
        )
        # Ausgabe-Datei suchen
        txt_path = out_base + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            try:
                os.remove(txt_path)
            except Exception:
                pass
            return text if text else "⚠️ Whisper hat keinen Text erkannt (leise Aufnahme?)"
        # Fallback: stdout ohne Timing-Zeilen
        if result.stdout.strip():
            lines = [l for l in result.stdout.splitlines()
                     if not l.startswith(("whisper_", "ggml_", "system_", "main:", "["))]
            return "\n".join(lines).strip() or result.stdout.strip()
        return f"❌ whisper-cli Fehler (rc={result.returncode}): {result.stderr[-300:]}"
    except subprocess.TimeoutExpired:
        return "❌ Transkription Timeout (> 10 min). Datei zu lang?"
    except Exception as e:
        return f"❌ whisper-cli Fehler: {e}"
    finally:
        if needs_conversion and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass


def _get_video_duration(filepath: str) -> float:
    """Gibt Video-Dauer in Sekunden zurück."""
    try:
        r = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-print_format", "json",
             "-show_format", filepath],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            import json
            data = json.loads(r.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return 0


def _extract_frames(video_path: str, max_frames: int = 8) -> list[str]:
    """Extrahiert gleichmäßig verteilte Frames als JPEG base64."""
    tmp_dir = f"/tmp/agentclaw_frames_{uuid.uuid4().hex[:8]}"
    os.makedirs(tmp_dir, exist_ok=True)
    frames_b64 = []

    try:
        duration = _get_video_duration(video_path)
        if duration <= 0:
            duration = 60

        # max_frames Frames gleichmäßig verteilt
        interval = max(duration / max_frames, 1)

        r = subprocess.run([
            FFMPEG_BIN, "-i", video_path,
            "-vf", f"fps=1/{interval:.1f}",
            "-vframes", str(max_frames),
            "-q:v", "3",
            "-f", "image2",
            os.path.join(tmp_dir, "frame_%03d.jpg"),
        ], capture_output=True, timeout=60)

        frame_files = sorted([
            os.path.join(tmp_dir, f)
            for f in os.listdir(tmp_dir)
            if f.endswith(".jpg")
        ])

        for fp in frame_files[:max_frames]:
            with open(fp, "rb") as fh:
                frames_b64.append(base64.b64encode(fh.read()).decode())
    except Exception as e:
        print(f"[Transcription] Frame-Extraktion Fehler: {e}", flush=True)
    finally:
        # Cleanup
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    return frames_b64


def _extract_audio_segment(video_path: str, duration_sec: int = 60) -> str | None:
    """Extrahiert ersten N Sekunden Audio als WAV für Transkription."""
    tmp_audio = f"/tmp/agentclaw_audio_{uuid.uuid4().hex[:8]}.wav"
    try:
        r = subprocess.run([
            FFMPEG_BIN, "-i", video_path,
            "-t", str(duration_sec),
            "-ar", "16000", "-ac", "1",
            "-f", "wav", tmp_audio, "-y",
        ], capture_output=True, timeout=60)
        if r.returncode == 0 and os.path.exists(tmp_audio):
            return tmp_audio
    except Exception as e:
        print(f"[Transcription] Audio-Extraktion Fehler: {e}", flush=True)
    return None


def _transcribe_with_ollama(filepath: str, model: str, task: str = "transcribe") -> str:
    """Transkribiert/analysiert Video via Ollama multimodal model."""
    ext = os.path.splitext(filepath)[1].lower()
    is_video = ext in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
    is_audio = ext in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

    print(f"[Transcription] {model} analysiert {os.path.basename(filepath)[:40]}", flush=True)

    if is_video:
        # Frames extrahieren und multimodal senden
        frames = _extract_frames(filepath, max_frames=6)
        if not frames:
            return "❌ Keine Frames extrahiert — ffmpeg Fehler?"

        prompt = (
            "Analysiere diese Video-Frames und beschreibe:\n"
            "1. Was ist im Video zu sehen? (kurze Zusammenfassung)\n"
            "2. Welche Personen/Objekte/Szenen sind erkennbar?\n"
            "3. Was wird gesprochen oder angezeigt (Text im Bild)?\n"
            "Antworte auf Deutsch, strukturiert und präzise."
        )
        if "transkrib" in task.lower() or "text" in task.lower():
            prompt = (
                "Diese Frames stammen aus einem Video. "
                "Extrahiere und transkribiere allen sichtbaren Text und gesprochene Inhalte (falls erkennbar). "
                "Beschreibe dann kurz den Gesamtinhalt. Antworte auf Deutsch."
            )

        try:
            payload = {
                "model": model,
                "prompt": prompt,
                "images": frames,
                "stream": False,
            }
            r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
            if r.ok:
                return r.json().get("response", "").strip()
            else:
                return f"❌ Ollama Fehler: {r.status_code}"
        except Exception as e:
            return f"❌ Ollama-Anfrage fehlgeschlagen: {e}"

    elif is_audio:
        # Audio: Whisper verwenden (Vision-Modelle können kein Audio)
        return _transcribe_audio_whisper(filepath)

    return "❌ Nicht unterstütztes Dateiformat"


def transcribe_file(filepath: str, task: str = "") -> str:
    """Öffentliche Funktion: Transkribiert/analysiert eine lokale Video/Audio-Datei."""
    if not os.path.exists(filepath):
        return f"❌ Datei nicht gefunden: {filepath}"

    model = _get_available_model()
    if not model:
        return "❌ Kein Ollama-Modell verfügbar. Bitte gemma4 oder gemma3 installieren."

    size_mb = os.path.getsize(filepath) / 1024 / 1024
    filename = os.path.basename(filepath)
    print(f"[Transcription] Starte: {filename} ({size_mb:.1f} MB) mit {model}", flush=True)

    result = _transcribe_with_ollama(filepath, model, task)

    return (
        f"📝 **Transkription/Analyse: {filename}**\n"
        f"Modell: `{model}` | Größe: {size_mb:.1f} MB\n\n"
        f"{result}"
    )


def transcribe_uploaded_video(video_data_b64: str, filename: str, task: str = "") -> str:
    """Transkribiert ein direkt hochgeladenes Video (base64)."""
    # Temporäre Datei speichern
    tmp_path = f"/tmp/agentclaw_upload_{uuid.uuid4().hex[:8]}_{filename}"
    try:
        # data URL prefix entfernen falls vorhanden
        if "," in video_data_b64:
            video_data_b64 = video_data_b64.split(",", 1)[1]
        with open(tmp_path, "wb") as f:
            f.write(base64.b64decode(video_data_b64))
        return transcribe_file(tmp_path, task)
    except Exception as e:
        return f"❌ Fehler beim Verarbeiten des Videos: {e}"
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def run_transcription(message: str, attachment_path: str = None) -> str:
    """Wird direkt aus process_task() aufgerufen."""
    if attachment_path:
        return transcribe_file(attachment_path, task=message)

    # URL im Text suchen (z.B. lokaler Pfad)
    path_match = re.search(r"(?:/[\w\-./]+\.(?:mp4|mov|avi|mkv|webm|mp3|wav|m4a|m4v))", message, re.IGNORECASE)
    if path_match:
        return transcribe_file(path_match.group(0), task=message)

    return (
        "❓ Kein Video/Audio gefunden.\n\n"
        "Bitte ein Video oder Audio-Datei hochladen oder den Pfad angeben:\n"
        "- Video hochladen über den 📎-Button\n"
        "- Pfad: `/Downloads/mein_video.mp4 transkribieren`"
    )


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult


class TranscriptionSkill(BaseSkill):
    id = "transcription"
    name = "Transcription"
    icon = "mic"
    description = "Transcribes audio and video files via Whisper/Ollama."
    triggers = [
        r"\b(transkribier\w*|transcribe|transkript\w*|verschrift\w*)\b",
        r"\b(audio|aufnahme|recording)\b.{0,30}\b(text|transkript|transcr)\b",
    ]
    requires = []

    def matches(self, message: str) -> bool:
        # Nicht triggern wenn YouTube-URL vorhanden — YouTube-Skill soll zuerst laufen
        if re.search(r"youtu(\.be|be\.com)", message, re.IGNORECASE):
            return False
        return super().matches(message)

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        attachment_path = context.get("attachment_path")
        try:
            result = run_transcription(message, attachment_path=attachment_path)
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
