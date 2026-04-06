"""
core/state.py — Globaler In-Memory-State und Threading-Locks.
Kein Import aus anderen eigenen Modulen (Ebene 0).
"""
import os
import re
import threading

# ── Debug Logging ─────────────────────────────────────────────────────────────
_DEBUG_LOG: bool = os.environ.get("DEBUG_LOG", "0") == "1"

# ── Threading Locks ───────────────────────────────────────────────────────────
_agents_lock    = threading.Lock()
_history_lock   = threading.Lock()
_providers_lock = threading.Lock()
_watchdogs_lock = threading.Lock()
_tasks_lock     = threading.Lock()
_activity_lock  = threading.Lock()
_events_lock    = threading.Lock()
_nodes_lock     = threading.Lock()

# ── Agent Tasks In-Memory Store ───────────────────────────────────────────────
_TASKS: dict = {}
_TASK_TTL_SECONDS = 3600  # Completed/failed Tasks nach 1h aus Memory entfernen

# ── WebSocket Client Registry ─────────────────────────────────────────────────
_USERS: dict = {}  # sid -> {agent_ids: []}

# ── Live Activity Tracker ─────────────────────────────────────────────────────
# { agent_id: { "type": "heartbeat"|"task", "label": str, "since": iso } }
_ACTIVITY: dict = {}

# ── Event System for Push Updates ─────────────────────────────────────────────
_EVENTS: list = []
_EVENT_VERSION: int = 0

# ── Mac Mail Pending Sort State (per-Agent) ───────────────────────────────────
_PENDING_MAIL_SORT: dict = {}  # { agent_id: bool }

# ── Redis Client (lazy init) ──────────────────────────────────────────────────
_redis_client = None

# ── Mac Mail Trigger Regex (Modul-Ebene für Heartbeat + process_task) ─────────
MAC_MAIL_TRIGGERS = re.compile(
    r"\b(mails?|e-?mails?|emails?)\b|"
    r"\b(posteingang|postfach|inbox)\b|"
    r"\b(anhang|attachment)\b|"
    r"\b(eingang|eingegangen)\b.{0,20}\b(nachricht|mail)\b|"
    r"\b(mail|nachricht)\b.{0,20}\b(eingang|eingegangen|neu|lesen|zeig|lies|check)\b|"
    r"\b(verschieb|archiviere?)\b.{0,30}\b(nachricht|mail)\b|"
    r"\b(ordner)\b.{0,20}\b(anlegen|erstellen|neu)\b|"
    r"\b(aufräum|sortier|einsortier|organis|kategorisier)\w*\b|"
    r"\b(triage|prüf\s*mail|check\s*mail|mail.*bewert|mail.*prüf|wichtig.*mail|dringend.*mail)\w*\b|"
    r"\b(älteste?|oldest)\b.{0,30}\b(mail|mails?|nachricht)\b|"
    r"\b(verschieb|move)\b.{0,40}\b(martin)\b|"
    r"\bja[,.]?\s*(verschieb|sortier|mach|go|los|ok|weiter|bestätig)\w*\b",
    re.IGNORECASE,
)
