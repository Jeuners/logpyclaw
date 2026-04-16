"""ComfyUI integration: image generation, video generation, image editing."""
import base64
import io
import json
import os
import re
import time
import uuid

import requests
from PIL import Image


# ── Debug logging (mirrors app.py _DEBUG_LOG) ──────────────────────────────────
_DEBUG = os.environ.get("DEBUG_LOG", "0") == "1"


def _dlog(*args, tag="DEBUG"):
    if _DEBUG:
        print(f"[{tag}]", *args, flush=True)


def _load_providers() -> dict:
    try:
        from core.config import PROVIDERS_FILE
        with open(PROVIDERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── Prompt extraction & optimization ───────────────────────────────────────────

def extract_img_prompt(message: str) -> str:
    """Strip trigger words from image generation message, return clean subject."""
    cleaned = re.sub(
        r"\b(bild|generier\w*|erstell\w*|zeich\w*|mal\w*|mach\w*|zeig\w*|"
        r"mir\w*|eine?\w*|eines?\w*|von|generate|draw|create|make|"
        r"paint|an?\b|image|picture|photo|of|bitte|please|einen?|einer?)\b",
        " ",
        message,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def extract_video_prompt(message: str) -> str:
    """Extract a clean scene description from a user message for video generation."""
    cleaned = re.sub(r"^[\s<]+|[\s>]+$", "", message.strip())
    cleaned = re.sub(
        r"\b(generiere?\s+(ein|einen|eine)?\s*video|erstelle?\s+(ein|einen|eine)?\s*video|"
        r"mach(e)?\s+(ein|einen|eine)?\s*video|erzeuge?\s+(ein|einen|eine)?\s*video|"
        r"create\s+a\s+video|generate\s+a\s+video|make\s+a\s+video|produce\s+a\s+video|"
        r"animate|animiere?|video\s+von|video\s+of|dreh(e)?\s+(ein|einen|eine)?|"
        r"bitte|please)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def prepare_video_prompt(message: str, providers: dict = None) -> str:
    """
    Turn a raw user message into an optimized English video prompt.
    Uses a fast LLM call to translate/expand if ollama is available,
    otherwise falls back to basic extraction + translation.
    """
    if providers is None:
        providers = _load_providers()

    raw = extract_video_prompt(message)
    if not raw:
        raw = message.strip()

    try:
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        system = (
            "You are a cinematic video prompt engineer. "
            "Convert the user's request into a concise, vivid English prompt for an AI video model. "
            "Output ONLY the prompt — no explanation, no quotes, no meta text. "
            "Keep it under 120 words. Focus on visual description: scene, lighting, mood, style, movement. "
            "If the input is German, translate to English. "
            "Always end with cinematic quality descriptors."
        )
        resp = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model": "gemma3:latest",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": raw},
                ],
                "stream": False,
                "options": {"num_predict": 150},
            },
            timeout=20,
        )
        if resp.ok:
            optimized = resp.json().get("message", {}).get("content", "").strip()
            if optimized and len(optimized) > 10:
                print(f"[Video] optimized prompt: {optimized[:100]}", flush=True)
                return optimized
    except Exception as e:
        print(f"[Video] prompt optimization failed, using raw: {e}", flush=True)

    # Fallback: basic German→English word substitution
    de_en = {
        "farben": "colors", "farbe": "color", "dunkel": "dark", "hell": "bright",
        "licht": "light", "neon": "neon", "grün": "green", "blau": "blue",
        "rot": "red", "schwarz": "black", "weiß": "white", "gold": "golden",
        "organisch": "organic", "biomechanisch": "biomechanical", "alien": "alien",
        "atmosphärisch": "atmospheric", "surreal": "surreal", "dramatisch": "dramatic",
        "kinofilm": "cinematic", "stimmung": "mood", "textur": "texture",
        "bewegung": "motion", "szene": "scene", "hintergrund": "background",
    }
    p = raw.lower()
    for de, en in de_en.items():
        p = re.sub(rf"\b{re.escape(de)}\b", en, p)
    p = p.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
    return p.strip()


