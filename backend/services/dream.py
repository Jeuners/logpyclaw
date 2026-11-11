"""
backend/services/dream.py — Täglicher Traum-Service.

Jeder LLM-Agent generiert einmal täglich einen Traum als Text-Prompt,
der via ComfyUI in ein Bild verwandelt wird. Bilder landen in dreams/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DREAMS_DIR = Path(__file__).parent.parent.parent / "dreams"


async def _ask_agent_dream(agent, conductor) -> str | None:
    """Fragt einen LLM-Agenten nach seinem Traum."""
    from backend.core.protocol import Message, external_ref, new_mission_id
    mission_id = new_mission_id()
    conductor.store.register_mission(mission_id, {
        "mission_id": mission_id,
        "title": f"dream:{agent.agent_id}",
        "state": "running",
        "started_at": time.time(),
    })
    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("dream_service"),
        recipient=agent.agent_id,
        content=(
            "Du träumst. Beschreibe deinen heutigen Traum in einem einzigen Satz "
            "als englischen Bildprompt für eine KI (keine Anführungszeichen, "
            "kein Präfix, nur der Prompt)."
        ),
    )
    try:
        resp = await asyncio.wait_for(conductor.dispatch(msg), timeout=60.0)
        result = resp.payload.get("result", "").strip()
        conductor.store.update_mission(mission_id, state="completed")
        return result if result else None
    except Exception as e:
        logger.warning("dream prompt failed for %s: %s", agent.agent_id, e)
        conductor.store.update_mission(mission_id, state="failed")
        return None


async def _generate_image(prompt: str, comfyui_url: str, out_path: Path) -> bool:
    """Ruft ComfyUI auf und speichert das Bild."""
    import random

    # Minimaler z-image-turbo Workflow
    wf = {
        "9":     {"class_type": "SaveImage",            "inputs": {"filename_prefix": "dream", "images": ["57:8", 0]}},
        "57:30": {"class_type": "CLIPLoader",            "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"}},
        "57:29": {"class_type": "VAELoader",             "inputs": {"vae_name": "ae.safetensors"}},
        "57:33": {"class_type": "ConditioningZeroOut",   "inputs": {"conditioning": ["57:27", 0]}},
        "57:8":  {"class_type": "VAEDecode",             "inputs": {"samples": ["57:3", 0], "vae": ["57:29", 0]}},
        "57:28": {"class_type": "UNETLoader",            "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"}},
        "57:27": {"class_type": "CLIPTextEncode",        "inputs": {"text": prompt, "clip": ["57:30", 0]}},
        "57:13": {"class_type": "EmptySD3LatentImage",   "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "57:11": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3, "model": ["57:28", 0]}},
        "57:3":  {"class_type": "KSampler",              "inputs": {
            "seed": random.randint(0, 2**32), "steps": 8, "cfg": 1,
            "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1,
            "model": ["57:11", 0], "positive": ["57:27", 0],
            "negative": ["57:33", 0], "latent_image": ["57:13", 0],
        }},
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{comfyui_url}/prompt", json={"prompt": wf})
            r.raise_for_status()
            pid = r.json()["prompt_id"]

        # Polling
        async with httpx.AsyncClient(timeout=10.0) as client:
            for _ in range(120):
                await asyncio.sleep(1)
                hist = (await client.get(f"{comfyui_url}/history/{pid}")).json()
                if hist:
                    imgs = hist.get(pid, {}).get("outputs", {}).get("9", {}).get("images", [])
                    if imgs:
                        fn = imgs[0]["filename"]
                        url = f"{comfyui_url}/view?filename={urllib.parse.quote(fn)}&type=output"
                        img_data = (await client.get(url)).content
                        out_path.write_bytes(img_data)
                        return True
    except Exception as e:
        logger.error("ComfyUI dream image failed: %s", e)
    return False


async def run_dream_cycle(conductor, comfyui_url: str) -> list[dict]:
    """Hauptfunktion: alle LLM-Agenten träumen, Bilder werden gespeichert."""
    from backend.agents.llm_agent import LLMAgent

    DREAMS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    day_dir = DREAMS_DIR / date_str
    day_dir.mkdir(exist_ok=True)

    results = []
    agents = [a for a in conductor.list_agents() if isinstance(a, LLMAgent)]

    for agent in agents:
        logger.info("dream cycle: asking %s", agent.agent_id)
        prompt = await _ask_agent_dream(agent, conductor)
        if not prompt:
            continue

        slug = agent.agent_id.replace(":", "_").replace("/", "_")
        img_path = day_dir / f"{slug}.png"
        meta_path = day_dir / f"{slug}.json"

        ok = await _generate_image(prompt, comfyui_url, img_path)

        entry = {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "date": date_str,
            "prompt": prompt,
            "image": str(img_path.relative_to(DREAMS_DIR.parent)) if ok else None,
        }
        meta_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2))
        results.append(entry)
        logger.info("dream: %s → %s", agent.name, prompt[:60])

    return results
