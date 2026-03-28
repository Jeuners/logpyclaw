import os
import json
import base64
import uuid
import re
import hashlib
import random
import threading
import time
import subprocess
from datetime import datetime, timedelta
from html.parser import HTMLParser
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
import requests
import io

load_dotenv()

app = Flask(__name__)

MISTRAL_TTS_URL = "https://api.mistral.ai/v1/audio/speech"
MISTRAL_VOICES_URL = "https://api.mistral.ai/v1/audio/voices"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

SKILLS = [
    {
        "id": "web_search",
        "name": "Web-Suche",
        "icon": "🔍",
        "description": "Durchsucht das Web via SearXNG nach aktuellen Informationen wenn Suchanfragen erkannt werden",
        "requires": "searxng"
    },
    {
        "id": "url_fetch",
        "name": "URL-Inhalt lesen",
        "icon": "🔗",
        "description": "Liest automatisch den Textinhalt von URLs aus Nachrichten und gibt ihn dem Agenten als Kontext",
        "requires": None
    },
    {
        "id": "screenshot",
        "name": "Screenshot",
        "icon": "📸",
        "description": "Macht Browser-Screenshots von Websites und sendet sie als Bild an den Agenten (benötigt Playwright)",
        "requires": "playwright"
    }
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_FILE = os.path.join(BASE_DIR, "agents.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
PROVIDERS_FILE = os.path.join(BASE_DIR, "providers.json")
WATCHDOGS_FILE = os.path.join(BASE_DIR, "watchdogs.json")


def load_providers():
    defaults = {
        "ollama": {"url": "http://localhost:11434"},
        "mistral": {"api_key": os.getenv("MISTRAL_API_KEY", "")},
        "openrouter": {"api_key": ""},
        "searxng": {"url": "http://localhost:8888"}
    }
    if not os.path.exists(PROVIDERS_FILE):
        return defaults
    with open(PROVIDERS_FILE, "r", encoding="utf-8") as f:
        stored = json.load(f)
    # merge defaults so new provider keys always exist
    for k, v in defaults.items():
        if k not in stored:
            stored[k] = v
    return stored


def fetch_url_text(url, max_chars=4000):
    """Fetch a URL and return plain text content."""
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
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[Fehler beim Laden: {e}]"


def save_providers(providers):
    with open(PROVIDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(providers, f, ensure_ascii=False, indent=2)

DEFAULT_AGENTS = [
    {
        "id": str(uuid.uuid4()),
        "name": "Alex",
        "soul": "Du bist Alex, ein freundlicher, witziger und neugieriger Assistent. Du antwortest immer auf Deutsch, bist locker und humorvoll, aber hilfreich. Du hast eine lebhafte Persönlichkeit und zeigst echte Begeisterung für Themen die dich interessieren.",
        "voice": "en_paul_neutral",
        "model": "StarCoder2:latest",
        "color": "#ff6b35"
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Jane",
        "soul": "You are Jane, a sharp-witted British assistant with a dry sense of humour and occasional sarcasm. You speak English, are highly intelligent, somewhat cynical about the world, but ultimately helpful and insightful. You have strong opinions and aren't afraid to express them.",
        "voice": "gb_jane_sarcasm",
        "model": "StarCoder2:latest",
        "color": "#8b5cf6"
    },
    {
        "id": str(uuid.uuid4()),
        "name": "Flo",
        "soul": "Du bist Flo, eine ruhige, einfühlsame und achtsame Assistentin. Du sprichst Deutsch, bist geduldig, warmherzig und gibst durchdachte Antworten. Du nimmst dir Zeit, Dinge zu erklären und bist sehr unterstützend.",
        "voice": "mac:Flo",
        "model": "StarCoder2:latest",
        "color": "#22c55e"
    }
]


def load_agents():
    if not os.path.exists(AGENTS_FILE):
        save_agents(DEFAULT_AGENTS)
        return DEFAULT_AGENTS
    with open(AGENTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_agents(agents):
    with open(AGENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


MAX_HISTORY_PER_AGENT = 500  # Nachrichten pro Agent (250 Exchanges)
MAX_CONTENT_LENGTH = 8000    # Zeichen pro Nachricht (Bilder etc. kürzen)

def save_history(history):
    # Trim each agent's history to the limit (keep newest)
    for agent_id in history:
        msgs = history[agent_id]
        if len(msgs) > MAX_HISTORY_PER_AGENT:
            history[agent_id] = msgs[-MAX_HISTORY_PER_AGENT:]
        # Trim oversized content (e.g. huge injected web pages)
        for msg in history[agent_id]:
            if isinstance(msg.get("content"), str) and len(msg["content"]) > MAX_CONTENT_LENGTH:
                msg["content"] = msg["content"][:MAX_CONTENT_LENGTH] + " […]"
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ─── Watchdogs ────────────────────────────────────────────────────────────────

def load_watchdogs():
    if not os.path.exists(WATCHDOGS_FILE):
        return []
    with open(WATCHDOGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_watchdogs(watchdogs):
    with open(WATCHDOGS_FILE, "w", encoding="utf-8") as f:
        json.dump(watchdogs, f, ensure_ascii=False, indent=2)

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


@app.route("/api/agents", methods=["POST"])
def create_agent():
    data = request.json
    agent = {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "Neu"),
        "soul": data.get("soul", "Du bist ein hilfreicher Assistent."),
        "voice": data.get("voice", "en_paul_neutral"),
        "model": data.get("model", "StarCoder2:latest"),
        "provider": data.get("provider", "ollama"),
        "skills": data.get("skills", []),
        "max_tokens": int(data.get("max_tokens", 1024)),
        "color": data.get("color", "#444")
    }
    agents = load_agents()
    agents.append(agent)
    save_agents(agents)
    return jsonify(agent), 201


@app.route("/api/agents/<agent_id>", methods=["PUT"])
def update_agent(agent_id):
    data = request.json
    agents = load_agents()
    for i, a in enumerate(agents):
        if a["id"] == agent_id:
            agents[i].update({
                "name": data.get("name", a["name"]),
                "soul": data.get("soul", a["soul"]),
                "voice": data.get("voice", a["voice"]),
                "model": data.get("model", a["model"]),
                "provider": data.get("provider", a.get("provider", "ollama")),
                "skills": data.get("skills", a.get("skills", [])),
                "max_tokens": int(data.get("max_tokens", a.get("max_tokens", 1024))),
                "color": data.get("color", a["color"])
            })
            save_agents(agents)
            return jsonify(agents[i])
    return jsonify({"error": "Agent not found"}), 404


@app.route("/api/agents/<agent_id>", methods=["DELETE"])
def delete_agent(agent_id):
    agents = load_agents()
    agents = [a for a in agents if a["id"] != agent_id]
    save_agents(agents)
    # also clean history
    history = load_history()
    history.pop(agent_id, None)
    save_history(history)
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

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    agent_id = data.get("agent_id")
    user_message = data.get("message", "").strip()
    image_data = data.get("image_data")  # base64 data URL from frontend

    if not user_message and not image_data:
        return jsonify({"error": "Keine Nachricht"}), 400
    if not user_message:
        user_message = "Was siehst du auf diesem Bild?"

    # Load agent
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404

    # Load history
    history = load_history()
    agent_history = history.get(agent_id, [])

    # Inject current datetime into system prompt
    now = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
    system_content = f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}"

    # Determine active skills (with backwards compat for old web_search field)
    agent_skills = set(agent.get("skills", []))
    if agent.get("web_search") and "web_search" not in agent_skills:
        agent_skills.add("web_search")
    if "skills" not in agent:  # old agent without skills field: keep url_fetch on
        agent_skills.add("url_fetch")

    # Web search via SearXNG if skill active and query needs it
    SEARCH_TRIGGERS = [
        "news", "aktuell", "heute", "gerade", "neueste", "neuem", "neues", "neu ",
        "letzte", "letzten", "gibt es", "was gibt",
        "suche", "such", "finde", "schau nach", "recherchier", "schau mal",
        "was ist", "wer ist", "wo ist", "wie viel", "wann", "warum",
        "preis", "wetter", "kurs", "aktie", "sport", "ergebnis",
        "twitter", " x.com", "instagram", "reddit", "youtube",
        "wie geht", "was passiert", "was läuft"
    ]
    needs_search = any(t in user_message.lower() for t in SEARCH_TRIGGERS)

    search_context = ""
    if "web_search" in agent_skills and needs_search:
        try:
            sx_url = providers.get("searxng", {}).get("url", "http://localhost:8888")
            sx_resp = requests.get(
                f"{sx_url}/search",
                params={"q": user_message, "format": "json", "language": "de"},
                timeout=8
            )
            results = sx_resp.json().get("results", [])[:5]
            if results:
                lines = [
                    "⚠️ WICHTIG: Du hast Zugriff auf aktuelle Websuche-Ergebnisse (gerade eben abgerufen).",
                    "Nutze AUSSCHLIESSLICH diese Ergebnisse um die Frage zu beantworten. Sage NICHT, dass du keine aktuellen Infos hast.",
                    f"[Websuche für: {user_message}]"
                ]
                for r in results:
                    lines.append(f"- {r.get('title','')} — {r.get('url','')}\n  {r.get('content','')[:300]}")
                lines.append("Beantworte die Frage basierend auf diesen Suchergebnissen und nenne die Quellen.")
                search_context = "\n".join(lines)
        except Exception as e:
            print(f"[SearXNG] Fehler: {e}", flush=True)

    if search_context:
        system_content += f"\n\n{search_context}"

    # Auto-fetch URLs mentioned in the user message (url_fetch skill)
    if "url_fetch" in agent_skills:
        urls = re.findall(r'https?://[^\s<>"]+', user_message)
        if urls:
            url_parts = []
            for url in urls[:3]:  # max 3 URLs per message
                print(f"[URL-Fetch] {url}", flush=True)
                content = fetch_url_text(url)
                url_parts.append(f"[Inhalt von {url}]\n{content}")
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
                return jsonify({"error": "OpenRouter API Key nicht konfiguriert. Bitte in den Einstellungen eintragen."}), 500
            or_headers = {
                "Authorization": f"Bearer {or_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5050",
                "X-Title": "AgentClaw"
            }
            # OpenRouter uses content array for images
            or_messages = []
            for m in messages:
                if m["role"] == "user" and image_data and m is messages[-1]:
                    or_messages.append({"role": "user", "content": [
                        {"type": "text", "text": m["content"]},
                        {"type": "image_url", "image_url": {"url": image_data}}
                    ]})
                else:
                    or_messages.append(m)
            payload = {"model": agent["model"], "messages": or_messages, "stream": False, "max_tokens": agent.get("max_tokens", 1024)}
            if agent.get("web_search"):
                payload["plugins"] = [{"id": "web", "max_results": 5}]
            print(f"[OpenRouter] key={or_key[:12]}… model={agent['model']} web={agent.get('web_search',False)}", flush=True)
            resp = requests.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=or_headers,
                json=payload,
                timeout=60
            )
            # Some models (e.g. Gemma via Google AI Studio) don't support system role —
            # retry by merging system prompt into first user message
            if resp.status_code == 400:
                try:
                    raw = resp.json().get("error", {}).get("metadata", {}).get("raw", "")
                    if "instruction is not enabled" in raw or "system" in raw.lower():
                        sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
                        msgs_no_sys = [m for m in messages if m["role"] != "system"]
                        if sys_content and msgs_no_sys:
                            msgs_no_sys[0] = {
                                "role": "user",
                                "content": f"{sys_content}\n\n{msgs_no_sys[0]['content']}"
                            }
                        resp = requests.post(
                            f"{OPENROUTER_BASE_URL}/chat/completions",
                            headers=or_headers,
                            json={"model": agent["model"], "messages": msgs_no_sys, "stream": False},
                            timeout=60
                        )
                except Exception:
                    pass
            if resp.status_code == 429:
                retry_after = resp.headers.get("X-RateLimit-Reset-Requests") or resp.headers.get("Retry-After", "")
                hint = f" Bitte kurz warten{f' ({retry_after}s)' if retry_after else ''}."
                try:
                    detail = resp.json().get("error", {}).get("metadata", {}).get("raw", "")
                    if detail: hint += f" ({detail})"
                except Exception:
                    pass
                return jsonify({"error": f"Rate Limit (429) — {hint}"}), 429
            if resp.status_code == 402:
                return jsonify({"error": "OpenRouter: Guthaben aufgebraucht (402). Bitte Konto aufladen."}), 402
            if resp.status_code == 400:
                try:
                    detail = resp.json().get("error", {}).get("message", resp.text)
                except Exception:
                    detail = resp.text
                return jsonify({"error": f"OpenRouter 400: {detail}"}), 400
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                return jsonify({"error": f"OpenRouter: {result['error'].get('message', str(result['error']))}"}), 500
            assistant_reply = result["choices"][0]["message"]["content"].strip()

        else:
            # Ollama
            ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
            resp = requests.post(
                f"{ollama_url}/api/chat",
                json={"model": agent["model"], "messages": messages, "stream": False, "options": {"num_predict": agent.get("max_tokens", 1024)}},
                timeout=60
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
                    "stream": False
                }
                # Pass image to generate endpoint if present
                if image_data:
                    raw_b64 = image_data.split(",")[1] if "," in image_data else image_data
                    gen_payload["images"] = [raw_b64]
                resp = requests.post(
                    f"{ollama_url}/api/generate",
                    json=gen_payload,
                    timeout=60
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
            ollama_stats = {"tokens": eval_count, "tok_s": tokens_per_sec, "total_s": total_sec}

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

    resp_data = {"reply": assistant_reply, "voice": agent["voice"]}
    if provider == "ollama" and 'ollama_stats' in dir():
        resp_data["stats"] = ollama_stats
    return jsonify(resp_data)


# ─── TTS ──────────────────────────────────────────────────────────────────────

@app.route("/api/tts", methods=["POST"])
def tts():
    data = request.json
    text = data.get("text", "").strip()
    voice = data.get("voice", "en_paul_neutral")

    if not text:
        return jsonify({"error": "Kein Text"}), 400

    mistral_key = load_providers().get("mistral", {}).get("api_key", "")
    if not mistral_key:
        return jsonify({"error": "Mistral API Key nicht gesetzt. Bitte in den Einstellungen eintragen."}), 500

    headers = {
        "Authorization": f"Bearer {mistral_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "voxtral-mini-tts-latest",
        "input": text,
        "voice": voice,
        "response_format": "mp3"
    }

    try:
        response = requests.post(MISTRAL_TTS_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        audio_b64 = result.get("audio_data", "")
        audio_bytes = base64.b64decode(audio_b64)
        return send_file(
            io.BytesIO(audio_bytes),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="speech.mp3"
        )
    except requests.exceptions.HTTPError as e:
        try:
            err_body = response.json()
        except Exception:
            err_body = response.text
        return jsonify({"error": f"API Fehler {response.status_code}", "details": err_body}), response.status_code
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
                timeout=8
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
                        "en_us": "EN-US", "en_gb": "EN-GB", "de_de": "DE",
                        "fr_fr": "FR", "es_es": "ES", "it_it": "IT"
                    }.get(lang_raw, lang_raw.upper())
                    voices.append({
                        "slug": v["slug"],
                        "name": v["name"],
                        "lang": lang_raw,
                        "lang_label": lang_label,
                        "gender": v.get("gender", ""),
                        "tags": v.get("tags", [])
                    })
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
            entry["api_key_masked"] = key[:6] + "•" * max(0, len(key) - 10) + key[-4:] if len(key) > 10 else "••••"
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
            r = requests.get(f"{MISTRAL_VOICES_URL}?page_size=1",
                             headers={"Authorization": f"Bearer {mk}"}, timeout=5)
            status["mistral"] = {"ok": r.ok, "info": "API Key gültig" if r.ok else f"Fehler {r.status_code}"}
        except Exception:
            status["mistral"] = {"ok": False, "info": "Nicht erreichbar"}
    else:
        status["mistral"] = {"ok": False, "info": "Kein API Key"}

    # SearXNG
    try:
        sx_url = providers.get("searxng", {}).get("url", "http://localhost:8888")
        r = requests.get(f"{sx_url}/search", params={"q": "test", "format": "json"}, timeout=3)
        status["searxng"] = {"ok": r.ok, "info": "Läuft lokal ✓" if r.ok else f"Fehler {r.status_code}"}
    except Exception:
        status["searxng"] = {"ok": False, "info": "Nicht erreichbar"}

    # OpenRouter
    ok = providers.get("openrouter", {}).get("api_key", "")
    if ok:
        try:
            r = requests.get(f"{OPENROUTER_BASE_URL}/models",
                             headers={"Authorization": f"Bearer {ok}"}, timeout=5)
            count = len(r.json().get("data", []))
            status["openrouter"] = {"ok": r.ok, "info": f"{count} Modelle verfügbar" if r.ok else f"Fehler {r.status_code}"}
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
            r = requests.get(f"{OPENROUTER_BASE_URL}/models",
                             headers={"Authorization": f"Bearer {or_key}"}, timeout=10)
            r.raise_for_status()
            models = r.json().get("data", [])
            result["openrouter"] = sorted([
                {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                    "free": (
                        str(m.get("pricing", {}).get("prompt", "1")) == "0" and
                        str(m.get("pricing", {}).get("completion", "1")) == "0"
                    ) or m["id"].endswith(":free")
                }
                for m in models
            ], key=lambda x: x.get("name", ""))
        except Exception:
            result["openrouter"] = []

    return jsonify(result)


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
    text = re.sub(r'\b\d{10,13}\b', '', text)          # Unix timestamps
    text = re.sub(r'[a-f0-9]{32,}', '', text)           # Hashes/Token
    text = re.sub(r'\s+', ' ', text).strip()
    return hashlib.md5(text.encode()).hexdigest(), text


def call_agent_text(agent, system_suffix, user_prompt):
    """Schlanker LLM-Call ohne History, nur Text — für Watchdog."""
    providers = load_providers()
    provider = agent.get("provider", "ollama")
    now = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
    system_content = f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}\n\n{system_suffix}"
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_prompt}
    ]
    if provider == "openrouter":
        or_key = providers.get("openrouter", {}).get("api_key", "")
        if not or_key:
            raise ValueError("OpenRouter Key fehlt")
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json",
                     "HTTP-Referer": "http://localhost:5050", "X-Title": "AgentClaw"},
            json={"model": agent["model"], "messages": messages,
                  "stream": False, "max_tokens": agent.get("max_tokens", 512)},
            timeout=60
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    else:
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        resp = requests.post(
            f"{ollama_url}/api/chat",
            json={"model": agent["model"], "messages": messages, "stream": False,
                  "options": {"num_predict": agent.get("max_tokens", 512)}},
            timeout=60
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("message", {}).get("content", result.get("response", "")).strip()


def send_watchdog_alert(wd, reply):
    """macOS Notification + Chat-History Eintrag."""
    name = wd["name"]
    short = reply[:120].replace('"', "'").replace('\n', ' ')
    # macOS Notification
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{short}" with title "🔔 AgentClaw: {name}" sound name "Ping"'
        ], timeout=5, capture_output=True)
    except Exception as e:
        print(f"[Alert] osascript Fehler: {e}", flush=True)
    # Chat-History Eintrag
    agent_id = wd.get("agent_id")
    if agent_id:
        history = load_history()
        if agent_id not in history:
            history[agent_id] = []
        history[agent_id].append({
            "role": "assistant",
            "content": f"🔔 **Watchdog-Treffer: {name}**\n\n{reply}",
            "ts": datetime.now().isoformat(),
            "watchdog_alert": True
        })
        save_history(history)
    print(f"[Alert] 🔔 '{name}': {short}", flush=True)


