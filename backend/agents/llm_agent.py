"""
backend/agents/llm_agent.py — LLM-gestützter Agent.

Unterstützt Ollama, Anthropic, OpenAI. Provider wird über `provider`-Feld gewählt.
Jeder LLM-Call tickt die CDC-Clock mit aktueller Eigenzeit-Rate.
Wenn ein conductor übergeben wird, streamt Ollama-Antworten Token für Token via SSE.
"""

from __future__ import annotations

import json as _json

import httpx

from backend.agents.base import AsyncAgent
from backend.config import get_settings
from backend.core.protocol import Message

# Modulweiter geteilter HTTP-Client mit Connection-Pooling — ein neuer
# AsyncClient pro Call würde TCP/TLS-Handshakes pro Request kosten.
_shared_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    """Lazy Singleton: geteilter AsyncClient für alle Provider-Calls.

    Bewusst kein close-Mechanismus — der Client lebt für die
    Prozess-Lebensdauer, das OS räumt die Sockets beim Exit auf.
    """
    global _shared_client
    if _shared_client is None:
        # Connection-Cap: verhindert, dass ein Mission-Burst (parallele
        # Plan-Steps) unbeschränkt viele Upstream-Connections öffnet
        _shared_client = httpx.AsyncClient(
            # Lange Generationen (z.B. komplette Single-File-Spiele mit 32k
            # Output-Tokens auf M3) brauchen deutlich mehr als 120s. Connect
            # bleibt kurz, damit tote Endpoints schnell auffallen.
            timeout=httpx.Timeout(600.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_client


class LLMAgent(AsyncAgent):
    def __init__(
        self,
        agent_id: str,
        name: str,
        model: str,
        provider: str,  # "ollama" | "anthropic" | "openai"
        soul: str = "",
        faction: str = "",
        ollama_url: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        reasoning_max_tokens: int = 0,
        conductor=None,
    ) -> None:
        super().__init__(agent_id, name)
        self.model = model
        self.provider = provider
        self.soul = soul
        self.faction = faction
        self.ollama_url = ollama_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Denkmodelle (M3 & Co.) können bei komplexen Prompts ihr komplettes
        # Token-Budget mit Reasoning verbrennen und 0 Content liefern — bei
        # Bedarf via OpenRouter-Param kappen (0 = kein Cap).
        self.reasoning_max_tokens = reasoning_max_tokens
        self._conductor = conductor

    # ── Handle ────────────────────────────────────────────────────────────────

    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        content = msg.payload.get("content", "")
        try:
            result = await self._call_llm(content, msg.mission_id, msg.task_id)
            return Message.response(msg, result, clock=clock)
        except Exception as e:
            return Message.error(msg, f"{self.provider} error: {e}", clock=clock)

    # ── Provider-Switch ───────────────────────────────────────────────────────

    async def _call_llm(self, content: str, mission_id: str = "", task_id: str = "") -> str:
        if self.provider == "ollama":
            return await self._ollama(content, mission_id, task_id)
        if self.provider == "anthropic":
            return await self._anthropic(content)
        if self.provider in ("openai", "openrouter", "groq"):
            return await self._openai_compat(content, mission_id, task_id)
        raise ValueError(f"Unbekannter Provider: {self.provider}")

    async def _ollama(self, content: str, mission_id: str = "", task_id: str = "") -> str:
        messages = []
        if self.soul:
            messages.append({"role": "system", "content": self.soul})
        messages.append({"role": "user", "content": content})

        store = self._conductor.store if self._conductor else None
        can_stream = bool(store and mission_id and task_id)

        client = _client()
        if can_stream:
            # Token-Streaming: jeder Token wird live via SSE emittiert
            full = ""
            async with client.stream(
                "POST",
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "options": {"temperature": self.temperature},
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full += token
                        store.emit_token(mission_id, task_id, token)
                    if chunk.get("done"):
                        break
            return full
        else:
            # Fallback: single-shot (kein Streaming verfügbar)
            r = await client.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": self.temperature},
                },
            )
            r.raise_for_status()
            return r.json()["message"]["content"]

    async def _anthropic(self, content: str) -> str:
        api_key = get_settings().anthropic_api_key
        system = self.soul or "You are a helpful assistant."
        r = await _client().post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "system": system,
                "messages": [{"role": "user", "content": content}],
            },
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]

    async def _openai_compat(self, content: str, mission_id: str = "", task_id: str = "") -> str:
        cfg = get_settings()
        if self.provider == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
            api_key  = cfg.openrouter_api_key
            extra_headers = {"HTTP-Referer": "https://logpyclaw.local", "X-Title": "LogpyClaw"}
        elif self.provider == "groq":
            from backend.core.key_pool import get_groq_key
            base_url = "https://api.groq.com/openai/v1"
            api_key  = get_groq_key()
            extra_headers = {}
        else:
            base_url = "https://api.openai.com/v1"
            api_key  = cfg.openai_api_key
            extra_headers = {}

        messages = []
        if self.soul:
            messages.append({"role": "system", "content": self.soul})
        messages.append({"role": "user", "content": content})

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", **extra_headers}
        store     = self._conductor.store if self._conductor else None
        can_stream = bool(store and mission_id and task_id)

        # Reasoning-Cap nur für OpenRouter (vereinheitlichter reasoning-Param)
        reasoning_extra = {}
        if self.provider == "openrouter" and self.reasoning_max_tokens > 0:
            reasoning_extra = {"reasoning": {"max_tokens": self.reasoning_max_tokens}}

        client = _client()
        if can_stream:
            full = ""
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    "stream": True,
                    **reasoning_extra,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(raw)
                        token = chunk["choices"][0]["delta"].get("content", "")
                    except Exception:
                        continue
                    if token:
                        full += token
                        store.emit_token(mission_id, task_id, token)
            if not full.strip():
                # Denkmodelle können das komplette Budget mit Reasoning
                # verbrennen (finish_reason=length, content leer) — das ist
                # ein Fehler, kein leeres Ergebnis.
                raise RuntimeError(
                    "LLM lieferte keinen Content (Token-Budget vermutlich "
                    "komplett vom Reasoning aufgebraucht)"
                )
            return full
        else:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    **reasoning_extra,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({
            "model": self.model,
            "provider": self.provider,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        })
        if self.faction:
            d["faction"] = self.faction
        return d
