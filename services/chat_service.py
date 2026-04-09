"""
services/chat_service.py — Chat-Flow Business-Logik.
Extrahiert aus app.py: /api/chat Route.
"""
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from storage.agents import load_agents
from storage.history import load_history, save_history
from storage.providers import load_providers
from core.config import MAX_HISTORY_PER_AGENT
from core.state import _PENDING_MAIL_SORT, MAC_MAIL_TRIGGERS

if TYPE_CHECKING:
    from skills.registry import SkillRegistry
    from services.agent_service import AgentService
    from services.event_service import EventService

logger = logging.getLogger(__name__)

A2A_COMMUNICATION_PROMPT = """
--- A2A COMMUNICATION ---
You are part of the AgentClaw multi-agent system.

BEHAVIOUR RULES:
1. ALWAYS check first if you can handle a request using YOUR OWN SKILLS (listed below).
2. Only delegate to other agents (@Mention) if you absolutely do not have the required skill yourself.
3. If you find information in your Memory, present it yourself — do not ask another agent.
4. Reply precisely and minimally — no long explanations.

DELEGATION (@Mention) — CRITICAL RULES:
  • Write ONLY: @AgentName [complete task instructions]
  • Include ALL necessary steps in a single @Mention!
  • You will NOT receive the result back. The other agent handles EVERYTHING itself.
  • NEVER add confirmation text like "Delegating to...", "Task sent to...", "I will inform..."
  • NEVER use [TOOL_CALL], JSON, function calls or similar formats!
  • The system handles all confirmation and status display automatically.
  • Wrong: "@Flo fetch Hackernews" then add "I am delegating this..."
  • Right: "@Flo fetch Hackernews, write report, send to Telegram"
--- END A2A ---
""".strip()


