"""
core/skills_registry.py — SKILLS-Metadaten, _SKILL_MAP, Codebase-Kontext.
"""
import os
from core.config import BASE_DIR


def _get_codebase_context() -> str:
    """Erstellt eine kompakte Übersicht des AgentClaw-Quellcodes für den codebase_read Skill."""
    lines = ["--- SYSTEM CODEBASE ZUGRIFF ---",
             f"Projekt: AgentClaw | Pfad: {BASE_DIR}",
             ""]
    try:
        entries = sorted(os.listdir(BASE_DIR))
        py_files = [e for e in entries if e.endswith(".py") and not e.startswith("_")]
        dirs = [e for e in entries if os.path.isdir(os.path.join(BASE_DIR, e))
                and not e.startswith(".") and e not in ("__pycache__", "venv", ".venv", "node_modules")]
        lines.append("Hauptdateien:")
        for f in py_files[:12]:
            fpath = os.path.join(BASE_DIR, f)
            size = os.path.getsize(fpath) // 1024
            lines.append(f"  {f} ({size} KB)")
        lines.append("")
        lines.append("Verzeichnisse: " + ", ".join(dirs[:12]))
    except Exception:
        pass
    lines += [
        "",
        "Architektur:",
        "  app.py          — Flask-Backend, alle API-Routen, Skills, WebSocket",
        "  templates/index.html — Komplette SPA (HTML/JS/CSS)",
        "  core/state.py   — Locks + globale State-Variablen",
        "  core/config.py  — Pfade, Konstanten, Helpers",
        "  core/skills_registry.py — SKILLS-Metadaten",
        "  storage/*.py    — agents.json, history.json, providers.json I/O",
        "  mac_mail/skill.py — AppleScript Mail-Integration",
        "  skills/*.py     — Image, Video, Telegram, Gmail, URL Skills",
        "  routes/*.py     — Flask Blueprints (Phase 3+)",
        "  WebSocket NS: /ws | Port: 5050 | async_mode: threading",
        "  A2A: @AgentName Task → _dispatch_mentions_from_reply()",
        "  Tasks: _TASKS dict + _tasks_lock + _enqueue_task() + tick_task_queue()",
        "",
    ]
    lines.append("Wichtige API-Routen:")
    routes = [
        "POST /api/chat            — Chat mit Agent (LLM + Skills)",
        "POST /api/tasks           — Neuen A2A-Task erstellen",
        "GET  /api/tasks/<id>      — Task-Status abfragen",
        "GET/PUT /api/agents       — Agenten laden/speichern",
        "PUT /api/agents/<id>/settings — Agent-Settings",
        "PUT /api/agents/<id>/heartbeat — Heartbeat-Prompt",
        "POST /api/a2a/dispatch    — A2A Task direkt dispatchen",
        "GET  /api/providers       — Provider-Config",
        "POST /api/tts             — Text-to-Speech",
        "DELETE /api/history/<id>  — Chat-History löschen",
    ]
    lines += [f"  {r}" for r in routes]
    lines.append("--- ENDE CODEBASE ---")
    return "\n".join(lines)


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
        "id": "video_gen",
        "name": "Video Producer",
        "icon": "🎬",
        "description": "Generates 5-second videos via ComfyUI using Wan 2.2 T2V (14B) with LightX2V — triggered by keywords like 'video', 'animiere', 'clip'",
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
    {
        "id": "mac_mail",
        "name": "Mac Mail",
        "icon": "📬",
        "description": "Liest E-Mails und Anhänge aus Apple Mail, verschiebt Nachrichten und legt Ordner an (via MCP auf Port 5051)",
        "requires": "mac_mail_mcp",
    },
    {
        "id": "codebase_read",
        "name": "Codebase Lesen",
        "icon": "🗂️",
        "description": "Gibt Zugriff auf Quellcode, Projektstruktur und Konfigurationsdateien des AgentClaw-Systems. Automatisch aktiv für Favoriten-Agenten.",
        "requires": None,
    },
    {
        "id": "orchestrator",
        "name": "Orchestrator",
        "icon": "🎯",
        "description": "Delegierungen dieses Agenten landen in der Inbox des Ziel-Agenten statt als direkter Task. Ideal für planende Agenten mit starkem LLM.",
        "requires": None,
    },
    {
        "id": "youtube",
        "name": "YouTube Download",
        "icon": "📺",
        "description": "Lädt YouTube-Videos oder Audio (MP3) herunter via yt-dlp. Unterstützt auch Video-Info abrufen ohne Download.",
        "requires": "yt-dlp",
    },
    {
        "id": "transcription",
        "name": "Video/Audio Transkription",
        "icon": "🎙️",
        "description": "Transkribiert und analysiert Videos und Audio-Dateien via ffmpeg + Ollama (gemma4/whisper). Funktioniert mit hochgeladenen Dateien und lokalen Pfaden.",
        "requires": "ollama",
    },
    {
        "id": "file_access",
        "name": "Datei-Zugriff (Downloads)",
        "icon": "📁",
        "description": "Lesen, Schreiben und Auflisten von Dateien im ~/Downloads/AgentClaw Ordner. Ermöglicht Transkriptionen, Ergebnisse und andere Inhalte als Dateien zu speichern.",
        "requires": None,
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "icon": "💼",
        "description": "Postet und plant LinkedIn-Beiträge via LinkedIn API. Unterstützt sofortiges Posten und zeitgesteuertes Scheduling (z.B. 'morgen um 9 Uhr', 'Freitag 14:00').",
        "requires": "LinkedIn Access Token",
    },
]

