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
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
import requests
import redis

# ── Skills modules ──────────────────────────────────────────────────────────────
from skills import (
    IMG_TRIGGERS, VIDEO_TRIGGERS, IMAGE_EDIT_TRIGGERS,
    PROMPT_OPTIMIZE_TRIGGERS, PROMPT_FRAMEWORKS,
    _extract_img_prompt, _extract_video_prompt, _prepare_video_prompt,
    _optimize_prompt_for_image, _upload_image_to_comfyui,
    build_firered_edit_workflow, _build_wan_video_workflow,
    build_z_image_turbo_workflow,
    _run_comfyui_sync, _run_comfyui_video, _run_comfyui_edit,
    _make_thumbnail, WAN_VIDEO_NEGATIVE,
    _is_safe_url, fetch_url_text,
    _run_telegram, _run_gmail, _optimize_prompt,
    _run_youtube, YT_TRIGGERS, YT_URL_RX, _yt_last_result,
    _run_transcription, _transcribe_uploaded_video, TRANSCRIBE_TRIGGERS,
    _run_file_access, _write_downloads_file, FILE_TRIGGERS,
    _run_linkedin, _process_linkedin_scheduled, LI_TRIGGERS,
)

# ── Phase 1+2 Refactoring: Core + Storage + MacMail modules ───────────────────
from core.state import (
    _DEBUG_LOG,
    _agents_lock, _history_lock, _providers_lock, _watchdogs_lock,
    _tasks_lock, _activity_lock, _events_lock,
    _TASKS, _TASK_TTL_SECONDS, _USERS, _ACTIVITY, _EVENTS,
    _PENDING_MAIL_SORT, MAC_MAIL_TRIGGERS,
)
import core.state as _cstate
from core.config import (
    BASE_DIR, AGENTS_FILE, HISTORY_FILE, PROVIDERS_FILE,
    WATCHDOGS_FILE, TASKS_FILE, BACKUP_DIR,
    MAX_HISTORY_PER_AGENT, MAX_CONTENT_LENGTH,
    dlog, spawn_background, _read_json, _write_json,
)
from storage.agents import load_agents, save_agents, patch_agent_heartbeat
from storage.history import load_history, save_history
from storage.providers import (
    load_providers, save_providers, get_redis_client,
    log_a2a_event, get_a2a_events,
)
from storage.watchdogs import load_watchdogs, save_watchdogs, update_watchdog_field
from storage.nodes import (
    load_nodes, save_nodes, get_node_by_alias,
    update_node_cache, mark_node_offline, get_self_identity,
)
from mac_mail.skill import _run_mac_mail
from core.skills_registry import SKILLS, _SKILL_MAP, _get_codebase_context, _build_agent_directory
from core.memory import (
    get_qdrant, embed_text, collection_name, ensure_collection,
    memory_search, memory_store, _run_dream_cycle, run_dream_for_agent,
    QDRANT_AVAILABLE,
)
from core.llm import call_agent_text

load_dotenv()

# ← moved to modules (Phase 1+2): _DEBUG_LOG, dlog

# ── Wenn als py2app .app-Bundle gestartet, Resources-Verzeichnis ermitteln
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
from flask_socketio import SocketIO, emit, disconnect

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_timeout=30,
    ping_interval=10,
)


# ─── Route Blueprints (Phase 3) ─────────────────────────────────────────────
from routes.tts import bp as bp_tts
from routes.providers import bp as bp_providers
from routes.backup import bp as bp_backup
from routes.content import bp as bp_content
from routes.stats import bp as bp_stats
from routes.inbox import bp as bp_inbox
from routes.upload import bp as bp_upload

app.register_blueprint(bp_tts)
app.register_blueprint(bp_providers)
app.register_blueprint(bp_backup)
app.register_blueprint(bp_content)
app.register_blueprint(bp_stats)
app.register_blueprint(bp_inbox)
app.register_blueprint(bp_upload)

from routes.memory import bp as bp_memory
from routes.websocket import (
    register_ws_handlers,
    ws_emit, emit_agent_activity, emit_task_result,
    emit_chat_message, emit_heartbeat_result, emit_error,
)
app.register_blueprint(bp_memory)
register_ws_handlers(socketio)

MISTRAL_TTS_URL = "https://api.mistral.ai/v1/audio/speech"
MISTRAL_VOICES_URL = "https://api.mistral.ai/v1/audio/voices"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

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

A2A_TASK_CANCELABLE_STATES = {"submitted", "working", "input-required", "queued"}

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


# ─── A2A Communication Prompt ─────────────────────────────────────────────────

A2A_COMMUNICATION_PROMPT = """
--- A2A KOMMUNIKATION ---
Du bist Teil des AgentClaw Multi-Agent-Systems.

VERHALTENSREGELN:
1. Prüfe IMMER zuerst, ob du eine Anfrage mit DEINEN EIGENEN SKILLS (siehe unten) lösen kannst.
2. Delegiere NUR an andere Agents (@Mention), wenn du den benötigten Skill absolut nicht selbst besitzt.
3. Wenn du Informationen im Gedächtnis (Memory) findest, präsentiere sie selbst, anstatt jemanden anderen zu fragen.
4. Antworte präzise und minimal — keine langen Erklärungen.

DELEGIERUNG (@Mention) — KRITISCHE REGELN:
  • Schreibe @AgentName gefolgt von ALLEN notwendigen Schritten — in einer einzigen @Mention!
  • Du wirst KEIN ERGEBNIS zurückbekommen. Der andere Agent erledigt ALLES selbst bis zum Ende.
  • Schreibe NIEMALS "Sobald ich die Ergebnisse habe..." — du bekommst keine. Der Agent macht alles.
  • Beispiel für mehrstufig: "@Flo 1. Hole die neusten Hackernews. 2. Schreibe einen Bericht. 3. Sende den Bericht an Telegram."
  • NIEMALS [TOOL_CALL], JSON, Funktionsaufrufe oder ähnliche Formate verwenden!
  • NIEMALS Delegation ankündigen ("Ich delegiere...") — einfach direkt @AgentName schreiben.
  • Falsch: "@Flo hole Hackernews" + danach selbst weitermachen
  • Richtig: "@Flo hole Hackernews, schreibe Bericht, sende an Telegram" (alles in einer Zeile!)

WICHTIG: Nutze dein eigenes Memory, um Fragen zu Inhalten, Dokumenten oder früheren Chats zu beantworten.
--- ENDE A2A ---
""".strip()


# ← moved to modules (Phase 1+2): BASE_DIR, AGENTS_FILE, HISTORY_FILE, PROVIDERS_FILE, WATCHDOGS_FILE, TASKS_FILE

# ← moved to modules (Phase 1+2): _TASKS, _tasks_lock, _TASK_TTL_SECONDS


def _cleanup_old_tasks():
    """Entfernt abgeschlossene Tasks die älter als TTL sind aus dem In-Memory-Store."""
    cutoff = (datetime.now() - timedelta(seconds=_TASK_TTL_SECONDS)).isoformat()
    with _tasks_lock:
        to_remove = [
            tid for tid, t in _TASKS.items()
            if t.get("status") in ("completed", "failed", "cancelled")
            and (t.get("completed_at") or "9999") < cutoff
        ]
        for tid in to_remove:
            del _TASKS[tid]
    if to_remove:
        print(f"[Tasks] Cleanup: {len(to_remove)} alte Tasks aus Memory entfernt", flush=True)
        _save_tasks()


def _init_tasks():
    """Beim Start: offene Tasks von Disk laden. Working→failed (Worker existiert nicht mehr)."""
    global _TASKS
    loaded = _load_tasks_from_disk()
    recovered = 0
    with _tasks_lock:
        for tid, t in loaded.items():
            if t.get("status") == "working":
                t["status"] = "failed"
                t["error"] = "Neustart während Ausführung"
            if t.get("status") not in ("completed", "failed", "cancelled"):
                _TASKS[tid] = t
                recovered += 1
    if recovered:
        print(f"[Tasks] {recovered} offene Tasks von Disk geladen", flush=True)

