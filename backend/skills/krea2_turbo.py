"""
backend/skills/krea2_turbo.py — Krea2-Turbo Image Generation Skill.

Nutzt das krea2_turbo_fp8_scaled UNet + krea2_darkbrush LoRA + qwen3vl-4b CLIP
+ qwen_image_vae aus /Users/jeuner/Downloads/image_krea2_turbo_t2i.json.

Macht vor dem Submit einen Health-Pre-Check: ertastet /system_stats und
gibt eine klare Fehlermeldung zurück, falls ComfyUI nicht erreichbar ist
(vermeidet 60s Polling-Timeout ohne Status).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import urllib.parse
from pathlib import Path

import httpx

from backend.skills import Skill


# Template-Pfad (User-Downloads, wird mit ausgeliefert)
_TEMPLATE_PATH = Path("/Users/jeuner/Downloads/image_krea2_turbo_t2i.json")


def _load_template() -> dict:
    with _TEMPLATE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


class Krea2TurboSkill(Skill):
    """Generiert Bilder via ComfyUI krea2_turbo Workflow (1:1, 2 MP, 8 steps, LoRA an)."""

    skill_id = "krea2_turbo"
    description = "Krea2-Turbo Bildgenerierung (qwen3vl prompt-refine + darkbrush LoRA)"

    def __init__(self, endpoint: str = "http://100.125.107.123:8000", timeout: int = 120) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    async def execute(self, query: str) -> str:
        try:
            return await self._generate(query)
        except Exception as e:
            # 2026-07-05: type+repr dazu, damit leere Exception-Strings nicht
            # still verschluckt werden (war transienter Bug)
            return f"[krea2_turbo] Fehler: {type(e).__name__}: {e!r}"

    async def _healthcheck(self) -> str | None:
        """Pre-Check: ComfyUI erreichbar? Gibt Fehlermeldung oder None."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.endpoint}/system_stats")
                r.raise_for_status()
                return None
        except httpx.ConnectError:
            return f"ComfyUI nicht erreichbar unter {self.endpoint} (Connection refused)"
        except httpx.TimeoutException:
            return f"ComfyUI Timeout bei {self.endpoint}/system_stats (>5s)"
        except httpx.HTTPStatusError as e:
            return f"ComfyUI HTTP {e.response.status_code} unter {self.endpoint}"
        except Exception as e:
            return f"ComfyUI Health-Check Fehler: {type(e).__name__}: {e}"

    async def _generate(self, prompt: str) -> str:
        err = await self._healthcheck()
        if err:
            return f"[krea2_turbo] ABGEBROCHEN — {err}"

        wf = json.loads(json.dumps(_load_template()))

        # Optionale Inline-Parameter
        clean = prompt
        w = re.search(r"width\s*[:=]\s*(\d+)", prompt, re.I)
        h = re.search(r"height\s*[:=]\s*(\d+)", prompt, re.I)
        if w:
            wf["49"]["inputs"]["width"] = int(w.group(1)) if "width" in wf["49"]["inputs"] else int(w.group(1))
            clean = clean.replace(w.group(0), "")
        if h:
            if "height" in wf["49"]["inputs"]:
                wf["49"]["inputs"]["height"] = int(h.group(1))
            clean = clean.replace(h.group(0), "")

        # User-Prompt in Node 30:19 einsetzen (default = "Bei dem Porträt...")
        wf["30:19"]["inputs"]["value"] = clean.strip()

        # Random-Seed
        wf["30:3"]["inputs"]["seed"] = random.randint(0, 2**48)

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.endpoint}/prompt", json={"prompt": wf})
            r.raise_for_status()
            pid = r.json()["prompt_id"]

        filename = await self._poll(pid)
        url = f"{self.endpoint}/view?filename={urllib.parse.quote(filename)}&type=output"
        return f"[krea2_turbo] Bild generiert: {filename}\n{url}"

    async def _poll(self, pid: str) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for _ in range(self.timeout):
                await asyncio.sleep(1)
                h = (await client.get(f"{self.endpoint}/history/{pid}")).json()
                if h:
                    imgs = h.get(pid, {}).get("outputs", {}).get("29", {}).get("images", [])
                    if imgs:
                        return imgs[0]["filename"]
        raise TimeoutError(f"ComfyUI timeout für job {pid} nach {self.timeout}s")
