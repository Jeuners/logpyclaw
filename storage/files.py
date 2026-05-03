"""
storage/files.py — Persistierung von Skill-Output-Bildern als Files.

Bilder werden nicht mehr als Base64-data-URI in der DB gespeichert (768 MB Bloat),
sondern unter static/uploads/skills/<id>.<ext> abgelegt. Die DB hält nur den
URL-Pfad (z.B. "/static/uploads/skills/abc123.png"), den der Frontend direkt
in <img src=...> rendern kann.
"""
import base64
import logging
import os
import re
import uuid

from core.config import BASE_DIR

logger = logging.getLogger(__name__)

SKILLS_UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads", "skills")

_DATA_URI_RX = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$", re.DOTALL)
_MIME_TO_EXT = {
    "image/png":  ".png",
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/webp": ".webp",
    "image/gif":  ".gif",
    "image/bmp":  ".bmp",
}


def is_data_uri(s) -> bool:
    return isinstance(s, str) and s.startswith("data:") and ";base64," in s


def persist_data_uri(data_uri: str, name_hint: str = "") -> str | None:
    """Schreibt einen base64 data-URI als Datei und gibt den /static/...-Pfad zurück.

    Bei Fehler oder Nicht-data-URI-Input: None.
    Idempotent — Nicht-data-URI-Strings werden unverändert durchgereicht (siehe persist_image_field).
    """
    if not is_data_uri(data_uri):
        return None
    m = _DATA_URI_RX.match(data_uri)
    if not m:
        return None
    mime = m.group("mime").lower()
    ext  = _MIME_TO_EXT.get(mime, ".bin")
    try:
        raw = base64.b64decode(m.group("b64"), validate=False)
    except Exception as e:
        logger.warning("persist_data_uri: base64-decode fehlgeschlagen: %s", e)
        return None

    os.makedirs(SKILLS_UPLOAD_DIR, exist_ok=True)
    safe_hint = re.sub(r"[^\w\-]", "_", name_hint)[:40] if name_hint else ""
    uid = uuid.uuid4().hex[:10]
    fname = f"{safe_hint}_{uid}{ext}" if safe_hint else f"{uid}{ext}"
    fname = fname.lstrip("_")
    path = os.path.join(SKILLS_UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        f.write(raw)
    return f"/static/uploads/skills/{fname}"


def persist_image_field(value, name_hint: str = "") -> str:
    """Wrapper für ein Image-Feld in einem Task/Message-Dict.

    - data:image/...;base64,... → schreibt File, gibt /static/...-Pfad zurück
    - bestehender /static/... oder http(s)://... oder leerer String → unverändert
    - None → ""
    """
    if not value:
        return ""
    if is_data_uri(value):
        path = persist_data_uri(value, name_hint=name_hint)
        return path or ""
    return value