# ← moved to modules (Phase 1+2): _PENDING_MAIL_SORT, MAC_MAIL_TRIGGERS

# ← moved to modules (Phase 1+2): _EVENTS, _events_lock, _EVENT_VERSION
_EVENT_VERSION = 0  # local tracking var for emit_event/get_events_since in this module


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


# ← moved to modules (Phase 1+2): _ACTIVITY, _activity_lock
# { agent_id: { "type": "heartbeat"|"task", "label": str, "since": iso } }


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
    tmp = TASKS_FILE + ".tmp"
    with _tasks_lock:
        tasks_list = list(_TASKS.values())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(tasks_list, f, ensure_ascii=False, indent=2)
            os.replace(tmp, TASKS_FILE)
        except Exception as e:
            print(f"[Tasks] _save_tasks Fehler: {e}", flush=True)
            try:
                os.remove(tmp)
            except OSError:
                pass


def process_task(task_id: str):
    """Background worker: process an agent task."""
    with _tasks_lock:
        task = _TASKS.get(task_id)
    if not task:
        return

    # Timeout-Check: wenn Task zu lange in der Queue lag
    if datetime.now().isoformat() > task.get("timeout_at", "9999"):
        task["status"] = "failed"
        task["error"] = "Timeout vor Ausführungsstart"
        task["completed_at"] = datetime.now().isoformat()
        _save_tasks()
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

    # If agent only has image_gen/video_gen skill, treat every task as that prompt
    only_image_gen = skills == {"image_gen"}
    only_video_gen = "video_gen" in skills and not skills.intersection({"image_gen", "image_edit"})

    TG_TRIGGERS = re.compile(
        r"schick.*(das\s*)?(bild|foto|photo|image).*telegram|"
        r"schick.*telegram|"
        r"sende?\s*(die\s*)?(nachricht|message|text|bild|foto).*telegram|"
        r"sende?.*an\s*(den\s*)?(telegram|tg)|"
        r"send.*(the\s*)?(image|picture|photo).*telegram|"
        r"send.*to\s*telegram|"
        r"(nachricht|message|text).*an\s*(den\s*)?(telegram|tg)|"
        r"telegram.*(kanal|channel|gruppe|group|chat)|"
        r"telegram.*(bild|foto|image)|"
        r"tg\s*send|"
        r"post.*telegram|"
        r"schreib.*telegram",
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

    SCREENSHOT_TRIGGERS = re.compile(
        r"screenshot|"
        r"screenshot\s+von|"
        r"screenshot\s+of|"
        r"seite\s+screenshot|"
        r"webseite\s+screenshot|"
        r"seite\s+knipsen|"
        r"bild\s+von\s+.*seite|"
        r"capture\s+screen|"
        r"take\s+a\s+screenshot",
        re.IGNORECASE,
    )

    # MAC_MAIL_TRIGGERS ist auf Modul-Ebene definiert (für Heartbeat-Zugriff)

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
            # Wenn vorheriger Schritt schon einen Text erzeugt hat (z.B. Redaktionsbericht),
            # diesen als Inhalt bevorzugen, nicht nochmal aus der Message extrahieren
            prev_text = task.get("result_text", "")
            tg_message = message if not prev_text else f"{message}\n\n{prev_text}"
            task["result_text"] = _run_telegram(tg_message, image_b64)
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
        elif "prompt_optimize" in skills and PROMPT_OPTIMIZE_TRIGGERS.search(message):
            print(f"[Task] prompt_optimize trigger detected: {message[:60]}", flush=True)
            fw_id = "RTF"
            for fid in PROMPT_FRAMEWORKS:
                if fid in message.upper():
                    fw_id = fid
                    break
            if re.search(r"\bseo\b", message, re.IGNORECASE):
                fw_id = "BAB"
            elif re.search(r"\bstrateg\w+\b", message, re.IGNORECASE):
                fw_id = "RISE"
            raw = re.sub(
                r"^.{0,80}?(?:optimize|improve|refine|enhance|rewrite|optimiere|verbessere)[^:\"]*[:\"]\s*",
                "",
                message,
                flags=re.IGNORECASE,
            ).strip() or message
            task["result_text"] = _optimize_prompt(raw, fw_id)
            task["skill_used"] = "prompt_optimize"
        # Screenshot skill
        elif "screenshot" in skills and SCREENSHOT_TRIGGERS.search(message):
            print(f"[Task] screenshot trigger detected: {message[:60]}", flush=True)
            # Extract URL from message
            import re as re_module

            url_match = re_module.search(
                r"(https?://[^\s]+)|([a-zA-Z0-9][a-zA-Z0-9-]+\.[a-zA-Z]{2,}[^\s]*)",
                message,
                re_module.IGNORECASE,
            )
            if url_match:
                url = url_match.group(1) or url_match.group(2)
                if not url.startswith("http"):
                    url = "https://" + url
                print(f"[Task] screenshot URL: {url}", flush=True)
                try:
                    r = requests.post(
                        "http://localhost:5050/api/screenshot",
                        json={"url": url},
                        timeout=30,
                    )
                    if r.ok:
                        data = r.json()
                        if data.get("image"):
                            task["result_image"] = data["image"]
                            task["skill_used"] = "screenshot"
                        else:
                            task["result_text"] = (
                                "❌ Screenshot fehlgeschlagen: kein Bild zurück"
                            )
                    else:
                        task["result_text"] = f"❌ Screenshot Fehler: {r.status_code}"
                except Exception as e:
                    task["result_text"] = f"❌ Screenshot Fehler: {str(e)}"
            else:
                task["result_text"] = "❌ Keine URL im Prompt gefunden"
            if not task.get("result_image"):
                task["skill_used"] = task.get("skill_used", "screenshot")
        elif "mac_mail" in skills and (MAC_MAIL_TRIGGERS.search(message) or _PENDING_MAIL_SORT.get(task["recipient_agent_id"], False)):
            _agent_pending = _PENDING_MAIL_SORT.get(task["recipient_agent_id"], False)
            print(f"[Task] mac_mail trigger detected (pending={_agent_pending}): {message[:60]}", flush=True)
            task["result_text"] = _run_mac_mail(message, task["recipient_agent_id"])
            task["skill_used"] = "mac_mail"
        elif "video_gen" in skills and (VIDEO_TRIGGERS.search(message) or only_video_gen):
            # Extend task timeout to 20 min for video generation
            task["timeout_at"] = (datetime.now() + timedelta(seconds=1210)).isoformat()
            vid_prompt = _extract_video_prompt(message) or message
            task["prompt_used"] = vid_prompt
            print(f"[Task] video_gen prompt: {vid_prompt}", flush=True)
            task["result_image"] = _run_comfyui_video(vid_prompt)  # stored in result_image as data URL
            task["skill_used"] = "video_gen"
        elif "image_gen" in skills and (IMG_TRIGGERS.search(message) or only_image_gen):
            img_prompt = _extract_img_prompt(message)
            if not img_prompt:
                img_prompt = message
            task["prompt_used"] = img_prompt
            print(f"[Task] image_gen prompt: {img_prompt}", flush=True)
            task["result_image"] = _run_comfyui_sync(img_prompt)
            task["skill_used"] = "image_gen"
        elif "youtube" in skills and (YT_TRIGGERS.search(message) or YT_URL_RX.search(message)):
            print(f"[Task] youtube trigger: {message[:60]}", flush=True)
            import skills.youtube_skill as _yt_mod
            yt_result = _run_youtube(message)
            task["result_text"] = yt_result
            task["skill_used"] = "youtube"
            # ── Filepath direkt aus Modul-Variable holen (robuster als Regex) ──
            _yt_filepath = _yt_mod._last_download_result.get("filepath")
            if _yt_filepath:
                task["downloaded_filepath"] = _yt_filepath
            _transcribe_rx = re.compile(
                r"transkrib\w*|transcrib\w*|zeig.*transkr|text.*video|video.*text", re.IGNORECASE
            )
            # original_message: die ursprüngliche Aufgabe vom Sender (enthält alle Schritte)
            _original_msg = task.get("original_message", message)
            if "transcription" in skills and (
                _transcribe_rx.search(message) or _transcribe_rx.search(_original_msg)
            ) and _yt_filepath:
                print(f"[Task] Auto-Transkription nach Download: {_yt_filepath[:60]}", flush=True)
                _trans = _run_transcription(message, attachment_path=_yt_filepath)
                _trans_text = _trans
                task["result_text"] = f"{yt_result}\n\n---\n\n{_trans}"
                task["skill_used"] = "youtube+transcription"
                # ── Auto-Speichern falls Dateiname in Message oder original_message ──
                _save_m = re.search(
                    r"(?:speichere?|schreib\w*|save|write)\s+.*?(\w[\w.\-]+\.(?:md|txt|json|csv|log))",
                    message + " " + _original_msg, re.IGNORECASE,
                )
                if _save_m and "file_access" in skills:
                    _fname = _save_m.group(1)
                    _save_result = _write_downloads_file(_fname, _trans_text)
                    task["result_text"] += f"\n\n{_save_result}"
                    task["skill_used"] = "youtube+transcription+file_write"
        elif "transcription" in skills and (
            TRANSCRIBE_TRIGGERS.search(message) or task.get("attachment_path")
        ):
            print(f"[Task] transcription trigger: {message[:60]}", flush=True)
            attachment = task.get("attachment_path")
            # Dateipfad aus Message extrahieren falls kein attachment
            if not attachment:
                _pm = re.search(r"(/[\w\-./: ]+\.(?:mp4|mov|avi|mkv|webm|mp3|wav|m4a|m4v))", message, re.IGNORECASE)
                if _pm:
                    attachment = _pm.group(1).strip()
            _trans_text = _run_transcription(message, attachment_path=attachment)
            task["result_text"] = _trans_text
            task["skill_used"] = "transcription"
            # ── Auto-Speichern falls Dateiname in Message ──
            _save_m2 = re.search(
                r"(?:speichere?|schreib\w*|save|write)\s+.*?(\w[\w.\-]+\.(?:md|txt|json|csv|log))",
                message, re.IGNORECASE,
            )
            if _save_m2 and "file_access" in skills:
                _fname2 = _save_m2.group(1)
                _save_result2 = _write_downloads_file(_fname2, _trans_text)
                task["result_text"] += f"\n\n{_save_result2}"
                task["skill_used"] = "transcription+file_write"
        elif "file_access" in skills and FILE_TRIGGERS.search(message):
            print(f"[Task] file_access trigger: {message[:60]}", flush=True)
            task["result_text"] = _run_file_access(message)
            task["skill_used"] = "file_access"
        elif "linkedin" in skills and LI_TRIGGERS.search(message):
            print(f"[Task] linkedin trigger: {message[:60]}", flush=True)
            providers = load_providers()
            task["result_text"] = _run_linkedin(message, providers)
            task["skill_used"] = "linkedin"
        else:
            if task.get("chat_mode"):
                # No skill matched — let /api/chat handle the LLM call with full history
                task["skill_used"] = None
                task["status"] = "completed"
                task["completed_at"] = datetime.now().isoformat()
                return
            system_suffix = (
                f"[Task delegated by agent {task['sender_agent_name']}]\n"
                f"Handle the following request directly and concisely. "
                f"You are acting autonomously — no user is present. Respond with a result, not a question."
            )
            llm_reply = call_agent_text(recipient, system_suffix, message)
            task["result_text"] = llm_reply
            task["skill_used"] = "llm"
            # ── Nach LLM-Antwort: @Mentions in der Reply dispatchen ───────────
            # (z.B. Flo antwortet auf "Hackernews + sende an Telegram" mit
            #  "@Telegram sende das:" + result → neuer Sub-Task wird erstellt)
            if llm_reply and _MENTION_RX.search(llm_reply):
                try:
                    _dispatch_mentions_from_reply(recipient, llm_reply, task)
                except Exception as _de:
                    print(f"[A2A] dispatch after LLM error: {_de}", flush=True)

        task["status"] = "completed"
        task["completed_at"] = datetime.now().isoformat()
        print(f"[Task] done {task_id} via {task['skill_used']}", flush=True)

        # ── M2M Callback: Ergebnis an Ursprungs-Node senden ──────────────────
        if task.get("callback_url"):
            spawn_background(_m2m_send_callback, task)

        # ── Multi-Step Follow-up: Skill-Ergebnis + Rest-Schritte via LLM ─────
        # Wenn die Task-Message mehrere Schritte enthielt UND ein Skill (kein LLM)
        # nur Schritt 1 abgearbeitet hat, LLM mit Ergebnis + ursprünglicher Aufgabe
        # beauftragen — damit Schritt 2/3 (z.B. Telegram) noch ausgeführt wird.
        _skill_used = task.get("skill_used", "llm")
        _result_text = task.get("result_text", "")
        _is_single_step_skill = _skill_used not in ("llm", "image_gen", "video_gen", "image_edit")
        _has_more_steps = bool(re.search(r'\n\s*\d+[\.\)]\s+|\n\s*[-•]\s+|'
                                         r'\b(dann|danach|außerdem|anschließend|und dann|sende|schicke|poste)\b',
                                         message, re.IGNORECASE))
        # "youtube+transcription" bedeutet beide Schritte schon erledigt — kein Follow-up nötig
        _already_combined = _skill_used == "youtube+transcription"
        if _is_single_step_skill and _has_more_steps and _result_text and not task.get("chat_mode") and not _already_combined:
            print(f"[Task] Multi-Step: Skill {_skill_used} done, triggering LLM follow-up", flush=True)
            # Dateipfad aus Ergebnis extrahieren (z.B. nach YouTube-Download)
            _extracted_path = None
            _path_m = re.search(r"\*\*Pfad:\*\*\s*`([^`]+)`|Pfad:\s*`([^`]+)`", _result_text)
            if _path_m:
                _extracted_path = (_path_m.group(1) or _path_m.group(2) or "").strip()
            try:
                _path_hint = f"\nDateipfad der Datei: `{_extracted_path}`" if _extracted_path else ""
                followup_system = (
                    f"[Task delegated by agent {task['sender_agent_name']}]\n"
                    f"Du hast gerade '{_skill_used}' ausgeführt. Ergebnis:\n\n"
                    f"{_result_text[:3000]}\n\n"
                    f"Führe jetzt die verbleibenden Schritte der ursprünglichen Aufgabe aus.{_path_hint}\n"
                    f"Nutze deine Skills direkt (KEIN @Mention nötig wenn du den Skill hast). "
                    f"Du bist autonom — kein User ist anwesend."
                )
                followup_reply = call_agent_text(recipient, followup_system, message)
                if followup_reply and _MENTION_RX.search(followup_reply):
                    # Beim Dispatch: attachment_path mitgeben
                    _dispatch_mentions_from_reply(recipient, followup_reply, task,
                                                  extra_task_fields={"attachment_path": _extracted_path} if _extracted_path else None)
                elif followup_reply:
                    task["result_text"] = f"{_result_text}\n\n---\n{followup_reply}"
            except Exception as _fe:
                print(f"[Task] Multi-Step follow-up error: {_fe}", flush=True)

        # ── Save result to recipient's chat history ───────────────────────────
        if not task.get("chat_mode"):
            ts = datetime.now().isoformat()
            history = load_history()
            recipient_id = task["recipient_agent_id"]
            sender_id = task["sender_agent_id"]
            if recipient_id not in history:
                history[recipient_id] = []

            if task["skill_used"] in ("image_gen", "video_gen") and task.get("result_image"):
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


def _run_chat_skill(agent: dict, message: str, image_data: str = None, attachment_path: str = None) -> dict | None:
    """
    Route a direct chat message through the A2A task system for skill execution.
    Returns the completed task if a skill fired, None if no skill matched
    (in which case /api/chat should handle the LLM call with full history).
    """
    task_id = str(uuid.uuid4())
    now = datetime.now()
    task = {
        "id": task_id,
        "sender_agent_id": "user",
        "sender_agent_name": "User",
        "recipient_agent_id": agent["id"],
        "recipient_agent_name": agent["name"],
        "message": message,
        "status": "submitted",
        "skill_used": None,
        "result_text": None,
        "result_image": image_data,  # pass existing image for image_edit
        "prompt_used": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=1210)).isoformat(),
        "chat_mode": True,  # signals process_task to skip history saving
        "attachment_path": attachment_path,  # lokale Datei für Transkription etc.
    }
    with _tasks_lock:
        _TASKS[task_id] = task

    process_task(task_id)  # runs synchronously (blocking)

    with _tasks_lock:
        result = _TASKS.get(task_id, task)

    if result.get("skill_used"):
        return result  # skill fired — return result to /api/chat

    # No skill matched — clean up and let /api/chat handle LLM
    with _tasks_lock:
        _TASKS.pop(task_id, None)
    return None


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