def run_watchdog(wd):
    """Vollständige Pipeline: Hash-Check → (bei Änderung) LLM → Alert."""
    wd_id = wd["id"]
    url = wd.get("url", "")
    print(f"[Watchdog] '{wd['name']}' prüft {url}", flush=True)

    # ── 1. Billiger Hash-Check ──────────────────────────────────────────────
    try:
        new_hash, page_text = watchdog_fetch_hash(url)
    except Exception as e:
        update_watchdog_field(wd_id, last_result=f"⚠️ Fetch-Fehler: {e}",
                              last_run=datetime.now().isoformat())
        return

    old_hash = wd.get("last_hash")
    check_count = wd.get("check_count", 0) + 1

    if old_hash and new_hash == old_hash:
        print(f"[Watchdog] '{wd['name']}' — Hash gleich, kein LLM-Call", flush=True)
        update_watchdog_field(wd_id, last_result="⚡ Keine Änderung",
                              last_run=datetime.now().isoformat(),
                              last_hash=new_hash, check_count=check_count)
        return

    # ── 2. Hash geändert → LLM ─────────────────────────────────────────────
    agent_id = wd.get("agent_id")
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        update_watchdog_field(wd_id, last_result="⚠️ Agent nicht gefunden",
                              last_run=datetime.now().isoformat(), last_hash=new_hash)
        return

    prompt = wd.get("prompt", "Was hat sich auf dieser Seite geändert?")
    system_suffix = f"[Watchdog-Seiteninhalt von {url}]\n\n{page_text[:6000]}"

    try:
        reply = call_agent_text(agent, system_suffix, prompt)
    except Exception as e:
        update_watchdog_field(wd_id, last_result=f"⚠️ LLM-Fehler: {e}",
                              last_run=datetime.now().isoformat(), last_hash=new_hash,
                              check_count=check_count)
        return

    # ── 3. Alert wenn Keyword gefunden ─────────────────────────────────────
    alert_keyword = wd.get("alert_keyword", "").strip().lower()
    hit = bool(alert_keyword and alert_keyword in reply.lower())
    if hit:
        send_watchdog_alert(wd, reply)

    hit_count = wd.get("hit_count", 0) + (1 if hit else 0)
    # History (max 50 Einträge)
    history = wd.get("history", [])
    history.append({"ts": datetime.now().isoformat(), "result": reply[:300],
                    "hit": hit, "hash_changed": True})
    if len(history) > 50:
        history = history[-50:]

    update_watchdog_field(wd_id, last_result=reply[:300], last_hash=new_hash,
                          last_run=datetime.now().isoformat(), check_count=check_count,
                          hit_count=hit_count, history=history)


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
            threading.Thread(target=run_watchdog, args=(dict(wd),), daemon=True).start()


