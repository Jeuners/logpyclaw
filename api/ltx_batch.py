"""
api/ltx_batch.py — LTX 2.3 Batch Renderer API.
Nimmt WAV + Bild entgegen, teilt WAV in 9s-Blöcke auf,
generiert per Ollama Prompts und rendert sequenziell via ComfyUI.
"""
import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
import wave as wave_mod
from typing import AsyncGenerator

import requests
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/ltx-batch", tags=["ltx-batch"])

COMFYUI_URL = "http://192.168.4.15:8000"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL_DEFAULT = "gemma4:e4b"
CHUNK_SEC_DEFAULT = 9.0

# job_id → asyncio.Queue[dict | None]  (None = sentinel)
_jobs: dict[str, asyncio.Queue] = {}
# job_id → {start_image_fn, segments: [{idx, duration, prompt, audio_fn}], concept, ollama_model}
# Hält den State zwischen /prepare und /render.
_prep_jobs: dict[str, dict] = {}
_results_dir = os.path.join(os.path.dirname(__file__), "..", "data", "ltx_batch")
os.makedirs(_results_dir, exist_ok=True)

_MIME_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif",
}


def _job_dir(job_id: str) -> str:
    d = os.path.join(_results_dir, job_id)
    os.makedirs(d, exist_ok=True)
    return d


# ── State-Persistenz auf Disk (überlebt Server-Restart) ────────────────────────

_STATE_FIELDS = (
    "transcript", "image_desc", "concept", "ollama_model",
    "chunk_sec_used", "source_image_mime", "source_wav_path",
    "source_image_path", "start_image_fn", "total",
)


def _state_json_path(job_id: str) -> str:
    return os.path.join(_job_dir(job_id), "state.json")


def _save_state(job_id: str):
    """Persistiert _prep_jobs[job_id] minimal auf Disk. Best-effort."""
    job = _prep_jobs.get(job_id)
    if not job:
        return
    try:
        out = {k: job.get(k) for k in _STATE_FIELDS if k in job}
        # Segments serialisieren — ohne ephemere Felder
        out["segments"] = [
            {k: v for k, v in s.items() if k in (
                "idx", "segment", "duration", "prompt", "audio_path",
                "image_mode", "custom_fn", "last_frame_fn", "video_url",
                "prompt_locked", "prompt_refined",
            )}
            for s in job.get("segments", [])
        ]
        with open(_state_json_path(job_id), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ltx_batch] _save_state({job_id[:8]}) Fehler: {e}", flush=True)


def _load_state_or_reconstruct(job_id: str) -> dict | None:
    """Lazy-Restore von _prep_jobs[job_id] aus state.json oder rekonstruiert
    aus Source-Files (source.wav + chunks). Gibt None zurück wenn nichts auffindbar."""
    if job_id in _prep_jobs:
        return _prep_jobs[job_id]
    jdir = os.path.join(_results_dir, job_id)
    if not os.path.isdir(jdir):
        return None

    # Bevorzugt: state.json
    sjp = os.path.join(jdir, "state.json")
    if os.path.exists(sjp):
        try:
            with open(sjp, encoding="utf-8") as f:
                state = json.load(f)
            state["status"] = "ready"
            state["error"] = None
            _prep_jobs[job_id] = state
            print(f"[ltx_batch] State von Disk restored: {job_id[:8]} ({len(state.get('segments',[]))} Segmente)", flush=True)
            return state
        except Exception as e:
            print(f"[ltx_batch] state.json laden fehlgeschlagen: {e}", flush=True)

    # Fallback: Rekonstruktion aus Source-Files
    src_wav = os.path.join(jdir, "source.wav")
    src_img = None
    src_mime = "image/png"
    for ext, mime in ((".png", "image/png"), (".jpg", "image/jpeg"),
                      (".webp", "image/webp"), (".gif", "image/gif")):
        p = os.path.join(jdir, f"source{ext}")
        if os.path.exists(p):
            src_img = p
            src_mime = mime
            break
    chunks_dir = os.path.join(jdir, "chunks")
    if not (os.path.exists(src_wav) and src_img and os.path.isdir(chunks_dir)):
        return None

    chunk_files = sorted(
        [f for f in os.listdir(chunks_dir) if f.startswith("seg_") and f.endswith(".wav")],
        key=lambda n: int(n.replace("seg_", "").replace(".wav", "")),
    )
    segments = []
    for i, fn in enumerate(chunk_files):
        path = os.path.join(chunks_dir, fn)
        try:
            with wave_mod.open(path, "rb") as wf:
                dur = wf.getnframes() / wf.getframerate()
        except Exception:
            dur = 9.0
        segments.append({
            "idx": i, "segment": i + 1, "duration": round(dur, 2),
            "prompt": "", "audio_path": path,
            "image_mode": "start" if i == 0 else "prev",
            "custom_fn": None,
        })

    state = {
        "status": "ready", "error": None, "progress_msg": "",
        "total": len(segments),
        "transcript": "", "image_desc": "",
        "concept": "", "ollama_model": OLLAMA_MODEL_DEFAULT,
        "chunk_sec_used": segments[0]["duration"] if segments else CHUNK_SEC_DEFAULT,
        "source_wav_path": src_wav,
        "source_image_path": src_img,
        "source_image_mime": src_mime,
        "start_image_fn": None,
        "segments": segments,
    }
    _prep_jobs[job_id] = state
    print(f"[ltx_batch] State rekonstruiert aus Disk: {job_id[:8]} ({len(segments)} Segmente, ohne Prompts)", flush=True)
    return state


def _ensure_start_image_uploaded(job: dict) -> tuple[bool, str]:
    """Stellt sicher dass job['start_image_fn'] auf einer aktuellen ComfyUI-Datei zeigt.
    Lädt bei Bedarf aus pending-bytes oder Source-File hoch. Gibt (ok, error) zurück."""
    if job.get("start_image_fn"):
        return True, ""
    pending_bytes = job.get("_pending_image_bytes")
    pending_mime = job.get("_pending_image_mime", "image/png")
    if not pending_bytes:
        src_path = job.get("source_image_path")
        if src_path and os.path.exists(src_path):
            try:
                with open(src_path, "rb") as f:
                    pending_bytes = f.read()
                pending_mime = job.get("source_image_mime", pending_mime)
            except Exception as e:
                return False, f"Source-Bild lesen: {e}"
    if not pending_bytes:
        return False, "Kein Start-Bild vorhanden — bitte /prepare erneut"
    try:
        fn = _upload_image(pending_bytes, pending_mime)
        job["start_image_fn"] = fn
        job.pop("_pending_image_bytes", None)
        job.pop("_pending_image_mime", None)
        return True, ""
    except Exception as e:
        return False, f"ComfyUI nicht erreichbar ({type(e).__name__}: {e})"


# ── helpers ────────────────────────────────────────────────────────────────────

