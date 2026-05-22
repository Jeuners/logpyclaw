"""
backend/agents/llm_agent.py — LLM-gestützter Agent.

Unterstützt Ollama, Anthropic, OpenAI. Provider wird über `provider`-Feld gewählt.
Jeder LLM-Call tickt die CDC-Clock mit aktueller Eigenzeit-Rate.
"""

from __future__ import annotations

import httpx

from backend.agents.base import AsyncAgent
from backend.config import get_settings
from backend.core.protocol import Message


class LLMAgent(AsyncAgent):
    def __init__(
        self,
        agent_id: str,
        name: str,
        model: str,
        provider: str,  # "ollama" | "anthropic" | "openai"
        soul: str = "",
        ollama_url: str = "http://localhost:11434",
        max_tokens: int = 2048,
    ) -> None:
        super().__init__(agent_id, name)
        self.model = model
        self.provider = provider
        self.soul = soul
        self.ollama_url = ollama_url
        self.max_tokens = max_tokens

    # ── Handle ────────────────────────────────────────────────────────────────

    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        content = msg.payload.get("content", "")
        try:
            result = await self._call_llm(content)
            return Message.response(msg, result, clock=clock)
        except Exception as e:
            return Message.error(msg, f"{self.provider} error: {e}", clock=clock)

    # ── Provider-Switch ───────────────────────────────────────────────────────

    async def _call_llm(self, content: str) -> str:
        if self.provider == "ollama":
            return await self._ollama(content)
        if self.provider == "anthropic":
            return await self._anthropic(content)
        if self.provider == "openai":
            return await self._openai(content)
        raise ValueError(f"Unbekannter Provider: {self.provider}")

    async def _ollama(self, content: str) -> str:
        messages = []
        if self.soul:
            messages.append({"role": "system", "content": self.soul})
        messages.append({"role": "user", "content": content})
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.ollama_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
            )
            r.raise_for_status()
            return r.json()["message"]["content"]

    async def _anthropic(self, content: str) -> str:
        api_key = get_settings().anthropic_api_key
        system = self.soul or "You are a helpful assistant."
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": content}],
                },
            )
            r.raise_for_status()
            return r.json()["content"][0]["text"]

    async def _openai(self, content: str) -> str:
        api_key = get_settings().openai_api_key
        messages = []
        if self.soul:
            messages.append({"role": "system", "content": self.soul})
        messages.append({"role": "user", "content": content})
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": self.model, "messages": messages, "max_tokens": self.max_tokens},
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({"model": self.model, "provider": self.provider})
        return d
