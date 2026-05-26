"""
backend/skills/ltxvideo.py — LTX 2.3 Image-to-Video / Text-to-Video Skill.

Lädt ein Bild hoch, queued den LTX-2.3-Workflow in ComfyUI und gibt
die Video-URL zurück.

Eingabe-Format (natürliche Sprache oder strukturiert):
  image: /pfad/zum/bild.png
  prompt: Person läuft durch Wald, Sonnenuntergang
  duration: 5          (Sekunden, default 5)
  width: 1280          (default 1280)
  height: 720          (default 720)
  fps: 25              (default 25)
  seed: 42             (default: zufällig)

Ohne image: → Text-to-Video-Modus (bypass=true).
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

_WF_PATH = Path(__file__).parent / "ltxvideo_workflow.json"
_WORKFLOW_TEMPLATE: dict = json.loads(_WF_PATH.read_text())


def _parse(content: str) -> dict:
    """Extrahiert Parameter aus dem Freitext."""
    def _find(pattern: str, cast=str, default=None):
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            try:
                return cast(m.group(1).strip())
            except (ValueError, IndexError):
                pass
        return default

    image   = _find(r'image\s*[:=]\s*(\S+)')
    # Fallback: ComfyUI-Filename im Vorgänger-Kontext (Bild → Video Chaining)
    if not image:
        m = re.search(r'\b([\w-]+_\d{5}_\.(?:png|jpg|jpeg|webp))\b', content, re.IGNORECASE)
        if m:
            image = m.group(1)
    prompt  = _find(r'prompt\s*[:=]\s*(.+?)(?:\n|duration|width|height|fps|seed|$)', default="")
    # Fallback: kein Keyword → gesamter Content ist Prompt (wenn kein image:)
    if not prompt and not image:
        prompt = content.strip()
    if not prompt:
        prompt = content.strip()

    return {
        "image":    image,
        "prompt":   prompt,
        "duration": _find(r'duration\s*[:=]\s*(\d+)', int, 5),
        "width":    _find(r'width\s*[:=]\s*(\d+)',    int, 1280),
        "height":   _find(r'height\s*[:=]\s*(\d+)',   int, 720),
        "fps":      _find(r'fps\s*[:=]\s*(\d+)',       int, 25),
        "seed":     _find(r'seed\s*[:=]\s*(\d+)',      int, None),
    }


class LTXVideoSkill(Skill):
    skill_id    = "ltxvideo"
    description = "Generiert Videos via LTX 2.3 in ComfyUI (Image-to-Video oder Text-to-Video)"

    def __init__(self, endpoint: str = "http://192.168.4.15:8000") -> None:
        self.endpoint = endpoint.rstrip("/")

    async def execute(self, query: str) -> str:
        try:
            return await self._generate(_parse(query))
        except Exception as e:
            return f"[LTXVideo] Fehler: {e}"

    # ── Intern ────────────────────────────────────────────────────────────────

    async def _generate(self, p: dict) -> str:
        wf = json.loads(json.dumps(_WORKFLOW_TEMPLATE))

        # Prompt
        wf["320:319"]["inputs"]["value"] = p["prompt"]

        # Größe / Timing
        wf["320:312"]["inputs"]["value"] = p["width"]
        wf["320:299"]["inputs"]["value"] = p["height"]
        wf["320:301"]["inputs"]["value"] = p["duration"]
        wf["320:300"]["inputs"]["value"] = p["fps"]

        # Seeds
        seed = p["seed"] if p["seed"] is not None else random.randint(0, 2**32)
        wf["320:276"]["inputs"]["noise_seed"] = seed
        wf["320:277"]["inputs"]["noise_seed"] = seed + 1

        # Image-to-Video vs Text-to-Video
        if p["image"]:
            img_name = await self._upload_image(p["image"])
            wf["269"]["inputs"]["image"] = img_name
            wf["320:302"]["inputs"]["value"] = False   # i2v
        else:
            wf["320:302"]["inputs"]["value"] = True    # t2v (bypass image nodes)
            wf["269"]["inputs"]["image"] = ""

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.endpoint}/prompt", json={"prompt": wf})
            r.raise_for_status()
            pid = r.json()["prompt_id"]

        filename, subfolder = await self._poll(pid)
        sf = f"&subfolder={urllib.parse.quote(subfolder)}" if subfolder else ""
        url = f"{self.endpoint}/view?filename={urllib.parse.quote(filename)}&type=output{sf}"
        return (
            f"[LTXVideo] Video generiert ✓\n"
            f"Datei: {subfolder}/{filename}\n"
            f"URL: {url}"
        )

    async def _upload_image(self, path_or_name: str) -> str:
        """Sorgt dafür dass das Bild im ComfyUI input/-Ordner liegt.

        - Lokale Datei: hochladen
        - ComfyUI-Output-Filename: via /view holen und re-uploaden
        - Sonst: filename as-is zurückgeben (angenommen schon im input/)
        """
        p = Path(path_or_name)
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Lokale Datei → direkt hochladen
            if p.exists():
                with p.open("rb") as f:
                    r = await client.post(
                        f"{self.endpoint}/upload/image",
                        files={"image": (p.name, f, "image/png")},
                    )
                    r.raise_for_status()
                    return r.json()["name"]

            # 2. Könnte ein ComfyUI-Output-Filename sein → /view holen, re-uploaden
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

            # 3. Fallback: as-is (liegt vielleicht schon im input/)
            return path_or_name

    async def _poll(self, pid: str, timeout: int = 600) -> tuple[str, str]:
        """Wartet bis der Job fertig ist, gibt (filename, subfolder) zurück."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(timeout):
                await asyncio.sleep(2)
                hist = (await client.get(f"{self.endpoint}/history/{pid}")).json()
                if not hist:
                    continue
                outputs = hist.get(pid, {}).get("outputs", {})
                node_out = outputs.get("75", {})
                # LTX SaveVideo legt MP4s in "images" ab — ältere Versionen in "videos"/"gifs"
                videos = (
                    node_out.get("videos")
                    or node_out.get("gifs")
                    or [v for v in node_out.get("images", []) if v.get("filename", "").lower().endswith((".mp4", ".webm", ".mov"))]
                )
                if videos:
                    v = videos[0]
                    return v["filename"], v.get("subfolder", "")
        raise TimeoutError(f"LTXVideo timeout für Job {pid}")
