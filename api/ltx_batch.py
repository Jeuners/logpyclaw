"""
api/ltx_batch.py — LTX 2.3 Batch Renderer API.
Nimmt WAV + Bild entgegen, teilt WAV in 9s-Blöcke auf,
generiert per Ollama Prompts und rendert sequenziell via ComfyUI.
"""
import asyncio
import json
import os
import tempfile
import time
import uuid
import wave as wave_mod
from typing import AsyncGenerator

import requests
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/ltx-batch", tags=["ltx-batch"])

COMFYUI_URL = "http://192.168.3.26:8000"
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


def _ollama_chat(messages: list[dict], max_tokens: int = 400, model: str = OLLAMA_MODEL_DEFAULT, timeout: int = 900) -> str:
    """Ollama /api/chat call. Default-Timeout 15 Min — große Batches (42 Prompts × 90 Tokens) brauchen das."""
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        },
        timeout=timeout,
    )
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
    """Bild-Beschreibung via Vision-Modell. Default gemma4:e4b (kann Text+Vision).
    Fallback-Kette nur wenn Preferred nicht verfügbar ist."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        available = {m["name"] for m in r.json().get("models", [])}
    except Exception as e:
        print(f"[ltx_batch] Ollama tags unreachable: {e}", flush=True)
        return ""
    vision_candidates = [preferred_model, "gemma4:e4b", "gemma3:latest", "gemma3:4b",
                         "moondream:latest", "llava:latest", "llama3.2-vision:latest"]
    vision_model = next((m for m in vision_candidates if m in available), None)
    if not vision_model:
        print(f"[ltx_batch] Kein Vision-Modell in Ollama gefunden ({vision_candidates}) — verfügbar: {sorted(available)[:8]}", flush=True)
        return ""
    import base64
    img_b64 = base64.b64encode(image_bytes).decode("ascii")
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": vision_model,
                "messages": [{
                    "role": "user",
                    "content": "Describe this image concisely for a video-generation prompt: who/what is in the scene, setting, mood, colors, style. 2-3 sentences, English.",
                    "images": [img_b64],
                }],
                "stream": False,
                "options": {"num_predict": 200},
            },
            timeout=600,
        )
        resp.raise_for_status()
        return (resp.json().get("message", {}).get("content", "") or "").strip()
    except Exception as e:
        print(f"[ltx_batch] Vision-Call ({vision_model}) failed: {e}", flush=True)
        return ""


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
        prev_last_frame_fn: str | None = None

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

            send("status", msg=f"Segment {seg_num}/{n}: Audio hochladen...")
            audio_fn = _upload_audio(audio_path)

            send("status", msg=f"Segment {seg_num}/{n}: Workflow starten — Bild={image_fn} {src_hint}")
            seed = (int(time.time()) + i * 7919) % (2**32)
            wf = _build_workflow(image_fn, audio_fn, prompt_text, duration, seed)
            r = requests.post(
                f"{COMFYUI_URL}/prompt",
                json={"prompt": wf, "client_id": f"ltxbatch-{job_id}-seg{i}"},
                timeout=30,
            )
            r.raise_for_status()
            prompt_id = r.json().get("prompt_id")
            if not prompt_id:
                raise RuntimeError(f"Kein prompt_id für Segment {seg_num}")

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

            # Last-Frame nur extrahieren, wenn das NÄCHSTE Segment mode='prev' hat
            next_needs_prev = (i < n - 1) and (segments[i + 1].get("image_mode", "prev") == "prev")
            if next_needs_prev:
                send("status", msg=f"🔗 Segment {seg_num}: Last-Frame für Seg. {seg_num + 1} extrahieren...")
                try:
                    video_bytes = _download_video_bytes(video_info)
                    frame_png, ff_err = _extract_last_frame(video_bytes)
                    if frame_png:
                        new_fn = _upload_image(frame_png, "image/png")
                        prev_last_frame_fn = new_fn
                        send("status", msg=f"✅ Seg. {seg_num} Last-Frame ({len(frame_png)//1024} KB) → {new_fn}")
                    else:
                        send("status", msg=f"⚠ Seg. {seg_num}: Frame-Extraktion fehlgeschlagen — nutze Start-Bild als Fallback. ffmpeg: {ff_err[:200]}")
                        prev_last_frame_fn = None
                except Exception as e:
                    send("status", msg=f"⚠ Seg. {seg_num} Frame-Fehler ({type(e).__name__}: {e})")
                    prev_last_frame_fn = None

            try:
                if audio_path:
                    os.unlink(audio_path)
            except Exception:
                pass

        send("complete", total=n)
    except Exception as e:
        send("error", msg=str(e))
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(None), loop)


# ── routes ─────────────────────────────────────────────────────────────────────

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
        # 1) WAV auf Disk + splitten
        state["status"] = "splitting"
        state["progress_msg"] = "WAV wird aufgeteilt..."
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix=f"ltxbatch_{job_id}_")
        with os.fdopen(fd, "wb") as f:
            f.write(wav_data)
        try:
            chunks = _split_wav(wav_path, chunk_sec)
            n = len(chunks)
            state["total"] = n
            state["progress_msg"] = f"{n} Segmente geschnitten. Transkribiere WAV via Whisper..."
            print(f"[ltx_batch][{job_id[:8]}] WAV → {n} Segmente, transkribiere...", flush=True)

            # 2) Whisper-Transkript (voller WAV)
            state["status"] = "transcribing"
            transcript = _transcribe_wav(wav_path)
            state["transcript"] = transcript
            if transcript:
                print(f"[ltx_batch][{job_id[:8]}] Transkript ({len(transcript)} chars): {transcript[:200]}...", flush=True)
                state["progress_msg"] = f"Transkript fertig ({len(transcript)} Zeichen). Beschreibe Start-Bild..."
            else:
                state["progress_msg"] = "Keine Transkription. Beschreibe Start-Bild..."
        finally:
            try: os.unlink(wav_path)
            except Exception: pass

        if not concept.strip():
            concept = "A character speaks expressively and gestures naturally while a story unfolds across multiple scenes."

        # 3) Bild-Beschreibung
        state["status"] = "describing_image"
        print(f"[ltx_batch][{job_id[:8]}] Beschreibe Start-Bild via {ollama_model}...", flush=True)
        image_desc = _describe_image(image_data, preferred_model=ollama_model)
        state["image_desc"] = image_desc
        if image_desc:
            print(f"[ltx_batch][{job_id[:8]}] Bild-Beschr. ({len(image_desc)} chars): {image_desc[:200]}...", flush=True)
        state["progress_msg"] = f"Bild beschrieben ({len(image_desc)} Zeichen). Generiere {state['total']} Prompts via Ollama..."

        # 4) Prompts
        state["status"] = "generating_prompts"
        prompts = _generate_prompts(
            concept, state["total"],
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
            # Bild-Bytes für späteren Upload beim /render merken
            state["_pending_image_bytes"] = image_data
            state["_pending_image_mime"] = image_mime

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
    except Exception as e:
        print(f"[ltx_batch][{job_id[:8]}] PREPARE FEHLER: {type(e).__name__}: {e}", flush=True)
        state["status"] = "error"
        state["error"] = f"{type(e).__name__}: {e}"


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
    job = _prep_jobs.get(job_id)
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
    job = _prep_jobs.get(job_id)
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
    job = _prep_jobs.get(job_id)
    if not job:
        return {"error": "job_id unbekannt oder abgelaufen"}
    if idx < 0 or idx >= len(job["segments"]):
        return {"error": "idx out of range"}
    data = await image.read()
    mime = image.content_type or "image/png"
    fn = _upload_image(data, mime)
    job["segments"][idx]["custom_fn"] = fn
    job["segments"][idx]["image_mode"] = "custom"
    return {"ok": True, "custom_fn": fn, "idx": idx}


@router.post("/render")
async def render_batch(payload: dict):
    """
    Startet das Rendering mit den (evtl. editierten) Segmenten.
    Body: {"job_id": "...", "segments": [{idx, prompt, image_mode, custom_fn?}]}
    """
    job_id = payload.get("job_id")
    edits = payload.get("segments", [])
    job = _prep_jobs.get(job_id)
    if not job:
        return {"error": "job_id unbekannt oder abgelaufen — bitte neu vorbereiten"}

    # Edits in segments mergen (idx → update prompt + mode + custom_fn)
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

    # Wenn Start-Bild-Upload in Prepare ausgelassen wurde (ComfyUI war down) —
    # jetzt nachholen. Wenn ComfyUI immer noch tot, sauberer Fehler.
    if not job.get("start_image_fn"):
        pending_bytes = job.get("_pending_image_bytes")
        pending_mime = job.get("_pending_image_mime", "image/png")
        if not pending_bytes:
            return {"error": "Kein Start-Bild vorhanden — bitte /prepare erneut ausführen"}
        try:
            start_fn = _upload_image(pending_bytes, pending_mime)
            job["start_image_fn"] = start_fn
            # Pending nach erfolgreichem Upload aufräumen (sparen Speicher)
            job.pop("_pending_image_bytes", None)
            job.pop("_pending_image_mime", None)
            print(f"[ltx_batch][{job_id[:8]}] Start-Bild-Upload beim /render nachgeholt: {start_fn}", flush=True)
        except Exception as e:
            return {"error": f"ComfyUI nicht erreichbar ({type(e).__name__}: {e}). Ins richtige Netz wechseln und erneut Rendern klicken."}

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
