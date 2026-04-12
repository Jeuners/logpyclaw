"""
api/upload.py — Universeller Datei-Upload (Bilder, Videos, Audio, Dokumente).
"""
import base64
import logging
import os
import re
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

from core.config import BASE_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["upload"])

ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm", ".opus"}
ALLOWED_DOC   = {".pdf", ".txt", ".md", ".csv", ".json"}
ALL_ALLOWED   = ALLOWED_IMAGE | ALLOWED_VIDEO | ALLOWED_AUDIO | ALLOWED_DOC


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Gibt zurück: { type, data_url, filename, path, size_mb }"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Kein Dateiname")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALL_ALLOWED:
        raise HTTPException(status_code=400, detail=f"Dateityp {ext} nicht erlaubt")

    upload_dir = os.path.join(BASE_DIR, "static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    uid = uuid.uuid4().hex[:8]
    safe_name = re.sub(r"[^\w\-.]", "_", file.filename)
    unique_name = f"{uid}_{safe_name}"
    save_path = os.path.join(upload_dir, unique_name)

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    size_mb = round(os.path.getsize(save_path) / 1024 / 1024, 2)
    static_path = f"/static/uploads/{unique_name}"

    if ext in ALLOWED_IMAGE:
        ftype = "image"
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        data_url = f"data:{mime};base64," + base64.b64encode(content).decode()
    elif ext in ALLOWED_VIDEO:
        ftype = "video"
        data_url = static_path
    elif ext in ALLOWED_AUDIO:
        ftype = "audio"
        data_url = static_path
    else:
        ftype = "document"
        data_url = static_path

    logger.info("Upload %s: %s (%.2f MB)", ftype, unique_name, size_mb)
    return {
        "type": ftype,
        "data_url": data_url,
        "filename": file.filename,
        "path": save_path,
        "static_path": static_path,
        "size_mb": size_mb,
    }