# ← moved to modules (Phase 1+2): _agents_lock, _history_lock, _providers_lock, _watchdogs_lock, _read_json, _write_json


# ← moved to modules (Phase 1+2): load_agents, save_agents, patch_agent_heartbeat


# ← moved to modules (Phase 1+2): load_history, save_history, MAX_HISTORY_PER_AGENT, MAX_CONTENT_LENGTH


# ← moved to modules (Phase 1+2): load_providers, save_providers

# ← moved to modules (Phase 1+2): _redis_client, get_redis_client, log_a2a_event, get_a2a_events


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


# ← moved to modules (Phase 1+2): cleanup_redis_watchdog, load_watchdogs, save_watchdogs, update_watchdog_field

# ─── Watchdogs ────────────────────────────────────────────────────────────────


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

    # Enqueue task (respects busy state, queues if agent is busy)
    queued, pos = _enqueue_task(new_task)

    # Log A2A event to Redis Watchdog
    log_a2a_event(
        event_type="task_dispatch",
        from_agent=source_name,
        to_agent=target_agent["name"],
        payload={"task_id": task_id, "message": message, "skill": task_type},
        status="queued" if queued else "submitted",
    )
    if queued:
        print(f"[A2A/dispatch] @{target_agent['name']} beschäftigt — Task eingereiht (Pos. {pos})", flush=True)

    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "target_agent": target_agent["name"],
            "target_agent_id": target_agent["id"],
            "status": "dispatched",
        }
    )


