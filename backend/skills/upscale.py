"""
backend/skills/upscale.py — ComfyUI Image-Upscale Skill (2x / 4x).

Skaliert Bilder via lokalem ComfyUI-Server (ImageScaleBy-Node).
Unterstützt zwei Faktoren: 2x und 4x. Default: 2x.

Workflow: backend/skills/upscaler_workflow.json
  LoadImage → ImageScaleBy (lanczos) → SaveImage

Eingabe-Format (natürliche Sprache oder strukturiert):
  image: /pfad/zum/bild.png
  factor: 2x | 4x | 2 | 4   (Default: 2x)

Default-Methode: lanczos (schnell, gute Qualität). Wer andere Methoden
braucht (bilinear, bicubic, nearest-exact, bisinc, mitchell), kann den
Workflow direkt anpassen — Skill reicht nur factor durch.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from pathlib import Path

import httpx

from backend.skills import Skill

_WF_PATH = Path(__file__).parent / "upscaler_workflow.json"
_WORKFLOW_TEMPLATE: dict = json.loads(_WF_PATH.read_text())


def _parse(content: str) -> dict:
    """Extrahiert `image` und `factor` aus dem Freitext.

    factor akzeptiert: "2x", "4x", "2", "4" (case-insensitive).
    Bei ungültigem Wert → Default 2x.
    """
    def _find(pattern: str, cast=str, default=None):
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            try:
                return cast(m.group(1).strip())
            except (ValueError, IndexError):
                pass
        return default

    image = _find(r'image\s*[:=]\s*(\S+)')
    # Fallback: hänge einen ComfyUI-Output-Filename (z.B. von vorgeschaltetem
    # comfyui-Skill) — gleiche Heuristik wie ltxvideo.py
    if not image:
        m = re.search(r'\b([\w-]+_\d{5}_\.(?:png|jpg|jpeg|webp))\b', content, re.IGNORECASE)
        if m:
            image = m.group(1)

    factor_raw = _find(r'factor\s*[:=]\s*(\d+x?)', str, "2x") or "2x"
    m = re.match(r'(\d)x?$', str(factor_raw).strip(), re.IGNORECASE)
    factor = int(m.group(1)) if m and int(m.group(1)) in (2, 4) else 2

    return {"image": image, "factor": factor}


class UpscaleSkill(Skill):
    skill_id    = "upscale"
    description = "Skaliert Bilder 2x oder 4x via ComfyUI (ImageScaleBy, lanczos)."

    def __init__(self, endpoint: str = "http://100.125.107.123:8000") -> None:
        self.endpoint = endpoint.rstrip("/")

    async def execute(self, query: str) -> str:
        try:
            return await self._upscale(_parse(query))
        except Exception as e:
            return f"[Upscale] Fehler: {e}"

    # ── Intern ────────────────────────────────────────────────────────────────

    async def _upscale(self, p: dict) -> str:
        if not p["image"]:
            return (
                "[Upscale] Kein Bild angegeben. Syntax: "
                "'image: <pfad/filename> factor: 2x|4x' "
                "(z.B. 'image: ~/bilder/cat.png factor: 4x')"
            )

        img_name = await self._upload_image(p["image"])

        wf = json.loads(json.dumps(_WORKFLOW_TEMPLATE))
        wf["2"]["inputs"]["image"] = img_name
        wf["3"]["inputs"]["scale_by"] = p["factor"]
        # prefix mit Faktor → Output-Dateien leichter zuordenbar
        wf["5"]["inputs"]["filename_prefix"] = f"upscale_{p['factor']}x"

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.endpoint}/prompt", json={"prompt": wf})
            r.raise_for_status()
            pid = r.json()["prompt_id"]

        filename, subfolder = await self._poll(pid)
        sf = f"&subfolder={urllib.parse.quote(subfolder)}" if subfolder else ""
        url = f"{self.endpoint}/view?filename={urllib.parse.quote(filename)}&type=output{sf}"
        return (
            f"[Upscale] {p['factor']}x fertig ✓\n"
            f"Datei: {subfolder + '/' if subfolder else ''}{filename}\n"
            f"URL: {url}"
        )

    async def _upload_image(self, path_or_name: str) -> str:
        """Stellt sicher dass das Bild im ComfyUI input/-Ordner liegt.

        1. Lokaler Pfad → direkt hochladen
        2. ComfyUI-Output-Filename → via /view holen, re-uploaden
        3. Sonst → as-is (liegt evtl. schon im input/)
        """
        p = Path(path_or_name)
        async with httpx.AsyncClient(timeout=30.0) as client:
            if p.exists():
                with p.open("rb") as f:
                    r = await client.post(
                        f"{self.endpoint}/upload/image",
                        files={"image": (p.name, f, "image/png")},
                    )
                    r.raise_for_status()
                    return r.json()["name"]

            name = path_or_name.lstrip("/")
            try:
                r = await client.get(
                    f"{self.endpoint}/view",
                    params={"filename": name, "type": "output"},
                )
                if r.status_code == 200 and r.content:
                    upload = await client.post(
                        f"{self.endpoint}/upload/image",
                        files={"image": (name, r.content, "image/png")},
                    )
                    upload.raise_for_status()
                    return upload.json()["name"]
            except httpx.HTTPError:
                pass

            return path_or_name

    async def _poll(self, pid: str, timeout: int = 60) -> tuple[str, str]:
        """Wartet bis der Job fertig ist, gibt (filename, subfolder) zurück."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(timeout):
                await asyncio.sleep(1)
                hist = (await client.get(f"{self.endpoint}/history/{pid}")).json()
                if not hist:
                    continue
                outputs = hist.get(pid, {}).get("outputs", {})
                node_out = outputs.get("5", {})  # SaveImage-Node ID
                imgs = node_out.get("images", [])
                if imgs:
                    return imgs[0]["filename"], imgs[0].get("subfolder", "")
        raise TimeoutError(f"Upscale timeout für Job {pid}")