def _split_wav(wav_path: str, chunk_sec: float) -> list[str]:
    """Split WAV file into chunks of chunk_sec seconds. Returns list of tmp paths."""
    paths = []
    with wave_mod.open(wav_path, "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        total_frames = wf.getnframes()
        chunk_frames = int(rate * chunk_sec)
        idx = 0
        while True:
            frames = wf.readframes(chunk_frames)
            if not frames:
                break
            fd, path = tempfile.mkstemp(suffix=f"_chunk{idx}.wav", prefix="ltxbatch_")
            with wave_mod.open(os.fdopen(fd, "wb"), "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(sampwidth)
                out.setframerate(rate)
                out.writeframes(frames)
            actual_sec = min(chunk_sec, (total_frames - idx * chunk_frames) / rate)
            paths.append((path, actual_sec))
            idx += 1
            if idx * chunk_frames >= total_frames:
                break
    return paths


def _ollama_chat(
    messages: list[dict],
    max_tokens: int = 400,
    model: str = OLLAMA_MODEL_DEFAULT,
    timeout: int = 900,
    json_mode: bool = False,
) -> str:
    """Ollama /api/chat call. Default-Timeout 15 Min — große Batches (42 Prompts × 90 Tokens) brauchen das.

    ``json_mode=True`` setzt ``format=json`` — Ollama erzwingt valides JSON.
    Notwendig für gemma4:e4b und andere kleine Modelle, die sonst kein JSON-Schema halten.
    """
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if json_mode:
        body["format"] = "json"
    resp = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "").strip()


def _transcribe_wav(wav_path: str) -> str:
    """WAV → Text via whisper-cli (whisper.cpp). Leer bei Fehler."""
    try:
        from skills.transcription_skill import _transcribe_audio_whisper
        txt = _transcribe_audio_whisper(wav_path)
        if txt and not txt.startswith("❌") and not txt.startswith("⚠"):
            return txt.strip()
        print(f"[ltx_batch] Transkription fehlgeschlagen/leer: {txt[:120]}", flush=True)
    except Exception as e:
        print(f"[ltx_batch] Transkription-Exception: {e}", flush=True)
    return ""


def _describe_image(image_bytes: bytes, preferred_model: str = OLLAMA_MODEL_DEFAULT) -> str:
    """Bild-Beschreibung via Vision-Modell. Bevorzugt moondream (klein+schnell+zuverlässig).
    gemma4:e4b ist zu langsam für Vision-Calls."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        available = {m["name"] for m in r.json().get("models", [])}
    except Exception as e:
        print(f"[ltx_batch] Ollama tags unreachable: {e}", flush=True)
        return ""
    # Reihenfolge: moondream zuerst (schnell), dann größere Modelle
    vision_candidates = ["moondream:latest", "llava:latest", "llama3.2-vision:latest",
                         "gemma3:latest", "gemma3:4b"]
    vision_model = next((m for m in vision_candidates if m in available), None)
    if not vision_model:
        print(f"[ltx_batch] Kein Vision-Modell in Ollama gefunden ({vision_candidates}) — verfügbar: {sorted(available)[:8]}", flush=True)
        return ""
    # Bild auf max. 512px verkleinern — Vision braucht keine höhere Auflösung
    import base64, io
    try:
        from PIL import Image as _PIL
        img = _PIL.open(io.BytesIO(image_bytes))
        img.thumbnail((512, 512), _PIL.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        image_bytes = buf.getvalue()
    except Exception as e:
        print(f"[ltx_batch] Bild-Resize fehlgeschlagen: {e}", flush=True)

    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    print(f"[ltx_batch] Vision-Call mit {vision_model}, Bildgröße {len(image_bytes)//1024}KB...", flush=True)
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": vision_model,
                "messages": [{
                    "role": "user",
                    "content": "Describe this image in 2 sentences for a video-generation prompt: who/what is in the scene, setting, mood, colors, style. English only.",
                    "images": [img_b64],
                }],
                "stream": False,
                "options": {"num_predict": 120},
            },
            timeout=120,
        )
        resp.raise_for_status()
        result = (resp.json().get("message", {}).get("content", "") or "").strip()
        print(f"[ltx_batch] Vision-Ergebnis: {result[:150]}", flush=True)
        return result
    except Exception as e:
        print(f"[ltx_batch] Vision-Call ({vision_model}) failed: {e}", flush=True)
        return ""


def _split_transcript_per_chunk(transcript: str, n_segments: int) -> list[str]:
    """Teilt das volle Transkript proportional auf n Segmente auf (wort-basiert).

    Naiv aber robust: wir teilen nach Wortanzahl, nicht nach Timestamps. Whisper
    liefert kein Timing pro Wort hier; eine zeit-genaue Aufteilung würde eine
    Forced-Alignment-Stage brauchen. Für die Prompt-Verfeinerung reicht „grob
    welche Worte gehören zu Segment k".
    """
    if not transcript or n_segments <= 0:
        return [""] * max(0, n_segments)
    words = transcript.split()
    if not words:
        return [""] * n_segments
    n = max(1, n_segments)
    per = max(1, len(words) // n)
    chunks: list[str] = []
    for i in range(n):
        start = i * per
        end = (i + 1) * per if i < n - 1 else len(words)
        chunks.append(" ".join(words[start:end]).strip())
    return chunks


def _fetch_comfyui_input_bytes(image_fn: str, timeout: int = 30) -> bytes | None:
    """Holt ein Input-Bild aus ComfyUI (Last-Frames + Start-Bild liegen dort).

    None bei Fehler — Refinement fällt dann auf den Original-Prompt zurück.
    """
    if not image_fn:
        return None
    try:
        r = requests.get(
            f"{COMFYUI_URL}/view",
            params={"filename": image_fn, "type": "input"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"[ltx_batch] _fetch_comfyui_input_bytes({image_fn}) Fehler: {e}", flush=True)
        return None


def _refine_prompt_for_segment(
    image_fn: str | None,
    original_prompt: str,
    chunk_text: str,
    concept: str,
    segment_index: int,
    n_segments: int,
    current_image_mode: str,
    has_prev_last_frame: bool,
    start_image_fn: str | None,
    ollama_model: str = OLLAMA_MODEL_DEFAULT,
) -> dict:
    """Just-in-Time Prompt-Refinement vor jedem Segment.

    Schritte:
    1. Echtes Eingangsbild aus ComfyUI holen → Vision-Beschreibung
    2. Ollama-Call mit echtem Bild-Wissen + Audio-Chunk → JSON mit refined
       prompt + image_decision + reason

    Returns:
        {
          "refined_prompt":      str,                         # leer = kein Refinement
          "suggested_image_mode": "prev" | "start" | None,    # None = kein Switch-Vorschlag
          "image_desc":           str,                         # Vision-Output (für Logging/UI)
          "reason":               str,
        }

    Niemals raisen — Fehler → leeres Refinement (Caller fällt auf Original zurück).
    """
    out = {
        "refined_prompt": "",
        "suggested_image_mode": None,
        "image_desc": "",
        "reason": "",
    }
    img_bytes = _fetch_comfyui_input_bytes(image_fn) if image_fn else None
    if not img_bytes:
        out["reason"] = "input image not retrievable from ComfyUI"
        return out

    # 1) Vision-Beschreibung des echten Eingangsbilds
    try:
        actual_desc = _describe_image(img_bytes, preferred_model=ollama_model)
    except Exception as e:
        actual_desc = ""
        print(f"[ltx_batch] refine: vision call failed: {e}", flush=True)
    out["image_desc"] = actual_desc
    if not actual_desc:
        out["reason"] = "vision describe returned empty — keeping original prompt"
        return out

    # 2) Ollama-JSON-Call: Refine + Frame-Wahl
    can_switch_to_start = bool(start_image_fn) and current_image_mode != "start"
    seg_num = segment_index + 1
    transition_hint = (
        f"This is segment {seg_num}/{n_segments}. "
        + ("It chains from the previous segment's last frame (mode='prev'). "
           if current_image_mode == "prev" and has_prev_last_frame else "")
        + ("If the input frame is broken (heavy motion blur, partial face, "
           "scene cut artifact, or character drift away from the original), "
           "you may suggest mode='start' to recover continuity. "
           if can_switch_to_start else "")
    )
    user = (
        f"Video concept: {concept or '(none)'}\n\n"
        f"Spoken audio for THIS segment ({seg_num}/{n_segments}):\n"
        f"\"\"\"\n{chunk_text or '(silent)'}\n\"\"\"\n\n"
        f"Previous prompt draft (may be wrong because it was written without "
        f"seeing the actual input frame):\n\"\"\"\n{original_prompt or '(empty)'}\n\"\"\"\n\n"
        f"Actual input frame for this segment (vision):\n{actual_desc}\n\n"
        f"{transition_hint}\n\n"
        "Return STRICT JSON, nothing else:\n"
        "{\n"
        '  "prompt": "<25-50 English words, ONE concrete moment, grounded in the actual input frame, fitting THIS segment\'s spoken words>",\n'
        '  "image_mode": "prev" | "start",\n'
        '  "reason": "<one short sentence why you kept or switched the frame>"\n'
        "}\n"
    )
    try:
        raw = _ollama_chat(
            [{"role": "user", "content": user}],
            max_tokens=350,
            model=ollama_model,
            timeout=180,
            json_mode=True,
        )
    except Exception as e:
        out["reason"] = f"refine LLM call failed: {e}"
        return out

    # JSON-Block extrahieren — toleriert führendes Geschwafel
    parsed = _try_extract_json(raw)
    if not parsed or not isinstance(parsed, dict):
        out["reason"] = "refine LLM returned non-JSON — keeping original prompt"
        print(f"[ltx_batch] refine: non-JSON output: {raw[:200]}", flush=True)
        return out

    refined = (parsed.get("prompt") or "").strip()
    if refined and len(refined) > 15:
        out["refined_prompt"] = refined
    mode = parsed.get("image_mode")
    if mode in ("prev", "start") and mode != current_image_mode:
        # Nur als Vorschlag akzeptieren, wenn auch wirklich umsetzbar
        if mode == "start" and not start_image_fn:
            mode = None
        if mode == "prev" and not has_prev_last_frame:
            mode = None
        out["suggested_image_mode"] = mode
    out["reason"] = (parsed.get("reason") or "").strip()[:300]
    return out


def _try_extract_json(s: str) -> dict | None:
    """Robuster JSON-Extractor — toleriert ```json...``` Fences und Vor-/Nachtext."""
    if not s:
        return None
    s = s.strip()
    # Code-Fence entfernen
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    # Direkter Versuch
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: erste { ... letzte }
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b != -1 and b > a:
        try:
            return json.loads(s[a:b + 1])
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _generate_prompts(
    concept: str,
    n_segments: int,
    ollama_model: str = OLLAMA_MODEL_DEFAULT,
    transcript: str = "",
    image_desc: str = "",
    chunk_sec: float = CHUNK_SEC_DEFAULT,
) -> list[str]:
    """
    Generate n_segments distinct visual prompts via Ollama — one call.
    Nutzt Transkript + Bild-Beschreibung als Kontext, damit der Agent wirklich weiß
    was gesprochen wird und was auf dem Startbild zu sehen ist.
    """
    parts = [f"Video concept: {concept}" if concept else ""]
    if image_desc:
        parts.append(f"Starting frame (what the viewer sees at t=0):\n{image_desc}")
    if transcript:
        parts.append(
            f"Spoken audio (full transcript covering ~{int(n_segments*chunk_sec)}s, "
            f"split into {n_segments} segments of ~{chunk_sec:.0f}s each):\n\"\"\"\n{transcript}\n\"\"\""
        )
    context = "\n\n".join(p for p in parts if p)

    user = (
        f"{context}\n\n"
        f"Task: Write {n_segments} SHORT English video prompts, one per line — one per segment, "
        f"in chronological order.\n"
        f"Each prompt must:\n"
        f"- Be visually grounded in the starting frame (same character/setting/style) but introduce action or motion that fits THIS segment's spoken words\n"
        f"- Describe ONE concrete moment (who, action, camera, mood) — 25-50 English words\n"
        f"- Flow naturally from the previous segment (chain of motion, not scene cuts unless the audio demands it)\n"
        f"- No numbering, no labels, no quotes, no explanations\n"
        f"- Exactly {n_segments} lines, nothing else\n\n"
        f"Output the {n_segments} prompts now:"
    )
    raw = _ollama_chat([
        {"role": "user", "content": user},
    ], max_tokens=n_segments * 90, model=ollama_model)

    lines = [l.strip(" -•*0123456789.:)\"'") for l in raw.splitlines() if l.strip()]
    lines = [l for l in lines if len(l) > 15]

    while len(lines) < n_segments:
        lines.append(f"Scene {len(lines)+1}: {concept[:100] or image_desc[:100]}")
    return lines[:n_segments]


def _upload_image(image_bytes: bytes, mime: str) -> str:
    ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
    filename = f"ltxbatch_img_{uuid.uuid4().hex[:8]}{ext}"
    files = {"image": (filename, image_bytes, mime)}
    resp = requests.post(f"{COMFYUI_URL}/upload/image", files=files, timeout=30)
    resp.raise_for_status()
    return filename


def _upload_audio(wav_path: str) -> str:
    filename = f"ltxbatch_audio_{uuid.uuid4().hex[:8]}.wav"
    with open(wav_path, "rb") as f:
        files = {"image": (filename, f.read(), "audio/wav")}
    resp = requests.post(f"{COMFYUI_URL}/upload/image", files=files, timeout=60)
    resp.raise_for_status()
    return filename


def _build_workflow(image_fn: str, audio_fn: str, prompt: str, duration: float, seed: int) -> dict:
    template_path = os.path.join(os.path.dirname(__file__), "..", "skills", "workflows", "ltx_ia2v.json")
    with open(template_path, encoding="utf-8") as f:
        wf = json.load(f)
    wf["269"]["inputs"]["image"] = image_fn
    wf["276"]["inputs"]["audio"] = audio_fn
    wf["276"]["inputs"].pop("audioUI", None)
    wf["340:319"]["inputs"]["value"] = prompt
    wf["340:331"]["inputs"]["value"] = max(1.0, min(9.0, float(duration)))
    wf["340:286"]["inputs"]["noise_seed"] = int(seed)
    return wf


def _poll(prompt_id: str, timeout: int = 3600) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        h = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
        data = h.json()
        entry = data.get(prompt_id, {})
        status = entry.get("status", {})
        if status.get("completed"):
            return entry.get("outputs", {})
        if status.get("status_str") == "error":
            raise RuntimeError(f"ComfyUI Fehler: {status.get('messages', [])}")
    return None


def _get_video_info(outputs: dict) -> dict | None:
    for node_out in outputs.values():
        for key in ("videos", "gifs", "images"):
            items = node_out.get(key, [])
            if items:
                return items[0]
    return None


def _submit_workflow(wf: dict, client_id: str) -> str:
    """POST /prompt → prompt_id. Bei 4xx den ComfyUI-Body in die Exception packen
    (r.raise_for_status() verschluckt sonst Node-Errors / fehlende Modelle)."""
    r = requests.post(
        f"{COMFYUI_URL}/prompt",
        json={"prompt": wf, "client_id": client_id},
        timeout=30,
    )
    if r.status_code >= 400:
        try:
            data = r.json()
            err = data.get("error") or {}
            node_errs = data.get("node_errors") or {}
            parts = []
            if err.get("message"):
                parts.append(f"{err.get('type','error')}: {err['message']}")
                if err.get("details"):
                    parts.append(str(err["details"])[:300])
            for node_id, ne in node_errs.items():
                ct = ne.get("class_type", "?")
                for e in ne.get("errors", []):
                    parts.append(f"[Node {node_id} {ct}] {e.get('type')}: {e.get('message')} ({e.get('details','')})")
            msg = " | ".join(parts) or r.text[:400]
        except Exception:
            msg = r.text[:400]
        raise RuntimeError(f"ComfyUI {r.status_code}: {msg}")
    pid = r.json().get("prompt_id")
    if not pid:
        raise RuntimeError(f"ComfyUI ohne prompt_id: {r.text[:200]}")
    return pid


def _video_info_to_url(info: dict) -> str:
    fn = info["filename"]
    subfolder = info.get("subfolder", "")
    ftype = info.get("type", "output")
    params = f"filename={fn}&type={ftype}"
    if subfolder:
        params += f"&subfolder={subfolder}"
    return f"{COMFYUI_URL}/view?{params}"


def _download_video_bytes(info: dict) -> bytes:
    url = _video_info_to_url(info)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def _extract_last_frame(video_bytes: bytes) -> tuple[bytes | None, str]:
    """Letztes Frame aus MP4-Bytes via ffmpeg extrahieren.

    Gibt (PNG-Bytes | None, stderr_excerpt) zurück.

    Strategie: `-update 1` überschreibt die Output-Datei mit JEDEM Frame,
    dekodiert das ganze Video durch → am Ende steht das echte letzte Frame
    in der Datei. Robust bei kurzen Videos (< 1s) wo `-sseof` versagt.
    """
    import subprocess
    fd_in, path_in = tempfile.mkstemp(suffix=".mp4")
    fd_out, path_out = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd_in, "wb") as f:
            f.write(video_bytes)
        os.close(fd_out)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", path_in,
             "-update", "1", "-frames:v", "1",
             "-vf", "select='eq(n,last_n)'",
             "-q:v", "2", path_out],
            capture_output=True,
        )
        # Fallback ohne select-Filter (ältere ffmpeg) — -update 1 alleine
        # schreibt trotzdem jedes Frame und überschreibt → letztes bleibt.
        if result.returncode != 0 or not os.path.getsize(path_out):
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", path_in,
                 "-update", "1", "-q:v", "2", path_out],
                capture_output=True,
            )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", "replace")[-300:]
            return None, stderr
        if not os.path.getsize(path_out):
            return None, "Output-PNG leer"
        with open(path_out, "rb") as f:
            return f.read(), ""
    except FileNotFoundError:
        return None, "ffmpeg nicht installiert (brew install ffmpeg)"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        try: os.unlink(path_in)
        except Exception: pass
        try: os.unlink(path_out)
        except Exception: pass


# ── background job ─────────────────────────────────────────────────────────────

def _job_log_path(job_id: str) -> str:
    return os.path.join(_results_dir, f"{job_id}.jsonl")


def _persist_event(job_id: str, payload: dict):
    """Event sofort auf Disk schreiben (append JSONL)."""
    try:
        with open(_job_log_path(job_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _replay_events(job_id: str) -> list[dict]:
    """Alle gespeicherten Events für job_id laden."""
    path = _job_log_path(job_id)
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
    return events


def _run_batch(
    job_id: str,
    segments: list[dict],
    start_image_fn: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
):
    """
    segments: [{idx, duration, prompt, audio_path, image_mode: 'start'|'prev'|'custom', custom_fn?}]
    start_image_fn: ComfyUI-Filename des Start-Bilds (schon hochgeladen).
    """
    def send(event: str, **data):
        payload = {"event": event, **data}
        _persist_event(job_id, payload)
        asyncio.run_coroutine_threadsafe(queue.put(payload), loop)

    try:
        n = len(segments)
        send("status", msg=f"Start: {n} Segmente rendern")
        # Falls ein Segment im Re-Render schon last_frame_fn gesetzt hat, hier reset:
        for s in segments:
            s.pop("last_frame_fn", None)
        prev_last_frame_fn: str | None = None

        # Transkript pro Segment vorbereiten — fürs Just-in-Time Refinement
        parent = _prep_jobs.get(job_id) or {}
        transcript = parent.get("transcript", "") or ""
        concept = parent.get("concept", "") or ""
        ollama_model = parent.get("ollama_model", OLLAMA_MODEL_DEFAULT) or OLLAMA_MODEL_DEFAULT
        chunk_texts = _split_transcript_per_chunk(transcript, n)

        for i, seg in enumerate(segments):
            seg_num = i + 1
            duration = float(seg.get("duration", 9.0))
            prompt_text = seg.get("prompt", "")
            audio_path = seg.get("audio_path")
            mode = seg.get("image_mode", "prev")

            # Image-Source bestimmen
            if mode == "custom" and seg.get("custom_fn"):
                image_fn = seg["custom_fn"]
                src_hint = f"[🖼 Custom-Bild]"
            elif mode == "start":
                image_fn = start_image_fn
                src_hint = f"[🏁 Start-Bild]"
            else:  # 'prev'
                if prev_last_frame_fn:
                    image_fn = prev_last_frame_fn
                    src_hint = f"[🔗 aus Seg. {i} Last-Frame]"
                else:
                    image_fn = start_image_fn
                    src_hint = f"[🏁 Start-Bild (kein prev)]"

            # ── Just-in-Time Prompt-Refinement (§ vor jedem Segment k≥1) ─────────
            # Vor dem Render: tatsächliches Eingangsbild ansehen, prompt verfeinern
            # und ggf. image_mode wechseln, wenn das Last-Frame kaputt aussieht.
            # Übersprungen bei Segment 0 (kein prev), bei locked-Prompts und bei
            # custom-Modus (User hat Bild explizit gewählt → nicht überschreiben).
            should_refine = (
                i >= 1
                and not seg.get("prompt_locked", False)
                and mode != "custom"
            )
            if should_refine:
                send("status", msg=f"🔍 Segment {seg_num}/{n}: Eingangsbild ansehen + Prompt verfeinern...")
                refine = _refine_prompt_for_segment(
                    image_fn=image_fn,
                    original_prompt=prompt_text,
                    chunk_text=chunk_texts[i] if i < len(chunk_texts) else "",
                    concept=concept,
                    segment_index=i,
                    n_segments=n,
                    current_image_mode=mode,
                    has_prev_last_frame=bool(prev_last_frame_fn),
                    start_image_fn=start_image_fn,
                    ollama_model=ollama_model,
                )
                # Image-Switch?
                suggested = refine.get("suggested_image_mode")
                if suggested == "start" and start_image_fn:
                    image_fn = start_image_fn
                    mode = "start"
                    seg["image_mode"] = "start"
                    src_hint = f"[🏁 Start-Bild — auto-switched]"
                elif suggested == "prev" and prev_last_frame_fn:
                    image_fn = prev_last_frame_fn
                    mode = "prev"
                    seg["image_mode"] = "prev"
                    src_hint = f"[🔗 aus Seg. {i} Last-Frame — auto-switched]"
                # Prompt-Refresh?
                refined_prompt = refine.get("refined_prompt") or ""
                if refined_prompt:
                    prompt_text = refined_prompt
                    seg["prompt"] = refined_prompt
                    seg["prompt_refined"] = True
                # SSE-Event ans UI
                send(
                    "prompt_refined",
                    segment=seg_num,
                    total=n,
                    prompt=prompt_text,
                    image_mode=mode,
                    image_desc=refine.get("image_desc", ""),
                    reason=refine.get("reason", ""),
                    refined=bool(refined_prompt),
                    switched_mode=suggested,
                )
                _save_state(job_id)

            send("status", msg=f"Segment {seg_num}/{n}: Audio hochladen...")
            audio_fn = _upload_audio(audio_path)

            send("status", msg=f"Segment {seg_num}/{n}: Workflow starten — Bild={image_fn} {src_hint}")
            seed = (int(time.time()) + i * 7919) % (2**32)
            wf = _build_workflow(image_fn, audio_fn, prompt_text, duration, seed)
            prompt_id = _submit_workflow(wf, f"ltxbatch-{job_id}-seg{i}")

            send("status", msg=f"Segment {seg_num}/{n}: Rendern... (prompt_id={prompt_id[:8]})")
            outputs = _poll(prompt_id, timeout=3600)
            if outputs is None:
                send("segment_error", segment=seg_num, msg="Timeout beim Rendern")
                continue

            video_info = _get_video_info(outputs)
            if not video_info:
                send("segment_error", segment=seg_num, msg="Keine Videoausgabe von ComfyUI")
                continue

            video_url = _video_info_to_url(video_info)
            send("segment_done", segment=seg_num, total=n, url=video_url, prompt=prompt_text)
            # URL auch im State persistieren (für Single-Segment-Re-Render Replace)
            seg["video_url"] = video_url

            # Last-Frame IMMER extrahieren (nicht nur wenn nächstes 'prev' nutzt) —
            # so kann der User später per /render-segment ein beliebiges Segment mit
            # mode='prev' neu rendern, auch wenn das ursprüngliche Folgesegment 'start' war.
            send("status", msg=f"🔗 Segment {seg_num}: Last-Frame extrahieren...")
            try:
                video_bytes = _download_video_bytes(video_info)
                frame_png, ff_err = _extract_last_frame(video_bytes)
                if frame_png:
                    new_fn = _upload_image(frame_png, "image/png")
                    prev_last_frame_fn = new_fn
                    seg["last_frame_fn"] = new_fn
                    _save_state(job_id)
                    send("status", msg=f"✅ Seg. {seg_num} Last-Frame ({len(frame_png)//1024} KB) → {new_fn}")
                else:
                    send("status", msg=f"⚠ Seg. {seg_num}: Frame-Extraktion fehlgeschlagen. ffmpeg: {ff_err[:200]}")
                    prev_last_frame_fn = None
            except Exception as e:
                send("status", msg=f"⚠ Seg. {seg_num} Frame-Fehler ({type(e).__name__}: {e})")
                prev_last_frame_fn = None
            # Audio-Chunks NICHT mehr löschen — werden für Single-Segment-Re-Render
            # und für die /audio Preview-Route in _job_dir/{job_id}/chunks/ vorgehalten.

        send("complete", total=n)
    except Exception as e:
        send("error", msg=str(e))
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(None), loop)


# ── routes ─────────────────────────────────────────────────────────────────────

def _persist_chunks(job_id: str, raw_chunks: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Tmp-Chunks aus _split_wav nach data/ltx_batch/{job_id}/chunks/seg_{i}.wav verschieben.
    Räumt vorher alte Chunks weg (bei /reprepare). Gibt neue Pfade + Duration zurück."""
    chunks_dir = os.path.join(_job_dir(job_id), "chunks")
    if os.path.isdir(chunks_dir):
        for f in os.listdir(chunks_dir):
            try: os.unlink(os.path.join(chunks_dir, f))
            except Exception: pass
    os.makedirs(chunks_dir, exist_ok=True)
    out: list[tuple[str, float]] = []
    for i, (tmp_path, dur) in enumerate(raw_chunks):
        final = os.path.join(chunks_dir, f"seg_{i}.wav")
        try:
            shutil.move(tmp_path, final)
        except Exception:
            shutil.copy(tmp_path, final)
            try: os.unlink(tmp_path)
            except Exception: pass
        out.append((final, dur))
    return out


def _run_prepare(
    job_id: str,
    wav_data: bytes,
    image_data: bytes,
    image_mime: str,
    concept: str,
    ollama_model: str,
    chunk_sec: float,
):
    """Prepare-Arbeit im Hintergrund. Schreibt Fortschritt nach _prep_jobs[job_id]."""
    state = _prep_jobs[job_id]
    try:
        # 0) Source-Dateien dauerhaft persistieren (für /reprepare ohne Re-Upload)
        jdir = _job_dir(job_id)
        src_wav = os.path.join(jdir, "source.wav")
        with open(src_wav, "wb") as f:
            f.write(wav_data)
        ext = _MIME_EXT.get(image_mime, ".png")
        src_img = os.path.join(jdir, f"source{ext}")
        with open(src_img, "wb") as f:
            f.write(image_data)
        state["source_wav_path"] = src_wav
        state["source_image_path"] = src_img
        state["source_image_mime"] = image_mime
        state["chunk_sec_used"] = chunk_sec
        state["concept"] = concept
        state["ollama_model"] = ollama_model

        # 1) WAV splitten + Chunks persistieren
        state["status"] = "splitting"
        state["progress_msg"] = "WAV wird aufgeteilt..."
        raw_chunks = _split_wav(src_wav, chunk_sec)
        chunks = _persist_chunks(job_id, raw_chunks)
        n = len(chunks)
        state["total"] = n
        state["progress_msg"] = f"{n} Segmente geschnitten. Transkribiere WAV via Whisper..."
        print(f"[ltx_batch][{job_id[:8]}] WAV → {n} Segmente, transkribiere...", flush=True)

        # 2) Whisper-Transkript (voller WAV)
        state["status"] = "transcribing"
        transcript = _transcribe_wav(src_wav)
        state["transcript"] = transcript
        if transcript:
            print(f"[ltx_batch][{job_id[:8]}] Transkript ({len(transcript)} chars): {transcript[:200]}...", flush=True)
            state["progress_msg"] = f"Transkript fertig ({len(transcript)} Zeichen). Generiere Prompts..."
        else:
            state["progress_msg"] = "Keine Transkription. Generiere Prompts..."

        if not concept.strip():
            concept = "A character speaks expressively and gestures naturally while a story unfolds across multiple scenes."
            state["concept"] = concept

        # 3) Bild-Beschreibung via moondream (klein+schnell)
        state["status"] = "describing_image"
        image_desc = ""
        try:
            image_desc = _describe_image(image_data, preferred_model=ollama_model)
        except Exception as e:
            print(f"[ltx_batch] _describe_image Exception: {e}", flush=True)
        state["image_desc"] = image_desc
        if image_desc:
            print(f"[ltx_batch][{job_id[:8]}] Bild-Beschr.: {image_desc[:200]}", flush=True)
        if image_desc:
            print(f"[ltx_batch][{job_id[:8]}] Bild-Beschr. ({len(image_desc)} chars): {image_desc[:200]}...", flush=True)
        state["progress_msg"] = f"Bild beschrieben ({len(image_desc)} Zeichen). Generiere {n} Prompts via Ollama..."

        # 4) Prompts
        state["status"] = "generating_prompts"
        prompts = _generate_prompts(
            concept, n,
            ollama_model=ollama_model,
            transcript=transcript,
            image_desc=image_desc,
            chunk_sec=chunk_sec,
        )
        state["progress_msg"] = "Prompts fertig. Versuche Start-Bild zu ComfyUI hochzuladen..."

        # 5) Start-Bild zu ComfyUI — OPTIONAL in Prepare. Wenn ComfyUI down ist,
        # machen wir den Upload beim /render erneut. User kann trotzdem reviewen.
        state["status"] = "uploading"
        try:
            start_image_fn = _upload_image(image_data, image_mime)
            state["start_image_fn"] = start_image_fn
            print(f"[ltx_batch][{job_id[:8]}] Start-Bild zu ComfyUI: {start_image_fn}", flush=True)
        except Exception as e:
            print(f"[ltx_batch][{job_id[:8]}] ComfyUI-Upload in Prepare fehlgeschlagen ({e}) — wird bei /render retry'd", flush=True)
            state["start_image_fn"] = None

        # 6) Segments bauen
        segments = []
        for i, (chunk_path, duration) in enumerate(chunks):
            segments.append({
                "idx": i,
                "segment": i + 1,
                "duration": duration,
                "prompt": prompts[i] if i < len(prompts) else f"Scene {i+1}",
                "audio_path": chunk_path,
                "image_mode": "start" if i == 0 else "prev",
                "custom_fn": None,
            })
        state["segments"] = segments
        state["status"] = "ready"
        state["progress_msg"] = f"Bereit: {len(segments)} Segmente zur Review"
        _save_state(job_id)
    except Exception as e:
        print(f"[ltx_batch][{job_id[:8]}] PREPARE FEHLER: {type(e).__name__}: {e}", flush=True)
        state["status"] = "error"
        state["error"] = f"{type(e).__name__}: {e}"


def _run_reprepare(
    job_id: str,
    concept: str,
    ollama_model: str,
    chunk_sec: float,
    redo_transcript: bool = False,
    redo_image_desc: bool = False,
):
    """Re-Prepare aus den persistierten Source-Dateien. Reuse Transkript + Bild-Beschreibung
    (wenn vorhanden), re-split WAV nur wenn chunk_sec sich geändert hat, regen-Prompts immer."""
    state = _prep_jobs[job_id]
    try:
        src_wav = state.get("source_wav_path")
        src_img = state.get("source_image_path")
        if not src_wav or not os.path.exists(src_wav) or not src_img or not os.path.exists(src_img):
            raise RuntimeError("Source-Dateien fehlen — bitte regulär /prepare ausführen")

        state["status"] = "splitting"
        state["error"] = None
        old_chunk_sec = float(state.get("chunk_sec_used") or chunk_sec)
        need_resplit = abs(old_chunk_sec - chunk_sec) > 0.01 or not state.get("segments")

        if need_resplit:
            state["progress_msg"] = f"WAV neu splitten ({chunk_sec:.0f}s/Segment)..."
            raw_chunks = _split_wav(src_wav, chunk_sec)
            chunks = _persist_chunks(job_id, raw_chunks)
            state["chunk_sec_used"] = chunk_sec
        else:
            chunks = [(s["audio_path"], s["duration"]) for s in state.get("segments", [])]
            state["progress_msg"] = "Segmente unverändert — reuse Audio-Chunks."
        n = len(chunks)
        state["total"] = n

        # Transkript: reuse wenn vorhanden, sonst neu
        transcript = state.get("transcript") or ""
        if redo_transcript or not transcript:
            state["status"] = "transcribing"
            state["progress_msg"] = "Transkribiere WAV via Whisper..."
            transcript = _transcribe_wav(src_wav)
            state["transcript"] = transcript

        # Bild-Beschreibung: reuse wenn vorhanden, sonst neu
        image_desc = state.get("image_desc") or ""
        if redo_image_desc or not image_desc:
            state["status"] = "describing_image"
            state["progress_msg"] = "Generiere Prompts..."
            try:
                with open(src_img, "rb") as f:
                    image_desc = _describe_image(f.read(), preferred_model=ollama_model)
                state["image_desc"] = image_desc
            except Exception as e:
                print(f"[ltx_batch][{job_id[:8]}] Vision-Reuse-Fehler: {e}", flush=True)

        if not concept.strip():
            concept = state.get("concept") or "A character speaks expressively and gestures naturally."
        state["concept"] = concept
        state["ollama_model"] = ollama_model

        state["status"] = "generating_prompts"
        state["progress_msg"] = f"Generiere {n} neue Prompts via {ollama_model}..."
        prompts = _generate_prompts(
            concept, n,
            ollama_model=ollama_model,
            transcript=transcript,
            image_desc=image_desc,
            chunk_sec=chunk_sec,
        )

        # Wenn nicht resplit: bestehende custom_fn / image_mode pro Segment behalten,
        # aber prompts ersetzen. Nur audio_path/duration aus chunks.
        old_by_idx = {s["idx"]: s for s in (state.get("segments") or [])}
        segments = []
        for i, (chunk_path, duration) in enumerate(chunks):
            old = old_by_idx.get(i, {})
            segments.append({
                "idx": i,
                "segment": i + 1,
                "duration": duration,
                "prompt": prompts[i] if i < len(prompts) else f"Scene {i+1}",
                "audio_path": chunk_path,
                "image_mode": old.get("image_mode") if not need_resplit else ("start" if i == 0 else "prev"),
                "custom_fn": old.get("custom_fn") if not need_resplit else None,
            })
            if not need_resplit and old.get("last_frame_fn"):
                segments[-1]["last_frame_fn"] = old["last_frame_fn"]
        state["segments"] = segments
        state["status"] = "ready"
        state["progress_msg"] = f"Re-Prepare fertig: {n} Segmente"
        _save_state(job_id)
        print(f"[ltx_batch][{job_id[:8]}] Re-Prepare fertig — n={n}, resplit={need_resplit}", flush=True)
    except Exception as e:
        print(f"[ltx_batch][{job_id[:8]}] REPREPARE FEHLER: {type(e).__name__}: {e}", flush=True)
        state["status"] = "error"
        state["error"] = f"{type(e).__name__}: {e}"


def _run_segment(
    sub_job_id: str,
    parent_job_id: str,
    idx: int,
    seg: dict,
    start_image_fn: str,
    prev_last_frame_fn: str | None,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
):
    """Single-Segment-Re-Render. Eigene Sub-Job-ID + SSE-Stream. Aktualisiert
    last_frame_fn im Parent-State, damit nachfolgende Segmente den neuen Frame nutzen können."""
    def send(event: str, **data):
        payload = {"event": event, **data}
        _persist_event(sub_job_id, payload)
        asyncio.run_coroutine_threadsafe(queue.put(payload), loop)

    seg_num = idx + 1
    parent = _prep_jobs.get(parent_job_id) or {}
    total = len(parent.get("segments", []))
    try:
        duration = float(seg.get("duration", 9.0))
        prompt_text = seg.get("prompt", "")
        audio_path = seg.get("audio_path")
        mode = seg.get("image_mode", "prev")

        if mode == "custom" and seg.get("custom_fn"):
            image_fn = seg["custom_fn"]
            src_hint = "[🖼 Custom]"
        elif mode == "start":
            image_fn = start_image_fn
            src_hint = "[🏁 Start]"
        else:  # prev
            if prev_last_frame_fn:
                image_fn = prev_last_frame_fn
                src_hint = f"[🔗 Last-Frame Seg.{seg_num - 1}]"
            else:
                image_fn = start_image_fn
                src_hint = "[🏁 Start (kein prev verfügbar)]"

        # Just-in-Time Refinement auch beim Single-Re-Render (analog _run_batch).
        # Nur wenn: nicht Segment 0, nicht prompt_locked, nicht custom-Mode.
        should_refine = (
            idx >= 1
            and not seg.get("prompt_locked", False)
            and mode != "custom"
        )
        if should_refine:
            transcript = parent.get("transcript", "") or ""
            concept = parent.get("concept", "") or ""
            ollama_model = parent.get("ollama_model", OLLAMA_MODEL_DEFAULT) or OLLAMA_MODEL_DEFAULT
            chunk_texts = _split_transcript_per_chunk(transcript, total or 1)
            send("status", msg=f"🔍 Seg {seg_num}: Eingangsbild ansehen + Prompt verfeinern...")
            refine = _refine_prompt_for_segment(
                image_fn=image_fn,
                original_prompt=prompt_text,
                chunk_text=chunk_texts[idx] if idx < len(chunk_texts) else "",
                concept=concept,
                segment_index=idx,
                n_segments=total or 1,
                current_image_mode=mode,
                has_prev_last_frame=bool(prev_last_frame_fn),
                start_image_fn=start_image_fn,
                ollama_model=ollama_model,
            )
            suggested = refine.get("suggested_image_mode")
            if suggested == "start" and start_image_fn:
                image_fn = start_image_fn
                mode = "start"
                seg["image_mode"] = "start"
                src_hint = "[🏁 Start — auto-switched]"
                if parent.get("segments") and idx < len(parent["segments"]):
                    parent["segments"][idx]["image_mode"] = "start"
            elif suggested == "prev" and prev_last_frame_fn:
                image_fn = prev_last_frame_fn
                mode = "prev"
                seg["image_mode"] = "prev"
                src_hint = f"[🔗 Last-Frame Seg.{seg_num - 1} — auto-switched]"
                if parent.get("segments") and idx < len(parent["segments"]):
                    parent["segments"][idx]["image_mode"] = "prev"
            refined_prompt = refine.get("refined_prompt") or ""
            if refined_prompt:
                prompt_text = refined_prompt
                seg["prompt"] = refined_prompt
                if parent.get("segments") and idx < len(parent["segments"]):
                    parent["segments"][idx]["prompt"] = refined_prompt
                    parent["segments"][idx]["prompt_refined"] = True
            send(
                "prompt_refined",
                segment=seg_num,
                total=total,
                prompt=prompt_text,
                image_mode=mode,
                image_desc=refine.get("image_desc", ""),
                reason=refine.get("reason", ""),
                refined=bool(refined_prompt),
                switched_mode=suggested,
            )
            _save_state(parent_job_id)

        send("status", msg=f"↻ Re-Render Seg {seg_num}/{total}: Audio hochladen...")
        if not audio_path or not os.path.exists(audio_path):
            send("segment_error", segment=seg_num, msg="Audio-Chunk nicht (mehr) verfügbar — bitte /reprepare")
            send("complete", total=total)
            return
        audio_fn = _upload_audio(audio_path)

        send("status", msg=f"Seg {seg_num}: Workflow starten — Bild={image_fn} {src_hint}")
        seed = (int(time.time()) + idx * 7919) % (2**32)
        wf = _build_workflow(image_fn, audio_fn, prompt_text, duration, seed)
        prompt_id = _submit_workflow(wf, f"ltxbatch-rerun-{sub_job_id}")

        send("status", msg=f"Seg {seg_num}: Rendern... (prompt_id={prompt_id[:8]})")
        outputs = _poll(prompt_id, timeout=3600)
        if outputs is None:
            send("segment_error", segment=seg_num, msg="Timeout beim Rendern")
            send("complete", total=total)
            return

        video_info = _get_video_info(outputs)
        if not video_info:
            send("segment_error", segment=seg_num, msg="Keine Videoausgabe von ComfyUI")
            send("complete", total=total)
            return

        video_url = _video_info_to_url(video_info)
        send("segment_done", segment=seg_num, total=total, url=video_url, prompt=prompt_text, replace=True)

        # Parent-State updaten + Last-Frame extrahieren
        if parent.get("segments") and idx < len(parent["segments"]):
            parent["segments"][idx]["video_url"] = video_url
        send("status", msg=f"🔗 Seg {seg_num}: Last-Frame extrahieren...")
        try:
            video_bytes = _download_video_bytes(video_info)
            frame_png, ff_err = _extract_last_frame(video_bytes)
            if frame_png:
                new_fn = _upload_image(frame_png, "image/png")
                if parent.get("segments") and idx < len(parent["segments"]):
                    parent["segments"][idx]["last_frame_fn"] = new_fn
                    _save_state(parent_job_id)
                send("status", msg=f"✅ Seg {seg_num} Last-Frame ({len(frame_png)//1024} KB) → {new_fn}")
            else:
                send("status", msg=f"⚠ Last-Frame-Extraktion fehlgeschlagen: {ff_err[:160]}")
        except Exception as e:
            send("status", msg=f"⚠ Last-Frame-Fehler ({type(e).__name__}: {e})")

        send("complete", total=total)
    except Exception as e:
        send("error", msg=str(e))
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(None), loop)


