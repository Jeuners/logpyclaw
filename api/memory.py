"""
api/memory.py — Memory API (Qdrant Vector Store) und Aktivitäts-Feed.
"""
import logging
import os
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from core.config import BASE_DIR
from core.memory import collection_name, get_qdrant
from core.state import _ACTIVITY, _activity_lock
from storage.agents import load_agents
from storage.providers import load_providers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["memory"])


def _get_agent_skills(agent_id: str) -> set:
    agents = load_agents()
    for a in agents:
        if a["id"] == agent_id:
            return set(a.get("skills", []))
    return set()


def _store_document_vector(agent_id: str, filename: str, text: str, embedding: list, file_data: bytes = None):
    """Speichert Dokument-Embedding in Qdrant und Datei auf Disk."""
    client = get_qdrant()
    if not client:
        raise RuntimeError("Qdrant nicht verfügbar")

    file_path = ""
    if file_data:
        upload_dir = os.path.join(BASE_DIR, "static", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
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
    logger.info("Document stored: %s for agent %s (path: %s)", filename, agent_id, file_path)


@router.get("/activity")
async def get_activity():
    with _activity_lock:
        return dict(_ACTIVITY)


@router.get("/memory/{agent_id}")
async def memory_info(agent_id: str):
    client = get_qdrant()
    if not client:
        return JSONResponse(
            status_code=503,
            content={"error": "Qdrant nicht verfügbar", "count": 0},
        )
    try:
        name = collection_name(agent_id)
        existing = [c.name for c in client.get_collections().collections]
        if name not in existing:
            return {"count": 0}
        info = client.get_collection(name)
        return {"count": info.points_count}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"error": str(e), "count": 0},
        )


@router.delete("/memory/{agent_id}")
async def memory_clear(agent_id: str):
    client = get_qdrant()
    if not client:
        raise HTTPException(status_code=503, detail="Qdrant nicht verfügbar")
    try:
        name = collection_name(agent_id)
        existing = [c.name for c in client.get_collections().collections]
        if name in existing:
            client.delete_collection(name)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/{agent_id}/document")
async def memory_upload_document(agent_id: str, file: UploadFile = File(...)):
    """Upload PDF oder Bild — als Vektor in Qdrant speichern."""
    if "document_memory" not in _get_agent_skills(agent_id):
        raise HTTPException(status_code=403, detail="document_memory skill not active")

    if not file.filename:
        raise HTTPException(status_code=400, detail="Empty filename")

    filename = file.filename.lower()
    file_data = await file.read()

    is_pdf = filename.endswith(".pdf")
    is_image = filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))

    if not (is_pdf or is_image):
        raise HTTPException(status_code=400, detail="Only PDF and images supported")

    providers = load_providers()
    google_key = providers.get("google_api", {}).get("api_key", "")

    try:
        # Qdrant-Verfügbarkeit vorab prüfen
        from core.memory import get_qdrant
        if get_qdrant() is None:
            raise HTTPException(status_code=503, detail="Qdrant nicht verfügbar")

        embedding = None

        if google_key and is_pdf:
            import base64
            try:
                import PyPDF2
                from io import BytesIO
                reader = PyPDF2.PdfReader(BytesIO(file_data))
                text = "\n".join([page.extract_text() or "" for page in reader.pages])
            except Exception:
                text = f"[Image/PDF file: {filename}]"

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent",
                    headers={"Authorization": f"Bearer {google_key}"},
                    json={"content": {"role": "user", "parts": [{"text": text[:2000]}]}},
                )
            if resp.is_success:
                embedding = resp.json()["embedding"]["values"]
                _store_document_vector(agent_id, filename, text[:1000], embedding, file_data=file_data)
                return {"ok": True, "filename": filename, "type": "pdf"}

        elif is_image:
            text = f"[Image: {filename}]"
            if google_key:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent",
                        headers={"Authorization": f"Bearer {google_key}"},
                        json={"content": {"role": "user", "parts": [{"text": text}]}},
                    )
                if resp.is_success:
                    embedding = resp.json()["embedding"]["values"]
                    _store_document_vector(agent_id, filename, text, embedding, file_data=file_data)
                    return {"ok": True, "filename": filename, "type": "image"}

        # Fallback: Ollama Embeddings
        if embedding is None:
            text = f"[Document: {filename}]"
            ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{ollama_url}/api/embeddings",
                    json={"model": "nomic-embed-text", "prompt": text},
                )
            if resp.is_success:
                embedding = resp.json()["embedding"]
                _store_document_vector(agent_id, filename, text, embedding, file_data=file_data)
                return {"ok": True, "filename": filename, "type": "fallback"}

        raise HTTPException(status_code=500, detail="No embedding provider available")

    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Memory upload failed for agent %s", agent_id)
        raise HTTPException(status_code=500, detail=str(e))
