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
2. Only delegate to other agents if you absolutely do not have the required skill yourself.
3. If you find information in your Memory, present it yourself — do not ask another agent.
4. Reply precisely and minimally — no long explanations.

SINGLE DELEGATION (@Mention):
  Use for ONE task to ONE agent:
  @AgentName [complete task instructions]

MULTI-TASK DELEGATION (TASKLIST):
  Use when you need to delegate MULTIPLE tasks — especially sequential ones (e.g. generate 4 images, multi-step pipelines).
  Write a [TASKLIST] block with a JSON array:

  [TASKLIST]
  [
    {"to": "Picasso", "task": "Generate image 1: [full detailed description]", "id": "img1"},
    {"to": "Picasso", "task": "Generate image 2: [full detailed description]", "after": "img1", "id": "img2"},
    {"to": "Picasso", "task": "Generate image 3: [full detailed description]", "after": "img2", "id": "img3"},
    {"to": "Jan",     "task": "Fetch references for X", "parallel": true}
  ]
  [/TASKLIST]

  Fields:
    to       (required) Agent name
    task     (required) Full task description — ALWAYS include ALL context (character descriptions, style, etc.)!
    id       (optional) Local reference ID
    after    (optional) Local ID of the task to wait for (sequential chain)
    priority (optional) 1-10, default 5
    parallel (optional) true = start immediately without waiting for previous same-agent task

  RULES:
  • Each task MUST be self-contained — include ALL necessary information (never assume the agent remembers previous tasks)
  • For image series: repeat the FULL character description in EVERY task
  • Tasks with "after" wait for the referenced task to complete before starting
  • Tasks without "after" to the same agent run sequentially automatically
  • NEVER mix TASKLIST and @Mentions in the same reply
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

        # 5a. TASKLIST zuerst prüfen (strukturiert, hat Vorrang vor @Mentions)
        from core.task_list import has_task_list, strip_task_list
        if has_task_list(reply):
            tl_dispatches = self._dispatch_task_list(agent, reply, images=images,
                                                     attachment_path=attachment_path)
            display_reply = strip_task_list(reply)
            if display_reply:
                self._events.emit_chat_message(agent_id, "assistant", display_reply)
            for item in tl_dispatches:
                self._events.emit_a2a_dispatch(
                    agent["id"], agent["name"], item.recipient_name, item.task_text,
                )
            return {
                "reply": display_reply,
                "a2a_dispatches": [{"recipient_name": i.recipient_name, "task_text": i.task_text} for i in tl_dispatches],
                "skill": None, "image": None, "agent_id": agent_id,
            }

        # 5b. @Mentions dispatchen + Display-Reply bereinigen (images/attachment weitergeben)
        dispatches = self._dispatch_mentions(agent, reply,
                                             images=images,
                                             attachment_path=attachment_path)
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
        Bilder und Attachment-Pfade aus dem Task werden an Skill und LLM weitergegeben.
        Gibt SkillResult oder dict zurück.
        """
        from storage.providers import load_providers as _lp
        providers = _lp()
        message = task.get("message", "")

        # Bilder aus Task oder context_image zusammensammeln
        images: list[str] | None = task.get("images") or None
        if not images and task.get("context_image"):
            images = [task["context_image"]]
        attachment_path: str | None = task.get("attachment_path") or None

        # Skill-Check (mit images + attachment_path aus dem Task)
        skill_result = self._try_skill(agent, message, images, attachment_path, providers)
        if skill_result:
            return skill_result

        # LLM mit vollständigem Kontext (Agent-Directory, Skills, History, Bilder)
        from storage.history import load_history
        history_data = load_history()
        agent_history = history_data.get(agent["id"], [])
        reply = self._call_llm(agent, message, agent_history, images, providers)

        # TASKLIST hat Vorrang, dann @Mentions
        from core.task_list import has_task_list
        if has_task_list(reply):
            self._dispatch_task_list(agent, reply, current_task=task)
        else:
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
                           current_task: dict | None = None,
                           images: list | None = None,
                           attachment_path: str | None = None) -> list:
        """
        @AgentName-Mentions aus Reply als A2A-Tasks dispatchen.
        images + attachment_path werden an alle Dispatches weitergegeben,
        damit Dateianhänge des Users auch beim Ziel-Agenten ankommen.
        Gibt die Liste der A2ADispatch-Objekte zurück (für Display-Bereinigung).
        """
        if not self._task_service:
            return []
        from core.a2a_protocol import parse_a2a_dispatches
        all_agents = load_agents()
        sender_depth = current_task.get("delegation_depth", 0) if current_task else 0

        # Bilder aus dem aktuellen Task-Kontext übernehmen (falls keine expliziten images)
        if not images and current_task:
            images = current_task.get("images") or []
            if not images and current_task.get("context_image"):
                images = [current_task["context_image"]]
        if not attachment_path and current_task:
            attachment_path = current_task.get("attachment_path", "")

        dispatches = parse_a2a_dispatches(reply, sender_agent, all_agents,
                                          sender_delegation_depth=sender_depth)

        # Gleiche Empfänger sequenziell ketten: jeder Task wartet auf den vorherigen
        # Beispiel: @Picasso Bild 1 → @Picasso Bild 2 → @Picasso Bild 3
        # → Task 2 depends_on Task 1, Task 3 depends_on Task 2, etc.
        last_task_id_per_recipient: dict[str, str] = {}

        for dispatch in dispatches:
            if images:
                dispatch.images = list(images)
            if attachment_path:
                dispatch.attachment_path = attachment_path
            task = dispatch.to_task_dict()
            dispatch.metadata["task_id"] = task["id"]

            # Kette: falls gleicher Agent bereits einen Task hat → depends_on setzen
            prev_id = last_task_id_per_recipient.get(dispatch.recipient_id)
            if prev_id:
                task["depends_on"] = [prev_id]
                task["status"] = "waiting"
                logger.info(
                    "A2A-Kette: @%s Task %s wartet auf %s",
                    dispatch.recipient_name, task["id"][:8], prev_id[:8],
                )

            last_task_id_per_recipient[dispatch.recipient_id] = task["id"]
            self._task_service.enqueue(task)
            logger.info("A2A-Task dispatched: @%s ← '%s...' (images=%d, attachment=%s, seq=%d)",
                        dispatch.recipient_name, dispatch.task_text[:60],
                        len(dispatch.images), bool(dispatch.attachment_path),
                        list(last_task_id_per_recipient.values()).count(task["id"]))
        return dispatches

    def _dispatch_task_list(self, sender_agent: dict, reply: str,
                            images: list | None = None,
                            attachment_path: str | None = None,
                            current_task: dict | None = None) -> list:
        """
        Parsed einen [TASKLIST]-Block und erstellt korrekt verkettete Tasks.

        Abhängigkeiten (after) werden über depends_on im TaskService aufgelöst.
        Mehrere Tasks an denselben Agenten ohne explicit 'after' werden automatisch
        sequenziell gekettet (wie bei @Mentions).
        Gibt die Liste der TaskItem-Objekte zurück.
        """
        if not self._task_service:
            return []
        from core.task_list import parse_task_list
        from storage.agents import load_agents
        all_agents = load_agents()
        depth = current_task.get("delegation_depth", 0) if current_task else 0

        items = parse_task_list(reply, sender_agent, all_agents, delegation_depth=depth)
        if not items:
            return []

        # Bilder/Attachment aus Kontext übernehmen
        if not images and current_task:
            images = current_task.get("images") or []
            if not images and current_task.get("context_image"):
                images = [current_task["context_image"]]
        if not attachment_path and current_task:
            attachment_path = current_task.get("attachment_path", "")

        # Lokale ID → System-Task-ID Mapping (für depends_on Auflösung)
        local_to_system: dict[str, str] = {}

        # Für implizite Sequenzierung: letzter Task pro Empfänger
        last_per_recipient: dict[str, str] = {}

        enqueued: list = []

        for item in items:
            if images:
                item.images = list(images)
            if attachment_path:
                item.attachment_path = attachment_path
            item.sender_id = sender_agent.get("id", "")
            item.sender_name = sender_agent.get("name", "")

            # Abhängigkeiten auflösen
            depends_on: list[str] = []

            # Explizite "after"-Abhängigkeit
            if item.after and item.after in local_to_system:
                depends_on.append(local_to_system[item.after])

            # Implizite Sequenzierung: selber Agent ohne explicit after + nicht parallel
            if not item.after and not item.parallel:
                prev = last_per_recipient.get(item.recipient_id)
                if prev:
                    depends_on.append(prev)

            task_id = str(__import__("uuid").uuid4())
            task = item.to_task_dict(system_task_id=task_id, depends_on=depends_on)

            # System-Task-ID registrieren für spätere "after"-Referenzen
            local_to_system[item.local_id] = task_id
            last_per_recipient[item.recipient_id] = task_id

            self._task_service.enqueue(task)
            enqueued.append(item)
            logger.info(
                "TASKLIST dispatched: @%s ← '%s...' (depends_on=%s)",
                item.recipient_name, item.task_text[:60],
                [d[:8] for d in depends_on],
            )

        return enqueued

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