@router.post("/prepare")
async def prepare_batch(
    wav: UploadFile = File(...),
    image: UploadFile = File(...),
    concept: str = Form(""),
    ollama_model: str = Form(OLLAMA_MODEL_DEFAULT),
    chunk_sec: float = Form(CHUNK_SEC_DEFAULT),
):
    """
    Startet die Prepare-Pipeline (WAV-Split + Whisper + Vision + Prompt-Gen +
    ComfyUI-Upload) als Background-Thread. Liefert sofort die job_id zurück.
    Progress pollen via GET /prepare-status/{job_id}.
    """
    job_id = uuid.uuid4().hex
    wav_data = await wav.read()
    image_data = await image.read()
    image_mime = image.content_type or "image/png"

    _prep_jobs[job_id] = {
        "status": "queued",
        "progress_msg": "Warten...",
        "total": 0,
        "transcript": "",
        "image_desc": "",
        "segments": [],
        "start_image_fn": None,
        "concept": concept,
        "ollama_model": ollama_model,
        "error": None,
    }

    import threading
    t = threading.Thread(
        target=_run_prepare,
        args=(job_id, wav_data, image_data, image_mime, concept, ollama_model, chunk_sec),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id}


@router.get("/prepare-status/{job_id}")
async def prepare_status(job_id: str):
    """Pollen während Prepare läuft. Liefert bei status='ready' die vollen Segmente."""
    job = _prep_jobs.get(job_id) or _load_state_or_reconstruct(job_id)
    if not job:
        return {"status": "unknown", "error": "job_id nicht bekannt"}
    resp = {
        "job_id": job_id,
        "status": job.get("status", "unknown"),
        "progress_msg": job.get("progress_msg", ""),
        "total": job.get("total", 0),
        "error": job.get("error"),
    }
    if job.get("status") == "ready":
        resp.update({
            "start_image_fn": job.get("start_image_fn"),
            "transcript": job.get("transcript", ""),
            "image_desc": job.get("image_desc", ""),
            "segments": [
                {
                    "idx": s["idx"],
                    "segment": s["segment"],
                    "duration": round(s["duration"], 2),
                    "prompt": s["prompt"],
                    "image_mode": s["image_mode"],
                    "custom_fn": s["custom_fn"],
                    # Audio-Preview URL — wird vom /audio-Endpunkt serviert
                    "audio_url": f"/api/ltx-batch/audio/{job_id}/{s['idx']}"
                    if s.get("audio_path") and os.path.exists(s["audio_path"]) else None,
                }
                for s in job.get("segments", [])
            ],
        })
    return resp


