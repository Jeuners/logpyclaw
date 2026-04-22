"""
core/llm_stream.py — Streaming LLM-Calls für OpenRouter und Ollama.

Liefert Token-by-Token als async Generator.
Wird von chat_service.stream_message() und api/chat.py SSE-Endpoint genutzt.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator

import httpx

from config.settings import settings

OPENROUTER_BASE_URL = settings.OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

# Timeout-Konfiguration: connect kurz, read lang (LLM braucht Zeit)
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=1800.0, write=10.0, pool=5.0)

# Retryable HTTP-Status für OpenRouter (Routing-Glitch, Rate-Limit, Upstream-Down).
# 401/403 NICHT drin — das sind Auth-Fehler, Retry bringt nichts.
_RETRYABLE_STATUS = {404, 408, 425, 429, 500, 502, 503, 504}
_MAX_RETRIES = 2  # 3 Versuche insgesamt
_BASE_BACKOFF = 5.0  # 5s, 10s, 20s

# Fallback wenn OpenRouter komplett ausfällt: lokales Ollama-Modell.
# Anpassbar — aktuell das Modell das überall zuverlässig läuft.
FALLBACK_PROVIDER = "ollama"
FALLBACK_MODEL = "gemma4:e4b"


async def stream_openrouter(
    messages: list[dict],
    model: str,
    api_key: str,
    max_tokens: int | None = None,
    referer: str = "http://localhost:5050",
) -> AsyncGenerator[str, None]:
    """
    Streamt Tokens von OpenRouter via SSE.
    Yields: Text-Chunks (einzelne Tokens oder kurze Sequenzen)

    Retry bei 404/429/5xx VOR dem ersten Chunk (typisch OpenRouter-Glitches).
    Sobald Tokens fließen → kein Retry mehr (Mid-Stream-Abbruch = User sieht Teil-Antwort).
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": referer,
        "X-Title": "AgentClaw",
    }

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                        # Body lesen für bessere Log-Message, dann retry
                        body = (await resp.aread()).decode("utf-8", "replace")[:200]
                        wait = _BASE_BACKOFF * (2 ** attempt)
                        logger.warning(
                            "stream_openrouter: HTTP %d (%s…) für model=%s — retry %d/%d in %.0fs",
                            resp.status_code, body, model, attempt + 1, _MAX_RETRIES, wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        raw = line[6:]  # Strip "data: "
                        if raw.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
            return  # Stream durch → Retry-Loop verlassen
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                wait = _BASE_BACKOFF * (2 ** attempt)
                logger.warning(
                    "stream_openrouter: %s für model=%s — retry %d/%d in %.0fs",
                    type(e).__name__, model, attempt + 1, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    if last_exc:
        raise last_exc


async def stream_ollama(
    messages: list[dict],
    model: str,
    ollama_url: str = "http://localhost:11434",
    max_tokens: int | None = None,
    think: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    Streamt Tokens von Ollama via NDJSON.
    Yields: {"content": str} oder {"thinking": str} dicts.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if max_tokens:
        payload["options"] = {"num_predict": max_tokens}
    payload["think"] = bool(think)

    async with httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{ollama_url.rstrip('/')}/api/chat",
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})
                    thinking = msg.get("thinking", "")
                    if thinking:
                        yield {"thinking": thinking}
                    content = msg.get("content", "")
                    if content:
                        yield {"content": content}
                    if chunk.get("done"):
                        break
                except (json.JSONDecodeError, KeyError):
                    continue


async def stream_llm(
    agent: dict,
    messages: list[dict],
    providers: dict,
    think_override: bool | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Universeller Streaming-LLM-Call — wählt Provider automatisch.

    Args:
        agent: Agent-Config-Dict (enthält provider, model, max_tokens)
        messages: OpenAI-kompatible Messages-Liste
        providers: Provider-Konfiguration
        think_override: Wenn nicht None, erzwingt Thinking an/aus.
                        Wenn None: Auto-Detect via model_capabilities.

    Yields:
        {"content": str} oder {"thinking": str} dicts.
        OpenRouter liefert nur content.
    """
    provider = agent.get("provider", "ollama")
    model = agent.get("model", "llama3")
    max_tokens = agent.get("max_tokens") or None

    if provider == "openrouter":
        or_key = providers.get("openrouter", {}).get("api_key", "")
        if not or_key:
            yield {"content": "[Fehler: OpenRouter API-Key fehlt]"}
            return
        # Retry ist intern in stream_openrouter — wenn trotzdem alles knallt,
        # fallback auf lokales Ollama (gemma4:e4b) damit der User nicht im Regen steht.
        first_chunk_seen = False
        try:
            async for chunk in stream_openrouter(messages, model, or_key, max_tokens):
                first_chunk_seen = True
                yield {"content": chunk}
            return
        except Exception as e:
            if first_chunk_seen:
                # Mid-Stream-Error — User hat schon Teil-Output gesehen, nicht neu anfangen.
                logger.warning("stream_llm: OpenRouter Mid-Stream-Error nach Start, kein Fallback: %s", e)
                raise
            logger.warning(
                "stream_llm: OpenRouter (%s) failed: %s — Fallback auf %s/%s",
                model, e, FALLBACK_PROVIDER, FALLBACK_MODEL,
            )
            yield {"content": f"⚠️ OpenRouter-Fehler, nutze lokalen Fallback `{FALLBACK_MODEL}`\n\n"}
            ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
            from core.model_capabilities import supports_thinking
            think = supports_thinking(FALLBACK_MODEL, provider="ollama", ollama_url=ollama_url) if think_override is None else think_override
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
            async for chunk in stream_ollama(flat_messages, FALLBACK_MODEL, ollama_url, max_tokens, think=think):
                yield chunk
            return
    else:
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        if think_override is None:
            from core.model_capabilities import supports_thinking
            think = supports_thinking(model, provider="ollama", ollama_url=ollama_url)
        else:
            think = think_override
        async for chunk in stream_ollama(messages, model, ollama_url, max_tokens, think=think):
            yield chunk