class ChatService:
    def __init__(self, registry: "SkillRegistry", agents: "AgentService", events: "EventService"):
        self._registry = registry
        self._agents = agents
        self._events = events
        self._task_service = None  # wird von ServiceContainer gesetzt

    def set_task_service(self, task_service):
        """TaskService registrieren für A2A-Delegation."""
        self._task_service = task_service

    def handle_message(self, agent_id: str, message: str,
                       images: list[str] | None = None,
                       attachment_path: str | None = None) -> dict:
        """
        Haupteinstieg für Chat-Nachrichten.
        Gibt zurück: {reply, skill, image, agent_id}
        """
        agent = self._agents.get_or_raise(agent_id)
        providers = load_providers()

        # 1. URL Auto-Fetch
        message = self._maybe_fetch_urls(message, agent)

        # 2. Skill-Shortcut prüfen
        skill_result = self._try_skill(agent, message, images, attachment_path, providers)
        if skill_result:
            reply = skill_result.text or ""
            image = skill_result.image
            # History speichern
            self._save_history(agent_id, message, reply, image=image, skill=skill_result.skill_used)
            self._events.emit_chat_message(agent_id, "assistant", reply, image=image)
            return {"reply": reply, "skill": skill_result.skill_used, "image": image, "agent_id": agent_id}

        # 3. LLM aufrufen
        history_data = load_history()
        agent_history = history_data.get(agent_id, [])
        reply = self._call_llm(agent, message, agent_history, images, providers)

        # 4. History speichern (Original mit @Mentions — für LLM-Kontext)
        self._save_history(agent_id, message, reply)

        # 5. A2A-Mentions dispatchen + Display-Reply bereinigen
        dispatches = self._dispatch_mentions(agent, reply)
        from core.a2a_protocol import strip_a2a_for_display
        display_reply = strip_a2a_for_display(reply) if dispatches else reply

        # 6. Chat-Event nur für Nicht-@Mention-Text emittieren
        if display_reply:
            self._events.emit_chat_message(agent_id, "assistant", display_reply)
        # A2A-Delegation-Events für jede Delegation
        for d in dispatches:
            self._events.emit_a2a_dispatch(
                agent["id"], agent["name"],
                d.recipient_name, d.task_text,
            )

        return {
            "reply": display_reply,
            "a2a_dispatches": [{"recipient_name": d.recipient_name, "task_text": d.task_text} for d in dispatches],
            "skill": None,
            "image": None,
            "agent_id": agent_id,
        }

    def _try_skill(self, agent, message, images, attachment_path, providers) -> object | None:
        """Versucht einen Skill direkt auszuführen."""
        if not self._registry:
            return None
        skill = self._registry.find_matching(agent, message)
        if not skill:
            return None
        logger.info("Skill '%s' matcht für Agent %s", skill.id, agent["name"])
        try:
            result = skill.execute(agent, message,
                                   images=images,
                                   attachment_path=attachment_path,
                                   providers=providers)
            return result
        except Exception as e:
            logger.error("Skill '%s' fehlgeschlagen: %s", skill.id, e)
            return None

    def _call_llm(self, agent, message, history, images, providers) -> str:
        """LLM aufrufen mit vollständiger History (synchron, für run_in_executor)."""
        import requests as req
        from core.config import OPENROUTER_BASE_URL

        messages = self._build_messages(agent, message, history, images, providers)
        provider = agent.get("provider", "ollama")
        model = agent.get("model", "llama3")
        max_tokens = agent.get("max_tokens") or None

        if provider == "openrouter":
            or_key = providers.get("openrouter", {}).get("api_key", "")
            resp = req.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {or_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5050",
                    "X-Title": "AgentClaw",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    **({"max_tokens": max_tokens} if max_tokens else {}),
                },
                timeout=360,
            )
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
        else:
            ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
            resp = req.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    **({"options": {"num_predict": max_tokens}} if max_tokens else {}),
                },
                timeout=360,
            )
            resp.raise_for_status()
            result = resp.json()
            return result.get("message", {}).get("content", result.get("response", "")).strip()

    def _save_history(self, agent_id, user_msg, assistant_msg, image=None, skill=None):
        """Chat-History speichern."""
        history = load_history()
        if agent_id not in history:
            history[agent_id] = []
        ts = datetime.now().isoformat()
        history[agent_id].append({"role": "user", "content": user_msg, "ts": ts})
        entry = {"role": "assistant", "content": assistant_msg, "ts": ts}
        if image:
            entry["image"] = image
        if skill:
            entry["skill_used"] = skill
        history[agent_id].append(entry)
        save_history(history)

    def _dispatch_mentions(self, sender_agent, reply: str) -> list:
        """
        @AgentName-Mentions aus Reply als A2A-Tasks dispatchen.
        Gibt die Liste der A2ADispatch-Objekte zurück (für Display-Bereinigung).
        """
        if not self._task_service:
            return []
        from core.a2a_protocol import parse_a2a_dispatches
        all_agents = load_agents()
        dispatches = parse_a2a_dispatches(reply, sender_agent, all_agents)
        for dispatch in dispatches:
            task = dispatch.to_task_dict()
            self._task_service.enqueue(task)
            logger.info("A2A-Task dispatched: @%s ← '%s...'",
                        dispatch.recipient_name, dispatch.task_text[:60])
        return dispatches

    def _maybe_fetch_urls(self, message: str, agent: dict) -> str:
        """URLs in der Nachricht automatisch fetchen wenn url_fetch-Skill aktiv."""
        if "url_fetch" not in agent.get("skills", []):
            return message
        url_rx = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)
        urls = url_rx.findall(message)
        if not urls:
            return message
        try:
            from skills.url_fetch import fetch_url_text, _is_safe_url
            fetched_parts = []
            for url in urls[:2]:  # Max 2 URLs pro Nachricht
                if _is_safe_url(url):
                    content = fetch_url_text(url)
                    if content:
                        fetched_parts.append(f"[Inhalt von {url}]:\n{content[:3000]}")
            if fetched_parts:
                return message + "\n\n" + "\n\n".join(fetched_parts)
        except Exception as e:
            logger.warning("URL-Fetch fehlgeschlagen: %s", e)
        return message

    async def stream_message(
        self,
        agent_id: str,
        message: str,
        images: list[str] | None = None,
    ):
        """
        Async Generator — streamt LLM-Antwort Token-by-Token.
        Wird von GET /api/chat/stream (SSE) genutzt.
        Yielded: str-Chunks (Tokens)
        Abschließend wird History gespeichert und Mentions dispatcht.
        """
        from core.llm_stream import stream_llm

        agent = self._agents.get_or_raise(agent_id)
        providers = load_providers()

        # URL Auto-Fetch (synchron, kurz)
        import asyncio
        message = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._maybe_fetch_urls(message, agent)
        )

        # Skill-Check (synchron im Executor — Skills können HTTP machen)
        skill_result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._try_skill(agent, message, images, None, providers)
        )
        if skill_result:
            reply = skill_result.text or ""
            yield reply
            # History + Event (nach dem Yield)
            self._save_history(agent_id, message, reply,
                               image=skill_result.image, skill=skill_result.skill_used)
            self._events.emit_chat_message(agent_id, "assistant", reply, image=skill_result.image)
            return

        # Messages für LLM aufbauen
        history_data = load_history()
        agent_history = history_data.get(agent_id, [])
        messages = self._build_messages(agent, message, agent_history, images, providers)

        # LLM streamen
        full_reply = []
        try:
            async for chunk in stream_llm(agent, messages, providers):
                full_reply.append(chunk)
                yield chunk
        except Exception as e:
            error_msg = f"[Streaming-Fehler: {e}]"
            logger.error("stream_message Fehler: %s", e)
            yield error_msg
            return

        reply = "".join(full_reply).strip()
        if reply:
            # History mit Original-Reply (inkl. @Mentions — für LLM-Kontext)
            self._save_history(agent_id, message, reply)

            # A2A-Dispatchen + Display bereinigen
            dispatches = self._dispatch_mentions(agent, reply)
            from core.a2a_protocol import strip_a2a_for_display
            display_reply = strip_a2a_for_display(reply) if dispatches else reply

            if display_reply:
                self._events.emit_chat_message(agent_id, "assistant", display_reply)
            for d in dispatches:
                self._events.emit_a2a_dispatch(
                    agent["id"], agent["name"],
                    d.recipient_name, d.task_text,
                )

            # Sentinel für die API: a2a_dispatches + display_reply
            yield {
                "__a2a__": True,
                "display_reply": display_reply,
                "a2a_dispatches": [
                    {"recipient_name": d.recipient_name, "task_text": d.task_text}
                    for d in dispatches
                ],
            }

    def _build_messages(
        self,
        agent: dict,
        message: str,
        history: list[dict],
        images: list[str] | None,
        providers: dict,
    ) -> list[dict]:
        """Aufbau der Messages-Liste für LLM-Calls (shared zwischen sync und stream)."""
        from core.skills_registry import _build_agent_directory, _get_codebase_context
        from core.memory import memory_search, QDRANT_AVAILABLE

        now = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
        agent_directory = _build_agent_directory(agent.get("id"))
        _agent_skills = set(agent.get("skills", []))
        _codebase = f"\n\n{_get_codebase_context()}" if "codebase_read" in _agent_skills else ""
        system_content = (
            f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}\n\n"
            f"{A2A_COMMUNICATION_PROMPT}\n\n"
            f"{agent_directory}{_codebase}"
        )

        if QDRANT_AVAILABLE:
            try:
                mem_results = memory_search(agent["id"], message, limit=3)
                if mem_results:
                    mem_text = "\n".join(f"- {m['text'][:200]}" for m in mem_results)
                    system_content += f"\n\n[Aus deinem Gedächtnis:]\n{mem_text}"
            except Exception:
                pass

        messages = [{"role": "system", "content": system_content}]
        for msg in history[-MAX_HISTORY_PER_AGENT:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        if images:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": message},
                *[{"type": "image_url", "image_url": {"url": img}} for img in images]
            ]})
        else:
            messages.append({"role": "user", "content": message})

        return messages

    def tick_telegram(self):
        """Telegram-Inbox pollen (wird vom Scheduler aufgerufen)."""
        try:
            providers = load_providers()
            tg = providers.get("telegram", {})
            if not tg.get("bot_token") or not tg.get("enabled"):
                return
            # Telegram-Polling-Logik
            # (minimal stub — wird bei Bedarf ausgebaut)
        except Exception as e:
            logger.warning("Telegram-Tick Fehler: %s", e)