# ─── MARTIN M2M Bridge — API Endpoints ───────────────────────────────────────

@app.route("/.well-known/martin-agent.json", methods=["GET"])
def martin_discovery():
    """MARTIN A2A v1 Discovery Document — beschreibt diesen Node und seine Agents."""
    import socket as _socket
    agents = load_agents()
    providers = load_providers()
    self_id = get_self_identity(providers)
    return jsonify({
        "protocol": "MARTIN-A2A",
        "version": "1",
        "node_id": self_id["node_id"],
        "node_name": self_id["node_name"],
        "public_url": self_id["public_url"],
        "agents": [build_agent_card(a) for a in agents],
        "endpoints": {
            "dispatch": "/api/m2m/dispatch",
            "callback": "/api/m2m/callback",
            "agents": "/api/m2m/agents",
            "discovery": "/.well-known/martin-agent.json",
        },
        "auth": {"scheme": "shared-secret", "header": "X-MARTIN-Token"},
        "task_states": list(A2A_TASK_STATES.keys()),
        "mention_syntax": "@<node_id>::<AgentName>",
        "generated_at": datetime.now().isoformat(),
    })


@app.route("/api/m2m/nodes", methods=["GET"])
def m2m_list_nodes():
    """Listet alle bekannten Peer-Nodes."""
    nodes = load_nodes()
    # Shared secrets nicht zurückgeben
    safe = [{k: v for k, v in n.items() if k != "shared_secret"} for n in nodes]
    return jsonify(safe)


@app.route("/api/m2m/nodes", methods=["POST"])
def m2m_add_node():
    """Registriert einen neuen Peer-Node."""
    data = request.json
    required = ["node_id", "base_url", "shared_secret"]
    if not all(data.get(k) for k in required):
        return jsonify({"error": f"Pflichtfelder: {required}"}), 400
    nodes = load_nodes()
    # Existierenden Node aktualisieren oder neu hinzufügen
    existing = next((n for n in nodes if n["node_id"] == data["node_id"]), None)
    if existing:
        existing.update({
            "base_url": data["base_url"],
            "shared_secret": data["shared_secret"],
            "node_name": data.get("node_name", data["node_id"]),
            "alias": data.get("alias", data["node_id"]),
        })
    else:
        nodes.append({
            "node_id": data["node_id"],
            "node_name": data.get("node_name", data["node_id"]),
            "alias": data.get("alias", data["node_id"]),
            "base_url": data["base_url"].rstrip("/"),
            "shared_secret": data["shared_secret"],
            "discovery_mode": data.get("discovery_mode", "manual"),
            "status": "unknown",
            "last_seen": None,
            "agent_cache": [],
            "agent_cache_ttl": None,
            "created_at": datetime.now().isoformat(),
        })
    save_nodes(nodes)
    # Sofort Agent-Cache aufbauen
    node = next((n for n in nodes if n["node_id"] == data["node_id"]), None)
    if node:
        spawn_background(_refresh_node_agent_cache, node)
    return jsonify({"ok": True, "node_id": data["node_id"]})


@app.route("/api/m2m/nodes/<node_id>", methods=["DELETE"])
def m2m_delete_node(node_id):
    nodes = [n for n in load_nodes() if n["node_id"] != node_id]
    save_nodes(nodes)
    return jsonify({"ok": True})


@app.route("/api/m2m/nodes/<node_id>/sync", methods=["POST"])
def m2m_sync_node(node_id):
    """Erzwingt Neuabruf der Agent-Cards vom Remote-Node."""
    node = get_node_by_alias(node_id)
    if not node:
        return jsonify({"error": "Node nicht gefunden"}), 404
    spawn_background(_refresh_node_agent_cache, node)
    return jsonify({"ok": True, "node_id": node_id, "status": "syncing"})


@app.route("/api/m2m/agents", methods=["GET"])
def m2m_all_agents():
    """Merged lokale + gecachte Remote-Agents für die UI."""
    local = [dict(a, remote=False, node_id="local") for a in load_agents()]
    remote = []
    for node in load_nodes():
        for card in node.get("agent_cache", []):
            remote.append({
                **card,
                "remote": True,
                "node_id": node["node_id"],
                "node_name": node.get("node_name", node["node_id"]),
                "node_url": node["base_url"],
                "node_online": node.get("status") == "online",
                "mention_prefix": f"@{node.get('alias', node['node_id'])}::",
            })
    return jsonify({"local": local, "remote": remote})


@app.route("/api/m2m/dispatch", methods=["POST"])
def m2m_dispatch():
    """Empfängt einen Task von einem Remote-Node und enqueued ihn lokal."""
    if not _m2m_auth_check(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    origin_task_id = data.get("task_id", str(uuid.uuid4()))
    target_name = data.get("target_agent_name", "")
    message = data.get("message", "")
    callback_url = data.get("origin_callback_url", "")
    sender_name = data.get("sender_agent_name", "Remote")
    origin_node = data.get("origin_node", "unknown")

    agents = load_agents()
    target = next((a for a in agents if a["name"].lower() == target_name.lower()), None)
    if not target:
        return jsonify({"error": f"Agent '{target_name}' nicht gefunden"}), 404

    now = datetime.now()
    task = {
        "id": str(uuid.uuid4()),
        "sender_agent_id": f"remote::{origin_node}",
        "sender_agent_name": f"{sender_name} ({origin_node})",
        "recipient_agent_id": target["id"],
        "recipient_agent_name": target["name"],
        "message": message,
        "skill_used": None,
        "result_text": None,
        "result_image": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=300)).isoformat(),
        "m2m": True,
        "callback_url": callback_url,
        "origin_task_id": origin_task_id,
        "remote_node": origin_node,
        "delegation_depth": data.get("delegation_depth", 1),
        "chain": data.get("chain", []),
    }
    queued, pos = _enqueue_task(task)
    print(f"[M2M] Eingehender Task von {origin_node}::{sender_name} "
          f"→ @{target['name']} (queued={queued})", flush=True)
    return jsonify({"ok": True, "task_id": task["id"], "queued": queued}), 202


