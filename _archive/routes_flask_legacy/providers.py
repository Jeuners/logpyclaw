"""
routes/providers.py — Provider-Konfiguration und Modell-Discovery.
"""
import requests
from flask import Blueprint, jsonify, request

from core.config import MISTRAL_VOICES_URL, OPENROUTER_BASE_URL
from storage.providers import load_providers, save_providers

bp = Blueprint("providers", __name__)


@bp.route("/api/providers", methods=["GET"])
def get_providers():
    providers = load_providers()
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


@bp.route("/api/providers", methods=["POST"])
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


@bp.route("/api/providers/status", methods=["GET"])
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
                "info": f"{count} Modelle verfügbar" if r.ok else f"Fehler {r.status_code}",
            }
        except Exception:
            status["openrouter"] = {"ok": False, "info": "Nicht erreichbar"}
    else:
        status["openrouter"] = {"ok": False, "info": "Kein API Key"}

    return jsonify(status)


@bp.route("/api/models", methods=["GET"])
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
                    ) or m["id"].endswith(":free"),
                }
                for m in models
            ]
            result["openrouter"] = sorted(
                parsed, key=lambda x: (0 if x["free"] else 1, x.get("name", "").lower())
            )
        except Exception:
            result["openrouter"] = []

    return jsonify(result)
