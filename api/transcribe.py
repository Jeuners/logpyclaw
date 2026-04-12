"""
api/transcribe.py — Echtzeit-Mikrofon-Transkription.
Primär: Mistral Audio API (kein lokales Modell nötig).
Fallback: whisper-cli (wenn Modell vorhanden).
"""
import logging
import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["transcribe"])

MISTRAL_TRANSCRIBE_URL = "https://api.mistral.ai/v1/audio/transcriptions"


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Transkribiert einen Audio-Blob (webm/opus/wav/ogg) zu Text.
    Gibt zurück: { text: "..." }
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "Leere Audio-Datei")

    fname = file.filename or "recording.webm"
    ext = os.path.splitext(fname)[1].lower() or ".webm"
    allowed = {".webm", ".ogg", ".opus", ".wav", ".mp3", ".m4a", ".flac"}
    if ext not in allowed:
        ext = ".webm"

    tmp_path = f"/tmp/agentclaw_mic_{uuid.uuid4().hex[:8]}{ext}"
    try:
        with open(tmp_path, "wb") as f:
            f.write(content)

        # 1. Mistral API (schnell, braucht Internet)
        from storage.providers import load_providers
        mistral_key = load_providers().get("mistral", {}).get("api_key", "")
        if mistral_key:
            text = await _transcribe_mistral(content, fname, ext, mistral_key)
            if text:
                return {"text": text}
            logger.info("Mistral nicht erreichbar — Fallback auf lokales Whisper")

        # 2. Lokales whisper-cli (offline, kein Internet nötig)
        from skills.transcription_skill import _transcribe_audio_whisper, WHISPER_MODEL
        if not os.path.exists(WHISPER_MODEL):
            raise HTTPException(503,
                "Kein Whisper-Modell gefunden und Mistral nicht erreichbar. "
                "Bitte Internet-Verbindung prüfen oder Whisper-Modell herunterladen."
            )
        text = _transcribe_audio_whisper(tmp_path)
        if text.startswith("❌"):
            raise HTTPException(500, text)
        return {"text": text.strip()}

    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


async def _transcribe_mistral(content: bytes, fname: str, ext: str, api_key: str) -> str | None:
    """Transkription via Mistral Audio API."""
    import httpx

    # MIME-Type für Mistral
    mime_map = {
        ".webm": "audio/webm",
        ".ogg":  "audio/ogg",
        ".opus": "audio/ogg",
        ".wav":  "audio/wav",
        ".mp3":  "audio/mpeg",
        ".m4a":  "audio/mp4",
        ".flac": "audio/flac",
    }
    mime = mime_map.get(ext, "audio/webm")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                MISTRAL_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (fname, content, mime)},
                data={"model": "voxtral-mini-2507", "language": "de"},
            )
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("text", "").strip()
                logger.info("Mistral-Transkription: %d Zeichen", len(text))
                return text
            else:
                logger.warning("Mistral-Transkription Fehler %d: %s", resp.status_code, resp.text[:200])
                return None
    except Exception as e:
        logger.warning("Mistral-Transkription Exception: %s", e)
        return None
