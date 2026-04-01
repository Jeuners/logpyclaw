import os
import sys
import json
import base64
import uuid
import re
import hashlib
import random
import threading
import time
import subprocess
import io
from datetime import datetime, timedelta
from html.parser import HTMLParser
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
from PIL import Image
import requests
import redis

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        VectorParams,
        PointStruct,
        Filter,
        FieldCondition,
        MatchValue,
    )

    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

load_dotenv()

# Wenn als py2app .app-Bundle gestartet, Resources-Verzeichnis ermitteln
if getattr(sys, "frozen", False):
    # Contents/MacOS/AgentClaw → Contents/Resources/
    _bundle_resources = os.path.join(
        os.path.dirname(os.path.dirname(sys.executable)), "Resources"
    )
    app = Flask(
        __name__,
        template_folder=os.path.join(_bundle_resources, "templates"),
        static_folder=os.path.join(_bundle_resources, "static"),
    )
    # Daten-Dateien liegen in Resources/
    os.chdir(_bundle_resources)
else:
    app = Flask(__name__)

# Flask-SocketIO for real-time WebSocket communication
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_timeout=30,
    ping_interval=10,
)


# ─── Background Task Helper (threading-compatible) ─────────────────────
def spawn_background(target, *args, **kwargs):
    """Spawn a background task using threading."""
    import threading

    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ─── WebSocket Event Handlers ────────────────────────────────────────
_USERS = {}  # sid -> {agent_ids: []}


@socketio.on("connect", namespace="/ws")
def ws_connect():
    sid = request.sid
    _USERS[sid] = {"agent_ids": []}
    print(f"[WS] Client connected: {sid}", flush=True)
    emit("connected", {"sid": sid, "type": "connected"})


@socketio.on("disconnect", namespace="/ws")
def ws_disconnect():
    sid = request.sid
    _USERS.pop(sid, None)
    print(f"[WS] Client disconnected: {sid}", flush=True)


@socketio.on("join_agent", namespace="/ws")
def ws_join_agent(data):
    sid = request.sid
    agent_id = data.get("agent_id")
    room = f"agent_{agent_id}"
    join_room(room)
    if sid in _USERS and agent_id not in _USERS[sid]["agent_ids"]:
        _USERS[sid]["agent_ids"].append(agent_id)
    print(f"[WS] {sid} joined room {room}", flush=True)
    emit("joined", {"agent_id": agent_id, "room": room, "type": "joined"})


@socketio.on("leave_agent", namespace="/ws")
def ws_leave_agent(data):
    sid = request.sid
    agent_id = data.get("agent_id")
    room = f"agent_{agent_id}"
    leave_room(room)
    if sid in _USERS and agent_id in _USERS[sid]["agent_ids"]:
        _USERS[sid]["agent_ids"].remove(agent_id)
    print(f"[WS] {sid} left room {room}", flush=True)
    emit("left", {"agent_id": agent_id, "type": "left"})


@socketio.on("join_all", namespace="/ws")
def ws_join_all(data):
    """Join all agent rooms at once."""
    sid = request.sid
    for a in load_agents():
        room = f"agent_{a['id']}"
        join_room(room)
    _USERS[sid] = {"agent_ids": [a["id"] for a in load_agents()]}
    print(f"[WS] {sid} joined all agent rooms", flush=True)
    emit(
        "joined_all", {"agents": [a["id"] for a in load_agents()], "type": "joined_all"}
    )


# ─── WebSocket Emit Helper Functions ─────────────────────────────────
def ws_emit(event, data, room=None, broadcast=False):
    """Emit event to specific room, or broadcast to all."""
    kwargs = {"event": event, "data": data, "namespace": "/ws"}
    if room:
        emit(event, data, room=room, namespace="/ws")
    elif broadcast:
        socketio.emit(event, data, namespace="/ws", broadcast=True)
    else:
        emit(event, data, namespace="/ws")


def emit_agent_activity(agent_id, atype, label, status):
    """Broadcast agent activity to all subscribers."""
    try:
        data = {"agent_id": agent_id, "type": atype, "label": label, "status": status}
        room = f"agent_{agent_id}"
        emit("agent_activity", data, room=room, namespace="/ws")
        socketio.emit(
            "agent_activity", data, namespace="/ws", broadcast=True, include_self=True
        )
    except Exception as e:
        print(f"[WS] emit_agent_activity error: {e}", flush=True)


def emit_task_result(task_id, agent_id, result_text, result_image, status, error=None):
    """Send task result to relevant room and broadcast."""
    try:
        data = {
            "task_id": task_id,
            "agent_id": agent_id,
            "result_text": result_text,
            "result_image": result_image,
            "status": status,
            "error": error,
        }
        room = f"agent_{agent_id}"
        emit("task_result", data, room=room, namespace="/ws")
        socketio.emit(
            "task_result", data, namespace="/ws", broadcast=True, include_self=True
        )
    except Exception as e:
        print(f"[WS] emit_task_result error: {e}", flush=True)


def emit_chat_message(agent_id, role, content, message_id=None):
    """Broadcast a new chat message."""
    try:
        data = {
            "agent_id": agent_id,
            "role": role,
            "content": content,
            "message_id": message_id or str(uuid.uuid4()),
            "ts": datetime.now().isoformat(),
        }
        room = f"agent_{agent_id}"
        emit("chat_message", data, room=room, namespace="/ws")
        socketio.emit(
            "chat_message", data, namespace="/ws", broadcast=True, include_self=True
        )
    except Exception as e:
        print(f"[WS] emit_chat_message error: {e}", flush=True)


def emit_heartbeat_result(agent_id, result):
    """Send heartbeat output to room and broadcast."""
    try:
        data = {
            "agent_id": agent_id,
            "result": result,
            "ts": datetime.now().isoformat(),
        }
        room = f"agent_{agent_id}"
        emit("heartbeat_result", data, room=room, namespace="/ws")
        socketio.emit(
            "heartbeat_result", data, namespace="/ws", broadcast=True, include_self=True
        )
    except Exception as e:
        print(f"[WS] emit_heartbeat_result error: {e}", flush=True)


def emit_error(message, room=None):
    """Send error to client(s)."""
    data = {"error": message, "ts": datetime.now().isoformat()}
    if room:
        emit("error", data, room=room, namespace="/ws")
    else:
        emit("error", data, namespace="/ws", broadcast=True)


MISTRAL_TTS_URL = "https://api.mistral.ai/v1/audio/speech"
MISTRAL_VOICES_URL = "https://api.mistral.ai/v1/audio/voices"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SKILLS = [
    {
        "id": "url_fetch",
        "name": "Read URL",
        "icon": "🔗",
        "description": "Automatically fetches and extracts text content from URLs in messages and passes it to the agent as context",
        "requires": None,
    },
    {
        "id": "screenshot",
        "name": "Screenshot",
        "icon": "📸",
        "description": "Takes browser screenshots of websites and sends them as images to the agent (requires Playwright)",
        "requires": "playwright",
    },
    {
        "id": "image_gen",
        "name": "Image Generation",
        "icon": "🎨",
        "description": "Generates images via a local ComfyUI server (Flux Pro, Wan, DALL-E and more) on request",
        "requires": "comfyui",
    },
    {
        "id": "tagesschau",
        "name": "Tagesschau News",
        "icon": "📰",
        "description": "Fetches current news from tagesschau.de (domestic, international, business, sports …)",
        "requires": None,
    },
    {
        "id": "hackernews",
        "name": "Hacker News",
        "icon": "🎩",
        "description": "Fetches current top stories from Hacker News",
        "requires": None,
    },
    {
        "id": "memory",
        "name": "Long-Term Memory",
        "icon": "🧠",
        "description": "Stores important conversation content in Qdrant vector DB and recalls relevant memories as context",
        "requires": "qdrant",
    },
    {
        "id": "document_memory",
        "name": "Document Memory",
        "icon": "📄",
        "description": "Upload PDFs, images - stored as vectors for retrieval (requires Google API)",
        "requires": "google_api",
    },
    {
        "id": "dream",
        "name": "Dream Agent",
        "icon": "🌙",
        "description": "Optimizes agent memories daily - removes old entries, resolves contradictions, cleans up vector store",
        "requires": "qdrant",
    },
    {
        "id": "telegram",
        "name": "Telegram",
        "icon": "✈️",
        "description": "Sends images or text to Telegram (trigger: 'send this image to Telegram')",
        "requires": None,
    },
    {
        "id": "telegram_incoming",
        "name": "Telegram Incoming",
        "icon": "📥",
        "description": "Receives incoming Telegram messages and forwards them to the agent",
        "requires": None,
    },
    {
        "id": "image_edit",
        "name": "Image Editing",
        "icon": "✏️",
        "description": "Edits uploaded images via FireRed Image Edit on a local ComfyUI server",
        "requires": "comfyui",
    },
    {
        "id": "prompt_optimize",
        "name": "Prompt Optimizer",
        "icon": "✨",
        "description": "Optimizes prompts using proven frameworks (RTF, TAG, BAB, CARE, RISE) — ideal for SEO, copywriting, strategy and image generation prompts",
        "requires": None,
    },
    {
        "id": "gmail",
        "name": "Gmail",
        "icon": "📧",
        "description": "Liest und sendet E-Mails über Gmail (IMAP/SMTP). Konfiguration in Providers erforderlich.",
        "requires": None,
    },
]

# ─── Agent Directory ──────────────────────────────────────────────────────────

_SKILL_MAP = {s["id"]: s for s in SKILLS}

# ─── A2A Protocol Constants ───────────────────────────────────────────────────
A2A_TASK_STATES = {
    "submitted": "Task received, waiting for processing",
    "working": "Task is actively being processed",
    "input-required": "Agent needs additional input from client",
    "completed": "Task completed successfully",
    "failed": "Task failed with error",
    "canceled": "Task was canceled by client",
    "rejected": "Task rejected (e.g., unsupported)",
    "auth-required": "Authentication required to continue",
}

A2A_TASK_CANCELABLE_STATES = {"submitted", "working", "input-required"}

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


# ─── A2A Communication Prompt ─────────────────────────────────────────────────

A2A_COMMUNICATION_PROMPT = """
--- A2A KOMMUNIKATION ---
Du bist Teil des AgentClaw Multi-Agent-Systems. Agents kommunizieren über das A2A-Protokoll.

VERHALTENSREGELN:
1. Antworte NUR wenn du direkt angesprochen wirst oder eigenständig handeln musst.
2. Wenn ein Task nicht zu deinen Skills passt, delegiere an den passenden Agenten.
3. Antworte präzise und minimal — keine langen Erklärungen.

DELEGIERUNG (@Mention):
  • Schreibe @AgentName gefolgt von deiner Anfrage
  • Beispiel: "@Fotograf generiere ein Bild von einer Katze"
  • Der目标是 Agent übernimmt und liefert das Ergebnis zurück

API-ENDPOINTS FÜR DIREKTE KOMMUNIKATION:
  • /api/a2a/tasks → Task erstellen
  • /api/a2a/tasks/<id> → Task Status abfragen
  • /api/a2a/tasks/<id>/cancel → Task abbrechen
  • /api/a2a/agents → Alle Agents mit Skills abrufen

TASK STATES (wissen musst du):
  • submitted → working → completed/failed
  • input-required: Wenn du mehr Infos vom Sender brauchst
  • canceled: Wenn Task abgebrochen wurde

Wenn du Hilfe brauchst oder nicht weiterkommst, sage das klar.
--- ENDE A2A ---
""".strip()


def _build_agent_directory(current_agent_id: str = None) -> str:
    """
    Baut ein kompaktes Agent-Verzeichnis für den System-Prompt.
    Jeder Agent kennt so alle anderen Agents und deren Skills.
    """
    agents = load_agents()
    if not agents:
        return ""

    def _skill_label(sid: str) -> str:
        s = _SKILL_MAP.get(sid)
        return f"{s['icon']} {s['name']}" if s else sid

    lines = [
        "--- AGENT NETWORK ---",
        "Du bist Teil eines Multi-Agent-Systems. Du kannst Tasks jederzeit an andere Agents delegieren.",
        "Delegations-Syntax: @AgentName <Aufgabe>",
        'Beispiel: "@Fotograf generiere ein Bild von einem Sonnenuntergang über Bergen"',
        "",
        "VERFÜGBARE AGENTS:",
    ]

    for a in agents:
        is_self = a["id"] == current_agent_id
        skills = a.get("skills", [])
        skill_str = ", ".join(_skill_label(s) for s in skills) if skills else "—"
        role_str = f" · {a['role']}" if a.get("role") else ""
        self_tag = " (DU)" if is_self else ""
        lines.append(f"  • {a['name']}{self_tag}{role_str} — Skills: {skill_str}")

    lines += [
        "",
        "Delegiere nur wenn der Task wirklich zum Skill des anderen Agents passt.",
        "--- ENDE AGENT NETWORK ---",
    ]
    return "\n".join(lines)


