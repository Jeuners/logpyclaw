"""
core/memory.py — Qdrant Vector Memory, Embeddings, Dream Cycles.
"""
import re
import uuid
from datetime import datetime, timedelta

import requests

from config.settings import settings
from storage.agents import load_agents
from storage.providers import load_providers

EMBED_DIM = settings.EMBED_DIM
EMBED_MODEL = settings.EMBED_MODEL

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PointStruct,
        VectorParams,
    )
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False


def get_qdrant():
    if not QDRANT_AVAILABLE:
        return None
    try:
        url = load_providers().get("qdrant", {}).get("url", "http://localhost:6333")
        return QdrantClient(url=url, timeout=5)
    except Exception:
        return None


def embed_text(text, ollama_url="http://localhost:11434"):
    resp = requests.post(
        f"{ollama_url}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text[:2000]},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def collection_name(agent_id):
    return f"agent_{agent_id.replace('-', '_')}"


def ensure_collection(client, agent_id):
    name = collection_name(agent_id)
    existing = [c.name for c in client.get_collections().collections]
    if name not in existing:
        client.create_collection(
            name, vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE)
        )
    return name


def memory_search(agent_id, query, top_k=4):
    """Return relevant past exchanges or documents as context string."""
    client = get_qdrant()
    if not client:
        return ""
    try:
        providers = load_providers()
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        name = collection_name(agent_id)
        existing = [c.name for c in client.get_collections().collections]
        if name not in existing:
            return ""

        # Special handling for "last image/document" queries
        if re.search(r"\b(letzte|letztes|last)\b.*\b(bild|foto|document|datei|image)\b", query, re.IGNORECASE):
            is_image_query = re.search(r"\b(bild|foto|image|jpg|png)\b", query, re.IGNORECASE)
            scroll = client.scroll(collection_name=name, limit=50, with_payload=True)
            docs = [p for p in scroll[0] if p.payload.get("type") == "document"]
            if docs:
                docs.sort(key=lambda x: x.payload.get("ts", ""), reverse=True)
                if is_image_query:
                    img_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
                    images = [d for d in docs if d.payload.get("filename", "").lower().endswith(img_exts)]
                    if images:
                        p = images[0].payload
                        return f"[LATEST IMAGE] Filename: {p.get('filename')}\nPath: {p.get('file_path')}\nContext: {p.get('text')}"
                p = docs[0].payload
                return f"[LATEST DOCUMENT] Filename: {p.get('filename')}\nPath: {p.get('file_path')}\nContext: {p.get('text')}"

        # Special handling for "today/heute" or "yesterday/gestern" queries
        if re.search(r"\b(heute|today|gestern|yesterday)\b", query, re.IGNORECASE):
            scroll = client.scroll(collection_name=name, limit=100, with_payload=True)
            all_entries = scroll[0]
            if all_entries:
                all_entries.sort(key=lambda x: x.payload.get("ts", ""), reverse=True)
                target_date = datetime.now().date()
                if re.search(r"\b(gestern|yesterday)\b", query, re.IGNORECASE):
                    target_date = target_date - timedelta(days=1)
                date_str = target_date.isoformat()
                recent = [e for e in all_entries if e.payload.get("ts", "").startswith(date_str)]
                if recent:
                    parts = ["### Recent Activity from Memory:"]
                    for e in recent[:10]:
                        p = e.payload
                        ts = p.get("ts", "").split("T")[1][:5]
                        if p.get("type") == "document":
                            parts.append(f"- [{ts}] 📄 Document: {p.get('filename')} ({p.get('file_path')})")
                        else:
                            parts.append(f"- [{ts}] 💬 User: {p.get('user')}\n  Assistant: {p.get('assistant')}")
                    return "\n".join(parts)

        vec = embed_text(query, ollama_url)
        result = client.query_points(
            collection_name=name, query=vec, limit=top_k, score_threshold=0.30
        )
        hits = result.points
        if not hits:
            return ""
        parts = ["### Relevant Past Context from Memory:"]
        for h in hits:
            p = h.payload
            mtype = p.get("type", "chat")
            if mtype == "document":
                fpath = p.get("file_path", "")
                parts.append(
                    f"#### 📄 Document: {p.get('filename', 'Unknown')}\n"
                    f"- Content: {p.get('text', '')}\n"
                    f"- Path: {fpath}"
                )
            else:
                parts.append(
                    f"#### 💬 Past Exchange\n"
                    f"- User: {p.get('user', '')}\n"
                    f"- Assistant: {p.get('assistant', '')}"
                )
        return "\n\n".join(parts)
    except Exception as e:
        print(f"[Memory] search error: {e}", flush=True)
        return ""


