"""
routes/tts.py — Text-to-Speech Endpunkte (Mistral Voxtral, Google Cloud TTS).
"""
import base64
import io

import requests
from flask import Blueprint, jsonify, request, send_file

from core.config import GOOGLE_TTS_URL, MISTRAL_TTS_URL, MISTRAL_VOICES_URL
from storage.providers import load_providers

bp = Blueprint("tts", __name__)

GOOGLE_TTS_VOICES = [
    # Deutsche Stimmen
    {"name": "de-DE-Neural2-B", "lang": "de-DE", "gender": "male",  "label": "Markus (Neural2)"},
    {"name": "de-DE-Neural2-D", "lang": "de-DE", "gender": "male",  "label": "Lukas (Neural2)"},
    {"name": "de-DE-Neural2-A", "lang": "de-DE", "gender": "female","label": "Anna (Neural2)"},
    {"name": "de-DE-Neural2-C", "lang": "de-DE", "gender": "female","label": "Clara (Neural2)"},
    {"name": "de-DE-Wavenet-B", "lang": "de-DE", "gender": "male",  "label": "Markus (Wavenet)"},
    {"name": "de-DE-Wavenet-D", "lang": "de-DE", "gender": "male",  "label": "Lukas (Wavenet)"},
    {"name": "de-DE-Wavenet-A", "lang": "de-DE", "gender": "female","label": "Anna (Wavenet)"},
    # Englische Stimmen
    {"name": "en-US-Neural2-A", "lang": "en-US", "gender": "male",  "label": "James (Neural2)"},
    {"name": "en-US-Neural2-D", "lang": "en-US", "gender": "male",  "label": "Ryan (Neural2)"},
    {"name": "en-US-Neural2-C", "lang": "en-US", "gender": "female","label": "Emma (Neural2)"},
    {"name": "en-GB-Neural2-B", "lang": "en-GB", "gender": "male",  "label": "Oliver (Neural2)"},
    {"name": "en-GB-Neural2-A", "lang": "en-GB", "gender": "female","label": "Sophie (Neural2)"},
]


@bp.route("/api/tts", methods=["POST"])
def tts():
    data = request.json
    text = data.get("text", "").strip()
    voice = data.get("voice", "")

    if not text:
        return jsonify({"error": "Kein Text"}), 400

    if not voice or voice.startswith("mac:") or voice in ("voxtral", "en_paul_neutral", "neutral_male", ""):
        return "", 204

    # ── Google Cloud TTS ───────────────────────────────────────────────────────
    if voice.startswith("google:"):
        voice_name = voice[len("google:"):]
        lang_code = "-".join(voice_name.split("-")[:2])
        google_key = load_providers().get("google_api", {}).get("api_key", "")
        if not google_key:
            return jsonify({"error": "Google API Key nicht gesetzt"}), 500
        payload = {
            "input": {"text": text},
            "voice": {"languageCode": lang_code, "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3"},
        }
        try:
            response = requests.post(
                f"{GOOGLE_TTS_URL}?key={google_key}",
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            audio_b64 = response.json().get("audioContent", "")
            audio_bytes = base64.b64decode(audio_b64)
            return send_file(
                io.BytesIO(audio_bytes),
                mimetype="audio/mpeg",
                as_attachment=False,
                download_name="speech.mp3",
            )
        except requests.exceptions.HTTPError:
            try:
                err_body = response.json()
            except Exception:
                err_body = response.text
            print(f"[TTS/Google] Fehler {response.status_code}: {err_body}", flush=True)
            return jsonify({"error": f"Google TTS Fehler {response.status_code}"}), response.status_code
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Mistral Voxtral TTS ────────────────────────────────────────────────────
    mistral_key = load_providers().get("mistral", {}).get("api_key", "")
    if not mistral_key:
        return jsonify({"error": "Mistral API Key nicht gesetzt. Bitte in den Einstellungen eintragen."}), 500

    headers = {
        "Authorization": f"Bearer {mistral_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "voxtral-mini-tts-latest",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }

    try:
        response = requests.post(MISTRAL_TTS_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        audio_b64 = result.get("audio_data", "")
        audio_bytes = base64.b64decode(audio_b64)
        return send_file(
            io.BytesIO(audio_bytes),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="speech.mp3",
        )
    except requests.exceptions.HTTPError:
        try:
            err_body = response.json()
        except Exception:
            err_body = response.text
        print(f"[TTS] API Fehler {response.status_code}: {err_body}", flush=True)
        return jsonify({"error": f"TTS API Fehler {response.status_code}"}), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/voices/google", methods=["GET"])
def google_voices():
    google_key = load_providers().get("google_api", {}).get("api_key", "")
    if not google_key:
        return jsonify({"voices": [], "available": False})
    return jsonify({"voices": GOOGLE_TTS_VOICES, "available": True})


@bp.route("/api/voices/mistral", methods=["GET"])
def mistral_voices():
    mistral_key = load_providers().get("mistral", {}).get("api_key", "")
    if not mistral_key:
        return jsonify({"voices": []})
    try:
        seen = set()
        voices = []
        prev_seen_count = -1
        page = 1
        while page <= 5:
            resp = requests.get(
                f"{MISTRAL_VOICES_URL}?page_size=30&page={page}",
                headers={"Authorization": f"Bearer {mistral_key}"},
                timeout=8,
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
        return jsonify({"voices": voices})
    except Exception as e:
        return jsonify({"voices": [], "error": str(e)})
