import os
import json
import base64
import uuid
import re
import requests
from datetime import datetime
from html.parser import HTMLParser
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
import io

load_dotenv()

app = Flask(__name__)

MISTRAL_TTS_URL = "https://api.mistral.ai/v1/audio/speech"
MISTRAL_VOICES_URL = "https://api.mistral.ai/v1/audio/voices"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_FILE = os.path.join(BASE_DIR, "agents.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
PROVIDERS_FILE = os.path.join(BASE_DIR, "providers.json")


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


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


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
        "web_search": data.get("web_search", False),
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
                "web_search": data.get("web_search", a.get("web_search", False)),
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

    if not user_message:
        return jsonify({"error": "Keine Nachricht"}), 400

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

    # Web search via SearXNG (local) if enabled and query seems to need it
    SEARCH_TRIGGERS = [
        "news", "aktuell", "heute", "gerade", "neueste", "letzte",
        "suche", "such", "finde", "schau nach", "recherchier",
        "was ist", "wer ist", "wo ist", "wie viel", "wann",
        "preis", "wetter", "kurs", "aktie", "sport", "ergebnis"
    ]
    needs_search = any(t in user_message.lower() for t in SEARCH_TRIGGERS)

    search_context = ""
    if agent.get("web_search") and needs_search:
        try:
            sx_url = providers.get("searxng", {}).get("url", "http://localhost:8888")
            sx_resp = requests.get(
                f"{sx_url}/search",
                params={"q": user_message, "format": "json", "language": "de"},
                timeout=8
            )
            results = sx_resp.json().get("results", [])[:5]
            if results:
                lines = ["[Websuche-Ergebnisse für: " + user_message + "]"]
                for r in results:
                    lines.append(f"- {r.get('title','')} — {r.get('url','')}\n  {r.get('content','')[:200]}")
                search_context = "\n".join(lines)
        except Exception as e:
            print(f"[SearXNG] Fehler: {e}", flush=True)

    if search_context:
        system_content += f"\n\n{search_context}"

    # Auto-fetch URLs mentioned in the user message
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
    messages.append({"role": "user", "content": user_message})

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
            payload = {"model": agent["model"], "messages": messages, "stream": False}
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
                json={"model": agent["model"], "messages": messages, "stream": False},
                timeout=60
            )
            if resp.status_code == 400:
                # Fallback to /api/generate for base models (e.g. StarCoder2)
                prompt_parts = []
                for msg in messages:
                    role = msg["role"].capitalize()
                    if role == "System":
                        prompt_parts.append(f"System: {msg['content']}")
                    elif role == "User":
                        prompt_parts.append(f"User: {msg['content']}")
                    elif role == "Assistant":
                        prompt_parts.append(f"Assistant: {msg['content']}")
                prompt_parts.append("Assistant:")
                resp = requests.post(
                    f"{ollama_url}/api/generate",
                    json={"model": agent["model"], "prompt": "\n".join(prompt_parts), "stream": False},
                    timeout=60
                )
            resp.raise_for_status()
            result = resp.json()
            if "message" in result:
                assistant_reply = result["message"].get("content", "").strip()
            else:
                assistant_reply = result.get("response", "").strip()

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

    return jsonify({"reply": assistant_reply, "voice": agent["voice"]})


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
        resp = requests.get(
            f"{MISTRAL_VOICES_URL}?page_size=100",
            headers={"Authorization": f"Bearer {mistral_key}"},
            timeout=10
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        seen = set()
        voices = []
        for v in items:
            if v["slug"] not in seen:
                seen.add(v["slug"])
                voices.append({
                    "slug": v["slug"],
                    "name": v["name"],
                    "lang": v["languages"][0] if v["languages"] else "en"
                })
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


if __name__ == "__main__":
    app.run(debug=True, port=5050)
