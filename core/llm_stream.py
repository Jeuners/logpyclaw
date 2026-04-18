"""
core/llm_stream.py — Streaming LLM-Calls für OpenRouter und Ollama.

Liefert Token-by-Token als async Generator.
Wird von chat_service.stream_message() und api/chat.py SSE-Endpoint genutzt.
"""
import json
import logging
from typing import AsyncGenerator

import httpx

from config.settings import settings

OPENROUTER_BASE_URL = settings.OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

# Timeout-Konfiguration: connect kurz, read lang (LLM braucht Zeit)
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=1800.0, write=10.0, pool=5.0)


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

    async with httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
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
        async for chunk in stream_openrouter(messages, model, or_key, max_tokens):
            yield {"content": chunk}
    else:
        ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        if think_override is None:
            from core.model_capabilities import supports_thinking
            think = supports_thinking(model, provider="ollama", ollama_url=ollama_url)
        else:
            think = think_override
        async for chunk in stream_ollama(messages, model, ollama_url, max_tokens, think=think):
            yield chunk