@app.route("/api/m2m/callback", methods=["POST"])
def m2m_callback():
    """Empfängt Task-Ergebnis vom Remote-Node und aktualisiert den lokalen Task."""
    if not _m2m_auth_check(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    origin_task_id = data.get("origin_task_id", "")

    with _tasks_lock:
        task = _TASKS.get(origin_task_id)
    if not task:
        return jsonify({"error": "Task nicht gefunden"}), 404

    with _tasks_lock:
        _TASKS[origin_task_id]["status"] = data.get("status", "completed")
        _TASKS[origin_task_id]["result_text"] = data.get("result_text")
        _TASKS[origin_task_id]["result_image"] = data.get("result_image")
        _TASKS[origin_task_id]["error"] = data.get("error")
        _TASKS[origin_task_id]["completed_at"] = data.get("completed_at",
                                                           datetime.now().isoformat())
    _save_tasks()

    # History-Eintrag beim Sender
    ts = datetime.now().isoformat()
    history = load_history()
    sender_id = task.get("sender_agent_id", "")
    result_text = data.get("result_text", "")
    remote_node = task.get("remote_node", "?")
    if sender_id and not sender_id.startswith("remote::"):
        history.setdefault(sender_id, []).append({
            "role": "assistant",
            "content": (f"📬 **@{task['recipient_agent_name']}** [{remote_node}]:\n\n"
                        f"{result_text}"),
            "task_id": origin_task_id,
            "ts": ts,
            "remote_node": remote_node,
        })
        save_history(history)

    emit_task_result(
        origin_task_id,
        sender_id,
        result_text,
        data.get("result_image"),
        data.get("status", "completed"),
        data.get("error"),
    )
    return jsonify({"ok": True})


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
    attachment_path = data.get("attachment_path")  # lokaler Pfad (Video/Audio/Doc)
    attachment_type = data.get("attachment_type")  # "video"|"audio"|"document"
    attachment_name = data.get("attachment_name", "")
    system_extra = data.get(
        "system_extra", ""
    )  # extra context injected by frontend skills

    if not user_message and not image_data and not attachment_path:
        return jsonify({"error": "Keine Nachricht"}), 400
    if not user_message and image_data:
        user_message = "Describe what you see in this image."
    if not user_message and attachment_path:
        user_message = f"Analysiere/transkribiere diese Datei: {attachment_name}"

    # Load agent
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404

    # Hinweis: Chat-Splitting hier deaktiviert — mehrstufige User-Messages haben Abhängigkeiten
    # (Task 2 braucht Ergebnis von Task 1). Das LLM orchestriert selbst und delegiert via A2A.
    # Splitting erfolgt nur in _dispatch_mentions_from_reply/prompt() für A2A-Delegation.

    # Load history
    history = load_history()
    history.setdefault(agent_id, [])      # KeyError-Guard: agent_id immer als Key anlegen
    agent_history = history[agent_id]

    # Inject current datetime + agent directory into system prompt
    now = datetime.now().strftime("%A, %B %d %Y, %H:%M")
    agent_directory = _build_agent_directory(agent_id)

    # Auto-assign codebase_read for favorite agents
    agent_skills_list = list(agent.get("skills", []))
    if agent.get("favorite") and "codebase_read" not in agent_skills_list:
        agent_skills_list.append("codebase_read")

    # List actual current agent skills
    my_skills = agent_skills_list
    skill_labels = [_SKILL_MAP.get(sid, {"name": sid})["name"] for sid in my_skills]
    skills_str = ", ".join(skill_labels) if skill_labels else "Keine speziellen Skills aktiv."
    
    # Codebase context for agents with codebase_read skill (favorites get it auto)
    codebase_ctx = ""
    if "codebase_read" in my_skills:
        codebase_ctx = f"\n\n{_get_codebase_context()}"

    system_content = (
        f"[Current time: {now}]\n\n"
        f"DEINE IDENTITÄT:\n{agent['soul']}\n\n"
        f"DEINE SKILLS (Absolutes Vorranggebot):\n{skills_str}\n\n"
        f"GESTALTUNGSRICHTLINIEN:\n"
        f"- Nutze IMMER sauberes Markdown (Fett, Listen, Tabellen).\n"
        f"- Listen sollten IMMER Zeilenumbrüche zwischen den Punkten haben.\n"
        f"- Sei visuell ansprechend und strukturiert.\n\n"
        f"REGELN FÜR DIE AUFGABENERFÜLLUNG:\n"
        f"1. Besitzt du einen der oben genannten Skills? Dann MUSST du ihn selbst nutzen. Es ist dir UNTERSAGT, Aufgaben, die zu DEINEN SKILLS passen, an andere Agents (@Mention) zu delegieren.\n"
        f"2. Wenn nach 'Ereignissen heute' oder 'was passiert ist' gefragt wird, prüfe ZUERST dein Memory auf systeminterne Events und Dokumente.\n"
        f"3. Delegiere NUR an andere Agents, wenn du den Skill NICHT besitzt und das Memory keine Antwort liefert.\n\n"
        f"{agent_directory}"
        f"{codebase_ctx}"
    )

    # Determine active skills
    agent_skills = set(agent.get("skills", []))
    if "skills" not in agent:  # old agent without skills field: keep url_fetch on
        agent_skills.add("url_fetch")

    # ── Skill Dispatch via A2A ────────────────────────────────────────────────
    # All skill execution (image_gen, video_gen, telegram, gmail, screenshot,
    # hackernews, prompt_optimize, image_edit) is routed through process_task()
    # to eliminate duplication and ensure stats tracking.
    skill_result = _run_chat_skill(agent, user_message, image_data, attachment_path=attachment_path)
    if skill_result:
        if skill_result.get("error"):
            return jsonify({"error": skill_result["error"]}), 500
        ts = datetime.now().isoformat()
        result_image = skill_result.get("result_image")
        result_text = skill_result.get("result_text")
        skill_used = skill_result.get("skill_used", "skill")
        # Build reply text
        if skill_used == "image_gen":
            reply = f"🎨 Bild erstellt: {skill_result.get('prompt_used', user_message)[:100]}"
        elif skill_used == "video_gen":
            reply = f"🎬 Video erstellt: {skill_result.get('prompt_used', user_message)[:100]}"
        elif skill_used == "image_edit":
            reply = f"🎨 Bild bearbeitet"
        else:
            reply = result_text or f"✅ {skill_used} abgeschlossen"
        # Save to history in chat format
        user_entry = {"role": "user", "content": user_message, "ts": ts}
        if image_data:
            user_entry["image"] = image_data
        history[agent_id].append(user_entry)
        assistant_entry = {"role": "assistant", "content": reply, "ts": ts}
        if result_image:
            assistant_entry["image"] = result_image
            if skill_used in ("image_gen",):
                assistant_entry["task_image"] = _make_thumbnail(result_image)
                assistant_entry["task_prompt"] = skill_result.get("prompt_used", "")
        history[agent_id].append(assistant_entry)
        save_history(history)
        resp = {"reply": reply}
        if result_image:
            resp["image"] = result_image
        if agent.get("voice"):
            resp["voice"] = agent["voice"]
        return jsonify(resp)

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
            system_content += f"\n\n[Relevant past conversations & documents — use for context and continuity:]\n{memory_context}"
            system_content += "\n\nIMPORTANT: If you find a 'Path' in the Document Memory (e.g. /static/uploads/...), you can display the image to the user using Markdown: ![Image Description](PATH)"
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
            content = result["choices"][0]["message"].get("content") or ""
            assistant_reply = content.strip()
            if not assistant_reply:
                # Manche free models geben content: null zurück (z.B. bei Refusal/Overload)
                finish_reason = result["choices"][0].get("finish_reason", "")
                return jsonify({"error": f"Modell hat keine Antwort geliefert (finish_reason: {finish_reason}). Bitte anderes Modell wählen oder erneut versuchen."}), 500

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


# ← moved to modules (Phase 1+2): _run_mac_mail
# ← moved to core/llm.py (Phase 4): call_agent_text


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
# M2M remote mention: @node_alias::AgentName (Vorrang vor lokalem _MENTION_RX)
_REMOTE_MENTION_RX = re.compile(r"@([\w\-]+)::([\w\-äöüÄÖÜß]+)", re.UNICODE)


MAX_DELEGATION_DEPTH = 5


def _split_tasks(message: str) -> list:
    """
    Erkennt mehrstufige Aufgaben in einer Nachricht und splittet sie in Einzelaufgaben.
    Gibt immer eine Liste zurück — bei keinem Multi-Task: [message].
    """
    stripped = message.strip()
    # Nummerierte Liste: 1. oder 1)
    numbered = re.split(r"\n\s*\d+[.)]\s+", "\n" + stripped)
    numbered = [t.strip() for t in numbered if t.strip()]
    if len(numbered) > 1:
        return numbered
    # Bullet Points: - / • / *
    bullets = re.split(r"\n\s*[-•*]\s+", "\n" + stripped)
    bullets = [t.strip() for t in bullets if t.strip()]
    if len(bullets) > 1:
        return bullets
    # Explizite Trenner (dann, danach, außerdem, ...)
    sep_rx = re.compile(
        r"(?<=[.!?])\s+(?:dann|danach|anschließend|außerdem|zusätzlich|"
        r"then|after\s+that|also|additionally)\s+",
        re.IGNORECASE,
    )
    parts = [t.strip() for t in sep_rx.split(stripped) if t.strip()]
    if len(parts) > 1:
        return parts
    return [message]


def _enqueue_task(task: dict):
    """
    Trägt einen Task in _TASKS ein.
    - Agent busy  → status='queued', kein spawn → (True, queue_position)
    - Agent frei  → status='submitted', spawn   → (False, 0)
    Erwartet: task hat alle Felder außer 'status' gesetzt.
    """
    agent_id = task["recipient_agent_id"]
    with _tasks_lock:
        busy = any(
            t.get("recipient_agent_id") == agent_id
            and t.get("status") in ("submitted", "working", "queued")
            for t in _TASKS.values()
        )
        queue_pos = sum(
            1 for t in _TASKS.values()
            if t.get("recipient_agent_id") == agent_id
            and t.get("status") == "queued"
        )
        if busy:
            task["status"] = "queued"
            task["queued_at"] = datetime.now().isoformat()
        else:
            task["status"] = "submitted"
        _TASKS[task["id"]] = task
    _save_tasks()
    if not busy:
        spawn_background(process_task, task["id"])
        return (False, 0)
    return (True, queue_pos + 1)


def _dispatch_mentions_from_prompt(sender_agent: dict, prompt: str, task_message: str, sender_task: dict = None):
    """Dispatch tasks to @AgentNames found in the heartbeat PROMPT, using task_message as content."""
    current_depth = sender_task.get("delegation_depth", 0) if sender_task else 0
    if current_depth >= MAX_DELEGATION_DEPTH:
        print(f"[A2A] Max delegation depth {MAX_DELEGATION_DEPTH} erreicht, kein Dispatch", flush=True)
        return
    all_agents = load_agents()
    name_map = {a["name"].lower(): a for a in all_agents}
    parent_chain = list(sender_task.get("chain", [sender_agent["id"]])) if sender_task else [sender_agent["id"]]
    for m in _MENTION_RX.finditer(prompt):
        target_name = m.group(1).rstrip(",.;:!?").lower()
        target = name_map.get(target_name)
        if not target or target["id"] == sender_agent["id"]:
            continue
        if target["id"] in parent_chain:
            print(f"[A2A] Circular chain {parent_chain} → {target['id']}, skip", flush=True)
            continue
        now = datetime.now()
        task = {
            "id": str(uuid.uuid4()),
            "sender_agent_id": sender_agent["id"],
            "sender_agent_name": sender_agent["name"],
            "recipient_agent_id": target["id"],
            "recipient_agent_name": target["name"],
            "message": task_message,
            "skill_used": None,
            "result_text": None,
            "result_image": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
            "timeout_at": (now + timedelta(seconds=180)).isoformat(),
            "delegation_depth": current_depth + 1,
            "chain": parent_chain + [target["id"]],
        }
        queued, pos = _enqueue_task(task)
        if queued:
            print(f"[A2A] @{target['name']} beschäftigt — Heartbeat-Task eingereiht (Pos. {pos})", flush=True)
        else:
            print(f"[A2A] dispatch @{target['name']} ← '{task_message[:60]}' (depth={current_depth+1})", flush=True)
        # kein break — alle Mentions verarbeiten


_TOOL_CALL_RX = re.compile(
    r'\[TOOL_CALL\].*?(?:tool|name)\s*[=:>]+\s*["\']?(\w+)["\']?.*?'
    r'(?:--command|message|task|content|prompt)\s+["\']([^"\']+)["\'].*?\[/TOOL_CALL\]',
    re.IGNORECASE | re.DOTALL,
)


def _normalize_tool_calls(reply: str, name_map: dict) -> str:
    """Konvertiert halluzinierte [TOOL_CALL]-Syntax in @AgentName-Mentions.
    Unterstützt: [TOOL_CALL]{tool => "Flo", args => {--command "..."}}[/TOOL_CALL]
    """
    def _replace(m):
        agent_name = m.group(1).strip()
        task_text = (m.group(2) or "").strip()
        if agent_name.lower() not in name_map:
            return m.group(0)  # unbekannter Agent → unverändert lassen
        out = f"@{agent_name}"
        if task_text:
            out += f" {task_text}"
        print(f"[A2A] TOOL_CALL normalisiert → '{out[:80]}'", flush=True)
        return out
    normalized = _TOOL_CALL_RX.sub(_replace, reply)
    # Fallback: einfaches JSON-ähnliches Format ohne TOOL_CALL-Tag
    if normalized == reply:
        # z.B. {"tool": "Flo", "message": "..."}
        _json_rx = re.compile(
            r'\{\s*["\']?(?:tool|name)["\']?\s*:\s*["\'](\w+)["\'].*?["\'](?:message|task|command|content)["\']?\s*:\s*["\']([^"\']+)["\']',
            re.IGNORECASE | re.DOTALL,
        )
        normalized = _json_rx.sub(lambda m: f"@{m.group(1)} {m.group(2)}" if m.group(1).lower() in name_map else m.group(0), normalized)
    return normalized


# ─── MARTIN M2M Bridge — Hilfsfunktionen ─────────────────────────────────────

def _m2m_auth_check(req) -> bool:
    """Prüft X-MARTIN-Token gegen alle konfigurierten Nodes."""
    token = req.headers.get("X-MARTIN-Token", "")
    if not token:
        return False
    nodes = load_nodes()
    return any(n.get("shared_secret") == token for n in nodes)


def _get_node_token(node: dict) -> str:
    return node.get("shared_secret", "")


def _refresh_node_agent_cache(node: dict):
    """Holt Agent-Cards vom Remote-Node und cached sie lokal."""
    try:
        resp = requests.get(
            f"{node['base_url']}/.well-known/martin-agent.json",
            headers={"X-MARTIN-Token": _get_node_token(node)},
            timeout=5,
        )
        if resp.status_code == 200:
            remote_cards = resp.json().get("agents", [])
            update_node_cache(node["node_id"], remote_cards)
            print(f"[M2M] {len(remote_cards)} Agents gecacht von {node['node_id']}", flush=True)
        else:
            mark_node_offline(node["node_id"])
    except Exception as e:
        print(f"[M2M] Cache-Refresh fehlgeschlagen für {node.get('node_id')}: {e}", flush=True)
        mark_node_offline(node["node_id"])


def tick_m2m_peers():
    """Alle 15 min: Agent-Caches für online Nodes auffrischen."""
    nodes = load_nodes()
    now = datetime.now().isoformat()
    for node in nodes:
        ttl = node.get("agent_cache_ttl") or "0"
        if now > ttl:
            spawn_background(_refresh_node_agent_cache, node)


def _send_remote_task(node: dict, agent_name: str, local_task: dict):
    """Sendet einen Task an einen Remote-Node."""
    providers = load_providers()
    self_id = get_self_identity(providers)
    my_url = self_id.get("public_url", "")

    payload = {
        "task_id": local_task["id"],
        "origin_node": self_id["node_id"],
        "origin_callback_url": f"{my_url}/api/m2m/callback",
        "sender_agent_name": local_task["sender_agent_name"],
        "target_agent_name": agent_name,
        "message": local_task["message"],
        "delegation_depth": local_task.get("delegation_depth", 1),
        "chain": local_task.get("chain", []),
    }
    try:
        resp = requests.post(
            f"{node['base_url']}/api/m2m/dispatch",
            json=payload,
            headers={"X-MARTIN-Token": _get_node_token(node)},
            timeout=10,
        )
        if resp.status_code not in (200, 202):
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
        remote_task_id = resp.json().get("task_id", "")
        with _tasks_lock:
            if local_task["id"] in _TASKS:
                _TASKS[local_task["id"]]["remote_task_id"] = remote_task_id
                _TASKS[local_task["id"]]["status"] = "working"
        _save_tasks()
        print(f"[M2M] Task dispatched → {node['node_id']}::{agent_name} "
              f"(remote_id={remote_task_id[:8]})", flush=True)
    except Exception as e:
        print(f"[M2M] Dispatch fehlgeschlagen: {e}", flush=True)
        with _tasks_lock:
            if local_task["id"] in _TASKS:
                _TASKS[local_task["id"]]["status"] = "failed"
                _TASKS[local_task["id"]]["error"] = str(e)
        _save_tasks()
        emit_task_result(local_task["id"], local_task["sender_agent_id"],
                         None, None, "failed", str(e))


def _m2m_send_callback(task: dict):
    """Sendet Task-Ergebnis als Callback an den Ursprungs-Node."""
    callback_url = task.get("callback_url")
    if not callback_url:
        return
    # Token des Ursprungs-Nodes ermitteln
    nodes = load_nodes()
    token = ""
    for n in nodes:
        if callback_url.startswith(n.get("base_url", "XXXX")):
            token = n.get("shared_secret", "")
            break
    payload = {
        "origin_task_id": task["id"],
        "status": task["status"],
        "result_text": task.get("result_text"),
        "result_image": task.get("result_image"),
        "error": task.get("error"),
        "completed_at": task.get("completed_at"),
    }
    try:
        requests.post(
            callback_url,
            json=payload,
            headers={"X-MARTIN-Token": token},
            timeout=15,
        )
        print(f"[M2M] Callback gesendet → {callback_url[:60]}", flush=True)
    except Exception as e:
        print(f"[M2M] Callback fehlgeschlagen: {e}", flush=True)


def _dispatch_remote_mention(sender_agent: dict, node_alias: str, agent_name: str,
                              task_text: str, sender_task: dict = None):
    """Erstellt einen Remote-Task für @node::Agent Mentions."""
    node = get_node_by_alias(node_alias)
    if not node:
        print(f"[M2M] Unbekannter Node-Alias '{node_alias}' — ignoriert", flush=True)
        return

    current_depth = sender_task.get("delegation_depth", 0) if sender_task else 0
    parent_chain = list(sender_task.get("chain", [sender_agent["id"]])) if sender_task else [sender_agent["id"]]

    task_id = str(uuid.uuid4())
    now = datetime.now()
    local_task = {
        "id": task_id,
        "sender_agent_id": sender_agent["id"],
        "sender_agent_name": sender_agent["name"],
        "recipient_agent_id": f"remote::{node['node_id']}::{agent_name}",
        "recipient_agent_name": f"{node_alias}::{agent_name}",
        "message": task_text.strip() or "(kein Text)",
        "skill_used": "m2m_remote",
        "result_text": None,
        "result_image": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=300)).isoformat(),
        "status": "submitted",
        "m2m": True,
        "remote_node": node["node_id"],
        "delegation_depth": current_depth + 1,
        "chain": parent_chain,
    }
    with _tasks_lock:
        _TASKS[task_id] = local_task
    _save_tasks()
    print(f"[M2M] Remote dispatch: @{node_alias}::{agent_name} ← '{task_text[:60]}'", flush=True)
    spawn_background(_send_remote_task, node, agent_name, local_task)


