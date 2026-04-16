"""
api/tts.py — Text-to-Speech Endpunkte (Mistral Voxtral, Google Cloud TTS).
"""
import base64
import logging

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from config.settings import settings
from storage.providers import load_providers

GOOGLE_TTS_URL = settings.GOOGLE_TTS_URL
MISTRAL_TTS_URL = settings.MISTRAL_TTS_URL
MISTRAL_VOICES_URL = settings.MISTRAL_VOICES_URL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tts"])

GOOGLE_TTS_VOICES = [
    # Deutsche Stimmen
    {"name": "de-DE-Neural2-B", "lang": "de-DE", "gender": "male",   "label": "Markus (Neural2)"},
    {"name": "de-DE-Neural2-D", "lang": "de-DE", "gender": "male",   "label": "Lukas (Neural2)"},
    {"name": "de-DE-Neural2-A", "lang": "de-DE", "gender": "female", "label": "Anna (Neural2)"},
    {"name": "de-DE-Neural2-C", "lang": "de-DE", "gender": "female", "label": "Clara (Neural2)"},
    {"name": "de-DE-Wavenet-B", "lang": "de-DE", "gender": "male",   "label": "Markus (Wavenet)"},
    {"name": "de-DE-Wavenet-D", "lang": "de-DE", "gender": "male",   "label": "Lukas (Wavenet)"},
    {"name": "de-DE-Wavenet-A", "lang": "de-DE", "gender": "female", "label": "Anna (Wavenet)"},
    # Englische Stimmen
    {"name": "en-US-Neural2-A", "lang": "en-US", "gender": "male",   "label": "James (Neural2)"},
    {"name": "en-US-Neural2-D", "lang": "en-US", "gender": "male",   "label": "Ryan (Neural2)"},
    {"name": "en-US-Neural2-C", "lang": "en-US", "gender": "female", "label": "Emma (Neural2)"},
    {"name": "en-GB-Neural2-B", "lang": "en-GB", "gender": "male",   "label": "Oliver (Neural2)"},
    {"name": "en-GB-Neural2-A", "lang": "en-GB", "gender": "female", "label": "Sophie (Neural2)"},
]


class TTSRequest(BaseModel):
    text: str
    voice: str = ""


@router.post("/tts")
async def tts(body: TTSRequest):
    text = body.text.strip()
    voice = body.voice

    if not text:
        raise HTTPException(status_code=400, detail="Kein Text")

    # macOS oder leere Stimme → kein Audio nötig
    if not voice or voice.startswith("mac:") or voice in ("voxtral", "en_paul_neutral", "neutral_male", ""):
        return Response(status_code=204)

    # ── Google Cloud TTS ───────────────────────────────────────────────────────
    if voice.startswith("google:"):
        voice_name = voice[len("google:"):]
        lang_code = "-".join(voice_name.split("-")[:2])
        google_key = load_providers().get("google_api", {}).get("api_key", "")
        if not google_key:
            raise HTTPException(status_code=500, detail="Google API Key nicht gesetzt")

        payload = {
            "input": {"text": text},
            "voice": {"languageCode": lang_code, "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3"},
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{GOOGLE_TTS_URL}?key={google_key}",
                    json=payload,
                )
            response.raise_for_status()
            audio_b64 = response.json().get("audioContent", "")
            audio_bytes = base64.b64decode(audio_b64)
            return Response(content=audio_bytes, media_type="audio/mpeg")
        except httpx.HTTPStatusError as e:
            logger.warning("Google TTS Fehler %d", e.response.status_code)
            raise HTTPException(status_code=e.response.status_code, detail=f"Google TTS Fehler {e.response.status_code}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Mistral Voxtral TTS ────────────────────────────────────────────────────
    mistral_key = load_providers().get("mistral", {}).get("api_key", "")
    if not mistral_key:
        raise HTTPException(status_code=500, detail="Mistral API Key nicht gesetzt. Bitte in den Einstellungen eintragen.")

    payload = {
        "model": "voxtral-mini-tts-latest",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                MISTRAL_TTS_URL,
                headers={"Authorization": f"Bearer {mistral_key}"},
                json=payload,
            )
        response.raise_for_status()
        audio_b64 = response.json().get("audio_data", "")
        audio_bytes = base64.b64decode(audio_b64)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except httpx.HTTPStatusError as e:
        logger.warning("TTS API Fehler %d", e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=f"TTS API Fehler {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voices/google")
async def google_voices():
    google_key = load_providers().get("google_api", {}).get("api_key", "")
    if not google_key:
        return {"voices": [], "available": False}
    return {"voices": GOOGLE_TTS_VOICES, "available": True}


@router.get("/voices/mistral")
async def mistral_voices():
    mistral_key = load_providers().get("mistral", {}).get("api_key", "")
    if not mistral_key:
        return {"voices": []}
    try:
        seen: set = set()
        voices = []
        page = 1
        prev_seen_count = -1

        async with httpx.AsyncClient(timeout=8.0) as client:
            while page <= 5:
                resp = await client.get(
                    f"{MISTRAL_VOICES_URL}?page_size=30&page={page}",
                    headers={"Authorization": f"Bearer {mistral_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break
                for v in items:
                    if v["slug"] not in seen:
                        seen.add(v["slug"])
                        lang_raw = v["languages"][0] if v["languages"] else "en"
                        lang_label = {
                            "en_us": "EN-US", "en_gb": "EN-GB", "de_de": "DE",
                            "fr_fr": "FR", "es_es": "ES", "it_it": "IT",
                        }.get(lang_raw, lang_raw.upper())
                        voices.append({
                            "slug": v["slug"],
                            "name": v["name"],
                            "lang": lang_raw,
                            "lang_label": lang_label,
                            "gender": v.get("gender", ""),
                            "tags": v.get("tags", []),
                        })
                if len(seen) == prev_seen_count:
                    break
                prev_seen_count = len(seen)
                total_pages = data.get("total_pages", 1)
                if page >= total_pages:
                    break
                page += 1

        return {"voices": voices}
    except Exception as e:
        return {"voices": [], "error": str(e)}
