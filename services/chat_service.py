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

        # 0. Nummerierte Listen → Task-Chain
        chain = self._detect_and_dispatch_chain(agent, message)
        if chain:
            return chain

        # 1. URL Auto-Fetch
        message = self._maybe_fetch_urls(message, agent)

        # 2. Skill-Shortcut prüfen
        skill_result = self._try_skill(agent, message, images, attachment_path, providers)
        if skill_result:
            reply = skill_result.text or (f"⚠ {skill_result.error}" if skill_result.error else "")
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

        # 4b. Memory in Qdrant speichern (non-blocking)
        try:
            from core.memory import QDRANT_AVAILABLE, memory_store
            if QDRANT_AVAILABLE:
                import threading
                threading.Thread(
                    target=memory_store, args=(agent_id, message, reply), daemon=True
                ).start()
        except Exception:
            pass

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
            "a2a_dispatches": [{"recipient_name": d.recipient_name, "task_text": d.task_text, "task_id": d.metadata.get("task_id", "")} for d in dispatches],
            "skill": None,
            "image": None,
            "agent_id": agent_id,
        }

    def execute(self, agent: dict, task: dict) -> object:
        """
        Dispatcher-Interface für TaskService:
        Prüft zuerst Skill-Match, fällt sonst auf LLM zurück mit vollständigem Kontext.
        Gibt SkillResult oder dict zurück.
        """
        from storage.providers import load_providers as _lp
        providers = _lp()
        message = task.get("message", "")

        # Skill-Check
        skill_result = self._try_skill(agent, message, None, None, providers)
        if skill_result:
            return skill_result

        # LLM mit vollständigem Kontext (Agent-Directory, Skills, History)
        from storage.history import load_history
        history_data = load_history()
        agent_history = history_data.get(agent["id"], [])
        reply = self._call_llm(agent, message, agent_history, None, providers)

        # A2A-Delegates aus der Antwort dispatchen (mit korrekter Depth)
        self._dispatch_mentions(agent, reply, current_task=task)
        return {"result_text": reply, "skill_used": "llm"}

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

    def _dispatch_mentions(self, sender_agent, reply: str,
                           current_task: dict | None = None) -> list:
        """
        @AgentName-Mentions aus Reply als A2A-Tasks dispatchen.
        Gibt die Liste der A2ADispatch-Objekte zurück (für Display-Bereinigung).
        """
        if not self._task_service:
            return []
        from core.a2a_protocol import parse_a2a_dispatches
        all_agents = load_agents()
        sender_depth = current_task.get("delegation_depth", 0) if current_task else 0
        dispatches = parse_a2a_dispatches(reply, sender_agent, all_agents,
                                          sender_delegation_depth=sender_depth)
        for dispatch in dispatches:
            task = dispatch.to_task_dict()
            dispatch.metadata["task_id"] = task["id"]   # task_id für Rückverfolgung
            self._task_service.enqueue(task)
            logger.info("A2A-Task dispatched: @%s ← '%s...'",
                        dispatch.recipient_name, dispatch.task_text[:60])
        return dispatches

    def _detect_and_dispatch_chain(self, agent: dict, message: str) -> dict | None:
        """
        Erkennt explizite Task-Chains. Triggert NUR wenn:
          - Nachricht beginnt mit '/chain' oder '>>chain' (expliziter Befehl), ODER
          - Erste Zeile beginnt mit '1.' UND jeder Schritt enthält ein @AgentName
        Verhindert False-Positives bei normalen nummerierten Listen/Fragen.
        """
        if not self._task_service:
            return None

        stripped = message.strip()
        explicit_chain = (stripped.lower().startswith("/chain")
                          or stripped.lower().startswith(">>chain"))

        lines = [l.strip() for l in stripped.splitlines() if l.strip()]
        steps_raw = []
        for line in lines:
            m = re.match(r'^(\d+)[.)]\s+(.+)', line)
            if m:
                steps_raw.append(m.group(2).strip())

        if len(steps_raw) < 2:
            return None

        if not explicit_chain:
            first_line = lines[0] if lines else ""
            if not re.match(r'^1[.)]\s+', first_line):
                return None
            mention_rx = re.compile(r'@[A-Za-zÄÖÜäöüß]')
            if not all(mention_rx.search(step) for step in steps_raw):
                return None

        # Schritte parsen: @AgentName extrahieren wenn vorhanden
        from core.a2a_protocol import _find_agent
        all_agents = load_agents()
        name_map = {a["name"].lower(): a for a in all_agents}
        steps = []
        for step_text in steps_raw:
            mention_m = re.match(r'^@([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\- ]{1,40}?)\s+(.+)', step_text)
            if mention_m:
                target_name = mention_m.group(1).strip()
                task_text = mention_m.group(2).strip()
                target = _find_agent(target_name, name_map)
            else:
                target = None
                task_text = step_text

            if target:
                steps.append({"agent": target, "text": task_text})
            else:
                # Kein @Mention → aktueller Agent
                steps.append({"agent": agent, "text": task_text})

        # Tasks erstellen mit depends_on-Kette
        import uuid
        from datetime import datetime, timedelta
        now = datetime.now()
        task_ids = []
        chain_steps_info = []

        for i, step in enumerate(steps):
            tgt = step["agent"]
            task_id = str(uuid.uuid4())
            depends_on = [task_ids[-1]] if task_ids else []
            task = {
                "id": task_id,
                "message": step["text"],
                "sender_agent_id": agent["id"],
                "sender_agent_name": agent["name"],
                "recipient_agent_id": tgt["id"],
                "recipient_agent_name": tgt["name"],
                "status": "waiting" if depends_on else "submitted",
                "priority": 6,
                "created_at": now.isoformat(),
                "timeout_at": (now + timedelta(seconds=1800)).isoformat(),
                "depends_on": depends_on,
                "chain_index": i,
                "chain_total": len(steps),
            }
            self._task_service.enqueue(task)
            task_ids.append(task_id)
            chain_steps_info.append({
                "step": i + 1,
                "agent_name": tgt["name"],
                "text": step["text"],
                "task_id": task_id,
            })
            logger.info("Chain-Task %d/%d erstellt: @%s ← '%s'",
                        i + 1, len(steps), tgt["name"], step["text"][:50])

        # Chain-Bestätigungsnachricht in History
        chain_summary = "\n".join(
            f"{s['step']}. @{s['agent_name']}: {s['text']}" for s in chain_steps_info
        )
        confirm_msg = f"✅ Task-Chain gestartet ({len(steps)} Schritte):\n{chain_summary}"
        self._save_history(agent["id"], message, confirm_msg)
        self._events.emit_chat_message(agent["id"], "assistant", confirm_msg)

        return {
            "__chain__": True,
            "reply": confirm_msg,
            "chain_steps": chain_steps_info,
        }

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

        # Nummerierte Listen → Task-Chain (synchron, kein Streaming nötig)
        chain = self._detect_and_dispatch_chain(agent, message)
        if chain:
            yield chain  # Sentinel mit chain_steps
            return

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
            reply = skill_result.text or (f"⚠ {skill_result.error}" if skill_result.error else "")
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

            # Memory in Qdrant speichern (non-blocking)
            try:
                from core.memory import QDRANT_AVAILABLE, memory_store
                if QDRANT_AVAILABLE:
                    import threading
                    threading.Thread(
                        target=memory_store, args=(agent_id, message, reply), daemon=True
                    ).start()
            except Exception:
                pass

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
                    {"recipient_name": d.recipient_name, "task_text": d.task_text, "task_id": d.metadata.get("task_id", "")}
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
                mem_context = memory_search(agent["id"], message, top_k=3)
                if mem_context:
                    system_content += f"\n\n[Aus deinem Gedächtnis:]\n{mem_context}"
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