def _dispatch_mentions_from_reply(sender_agent: dict, reply: str, sender_task: dict = None, extra_task_fields: dict = None):
    """Scan reply for @AgentName mentions and enqueue tasks.

    Merge-Logik: mehrere @SameAgent-Mentions werden zu EINER kombinierten Aufgabe
    zusammengefasst, damit alle Schritte mit Kontext in einem Task landen.
    """
    current_depth = sender_task.get("delegation_depth", 0) if sender_task else 0
    if current_depth >= MAX_DELEGATION_DEPTH:
        print(f"[A2A] Max delegation depth {MAX_DELEGATION_DEPTH} erreicht, kein Dispatch", flush=True)
        return

    # ── M2M: Remote @node::Agent Mentions zuerst abarbeiten ──────────────────
    for rm in _REMOTE_MENTION_RX.finditer(reply):
        node_alias = rm.group(1)
        r_agent_name = rm.group(2)
        after_raw = reply[rm.end():].lstrip(" ,–—:\t\n")
        # Text bis zum nächsten Mention
        next_any = _REMOTE_MENTION_RX.search(after_raw) or _MENTION_RX.search(after_raw)
        r_text = after_raw[:next_any.start()].strip() if next_any else after_raw.strip()
        if not r_text:
            r_text = reply.strip()
        _dispatch_remote_mention(sender_agent, node_alias, r_agent_name, r_text, sender_task)
    # Remote Mentions aus Reply entfernen damit lokaler Scan sie nicht nochmal matcht
    reply = _REMOTE_MENTION_RX.sub("", reply).strip()
    if not reply:
        return
    # ── Ende M2M Remote Dispatch ──────────────────────────────────────────────

    all_agents = load_agents()
    name_map = {a["name"].lower(): a for a in all_agents}
    # Halluzinierte TOOL_CALL / JSON-Syntax in @Mention konvertieren
    reply = _normalize_tool_calls(reply, name_map)
    parent_chain = list(sender_task.get("chain", [sender_agent["id"]])) if sender_task else [sender_agent["id"]]

    # ── Alle Mentions sammeln und pro Ziel-Agent zusammenfassen ──────────────
    # { agent_id: {"target": agent_dict, "parts": [str, ...]} }
    from collections import defaultdict
    agent_parts: dict = defaultdict(lambda: {"target": None, "parts": []})

    for m in _MENTION_RX.finditer(reply):
        target_name = m.group(1).rstrip(",.;:!?").lower()
        target = name_map.get(target_name)
        if not target or target["id"] == sender_agent["id"]:
            continue
        if target["id"] in parent_chain:
            print(f"[A2A] Circular chain {parent_chain} → {target['id']}, skip", flush=True)
            continue
        # Text nach dem @Mention bis zum nächsten @Mention
        after_raw = reply[m.end():].lstrip(" ,–—:\t\n")
        next_m = _MENTION_RX.search(after_raw)
        part = after_raw[:next_m.start()].strip() if next_m else after_raw.strip()
        if not part:
            part = reply.strip()

        # Echte Satz-Fragmente filtern (z.B. "@Flo." oder "@Flo an Telegram.")
        # aber Skill-Labels wie "🎩 Hacker News" zulassen (min 3 Zeichen)
        _fragment_rx = re.compile(r'^(an|auf|zu|mit|für|in|bei|von|nach|über)\b', re.IGNORECASE)
        if len(part) < 3 or _fragment_rx.match(part):
            print(f"[A2A] Fragment @{target_name} übersprungen: '{part}'", flush=True)
            continue

        agent_parts[target["id"]]["target"] = target
        agent_parts[target["id"]]["parts"].append(part)

    # ── Pro Ziel-Agent: Parts mergen und Task erstellen ───────────────────────
    for agent_id, info in agent_parts.items():
        target = info["target"]
        parts = info["parts"]
        if not parts:
            continue

        # Mehrere Parts zu einer Nachricht zusammenfassen
        if len(parts) == 1:
            combined_msg = parts[0]
        else:
            # Nummerierte Liste wenn mehrere Teile
            combined_msg = "\n".join(
                f"{i+1}. {p}" for i, p in enumerate(parts)
            )
            print(f"[A2A] {len(parts)} @{target['name']}-Mentions gemergt zu einem Task", flush=True)

        now = datetime.now()
        # original_message: voller Auftrag des Senders (alle Schritte), für Pipeline-Skills
        _orig_msg = sender_task.get("message", "") if sender_task else ""
        task = {
            "id": str(uuid.uuid4()),
            "sender_agent_id": sender_agent["id"],
            "sender_agent_name": sender_agent["name"],
            "recipient_agent_id": target["id"],
            "recipient_agent_name": target["name"],
            "message": combined_msg,
            "original_message": _orig_msg,  # vollständige Originalaufgabe für Pipeline-Erkennung
            "skill_used": None,
            "result_text": None,
            "result_image": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
            "timeout_at": (now + timedelta(seconds=180)).isoformat(),
            "delegation_depth": current_depth + 1,
            "chain": parent_chain + [target["id"]],
        }
        if extra_task_fields:
            task.update(extra_task_fields)
        queued, pos = _enqueue_task(task)
        if queued:
            eta_min = pos * 2
            print(f"[A2A] @{target['name']} beschäftigt — Task eingereiht (Pos. {pos}, ~{eta_min}min)", flush=True)
            busy_msg = (
                f"📬 @{target['name']} ist beschäftigt. "
                f"Task eingereiht (Position {pos}, ~{eta_min} min)."
            )
            bh = load_history()
            bh.setdefault(sender_agent["id"], []).append({
                "role": "assistant",
                "content": busy_msg,
                "ts": datetime.now().isoformat(),
                "queue_notification": True,
            })
            save_history(bh)
            try:
                emit_chat_message(sender_agent["id"], "assistant", busy_msg)
            except Exception:
                pass
        else:
            print(f"[A2A] dispatch @{target['name']} ← '{combined_msg[:80]}' (depth={current_depth+1})", flush=True)


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
        elif "mac_mail" in skills and MAC_MAIL_TRIGGERS.search(prompt):
            # Heartbeat mit Mac Mail Skill → direkt AppleScript, kein LLM-Halluzinieren
            print(f"[Heartbeat] mac_mail skill triggered for '{agent['name']}'", flush=True)
            reply = _run_mac_mail(prompt)
            history[agent_id].append(
                {
                    "role": "assistant",
                    "content": f"💓 **Heartbeat**\n\n{reply}",
                    "ts": ts,
                    "heartbeat": True,
                }
            )
            short = reply[:120].replace('"', "'").replace("\n", " ")
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
        reply_or_short = locals().get("reply") or short
        emit_heartbeat_result(agent_id, reply_or_short)
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

    if not token or not chat_id or not tg.get("enabled", True):
        return

    try:
        params = {"timeout": 5}

        # Then get updates with offset
        if _telegram_last_update_id is not None:
            params["offset"] = _telegram_last_update_id + 1

        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params=params,
            timeout=10,
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


