"""
core/llm.py — Schlanker LLM-Call ohne History (Ollama / OpenRouter).
Verwendet von Watchdog, Heartbeat und als Fallback in /api/chat.
"""
import time as _time
from datetime import datetime

import requests

from core.config import OPENROUTER_BASE_URL
from core.skills_registry import _build_agent_directory, _get_codebase_context
from storage.providers import load_providers


def call_agent_text(agent, system_suffix, user_prompt, retries: int = 2):
    """Schlanker LLM-Call ohne History, nur Text — für Watchdog/Heartbeat. Mit Timeout-Retry."""
    providers = load_providers()
    provider = agent.get("provider", "ollama")
    now = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
    agent_directory = _build_agent_directory(agent.get("id"))
    _agent_skills = set(agent.get("skills", []))
    if agent.get("favorite"):
        _agent_skills.add("codebase_read")
    _codebase = f"\n\n{_get_codebase_context()}" if "codebase_read" in _agent_skills else ""
    system_content = f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}\n\n{agent_directory}{_codebase}\n\n{system_suffix}"
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_prompt},
    ]
    last_exc = None
    for attempt in range(retries + 1):
        try:
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
                        **({"max_tokens": agent["max_tokens"]} if agent.get("max_tokens") else {}),
                    },
                    timeout=360,
                )
                resp.raise_for_status()
                return (resp.json()["choices"][0]["message"].get("content") or "").strip()
            else:
                ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
                resp = requests.post(
                    f"{ollama_url}/api/chat",
                    json={
                        "model": agent["model"],
                        "messages": messages,
                        "stream": False,
                        **({"options": {"num_predict": agent["max_tokens"]}} if agent.get("max_tokens") else {}),
                    },
                    timeout=360,
                )
                resp.raise_for_status()
                result = resp.json()
                return result.get("message", {}).get("content", result.get("response", "")).strip()
        except requests.exceptions.Timeout as e:
            last_exc = e
            print(f"[call_agent_text] Timeout attempt {attempt+1}/{retries+1} für {agent.get('name')}", flush=True)
            if attempt < retries:
                _time.sleep(2 ** attempt)
        except requests.exceptions.RequestException:
            raise
    raise last_exc