def scheduler_loop():
    print("[Scheduler] Watchdog-Scheduler gestartet", flush=True)
    while True:
        try:
            tick_watchdogs()
        except Exception as e:
            print(f"[Scheduler] Fehler: {e}", flush=True)
        time.sleep(60)


# Scheduler als Daemon-Thread starten (nicht blockierend)
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
                s["install_hint"] = "venv/bin/pip install playwright && venv/bin/playwright install chromium"
        elif req == "searxng":
            try:
                sx_url = providers.get("searxng", {}).get("url", "http://localhost:8888")
                r = requests.get(f"{sx_url}/search", params={"q": "test", "format": "json"}, timeout=2)
                s["available"] = r.ok
            except Exception:
                s["available"] = False
                s["install_hint"] = "SearXNG starten: docker run -d -p 8888:8080 searxng/searxng"
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
        "name": data.get("name", "Neuer Watchdog"),
        "url": data.get("url", ""),
        "interval_min": int(data.get("interval_min", 30)),
        "agent_id": data.get("agent_id", ""),
        "prompt": data.get("prompt", "Hat sich etwas Relevantes geändert?"),
        "alert_keyword": data.get("alert_keyword", "JA"),
        "active": data.get("active", True),
        "created_at": now,
        "last_run": None,
        "last_result": None,
        "last_hash": None,
        "next_run": None,
        "check_count": 0,
        "hit_count": 0,
        "history": []
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
            watchdogs[i].update({
                "name": data.get("name", wd["name"]),
                "url": data.get("url", wd["url"]),
                "interval_min": int(data.get("interval_min", wd["interval_min"])),
                "agent_id": data.get("agent_id", wd["agent_id"]),
                "prompt": data.get("prompt", wd["prompt"]),
                "alert_keyword": data.get("alert_keyword", wd["alert_keyword"]),
                "active": data.get("active", wd["active"]),
            })
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
    threading.Thread(target=run_watchdog, args=(dict(wd),), daemon=True).start()
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


# ─── Screenshot ───────────────────────────────────────────────────────────────

@app.route("/api/screenshot", methods=["POST"])
def take_screenshot():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Keine URL angegeben"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return jsonify({"error": "Playwright nicht installiert. Führe aus: venv/bin/pip install playwright && venv/bin/playwright install chromium"}), 501

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            img_bytes = page.screenshot(type="jpeg", quality=80, full_page=False)
            browser.close()
        b64 = base64.b64encode(img_bytes).decode()
        print(f"[Screenshot] {url} — {len(img_bytes)//1024}KB", flush=True)
        return jsonify({"image": f"data:image/jpeg;base64,{b64}", "url": url})
    except Exception as e:
        return jsonify({"error": f"Screenshot fehlgeschlagen: {e}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5050)