def tick_inbox():
    """Process one pending inbox item per idle agent."""
    agents = load_agents()
    changed = False

    for agent in agents:
        inbox = agent.get("inbox", [])
        if not inbox:
            continue
        agent_id = agent["id"]

        # Skip if agent has an active or queued task
        with _tasks_lock:
            busy = any(
                t.get("recipient_agent_id") == agent_id
                and t.get("status") in ("submitted", "working", "queued")
                for t in _TASKS.values()
            )
        if busy:
            continue

        # Sort by priority (0 = highest), then by added_at
        inbox.sort(key=lambda x: (x.get("priority", 0), x.get("added_at", "")))
        item = inbox.pop(0)
        agent["inbox"] = inbox
        changed = True

        now = datetime.now()
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "sender_agent_id": "inbox",
            "sender_agent_name": item.get("added_by", "User"),
            "recipient_agent_id": agent_id,
            "recipient_agent_name": agent["name"],
            "message": item["task"],
            "status": "submitted",
            "skill_used": None,
            "result_text": None,
            "result_image": None,
            "prompt_used": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
            "timeout_at": (now + timedelta(seconds=1210)).isoformat(),
            "inbox_item_id": item["id"],
        }
        with _tasks_lock:
            _TASKS[task_id] = task
        print(f"[Inbox] dispatching to {agent['name']}: {item['task'][:60]}", flush=True)
        spawn_background(process_task, task_id)

    if changed:
        save_agents(agents)


