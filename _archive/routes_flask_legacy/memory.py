"""
routes/memory.py — Memory API, Aktivitäts-Feed, Dokument-Upload.
"""
import os
import uuid
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request

from core.config import BASE_DIR
from core.memory import collection_name, ensure_collection, get_qdrant
from core.state import _ACTIVITY, _activity_lock
from storage.agents import load_agents
from storage.providers import load_providers

bp = Blueprint("memory", __name__)


def _get_agent_skills(agent_id):
    agents = load_agents()
    for a in agents:
        if a["id"] == agent_id:
            return set(a.get("skills", []))
    return set()


def _store_document_vector(agent_id, filename, text, embedding, file_data=None):
    """Store document embedding in Qdrant and save file to disk."""
    client = get_qdrant()
    if not client:
        return

    upload_dir = os.path.join(BASE_DIR, "static", "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    file_path = ""
    if file_data:
        unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
        save_path = os.path.join(upload_dir, unique_name)
        with open(save_path, "wb") as f:
            f.write(file_data)
        file_path = f"/static/uploads/{unique_name}"

    from qdrant_client.models import Distance, PointStruct, VectorParams

    name = collection_name(agent_id)
    existing = [c.name for c in client.get_collections().collections]
    if name not in existing:
        client.create_collection(
            name,
            vectors_config=VectorParams(size=len(embedding), distance=Distance.COSINE),
        )

    client.upsert(
        collection_name=name,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "filename": filename,
                    "file_path": file_path,
                    "text": text,
                    "type": "document",
                    "ts": datetime.now().isoformat(),
                },
            )
        ],
    )
    print(f"[Document Memory] stored {filename} (path: {file_path}) for agent {agent_id}", flush=True)


@bp.route("/api/activity", methods=["GET"])
def get_activity():
    with _activity_lock:
        return jsonify(dict(_ACTIVITY))


@bp.route("/api/memory/<agent_id>", methods=["GET"])
def memory_info(agent_id):
    client = get_qdrant()
    if not client:
        return jsonify({"error": "Qdrant nicht verfügbar", "count": 0})
    try:
        name = collection_name(agent_id)
        existing = [c.name for c in client.get_collections().collections]
        if name not in existing:
            return jsonify({"count": 0})
        info = client.get_collection(name)
        return jsonify({"count": info.points_count})
    except Exception as e:
        return jsonify({"error": str(e), "count": 0})


@bp.route("/api/memory/<agent_id>", methods=["DELETE"])
def memory_clear(agent_id):
    client = get_qdrant()
    if not client:
        return jsonify({"error": "Qdrant nicht verfügbar"}), 503
    try:
        name = collection_name(agent_id)
        existing = [c.name for c in client.get_collections().collections]
        if name in existing:
            client.delete_collection(name)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/memory/<agent_id>/document", methods=["POST"])
def memory_upload_document(agent_id):
    """Upload PDF or image — store as vector in Qdrant."""
    print(f"[Memory] Uploading document for agent {agent_id}", flush=True)
    if "document_memory" not in _get_agent_skills(agent_id):
        print(f"[Memory] Skill not active for agent {agent_id}", flush=True)
        return jsonify({"error": "document_memory skill not active"}), 403

    if "file" not in request.files:
        print("[Memory] No file in request.files", flush=True)
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        print("[Memory] Empty filename", flush=True)
        return jsonify({"error": "Empty filename"}), 400

    filename = file.filename.lower()
    file_data = file.read()
    print(f"[Memory] Received file: {filename} ({len(file_data)} bytes)", flush=True)

    is_pdf = filename.endswith(".pdf")
    is_image = filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))

    if not (is_pdf or is_image):
        print(f"[Memory] Unsupported file type: {filename}", flush=True)
        return jsonify({"error": "Only PDF and images supported"}), 400

    providers = load_providers()
    google_key = providers.get("google_api", {}).get("api_key", "")
    print(f"[Memory] Using google_key: {bool(google_key)}", flush=True)

    try:
        if google_key and is_pdf:
            print("[Memory] Processing PDF with Google API", flush=True)
            import base64
            try:
                import PyPDF2
                from io import BytesIO
                reader = PyPDF2.PdfReader(BytesIO(file_data))
                text = "\n".join([page.extract_text() or "" for page in reader.pages])
            except Exception:
                text = f"[Image/PDF file: {filename}]"

            resp = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent",
                headers={"Authorization": f"Bearer {google_key}"},
                json={"content": {"role": "user", "parts": [{"text": text[:2000]}]}},
                timeout=30,
            )
            if resp.ok:
                embedding = resp.json()["embedding"]["values"]
                _store_document_vector(agent_id, filename, text[:1000], embedding, file_data=file_data)
                return jsonify({"ok": True, "filename": filename, "type": "pdf"})

        elif is_image:
            print("[Memory] Processing Image", flush=True)
            text = f"[Image: {filename}]"
            if google_key:
                print("[Memory] Processing Image with Google API", flush=True)
                resp = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent",
                    headers={"Authorization": f"Bearer {google_key}"},
                    json={"content": {"role": "user", "parts": [{"text": text}]}},
                    timeout=30,
                )
                if resp.ok:
                    embedding = resp.json()["embedding"]["values"]
                    _store_document_vector(agent_id, filename, text, embedding, file_data=file_data)
                    return jsonify({"ok": True, "filename": filename, "type": "image"})

        # Fallback to Ollama embeddings
        print("[Memory] Falling back to Ollama embeddings", flush=True)
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        text = f"[Document: {filename}]"
        resp = requests.post(
            f"{ollama_url}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text},
            timeout=30,
        )
        if resp.ok:
            embedding = resp.json()["embedding"]
            _store_document_vector(agent_id, filename, text, embedding, file_data=file_data)
            return jsonify({"ok": True, "filename": filename, "type": "fallback"})
        else:
            print(f"[Memory] Ollama error: {resp.text}", flush=True)

        return jsonify({"error": "No embedding provider available"}), 500

    except Exception as e:
        import traceback
        print(f"[Memory] Exception: {traceback.format_exc()}", flush=True)
        return jsonify({"error": str(e)}), 500