def memory_store(agent_id, user_msg, assistant_msg):
    """Store a user↔assistant exchange as a memory point."""
    client = get_qdrant()
    if not client:
        return
    try:
        providers = load_providers()
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        text = f"{user_msg}\n{assistant_msg}"
        vec = embed_text(text, ollama_url)
        name = ensure_collection(client, agent_id)
        client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vec,
                    payload={
                        "user": user_msg[:1000],
                        "assistant": assistant_msg[:1000],
                        "ts": datetime.now().isoformat(),
                    },
                )
            ],
        )
        print(f"[Memory] stored for agent {agent_id}", flush=True)
    except Exception as e:
        print(f"[Memory] store error: {e}", flush=True)


def _run_dream_cycle():
    """Execute dream cycle — optimize all agent memories."""
    client = get_qdrant()
    if not client:
        return "❌ Qdrant nicht verfügbar"

    agents = load_agents()
    retention_days = 30
    cutoff = datetime.now() - timedelta(days=retention_days)

    results = []
    total_before = 0
    total_after = 0

    for agent in agents:
        dream_cfg = agent.get("dream", {})
        if not dream_cfg.get("active", False):
            continue

        agent_id = agent["id"]
        name = collection_name(agent_id)

        try:
            existing = [c.name for c in client.get_collections().collections]
            if name not in existing:
                results.append(f"• {agent['name']}: keine Einträge")
                continue

            scroll = client.scroll(collection_name=name, limit=1000, with_payload=True)
            points = scroll[0]
            total_before += len(points)

            old_ids = []
            for p in points:
                ts = p.payload.get("ts", "")
                try:
                    pt = datetime.fromisoformat(ts)
                    if pt < cutoff:
                        old_ids.append(p.id)
                except Exception:
                    pass

            if old_ids:
                client.delete(collection_name=name, points_selector=old_ids)

            remaining = len(points) - len(old_ids)
            total_after += remaining
            results.append(
                f"• {agent['name']}: {len(points)} → {remaining} (→ gelöscht: {len(old_ids)})"
            )

        except Exception as e:
            results.append(f"• {agent['name']}: Fehler - {str(e)[:50]}")

    summary = "🌙 **Träume abgeschlossen**\n━━━━━━━━━━━━━━━━━━━━\n"
    summary += "\n".join(results)
    if results:
        summary += f"\n\n📊 Gesamt: {total_before} → {total_after} Einträge"
    else:
        summary += "\n\nKeine Agenten mit Dream-Flag aktiviert."

    return summary


def run_dream_for_agent(agent_id):
    """Execute dream cycle for a single agent."""
    client = get_qdrant()
    if not client:
        print("[Dream] Qdrant not available", flush=True)
        return

    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        print(f"[Dream] Agent {agent_id} not found", flush=True)
        return

    dream_cfg = agent.get("dream", {})
    retention_days = dream_cfg.get("retention_days", 30)
    cutoff = datetime.now() - timedelta(days=retention_days)
    name = collection_name(agent_id)

    try:
        existing = [c.name for c in client.get_collections().collections]
        if name not in existing:
            print(f"[Dream] {agent['name']}: keine Einträge", flush=True)
            return

        scroll = client.scroll(collection_name=name, limit=1000, with_payload=True)
        points = scroll[0]

        old_ids = []
        for p in points:
            ts = p.payload.get("ts", "")
            try:
                pt = datetime.fromisoformat(ts)
                if pt < cutoff:
                    old_ids.append(p.id)
            except Exception:
                pass

        if old_ids:
            client.delete(collection_name=name, points_selector=old_ids)

        print(
            f"[Dream] {agent['name']}: {len(points)} → {len(points) - len(old_ids)} (gelöscht: {len(old_ids)})",
            flush=True,
        )

    except Exception as e:
        print(f"[Dream] {agent['name']}: Fehler - {str(e)[:50]}", flush=True)