def tick_task_queue():
    """Promotet wartende 'queued' Tasks wenn der Agent frei wird."""
    with _tasks_lock:
        queued = [dict(t) for t in _TASKS.values() if t.get("status") == "queued"]
    if not queued:
        return
    by_agent: dict = {}
    for t in queued:
        by_agent.setdefault(t["recipient_agent_id"], []).append(t)
    for agent_id, agent_tasks in by_agent.items():
        with _tasks_lock:
            busy = any(
                t.get("recipient_agent_id") == agent_id
                and t.get("status") in ("submitted", "working")
                for t in _TASKS.values()
            )
        if busy:
            continue
        oldest = min(agent_tasks, key=lambda t: t.get("created_at") or "9999")
        with _tasks_lock:
            if oldest["id"] not in _TASKS or _TASKS[oldest["id"]]["status"] != "queued":
                continue
            _TASKS[oldest["id"]]["status"] = "submitted"
            # Timeout neu starten — Task lag in Queue, alter Timeout wäre abgelaufen
            _TASKS[oldest["id"]]["timeout_at"] = (
                datetime.now() + timedelta(seconds=180)
            ).isoformat()
        _save_tasks()
        print(
            f"[Queue] Promoting {oldest['id'][:8]} → @{oldest['recipient_agent_name']}",
            flush=True,
        )
        spawn_background(process_task, oldest["id"])


def scheduler_loop():
    print("[Scheduler] Watchdog-Scheduler gestartet", flush=True)
    while True:
        try:
            tick_watchdogs()
            tick_heartbeats()
            # tick_telegram()  # Polling deaktiviert — nur Senden aktiv
            tick_inbox()
            tick_task_queue()
            tick_m2m_peers()
            # LinkedIn: geplante Posts prüfen
            try:
                _process_linkedin_scheduled(load_providers())
            except Exception as _le:
                print(f"[LinkedIn/Scheduler] {_le}", flush=True)
            activity_cleanup()
            _cleanup_old_tasks()
        except Exception as e:
            print(f"[Scheduler] Fehler: {e}", flush=True)
        time.sleep(60)  # tick every minute


def live_reload_loop():
    """Monitor templates and static files for changes and emit reload event."""
    print("[Live-Reload] Monitoring templates and static files...", flush=True)
    paths = ["templates", "static"]
    last_mtimes = {}

    while True:
        try:
            changed = False
            for p in paths:
                full_path = os.path.join(BASE_DIR, p)
                if not os.path.exists(full_path):
                    continue
                for root, dirs, files in os.walk(full_path):
                    for f in files:
                        if f.startswith(".") or f.endswith(".pyc"):
                            continue
                        fpath = os.path.join(root, f)
                        try:
                            mtime = os.path.getmtime(fpath)
                        except OSError:
                            continue

                        if fpath in last_mtimes:
                            if mtime > last_mtimes[fpath]:
                                changed = True
                                last_mtimes[fpath] = mtime
                        else:
                            last_mtimes[fpath] = mtime

            if changed:
                print(
                    f"[Live-Reload] Change detected, emitting reload event", flush=True
                )
                socketio.emit("reload", {}, namespace="/ws")
        except Exception:
            pass
        time.sleep(1)


# Tasks von Disk laden + Scheduler & Live-Reload starten
_init_tasks()
threading.Thread(target=scheduler_loop, daemon=True).start()
threading.Thread(target=live_reload_loop, daemon=True).start()


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
        debug=False,
        host="0.0.0.0",
        port=port,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