@router.get("/audio/{job_id}/{idx}")
async def serve_audio(job_id: str, idx: int):
    """Serviert den WAV-Chunk eines Segments für den Browser-Audio-Player."""
    from fastapi.responses import FileResponse
    from fastapi import HTTPException
    job = _prep_jobs.get(job_id) or _load_state_or_reconstruct(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id unbekannt")
    segs = job.get("segments", [])
    if idx < 0 or idx >= len(segs):
        raise HTTPException(status_code=404, detail="idx out of range")
    audio_path = segs[idx].get("audio_path")
    if not audio_path or not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Audio-Datei nicht (mehr) verfügbar")
    return FileResponse(audio_path, media_type="audio/wav")


@router.post("/upload-ref")
async def upload_ref(
    job_id: str = Form(...),
    idx: int = Form(...),
    image: UploadFile = File(...),
):
    """Custom-Referenz-Bild für ein einzelnes Segment hochladen."""
    job = _prep_jobs.get(job_id) or _load_state_or_reconstruct(job_id)
    if not job:
        return {"error": "job_id unbekannt oder abgelaufen"}
    if idx < 0 or idx >= len(job["segments"]):
        return {"error": "idx out of range"}
    data = await image.read()
    mime = image.content_type or "image/png"
    fn = _upload_image(data, mime)
    job["segments"][idx]["custom_fn"] = fn
    job["segments"][idx]["image_mode"] = "custom"
    _save_state(job_id)
    return {"ok": True, "custom_fn": fn, "idx": idx}


@router.post("/render")
async def render_batch(payload: dict):
    """
    Startet das Rendering mit den (evtl. editierten) Segmenten.
    Body: {"job_id": "...", "segments": [{idx, prompt, image_mode, custom_fn?}]}
    """
    job_id = payload.get("job_id")
    edits = payload.get("segments", [])
    job = _prep_jobs.get(job_id) or _load_state_or_reconstruct(job_id)
    if not job:
        return {"error": "job_id unbekannt oder abgelaufen — bitte neu vorbereiten"}

    # Edits in segments mergen (idx → update prompt + mode + custom_fn + lock)
    by_idx = {s["idx"]: s for s in job["segments"]}
    for e in edits:
        idx = e.get("idx")
        if idx in by_idx:
            s = by_idx[idx]
            if "prompt" in e:
                s["prompt"] = (e["prompt"] or "").strip() or s["prompt"]
            if "image_mode" in e and e["image_mode"] in ("start", "prev", "custom"):
                s["image_mode"] = e["image_mode"]
            if e.get("custom_fn"):
                s["custom_fn"] = e["custom_fn"]
            # Prompt-Lock: wenn gesetzt, kein Just-in-Time Refinement im Render.
            if "prompt_locked" in e:
                s["prompt_locked"] = bool(e["prompt_locked"])

    ok, err = _ensure_start_image_uploaded(job)
    if not ok:
        return {"error": f"{err}. Ins richtige Netz wechseln und erneut Rendern klicken."}

    _save_state(job_id)
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = queue
    loop = asyncio.get_event_loop()

    import threading
    t = threading.Thread(
        target=_run_batch,
        args=(job_id, job["segments"], job["start_image_fn"], queue, loop),
        daemon=True,
    )
    t.start()

    # _prep_jobs behalten bis Job fertig ist (für Replay evtl. nützlich, aufräumen optional)
    return {"job_id": job_id, "total": len(job["segments"])}


@router.post("/reprepare")
async def reprepare_batch(payload: dict):
    """
    Re-Prepare ohne Re-Upload: nutzt die bei /prepare persistierten Source-Dateien
    aus data/ltx_batch/{job_id}/. Reuse Transkript + Bild-Beschreibung; re-split nur
    wenn chunk_sec geändert; Prompts werden immer neu generiert.

    Body: {"job_id", "concept"?, "ollama_model"?, "chunk_sec"?,
           "redo_transcript"?: bool, "redo_image_desc"?: bool}
    """
    job_id = payload.get("job_id")
    job = _prep_jobs.get(job_id) or _load_state_or_reconstruct(job_id)
    if not job:
        return {"error": "job_id unbekannt — bitte regulär /prepare ausführen"}
    if not job.get("source_wav_path") or not os.path.exists(job["source_wav_path"]):
        return {"error": "Source-WAV nicht (mehr) verfügbar — bitte regulär /prepare ausführen"}
    if not job.get("source_image_path") or not os.path.exists(job["source_image_path"]):
        return {"error": "Source-Bild nicht (mehr) verfügbar — bitte regulär /prepare ausführen"}

    concept = payload.get("concept", job.get("concept", ""))
    ollama_model = payload.get("ollama_model", job.get("ollama_model", OLLAMA_MODEL_DEFAULT))
    chunk_sec = float(payload.get("chunk_sec", job.get("chunk_sec_used", CHUNK_SEC_DEFAULT)))
    redo_transcript = bool(payload.get("redo_transcript", False))
    redo_image_desc = bool(payload.get("redo_image_desc", False))

    job["status"] = "queued"
    job["progress_msg"] = "Re-Prepare gestartet..."
    job["error"] = None

    import threading
    t = threading.Thread(
        target=_run_reprepare,
        args=(job_id, concept, ollama_model, chunk_sec, redo_transcript, redo_image_desc),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id, "reprepare": True}


@router.post("/render-segment")
async def render_segment(payload: dict):
    """
    Rendert ein einzelnes Segment neu (z.B. nach Prompt-/Bild-Edit). Nutzt eine Sub-Job-ID
    für eigenen SSE-Stream — der parallele Original-Job bleibt unberührt.

    Body: {"job_id", "idx", "prompt"?, "image_mode"?, "custom_fn"?}
    Returns: {"sub_job_id", "idx", "segment"}
    """
    job_id = payload.get("job_id")
    idx = int(payload.get("idx", -1))
    job = _prep_jobs.get(job_id) or _load_state_or_reconstruct(job_id)
    if not job:
        return {"error": "job_id unbekannt"}
    segs = job.get("segments", [])
    if idx < 0 or idx >= len(segs):
        return {"error": "idx out of range"}

    # Edits in Parent-State mergen
    seg = segs[idx]
    new_prompt = (payload.get("prompt") or "").strip()
    if new_prompt:
        seg["prompt"] = new_prompt
    if payload.get("image_mode") in ("start", "prev", "custom"):
        seg["image_mode"] = payload["image_mode"]
    if payload.get("custom_fn"):
        seg["custom_fn"] = payload["custom_fn"]
    if "prompt_locked" in payload:
        seg["prompt_locked"] = bool(payload["prompt_locked"])

    ok, err = _ensure_start_image_uploaded(job)
    if not ok:
        return {"error": err}

    _save_state(job_id)
    prev_last_frame_fn = None
    if idx > 0:
        prev_last_frame_fn = segs[idx - 1].get("last_frame_fn")

    sub_job_id = f"{job_id}_s{idx}_{uuid.uuid4().hex[:6]}"
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[sub_job_id] = queue
    loop = asyncio.get_event_loop()

    import threading
    t = threading.Thread(
        target=_run_segment,
        args=(sub_job_id, job_id, idx, dict(seg), job["start_image_fn"], prev_last_frame_fn, queue, loop),
        daemon=True,
    )
    t.start()
    return {"sub_job_id": sub_job_id, "idx": idx, "segment": idx + 1}


@router.post("/concat/{job_id}")
def concat_finished(job_id: str):
    """Verbindet alle bisher fertig gerenderten Segmente zu einem Master-MP4.

    - Liest ``video_url``-Felder aller Segmente aus dem Job-State
    - Lädt die Videos aus ComfyUI runter, schreibt eine concat-Liste
    - Ruft ``ffmpeg -f concat -safe 0 -i list.txt -c copy``
    - Persistiert das Master-MP4 in ``data/ltx_batch/{job_id}/master_<ts>.mp4``
    - Gibt ``/api/ltx-batch/master/{job_id}/{filename}`` als URL zurück

    Idempotent — kann beliebig oft gerufen werden, jeder Aufruf erzeugt eine
    neue Master-Datei mit Timestamp-Suffix.
    """
    import subprocess
    from fastapi.responses import JSONResponse

    job = _prep_jobs.get(job_id) or _load_state_or_reconstruct(job_id)
    if not job:
        return JSONResponse({"error": "job_id unbekannt"}, status_code=404)

    segs = sorted(
        [s for s in job.get("segments", []) if s.get("video_url")],
        key=lambda s: s.get("idx", 0),
    )
    if not segs:
        return JSONResponse({"error": "noch keine fertigen Segmente"}, status_code=400)

    jdir = _job_dir(job_id)
    work_dir = os.path.join(jdir, "concat_work")
    os.makedirs(work_dir, exist_ok=True)
    list_path = os.path.join(work_dir, "list.txt")
    downloaded: list[str] = []

    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for s in segs:
                url = s["video_url"]
                # Download — ComfyUI-URL ist /view?filename=...&type=output
                local = os.path.join(work_dir, f"seg_{s['idx']:03d}.mp4")
                try:
                    r = requests.get(url, timeout=120)
                    r.raise_for_status()
                    with open(local, "wb") as out:
                        out.write(r.content)
                    downloaded.append(local)
                    # ffmpeg concat-format verlangt 'file <pfad>' pro Zeile,
                    # apostrophes im Pfad escaped
                    safe = local.replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")
                except Exception as e:
                    return JSONResponse(
                        {"error": f"Download Segment {s['idx']+1} fehlgeschlagen: {e}"},
                        status_code=502,
                    )

        ts = int(time.time())
        master_fn = f"master_{ts}.mp4"
        master_path = os.path.join(jdir, master_fn)

        # Erst stream-copy versuchen (schnell, keine Re-Encoding). Wenn fail
        # (Codec-Mismatch zwischen Segmenten), Fallback auf Re-Encode.
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", list_path, "-c", "copy", master_path],
                check=True, capture_output=True, timeout=180,
            )
        except subprocess.CalledProcessError:
            # Re-Encode-Fallback (langsamer aber robust)
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", list_path, "-c:v", "libx264", "-preset", "veryfast",
                 "-c:a", "aac", "-movflags", "+faststart", master_path],
                check=True, capture_output=True, timeout=600,
            )
        size = os.path.getsize(master_path)
        return {
            "ok": True,
            "url": f"/api/ltx-batch/master/{job_id}/{master_fn}",
            "filename": master_fn,
            "segments_used": len(segs),
            "size_bytes": size,
        }
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace")[-400:] if e.stderr else ""
        return JSONResponse(
            {"error": f"ffmpeg concat fehlgeschlagen", "stderr": stderr},
            status_code=500,
        )
    except FileNotFoundError:
        return JSONResponse(
            {"error": "ffmpeg nicht installiert (brew install ffmpeg)"},
            status_code=500,
        )
    finally:
        # work_dir aufräumen — Segmente sind dort runtergeladen, brauchen wir
        # nach dem Concat nicht mehr. Master-MP4 liegt in jdir/.
        try:
            for f in downloaded:
                if os.path.exists(f):
                    os.unlink(f)
            if os.path.exists(list_path):
                os.unlink(list_path)
            if os.path.isdir(work_dir):
                os.rmdir(work_dir)
        except Exception:
            pass


