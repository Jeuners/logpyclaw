"""
storage/providers.py — Provider-Konfiguration + Redis-Logger.
"""
import json
import os
import uuid
from datetime import datetime

import redis as redis_lib

from core.config import PROVIDERS_FILE, _read_json, _write_json
from core.state import _providers_lock, _redis_client as _rc_init


# ── Providers ─────────────────────────────────────────────────────────────────
_PROVIDER_DEFAULTS = {
    "ollama":     {"url": "http://localhost:11434"},
    "mistral":    {"api_key": os.getenv("MISTRAL_API_KEY", "")},
    "openrouter": {"api_key": ""},
    "comfyui":    {"url": "http://localhost:8188", "model": "flux2pro"},
    "google_api": {"api_key": ""},
    "telegram":   {"bot_token": "", "chat_id": ""},
    "gmail":      {"email": "", "app_password": ""},
    "redis":      {"host": "localhost", "port": 6379, "enabled": False},
    "martin_m2m": {
        "node_id": "",
        "node_name": "",
        "public_url": "",
        "enabled": False,
    },
}


def load_providers() -> dict:
    with _providers_lock:
        stored = _read_json(PROVIDERS_FILE, {})
        for k, v in _PROVIDER_DEFAULTS.items():
            if k not in stored:
                stored[k] = v
        return stored


def save_providers(providers: dict):
    with _providers_lock:
        _write_json(PROVIDERS_FILE, providers)


# ── Redis Client (lazy, singleton) ────────────────────────────────────────────
_redis_client = None


def get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    providers = load_providers()
    redis_config = providers.get("redis", {})
    if not redis_config.get("enabled", False):
        return None

    try:
        _redis_client = redis_lib.Redis(
            host=redis_config.get("host", "localhost"),
            port=redis_config.get("port", 6379),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        _redis_client.ping()
        print("[Redis] Connected", flush=True)
        return _redis_client
    except Exception as e:
        print(f"[Redis] Connection failed: {e}", flush=True)
        return None


# ── A2A Event Logger (Redis) ──────────────────────────────────────────────────
def log_a2a_event(event_type: str, from_agent: str, to_agent: str,
                  payload: dict, status: str = "submitted"):
    client = get_redis_client()
    if not client:
        return
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "payload": payload,
        "status": status,
    }
    try:
        date_key = datetime.now().strftime("%Y-%m-%d")
        event_key = f"a2a:event:{event['event_id']}"
        client.setex(event_key, 604800, json.dumps(event))
        client.lpush(f"a2a:events:{date_key}", event["event_id"])
        client.expire(f"a2a:events:{date_key}", 604800)
    except Exception as e:
        print(f"[Redis] log_a2a_event error: {e}", flush=True)


def get_a2a_events(limit: int = 50, agent_filter: str = None) -> list:
    client = get_redis_client()
    if not client:
        return []
    events = []
    today = datetime.now().strftime("%Y-%m-%d")
    event_ids = client.lrange(f"a2a:events:{today}", 0, limit - 1)
    for eid in event_ids:
        raw = client.get(f"a2a:event:{eid}")
        if raw:
            ev = json.loads(raw)
            if agent_filter and agent_filter not in [ev.get("from_agent", ""), ev.get("to_agent", "")]:
                continue
            events.append(ev)
    return events[:limit]


def cleanup_redis_watchdog(max_memory_mb: int = 2048):
    client = get_redis_client()
    if not client:
        return
    try:
        for key in client.scan_iter("a2a:event:*"):
            if not client.ttl(key) or client.ttl(key) < 0:
                client.expire(key, 604800)
        print("[Redis] Cleanup completed", flush=True)
    except Exception as e:
        print(f"[Redis] Cleanup error: {e}", flush=True)
