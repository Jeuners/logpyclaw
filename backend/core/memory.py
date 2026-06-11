"""
backend/core/memory.py — Semantisches Langzeit-Gedächtnis (RAG) für logpyclaw.

Speichert Texte (Q&A, Task-Ergebnisse, Fakten) als Vektoren und findet inhaltlich
ähnliche frühere Einträge via KNN — das echte semantische Gedächtnis, das weder die
alte agentclaw noch v3 bisher hatten.

Scoping (Hybrid):
  - scope="global"       → geteiltes Wissen für ALLE Agenten
  - scope="agent:martin" → privates Gedächtnis des Operators
  - scope="agent:alice"  → privates Gedächtnis von Alice, usw.
  recall(scopes=[...]) sucht z. B. in ["agent:martin", "global"] → eigenes + geteiltes.

Technik:
  - Embeddings: lokales Ollama, `nomic-embed-text` (768-dim) MIT Task-Präfixen
    (`search_document:` zum Speichern, `search_query:` beim Suchen → bessere Trennschärfe).
  - Vektor-Engine: **sqlite-vec** — KNN direkt in SQLite, kein Server, eine .db-Datei,
    portabel (läuft lokal wie auf c2).
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import threading
import time

import httpx
import sqlite_vec

_DIM = 768
_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
_OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = os.environ.get("MEMORY_DB", os.path.join(_REPO, "memory.db"))

GLOBAL = "global"


def _f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def scope_for(agent_id: str) -> str:
    """Gibt den eigenen Scope eines Agenten zurück (z. B. 'agent:martin')."""
    return agent_id or GLOBAL


def recall_scopes(agent_id: str | None) -> list[str]:
    """Standard-Suchbereich: eigener Scope + global."""
    if agent_id and agent_id != GLOBAL:
        return [agent_id, GLOBAL]
    return [GLOBAL]


class SemanticMemory:
    """Vektor-Gedächtnis: remember(text, scope) speichert, recall(query, scopes) findet Ähnliches."""

    def __init__(self, db_path: str = _DB, ollama_url: str = _OLLAMA, model: str = _MODEL):
        self.db_path = db_path
        self.ollama = ollama_url
        self.model = model
        self._lock = threading.Lock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memory_items("
            "id INTEGER PRIMARY KEY, text TEXT, kind TEXT, scope TEXT, meta TEXT, created REAL)"
        )
        # vec0 mit Cosine-Distanz (nomic ist nicht unit-normalisiert)
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
            f"embedding float[{_DIM}] distance_metric=cosine)"
        )
        self.db.commit()

    async def _embed(self, text: str, is_query: bool) -> list[float]:
        prefix = "search_query: " if is_query else "search_document: "
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                f"{self.ollama}/api/embeddings",
                json={"model": self.model, "prompt": prefix + text},
            )
            r.raise_for_status()
            return r.json()["embedding"]

    async def remember(self, text: str, kind: str = "note", scope: str = GLOBAL, meta: dict | None = None) -> int:
        """Speichert einen Text als Erinnerung in einem Scope. Gibt die ID zurück (-1 wenn leer)."""
        text = (text or "").strip()
        if not text:
            return -1
        vec = await self._embed(text, is_query=False)
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO memory_items(text, kind, scope, meta, created) VALUES(?,?,?,?,?)",
                (text, kind, scope or GLOBAL, json.dumps(meta or {}), time.time()),
            )
            rid = cur.lastrowid
            self.db.execute("INSERT INTO memory_vec(rowid, embedding) VALUES(?, ?)", (rid, _f32(vec)))
            self.db.commit()
        return rid

    async def recall(self, query: str, k: int = 5, scopes: list[str] | None = None, min_score: float = 0.0) -> list[dict]:
        """Findet die k ähnlichsten Erinnerungen. `scopes` schränkt auf Bereiche ein
        (z. B. ['agent:martin','global']); None = alle Scopes."""
        query = (query or "").strip()
        if not query:
            return []
        qv = await self._embed(query, is_query=True)
        fetch = k * 5 if scopes else k  # bei Scope-Filter mehr holen, dann filtern
        scope_set = set(scopes) if scopes else None
        with self._lock:
            rows = self.db.execute(
                "SELECT rowid, distance FROM memory_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (_f32(qv), fetch),
            ).fetchall()
            out: list[dict] = []
            for rid, dist in rows:
                it = self.db.execute(
                    "SELECT text, kind, scope, meta, created FROM memory_items WHERE id = ?", (rid,)
                ).fetchone()
                if not it:
                    continue
                if scope_set is not None and it[2] not in scope_set:
                    continue
                score = round(1.0 - float(dist), 4)  # Cosine-Ähnlichkeit
                if score < min_score:
                    continue
                out.append({
                    "id": rid, "text": it[0], "kind": it[1], "scope": it[2],
                    "meta": json.loads(it[3] or "{}"), "created": it[4], "score": score,
                })
                if len(out) >= k:
                    break
            return out

    def forget(self, mem_id: int) -> bool:
        with self._lock:
            ex = self.db.execute("SELECT 1 FROM memory_items WHERE id=?", (mem_id,)).fetchone()
            self.db.execute("DELETE FROM memory_items WHERE id=?", (mem_id,))
            self.db.execute("DELETE FROM memory_vec WHERE rowid=?", (mem_id,))
            self.db.commit()
            return bool(ex)

    def stats(self) -> dict:
        n = self.db.execute("SELECT count(*) FROM memory_items").fetchone()[0]
        by_scope = dict(self.db.execute("SELECT scope, count(*) FROM memory_items GROUP BY scope").fetchall())
        return {"count": n, "dim": _DIM, "model": self.model, "db": self.db_path, "scopes": by_scope}