_SKILL_MAP = {s["id"]: s for s in SKILLS}


def _build_agent_directory(current_agent_id: str = None) -> str:
    """
    Baut ein kompaktes Agent-Verzeichnis und Delegationsregeln dynamisch auf.
    Filtert Agenten heraus, deren Skills der aktuelle Agent bereits selbst besitzt.
    """
    from storage.agents import load_agents

    agents = load_agents()
    if not agents:
        return ""

    current_agent = next((a for a in agents if a["id"] == current_agent_id), None)
    my_skills = set(current_agent.get("skills", [])) if current_agent else set()

    def _skill_label(sid: str) -> str:
        s = _SKILL_MAP.get(sid)
        return f"{s['icon']} {s['name']}" if s else sid

    skill_to_agents = {}
    for a in agents:
        if a["id"] == current_agent_id:
            continue
        for s in a.get("skills", []):
            if s not in my_skills:
                skill_to_agents.setdefault(s, []).append(a["name"])

    own_skills_str = (
        ", ".join(_skill_label(s) for s in sorted(my_skills)) if my_skills else "keine"
    )
    lines = [
        "--- AGENT NETZWERK ---",
        f"⚡ DEINE EIGENEN SKILLS: {own_skills_str}",
        f"→ Diese Skills führst du IMMER selbst aus. Für diese Skills delegierst du NIEMALS.",
        "",
        "Für Skills die du NICHT besitzt kannst du @AgentName <Task> delegieren:",
        "",
    ]

    delegation_map = [
        ("Screenshot", "screenshot"),
        ("Website analysieren / URL check", "url_fetch"),
        ("Bild generieren / Foto / Malen", "image_gen"),
        ("Video generieren / Animieren / Clip", "video_gen"),
        ("Prompt optimieren", "prompt_optimize"),
        ("Tagesschau / Nachrichten", "tagesschau"),
        ("Hacker News / Tech News", "hackernews"),
        ("Memory / Erinnerungen", "memory"),
        ("Telegram Nachrichten", "telegram_incoming"),
        ("Web Suche", "web_search"),
        ("Gmail / E-Mail", "gmail"),
    ]

    for label, sid in delegation_map:
        if sid in my_skills:
            continue
        target_agents = skill_to_agents.get(sid, [])
        if target_agents:
            mentions = " oder ".join([f"@{name}" for name in target_agents])
            lines.append(f"• **{label}** → {mentions}")

    lines += [
        "",
        "VERFÜGBARE HILFS-AGENTS IM NETZWERK:",
    ]

    for a in agents:
        if a["id"] == current_agent_id:
            continue
        other_skills = set(a.get("skills", []))
        useful_skills = other_skills - my_skills
        if useful_skills:
            skill_str = ", ".join(_skill_label(s) for s in sorted(useful_skills))
            lines.append(f"  • {a['name']} — kann: {skill_str}")

    lines += [
        "",
        "REGEL: Jeder Agent sieht nur die Skills anderer Agents die er selbst NICHT hat.",
        "Ein Agent der für einen Skill nicht gelistet ist → nutze deinen eigenen.",
        "--- ENDE AGENT NETZWERK ---",
    ]
    return "\n".join(lines)