@router.get("/master/{job_id}/{filename}")
def serve_master(job_id: str, filename: str):
    """Liefert das Master-MP4 aus dem Job-Verzeichnis aus."""
    from fastapi.responses import FileResponse, JSONResponse
    if "/" in filename or ".." in filename or not filename.startswith("master_"):
        return JSONResponse({"error": "ungültiger filename"}, status_code=400)
    path = os.path.join(_job_dir(job_id), filename)
    if not os.path.exists(path):
        return JSONResponse({"error": "Master-MP4 nicht gefunden"}, status_code=404)
    return FileResponse(path, media_type="video/mp4", filename=filename)


@router.get("/progress/{job_id}")
async def progress_stream(job_id: str):
    queue = _jobs.get(job_id)

    # Job läuft nicht im RAM → Disk-Replay (Server-Neustart oder abgeschlossen)
    if queue is None:
        saved = _replay_events(job_id)

        async def _replay() -> AsyncGenerator[str, None]:
            if not saved:
                yield f"data: {json.dumps({'event': 'error', 'msg': 'Job nicht gefunden (kein Verlauf gespeichert)'})}\n\n"
                return
            for evt in saved:
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            # Falls Job mit complete/error endete ist er fertig; sonst als unterbrochen markieren
            last_event = saved[-1].get("event", "") if saved else ""
            if last_event not in ("complete", "error", "done"):
                yield f"data: {json.dumps({'event': 'error', 'msg': '⚠ Server wurde neu gestartet — Job unterbrochen. Bisherige Segmente oben sichtbar.'})}\n\n"

        return StreamingResponse(
            _replay(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def _generate() -> AsyncGenerator[str, None]:
        # Bereits gespeicherte Events zuerst senden (Reconnect nach Browser-Reload)
        for evt in _replay_events(job_id):
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

        # Idle-Timeout statt Gesamt-Timeout: bei jedem echten Event wird die
        # Deadline zurückgesetzt; Keepalive-Ticks zählen nicht als Aktivität.
        # Window > _poll(timeout=3600) damit ein laufender ComfyUI-Render
        # nicht abgebrochen wird, während er still wartet.
        IDLE_TIMEOUT_S = 90 * 60
        last_event_at = asyncio.get_event_loop().time()
        try:
            while True:
                idle = asyncio.get_event_loop().time() - last_event_at
                if idle >= IDLE_TIMEOUT_S:
                    yield (
                        "data: "
                        + json.dumps({
                            "event": "error",
                            "msg": f"Idle-Timeout ({IDLE_TIMEOUT_S // 60} min ohne Event)",
                        })
                        + "\n\n"
                    )
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30)
                    if item is None:
                        yield "data: {\"event\":\"done\"}\n\n"
                        break
                    last_event_at = asyncio.get_event_loop().time()
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _jobs.pop(job_id, None)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