def optimize_prompt_for_image(prompt: str) -> str:
    """Optimize prompt for image generation: English only, no text in output."""
    translations = {
        "strand": "beach", "meer": "sea", "himmel": "sky", "sonne": "sun",
        "mond": "moon", "sterne": "stars", "planeten": "planets", "galaxie": "galaxy",
        "berg": "mountain", "wald": "forest", "fluss": "river", "see": "lake",
        "stadt": "city", "haus": "house", "mensch": "person", "personen": "people",
        "frau": "woman", "mann": "man", "kind": "child", "tier": "animal",
        "vogel": "bird", "blume": "flower", "baum": "tree", "straße": "street",
        "gebäude": "building", "auto": "car", "boot": "boat", "flugzeug": "airplane",
    }
    p = prompt.lower()
    for de, en in translations.items():
        p = re.sub(rf"\b{de}\b", en, p)
    p = p.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")
    negative = (
        "no text, no letters, no words, no watermark, no signature, "
        "no title, no caption, no writing, clean image, photorealistic"
    )
    return f"{p}, {negative}"


# ── Thumbnail helper ────────────────────────────────────────────────────────────

def make_thumbnail(b64_data_url: str, max_size: int = 200) -> str:
    """Create a small JPEG thumbnail from a base64 data URL. Returns data URL or None."""
    try:
        if not b64_data_url or not b64_data_url.startswith("data:"):
            return None
        header, b64_str = b64_data_url.split(",", 1)
        img_data = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(img_data))
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=70, optimize=True)
        thumb_b64 = base64.b64encode(output.getvalue()).decode()
        return f"data:image/jpeg;base64,{thumb_b64}"
    except Exception as e:
        print(f"[Thumbnail] Error: {e}", flush=True)
        return None


# ── ComfyUI helpers ─────────────────────────────────────────────────────────────

def upload_image_to_comfyui(image_b64: str, base_url: str) -> str:
    """Upload image to ComfyUI and return filename."""
    filename = f"agentclaw_edit_{uuid.uuid4().hex[:8]}.png"
    if "," in image_b64:
        header, b64data = image_b64.split(",", 1)
        mime = "image/jpeg" if ("jpeg" in header or "jpg" in header) else "image/png"
    else:
        b64data = image_b64
        mime = "image/png"
    img_bytes = base64.b64decode(b64data)
    files = {"image": (filename, img_bytes, mime)}
    resp = requests.post(f"{base_url}/upload/image", files=files, timeout=30)
    resp.raise_for_status()
    return filename


def upload_audio_to_comfyui(audio_path: str, base_url: str) -> str:
    """Upload audio to ComfyUI input/ folder. Returns server-side filename."""
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio nicht gefunden: {audio_path}")
    ext = os.path.splitext(audio_path)[1].lower() or ".mp3"
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
            "flac": "audio/flac", "ogg": "audio/ogg"}.get(ext.lstrip("."), "audio/mpeg")
    filename = f"agentclaw_ia2v_{uuid.uuid4().hex[:8]}{ext}"
    with open(audio_path, "rb") as f:
        files = {"image": (filename, f.read(), mime)}
    resp = requests.post(f"{base_url}/upload/image", files=files, timeout=60)
    resp.raise_for_status()
    return filename


