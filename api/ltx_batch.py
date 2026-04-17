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


def _ollama_chat(messages: list[dict], max_tokens: int = 400, model: str = OLLAMA_MODEL_DEFAULT) -> str:
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "").strip()


def _generate_prompts(concept: str, n_segments: int, ollama_model: str = OLLAMA_MODEL_DEFAULT) -> list[str]:
    """Generate n_segments visual prompts for the video segments via Ollama."""
    system = (
        "You are a cinematic video prompt engineer for AI video generation. "
        "Generate exactly the requested number of English visual prompts for consecutive 9-second video segments. "
        "Each prompt describes what happens visually in that segment. "
        "The same character/scene image is used for all segments. "
        "Format: one prompt per line, no numbering, no extra text. "
        "Each prompt: 50-80 words, cinematic quality, focus on motion/action/expression."
    )
    user = (
        f"Overall concept: {concept}\n\n"
        f"Generate exactly {n_segments} prompts (one per line) for {n_segments} consecutive 9-second segments."
    )
    raw = _ollama_chat([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], max_tokens=n_segments * 100, model=ollama_model)
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    # ensure we have exactly n_segments prompts
    while len(lines) < n_segments:
        lines.append(concept)
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


def _poll(prompt_id: str, timeout: int = 1800) -> dict | None:
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


def _get_video_url(outputs: dict) -> str | None:
    for node_out in outputs.values():
        for key in ("videos", "gifs", "images"):
            items = node_out.get(key, [])
            if items:
                info = items[0]
                fn = info["filename"]
                subfolder = info.get("subfolder", "")
                ftype = info.get("type", "output")
                params = f"filename={fn}&type={ftype}"
                if subfolder:
                    params += f"&subfolder={subfolder}"
                return f"{COMFYUI_URL}/view?{params}"
    return None


# ── background job ─────────────────────────────────────────────────────────────

def _run_batch(
    job_id: str,
    image_bytes: bytes,
    image_mime: str,
    wav_path: str,
    concept: str,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    ollama_model: str = OLLAMA_MODEL_DEFAULT,
    chunk_sec: float = CHUNK_SEC_DEFAULT,
):
    def send(event: str, **data):
        payload = {"event": event, **data}
        asyncio.run_coroutine_threadsafe(queue.put(payload), loop)

    try:
        send("status", msg="Bild wird hochgeladen...")
        image_fn = _upload_image(image_bytes, image_mime)
        send("status", msg=f"Bild hochgeladen: {image_fn}")

        send("status", msg="WAV wird aufgeteilt...")
        chunks = _split_wav(wav_path, chunk_sec)
        n = len(chunks)
        send("status", msg=f"{n} Segmente à max. {chunk_sec}s erkannt")

        send("status", msg=f"Generiere {n} Prompts via Ollama ({ollama_model})...")
        prompts = _generate_prompts(concept, n, ollama_model=ollama_model)
        for i, p in enumerate(prompts):
            send("prompt", segment=i + 1, text=p)

        for i, (chunk_path, duration) in enumerate(chunks):
            seg_num = i + 1
            send("status", msg=f"Segment {seg_num}/{n}: Audio hochladen...")
            audio_fn = _upload_audio(chunk_path)

            send("status", msg=f"Segment {seg_num}/{n}: ComfyUI Workflow starten...")
            wf = _build_workflow(image_fn, audio_fn, prompts[i], duration, int(time.time()) % (2**32))
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
            outputs = _poll(prompt_id, timeout=1800)
            if outputs is None:
                send("segment_error", segment=seg_num, msg="Timeout beim Rendern")
                continue

            video_url = _get_video_url(outputs)
            if video_url:
                send("segment_done", segment=seg_num, total=n, url=video_url, prompt=prompts[i])
            else:
                send("segment_error", segment=seg_num, msg="Keine Videoausgabe von ComfyUI")

            try:
                os.unlink(chunk_path)
            except Exception:
                pass

        send("complete", total=n)
    except Exception as e:
        send("error", msg=str(e))
    finally:
        asyncio.run_coroutine_threadsafe(queue.put(None), loop)
        try:
            os.unlink(wav_path)
        except Exception:
            pass


# ── routes ─────────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_batch(
    wav: UploadFile = File(...),
    image: UploadFile = File(...),
    concept: str = Form(""),
    ollama_model: str = Form(OLLAMA_MODEL_DEFAULT),
    chunk_sec: float = Form(CHUNK_SEC_DEFAULT),
):
    job_id = uuid.uuid4().hex
    wav_data = await wav.read()
    image_data = await image.read()
    image_mime = image.content_type or "image/png"

    # Save WAV to temp file
    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix=f"ltxbatch_{job_id}_")
    with os.fdopen(fd, "wb") as f:
        f.write(wav_data)

    # Generate concept via Ollama if not provided
    if not concept.strip():
        concept = "A character speaks expressively and gestures naturally while a story unfolds across multiple scenes."

    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = queue
    loop = asyncio.get_event_loop()

    import threading
    t = threading.Thread(
        target=_run_batch,
        args=(job_id, image_data, image_mime, wav_path, concept, queue, loop),
        kwargs={"ollama_model": ollama_model, "chunk_sec": chunk_sec},
        daemon=True,
    )
    t.start()

    return {"job_id": job_id}


@router.get("/progress/{job_id}")
async def progress_stream(job_id: str):
    queue = _jobs.get(job_id)
    if queue is None:
        async def _not_found():
            yield "data: {\"event\":\"error\",\"msg\":\"Job nicht gefunden\"}\n\n"
        return StreamingResponse(_not_found(), media_type="text/event-stream")

    async def _generate() -> AsyncGenerator[str, None]:
        try:
            while True:
                item = await asyncio.wait_for(queue.get(), timeout=120)
                if item is None:
                    yield "data: {\"event\":\"done\"}\n\n"
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except asyncio.TimeoutError:
            yield "data: {\"event\":\"error\",\"msg\":\"Timeout\"}\n\n"
        finally:
            _jobs.pop(job_id, None)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
