"""
api/comfyui.py — ComfyUI Direkt-API.
Portiert aus app.py: /api/comfyui/config, /api/comfyui/generate
"""
import asyncio
import base64
import logging
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from storage.providers import load_providers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/comfyui", tags=["comfyui"])


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    width: int = Field(default=1024, ge=64, le=4096)
    height: int = Field(default=1024, ge=64, le=4096)
    seed: int | None = None


@router.get("/config")
def comfyui_config():
    """ComfyUI-Konfiguration + Basis-Workflow abrufen."""
    from skills.comfyui import build_z_image_turbo_workflow
    cfg = load_providers().get("comfyui", {})
    return {
        "url": cfg.get("url", "http://localhost:8188"),
        "workflow": build_z_image_turbo_workflow("__PROMPT__", 0),
    }


@router.post("/generate")
async def comfyui_generate(req: GenerateRequest):
    """
    Bild über ComfyUI generieren.
    Blockierendes Polling (bis 6min) → in Executor auslagern.
    """
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, lambda: _generate_sync(req))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ComfyUI generate Fehler")
        raise HTTPException(500, str(e))


def _generate_sync(req: GenerateRequest) -> dict:
    """Synchrone ComfyUI-Generierung (läuft im ThreadPool)."""
    import requests as reqs
    from skills.comfyui import build_z_image_turbo_workflow

    providers = load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")
    seed = req.seed if req.seed is not None else int(time.time()) % (2 ** 32)

    workflow = build_z_image_turbo_workflow(req.prompt, seed)

    # Prompt einreihen
    try:
        r = reqs.post(
            f"{base_url}/prompt",
            json={"prompt": workflow, "client_id": "agentclaw"},
            timeout=30,
        )
        r.raise_for_status()
        resp_json = r.json()
    except Exception as e:
        raise HTTPException(502, f"ComfyUI nicht erreichbar: {e}")

    if "prompt_id" not in resp_json:
        raise HTTPException(500, f"ComfyUI Antwort unerwartet: {resp_json}")

    prompt_id = resp_json["prompt_id"]
    logger.info("ComfyUI: prompt_id=%s", prompt_id)

    # Polling bis Completion (max 360s)
    deadline = time.time() + 360
    outputs = None
    while time.time() < deadline:
        time.sleep(2)
        try:
            h = reqs.get(f"{base_url}/history/{prompt_id}", timeout=10)
            entry = h.json().get(prompt_id, {})
            if entry.get("status", {}).get("completed"):
                outputs = entry.get("outputs", {})
                break
        except Exception:
            pass  # kurz nochmal versuchen

    if not outputs:
        raise HTTPException(504, "Timeout: ComfyUI hat nicht rechtzeitig geantwortet")

    # Erstes Bild aus Outputs extrahieren
    img_info = None
    for node_out in outputs.values():
        imgs = node_out.get("images", [])
        if imgs:
            img_info = imgs[0]
            break

    if not img_info:
        raise HTTPException(500, "Keine Bilddaten in der ComfyUI-Antwort")

    filename = img_info["filename"]
    subfolder = img_info.get("subfolder", "")
    img_type = img_info.get("type", "output")
    params = f"filename={filename}&type={img_type}"
    if subfolder:
        params += f"&subfolder={subfolder}"

    img_r = reqs.get(f"{base_url}/view?{params}", timeout=30)
    img_r.raise_for_status()
    mime = img_r.headers.get("Content-Type", "image/png").split(";")[0]
    b64 = base64.b64encode(img_r.content).decode()
    logger.info("ComfyUI: Bild fertig — %s (%dKB)", filename, len(img_r.content) // 1024)
    return {"image": f"data:{mime};base64,{b64}", "filename": filename}