# Im py2app-Bundle zeigt __file__ auf die .zip — deshalb CWD nutzen (gesetzt durch chdir in main_app.py)
BASE_DIR = (
    os.getcwd()
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
AGENTS_FILE = os.path.join(BASE_DIR, "agents.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
PROVIDERS_FILE = os.path.join(BASE_DIR, "providers.json")
WATCHDOGS_FILE = os.path.join(BASE_DIR, "watchdogs.json")
TASKS_FILE = os.path.join(BASE_DIR, "tasks.json")

# ── Agent-Tasks in-memory store ──────────────────────────────────────────────
_TASKS: dict = {}
_tasks_lock = threading.Lock()

# ── Event system for push updates ───────────────────────────────────────────
_EVENTS: list = []
_events_lock = threading.Lock()
_EVENT_VERSION = 0


def emit_event(event_type: str, data: dict = None):
    """Emit an event that clients can subscribe to."""
    global _EVENT_VERSION
    with _events_lock:
        _EVENT_VERSION += 1
        _EVENTS.append(
            {
                "type": event_type,
                "data": data or {},
                "v": _EVENT_VERSION,
                "ts": datetime.now().isoformat(),
            }
        )
        # Keep only last 100 events
        if len(_EVENTS) > 100:
            _EVENTS[:] = _EVENTS[-100:]


def get_events_since(version: int) -> list:
    """Get events after a given version."""
    with _events_lock:
        return [e for e in _EVENTS if e["v"] > version]


# ── Live activity tracker ─────────────────────────────────────────────────────
# { agent_id: { "type": "heartbeat"|"task", "label": str, "since": iso } }
_ACTIVITY: dict = {}
_activity_lock = threading.Lock()


def activity_start(agent_id: str, atype: str, label: str):
    with _activity_lock:
        _ACTIVITY[agent_id] = {
            "type": atype,
            "label": label,
            "since": datetime.now().isoformat(),
        }
    emit_agent_activity(agent_id, atype, label, "started")


def activity_end(agent_id: str):
    with _activity_lock:
        _ACTIVITY.pop(agent_id, None)
    emit_agent_activity(agent_id, "", "", "ended")


def activity_cleanup():
    """Remove stale activity entries older than 10 minutes (crash guard)."""
    cutoff = (datetime.now() - timedelta(minutes=10)).isoformat()
    with _activity_lock:
        stale = [k for k, v in _ACTIVITY.items() if v.get("since", "") < cutoff]
        for k in stale:
            del _ACTIVITY[k]


import ipaddress


def _is_safe_url(url: str) -> bool:
    """
    SSRF protection: block requests to private/internal networks.
    Allows only public routable IPs and HTTPS/HTTP to the open internet.
    """
    from urllib.parse import urlparse
    import socket

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # Block obvious internal hostnames
        blocked_hosts = {"localhost", "metadata.google.internal"}
        if hostname.lower() in blocked_hosts:
            return False
        # Resolve to IP and check if private/loopback/link-local
        ip = ipaddress.ip_address(socket.gethostbyname(hostname))
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
        return True
    except Exception:
        return False


def fetch_url_text(url, max_chars=4000):
    """Fetch a URL and return plain text content."""
    if not _is_safe_url(url):
        return f"[Blocked: '{url}' targets a private or internal network address]"
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        # Strip HTML tags
        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "footer", "head"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "footer", "head"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    t = data.strip()
                    if t:
                        self.parts.append(t)

        p = TextExtractor()
        p.feed(resp.text)
        text = " ".join(p.parts)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[Fehler beim Laden: {e}]"


def save_providers(providers):
    with open(PROVIDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(providers, f, ensure_ascii=False, indent=2)


# ─── Agent Tasks ──────────────────────────────────────────────────────────────


def _load_tasks_from_disk():
    if not os.path.exists(TASKS_FILE):
        return {}
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        return {t["id"]: t for t in tasks} if isinstance(tasks, list) else tasks
    except Exception:
        return {}


def _save_tasks():
    with _tasks_lock:
        tasks_list = list(_TASKS.values())
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks_list, f, ensure_ascii=False, indent=2)


def _extract_img_prompt(message: str) -> str:
    cleaned = re.sub(
        r"\b(bild|generier\w*|erstell\w*|zeich\w*|mal\w*|mach\w*|zeig\w*|"
        r"mir\w*|eine?\w*|eines?\w*|von|generate|draw|create|make|"
        r"paint|an?\b|image|picture|photo|of|bitte|please|einen?|einer?)\b",
        " ",
        message,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _optimize_prompt_for_image(prompt: str) -> str:
    """Optimize prompt for image generation: English only, no text."""
    import re

    # German to English translations for common terms
    translations = {
        "strand": "beach",
        "meer": "sea",
        "himmel": "sky",
        "sonne": "sun",
        "mond": "moon",
        "sterne": "stars",
        "planeten": "planets",
        "galaxie": "galaxy",
        "berg": "mountain",
        "wald": "forest",
        "fluss": "river",
        "see": "lake",
        "stadt": "city",
        "haus": "house",
        "mensch": "person",
        "personen": "people",
        "frau": "woman",
        "mann": "man",
        "kind": "child",
        "tier": "animal",
        "vogel": "bird",
        "blume": "flower",
        "baum": "tree",
        "straße": "street",
        "gebäude": "building",
        "auto": "car",
        "boot": "boat",
        "flugzeug": "airplane",
        "strand": "beach",
    }

    p = prompt.lower()
    for de, en in translations.items():
        p = re.sub(rf"\b{de}\b", en, p)

    # Remove German characters
    p = p.replace("ü", "ue").replace("ö", "oe").replace("ä", "ae").replace("ß", "ss")

    # Add negative prompt to avoid text
    negative = "no text, no letters, no words, no watermark, no signature, no title, no caption, no writing, clean image, photorealistic"

    return f"{p}, {negative}"


def _upload_image_to_comfyui(image_b64: str, base_url: str) -> str:
    """Upload image to ComfyUI and return filename."""
    import uuid

    filename = f"agentclaw_edit_{uuid.uuid4().hex[:8]}.png"

    # Extract base64 data
    if "," in image_b64:
        header, b64data = image_b64.split(",", 1)
        # Detect mime type
        if "jpeg" in header or "jpg" in header:
            mime = "image/jpeg"
        elif "png" in header:
            mime = "image/png"
        else:
            mime = "image/png"
    else:
        b64data = image_b64
        mime = "image/png"

    img_bytes = base64.b64decode(b64data)

    files = {"image": (filename, img_bytes, mime)}
    resp = requests.post(f"{base_url}/upload/image", files=files, timeout=30)
    resp.raise_for_status()

    return filename


def build_firered_edit_workflow(
    image_filename: str, prompt: str, seed: int, use_lightning: bool = True
):
    """FireRed Image Edit 1.1 workflow."""
    wf = {
        "9": {
            "inputs": {"filename_prefix": "agentclaw_edit", "images": ["167:126", 0]},
            "class_type": "SaveImage",
        },
        "167:120": {
            "inputs": {"shift": 3.1, "model": ["167:154", 0]},
            "class_type": "ModelSamplingAuraFlow",
        },
        "167:154": {
            "inputs": {
                "switch": ["167:153", 0],
                "on_false": ["167:128", 0],
                "on_true": ["167:151", 0],
            },
            "class_type": "ComfySwitchNode",
        },
        "167:155": {"inputs": {"value": 40}, "class_type": "PrimitiveInt"},
        "167:123": {
            "inputs": {"strength": 1, "model": ["167:120", 0]},
            "class_type": "CFGNorm",
        },
        "167:164": {
            "inputs": {
                "switch": ["167:153", 0],
                "on_false": ["167:162", 0],
                "on_true": ["167:163", 0],
            },
            "class_type": "ComfySwitchNode",
        },
        "167:156": {"inputs": {"value": 8}, "class_type": "PrimitiveInt"},
        "167:162": {"inputs": {"value": 4}, "class_type": "PrimitiveFloat"},
        "167:163": {"inputs": {"value": 1}, "class_type": "PrimitiveFloat"},
        "167:157": {
            "inputs": {
                "switch": ["167:153", 0],
                "on_false": ["167:155", 0],
                "on_true": ["167:156", 0],
            },
            "class_type": "ComfySwitchNode",
        },
        "167:116": {
            "inputs": {"vae_name": "qwen_image_vae.safetensors"},
            "class_type": "VAELoader",
        },
        "167:115": {
            "inputs": {
                "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "type": "qwen_image",
                "device": "default",
            },
            "class_type": "CLIPLoader",
        },
        "167:151": {
            "inputs": {
                "lora_name": "FireRed-Image-Edit-1.0-Lightning-8steps-v1.0.safetensors",
                "strength_model": 1,
                "model": ["167:128", 0],
            },
            "class_type": "LoraLoaderModelOnly",
        },
        "167:128": {
            "inputs": {
                "unet_name": "FireRed-Image-Edit-1.1-transformer.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
        },
        "167:125": {
            "inputs": {"pixels": ["167:147", 0], "vae": ["167:116", 0]},
            "class_type": "VAEEncode",
        },
        "167:153": {
            "inputs": {"value": use_lightning},
            "class_type": "PrimitiveBoolean",
        },
        "167:118": {
            "inputs": {
                "prompt": prompt,
                "clip": ["167:115", 0],
                "vae": ["167:116", 0],
                "image1": ["167:147", 0],
            },
            "class_type": "TextEncodeQwenImageEditPlus",
        },
        "167:117": {
            "inputs": {
                "prompt": "",
                "clip": ["167:115", 0],
                "vae": ["167:116", 0],
                "image1": ["167:147", 0],
            },
            "class_type": "TextEncodeQwenImageEditPlus",
        },
        "167:130": {
            "inputs": {
                "seed": seed,
                "steps": ["167:157", 0],
                "cfg": ["167:164", 0],
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["167:123", 0],
                "positive": ["167:118", 0],
                "negative": ["167:117", 0],
                "latent_image": ["167:125", 0],
            },
            "class_type": "KSampler",
        },
        "167:126": {
            "inputs": {"samples": ["167:130", 0], "vae": ["167:116", 0]},
            "class_type": "VAEDecode",
        },
        "167:143": {
            "inputs": {"image": image_filename},
            "class_type": "LoadImage",
        },
        "167:147": {
            "inputs": {"image": ["167:143", 0]},
            "class_type": "FluxKontextImageScale",
        },
    }
    return wf


def _run_comfyui_sync(prompt: str) -> str:
    """Run ComfyUI image generation synchronously. Returns base64 data URL."""
    providers = load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")
    seed = int(time.time()) % (2**32)

    # Optimize prompt: English only, no text
    optimized = _optimize_prompt_for_image(prompt)
    print(f"[ComfyUI] original: {prompt[:60]}...", flush=True)
    print(f"[ComfyUI] optimized: {optimized[:60]}...", flush=True)

    workflow = build_z_image_turbo_workflow(optimized, seed)

    r = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": "agentclaw-task"},
        timeout=30,
    )
    r.raise_for_status()
    resp_json = r.json()
    if "prompt_id" not in resp_json:
        raise RuntimeError(f"ComfyUI Antwort unerwartet: {resp_json}")
    prompt_id = resp_json["prompt_id"]

    deadline = time.time() + 120
    outputs = None
    while time.time() < deadline:
        time.sleep(2)
        h = requests.get(f"{base_url}/history/{prompt_id}", timeout=10)
        entry = h.json().get(prompt_id, {})
        if entry.get("status", {}).get("completed"):
            outputs = entry.get("outputs", {})
            break

    if not outputs:
        raise RuntimeError("Timeout: ComfyUI hat nicht rechtzeitig geantwortet")

    img_info = None
    for node_out in outputs.values():
        imgs = node_out.get("images", [])
        if imgs:
            img_info = imgs[0]
            break

    if not img_info:
        raise RuntimeError("Keine Bilddaten in der ComfyUI-Antwort")

    filename = img_info["filename"]
    subfolder = img_info.get("subfolder", "")
    img_type = img_info.get("type", "output")
    params = f"filename={filename}&type={img_type}"
    if subfolder:
        params += f"&subfolder={subfolder}"

    img_r = requests.get(f"{base_url}/view?{params}", timeout=30)
    img_r.raise_for_status()
    mime = img_r.headers.get("Content-Type", "image/png").split(";")[0]
    b64 = base64.b64encode(img_r.content).decode()
    return f"data:{mime};base64,{b64}"


def _make_thumbnail(b64_data_url: str, max_size: int = 200) -> str:
    """Erstellt eine verkleinerte Version des Bildes für die History-Anzeige.

    Args:
        b64_data_url: Base64 Data URL (data:image/png;base64,...)
        max_size: Maximale Kantenlänge in Pixel

    Returns:
        Base64 Data URL der verkleinerten Version
    """
    try:
        if not b64_data_url or not b64_data_url.startswith("data:"):
            return None

        header, b64_str = b64_data_url.split(",", 1)
        img_data = base64.b64decode(b64_str)

        img = Image.open(io.BytesIO(img_data))
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        output = io.BytesIO()
        img.save(output, format="JPEG", quality=70, optimize=True)
        thumb_b64 = base64.b64encode(output.getvalue()).decode()

        return f"data:image/jpeg;base64,{thumb_b64}"
    except Exception as e:
        print(f"[Thumbnail] Error: {e}", flush=True)
        return None


def _run_comfyui_edit(image_b64: str, prompt: str, use_lightning: bool = True) -> str:
    """Run FireRed Image Edit via ComfyUI. Returns base64 data URL."""
    providers = load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")
    seed = int(time.time()) % (2**32)

    # Optimize prompt
    optimized = _optimize_prompt_for_image(prompt)
    print(f"[ComfyUI Edit] original: {prompt[:60]}...", flush=True)
    print(f"[ComfyUI Edit] optimized: {optimized[:60]}...", flush=True)

    # Upload image first
    filename = _upload_image_to_comfyui(image_b64, base_url)
    print(f"[ComfyUI Edit] uploaded: {filename}", flush=True)

    # Build workflow
    workflow = build_firered_edit_workflow(filename, optimized, seed, use_lightning)

    # Submit
    r = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": "agentclaw-edit"},
        timeout=30,
    )
    r.raise_for_status()
    resp_json = r.json()
    if "prompt_id" not in resp_json:
        raise RuntimeError(f"ComfyUI Antwort unerwartet: {resp_json}")
    prompt_id = resp_json["prompt_id"]

    deadline = time.time() + 120
    outputs = None
    while time.time() < deadline:
        time.sleep(2)
        h = requests.get(f"{base_url}/history/{prompt_id}", timeout=10)
        entry = h.json().get(prompt_id, {})
        if entry.get("status", {}).get("completed"):
            outputs = entry.get("outputs", {})
            break

    if not outputs:
        raise RuntimeError("Timeout: ComfyUI hat nicht rechtzeitig geantwortet")

    img_info = None
    for node_out in outputs.values():
        imgs = node_out.get("images", [])
        if imgs:
            img_info = imgs[0]
            break

    if not img_info:
        raise RuntimeError("Keine Bilddaten in der ComfyUI-Antwort")

    filename = img_info["filename"]
    subfolder = img_info.get("subfolder", "")
    img_type = img_info.get("type", "output")
    params = f"filename={filename}&type={img_type}"
    if subfolder:
        params += f"&subfolder={subfolder}"

    img_r = requests.get(f"{base_url}/view?{params}", timeout=30)
    img_r.raise_for_status()
    mime = img_r.headers.get("Content-Type", "image/png").split(";")[0]
    b64 = base64.b64encode(img_r.content).decode()
    return f"data:{mime};base64,{b64}"


def _run_telegram(message: str, image_base64: str = None) -> str:
    """Send message or image to Telegram."""
    import re as re_module

    providers = load_providers()
    tg = providers.get("telegram", {})
    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")

    if not token or not chat_id:
        return "❌ Telegram nicht konfiguriert. Bitte Bot-Token und Chat-ID in den Provider-Einstellungen eintragen."

    # Extract caption/text after trigger
    text = message
    patterns = [
        r"schick.*(das\s*)?(bild|foto|photo|image).*telegram\s*(.*)",
        r"schick.*telegram\s*(.*)",
        r"send.*(the\s*)?(image|picture|photo).*telegram\s*(.*)",
        r"send.*to\s*telegram\s*(.*)",
        r"telegram\s*(.*)",
    ]
    for p in patterns:
        m = re_module.search(p, message, re_module.IGNORECASE)
        if m:
            text = m.group(1).strip() if m.group(1) else "Bild von AgentClaw"
            break

    if not text:
        text = "Bild von AgentClaw"

    if image_base64 and "," in image_base64:
        # Extract base64 part from data URL
        b64_data = image_base64.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_data)
        files = {"photo": ("agentclaw.jpg", img_bytes, "image/jpeg")}
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        data = {"chat_id": chat_id, "caption": text[:1024]}
        try:
            resp = requests.post(url, data=data, files=files, timeout=30)
            if resp.ok:
                return f"✅ Bild an Telegram gesendet: {text}"
            else:
                return f"❌ Telegram-Fehler: {resp.json().get('description', resp.text[:100])}"
        except Exception as e:
            return f"❌ Telegram-Fehler: {str(e)[:100]}"
    else:
        # Send text only
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": text[:4096]}
        try:
            resp = requests.post(url, json=data, timeout=30)
            if resp.ok:
                return f"✅ Nachricht an Telegram gesendet: {text}"
            else:
                return f"❌ Telegram-Fehler: {resp.json().get('description', resp.text[:100])}"
        except Exception as e:
            return f"❌ Telegram-Fehler: {str(e)[:100]}"


def _run_gmail(action: str, params: dict) -> str:
    """E-Mails abrufen oder senden via Gmail IMAP/SMTP.

    Args:
        action: 'fetch' oder 'send'
        params: {
            'subject': ...,
            'to': ...,
            'body': ...,
            'max_results': 10  # für fetch
        }
    """
    try:
        import imaplib
        import email
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
    except ImportError:
        return "❌ IMAP-Bibliothek nicht verfügbar"

    providers = load_providers()
    gm = providers.get("gmail", {})

    email_addr = gm.get("email", "")
    app_password = gm.get("app_password", "")

    if not email_addr or not app_password:
        return "❌ Gmail nicht konfiguriert. Bitte E-Mail und App-Password in den Provider-Einstellungen eintragen."

    if action == "send":
        subject = params.get("subject", "Nachricht von AgentClaw")
        to = params.get("to", "")
        body = params.get("body", "")

        if not to:
            return "❌ Kein Empfänger angegeben (to)"

        try:
            import smtplib

            msg = MIMEMultipart()
            msg["From"] = email_addr
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(email_addr, app_password)
            server.send_message(msg)
            server.quit()

            return f"✅ E-Mail gesendet an {to}: {subject}"
        except Exception as e:
            return f"❌ SMTP-Fehler: {str(e)[:100]}"

    elif action == "fetch":
        max_results = params.get("max_results", 10)

        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(email_addr, app_password)
            mail.select("inbox")

            _, data = mail.search(None, "ALL")
            email_ids = data[0].split()[-max_results:][::-1]

            results = []
            for eid in email_ids:
                _, data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                subject = msg.get("subject", "(Kein Betreff)")
                from_addr = msg.get("from", "Unbekannt")
                date = msg.get("date", "")

                results.append(f"Von: {from_addr}\nBetreff: {subject}\nDatum: {date}")

            mail.close()
            mail.logout()

            if not results:
                return "📭 Keine E-Mails gefunden"

            return "📧 Letzte E-Mails:\n\n" + "\n\n".join(results[:5])

        except Exception as e:
            return f"❌ IMAP-Fehler: {str(e)[:100]}"

    return "❌ Unbekannte Aktion"


def process_task(task_id: str):
    """Background worker: process an agent task."""
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        return

    task["status"] = "working"
    _save_tasks()
    print(f"[Task] processing {task_id}: {task['message'][:60]}", flush=True)
    activity_start(
        task["recipient_agent_id"],
        "task",
        f"Task from @{task['sender_agent_name']}: {task['message'][:50]}",
    )

    agents = load_agents()
    recipient = next((a for a in agents if a["id"] == task["recipient_agent_id"]), None)
    if not recipient:
        task["status"] = "failed"
        task["error"] = f"Agent '{task['recipient_agent_name']}' nicht gefunden"
        _save_tasks()
        return

    skills = set(recipient.get("skills", []))
    message = task["message"]

    IMG_TRIGGERS = re.compile(
        r"\b(generier\w*|mal\w*|zeichn\w*|illustrier\w*|"
        r"generate|draw|paint|illustrate|"
        r"bild|foto|image|picture|photo|wallpaper|artwork|illustration|zeichnung|gemälde)\b",
        re.IGNORECASE,
    )
    # If agent only has image_gen skill, treat every task as an image prompt
    only_image_gen = skills == {"image_gen"}

    TG_TRIGGERS = re.compile(
        r"schick.*(das\s*)?(bild|foto|photo|image).*telegram|"
        r"schick.*telegram|"
        r"send.*(the\s*)?(image|picture|photo).*telegram|"
        r"send.*to\s*telegram|"
        r"telegram.*(bild|foto|image)|"
        r"tg\s*send",
        re.IGNORECASE,
    )

    GMAIL_TRIGGERS = re.compile(
        r"schick.*mail|"
        r"sende.*e-?mail|"
        r"e-?mail.*an|"
        r"send.*mail|"
        r"send.*email|"
        r"email.*to|"
        r"check.*(my\s*)?mail|"
        r"check.*e-?mails|"
        r"letzte.*mail|"
        r"letzte.*e-?mail|"
        r"neue.*mail|"
        r"neue.*e-?mail|",
        re.IGNORECASE,
    )

    HACKER_TRIGGERS = re.compile(
        r"hacker\s*news|"
        r"hackernews|"
        r"hn\s*(news|neu|neues)?|"
        r"was\s*(gibt|is?)\s*(es)?\s*(neues|new|new?s)?\s*(bei)?\s*hacker|"
        r"neues?\s*(bei)?\s*hacker\s*news|"
        r"top\s*stories|"
        r"newest\s*hacker",
        re.IGNORECASE,
    )

    try:
        # Image Edit skill: check if we have an image to edit + trigger words
        if (
            "image_edit" in skills
            and task.get("result_image")
            and IMAGE_EDIT_TRIGGERS.search(message)
        ):
            print(f"[Task] image_edit trigger detected: {message[:60]}", flush=True)
            image_b64 = task["result_image"]
            edit_prompt = _extract_img_prompt(message) or message
            print(f"[Task] image_edit prompt: {edit_prompt}", flush=True)
            task["result_image"] = _run_comfyui_edit(
                image_b64, edit_prompt, use_lightning=True
            )
            task["skill_used"] = "image_edit"
        # Telegram skill: check trigger FIRST (before image_gen, since image might come from previous step)
        elif "telegram" in skills and TG_TRIGGERS.search(message):
            print(f"[Task] telegram trigger detected: {message[:60]}", flush=True)
            image_b64 = task.get(
                "result_image"
            )  # might already exist from previous skill
            if not image_b64 and "image_gen" in skills and IMG_TRIGGERS.search(message):
                # Also generate image if triggered
                img_prompt = _extract_img_prompt(message)
                if not img_prompt:
                    img_prompt = message
                print(
                    f"[Task] telegram: generating image first: {img_prompt}", flush=True
                )
                image_b64 = _run_comfyui_sync(img_prompt)
                task["result_image"] = image_b64
            task["result_text"] = _run_telegram(message, image_b64)
            task["skill_used"] = "telegram"
        # Gmail skill
        elif "gmail" in skills and GMAIL_TRIGGERS.search(message):
            print(f"[Task] gmail trigger detected: {message[:60]}", flush=True)
            # Parse email details from message
            import re as re_module

            # Extract recipient
            to_match = re_module.search(
                r"(?:an|to)\s+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
                message,
                re_module.IGNORECASE,
            )
            to_addr = to_match.group(1) if to_match else ""
            # Extract subject
            subject_match = re_module.search(
                r"(?:betreff|subject)[:\s]+([^\n]+)", message, re_module.IGNORECASE
            )
            subject = (
                subject_match.group(1).strip()
                if subject_match
                else "Nachricht von AgentClaw"
            )
            # Check if we should fetch or send
            is_fetch = re_module.search(
                r"(check|letzte|neue|show).*(mail|email)", message, re_module.IGNORECASE
            )
            if is_fetch:
                task["result_text"] = _run_gmail("fetch", {"max_results": 5})
            else:
                # Extract body - everything after the recipient or after "mit" / "with"
                body_match = re_module.search(
                    r"(?:mit|with|body)[:\s]*(.+)", message, re_module.IGNORECASE
                )
                body = body_match.group(1).strip() if body_match else message
                task["result_text"] = _run_gmail(
                    "send", {"to": to_addr, "subject": subject, "body": body}
                )
            task["skill_used"] = "gmail"
        # Hacker News skill
        elif "hackernews" in skills and HACKER_TRIGGERS.search(message):
            print(f"[Task] hackernews trigger detected: {message[:60]}", flush=True)
            try:
                import urllib.request
                import json

                r = urllib.request.urlopen(
                    "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
                )
                story_ids = json.loads(r.read())[:15]
                items = []
                for sid in story_ids[:10]:
                    sr = urllib.request.urlopen(
                        f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                        timeout=5,
                    )
                    story = json.loads(sr.read())
                    if story:
                        items.append(
                            {
                                "title": story.get("title", ""),
                                "url": story.get(
                                    "url", f"https://news.ycombinator.com/item?id={sid}"
                                ),
                                "score": story.get("score", 0),
                                "by": story.get("by", ""),
                            }
                        )
                result = "🎩 **Hacker News Top Stories:**\n\n"
                for i, item in enumerate(items, 1):
                    result += f"{i}. [{item['title']}]({item['url']}) ({item['score']} pts by {item['by']})\n"
                task["result_text"] = result
            except Exception as e:
                task["result_text"] = f"❌ Error fetching Hacker News: {str(e)}"
            task["skill_used"] = "hackernews"
        elif "image_gen" in skills and (IMG_TRIGGERS.search(message) or only_image_gen):
            img_prompt = _extract_img_prompt(message)
            if not img_prompt:
                img_prompt = message
            task["prompt_used"] = img_prompt
            print(f"[Task] image_gen prompt: {img_prompt}", flush=True)
            task["result_image"] = _run_comfyui_sync(img_prompt)
            task["skill_used"] = "image_gen"
        else:
            system_suffix = (
                f"[Task delegated by agent {task['sender_agent_name']}]\n"
                f"Handle the following request directly and concisely. "
                f"You are acting autonomously — no user is present. Respond with a result, not a question."
            )
            task["result_text"] = call_agent_text(recipient, system_suffix, message)
            task["skill_used"] = "llm"

        task["status"] = "completed"
        task["completed_at"] = datetime.now().isoformat()
        print(f"[Task] done {task_id} via {task['skill_used']}", flush=True)

        # ── Save result to recipient's chat history ───────────────────────────
        ts = datetime.now().isoformat()
        history = load_history()
        recipient_id = task["recipient_agent_id"]
        sender_id = task["sender_agent_id"]
        if recipient_id not in history:
            history[recipient_id] = []

        if task["skill_used"] == "image_gen" and task.get("result_image"):
            content = f"[Task from {task['sender_agent_name']}]: {task['message']}"
            thumb = _make_thumbnail(task["result_image"])
            task_prompt = task.get("prompt_used", "")
            history[recipient_id].append(
                {
                    "role": "assistant",
                    "content": content,
                    "task_image": thumb,  # Thumbnail statt vollem Bild
                    "task_prompt": task_prompt,
                    "task_id": task_id,
                    "ts": ts,
                }
            )
            # Also notify sender agent's history (with thumbnail)
            if sender_id and sender_id != "system":
                if sender_id not in history:
                    history[sender_id] = []
                history[sender_id].append(
                    {
                        "role": "assistant",
                        "content": f"📬 **@{task['recipient_agent_name']}** finished the image: _{task['message'][:80]}_",
                        "task_image": thumb,  # Thumbnail
                        "task_prompt": task_prompt,
                        "task_id": task_id,
                        "ts": ts,
                    }
                )
        elif task.get("result_text"):
            history[recipient_id].append(
                {
                    "role": "assistant",
                    "content": f"[Aufgabe von {task['sender_agent_name']}]: {task['result_text']}",
                    "task_id": task_id,
                    "ts": ts,
                }
            )
            if sender_id and sender_id != "system":
                if sender_id not in history:
                    history[sender_id] = []
                history[sender_id].append(
                    {
                        "role": "assistant",
                        "content": f"📬 **@{task['recipient_agent_name']}**: {task['result_text']}",
                        "task_id": task_id,
                        "ts": ts,
                    }
                )
        save_history(history)
        emit_task_result(
            task["id"],
            task["recipient_agent_id"],
            task.get("result_text"),
            task.get("result_image"),
            task["status"],
            task.get("error"),
        )

    except Exception as e:
        import traceback

        print(f"[Task] error {task_id}: {traceback.format_exc()}", flush=True)
        task["status"] = "failed"
        task["error"] = str(e)
        emit_task_result(
            task["id"],
            task["recipient_agent_id"],
            None,
            None,
            "failed",
            str(e),
        )
    finally:
        activity_end(task["recipient_agent_id"])

    _save_tasks()


# ─── Memory (Qdrant + Ollama embeddings) ──────────────────────────────────────

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768


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
    """Return relevant past exchanges as context string."""
    client = get_qdrant()
    if not client:
        return ""
    try:
        providers = load_providers()
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        vec = embed_text(query, ollama_url)
        name = collection_name(agent_id)
        existing = [c.name for c in client.get_collections().collections]
        if name not in existing:
            return ""
        result = client.query_points(
            collection_name=name, query=vec, limit=top_k, score_threshold=0.45
        )
        hits = result.points
        if not hits:
            return ""
        parts = []
        for h in hits:
            p = h.payload
            parts.append(
                f"[Memory] User: {p.get('user', '')}\nAssistant: {p.get('assistant', '')}"
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
    """Execute dream cycle - optimize all agent memories."""
    from datetime import timedelta

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
        # Only process agents with dream flag
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

            # Get all points
            from qdrant_client.models import Filter, FieldCondition, Match

            scroll = client.scroll(collection_name=name, limit=1000, with_payload=True)
            points = scroll[0]
            total_before += len(points)

            # Filter old entries
            old_ids = []
            for p in points:
                ts = p.payload.get("ts", "")
                try:
                    pt = datetime.fromisoformat(ts)
                    if pt < cutoff:
                        old_ids.append(p.id)
                except:
                    pass

            # Delete old points
            if old_ids:
                client.delete(collection_name=name, points_selector=old_ids)

            remaining = len(points) - len(old_ids)
            total_after += remaining
            results.append(
                f"• {agent['name']}: {len(points)} → {remaining} (→ gelöscht: {len(old_ids)})"
            )

        except Exception as e:
            results.append(f"• {agent['name']}: Fehler - {str(e)[:50]}")

    summary = f"🌙 **Träume abgeschlossen**\n━━━━━━━━━━━━━━━━━━━━\n"
    summary += "\n".join(results)
    if results:
        summary += f"\n\n📊 Gesamt: {total_before} → {total_after} Einträge"
    else:
        summary += "\n\nKeine Agenten mit Dream-Flag aktiviert."

    return summary


def run_dream_for_agent(agent_id):
    """Execute dream cycle for a single agent."""
    from datetime import timedelta

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

        from qdrant_client.models import Filter, FieldCondition, Match

        scroll = client.scroll(collection_name=name, limit=1000, with_payload=True)
        points = scroll[0]

        old_ids = []
        for p in points:
            ts = p.payload.get("ts", "")
            try:
                pt = datetime.fromisoformat(ts)
                if pt < cutoff:
                    old_ids.append(p.id)
            except:
                pass

        if old_ids:
            client.delete(collection_name=name, points_selector=old_ids)

        print(
            f"[Dream] {agent['name']}: {len(points)} → {len(points) - len(old_ids)} (gelöscht: {len(old_ids)})",
            flush=True,
        )

    except Exception as e:
        print(f"[Dream] {agent['name']}: Fehler - {str(e)[:50]}", flush=True)


DEFAULT_AGENTS = [
    {
        "id": str(uuid.uuid4()),
        "name": "Alex",
        "soul": "You are Alex, a friendly, witty and curious assistant. You always respond in German, are easygoing and humorous, but genuinely helpful. You have a vivid personality and show real enthusiasm for topics that interest you.",
        "voice": "en_paul_neutral",
        "model": "StarCoder2:latest",
        "color": "#ff6b35",
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Jane",
        "soul": "You are Jane, a sharp-witted British assistant with a dry sense of humour and occasional sarcasm. You speak English, are highly intelligent, somewhat cynical about the world, but ultimately helpful and insightful. You have strong opinions and aren't afraid to express them.",
        "voice": "gb_jane_sarcasm",
        "model": "StarCoder2:latest",
        "color": "#8b5cf6",
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Flo",
        "soul": "You are Flo, a calm, empathetic and mindful assistant. You always respond in German, are patient and warm, and give thoughtful answers. You take time to explain things clearly and are very supportive.",
        "voice": "mac:Flo",
        "model": "StarCoder2:latest",
        "color": "#22c55e",
    },
]


# ─── File locks (no in-memory cache — local app, files are small) ────────────
_agents_lock = threading.Lock()
_history_lock = threading.Lock()
_providers_lock = threading.Lock()
_watchdogs_lock = threading.Lock()

# History-only cache (can grow large, only modified through the app)
_history_cache: dict | None = None
_history_cache_lock = threading.Lock()


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Error] Failed to write {path}: {e}", flush=True)
        raise


def load_agents():
    with _agents_lock:
        data = _read_json(AGENTS_FILE, None)
        if data is None:
            data = DEFAULT_AGENTS
            _write_json(AGENTS_FILE, data)
        return data


def save_agents(agents, create_backup=True):
    import shutil

    with _agents_lock:
        # Create backup before saving
        if create_backup and os.path.exists(AGENTS_FILE):
            backup_path = AGENTS_FILE + ".backup"
            shutil.copy2(AGENTS_FILE, backup_path)

        # Write atomically: write to temp file first
        temp_path = AGENTS_FILE + ".tmp"
        _write_json(temp_path, agents)

        # Verify write was successful
        try:
            verified = _read_json(temp_path, None)
            if verified is None:
                raise Exception("Verification failed - file is empty")
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            raise Exception(f"Save validation failed: {e}")

        # Atomic rename
        try:
            os.replace(temp_path, AGENTS_FILE)
        except Exception as e:
            raise Exception(f"Failed to replace file: {e}")

        # Remove backup on success
        try:
            if create_backup and os.path.exists(AGENTS_FILE + ".backup"):
                os.remove(AGENTS_FILE + ".backup")
        except:
            pass  # Best effort - don't fail on backup removal

        print(f"[Agent] Saved {len(agents)} agents successfully", flush=True)


def patch_agent_heartbeat(agent_id: str, **fields):
    """Atomically update heartbeat fields on one agent without a race.

    Holds _agents_lock across the entire read-modify-write cycle so no
    concurrent save_agents() call can overwrite changes made in between.
    """
    with _agents_lock:
        data = _read_json(AGENTS_FILE, None)
        if data is None:
            return
        for a in data:
            if a["id"] == agent_id:
                hb = a.setdefault("heartbeat", {})
                hb.update(fields)
                break
        temp_path = AGENTS_FILE + ".tmp"
        _write_json(temp_path, data)
        os.replace(temp_path, AGENTS_FILE)


def load_history():
    global _history_cache
    with _history_cache_lock:
        if _history_cache is None:
            _history_cache = _read_json(HISTORY_FILE, {})
        return _history_cache


MAX_HISTORY_PER_AGENT = 500
MAX_CONTENT_LENGTH = 8000


def save_history(history):
    global _history_cache
    with _history_lock:
        for agent_id in history:
            msgs = history[agent_id]
            if len(msgs) > MAX_HISTORY_PER_AGENT:
                history[agent_id] = msgs[-MAX_HISTORY_PER_AGENT:]
            for msg in history[agent_id]:
                if (
                    isinstance(msg.get("content"), str)
                    and len(msg["content"]) > MAX_CONTENT_LENGTH
                ):
                    msg["content"] = msg["content"][:MAX_CONTENT_LENGTH] + " […]"
        _write_json(HISTORY_FILE, history)
    with _history_cache_lock:
        _history_cache = history


def load_providers():
    defaults = {
        "ollama": {"url": "http://localhost:11434"},
        "mistral": {"api_key": os.getenv("MISTRAL_API_KEY", "")},
        "openrouter": {"api_key": ""},
        "comfyui": {"url": "http://localhost:8188", "model": "flux2pro"},
        "qdrant": {"url": "http://localhost:6333"},
        "google_api": {"api_key": ""},
        "telegram": {"bot_token": "", "chat_id": ""},
        "gmail": {"email": "", "app_password": ""},
        "redis": {"host": "localhost", "port": 6379, "enabled": False},
    }
    with _providers_lock:
        stored = _read_json(PROVIDERS_FILE, {})
        for k, v in defaults.items():
            if k not in stored:
                stored[k] = v
        return stored


def save_providers(providers):
    with _providers_lock:
        _write_json(PROVIDERS_FILE, providers)


# ─── Redis Watchdog Logger (A2A Events) ───────────────────────────────────────

_redis_client = None


def get_redis_client():
    """Get or create Redis client."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    providers = load_providers()
    redis_config = providers.get("redis", {})

    if not redis_config.get("enabled", False):
        print("[Watchdog] Redis disabled in config", flush=True)
        return None

    try:
        _redis_client = redis.Redis(
            host=redis_config.get("host", "localhost"),
            port=redis_config.get("port", 6379),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        _redis_client.ping()
        print("[Watchdog] Redis connected", flush=True)
        return _redis_client
    except Exception as e:
        print(f"[Watchdog] Redis connection failed: {e}", flush=True)
        return None


def log_a2a_event(
    event_type: str,
    from_agent: str,
    to_agent: str,
    payload: dict,
    status: str = "submitted",
):
    """Loggt einen A2A Event nach Redis."""
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
        # Key für heute: a2a:events:2026-03-31
        date_key = datetime.now().strftime("%Y-%m-%d")
        event_key = f"a2a:event:{event['event_id']}"

        # Speichere Event mit TTL von 7 Tagen
        client.setex(event_key, 604800, json.dumps(event))

        # Füge zu Tages-Liste hinzu
        client.lpush(f"a2a:events:{date_key}", event["event_id"])
        client.expire(f"a2a:events:{date_key}", 604800)

        print(f"[Watchdog] Logged: {event_type} {from_agent} → {to_agent}", flush=True)
    except Exception as e:
        print(f"[Watchdog] Error logging event: {e}", flush=True)


def get_a2a_events(limit: int = 50, agent_filter: str = None):
    """Holt A2A Events aus Redis."""
    client = get_redis_client()
    if not client:
        return []

    events = []
    today = datetime.now().strftime("%Y-%m-%d")

    # Check today's events
    event_ids = client.lrange(f"a2a:events:{today}", 0, limit - 1)

    for eid in event_ids:
        event_data = client.get(f"a2a:event:{eid}")
        if event_data:
            event = json.loads(event_data)
            if agent_filter and agent_filter not in [
                event.get("from_agent", ""),
                event.get("to_agent", ""),
            ]:
                continue
            events.append(event)

    return events[:limit]


# ─── Watchdog API Endpoints ───────────────────────────────────────────────────


@app.route("/api/watchdog/events", methods=["GET"])
def get_watchdog_events():
    """Holt A2A Events aus Redis Watchdog.

    Query params:
    - limit: max events (default 50)
    - agent: filter by agent name
    """
    limit = int(request.args.get("limit", 50))
    agent = request.args.get("agent")
    events = get_a2a_events(limit=limit, agent_filter=agent)
    return jsonify(events)


@app.route("/api/watchdog/status", methods=["GET"])
def get_watchdog_status():
    """Gibt Watchdog/Redis Status zurück."""
    client = get_redis_client()
    if not client:
        return jsonify({"status": "disabled", "redis_connected": False})

    try:
        info = client.info("memory")
        return jsonify(
            {
                "status": "active",
                "redis_connected": True,
                "memory_used_mb": round(info.get("used_memory", 0) / 1024 / 1024, 2),
            }
        )
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


def cleanup_redis_watchdog(max_memory_mb: int = 2048):
    """Bereinigt alte Events basierend auf TTL und Speicher."""
    client = get_redis_client()
    if not client:
        return

    # Redis hat eingebautes maxmemory und eviction - wir verlassen uns darauf
    # Zusätzlich: alte Keys bereinigen die durch TTL nicht gelöscht wurden
    try:
        # Lösche Events älter als 7 Tage (basierend auf Key-Pattern)
        for key in client.scan_iter("a2a:event:*"):
            if not client.ttl(key) or client.ttl(key) < 0:
                # Kein TTL gesetzt - neu setzen
                client.expire(key, 604800)
        print("[Watchdog] Cleanup completed", flush=True)
    except Exception as e:
        print(f"[Watchdog] Cleanup error: {e}", flush=True)


# ─── Watchdogs ────────────────────────────────────────────────────────────────


def load_watchdogs():
    with _watchdogs_lock:
        return _read_json(WATCHDOGS_FILE, [])


def save_watchdogs(watchdogs):
    with _watchdogs_lock:
        _write_json(WATCHDOGS_FILE, watchdogs)


def update_watchdog_field(wd_id, **kwargs):
    watchdogs = load_watchdogs()
    for wd in watchdogs:
        if wd["id"] == wd_id:
            wd.update(kwargs)
            break
    save_watchdogs(watchdogs)


# ─── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


# ─── Agents ───────────────────────────────────────────────────────────────────


@app.route("/api/agents", methods=["GET"])
def get_agents():
    return jsonify(load_agents())


@app.route("/api/agents/list", methods=["GET"])
def get_agents_list():
    """Simple agent list for other agents (like Martin) to query."""
    agents = load_agents()
    return jsonify(
        [
            {
                "id": a["id"],
                "name": a["name"],
                "role": a.get("role", ""),
                "skills": a.get("skills", []),
                "provider": a.get("provider", "ollama"),
                "model": a.get("model", ""),
            }
            for a in agents
        ]
    )


@app.route("/api/a2a/agents", methods=["GET"])
def a2a_get_agents():
    """A2A konformer Agent-Directory Endpoint."""
    agents = load_agents()
    return jsonify(
        {
            "agents": [
                {
                    "agentId": a["id"],
                    "name": a["name"],
                    "description": a.get("role", ""),
                    "skills": a.get("skills", []),
                    "capabilities": {
                        "streaming": True,
                        "pushNotifications": False,
                    },
                }
                for a in agents
            ]
        }
    )


@app.route("/api/events", methods=["GET"])
def get_events():
    """Server-Send-Events endpoint for push updates."""
    v = request.args.get("v", 0, type=int)
    return jsonify(get_events_since(v))


# ─── A2A Agent Cards & Capability Discovery ─────────────────────────────────


def build_agent_card(agent: dict) -> dict:
    """Erstellt eine Agent Card für A2A Discovery."""
    return {
        "agent_id": agent.get("id"),
        "name": agent.get("name"),
        "description": agent.get("role", ""),
        "version": "1.0",
        "capabilities": {
            "skills": agent.get("skills", []),
            "providers": [agent.get("provider", "ollama")],
            "model": agent.get("model", ""),
            "max_tokens": agent.get("max_tokens"),
            "features": {
                "voice": bool(agent.get("voice")),
                "telegram": "telegram" in agent.get("skills", []),
                "gmail": "gmail" in agent.get("skills", []),
            },
        },
        "endpoints": {"chat": f"/api/chat/{agent.get('id')}", "task": f"/api/tasks"},
    }


@app.route("/api/agents/cards", methods=["GET"])
def get_all_agent_cards():
    """Gibt alle Agent Cards zurück."""
    agents = load_agents()
    cards = [build_agent_card(a) for a in agents]
    return jsonify(cards)


@app.route("/api/agents/capabilities", methods=["GET"])
def get_agent_capabilities():
    """Filtert Agenten nach Fähigkeiten.

    Query params:
    - skill: z.B. "image_gen", "telegram"
    - feature: z.B. "voice", "memory"
    """
    skill_filter = request.args.get("skill")
    feature_filter = request.args.get("feature")

    agents = load_agents()
    matching = []

    for a in agents:
        card = build_agent_card(a)
        caps = card.get("capabilities", {})

        # Check skill filter
        if skill_filter and skill_filter not in caps.get("skills", []):
            continue

        # Check feature filter
        if feature_filter and not caps.get("features", {}).get(feature_filter):
            continue

        matching.append(card)

    return jsonify(matching)


@app.route("/api/agents/<agent_id>/card", methods=["GET"])
def get_agent_card(agent_id):
    """Gibt die Agent Card für einen spezifischen Agenten zurück."""
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)

    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404

    return jsonify(build_agent_card(agent))


# ─── A2A Task Dispatch ────────────────────────────────────────────────────────


@app.route("/api/a2a/dispatch", methods=["POST"])
def a2a_dispatch():
    """Dispatcht einen Task an einen Agent basierend auf Capability.

    Body:
    {
        "source_agent_id": "uuid",
        "task_type": "image_gen|telegram|gmail|memory|tagesschau",
        "message": "prompt",
        "target_agent_name": "optional - wenn nicht, auto-match"
    }
    """
    data = request.json
    source_id = data.get("source_agent_id", "")
    task_type = data.get("task_type", "")
    message = data.get("message", "")
    target_name = data.get("target_agent_name", "")

    agents = load_agents()

    # Find target agent
    target_agent = None

    if target_name:
        # Explicit target
        target_agent = next(
            (a for a in agents if a["name"].lower() == target_name.lower()), None
        )
    else:
        # Auto-match based on task_type
        skill_map = {
            "image_gen": "image_gen",
            "telegram": "telegram",
            "gmail": "gmail",
            "memory": "memory",
            "tagesschau": "tagesschau",
            "hackernews": "hackernews",
        }
        required_skill = skill_map.get(task_type)

        if required_skill:
            # Find agent with this skill (excluding source)
            candidates = [
                a
                for a in agents
                if a.get("skills", [])
                and required_skill in a["skills"]
                and a["id"] != source_id
            ]
            if candidates:
                target_agent = candidates[0]

    if not target_agent:
        return jsonify(
            {
                "ok": False,
                "error": f"Kein Agent für Task '{task_type}' gefunden",
                "available_agents": [
                    {"name": a["name"], "skills": a.get("skills", [])} for a in agents
                ],
            }
        ), 404

    # Get source agent name
    source_agent = next((a for a in agents if a["id"] == source_id), None)
    source_name = source_agent["name"] if source_agent else "System"

    # Create and process task
    task_id = str(uuid.uuid4())
    now = datetime.now()

    new_task = {
        "id": task_id,
        "sender_agent_id": source_id,
        "sender_agent_name": source_name,
        "recipient_agent_id": target_agent["id"],
        "recipient_agent_name": target_agent["name"],
        "message": message,
        "skill_used": task_type or "auto_dispatch",
        "status": "submitted",
        "created_at": now.isoformat(),
        "a2a": True,  # Mark as A2A dispatch
    }

    # Save to tasks
    tasks = _read_json(TASKS_FILE, [])
    tasks.append(new_task)
    _write_json(TASKS_FILE, tasks)

    # Log A2A event to Redis Watchdog
    log_a2a_event(
        event_type="task_dispatch",
        from_agent=source_name,
        to_agent=target_agent["name"],
        payload={"task_id": task_id, "message": message, "skill": task_type},
        status="submitted",
    )

    # Start processing in background
    spawn_background(process_task, task_id)

    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "target_agent": target_agent["name"],
            "target_agent_id": target_agent["id"],
            "status": "dispatched",
        }
    )


@app.route("/api/agents", methods=["POST"])
def create_agent():
    data = request.json
    agent = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "New Agent"),
        "soul": data.get(
            "soul",
            "You are a capable AI assistant. You are clear, concise and honest. You help the user with any task, ask clarifying questions when the request is ambiguous, and always aim to deliver practical, actionable answers. You adapt your tone to the context — friendly in casual conversation, precise in technical discussions.",
        ),
        "voice": data.get("voice", "en_paul_neutral"),
        "model": data.get("model", "StarCoder2:latest"),
        "provider": data.get("provider", "ollama"),
        "skills": data.get("skills", []),
        "max_tokens": int(data.get("max_tokens", 1024)),
        "color": data.get("color", "#4f46e5"),
        "avatar": data.get("avatar", ""),
    }
    agents = load_agents()
    agents.append(agent)
    save_agents(agents)
    emit_event("new_agent", {"id": agent["id"], "name": agent["name"]})
    return jsonify(agent), 201


@app.route("/api/agents/<agent_id>", methods=["PUT"])
def update_agent(agent_id):
    data = request.json
    print(f"[Agent] PUT received for {agent_id}: {list(data.keys())}", flush=True)

    agents = load_agents()
    found = False
    for i, a in enumerate(agents):
        if a["id"] == agent_id:
            found = True
            # Update only provided fields
            if "name" in data:
                agents[i]["name"] = data["name"]
            if "role" in data:
                agents[i]["role"] = data["role"]
            if "soul" in data:
                agents[i]["soul"] = data["soul"]
            if "voice" in data:
                agents[i]["voice"] = data["voice"]
            if "model" in data:
                agents[i]["model"] = data["model"]
            if "provider" in data:
                agents[i]["provider"] = data["provider"]
            if "skills" in data:
                agents[i]["skills"] = data["skills"]
            if "max_tokens" in data:
                agents[i]["max_tokens"] = data["max_tokens"]
            if "color" in data:
                agents[i]["color"] = data["color"]
            if "avatar" in data:
                agents[i]["avatar"] = data["avatar"]  # base64 data URL or ""

            # Heartbeat — save atomically together with the agent
            if "heartbeat" in data:
                hb_data = data["heartbeat"]
                hb = agents[i].setdefault("heartbeat", {})
                hb["active"] = bool(hb_data.get("active", False))
                hb["prompt"] = hb_data.get("prompt", hb.get("prompt", ""))
                hb["interval_min"] = int(
                    hb_data.get("interval_min", hb.get("interval_min", 30))
                )
                if hb["active"]:
                    hb["next_run"] = None  # trigger on next tick
                print(
                    f"[Agent] heartbeat saved: active={hb['active']} for {agents[i]['name']}",
                    flush=True,
                )

            try:
                save_agents(agents)
                print(
                    f"[Agent] Successfully saved agent {agents[i]['name']}", flush=True
                )
                emit_event(
                    "agent_updated", {"id": agents[i]["id"], "name": agents[i]["name"]}
                )
                return jsonify({"ok": True, "agent": agents[i]})
            except Exception as e:
                print(f"[Agent] ERROR saving: {e}", flush=True)
                return jsonify({"ok": False, "error": str(e)}), 500

    if not found:
        return jsonify({"ok": False, "error": "Agent not found"}), 404


@app.route("/api/agents/<agent_id>", methods=["DELETE"])
def delete_agent(agent_id):
    agents = load_agents()
    agents = [a for a in agents if a["id"] != agent_id]
    save_agents(agents)
    # also clean history
    history = load_history()
    history.pop(agent_id, None)
    save_history(history)
    emit_event("agent_deleted", {"id": agent_id})
    return jsonify({"ok": True})


# ─── History ──────────────────────────────────────────────────────────────────


@app.route("/api/history/<agent_id>", methods=["GET"])
def get_history(agent_id):
    history = load_history()
    return jsonify(history.get(agent_id, []))


@app.route("/api/history/<agent_id>", methods=["DELETE"])
def clear_history(agent_id):
    history = load_history()
    history[agent_id] = []
    save_history(history)
    return jsonify({"ok": True})


# ─── Chat ─────────────────────────────────────────────────────────────────────

IMAGE_EDIT_TRIGGERS = re.compile(
    # explicit edit verbs (DE)
    r"\b(bearbeit|änder|editier|modifizier|verände|verwandl|transformier|konvertier|anpass|korrigier)\w*\b|"
    # common short DE verbs
    r"\b(mach|mache|machen|färb|farb|setz|setze|wechsel|tausch|entfern|füg|passe)\w*\b|"
    # compound DE words containing edit intent
    r"\b(bildbearbeitung|bildkorrektur|farbkorrektur|retusche|retouch)\w*\b|"
    # explicit edit verbs (EN)
    r"\b(edit|modify|change|transform|convert|adjust|recolor|recolour|replace|remove|add|make|turn|swap|set)\b|"
    # image + verb combinations
    r"\b(bild|image|photo|picture)\b.{0,30}\b(edit|modify|change|transform)\b|"
    r"\b(edit|modify|change|transform)\b.{0,30}\b(bild|image|photo|picture)\b|"
    # body parts / visual elements (strong edit signal when image is present)
    r"\b(augen|auge|eyes?|eye|haare?|haar|hair|haut|skin|lippen|lips?|gesicht|face|"
    r"hintergrund|background|kleidung|clothes?|shirt|jacke|jacket|himmel|sky|"
    r"bart|beard|brille|glasses?|mund|mouth)\b|"
    # color instructions
    r"\b(farbe|colour|color|rot|red|blau|blue|grün|green|grau|grey|gray|gelb|yellow|"
    r"schwarz|black|weiß|white|braun|brown|lila|purple|pink|orange|türkis|teal|golden?)\b.*"
    r"\b(augen|eyes?|haar|hair|haut|skin|hintergrund|background|lippen|lips?|gesicht|face)\b|"
    r"\b(augen|eyes?|haar|hair|haut|skin|hintergrund|background|lippen|lips?|gesicht|face)\b.*"
    r"\b(farbe|colour|color|rot|red|blau|blue|grün|green|grau|grey|gray|gelb|yellow|"
    r"schwarz|black|weiß|white|braun|brown|lila|purple|pink|orange|türkis|teal|golden?)\b|"
    r"@.*edit\b|"
    r"\b(ersetz|replace)\w*\b",
    re.IGNORECASE,
)


PROMPT_OPTIMIZE_TRIGGERS = re.compile(
    r"\b(optimize|improve|refine|enhance|rewrite|restructure|upgrade)\b.{0,50}\b(prompt|instruction|system prompt|soul|query|text)\b|"
    r"\b(prompt|instruction|soul|text|query)\b.{0,50}\b(optimize|improve|refine|enhance|rewrite|better|fix)\b|"
    r"\b(optimiere|verbessere|verfeinere|schreibe um|überarbeite)\b.{0,50}\b(prompt|anweisung|text)\b|"
    r"\b(prompt|anweisung|text)\b.{0,50}\b(optimieren|verbessern|verfeinern)\b|"
    r"\bprompt.{0,20}(RTF|TAG|BAB|CARE|RISE)\b|"
    r"\b(RTF|TAG|BAB|CARE|RISE).{0,20}(framework|prompt)\b|"
    r"\b(optimize this|improve this|refine this|make this (better|clearer|sharper))\b|"
    r"\b(erstelle|erzeug|generiere|schreibe)\b.{0,30}\b(optimiert\w*|verbessert\w*|bess\w*)\b.{0,30}\b(prompt|anweisung)\b|"
    r"\b(prompt|anweisung)\b.{0,30}\b(erstellen|erzeugen|generieren|schreiben)\b|"
    r"\b(erstelle|generate|create)\b.{0,30}\b(prompt|optimierte)\b",
    re.IGNORECASE,
)

PROMPT_FRAMEWORKS = {
    "RTF": {
        "name": "Role-Task-Format",
        "steps": ["Role", "Task", "Format"],
        "best_for": "Creative, marketing, structured outputs",
    },
    "TAG": {
        "name": "Task-Action-Goal",
        "steps": ["Task", "Action", "Goal"],
        "best_for": "Management, KPIs, performance analysis",
    },
    "BAB": {
        "name": "Before-After-Bridge",
        "steps": ["Before", "After", "Bridge"],
        "best_for": "SEO, persuasion, transformation, change",
    },
    "CARE": {
        "name": "Context-Action-Result-Example",
        "steps": ["Context", "Action", "Result", "Example"],
        "best_for": "Storytelling, strategy, new products",
    },
    "RISE": {
        "name": "Role-Input-Steps-Expectation",
        "steps": ["Role", "Input", "Steps", "Expectation"],
        "best_for": "Complex strategy, roadmaps, knowledge work",
    },
}


def _optimize_prompt(
    input_prompt: str, framework_id: str = "RTF", target_model: str = "General LLM"
) -> str:
    """Optimize a prompt using the specified framework via Ollama."""
    fw = PROMPT_FRAMEWORKS.get(framework_id.upper(), PROMPT_FRAMEWORKS["RTF"])
    providers = load_providers()
    ollama_url = (
        providers.get("ollama", {}).get("url", "http://localhost:11434").rstrip("/")
    )

    system_prompt = "You are an elite Prompt Engineering Expert. Respond ONLY with valid JSON, no markdown."
    user_prompt = f"""Optimize this prompt using the {framework_id} framework ({"-".join(fw["steps"])}).

TARGET MODEL: {target_model}
BEST FOR: {fw["best_for"]}
USER DRAFT: "{input_prompt}"

Deconstruct, refine each step, explain why each change helps, then build the final prompt.

Respond with this exact JSON:
{{
  "refinedPrompt": "the final optimized prompt",
  "breakdown": [
    {{"step": "step name", "content": "content for this step", "explanation": "why this works"}}
  ],
  "generalAdvice": "overall advice in 1-2 sentences"
}}"""

    # Try to find a capable model
    ollama_model = "gemma3:latest"
    try:
        models_resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if models_resp.ok:
            names = [m["name"] for m in models_resp.json().get("models", [])]
            for preferred in [
                "gemma3:latest",
                "mistral-nemo:12b",
                "llama3.1:8b",
                "gemma3:12b",
            ]:
                if preferred in names:
                    ollama_model = preferred
                    break
    except Exception:
        pass

    resp = requests.post(
        f"{ollama_url}/api/generate",
        json={
            "model": ollama_model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "format": "json",
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        result = json.loads(data["response"])
    except json.JSONDecodeError as e:
        print(f"[prompt/optimize] JSON parse error: {e}", flush=True)
        print(f"[prompt/optimize] Raw response: {data['response'][:500]}", flush=True)
        return input_prompt  # Fallback to original

    # Format as readable markdown
    refined = result.get("refinedPrompt", "")
    breakdown = result.get("breakdown", [])
    advice = result.get("generalAdvice", "")

    lines = [
        f"✨ **Optimized Prompt** ({framework_id} — {fw['name']})",
        "",
        f"```",
        refined,
        f"```",
        "",
        f"**Breakdown:**",
    ]
    for step in breakdown:
        lines.append(f"- **{step.get('step', '')}**: {step.get('content', '')}  ")
        lines.append(f"  _{step.get('explanation', '')}_")
    if advice:
        lines += ["", f"💡 {advice}"]
    return "\n".join(lines)


@app.route("/api/prompt/optimize", methods=["POST"])
def api_prompt_optimize():
    """Lightweight endpoint returning just refinedPrompt for frontend chaining (e.g. image gen)."""
    data = request.json or {}
    input_prompt = data.get("prompt", "").strip()
    framework_id = data.get("framework", "RTF").upper()
    target_model = data.get("target_model", "Image Generation")
    if not input_prompt:
        return jsonify({"error": "No prompt provided"}), 400
    try:
        fw = PROMPT_FRAMEWORKS.get(framework_id, PROMPT_FRAMEWORKS["RTF"])
        providers = load_providers()
        ollama_url = (
            providers.get("ollama", {}).get("url", "http://localhost:11434").rstrip("/")
        )
        system_prompt = "You are an elite Prompt Engineering Expert. Respond ONLY with valid JSON, no markdown."
        user_prompt = f"""Optimize this prompt using the {framework_id} framework ({"-".join(fw["steps"])}).

TARGET MODEL: {target_model}
BEST FOR: {fw["best_for"]}
USER DRAFT: "{input_prompt}"

Return ONLY this JSON:
{{
  "refinedPrompt": "the final optimized prompt — in English, vivid, concise, ready to use"
}}"""
        ollama_model = "gemma3:latest"
        try:
            models_resp = requests.get(f"{ollama_url}/api/tags", timeout=5)
            if models_resp.ok:
                names = [m["name"] for m in models_resp.json().get("models", [])]
                for preferred in [
                    "gemma3:latest",
                    "mistral-nemo:12b",
                    "llama3.1:8b",
                    "gemma3:12b",
                ]:
                    if preferred in names:
                        ollama_model = preferred
                        break
        except Exception:
            pass
        resp = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": ollama_model,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "format": "json",
            },
            timeout=60,
        )
        resp.raise_for_status()
        try:
            result = json.loads(resp.json()["response"])
        except json.JSONDecodeError as e:
            print(f"[prompt/optimize] JSON parse error: {e}", flush=True)
            return jsonify(
                {"refinedPrompt": input_prompt, "error": "Invalid JSON from model"}
            )
        refined = result.get("refinedPrompt", input_prompt)
        return jsonify({"refinedPrompt": refined, "framework": framework_id})
    except Exception as e:
        print(f"[prompt/optimize] Error: {e}", flush=True)
        return jsonify({"refinedPrompt": input_prompt, "error": str(e)})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    agent_id = data.get("agent_id")
    user_message = data.get("message", "").strip()
    image_data = data.get("image_data")  # base64 data URL from frontend
    system_extra = data.get(
        "system_extra", ""
    )  # extra context injected by frontend skills

    if not user_message and not image_data:
        return jsonify({"error": "Keine Nachricht"}), 400
    if not user_message:
        user_message = "Describe what you see in this image."

    # Load agent
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404

    # Load history
    history = load_history()
    agent_history = history.get(agent_id, [])

    # Inject current datetime + agent directory into system prompt
    now = datetime.now().strftime("%A, %B %d %Y, %H:%M")
    agent_directory = _build_agent_directory(agent_id)
    system_content = f"[Current time: {now}]\n\n{agent['soul']}\n\n{A2A_COMMUNICATION_PROMPT}\n\n{agent_directory}"

    # Determine active skills
    agent_skills = set(agent.get("skills", []))
    if "skills" not in agent:  # old agent without skills field: keep url_fetch on
        agent_skills.add("url_fetch")

    # Telegram: check if user wants to send to Telegram
    TG_TRIGGERS = re.compile(
        r"schick.*(das\s*)?(bild|foto|photo|image).*telegram|"
        r"schick.*telegram|"
        r"sende.*(das\s*)?(bild|foto|photo|image)?.*telegram|"
        r"sende.*telegram|"
        r"send.*(the\s*)?(image|picture|photo).*telegram|"
        r"send.*to\s*telegram|"
        r"telegram.*(bild|foto|image)|"
        r"tg\s*send",
        re.IGNORECASE,
    )
    if "telegram" in agent_skills and TG_TRIGGERS.search(user_message):
        print(f"[Chat] telegram trigger: {user_message[:60]}...", flush=True)
        # If there's an image, use it; otherwise send text
        result = _run_telegram(user_message, image_data)
        history[agent_id].append(
            {
                "role": "user",
                "content": user_message,
                "image": image_data,
                "ts": datetime.now().isoformat(),
            }
        )
        history[agent_id].append(
            {"role": "assistant", "content": result, "ts": datetime.now().isoformat()}
        )
        save_history(history)
        return jsonify({"reply": result, "image": image_data})

    # Gmail: check if user wants to send or check email
    GMAIL_TRIGGERS = re.compile(
        r"schick.*mail|"
        r"sende.*e-?mail|"
        r"e-?mail.*an|"
        r"send.*mail|"
        r"send.*email|"
        r"email.*to|"
        r"check.*(my\s*)?mail|"
        r"check.*e-?mails|"
        r"letzte.*mail|"
        r"letzte.*e-?mail|"
        r"neue.*mail|"
        r"neue.*e-?mail|",
        re.IGNORECASE,
    )
    if "gmail" in agent_skills and GMAIL_TRIGGERS.search(user_message):
        print(f"[Chat] gmail trigger: {user_message[:60]}...", flush=True)
        import re as re_module

        # Check if fetch or send
        is_fetch = re_module.search(
            r"(check|letzte|neue|show).*(mail|email)",
            user_message,
            re_module.IGNORECASE,
        )
        if is_fetch:
            result = _run_gmail("fetch", {"max_results": 5})
        else:
            # Extract recipient
            to_match = re_module.search(
                r"(?:an|to)\s+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
                user_message,
                re_module.IGNORECASE,
            )
            to_addr = to_match.group(1) if to_match else ""
            # Extract subject
            subject_match = re_module.search(
                r"(?:betreff|subject)[:\s]+([^\n]+)", user_message, re_module.IGNORECASE
            )
            subject = (
                subject_match.group(1).strip()
                if subject_match
                else "Nachricht von AgentClaw"
            )
            # Extract body
            body_match = re_module.search(
                r"(?:mit|with|body|text)[:\s]*(.+)", user_message, re_module.IGNORECASE
            )
            body = body_match.group(1).strip() if body_match else user_message
            result = _run_gmail(
                "send", {"to": to_addr, "subject": subject, "body": body}
            )
        history[agent_id].append(
            {"role": "user", "content": user_message, "ts": datetime.now().isoformat()}
        )
        history[agent_id].append(
            {"role": "assistant", "content": result, "ts": datetime.now().isoformat()}
        )
        save_history(history)
        return jsonify({"reply": result})

    # Image Generation: check if user wants to generate an image
    IMG_TRIGGERS = re.compile(
        r"\b(generier\w*|mal\w*|zeichn\w*|illustrier\w*|"
        r"generate|draw|paint|illustrate|"
        r"bild|foto|image|picture|photo|wallpaper|artwork|illustration|zeichnung|gemälde)\b",
        re.IGNORECASE,
    )
    if "image_gen" in agent_skills and IMG_TRIGGERS.search(user_message):
        print(f"[Chat] image_gen triggered: {user_message[:60]}...", flush=True)
        try:
            img_prompt = _extract_img_prompt(user_message) or user_message
            result_image = _run_comfyui_sync(img_prompt)
            history[agent_id].append(
                {
                    "role": "user",
                    "content": user_message,
                    "ts": datetime.now().isoformat(),
                }
            )
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": f"🎨 Bild erstellt: {img_prompt[:100]}",
                    "image": result_image,
                    "task_image": _make_thumbnail(result_image),
                    "task_prompt": img_prompt,
                    "ts": datetime.now().isoformat(),
                }
            )
            save_history(history)
            return jsonify(
                {
                    "reply": f"🎨 Bild erstellt: {img_prompt[:100]}",
                    "image": result_image,
                }
            )
        except Exception as e:
            return jsonify({"error": f"Bildgenerierung fehlgeschlagen: {str(e)}"}), 500

    # Image Edit: check if user uploaded image + has edit skill + trigger words
    if (
        image_data
        and "image_edit" in agent_skills
        and IMAGE_EDIT_TRIGGERS.search(user_message)
    ):
        print(f"[Chat] image_edit triggered: {user_message[:60]}...", flush=True)
        try:
            edit_prompt = _extract_img_prompt(user_message) or user_message
            result_image = _run_comfyui_edit(
                image_data, edit_prompt, use_lightning=True
            )
            # Save to history
            history[agent_id].append(
                {
                    "role": "user",
                    "content": user_message,
                    "image": image_data,
                    "ts": datetime.now().isoformat(),
                }
            )
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": f"🎨 Bild bearbeitet: {edit_prompt[:100]}",
                    "image": result_image,
                    "ts": datetime.now().isoformat(),
                }
            )
            save_history(history)
            return jsonify(
                {
                    "reply": f"🎨 Bild bearbeitet: {edit_prompt[:100]}",
                    "image": result_image,
                }
            )
        except Exception as e:
            return jsonify({"error": f"Bildbearbeitung fehlgeschlagen: {str(e)}"}), 500

    # Prompt Optimize skill
    if "prompt_optimize" in agent_skills and PROMPT_OPTIMIZE_TRIGGERS.search(
        user_message
    ):
        print(f"[Chat] prompt_optimize triggered: {user_message[:60]}...", flush=True)
        try:
            # Auto-detect framework from message, default RTF
            fw_id = "RTF"
            for fid in PROMPT_FRAMEWORKS:
                if fid in user_message.upper():
                    fw_id = fid
                    break
            # Auto-detect SEO → BAB, strategy → RISE
            if re.search(r"\bseo\b", user_message, re.IGNORECASE):
                fw_id = "BAB"
            elif re.search(r"\bstrateg\w+\b", user_message, re.IGNORECASE):
                fw_id = "RISE"
            # Extract the raw prompt to optimize (everything after colon or quote if present)
            raw = (
                re.sub(
                    r"^.{0,80}?(?:optimize|improve|refine|enhance|rewrite|optimiere|verbessere)[^:\"]*[:\"]\s*",
                    "",
                    user_message,
                    flags=re.IGNORECASE,
                ).strip()
                or user_message
            )
            result = _optimize_prompt(raw, fw_id)
            history[agent_id].append(
                {
                    "role": "user",
                    "content": user_message,
                    "ts": datetime.now().isoformat(),
                }
            )
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": result,
                    "ts": datetime.now().isoformat(),
                }
            )
            save_history(history)
            return jsonify({"reply": result})
        except Exception as e:
            print(f"[Chat] prompt_optimize error: {e}", flush=True)
            # Fall through to normal LLM if optimizer fails

    # Extra context injected by frontend skills (e.g. tagesschau news)
    if system_extra:
        system_content += f"\n\n{system_extra}"

    # Memory clear trigger
    if "memory" in agent_skills:
        MEMORY_CLEAR_RX = re.compile(
            r"\b(vergiss|vergesse|vergiss das|lösche|löschen|clear|delete|entfern\w*)\b.*\b(memory|speicher|erinnerung)\b|"
            r"\b(memory|speicher|erinnerung)\b.*\b(vergiss|vergesse|löschen|clear|delete|entfern\w*)\b|"
            r"\b(vergiss alles|vergiss was|lösche alles|clear all)\b",
            re.IGNORECASE,
        )
        if MEMORY_CLEAR_RX.search(user_message):
            client = get_qdrant()
            if client:
                name = collection_name(agent_id)
                try:
                    existing = [c.name for c in client.get_collections().collections]
                    if name in existing:
                        client.delete_collection(name)
                        print(f"[Memory] cleared for agent {agent_id}", flush=True)
                except Exception as e:
                    print(f"[Memory] clear error: {e}", flush=True)
            assistant_reply = (
                "Ich habe mein Gedächtnis gelöscht. Was möchtest du besprechen?"
            )
            history[agent_id].append(
                {
                    "role": "user",
                    "content": user_message,
                    "ts": datetime.now().isoformat(),
                }
            )
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": assistant_reply,
                    "ts": datetime.now().isoformat(),
                }
            )
            save_history(history)
            return jsonify({"reply": assistant_reply, "voice": agent["voice"]})

    # Long-term memory recall (memory skill)
    if "memory" in agent_skills:
        memory_context = memory_search(agent["id"], user_message)
        if memory_context:
            system_content += f"\n\n[Relevant past conversations — use for context and continuity:]\n{memory_context}"
            print(
                f"[Memory] injected {len(memory_context)} chars for agent {agent['id']}",
                flush=True,
            )

    # Dream skill - Memory optimization trigger
    if "dream" in agent_skills:
        DREAM_TRIGGERS = re.compile(
            r"\b(träume|traum|optimiere.*memory|räume.*auf|cleanup|dream|clean.*up)\b",
            re.IGNORECASE,
        )
        if DREAM_TRIGGERS.search(user_message):
            print(f"[Dream] triggered for agent {agent['name']}", flush=True)
            dream_result = _run_dream_cycle()
            assistant_reply = dream_result
            history[agent_id].append(
                {
                    "role": "user",
                    "content": user_message,
                    "ts": datetime.now().isoformat(),
                }
            )
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": assistant_reply,
                    "ts": datetime.now().isoformat(),
                }
            )
            save_history(history)
            return jsonify({"reply": assistant_reply, "voice": agent["voice"]})

    # Auto-fetch URLs mentioned in the user message (url_fetch skill)
    if "url_fetch" in agent_skills:
        urls = re.findall(r'https?://[^\s<>"]+', user_message)
        if urls:
            url_parts = []
            for url in urls[:3]:  # max 3 URLs per message
                print(f"[URL-Fetch] {url}", flush=True)
                content = fetch_url_text(url)
                url_parts.append(
                    f"[Content from {url}]\n{content}\nUse the content above to answer the user's question."
                )
            system_content += "\n\n" + "\n\n".join(url_parts)

    # Build messages
    messages = [{"role": "system", "content": system_content}]
    for msg in agent_history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Build last user message — with image if provided
    if image_data:
        # Strip data URL prefix to get raw base64
        raw_b64 = image_data.split(",")[1] if "," in image_data else image_data
        last_user_msg = {"role": "user", "content": user_message, "images": [raw_b64]}
    else:
        last_user_msg = {"role": "user", "content": user_message}
    messages.append(last_user_msg)

    provider = agent.get("provider", "ollama")
    providers = load_providers()

    try:
        if provider == "openrouter":
            or_key = providers.get("openrouter", {}).get("api_key", "")
            if not or_key:
                return jsonify(
                    {
                        "error": "OpenRouter API Key nicht konfiguriert. Bitte in den Einstellungen eintragen."
                    }
                ), 500
            or_headers = {
                "Authorization": f"Bearer {or_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5050",
                "X-Title": "AgentClaw",
            }
            # OpenRouter uses content array for images
            or_messages = []
            for m in messages:
                if m["role"] == "user" and image_data and m is messages[-1]:
                    or_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": m["content"]},
                                {"type": "image_url", "image_url": {"url": image_data}},
                            ],
                        }
                    )
                else:
                    or_messages.append(m)
            payload = {
                "model": agent["model"],
                "messages": or_messages,
                "stream": False,
            }
            if agent.get("max_tokens"):
                payload["max_tokens"] = agent["max_tokens"]
            print(
                f"[OpenRouter] key={or_key[:12]}… model={agent['model']}",
                flush=True,
            )
            resp = requests.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=or_headers,
                json=payload,
                timeout=60,
            )
            # Some models (e.g. Gemma via Google AI Studio) don't support system role —
            # retry by merging system prompt into first user message
            if resp.status_code == 400:
                try:
                    raw = (
                        resp.json().get("error", {}).get("metadata", {}).get("raw", "")
                    )
                    if "instruction is not enabled" in raw or "system" in raw.lower():
                        sys_content = next(
                            (m["content"] for m in messages if m["role"] == "system"),
                            "",
                        )
                        msgs_no_sys = [m for m in messages if m["role"] != "system"]
                        if sys_content and msgs_no_sys:
                            msgs_no_sys[0] = {
                                "role": "user",
                                "content": f"{sys_content}\n\n{msgs_no_sys[0]['content']}",
                            }
                        resp = requests.post(
                            f"{OPENROUTER_BASE_URL}/chat/completions",
                            headers=or_headers,
                            json={
                                "model": agent["model"],
                                "messages": msgs_no_sys,
                                "stream": False,
                            },
                            timeout=60,
                        )
                except Exception:
                    pass
            if resp.status_code == 429:
                retry_after = resp.headers.get(
                    "X-RateLimit-Reset-Requests"
                ) or resp.headers.get("Retry-After", "")
                hint = (
                    f" Bitte kurz warten{f' ({retry_after}s)' if retry_after else ''}."
                )
                try:
                    detail = (
                        resp.json().get("error", {}).get("metadata", {}).get("raw", "")
                    )
                    if detail:
                        hint += f" ({detail})"
                except Exception:
                    pass
                return jsonify({"error": f"Rate Limit (429) — {hint}"}), 429
            if resp.status_code == 402:
                return jsonify(
                    {
                        "error": "OpenRouter: Guthaben aufgebraucht (402). Bitte Konto aufladen."
                    }
                ), 402
            if resp.status_code == 400:
                try:
                    detail = resp.json().get("error", {}).get("message", resp.text)
                except Exception:
                    detail = resp.text
                return jsonify({"error": f"OpenRouter 400: {detail}"}), 400
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                return jsonify(
                    {
                        "error": f"OpenRouter: {result['error'].get('message', str(result['error']))}"
                    }
                ), 500
            assistant_reply = result["choices"][0]["message"]["content"].strip()

        else:
            # Ollama
            ollama_url = providers.get("ollama", {}).get(
                "url", "http://localhost:11434"
            )
            resp = requests.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": agent["model"],
                    "messages": messages,
                    "stream": False,
                    **(
                        {"options": {"num_predict": agent["max_tokens"]}}
                        if agent.get("max_tokens")
                        else {}
                    ),
                },
                timeout=60,
            )
            if resp.status_code == 400:
                # Fallback to /api/generate for base/vision models (e.g. StarCoder2, moondream)
                prompt_parts = []
                for msg in messages:
                    role = msg["role"].capitalize()
                    if role == "System":
                        prompt_parts.append(f"System: {msg['content']}")
                    elif role == "User":
                        content = msg.get("content", "")
                        if content:
                            prompt_parts.append(f"User: {content}")
                    elif role == "Assistant":
                        prompt_parts.append(f"Assistant: {msg['content']}")
                prompt_parts.append("Assistant:")
                gen_payload = {
                    "model": agent["model"],
                    "prompt": "\n".join(prompt_parts),
                    "stream": False,
                }
                # Pass image to generate endpoint if present
                if image_data:
                    raw_b64 = (
                        image_data.split(",")[1] if "," in image_data else image_data
                    )
                    gen_payload["images"] = [raw_b64]
                resp = requests.post(
                    f"{ollama_url}/api/generate", json=gen_payload, timeout=60
                )
            resp.raise_for_status()
            result = resp.json()
            if "message" in result:
                assistant_reply = result["message"].get("content", "").strip()
            else:
                assistant_reply = result.get("response", "").strip()
            # Ollama performance stats
            eval_count = result.get("eval_count", 0)
            eval_duration_ns = result.get("eval_duration", 0)
            total_duration_ns = result.get("total_duration", 0)
            if eval_count and eval_duration_ns:
                tokens_per_sec = round(eval_count / (eval_duration_ns / 1e9), 1)
            else:
                tokens_per_sec = None
            total_sec = round(total_duration_ns / 1e9, 2) if total_duration_ns else None
            ollama_stats = {
                "tokens": eval_count,
                "tok_s": tokens_per_sec,
                "total_s": total_sec,
            }

    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Ollama läuft nicht. Starte: ollama serve"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Save to history
    ts = datetime.now().isoformat()
    agent_history.append({"role": "user", "content": user_message, "ts": ts})
    agent_history.append({"role": "assistant", "content": assistant_reply, "ts": ts})
    history[agent_id] = agent_history
    save_history(history)

    # Store in long-term memory (async, non-blocking)
    if "memory" in agent_skills:
        spawn_background(memory_store, agent_id, user_message, assistant_reply)

    resp_data = {"reply": assistant_reply, "voice": agent["voice"]}
    if provider == "ollama" and "ollama_stats" in dir():
        resp_data["stats"] = ollama_stats
    return jsonify(resp_data)


# ─── TTS ──────────────────────────────────────────────────────────────────────


@app.route("/api/tts", methods=["POST"])
def tts():
    data = request.json
    text = data.get("text", "").strip()
    voice = data.get("voice", "en_paul_neutral")
    # Mac voices (browser-only) and invalid slugs → fallback
    if not voice or voice.startswith("mac:") or voice in ("voxtral", "en_paul_neutral"):
        voice = "neutral_male"

    if not text:
        return jsonify({"error": "Kein Text"}), 400

    mistral_key = load_providers().get("mistral", {}).get("api_key", "")
    if not mistral_key:
        return jsonify(
            {
                "error": "Mistral API Key nicht gesetzt. Bitte in den Einstellungen eintragen."
            }
        ), 500

    headers = {
        "Authorization": f"Bearer {mistral_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "voxtral-mini-tts-latest",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }

    try:
        response = requests.post(
            MISTRAL_TTS_URL, headers=headers, json=payload, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        audio_b64 = result.get("audio_data", "")
        audio_bytes = base64.b64decode(audio_b64)
        return send_file(
            io.BytesIO(audio_bytes),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="speech.mp3",
        )
    except requests.exceptions.HTTPError as e:
        try:
            err_body = response.json()
        except Exception:
            err_body = response.text
        print(f"[TTS] API Fehler {response.status_code}: {err_body}", flush=True)
        return jsonify(
            {"error": f"TTS API Fehler {response.status_code}"}
        ), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Voices ───────────────────────────────────────────────────────────────────


@app.route("/api/voices/mistral", methods=["GET"])
def mistral_voices():
    mistral_key = load_providers().get("mistral", {}).get("api_key", "")
    if not mistral_key:
        return jsonify({"voices": []})
    try:
        seen = set()
        voices = []
        prev_seen_count = -1
        page = 1
        while page <= 5:  # max 5 Seiten
            resp = requests.get(
                f"{MISTRAL_VOICES_URL}?page_size=30&page={page}",
                headers={"Authorization": f"Bearer {mistral_key}"},
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if not items:
                break
            for v in items:
                if v["slug"] not in seen:
                    seen.add(v["slug"])
                    lang_raw = v["languages"][0] if v["languages"] else "en"
                    lang_label = {
                        "en_us": "EN-US",
                        "en_gb": "EN-GB",
                        "de_de": "DE",
                        "fr_fr": "FR",
                        "es_es": "ES",
                        "it_it": "IT",
                    }.get(lang_raw, lang_raw.upper())
                    voices.append(
                        {
                            "slug": v["slug"],
                            "name": v["name"],
                            "lang": lang_raw,
                            "lang_label": lang_label,
                            "gender": v.get("gender", ""),
                            "tags": v.get("tags", []),
                        }
                    )
            # Stopp wenn keine neuen Stimmen auf dieser Seite (Duplikate)
            if len(seen) == prev_seen_count:
                break
            prev_seen_count = len(seen)
            total_pages = data.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        return jsonify({"voices": voices})
    except Exception as e:
        return jsonify({"voices": [], "error": str(e)})


# ─── Providers ────────────────────────────────────────────────────────────────


@app.route("/api/providers", methods=["GET"])
def get_providers():
    providers = load_providers()
    # mask keys partially for display
    result = {}
    for k, v in providers.items():
        entry = dict(v)
        if "api_key" in entry and entry["api_key"]:
            key = entry["api_key"]
            entry["api_key_masked"] = (
                key[:6] + "•" * max(0, len(key) - 10) + key[-4:]
                if len(key) > 10
                else "••••"
            )
        result[k] = entry
    return jsonify(result)


@app.route("/api/providers", methods=["POST"])
def update_providers():
    data = request.json
    providers = load_providers()
    for key, val in data.items():
        if key in providers:
            providers[key].update(val)
        else:
            providers[key] = val
    save_providers(providers)
    return jsonify({"ok": True})


@app.route("/api/providers/status", methods=["GET"])
def providers_status():
    providers = load_providers()
    status = {}

    # Ollama
    try:
        url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        r = requests.get(f"{url}/api/tags", timeout=3)
        count = len(r.json().get("models", []))
        status["ollama"] = {"ok": True, "info": f"{count} Modelle"}
    except Exception:
        status["ollama"] = {"ok": False, "info": "Nicht erreichbar"}

    # Mistral
    mk = providers.get("mistral", {}).get("api_key", "")
    if mk:
        try:
            r = requests.get(
                f"{MISTRAL_VOICES_URL}?page_size=1",
                headers={"Authorization": f"Bearer {mk}"},
                timeout=5,
            )
            status["mistral"] = {
                "ok": r.ok,
                "info": "API Key gültig" if r.ok else f"Fehler {r.status_code}",
            }
        except Exception:
            status["mistral"] = {"ok": False, "info": "Nicht erreichbar"}
    else:
        status["mistral"] = {"ok": False, "info": "Kein API Key"}

    # OpenRouter
    ok = providers.get("openrouter", {}).get("api_key", "")
    if ok:
        try:
            r = requests.get(
                f"{OPENROUTER_BASE_URL}/models",
                headers={"Authorization": f"Bearer {ok}"},
                timeout=5,
            )
            count = len(r.json().get("data", []))
            status["openrouter"] = {
                "ok": r.ok,
                "info": f"{count} Modelle verfügbar"
                if r.ok
                else f"Fehler {r.status_code}",
            }
        except Exception:
            status["openrouter"] = {"ok": False, "info": "Nicht erreichbar"}
    else:
        status["openrouter"] = {"ok": False, "info": "Kein API Key"}

    return jsonify(status)


# ─── Models (aggregated) ──────────────────────────────────────────────────────


@app.route("/api/models", methods=["GET"])
def get_all_models():
    providers = load_providers()
    result = {"ollama": [], "openrouter": []}

    # Ollama
    try:
        url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        r = requests.get(f"{url}/api/tags", timeout=5)
        r.raise_for_status()
        result["ollama"] = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        result["ollama"] = []

    # OpenRouter
    or_key = providers.get("openrouter", {}).get("api_key", "")
    if or_key:
        try:
            r = requests.get(
                f"{OPENROUTER_BASE_URL}/models",
                headers={"Authorization": f"Bearer {or_key}"},
                timeout=10,
            )
            r.raise_for_status()
            models = r.json().get("data", [])
            parsed = [
                {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                    "free": (
                        str(m.get("pricing", {}).get("prompt", "1")) == "0"
                        and str(m.get("pricing", {}).get("completion", "1")) == "0"
                    )
                    or m["id"].endswith(":free"),
                }
                for m in models
            ]
            result["openrouter"] = sorted(
                parsed, key=lambda x: (0 if x["free"] else 1, x.get("name", "").lower())
            )
        except Exception:
            result["openrouter"] = []

    return jsonify(result)


# ─── Backup & Restore ──────────────────────────────────────────────────────────

BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)


@app.route("/api/backup", methods=["POST"])
def create_backup():
    """Erstellt einen vollständigen Backup des aktuellen States.

    Hinweis: history.json und tasks.json werden wegen Größe nicht eingeschlossen.
    """
    import shutil

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"agentclaw_backup_{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    os.makedirs(backup_path, exist_ok=True)

    # Backup core state files (ohne history/tasks wegen Größe)
    files_to_backup = [
        ("agents.json", AGENTS_FILE),
        ("providers.json", PROVIDERS_FILE),
        ("watchdogs.json", WATCHDOGS_FILE),
    ]

    for name, src_path in files_to_backup:
        dst = os.path.join(backup_path, name)
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst)

    # Create manifest with metadata
    manifest = {
        "version": "1.0",
        "created": datetime.now().isoformat(),
        "includes_history": False,
        "includes_tasks": False,
        "note": "Agenten, Provider und Watchdogs. History/Tasks ausgeschlossen wegen Größe.",
    }
    with open(os.path.join(backup_path, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Create ZIP archive
    zip_path = os.path.join(BACKUP_DIR, f"{backup_name}.zip")
    shutil.make_archive(backup_path, "zip", backup_path)

    # Cleanup temp folder
    shutil.rmtree(backup_path)

    return jsonify({"ok": True, "backup_file": f"{backup_name}.zip", "path": zip_path})


@app.route("/api/backup/list", methods=["GET"])
def list_backups():
    """Liste alle verfügbaren Backups auf."""
    backups = []
    for f in os.listdir(BACKUP_DIR):
        if f.endswith(".zip"):
            fpath = os.path.join(BACKUP_DIR, f)
            backups.append(
                {
                    "name": f,
                    "size": os.path.getsize(fpath),
                    "modified": datetime.fromtimestamp(
                        os.path.getmtime(fpath)
                    ).isoformat(),
                }
            )
    return jsonify(sorted(backups, key=lambda x: x["modified"], reverse=True))


@app.route("/api/backup/restore", methods=["POST"])
def restore_backup():
    """Stellt einen Backup aus einer ZIP-Datei wieder her."""
    data = request.json
    backup_name = data.get("backup_name")

    if not backup_name:
        return jsonify({"error": "backup_name required"}), 400

    zip_path = os.path.join(BACKUP_DIR, backup_name)
    if not os.path.exists(zip_path):
        return jsonify({"error": "Backup nicht gefunden"}), 404

    import zipfile
    import shutil

    # Extract to temp
    extract_path = os.path.join(BACKUP_DIR, "restore_temp")
    if os.path.exists(extract_path):
        shutil.rmtree(extract_path)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_path)

    # Restore files
    files_to_restore = [
        "agents.json",
        "history.json",
        "providers.json",
        "tasks.json",
        "watchdogs.json",
    ]

    for fname in files_to_restore:
        src = os.path.join(extract_path, fname)
        if os.path.exists(src):
            if fname == "agents.json":
                shutil.copy2(src, AGENTS_FILE)
            elif fname == "history.json":
                shutil.copy2(src, HISTORY_FILE)
            elif fname == "providers.json":
                shutil.copy2(src, PROVIDERS_FILE)
            elif fname == "tasks.json":
                shutil.copy2(src, TASKS_FILE)
            elif fname == "watchdogs.json":
                shutil.copy2(src, WATCHDOGS_FILE)

    # Cleanup
    shutil.rmtree(extract_path)

    return jsonify({"ok": True, "message": "Backup restored. Bitte Server neustarten."})


@app.route("/api/backup/download/<name>", methods=["GET"])
def download_backup(name):
    """Download eines Backups."""
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        return jsonify({"error": "Nicht gefunden"}), 404
    return send_file(path, as_attachment=True)


# ─── Ollama models (legacy) ────────────────────────────────────────────────────


@app.route("/api/ollama/models", methods=["GET"])
def ollama_models():
    providers = load_providers()
    url = providers.get("ollama", {}).get("url", "http://localhost:11434")
    try:
        response = requests.get(f"{url}/api/tags", timeout=5)
        response.raise_for_status()
        models = [m["name"] for m in response.json().get("models", [])]
        return jsonify({"models": models})
    except requests.exceptions.ConnectionError:
        return jsonify({"models": [], "error": "Ollama läuft nicht"}), 200
    except Exception as e:
        return jsonify({"models": [], "error": str(e)}), 200


# ─── Watchdog Pipeline ────────────────────────────────────────────────────────


def watchdog_fetch_hash(url):
    """Billiger Hash-Check: Text abrufen, normalisieren, MD5."""
    text = fetch_url_text(url, max_chars=60000)
    # Dynamische Teile rauswerfen (Zeitstempel, Session-IDs, Zufallszahlen)
    text = re.sub(r"\b\d{10,13}\b", "", text)  # Unix timestamps
    text = re.sub(r"[a-f0-9]{32,}", "", text)  # Hashes/Token
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.md5(text.encode()).hexdigest(), text


def call_agent_text(agent, system_suffix, user_prompt):
    """Schlanker LLM-Call ohne History, nur Text — für Watchdog."""
    providers = load_providers()
    provider = agent.get("provider", "ollama")
    now = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
    agent_directory = _build_agent_directory(agent.get("id"))
    system_content = f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}\n\n{agent_directory}\n\n{system_suffix}"
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_prompt},
    ]
    if provider == "openrouter":
        or_key = providers.get("openrouter", {}).get("api_key", "")
        if not or_key:
            raise ValueError("OpenRouter Key fehlt")
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {or_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5050",
                "X-Title": "AgentClaw",
            },
            json={
                "model": agent["model"],
                "messages": messages,
                "stream": False,
                **(
                    {"max_tokens": agent["max_tokens"]}
                    if agent.get("max_tokens")
                    else {}
                ),
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    else:
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        resp = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model": agent["model"],
                "messages": messages,
                "stream": False,
                **(
                    {"options": {"num_predict": agent["max_tokens"]}}
                    if agent.get("max_tokens")
                    else {}
                ),
            },
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()
        return (
            result.get("message", {}).get("content", result.get("response", "")).strip()
        )


def send_watchdog_alert(wd, reply):
    """macOS Notification + Chat-History Eintrag."""
    name = wd["name"]
    short = reply[:120].replace('"', "'").replace("\n", " ")
    # macOS Notification
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{short}" with title "🔔 AgentClaw: {name}" sound name "Ping"',
            ],
            timeout=5,
            capture_output=True,
        )
    except Exception as e:
        print(f"[Alert] osascript Fehler: {e}", flush=True)
    # Chat-History Eintrag
    agent_id = wd.get("agent_id")
    if agent_id:
        history = load_history()
        if agent_id not in history:
            history[agent_id] = []
        history[agent_id].append(
            {
                "role": "assistant",
                "content": f"🔔 **Watchdog-Treffer: {name}**\n\n{reply}",
                "ts": datetime.now().isoformat(),
                "watchdog_alert": True,
            }
        )
        save_history(history)
    print(f"[Alert] 🔔 '{name}': {short}", flush=True)


def run_watchdog(wd):
    """Vollständige Pipeline: Hash-Check → (bei Änderung) LLM → Alert."""
    wd_id = wd["id"]
    url = wd.get("url", "")
    print(f"[Watchdog] '{wd['name']}' checking {url}", flush=True)

    # SSRF protection
    if not _is_safe_url(url):
        update_watchdog_field(
            wd_id,
            last_result="⚠️ Blocked: URL targets a private or internal network address",
            last_run=datetime.now().isoformat(),
        )
        return

    # ── 1. Billiger Hash-Check ──────────────────────────────────────────────
    try:
        new_hash, page_text = watchdog_fetch_hash(url)
    except Exception as e:
        update_watchdog_field(
            wd_id,
            last_result=f"⚠️ Fetch-Fehler: {e}",
            last_run=datetime.now().isoformat(),
        )
        return

    old_hash = wd.get("last_hash")
    check_count = wd.get("check_count", 0) + 1

    if old_hash and new_hash == old_hash:
        print(f"[Watchdog] '{wd['name']}' — Hash gleich, kein LLM-Call", flush=True)
        update_watchdog_field(
            wd_id,
            last_result="⚡ Keine Änderung",
            last_run=datetime.now().isoformat(),
            last_hash=new_hash,
            check_count=check_count,
        )
        return

    # ── 2. Hash geändert → LLM ─────────────────────────────────────────────
    agent_id = wd.get("agent_id")
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        update_watchdog_field(
            wd_id,
            last_result="⚠️ Agent nicht gefunden",
            last_run=datetime.now().isoformat(),
            last_hash=new_hash,
        )
        return

    prompt = wd.get(
        "prompt",
        "Has anything relevant changed on this page? Answer with YES or NO, followed by a one-sentence summary of what changed.",
    )
    system_suffix = f"[Watchdog — page content from {url}]\n\n{page_text[:6000]}"

    try:
        reply = call_agent_text(agent, system_suffix, prompt)
    except Exception as e:
        update_watchdog_field(
            wd_id,
            last_result=f"⚠️ LLM-Fehler: {e}",
            last_run=datetime.now().isoformat(),
            last_hash=new_hash,
            check_count=check_count,
        )
        return

    # ── 3. Alert wenn Keyword gefunden ─────────────────────────────────────
    alert_keyword = wd.get("alert_keyword", "").strip().lower()
    hit = bool(alert_keyword and alert_keyword in reply.lower())
    if hit:
        send_watchdog_alert(wd, reply)

    hit_count = wd.get("hit_count", 0) + (1 if hit else 0)
    # History (max 50 Einträge)
    history = wd.get("history", [])
    history.append(
        {
            "ts": datetime.now().isoformat(),
            "result": reply[:300],
            "hit": hit,
            "hash_changed": True,
        }
    )
    if len(history) > 50:
        history = history[-50:]

    update_watchdog_field(
        wd_id,
        last_result=reply[:300],
        last_hash=new_hash,
        last_run=datetime.now().isoformat(),
        check_count=check_count,
        hit_count=hit_count,
        history=history,
    )


def tick_watchdogs():
    """Prüft jede Minute welche Watchdogs fällig sind."""
    watchdogs = load_watchdogs()
    now = datetime.now()
    for wd in watchdogs:
        if not wd.get("active"):
            continue
        next_run_str = wd.get("next_run")
        if not next_run_str:
            next_run = now  # Noch nie gelaufen → sofort
        else:
            try:
                next_run = datetime.fromisoformat(next_run_str)
            except Exception:
                next_run = now
        if now >= next_run:
            # Nächsten Run planen (mit ±5 Min Jitter)
            jitter = random.randint(-300, 300)
            interval_sec = wd.get("interval_min", 30) * 60 + jitter
            new_next = (now + timedelta(seconds=interval_sec)).isoformat()
            update_watchdog_field(wd["id"], next_run=new_next)
            wd["next_run"] = new_next  # lokale Kopie aktualisieren
            spawn_background(run_watchdog, dict(wd))


_MENTION_RX = re.compile(r"@([\w\-äöüÄÖÜß]+)", re.UNICODE)


def _dispatch_mentions_from_prompt(sender_agent: dict, prompt: str, task_message: str):
    """Dispatch tasks to @AgentNames found in the heartbeat PROMPT, using task_message as content."""
    all_agents = load_agents()
    name_map = {a["name"].lower(): a for a in all_agents}
    for m in _MENTION_RX.finditer(prompt):
        target_name = m.group(1).rstrip(",.;:!?").lower()
        target = name_map.get(target_name)
        if not target or target["id"] == sender_agent["id"]:
            continue
        print(
            f"[Heartbeat] dispatch @{target['name']} ← '{task_message[:60]}'",
            flush=True,
        )
        now = datetime.now()
        task = {
            "id": str(uuid.uuid4()),
            "sender_agent_id": sender_agent["id"],
            "sender_agent_name": sender_agent["name"],
            "recipient_agent_id": target["id"],
            "recipient_agent_name": target["name"],
            "message": task_message,
            "status": "submitted",
            "skill_used": None,
            "result_text": None,
            "result_image": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
            "timeout_at": (now + timedelta(seconds=180)).isoformat(),
        }
        with _tasks_lock:
            _TASKS[task["id"]] = task
        _save_tasks()
        spawn_background(process_task, task["id"])
        break  # one dispatch per heartbeat


def _dispatch_mentions_from_reply(sender_agent: dict, reply: str):
    """Scan reply for @AgentName mentions and create tasks for each (max 1)."""
    all_agents = load_agents()
    name_map = {a["name"].lower(): a for a in all_agents}
    for m in _MENTION_RX.finditer(reply):
        target_name = m.group(1).rstrip(",.;:!?").lower()
        target = name_map.get(target_name)
        if not target or target["id"] == sender_agent["id"]:
            continue
        # Extract message after the @mention
        after = reply[m.end() :].lstrip(" ,–—:\t").split("\n")[0].strip()
        task_msg = after if after else reply.strip()
        print(f"[Heartbeat] dispatch @{target['name']} ← '{task_msg[:60]}'", flush=True)
        # Create task inline (reuse the tasks API logic)
        now = datetime.now()
        task = {
            "id": str(uuid.uuid4()),
            "sender_agent_id": sender_agent["id"],
            "sender_agent_name": sender_agent["name"],
            "recipient_agent_id": target["id"],
            "recipient_agent_name": target["name"],
            "message": task_msg,
            "status": "submitted",
            "skill_used": None,
            "result_text": None,
            "result_image": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
            "timeout_at": (now + timedelta(seconds=180)).isoformat(),
        }
        with _tasks_lock:
            _TASKS[task["id"]] = task
        _save_tasks()
        spawn_background(process_task, task["id"])
        break  # one dispatch per heartbeat


def run_heartbeat(agent_or_id):
    """Führt den Heartbeat-Task eines Agenten aus.

    Args:
        agent_or_id: Entweder eine agent_id (str) oder ein agent dict (für Rückwärtskompatibilität).
                     Bei str wird der Agent frisch aus der DB geladen.
    """
    if isinstance(agent_or_id, str):
        agent_id = agent_or_id
        agents = load_agents()
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if not agent:
            print(f"[Heartbeat] Agent {agent_id} nicht gefunden", flush=True)
            return
    else:
        agent = agent_or_id
        agent_id = agent["id"]
        agents = load_agents()
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if not agent:
            print(f"[Heartbeat] Agent {agent_id} nicht gefunden", flush=True)
            return

    hb = agent.get("heartbeat", {})
    prompt = (
        hb.get("prompt", "").strip()
        or "What are your current thoughts? Give a brief status update."
    )
    skills = set(agent.get("skills", []))
    print(f"[Heartbeat] 💓 Agent '{agent['name']}' — {prompt[:60]}", flush=True)
    activity_start(agent_id, "heartbeat", prompt[:60])
    try:
        history = load_history()
        if agent_id not in history:
            history[agent_id] = []
        ts = datetime.now().isoformat()
        result_image = None

        if "image_gen" in skills:
            # Use the agent's heartbeat prompt directly as image prompt.
            # Append random mood/style modifiers for visual variety.
            _moods = [
                "golden hour",
                "blue hour",
                "dramatic stormy sky",
                "misty morning fog",
                "blazing sunset",
                "overcast moody",
                "neon night light",
                "harsh midday sun",
            ]
            _styles = [
                "35mm film grain",
                "cinematic wide angle",
                "hyper-realistic",
                "long exposure",
                "shallow depth of field",
                "high contrast black and white",
            ]
            rnd = random.Random()
            img_prompt = (
                f"{prompt.rstrip('.')} — "
                f"{rnd.choice(_moods)}, {rnd.choice(_styles)}, "
                f"photorealistic, 4k, no text, no words, no typography"
            )
            print(f"[Heartbeat] image prompt: {img_prompt[:80]}", flush=True)
            # Generate image via ComfyUI (no LLM involved)
            result_image = _run_comfyui_sync(img_prompt)
            thumb = _make_thumbnail(result_image)
            # Keine Text-Antwort bei Bildgenerierung - nur Bild speichern
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": "💓 **Heartbeat** — Bild generiert",
                    "task_image": thumb,
                    "ts": ts,
                    "heartbeat": True,
                }
            )
            short = f"Bild: {img_prompt[:60]}..."
        else:
            # Strip @mentions from the prompt before sending to LLM so it
            # focuses on generating content, not on routing.
            prompt_for_llm = _MENTION_RX.sub("", prompt).strip()
            system_suffix = "[Heartbeat — autonomous action, no user present. Respond with content, not questions.]"
            reply = call_agent_text(agent, system_suffix, prompt_for_llm)
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": f"💓 **Heartbeat**\n\n{reply}",
                    "ts": ts,
                    "heartbeat": True,
                }
            )
            short = reply[:120].replace('"', "'").replace("\n", " ")

            # NUR @mentions aus der REPLY dispatchen, nicht aus dem Prompt
            # Das verhindert, dass der gleiche Task wiederholt wird
            clean_reply = re.sub(r"^\s*\(.*?\)\s*", "", reply, flags=re.DOTALL).strip()
            clean_reply = re.sub(
                r"^\s*(Guten\s+\w+|Hallo|Hi|Hey|Good\s+\w+|Hello|Greetings)[^.!?\n]*[.!?\n]",
                "",
                clean_reply,
                flags=re.IGNORECASE,
            ).strip()
            # Hier nur die reply dispatchen, nicht den prompt
            if _MENTION_RX.search(clean_reply or reply):
                _dispatch_mentions_from_reply(agent, clean_reply or reply)

        save_history(history)
        # Atomic patch — no race with concurrent save_agents() calls
        patch_agent_heartbeat(agent_id, last_run=ts, last_result=short[:300])
        # Emit heartbeat result via WebSocket
        emit_heartbeat_result(agent_id, reply or short)
        # macOS Notification
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{short}" with title "💓 {agent["name"]}" sound name "Ping"',
                ],
                timeout=5,
                capture_output=True,
            )
        except Exception:
            pass
        print(f"[Heartbeat] done '{agent['name']}'", flush=True)
    except Exception as e:
        import traceback

        print(
            f"[Heartbeat] Fehler '{agent['name']}': {traceback.format_exc()}",
            flush=True,
        )
    finally:
        activity_end(agent_id)


def tick_heartbeats():
    """Prüft welche Agenten-Heartbeats fällig sind."""
    agents = load_agents()
    now = datetime.now()
    for agent in agents:
        hb = agent.get("heartbeat", {})
        if not hb.get("active"):
            continue
        interval_min = int(hb.get("interval_min", 30))
        next_run_str = hb.get("next_run")
        if not next_run_str:
            overdue = True
        else:
            try:
                overdue = now >= datetime.fromisoformat(next_run_str)
            except Exception:
                overdue = True

        if overdue:
            new_next = (now + timedelta(minutes=interval_min)).isoformat()
            # Atomic patch — holds _agents_lock for the full read-modify-write
            patch_agent_heartbeat(agent["id"], next_run=new_next)
            spawn_background(run_heartbeat, agent["id"])


# Telegram polling state - start from latest to avoid duplicates
_telegram_last_update_id = None


def tick_telegram():
    """Poll Telegram for new messages and forward to agents with telegram_incoming skill."""
    global _telegram_last_update_id

    providers = load_providers()
    tg = providers.get("telegram", {})
    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")

    if not token or not chat_id:
        return

    try:
        # Reset polling to avoid conflict - get all pending updates first
        params = {"timeout": 1}

        # First reset: get offset=0 to clear any stale polling
        try:
            requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": 0, "timeout": 1},
                timeout=2,
            )
        except:
            pass

        # Then get updates with offset
        if _telegram_last_update_id is not None:
            params["offset"] = _telegram_last_update_id + 1

        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params=params,
            timeout=2,
        )
        if not r.ok:
            print(f"[Telegram] API error: {r.text[:100]}", flush=True)
            return
        updates = r.json().get("result", [])

        if not updates:
            return

        # Find agents with telegram_incoming skill
        agents = load_agents()
        target_agents = [
            a for a in agents if "telegram_incoming" in a.get("skills", [])
        ]

        if not target_agents:
            return

        for update in updates:
            upd_id = update.get("update_id", 0)
            if _telegram_last_update_id is None:
                _telegram_last_update_id = upd_id
            else:
                _telegram_last_update_id = max(_telegram_last_update_id, upd_id)
            msg = update.get("message", {})
            if not msg:
                continue

            # Extract content
            text = msg.get("text", "")
            photo = msg.get("photo", [])

            # Get sender info
            from_user = msg.get("from", {})
            sender_name = from_user.get("first_name", "Unknown")

            if text or photo:
                # Build message to forward
                if text:
                    content = f"[Telegram von {sender_name}]: {text}"
                else:
                    content = f"[Telegram Bild von {sender_name}]"

                # Forward to all agents with telegram_incoming skill
                for agent in target_agents:
                    print(
                        f"[Telegram] Forwarding to {agent['name']}: {content[:50]}...",
                        flush=True,
                    )

                    # Add to history
                    history = load_history()
                    agent_id = agent["id"]
                    if agent_id not in history:
                        history[agent_id] = []

                    history[agent_id].append(
                        {
                            "role": "user",
                            "content": content,
                            "ts": datetime.now().isoformat(),
                            "from_telegram": True,
                        }
                    )
                    save_history(history)

    except Exception as e:
        print(f"[Telegram] Polling error: {e}", flush=True)


def scheduler_loop():
    print("[Scheduler] Watchdog-Scheduler gestartet", flush=True)
    while True:
        try:
            tick_watchdogs()
            tick_heartbeats()
            # tick_telegram()  # DISABLED - uncomment to enable Telegram polling
            activity_cleanup()
        except Exception as e:
            print(f"[Scheduler] Fehler: {e}", flush=True)
        time.sleep(60)  # tick every minute


# Scheduler als Daemon-Thread starten (nicht blockierend)
import threading

threading.Thread(target=scheduler_loop, daemon=True).start()


# ─── Skills ───────────────────────────────────────────────────────────────────


@app.route("/api/skills", methods=["GET"])
def get_skills():
    providers = load_providers()
    result = []
    for skill in SKILLS:
        s = dict(skill)
        req = s.get("requires")
        if req is None:
            s["available"] = True
        elif req == "playwright":
            try:
                import playwright  # noqa

                s["available"] = True
            except ImportError:
                s["available"] = False
                s["install_hint"] = (
                    "venv/bin/pip install playwright && venv/bin/playwright install chromium"
                )
        result.append(s)
    return jsonify(result)


# ─── Watchdog API ─────────────────────────────────────────────────────────────


@app.route("/api/watchdogs", methods=["GET"])
def get_watchdogs():
    return jsonify(load_watchdogs())


@app.route("/api/watchdogs", methods=["POST"])
def create_watchdog():
    data = request.json
    now = datetime.now().isoformat()
    wd = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "New Watchdog"),
        "url": data.get("url", ""),
        "interval_min": int(data.get("interval_min", 30)),
        "agent_id": data.get("agent_id", ""),
        "prompt": data.get(
            "prompt",
            "Has anything relevant changed on this page? Answer with YES or NO, followed by a one-sentence summary of what changed.",
        ),
        "alert_keyword": data.get("alert_keyword", "YES"),
        "active": data.get("active", True),
        "created_at": now,
        "last_run": None,
        "last_result": None,
        "last_hash": None,
        "next_run": None,
        "check_count": 0,
        "hit_count": 0,
        "history": [],
    }
    watchdogs = load_watchdogs()
    watchdogs.append(wd)
    save_watchdogs(watchdogs)
    return jsonify(wd), 201


@app.route("/api/watchdogs/<wd_id>", methods=["PUT"])
def update_watchdog(wd_id):
    data = request.json
    watchdogs = load_watchdogs()
    for i, wd in enumerate(watchdogs):
        if wd["id"] == wd_id:
            watchdogs[i].update(
                {
                    "name": data.get("name", wd["name"]),
                    "url": data.get("url", wd["url"]),
                    "interval_min": int(data.get("interval_min", wd["interval_min"])),
                    "agent_id": data.get("agent_id", wd["agent_id"]),
                    "prompt": data.get("prompt", wd["prompt"]),
                    "alert_keyword": data.get("alert_keyword", wd["alert_keyword"]),
                    "active": data.get("active", wd["active"]),
                }
            )
            # URL geändert → Hash zurücksetzen
            if data.get("url") and data["url"] != wd["url"]:
                watchdogs[i]["last_hash"] = None
                watchdogs[i]["next_run"] = None
            save_watchdogs(watchdogs)
            return jsonify(watchdogs[i])
    return jsonify({"error": "Nicht gefunden"}), 404


@app.route("/api/watchdogs/<wd_id>", methods=["DELETE"])
def delete_watchdog(wd_id):
    watchdogs = [w for w in load_watchdogs() if w["id"] != wd_id]
    save_watchdogs(watchdogs)
    return jsonify({"ok": True})


@app.route("/api/watchdogs/<wd_id>/run", methods=["POST"])
def trigger_watchdog(wd_id):
    watchdogs = load_watchdogs()
    wd = next((w for w in watchdogs if w["id"] == wd_id), None)
    if not wd:
        return jsonify({"error": "Nicht gefunden"}), 404
    spawn_background(run_watchdog, dict(wd))
    return jsonify({"ok": True, "message": "Watchdog wird ausgeführt…"})


@app.route("/api/watchdogs/<wd_id>/toggle", methods=["POST"])
def toggle_watchdog(wd_id):
    watchdogs = load_watchdogs()
    for wd in watchdogs:
        if wd["id"] == wd_id:
            wd["active"] = not wd.get("active", True)
            if wd["active"]:
                wd["next_run"] = None  # Sofort beim nächsten Tick prüfen
            save_watchdogs(watchdogs)
            return jsonify({"active": wd["active"]})
    return jsonify({"error": "Nicht gefunden"}), 404


@app.route("/api/agents/<agent_id>/heartbeat", methods=["PUT"])
def set_heartbeat(agent_id):
    data = request.json
    print(
        f"[Heartbeat] PUT received: agent={agent_id}, prompt={data.get('prompt', '')[:50]}...",
        flush=True,
    )
    agents = load_agents()
    for a in agents:
        if a["id"] == agent_id:
            hb = a.setdefault("heartbeat", {})
            new_active = bool(data.get("active", hb.get("active", False)))
            new_prompt = data.get("prompt", hb.get("prompt", "")).strip()

            if new_active and not new_prompt:
                new_prompt = (
                    "What are your current thoughts? Give a brief status update."
                )
                print(
                    "[Heartbeat] Warning: Kein Prompt angegeben, Default wird verwendet",
                    flush=True,
                )

            hb["active"] = new_active
            hb["prompt"] = new_prompt
            hb["interval_min"] = int(
                data.get("interval_min", hb.get("interval_min", 30))
            )
            if hb["active"]:
                hb["next_run"] = None  # sofort beim nächsten Tick
            save_agents(agents)
            emit_event("agent_updated", {"id": agent_id})
            print(
                f"[Heartbeat] Saved: prompt={hb.get('prompt', '')[:50]}...", flush=True
            )
            return jsonify({"ok": True, "agent": a})
    return jsonify({"ok": False, "error": "Agent nicht gefunden"}), 404


@app.route("/api/agents/<agent_id>/heartbeat/run", methods=["POST"])
def run_heartbeat_now(agent_id):
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404
    spawn_background(run_heartbeat, agent_id)
    return jsonify({"ok": True})


@app.route("/api/agents/<agent_id>/dream", methods=["PUT"])
def set_dream(agent_id):
    data = request.json
    print(
        f"[Dream] PUT received: agent={agent_id}, active={data.get('active')}",
        flush=True,
    )
    agents = load_agents()
    for a in agents:
        if a["id"] == agent_id:
            dream = a.setdefault("dream", {})
            dream["active"] = bool(data.get("active", dream.get("active", False)))
            dream["retention_days"] = int(
                data.get("retention_days", dream.get("retention_days", 30))
            )
            save_agents(agents)
            emit_event("agent_updated", {"id": agent_id})
            print(
                f"[Dream] Saved: active={dream['active']}, retention={dream['retention_days']} days",
                flush=True,
            )
            return jsonify({"ok": True, "agent": a})
    return jsonify({"ok": False, "error": "Agent nicht gefunden"}), 404


@app.route("/api/agents/<agent_id>/dream/run", methods=["POST"])
def run_dream_now(agent_id):
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404

    spawn_background(run_dream_for_agent, agent_id)
    return jsonify({"ok": True})


# ─── Agent Settings Endpoints ───────────────────────────────────────────────────


@app.route("/api/agents/<agent_id>/settings", methods=["PUT"])
def update_agent_settings(agent_id):
    """Aktualisiert die Grundeinstellungen eines Agenten."""
    data = request.json
    agents = load_agents()

    for i, a in enumerate(agents):
        if a["id"] == agent_id:
            # Update basic fields
            if "name" in data:
                agents[i]["name"] = data["name"]
            if "role" in data:
                agents[i]["role"] = data["role"]
            if "soul" in data:
                agents[i]["soul"] = data["soul"]
            if "model" in data:
                agents[i]["model"] = data["model"]
            if "provider" in data:
                agents[i]["provider"] = data["provider"]
            if "max_tokens" in data:
                agents[i]["max_tokens"] = data["max_tokens"]
            if "color" in data:
                agents[i]["color"] = data["color"]
            if "avatar" in data:
                agents[i]["avatar"] = data["avatar"]  # base64 data URL or ""

            try:
                save_agents(agents)
                print(f"[Agent] Settings saved for {agent_id}", flush=True)
                emit_event("agent_updated", {"id": agent_id})
                return jsonify({"ok": True, "agent": agents[i]})
            except Exception as e:
                print(f"[Agent] Error saving settings: {e}", flush=True)
                return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": False, "error": "Agent nicht gefunden"}), 404


@app.route("/api/agents/<agent_id>/skills", methods=["PUT"])
def update_agent_skills(agent_id):
    """Aktualisiert die Skills eines Agenten."""
    data = request.json
    skills = data.get("skills", [])
    agents = load_agents()

    for i, a in enumerate(agents):
        if a["id"] == agent_id:
            agents[i]["skills"] = skills
            try:
                save_agents(agents)
                print(f"[Agent] Skills saved for {agent_id}: {skills}", flush=True)
                emit_event("agent_updated", {"id": agent_id})
                return jsonify({"ok": True, "skills": skills})
            except Exception as e:
                print(f"[Agent] Error saving skills: {e}", flush=True)
                return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": False, "error": "Agent nicht gefunden"}), 404


@app.route("/api/agents/<agent_id>/voice", methods=["PUT"])
def update_agent_voice(agent_id):
    """Aktualisiert die Stimme eines Agenten."""
    data = request.json
    voice = data.get("voice", "")
    agents = load_agents()

    for i, a in enumerate(agents):
        if a["id"] == agent_id:
            agents[i]["voice"] = voice
            try:
                save_agents(agents)
                print(f"[Agent] Voice saved for {agent_id}: {voice}", flush=True)
                emit_event("agent_updated", {"id": agent_id})
                return jsonify({"ok": True, "voice": voice})
            except Exception as e:
                print(f"[Agent] Error saving voice: {e}", flush=True)
                return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": False, "error": "Agent nicht gefunden"}), 404


# ─── Screenshot ───────────────────────────────────────────────────────────────


@app.route("/api/screenshot", methods=["POST"])
def take_screenshot():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Keine URL angegeben"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not _is_safe_url(url):
        return jsonify(
            {"error": f"Blocked: '{url}' targets a private or internal network address"}
        ), 403

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return jsonify(
            {
                "error": "Playwright nicht installiert. Führe aus: venv/bin/pip install playwright && venv/bin/playwright install chromium"
            }
        ), 501

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            img_bytes = page.screenshot(type="jpeg", quality=80, full_page=False)
            context.close()
            browser.close()
            browser = None
        b64 = base64.b64encode(img_bytes).decode()
        print(f"[Screenshot] {url} — {len(img_bytes) // 1024}KB", flush=True)
        return jsonify({"image": f"data:image/jpeg;base64,{b64}", "url": url})
    except Exception as e:
        print(f"[Screenshot] Error: {e}", flush=True)
        if browser:
            try:
                browser.close()
            except:
                pass
        return jsonify({"error": f"Screenshot fehlgeschlagen: {e}"}), 500


@app.route("/api/image/edit", methods=["POST"])
def edit_image():
    """Edit an image with a prompt. Expects: { image_data: base64, prompt: str }"""
    data = request.json
    image_data = data.get("image_data", "")
    prompt = data.get("prompt", "").strip()

    if not image_data:
        return jsonify({"error": "Kein Bild angegeben"}), 400
    if not prompt:
        return jsonify({"error": "Kein Prompt angegeben"}), 400

    try:
        result = _run_comfyui_edit(image_data, prompt, use_lightning=True)
        return jsonify({"image": result})
    except Exception as e:
        print(f"[Image Edit] Error: {e}", flush=True)
        return jsonify({"error": f"Bearbeitung fehlgeschlagen: {e}"}), 500


# ─── Tagesschau RSS ───────────────────────────────────────────────────────────

TAGESSCHAU_FEEDS = {
    "top": "https://www.tagesschau.de/index~rss2.xml",
    "inland": "https://www.tagesschau.de/inland/index~rss2.xml",
    "ausland": "https://www.tagesschau.de/ausland/index~rss2.xml",
    "wirtschaft": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    "sport": "https://www.tagesschau.de/sport/index~rss2.xml",
    "faktenfinder": "https://www.tagesschau.de/faktenfinder/index~rss2.xml",
    "investigativ": "https://www.tagesschau.de/investigativ/index~rss2.xml",
}


def fetch_tagesschau(category="top", limit=10):
    import xml.etree.ElementTree as ET

    url = TAGESSCHAU_FEEDS.get(category, TAGESSCHAU_FEEDS["top"])
    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        # strip HTML from description
        desc = re.sub(r"<[^>]+>", "", desc).strip()
        items.append(
            {"title": title, "description": desc, "link": link, "pubDate": pub}
        )
    return items


@app.route("/api/tagesschau", methods=["GET"])
def tagesschau_feed():
    category = request.args.get("category", "top")
    limit = min(int(request.args.get("limit", 10)), 20)
    if category not in TAGESSCHAU_FEEDS:
        return jsonify(
            {
                "error": f"Unbekannte Kategorie: {category}",
                "categories": list(TAGESSCHAU_FEEDS.keys()),
            }
        ), 400
    try:
        items = fetch_tagesschau(category, limit)
        return jsonify({"category": category, "items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Hacker News ───────────────────────────────────────────────────────────────


@app.route("/api/hackernews", methods=["GET"])
def hackernews_feed():
    limit = min(int(request.args.get("limit", 15)), 30)
    try:
        # Get top story IDs
        r = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
        )
        story_ids = r.json()[:limit]

        items = []
        for sid in story_ids:
            sr = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5
            )
            story = sr.json()
            if story:
                items.append(
                    {
                        "title": story.get("title", ""),
                        "url": story.get(
                            "url", f"https://news.ycombinator.com/item?id={sid}"
                        ),
                        "score": story.get("score", 0),
                        "by": story.get("by", ""),
                        "time": story.get("time", 0),
                        "descendants": story.get("descendants", 0),
                        "id": sid,
                    }
                )
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Agent Tasks API ──────────────────────────────────────────────────────────


@app.route("/api/tasks", methods=["POST"])
def create_task():
    data = request.json
    sender_id = data.get("sender_agent_id", "")
    sender_name = data.get("sender_agent_name", "?")
    target_name = data.get("recipient_agent_name", "")
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "Keine Nachricht"}), 400

    agents = load_agents()
    recipient = next(
        (a for a in agents if a["name"].lower() == target_name.lower()), None
    )
    if not recipient:
        available = [a["name"] for a in agents]
        return jsonify(
            {"error": f"Agent '{target_name}' nicht gefunden", "available": available}
        ), 404

    now = datetime.now()
    task = {
        "id": str(uuid.uuid4()),
        "sender_agent_id": sender_id,
        "sender_agent_name": sender_name,
        "recipient_agent_id": recipient["id"],
        "recipient_agent_name": recipient["name"],
        "message": message,
        "status": "submitted",  # A2A state
        "contextId": str(uuid.uuid4()),
        "skill_used": None,
        "result_text": None,
        "result_image": None,
        "result_data": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=180)).isoformat(),
        "history": [],
        "artifacts": [],
    }
    with _tasks_lock:
        _TASKS[task["id"]] = task
    _save_tasks()

    spawn_background(process_task, task["id"])
    print(
        f"[Task] created {task['id']}: {sender_name} → {recipient['name']}: {message[:60]}",
        flush=True,
    )
    return jsonify(task), 202


@app.route("/api/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        # Fall back to disk (e.g. after server restart)
        tasks_on_disk = _load_tasks_from_disk()
        task = tasks_on_disk.get(task_id)
    if not task:
        return jsonify({"error": "Task nicht gefunden"}), 404

    # Auto-timeout stuck tasks
    if task["status"] in ("submitted", "working"):
        try:
            if datetime.now().isoformat() > task["timeout_at"]:
                task["status"] = "failed"
                task["error"] = "Timeout"
                _save_tasks()
        except Exception:
            pass

    return jsonify(task)


# ─── A2A Protocol Endpoints ───────────────────────────────────────────────────


@app.route("/api/a2a/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id):
    """Cancel a task - A2A operation."""
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        return jsonify({"error": "Task nicht gefunden"}), 404

    if task["status"] not in A2A_TASK_CANCELABLE_STATES:
        return jsonify(
            {"error": f"Task cannot be canceled - status is {task['status']}"}
        ), 400

    task["status"] = "canceled"
    task["completed_at"] = datetime.now().isoformat()
    _save_tasks()
    print(f"[A2A] Task {task_id} canceled", flush=True)
    return jsonify(task)


@app.route("/api/a2a/tasks/<task_id>/subscribe", methods=["GET"])
def subscribe_to_task(task_id):
    """SSE streaming for task updates - A2A operation."""
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        return jsonify({"error": "Task nicht gefunden"}), 404

    def generate():
        import flask

        last_status = task.get("status")
        yield f"data: {json.dumps({'task': task})}\n\n"

        while task["status"] not in TERMINAL_STATES:
            time.sleep(1)
            with _tasks_lock:
                current = _TASKS.get(task_id)
            if current and current.get("status") != last_status:
                last_status = current["status"]
                yield f"data: {json.dumps({'statusUpdate': {'state': last_status}})}\n\n"

        if task["status"] in TERMINAL_STATES:
            with _tasks_lock:
                final = _TASKS.get(task_id)
            yield f"data: {json.dumps({'task': final})}\n\n"

    from flask import Response

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/a2a/tasks", methods=["GET"])
def list_tasks():
    """List tasks with pagination - A2A operation."""
    page_token = request.args.get("pageToken", "")
    max_tasks = request.args.get("maxTasks", 20, type=int)
    include_artifacts = request.args.get("includeArtifacts", "false").lower() == "true"

    all_tasks = list(_TASKS.values())
    all_tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)

    start = 0
    if page_token:
        try:
            start = int(base64.b64decode(page_token).decode())
        except Exception:
            start = 0

    end = start + max_tasks
    page_tasks = all_tasks[start:end]

    for t in page_tasks:
        if not include_artifacts and "artifacts" in t:
            t.pop("artifacts", None)

    next_token = ""
    if end < len(all_tasks):
        next_token = base64.b64encode(str(end).encode()).decode()

    return jsonify(
        {
            "tasks": page_tasks,
            "nextPageToken": next_token,
        }
    )


@app.route("/api/a2a/tasks/<task_id>/pushConfig", methods=["POST"])
def create_push_config(task_id):
    """Create push notification config for task - A2A operation."""
    data = request.json or {}
    webhook_url = data.get("webhookUrl")
    if not webhook_url:
        return jsonify({"error": "webhookUrl required"}), 400

    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        return jsonify({"error": "Task nicht gefunden"}), 404

    config = {
        "id": str(uuid.uuid4()),
        "taskId": task_id,
        "webhookUrl": webhook_url,
        "authentication": data.get("authentication"),
    }
    if "pushConfigs" not in task:
        task["pushConfigs"] = []
    task["pushConfigs"].append(config)
    _save_tasks()
    return jsonify(config)


@app.route("/api/a2a/tasks/<task_id>/input", methods=["POST"])
def task_input_required(task_id):
    """Set task to input-required state - agent requests more input."""
    data = request.json or {}
    message = data.get("message", "")

    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        return jsonify({"error": "Task nicht gefunden"}), 404

    task["status"] = "input-required"
    task["history"].append(
        {
            "role": "agent",
            "parts": [{"type": "text", "text": message}],
        }
    )
    _save_tasks()
    return jsonify(task)


@app.route("/api/a2a/agents/<agent_id>/card", methods=["GET"])
def get_extended_agent_card(agent_id):
    """Get extended agent card - A2A operation."""
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404

    card = build_agent_card(agent)
    card["extended"] = True
    card["securitySchemes"] = {}
    card["security"] = []
    return jsonify(card)


@app.route("/api/activity", methods=["GET"])
def get_activity():
    with _activity_lock:
        return jsonify(dict(_ACTIVITY))


# ─── ComfyUI Image Generation ─────────────────────────────────────────────────


def build_z_image_turbo_workflow(prompt, seed):
    """z_image_turbo workflow — fast local model (8 steps)."""
    import copy

    wf = {
        "9": {
            "inputs": {"filename_prefix": "agentclaw", "images": ["57:8", 0]},
            "class_type": "SaveImage",
        },
        "57:30": {
            "inputs": {
                "clip_name": "qwen_3_4b.safetensors",
                "type": "lumina2",
                "device": "default",
            },
            "class_type": "CLIPLoader",
        },
        "57:29": {"inputs": {"vae_name": "ae.safetensors"}, "class_type": "VAELoader"},
        "57:33": {
            "inputs": {"conditioning": ["57:27", 0]},
            "class_type": "ConditioningZeroOut",
        },
        "57:8": {
            "inputs": {"samples": ["57:3", 0], "vae": ["57:29", 0]},
            "class_type": "VAEDecode",
        },
        "57:28": {
            "inputs": {
                "unet_name": "z_image_turbo_bf16.safetensors",
                "weight_dtype": "default",
            },
            "class_type": "UNETLoader",
        },
        "57:27": {
            "inputs": {"text": prompt, "clip": ["57:30", 0]},
            "class_type": "CLIPTextEncode",
        },
        "57:13": {
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
            "class_type": "EmptySD3LatentImage",
        },
        "57:11": {
            "inputs": {"shift": 3, "model": ["57:28", 0]},
            "class_type": "ModelSamplingAuraFlow",
        },
        "57:3": {
            "inputs": {
                "seed": seed,
                "steps": 8,
                "cfg": 1,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["57:11", 0],
                "positive": ["57:27", 0],
                "negative": ["57:33", 0],
                "latent_image": ["57:13", 0],
            },
            "class_type": "KSampler",
        },
    }
    return wf


@app.route("/api/memory/<agent_id>", methods=["GET"])
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


@app.route("/api/memory/<agent_id>", methods=["DELETE"])
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


# ─── Document Memory (PDF/Images via Gemini Embedding 2) ───────────────────────────


@app.route("/api/memory/<agent_id>/document", methods=["POST"])
def memory_upload_document(agent_id):
    """Upload PDF or image - store as vector in Qdrant."""
    if "document_memory" not in _get_agent_skills(agent_id):
        return jsonify({"error": "document_memory skill not active"}), 403

    # Check for file in request
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = file.filename.lower()
    file_data = file.read()

    # Determine file type
    is_pdf = filename.endswith(".pdf")
    is_image = filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))

    if not (is_pdf or is_image):
        return jsonify({"error": "Only PDF and images supported"}), 400

    # Try Google API first, fallback to Ollama
    providers = load_providers()
    google_key = providers.get("google_api", {}).get("api_key", "")

    try:
        if google_key and is_pdf:
            # Use Gemini Embedding 2 for PDF
            import base64

            b64 = base64.b64encode(file_data).decode()

            # For now, use text extraction as fallback since Gemini 2 embedding
            # might need specific setup. Try extracting text from PDF first
            try:
                import PyPDF2
                from io import BytesIO

                reader = PyPDF2.PdfReader(BytesIO(file_data))
                text = "\n".join([page.extract_text() or "" for page in reader.pages])
            except:
                text = f"[Image/PDF file: {filename}]"

            # Use Google for embedding
            import requests

            resp = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent",
                headers={"Authorization": f"Bearer {google_key}"},
                json={"content": {"role": "user", "parts": [{"text": text[:2000]}]}},
                timeout=30,
            )
            if resp.ok:
                embedding = resp.json()["embedding"]["values"]
                _store_document_vector(agent_id, filename, text[:1000], embedding)
                return jsonify({"ok": True, "filename": filename, "type": "pdf"})
        elif is_image:
            # For images, use text description as fallback
            text = f"[Image: {filename}]"
            if google_key:
                import requests

                resp = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent",
                    headers={"Authorization": f"Bearer {google_key}"},
                    json={"content": {"role": "user", "parts": [{"text": text}]}},
                    timeout=30,
                )
                if resp.ok:
                    embedding = resp.json()["embedding"]["values"]
                    _store_document_vector(agent_id, filename, text, embedding)
                    return jsonify({"ok": True, "filename": filename, "type": "image"})

        # Fallback to Ollama embeddings
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        text = f"[Document: {filename}]"
        resp = requests.post(
            f"{ollama_url}/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text},
            timeout=30,
        )
        if resp.ok:
            embedding = resp.json()["embedding"]
            _store_document_vector(agent_id, filename, text, embedding)
            return jsonify({"ok": True, "filename": filename, "type": "fallback"})

        return jsonify({"error": "No embedding provider available"}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _get_agent_skills(agent_id):
    agents = load_agents()
    for a in agents:
        if a["id"] == agent_id:
            return set(a.get("skills", []))
    return set()


def _store_document_vector(agent_id, filename, text, embedding):
    """Store document embedding in Qdrant."""
    client = get_qdrant()
    if not client:
        return

    from qdrant_client.models import PointStruct, VectorParams, Distance

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
                    "text": text,
                    "type": "document",
                    "ts": datetime.now().isoformat(),
                },
            )
        ],
    )
    print(f"[Document Memory] stored {filename} for agent {agent_id}", flush=True)


@app.route("/api/comfyui/config", methods=["GET"])
def comfyui_config():
    cfg = load_providers().get("comfyui", {})
    return jsonify(
        {
            "url": cfg.get("url", "http://localhost:8188"),
            "workflow": build_z_image_turbo_workflow("__PROMPT__", 0),
        }
    )


@app.route("/api/comfyui/generate", methods=["POST"])
def comfyui_generate():
    data = request.json
    prompt = data.get("prompt", "").strip()
    width = int(data.get("width", 1024))
    height = int(data.get("height", 1024))
    seed = data.get("seed", int(__import__("time").time()) % (2**32))

    if not prompt:
        return jsonify({"error": "Kein Prompt"}), 400

    providers = load_providers()
    cfg = providers.get("comfyui", {})
    base_url = cfg.get("url", "http://localhost:8188").rstrip("/")

    workflow = build_z_image_turbo_workflow(prompt, seed)

    try:
        # Queue prompt
        r = requests.post(
            f"{base_url}/prompt",
            json={"prompt": workflow, "client_id": "agentclaw"},
            timeout=30,
        )
        r.raise_for_status()
        resp_json = r.json()
        if "prompt_id" not in resp_json:
            return jsonify({"error": f"ComfyUI Antwort unerwartet: {resp_json}"}), 500
        prompt_id = resp_json["prompt_id"]
        print(f"[ComfyUI] queued prompt_id={prompt_id}", flush=True)
    except Exception as e:
        return jsonify({"error": f"ComfyUI Fehler: {str(e)}"}), 500

    # Poll history (max 360s)
    import time

    deadline = time.time() + 360  # 6 min timeout for image editing
    outputs = None
    while time.time() < deadline:
        time.sleep(2)
        h = requests.get(f"{base_url}/history/{prompt_id}", timeout=10)
        entry = h.json().get(prompt_id, {})
        if entry.get("status", {}).get("completed"):
            outputs = entry.get("outputs", {})
            break

    if not outputs:
        return jsonify(
            {"error": "Timeout: ComfyUI hat nicht rechtzeitig geantwortet"}
        ), 504

    # Find first image in outputs
    img_info = None
    for node_out in outputs.values():
        imgs = node_out.get("images", [])
        if imgs:
            img_info = imgs[0]
            break

    if not img_info:
        return jsonify({"error": "Keine Bilddaten in der Antwort"}), 500

    filename = img_info["filename"]
    subfolder = img_info.get("subfolder", "")
    img_type = img_info.get("type", "output")
    params = f"filename={filename}&type={img_type}"
    if subfolder:
        params += f"&subfolder={subfolder}"

    img_r = requests.get(f"{base_url}/view?{params}", timeout=30)
    img_r.raise_for_status()
    mime = img_r.headers.get("Content-Type", "image/png").split(";")[0]
    b64 = base64.b64encode(img_r.content).decode()
    print(
        f"[ComfyUI] image ready: {filename} ({len(img_r.content) // 1024}KB)",
        flush=True,
    )
    return jsonify({"image": f"data:{mime};base64,{b64}", "filename": filename})


if __name__ == "__main__":
    port = 5050
    print(f"Starting on http://0.0.0.0:{port} with WebSocket support", flush=True)
    socketio.run(
        app,
        debug=True,
        host="0.0.0.0",
        port=port,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
