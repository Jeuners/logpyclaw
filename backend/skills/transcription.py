"""
backend/skills/transcription.py — Audio/Video Transkription.

Pipeline:
  1. Video → Audio-Spur extrahieren (ffmpeg)
  2. Audio → Text (whisper-cli, lokales Modell)
  3. Fallback: Ollama multimodal (Frame-Analyse für Video)

Config:
  whisper_model: Pfad zum ggml-Modell (WHISPER_MODEL_PATH)
  whisper_cli:   Pfad zum whisper-cli Binary (WHISPER_CLI_PATH)
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import uuid

import httpx

from backend.skills import Skill, SkillConfigField

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODEL_CANDIDATES = [
    os.path.join(_REPO_ROOT, "models", "ggml-large-v3-turbo.bin"),
    os.path.join(_REPO_ROOT, "models", "ggml-small.bin"),
    os.path.join(_REPO_ROOT, "models", "ggml-base.bin"),
    os.path.expanduser("~/Downloads/AgentClaw/ggml-large-v3-turbo.bin"),
    os.path.expanduser("~/Downloads/AgentClaw/ggml-small.bin"),
]
_DEFAULT_MODEL = next((p for p in _MODEL_CANDIDATES if os.path.exists(p)), _MODEL_CANDIDATES[-1])
_DEFAULT_CLI   = shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"

_MEDIA_EXT = re.compile(r"\.(mp4|mov|avi|mkv|webm|mp3|wav|m4a|m4v|aac|ogg|flac)$", re.I)
_FFMPEG    = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
_FFPROBE   = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

_TRANSCRIPTION_MODELS = ["gemma4:e4b", "gemma3:latest", "moondream:latest", "llava:latest"]


class TranscriptionSkill(Skill):
    skill_id = "transcription"
    description = "Transkribiert Audio/Video-Dateien via whisper-cli + Ollama (Fallback)."
    CONFIG_FIELDS = (
        SkillConfigField("whisper_model", env="WHISPER_MODEL_PATH", default=_DEFAULT_MODEL),
        SkillConfigField("whisper_cli",   env="WHISPER_CLI_PATH",   default=_DEFAULT_CLI),
        SkillConfigField("ollama_url",    env="OLLAMA_URL",          default="http://localhost:11434"),
    )

    async def execute(self, query: str) -> str:
        # Dateipfad aus Query extrahieren
        filepath = self._find_file(query)
        if not filepath:
            return (
                "[Transcription] Kein Video/Audio-Pfad gefunden.\n"
                "Format: `/pfad/zur/datei.mp4 transkribieren`"
            )
        if not os.path.exists(filepath):
            return f"[Transcription] Datei nicht gefunden: {filepath}"

        ext = os.path.splitext(filepath)[1].lower()
        is_audio = ext in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

        if is_audio:
            result = self._whisper(filepath)
        else:
            result = self._whisper_from_video(filepath)

        if result.startswith("❌") or "whisper-cli nicht" in result:
            # Ollama-Fallback für Video
            if not is_audio:
                result = await self._ollama_analyze(filepath, query)

        size_mb = round(os.path.getsize(filepath) / 1024 / 1024, 1)
        return (
            f"📝 **Transkription: {os.path.basename(filepath)}** ({size_mb} MB)\n\n"
            f"{result}"
        )

    # ── Whisper ───────────────────────────────────────────────────────────────

    def _whisper(self, audio_path: str) -> str:
        cli   = self.config["whisper_cli"]
        model = self.config["whisper_model"]
        if not os.path.exists(cli):
            return f"❌ whisper-cli nicht gefunden: {cli}\n`brew install whisper-cpp`"
        if not os.path.exists(model):
            return f"❌ Whisper-Modell nicht gefunden: {model}"

        # whisper-cli braucht WAV 16kHz mono
        wav = audio_path
        cleanup = False
        if not audio_path.lower().endswith(".wav"):
            wav = f"/tmp/agentclaw_whisper_{uuid.uuid4().hex[:8]}.wav"
            r = subprocess.run(
                [_FFMPEG, "-i", audio_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav, "-y"],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                return f"❌ WAV-Konvertierung fehlgeschlagen: {r.stderr.decode()[:200]}"
            cleanup = True

        out_base = f"/tmp/agentclaw_wout_{uuid.uuid4().hex[:8]}"
        try:
            r = subprocess.run(
                [cli, "-m", model, "-f", wav, "-l", "auto",
                 "--output-txt", "--output-file", out_base, "--no-prints"],
                capture_output=True, text=True, timeout=600,
            )
            txt_path = out_base + ".txt"
            if os.path.exists(txt_path):
                text = open(txt_path, encoding="utf-8").read().strip()
                try:
                    os.remove(txt_path)
                except Exception:
                    pass
                return text or "⚠ Kein Text erkannt."
            return r.stdout.strip() or f"❌ rc={r.returncode}: {r.stderr[-200:]}"
        except subprocess.TimeoutExpired:
            return "❌ Timeout (>10 min) — Datei zu lang?"
        finally:
            if cleanup and os.path.exists(wav):
                try:
                    os.remove(wav)
                except Exception:
                    pass

    def _whisper_from_video(self, video_path: str) -> str:
        wav = f"/tmp/agentclaw_vidaudio_{uuid.uuid4().hex[:8]}.wav"
        try:
            r = subprocess.run(
                [_FFMPEG, "-i", video_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav, "-y"],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                return f"❌ Audio-Extraktion: {r.stderr.decode()[:200]}"
            return self._whisper(wav)
        finally:
            if os.path.exists(wav):
                try:
                    os.remove(wav)
                except Exception:
                    pass

    # ── Ollama Fallback (Frame-Analyse) ───────────────────────────────────────

    async def _ollama_analyze(self, video_path: str, task: str) -> str:
        model = await self._find_ollama_model()
        if not model:
            return "❌ Kein Ollama-Modell verfügbar (gemma4, gemma3, llava o.ä. installieren)."

        frames = self._extract_frames(video_path)
        if not frames:
            return "❌ Keine Frames extrahiert (ffmpeg-Fehler?)."

        prompt = (
            "Analysiere diese Video-Frames auf Deutsch:\n"
            "1. Was ist zu sehen? (kurze Zusammenfassung)\n"
            "2. Erkennbare Personen/Objekte/Szenen\n"
            "3. Sichtbarer Text oder Beschriftungen"
        )
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.config['ollama_url']}/api/generate",
                json={"model": model, "prompt": prompt, "images": frames, "stream": False},
            )
            r.raise_for_status()
            return r.json().get("response", "").strip()

    async def _find_ollama_model(self) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.config['ollama_url']}/api/tags")
                if r.ok:
                    available = {m["name"] for m in r.json().get("models", [])}
                    for m in _TRANSCRIPTION_MODELS:
                        if m in available:
                            return m
        except Exception:
            pass
        return None

    def _extract_frames(self, video_path: str, max_frames: int = 6) -> list[str]:
        tmp = f"/tmp/agentclaw_frames_{uuid.uuid4().hex[:8]}"
        os.makedirs(tmp, exist_ok=True)
        try:
            subprocess.run(
                [_FFMPEG, "-i", video_path, "-vf", "fps=1/5",
                 "-vframes", str(max_frames), "-q:v", "3",
                 os.path.join(tmp, "frame_%03d.jpg")],
                capture_output=True, timeout=60,
            )
            frames = []
            for f in sorted(os.listdir(tmp))[:max_frames]:
                with open(os.path.join(tmp, f), "rb") as fh:
                    frames.append(base64.b64encode(fh.read()).decode())
            return frames
        except Exception:
            return []
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ── Hilfsfunktionen ───────────────────────────────────────────────────────

    def _find_file(self, query: str) -> str | None:
        m = re.search(r"(~?/[\w\-./]+\.\w+)", query)
        if m:
            return os.path.expanduser(m.group(1))
        m = re.search(r"\b([\w\-]+\.(?:mp4|mov|avi|mkv|webm|mp3|wav|m4a|m4v|aac))\b", query, re.I)
        if m:
            dl = os.path.expanduser("~/Downloads/AgentClaw")
            candidate = os.path.join(dl, m.group(1))
            if os.path.exists(candidate):
                return candidate
        return None