def _poll_comfyui(base_url: str, prompt_id: str, timeout: int = 120, interval: int = 2) -> dict:
    """Poll ComfyUI history until completed or timeout. Returns outputs dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        h = requests.get(f"{base_url}/history/{prompt_id}", timeout=10)
        data = h.json()
        entry = data.get(prompt_id, {})
        status = entry.get("status", {})
        if status.get("completed"):
            _dlog(
                f"ComfyUI completed. status_str={status.get('status_str')} "
                f"output_nodes={list(entry.get('outputs', {}).keys())}",
                tag="ComfyUI",
            )
            return entry.get("outputs", {})
        if status.get("status_str") == "error":
            msgs = status.get("messages", [])
            raise RuntimeError(f"ComfyUI Workflow-Fehler: {msgs}")
    return None


def _download_comfyui_file(base_url: str, file_info: dict, default_mime: str = "image/png") -> str:
    """Download a file from ComfyUI outputs and return as base64 data URL."""
    filename = file_info["filename"]
    subfolder = file_info.get("subfolder", "")
    f_type = file_info.get("type", "output")
    params = f"filename={filename}&type={f_type}"
    if subfolder:
        params += f"&subfolder={subfolder}"
    r = requests.get(f"{base_url}/view?{params}", timeout=60)
    r.raise_for_status()
    mime = r.headers.get("Content-Type", default_mime).split(";")[0]
    b64 = base64.b64encode(r.content).decode()
    return f"data:{mime};base64,{b64}"


# ── Workflow builders ───────────────────────────────────────────────────────────

def build_z_image_turbo_workflow(prompt: str, seed: int) -> dict:
    """z_image_turbo workflow — fast local model (8 steps)."""
    return {
        "9": {
            "inputs": {"filename_prefix": "agentclaw", "images": ["57:8", 0]},
            "class_type": "SaveImage",
        },
        "57:30": {
            "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"},
            "class_type": "CLIPLoader",
        },
        "57:29": {"inputs": {"vae_name": "ae.safetensors"}, "class_type": "VAELoader"},
        "57:33": {
            "inputs": {"text": IMAGE_NEGATIVE_PROMPT, "clip": ["57:30", 0]},
            "class_type": "CLIPTextEncode",
        },
        "57:8": {
            "inputs": {"samples": ["57:3", 0], "vae": ["57:29", 0]},
            "class_type": "VAEDecode",
        },
        "57:28": {
            "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"},
            "class_type": "UNETLoader",
        },
        "57:27": {
            "inputs": {"text": prompt, "clip": ["57:30", 0]},
            "class_type": "CLIPTextEncode",
        },
        "57:13": {
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
            "class_type": "EmptySD3LatentImage",
        },
        "57:11": {
            "inputs": {"shift": 3, "model": ["57:28", 0]},
            "class_type": "ModelSamplingAuraFlow",
        },
        "57:3": {
            "inputs": {
                "seed": seed, "steps": 8, "cfg": 1,
                "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1,
                "model": ["57:11", 0], "positive": ["57:27", 0],
                "negative": ["57:33", 0], "latent_image": ["57:13", 0],
            },
            "class_type": "KSampler",
        },
    }


IMAGE_NEGATIVE_PROMPT = (
    "text, letters, words, watermark, signature, title, caption, writing, logo, brand, "
    "typography, font, label, banner, subtitles, overlay, stamp, badge, icon, symbol, "
    "blurry, low quality, worst quality, deformed, distorted, ugly, duplicate"
)

WAN_VIDEO_NEGATIVE = (
    "vivid colors, overexposed, static, blurry details, subtitles, stylized, artwork, "
    "painting, still frame, grayish overall, worst quality, low quality, JPEG compression artifacts, "
    "ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn face, deformed, disfigured, "
    "malformed limbs, fused fingers, static motion, cluttered background, three legs, "
    "crowded background, walking backwards, nudity, NSFW"
)


def build_wan_video_workflow(prompt: str, seed: int) -> dict:
    """Build the Wan 2.2 T2V LightX2V 4-step workflow."""
    return {
        "71": {"inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}, "class_type": "CLIPLoader"},
        "72": {"inputs": {"text": WAN_VIDEO_NEGATIVE, "clip": ["71", 0]}, "class_type": "CLIPTextEncode"},
        "73": {"inputs": {"vae_name": "wan_2.1_vae.safetensors"}, "class_type": "VAELoader"},
        "74": {"inputs": {"width": 640, "height": 640, "length": 81, "batch_size": 1}, "class_type": "EmptyHunyuanLatentVideo"},
        "75": {"inputs": {"unet_name": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
        "76": {"inputs": {"unet_name": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
        "78": {
            "inputs": {
                "add_noise": "disable", "noise_seed": seed, "steps": 4, "cfg": 1,
                "sampler_name": "euler", "scheduler": "simple",
                "start_at_step": 2, "end_at_step": 4, "return_with_leftover_noise": "disable",
                "model": ["86", 0], "positive": ["89", 0], "negative": ["72", 0], "latent_image": ["81", 0],
            },
            "class_type": "KSamplerAdvanced",
        },
        "80": {
            "inputs": {"filename_prefix": "video/ComfyUI", "format": "auto", "codec": "auto", "video-preview": "", "video": ["88", 0]},
            "class_type": "SaveVideo",
        },
        "81": {
            "inputs": {
                "add_noise": "enable", "noise_seed": seed, "steps": 4, "cfg": 1,
                "sampler_name": "euler", "scheduler": "simple",
                "start_at_step": 0, "end_at_step": 2, "return_with_leftover_noise": "enable",
                "model": ["82", 0], "positive": ["89", 0], "negative": ["72", 0], "latent_image": ["74", 0],
            },
            "class_type": "KSamplerAdvanced",
        },
        "82": {"inputs": {"shift": 5.0, "model": ["83", 0]}, "class_type": "ModelSamplingSD3"},
        "83": {
            "inputs": {"lora_name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", "strength_model": 1.0, "model": ["75", 0]},
            "class_type": "LoraLoaderModelOnly",
        },
        "85": {
            "inputs": {"lora_name": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors", "strength_model": 1.0, "model": ["76", 0]},
            "class_type": "LoraLoaderModelOnly",
        },
        "86": {"inputs": {"shift": 5.0, "model": ["85", 0]}, "class_type": "ModelSamplingSD3"},
        "87": {"inputs": {"samples": ["78", 0], "vae": ["73", 0]}, "class_type": "VAEDecode"},
        "88": {"inputs": {"fps": 16, "images": ["87", 0]}, "class_type": "CreateVideo"},
        "89": {"inputs": {"text": prompt, "clip": ["71", 0]}, "class_type": "CLIPTextEncode"},
    }


def build_firered_edit_workflow(
    image_filename: str, prompt: str, seed: int, use_lightning: bool = True
) -> dict:
    """FireRed Image Edit 1.1 workflow."""
    return {
        "9": {
            "inputs": {"filename_prefix": "agentclaw_edit", "images": ["167:126", 0]},
            "class_type": "SaveImage",
        },
        "167:120": {
            "inputs": {"shift": 3.1, "model": ["167:154", 0]},
            "class_type": "ModelSamplingAuraFlow",
        },
        "167:154": {
            "inputs": {"switch": ["167:153", 0], "on_false": ["167:128", 0], "on_true": ["167:151", 0]},
            "class_type": "ComfySwitchNode",
        },
        "167:155": {"inputs": {"value": 40}, "class_type": "PrimitiveInt"},
        "167:123": {
            "inputs": {"strength": 1, "model": ["167:120", 0]},
            "class_type": "CFGNorm",
        },
        "167:164": {
            "inputs": {"switch": ["167:153", 0], "on_false": ["167:162", 0], "on_true": ["167:163", 0]},
            "class_type": "ComfySwitchNode",
        },
        "167:156": {"inputs": {"value": 8}, "class_type": "PrimitiveInt"},
        "167:162": {"inputs": {"value": 4}, "class_type": "PrimitiveFloat"},
        "167:163": {"inputs": {"value": 1}, "class_type": "PrimitiveFloat"},
        "167:157": {
            "inputs": {"switch": ["167:153", 0], "on_false": ["167:155", 0], "on_true": ["167:156", 0]},
            "class_type": "ComfySwitchNode",
        },
        "167:116": {
            "inputs": {"vae_name": "qwen_image_vae.safetensors"},
            "class_type": "VAELoader",
        },
        "167:115": {
            "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"},
            "class_type": "CLIPLoader",
        },
        "167:151": {
            "inputs": {"lora_name": "FireRed-Image-Edit-1.0-Lightning-8steps-v1.0.safetensors", "strength_model": 1, "model": ["167:128", 0]},
            "class_type": "LoraLoaderModelOnly",
        },
        "167:128": {
            "inputs": {"unet_name": "FireRed-Image-Edit-1.1-transformer.safetensors", "weight_dtype": "default"},
            "class_type": "UNETLoader",
        },
        "167:125": {
            "inputs": {"pixels": ["167:147", 0], "vae": ["167:116", 0]},
            "class_type": "VAEEncode",
        },
        "167:153": {
            "inputs": {"value": use_lightning},
            "class_type": "PrimitiveBoolean",
        },
        "167:118": {
            "inputs": {"prompt": prompt, "clip": ["167:115", 0], "vae": ["167:116", 0], "image1": ["167:147", 0]},
            "class_type": "TextEncodeQwenImageEditPlus",
        },
        "167:117": {
            "inputs": {"prompt": "", "clip": ["167:115", 0], "vae": ["167:116", 0], "image1": ["167:147", 0]},
            "class_type": "TextEncodeQwenImageEditPlus",
        },
        "167:130": {
            "inputs": {
                "seed": seed, "steps": ["167:157", 0], "cfg": ["167:164", 0],
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1,
                "model": ["167:123", 0], "positive": ["167:118", 0],
                "negative": ["167:117", 0], "latent_image": ["167:125", 0],
            },
            "class_type": "KSampler",
        },
        "167:126": {
            "inputs": {"samples": ["167:130", 0], "vae": ["167:116", 0]},
            "class_type": "VAEDecode",
        },
        "167:143": {
            "inputs": {"image": image_filename},
            "class_type": "LoadImage",
        },
        "167:147": {
            "inputs": {"image": ["167:143", 0]},
            "class_type": "FluxKontextImageScale",
        },
    }


# ── Main skill runners ──────────────────────────────────────────────────────────

def run_comfyui_sync(prompt: str) -> str:
    """Run ComfyUI image generation synchronously. Returns base64 data URL."""
    providers = _load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")
    seed = int(time.time()) % (2**32)

    optimized = optimize_prompt_for_image(prompt)
    print(f"[ComfyUI] original: {prompt[:60]}...", flush=True)
    print(f"[ComfyUI] optimized: {optimized[:60]}...", flush=True)

    workflow = build_z_image_turbo_workflow(optimized, seed)
    r = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": "agentclaw-task"},
        timeout=30,
    )
    r.raise_for_status()
    resp_json = r.json()
    if "prompt_id" not in resp_json:
        raise RuntimeError(f"ComfyUI Antwort unerwartet: {resp_json}")
    prompt_id = resp_json["prompt_id"]

    outputs = _poll_comfyui(base_url, prompt_id, timeout=120, interval=2)
    if not outputs:
        raise RuntimeError("Timeout: ComfyUI hat nicht rechtzeitig geantwortet")

    img_info = None
    for node_out in outputs.values():
        imgs = node_out.get("images", [])
        if imgs:
            img_info = imgs[0]
            break

    if not img_info:
        raise RuntimeError("Keine Bilddaten in der ComfyUI-Antwort")

    return _download_comfyui_file(base_url, img_info, default_mime="image/png")


def run_comfyui_video(prompt: str) -> str:
    """Generate a video via Wan 2.2 T2V on ComfyUI. Returns base64 data URL."""
    providers = _load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")
    seed = int(time.time()) % (2**32)

    optimized = prepare_video_prompt(prompt, providers)
    print(f"[Video] raw: {prompt[:80]}", flush=True)
    print(f"[Video] final prompt: {optimized[:120]}", flush=True)

    workflow = build_wan_video_workflow(optimized, seed)
    r = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": "agentclaw-video"},
        timeout=30,
    )
    r.raise_for_status()
    resp_json = r.json()
    if "prompt_id" not in resp_json:
        raise RuntimeError(f"ComfyUI Antwort unerwartet: {resp_json}")
    prompt_id = resp_json["prompt_id"]
    print(f"[Video] queued prompt_id={prompt_id}", flush=True)

    # Video generation takes longer — 20 min timeout
    outputs = _poll_comfyui(base_url, prompt_id, timeout=1200, interval=3)
    if outputs is None:
        raise RuntimeError("Timeout: ComfyUI Video hat nicht rechtzeitig geantwortet")

    video_info = None
    for node_out in outputs.values():
        for key in ("videos", "gifs", "images"):
            items = node_out.get(key, [])
            if items:
                video_info = items[0]
                _dlog(f"found output under key '{key}': {video_info}", tag="Video")
                break
        if video_info:
            break

    if not video_info:
        _dlog(f"Full outputs dump: {outputs}", tag="Video")
        raise RuntimeError("Keine Videodaten in der ComfyUI-Antwort")

    print(f"[Video] downloading: {video_info['filename']}", flush=True)
    return _download_comfyui_file(base_url, video_info, default_mime="video/mp4")


def build_ltx_ia2v_workflow(
    image_filename: str,
    audio_filename: str,
    prompt: str,
    duration_sec: float,
    seed: int,
) -> dict:
    """Load LTX 2.3 ia2v template and patch inputs."""
    template_path = os.path.join(os.path.dirname(__file__), "workflows", "ltx_ia2v.json")
    with open(template_path, encoding="utf-8") as f:
        wf = json.load(f)
    # Image + Audio Inputs
    wf["269"]["inputs"]["image"] = image_filename
    wf["276"]["inputs"]["audio"] = audio_filename
    wf["276"]["inputs"].pop("audioUI", None)
    # Prompt-Text
    wf["340:319"]["inputs"]["value"] = prompt
    # Duration (max 9s laut Workflow)
    wf["340:331"]["inputs"]["value"] = max(1.0, min(9.0, float(duration_sec)))
    # Seed (Main Sampler)
    wf["340:286"]["inputs"]["noise_seed"] = int(seed)
    return wf


def run_comfyui_ia2v(
    image_b64: str,
    audio_path: str,
    prompt: str,
    duration_sec: float = 9.0,
) -> str:
    """Generate talking video (image + audio → video) via LTX 2.3. Returns base64 video."""
    providers = _load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")
    seed = int(time.time()) % (2**32)

    print(f"[IA2V] upload image + audio to {base_url}", flush=True)
    image_filename = upload_image_to_comfyui(image_b64, base_url)
    audio_filename = upload_audio_to_comfyui(audio_path, base_url)
    print(f"[IA2V] image={image_filename} audio={audio_filename}", flush=True)
    print(f"[IA2V] duration={duration_sec}s prompt={prompt[:80]}", flush=True)

    workflow = build_ltx_ia2v_workflow(
        image_filename, audio_filename, prompt, duration_sec, seed,
    )
    r = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": "agentclaw-ia2v"},
        timeout=30,
    )
    r.raise_for_status()
    resp_json = r.json()
    if "prompt_id" not in resp_json:
        raise RuntimeError(f"ComfyUI Antwort unerwartet: {resp_json}")
    prompt_id = resp_json["prompt_id"]
    print(f"[IA2V] queued prompt_id={prompt_id}", flush=True)

    outputs = _poll_comfyui(base_url, prompt_id, timeout=1800, interval=3)
    if outputs is None:
        raise RuntimeError("Timeout: LTX ia2v hat nicht rechtzeitig geantwortet")

    video_info = None
    for node_out in outputs.values():
        for key in ("videos", "gifs", "images"):
            items = node_out.get(key, [])
            if items:
                video_info = items[0]
                break
        if video_info:
            break
    if not video_info:
        raise RuntimeError("Keine Videodaten in der ComfyUI-Antwort")

    print(f"[IA2V] downloading: {video_info['filename']}", flush=True)
    return _download_comfyui_file(base_url, video_info, default_mime="video/mp4")


def run_comfyui_edit(image_b64: str, prompt: str, use_lightning: bool = True) -> str:
    """Run FireRed Image Edit via ComfyUI. Returns base64 data URL."""
    providers = _load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")
    seed = int(time.time()) % (2**32)

    optimized = optimize_prompt_for_image(prompt)
    print(f"[ComfyUI Edit] original: {prompt[:60]}...", flush=True)
    print(f"[ComfyUI Edit] optimized: {optimized[:60]}...", flush=True)

    filename = upload_image_to_comfyui(image_b64, base_url)
    print(f"[ComfyUI Edit] uploaded: {filename}", flush=True)

    workflow = build_firered_edit_workflow(filename, optimized, seed, use_lightning)
    r = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": "agentclaw-edit"},
        timeout=30,
    )
    r.raise_for_status()
    resp_json = r.json()
    if "prompt_id" not in resp_json:
        raise RuntimeError(f"ComfyUI Antwort unerwartet: {resp_json}")
    prompt_id = resp_json["prompt_id"]

    outputs = _poll_comfyui(base_url, prompt_id, timeout=120, interval=2)
    if not outputs:
        raise RuntimeError("Timeout: ComfyUI hat nicht rechtzeitig geantwortet")

    img_info = None
    for node_out in outputs.values():
        imgs = node_out.get("images", [])
        if imgs:
            img_info = imgs[0]
            break

    if not img_info:
        raise RuntimeError("Keine Bilddaten in der ComfyUI-Antwort")

    return _download_comfyui_file(base_url, img_info, default_mime="image/png")


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult
from skills.triggers import IMG_TRIGGERS, VIDEO_TRIGGERS, IMAGE_EDIT_TRIGGERS


class ImageGenSkill(BaseSkill):
    id = "image_gen"
    name = "Image Generation"
    icon = "image"
    description = "Generates images via ComfyUI."
    triggers = [IMG_TRIGGERS.pattern]
    requires = ["comfyui"]

    def matches(self, message: str) -> bool:
        return bool(IMG_TRIGGERS.search(message))

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        try:
            image_b64 = run_comfyui_sync(message)
            return SkillResult(image=image_b64, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)


_YT_URL_SKIP = re.compile(r"youtu(\.be|be\.com)", re.IGNORECASE)


class VideoGenSkill(BaseSkill):
    id = "video_gen"
    name = "Video Generation"
    icon = "videocam"
    description = "Generates videos via ComfyUI."
    triggers = [VIDEO_TRIGGERS.pattern]
    requires = ["comfyui"]

    def matches(self, message: str) -> bool:
        if _YT_URL_SKIP.search(message):
            return False
        return bool(VIDEO_TRIGGERS.search(message))

    def longest_match(self, message: str) -> int:
        if _YT_URL_SKIP.search(message):
            return 0
        return super().longest_match(message)

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        try:
            video_b64 = run_comfyui_video(message)
            return SkillResult(image=video_b64, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)


class ImageEditSkill(BaseSkill):
    id = "image_edit"
    name = "Image Edit"
    icon = "edit"
    description = "Edits images via ComfyUI (FireRed workflow)."
    triggers = [IMAGE_EDIT_TRIGGERS.pattern]
    requires = ["comfyui"]

    def matches(self, message: str) -> bool:
        return bool(IMAGE_EDIT_TRIGGERS.search(message))

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        image_b64 = context.get("image_b64", "")
        if not image_b64:
            return SkillResult(error="Kein Bild für Bildbearbeitung übergeben", skill_used=self.id)
        try:
            result_b64 = run_comfyui_edit(image_b64, message)
            return SkillResult(image=result_b64, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)


# ── Talking Video: Bild + Audio → Video (LTX 2.3) ─────────────────────────────

TALKING_TRIGGERS = re.compile(
    r"\b(talking[- ]?video|lip[- ]?sync|reden(?:des|des)? video|sprech\w* video|"
    r"ia2v|image.?audio.?video|bild.{0,10}audio.{0,10}video|animier\w* sprech\w*)\b",
    re.IGNORECASE,
)

_DURATION_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s|sec|sekunden|seconds)\b", re.IGNORECASE)


def _resolve_local_audio(message: str, attachment_path: str | None) -> str | None:
    """Audio-Pfad aus message (bare filename oder abs path) oder attachment auflösen."""
    if attachment_path and os.path.exists(attachment_path):
        return attachment_path
    m = re.search(
        r"(/[\w\-./]+\.(?:mp3|wav|m4a|flac|ogg))", message, re.IGNORECASE,
    )
    if m and os.path.exists(m.group(1)):
        return m.group(1)
    m = re.search(r"\b([\w\-\.]+\.(?:mp3|wav|m4a|flac|ogg))\b", message, re.IGNORECASE)
    if m:
        candidate = os.path.expanduser(f"~/Downloads/AgentClaw/{m.group(1)}")
        if os.path.exists(candidate):
            return candidate
    return None


class TalkingVideoSkill(BaseSkill):
    id = "talking_video"
    name = "Talking Video"
    icon = "movie_filter"
    description = "Generates video from image + audio via LTX 2.3 (up to 9s, lip-sync)."
    triggers = [TALKING_TRIGGERS.pattern]
    requires = ["comfyui"]

    def matches(self, message: str) -> bool:
        return bool(TALKING_TRIGGERS.search(message))

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        image_b64 = context.get("image_b64", "")
        if not image_b64:
            return SkillResult(
                error="Kein Bild übergeben — bitte Bild anhängen.",
                skill_used=self.id,
            )
        audio_path = _resolve_local_audio(message, context.get("attachment_path"))
        if not audio_path:
            return SkillResult(
                error="Keine Audio-Datei gefunden. Pfad angeben oder in ~/Downloads/AgentClaw/ ablegen.",
                skill_used=self.id,
            )
        # Duration aus Text, sonst Default 9s (Max laut Workflow)
        dm = _DURATION_RX.search(message)
        duration = float(dm.group(1)) if dm else 9.0
        try:
            video_b64 = run_comfyui_ia2v(image_b64, audio_path, message, duration)
            return SkillResult(image=video_b64, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
