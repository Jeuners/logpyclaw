"""
core/llm.py — Schlanker LLM-Call ohne History (Ollama / OpenRouter).
Verwendet von Watchdog, Heartbeat und als Fallback in /api/chat.
"""
import time as _time
from datetime import datetime

import requests

from config.settings import settings

OPENROUTER_BASE_URL = settings.OPENROUTER_BASE_URL

# LLM-Request Timeout in Sekunden. Große Modelle (≥9GB) auf Ollama brauchen
# bei langem Input (>4k Tokens) schnell 10+ Min.
LLM_REQUEST_TIMEOUT = 900
from core.skills_registry import _build_agent_directory, _get_codebase_context
from storage.providers import load_providers


def call_agent_text(agent, system_suffix, user_prompt, retries: int = 2):
    """Schlanker LLM-Call ohne History, nur Text — für Watchdog/Heartbeat. Mit Timeout-Retry."""
    providers = load_providers()
    provider = agent.get("provider", "ollama")
    now = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
    _agent_skills = set(agent.get("skills", []))
    if agent.get("favorite"):
        _agent_skills.add("codebase_read")
    _codebase = f"\n\n{_get_codebase_context()}" if "codebase_read" in _agent_skills else ""
    # Agent-Directory nur für Operator-Agenten.
    if agent.get("operator", False):
        agent_directory = _build_agent_directory(agent.get("id"))
        system_content = f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}\n\n{agent_directory}{_codebase}\n\n{system_suffix}"
    else:
        system_content = f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}{_codebase}\n\n{system_suffix}"
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
                    timeout=LLM_REQUEST_TIMEOUT,
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
                    timeout=LLM_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                result = resp.json()
                return result.get("message", {}).get("content", result.get("response", "")).strip()
        except requests.exceptions.Timeout as e:
            last_exc = e
            print(f"[call_agent_text] Timeout attempt {attempt+1}/{retries+1} für {agent.get('name')}", flush=True)
            if attempt < retries:
                _time.sleep(2 ** attempt)
        except requests.exceptions.HTTPError as e:
            # OpenRouter liefert sporadisch 404/429/5xx (Routing-Glitch, Rate-Limit,
            # Upstream-Down). Diese sind retryable. 401/403 = Auth-Fehler → sofort raise.
            status = getattr(e.response, "status_code", 0)
            if status in (404, 408, 425, 429, 500, 502, 503, 504) and attempt < retries:
                last_exc = e
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s
                print(f"[call_agent_text] HTTP {status} von {provider} für {agent.get('name')} — retry {attempt+1}/{retries} in {wait}s", flush=True)
                _time.sleep(wait)
                continue
            raise
        except requests.exceptions.RequestException as e:
            # Connection-Errors etc. auch retryable
            last_exc = e
            if attempt < retries:
                wait = 5 * (2 ** attempt)
                print(f"[call_agent_text] {type(e).__name__} für {agent.get('name')} — retry {attempt+1}/{retries} in {wait}s", flush=True)
                _time.sleep(wait)
                continue
            break  # raus aus Retry-Loop → Fallback unten
    # Alle OpenRouter-Versuche fehlgeschlagen → Fallback auf Ollama gemma4:e4b
    if provider == "openrouter":
        from core.llm_stream import FALLBACK_MODEL
        print(
            f"[call_agent_text] OpenRouter final failed für {agent.get('name')} "
            f"({agent.get('model')}) — Fallback auf ollama/{FALLBACK_MODEL}",
            flush=True,
        )
        try:
            ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
            # Ollama versteht keine Multi-Part-Content-Listen → auf Text flachklopfen
            def _flatten_msg(m):
                c = m.get("content", "")
                if isinstance(c, list):
                    parts = []
                    for p in c:
                        if isinstance(p, dict):
                            t = p.get("text") or p.get("content") or ""
                            if t:
                                parts.append(t)
                        elif isinstance(p, str):
                            parts.append(p)
                    c = "\n".join(parts)
                return {"role": m.get("role", "user"), "content": c or ""}
            flat_messages = [_flatten_msg(m) for m in messages]
            resp = requests.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": FALLBACK_MODEL,
                    "messages": flat_messages,
                    "stream": False,
                    **({"options": {"num_predict": agent["max_tokens"]}} if agent.get("max_tokens") else {}),
                },
                timeout=LLM_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("message", {}).get("content", result.get("response", "")).strip()
        except Exception as fe:
            print(f"[call_agent_text] Fallback ebenfalls gescheitert: {fe}", flush=True)
            raise last_exc or fe
    raise last_exc
