"""
backend/agents/claude_agent.py — Claude Frontier Agent.

Führt `claude -p "..."` als lokalen Subprocess aus.
Keychain-Auth funktioniert, da kein SSH-Hop nötig.

Konfiguration in agents.yaml (type: claude):
  claude_bin: Pfad zum claude CLI (default: claude)
  model:      Modell-ID           (default: claude-opus-4-7)
  goal:       System-Prompt / Basis-Kontext für alle Anfragen
  timeout:    Sekunden            (default: 120)
"""
from __future__ import annotations

import asyncio

from backend.agents.base import AsyncAgent
from backend.core.protocol import Message


class ClaudeSSHAgent(AsyncAgent):

    def __init__(
        self,
        agent_id:   str = "agent:claude",
        name:       str = "Claude",
        claude_bin: str = "claude",
        model:      str = "claude-opus-4-7",
        goal:       str = "",
        faction:    str = "makers",
        timeout:    int = 120,
        # SSH-Parameter werden akzeptiert aber ignoriert
        **_kwargs,
    ) -> None:
        super().__init__(agent_id, name)
        self._bin     = claude_bin
        self._model   = model
        self._goal    = goal
        self._faction = faction
        self._timeout = int(timeout)

    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        prompt = msg.payload.get("content", "")
        try:
            result = await self._run(prompt)
            return Message.response(msg, result, clock=clock)
        except Exception as e:
            return Message.error(msg, f"[Claude] {e}", clock=clock)

    async def _run(self, prompt: str) -> str:
        args = [self._bin, "-p", prompt]
        if self._model:
            args += ["--model", self._model]
        if self._goal:
            args += ["--system-prompt", self._goal]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except TimeoutError:
            proc.kill()
            raise TimeoutError(f"claude timeout nach {self._timeout}s")

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise RuntimeError(err or f"claude exited {proc.returncode}")
        return stdout.decode().strip()

    @property
    def description(self) -> str:
        return f"Claude Frontier Agent — {self._model}"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["faction"] = self._faction
        d["model"]   = self._model
        return d
