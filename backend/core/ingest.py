"""
backend/core/ingest.py — Wissenspakete in das semantische Gedächtnis laden.

Nimmt Texte/Dateien (PDF, TXT, MD, HTML, JSON), zerlegt sie in sinnvolle Häppchen
(Chunks) und legt jedes Häppchen als Erinnerung ab. So findet der Recall später die
genaue Passage statt „ein ganzes Buch = ein Vektor".

Bilder werden über das Vision-Modell beschrieben und die Beschreibung abgelegt
(siehe ingest_image).
"""
from __future__ import annotations

import html as _html
import json
import os
import re

_TAG = re.compile(r"<[^>]+>")


def chunk_text(text: str, target: int = 1100, overlap: int = 150) -> list[str]:
    """Teilt Text in ~`target`-Zeichen-Häppchen entlang Absatzgrenzen, mit Überlappung."""
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > target * 1.6:  # sehr langer Absatz → an Sätzen splitten
            for sent in re.split(r"(?<=[.!?])\s+", p):
                if len(buf) + len(sent) > target and buf:
                    chunks.append(buf.strip())
                    buf = buf[-overlap:] if overlap else ""
                buf += " " + sent
        elif len(buf) + len(p) > target and buf:
            chunks.append(buf.strip())
            buf = (buf[-overlap:] + "\n\n" + p) if overlap else p
        else:
            buf = (buf + "\n\n" + p) if buf else p
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def extract_file(path: str) -> str:
    """Extrahiert reinen Text aus einer Datei je nach Endung."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n\n".join((pg.extract_text() or "") for pg in reader.pages)
    raw = open(path, encoding="utf-8", errors="ignore").read()
    if ext in (".html", ".htm"):
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        return _html.unescape(_TAG.sub(" ", raw))
    if ext == ".json":
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
        except Exception:
            return raw
    return raw  # txt, md, …


async def ingest_text(memory, text: str, scope: str, kind: str = "knowledge",
                      source: str = "", base_meta: dict | None = None) -> int:
    """Zerlegt Text in Häppchen und legt sie im Scope ab. Gibt die Häppchen-Zahl zurück."""
    chunks = chunk_text(text)
    n = len(chunks)
    for i, ch in enumerate(chunks):
        meta = {**(base_meta or {}), "source": source, "chunk": i, "chunks": n}
        await memory.remember(ch, kind=kind, scope=scope, meta=meta)
    return n


async def ingest_file(memory, path: str, scope: str, kind: str = "knowledge",
                      base_meta: dict | None = None) -> dict:
    """Lädt eine Datei (PDF/TXT/MD/HTML/JSON) als Wissenspaket in einen Scope."""
    text = extract_file(path)
    name = os.path.basename(path)
    n = await ingest_text(memory, text, scope, kind, source=name, base_meta=base_meta)
    return {"file": name, "scope": scope, "chunks": n, "chars": len(text)}
