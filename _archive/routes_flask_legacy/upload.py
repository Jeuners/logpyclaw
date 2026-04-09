"""
routes/upload.py — Universeller Datei-Upload (Bilder, Videos, Audio, Dokumente).
"""
import base64
import os
import re
import uuid

from flask import Blueprint, jsonify, request

from core.config import BASE_DIR

bp = Blueprint("upload", __name__)

ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
ALLOWED_DOC   = {".pdf", ".txt", ".md", ".csv", ".json"}
ALL_ALLOWED   = ALLOWED_IMAGE | ALLOWED_VIDEO | ALLOWED_AUDIO | ALLOWED_DOC


@bp.route("/api/upload", methods=["POST"])
def upload_file():
    """Gibt zurück: { type, data_url, filename, path, size_mb }"""
    if "file" not in request.files:
        return jsonify({"error": "Keine Datei"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Kein Dateiname"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALL_ALLOWED:
        return jsonify({"error": f"Dateityp {ext} nicht erlaubt"}), 400

    upload_dir = os.path.join(BASE_DIR, "static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    uid = uuid.uuid4().hex[:8]
    safe_name = re.sub(r"[^\w\-.]", "_", file.filename)
    unique_name = f"{uid}_{safe_name}"
    save_path = os.path.join(upload_dir, unique_name)
    file.save(save_path)

    size_mb = round(os.path.getsize(save_path) / 1024 / 1024, 2)
    static_path = f"/static/uploads/{unique_name}"

    if ext in ALLOWED_IMAGE:
        ftype = "image"
        with open(save_path, "rb") as f:
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            data_url = f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    elif ext in ALLOWED_VIDEO:
        ftype = "video"
        data_url = static_path
    elif ext in ALLOWED_AUDIO:
        ftype = "audio"
        data_url = static_path
    else:
        ftype = "document"
        data_url = static_path

    print(f"[Upload] {ftype}: {unique_name} ({size_mb} MB)", flush=True)
    return jsonify({
        "type": ftype,
        "data_url": data_url,
        "filename": file.filename,
        "path": save_path,
        "static_path": static_path,
        "size_mb": size_mb,
    })
