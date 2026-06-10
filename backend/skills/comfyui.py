"""
backend/skills/comfyui.py — ComfyUI Image Generation Skill.

Ruft den lokalen ComfyUI-Server (z-image-turbo Workflow) auf und
gibt den Dateinamen des generierten Bildes zurück.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import urllib.parse

import httpx

from backend.skills import Skill

_WORKFLOW_TEMPLATE = {
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "logpyclaw", "images": ["57:8", 0]},
    },
    "57:30": {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"},
    },
    "57:29": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
    "57:33": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["57:27", 0]}},
    "57:8": {"class_type": "VAEDecode", "inputs": {"samples": ["57:3", 0], "vae": ["57:29", 0]}},
    "57:28": {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"},
    },
    "57:27": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["57:30", 0]}},
    "57:13": {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": 1200, "height": 675, "batch_size": 1},  # 16:9 — matched LTX-Video
    },
    "57:11": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3, "model": ["57:28", 0]}},
    "57:3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 8,
            "cfg": 1,
            "sampler_name": "res_multistep",
            "scheduler": "simple",
            "denoise": 1,
            "model": ["57:11", 0],
            "positive": ["57:27", 0],
            "negative": ["57:33", 0],
            "latent_image": ["57:13", 0],
        },
    },
}


class ComfyUISkill(Skill):
    skill_id = "comfyui"
    description = "Generiert Bilder via lokalem ComfyUI-Server (z-image-turbo, 8 Steps)"

    def __init__(self, endpoint: str = "http://192.168.4.15:8000") -> None:
        self.endpoint = endpoint

    async def execute(self, query: str) -> str:
        try:
            return await self._generate(query)
        except Exception as e:
            return f"[ComfyUI] Fehler: {e}"

    async def _generate(self, prompt: str) -> str:
        wf = json.loads(json.dumps(_WORKFLOW_TEMPLATE))

        # Optionale Inline-Parameter: "width: 1280", "height: 720" — Rest ist Prompt
        clean = prompt
        w = re.search(r'width\s*[:=]\s*(\d+)', prompt, re.I)
        h = re.search(r'height\s*[:=]\s*(\d+)', prompt, re.I)
        if w:
            wf["57:13"]["inputs"]["width"] = int(w.group(1))
            clean = clean.replace(w.group(0), "")
        if h:
            wf["57:13"]["inputs"]["height"] = int(h.group(1))
            clean = clean.replace(h.group(0), "")

        wf["57:27"]["inputs"]["text"] = clean.strip()
        wf["57:3"]["inputs"]["seed"] = random.randint(0, 2**32)

        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{self.endpoint}/prompt",
                json={"prompt": wf},
            )
            r.raise_for_status()
            pid = r.json()["prompt_id"]

        filename = await self._poll(pid)
        url = f"{self.endpoint}/view?filename={urllib.parse.quote(filename)}&type=output"
        return f"[ComfyUI] Bild generiert: {filename}\n{url}"

    async def _poll(self, pid: str, timeout: int = 120) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for _ in range(timeout):
                await asyncio.sleep(1)
                hist = (await client.get(f"{self.endpoint}/history/{pid}")).json()
                if hist:
                    imgs = hist.get(pid, {}).get("outputs", {}).get("9", {}).get("images", [])
                    if imgs:
                        return imgs[0]["filename"]
        raise TimeoutError(f"ComfyUI timeout für job {pid}")
