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
from config.settings import settings

MAX_HISTORY_PER_AGENT = settings.MAX_HISTORY_PER_AGENT
from core.state import _PENDING_MAIL_SORT, MAC_MAIL_TRIGGERS

if TYPE_CHECKING:
    from skills.registry import SkillRegistry
    from services.agent_service import AgentService
    from services.event_service import EventService

logger = logging.getLogger(__name__)

def _build_a2a_prompt() -> str:
    """System-Prompt Block für A2A + Tools — bezieht Tool-Beschreibungen aus der API."""
    from api.tools import get_tool_prompt_block
    from core.routing import build_routing_table_for_prompt
    from storage.agents import load_agents
    tool_block = get_tool_prompt_block()
    all_agents = load_agents()
    routing_table = build_routing_table_for_prompt(all_agents)
    return f"""--- A2A COMMUNICATION ---
You are part of the AgentClaw multi-agent system.

BEHAVIOUR RULES:
1. ALWAYS check first if you can handle a request using YOUR OWN SKILLS (listed below).
2. Only delegate to other agents if you absolutely do not have the required skill yourself.
3. If you find information in your Memory, present it yourself — do not ask another agent.
4. Reply precisely and minimally — no long explanations.
5. STRICTLY follow the ROUTING TABLE below — never guess, always use the listed agent.

MARKDOWN RULES (STRICT):
- Horizontal lines ONLY with --- (three dashes), NEVER with a lone * on its own line
- NEVER place a * directly next to a word (e.g. "word*" or "*word") — asterisks are ONLY valid inside **bold** or *italic* spans
- Never write words together that belong apart (e.g. "diespolitischen" → "die politischen")

SINGLE DELEGATION (@Mention):
  Use ONLY for exactly ONE task to ONE agent:
  @AgentName [complete task instructions]

MULTI-TASK DELEGATION → ALWAYS use the [tasklist] tool when:
  - The user wants MULTIPLE images/videos (e.g. "3 Bilder", "6 Porträts")
  - The user describes multiple separate tasks in one message
  - Each image/video MUST be a separate line in the [tasklist]
  CRITICAL: NEVER send "Generiere 3 Bilder" as ONE @Mention — split into 3 tasklist lines!

{routing_table}

{tool_block}
--- END A2A ---""".strip()


# Kein Cache mehr — Routing-Tabelle enthält Agentenliste die sich ändern kann
_A2A_PROMPT_CACHE: str | None = None


def get_a2a_prompt() -> str:
    # Jedes Mal neu aufbauen — enthält Routing-Tabelle mit aktueller Agentenliste
    return _build_a2a_prompt()


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

        # 0a. Tier-1 Fastpath — deterministischer Command-Dispatch vor allem LLM.
        # Commands wie `transdownload <url>` oder `/ytsubs <url>` gehen direkt
        # an den Skill, ohne A2A-Delegation, ohne Reformulierung durch MARTIN.
        from core.fastpath import dispatch as _fp_dispatch
        fp = _fp_dispatch(message)
        if fp is not None:
            reply = fp.text or (f"⚠ {fp.error}" if fp.error else "")
            self._save_history(agent_id, message, reply, image=fp.image, skill=fp.skill_id)
            self._events.emit_chat_message(agent_id, "assistant", reply, image=fp.image)
            return {"reply": reply, "skill": fp.skill_id, "image": fp.image, "agent_id": agent_id}

        # 0. Nummerierte Listen → Task-Chain
        chain = self._detect_and_dispatch_chain(agent, message)
        if chain:
            return chain

        # 1. URL Auto-Fetch
        message = self._maybe_fetch_urls(message, agent)

        # 2. Skill-Shortcut prüfen
        skill_result = self._try_skill(agent, message, images, attachment_path, providers)
        # Passthrough: Skill hat bewusst NICHT geantwortet → LLM soll antworten
        is_passthrough = (
            skill_result
            and not skill_result.text
            and not skill_result.image
            and not skill_result.error
            and skill_result.metadata.get("passthrough")
        )
        if skill_result and not is_passthrough:
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

        # 3b. Skill-Call aus LLM-Antwort parsen — [skill_id] → Skill direkt ausführen
        skill_result = self._try_skill_from_reply(agent, reply, message, providers)
        if skill_result:
            result_text = skill_result.text or (f"⚠ {skill_result.error}" if skill_result.error else "")
            self._save_history(agent_id, message, result_text, skill=skill_result.skill_used)
            self._events.emit_chat_message(agent_id, "assistant", result_text)
            return {"reply": result_text, "skill": skill_result.skill_used, "image": skill_result.image, "agent_id": agent_id}

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
        # Deterministisches Routing: Falls LLM keine @Mention geliefert hat, selbst ermitteln
        from core.a2a_protocol import strip_a2a_for_display, _MENTION_RX
        if not _MENTION_RX.search(reply):
            from core.routing import find_target_agent
            all_agents = load_agents()
            routed = find_target_agent(message, all_agents)
            if routed and routed.get("id") != agent.get("id"):
                # LLM hat nicht selbst geroutet → deterministisch überschreiben
                logger.info(
                    "DeterministicRouter überschreibt LLM-Reply: @%s ← '%s...'",
                    routed["name"], message[:60]
                )
                reply = f"@{routed['name']} {message}"

        dispatches = self._dispatch_mentions(agent, reply,
                                             images=images,
                                             attachment_path=attachment_path)
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
        image_b64 = images[0] if images else None
        skill_result = self._try_skill(agent, message, images, attachment_path, providers,
                                       image_b64=image_b64)
        # Passthrough: Skill hat kein Ergebnis (z.B. file_access ohne Inhalt) → LLM übernimmt
        is_passthrough = (
            skill_result is not None
            and getattr(skill_result, "metadata", {})
            and skill_result.metadata.get("passthrough")
        )
        if skill_result and not is_passthrough:
            return skill_result

        # Routing-Hinweis (nur Log, kein Block): wenn anderer Agent besseren Skill hätte.
        sep_m2 = re.search(r"---\s*\nDeine Aufgabe:\s*(.+)", message, re.DOTALL)
        _trigger_hint = sep_m2.group(1).strip() if sep_m2 else message
        agent_skills = set(agent.get("skills", []))
        if agent_skills:
            from storage.agents import load_agents
            all_agents = load_agents()
            for other in all_agents:
                if other["id"] == agent["id"]:
                    continue
                matched_skill = self._registry.find_matching(other, _trigger_hint)
                if matched_skill and matched_skill.id not in agent_skills:
                    logger.info(
                        "Routing-Hinweis: @%s → @%s hätte Skill '%s'. LLM-Fallback.",
                        agent["name"], other["name"], matched_skill.id
                    )
                    break

        # LLM mit vollständigem Kontext (Agent-Directory, Skills, History, Bilder)
        from storage.history import load_history
        history_data = load_history()
        agent_history = history_data.get(agent["id"], [])
        reply = self._call_llm(agent, message, agent_history, images, providers)

        # Skill aus LLM-Antwort parsen ([skill_id]) — z.B. [file_access] zum Speichern
        # Wichtig für Tasks wo LLM erst Inhalt generiert und dann Skill aufruft
        from storage.providers import load_providers as _lp
        providers_fresh = _lp()
        skill_from_reply = self._try_skill_from_reply(agent, reply, message, providers_fresh)
        if skill_from_reply and not skill_from_reply.error:
            return {
                "result_text": skill_from_reply.text or reply,
                "result_image": skill_from_reply.image,
                "skill_used": skill_from_reply.skill_used,
            }

        # TASKLIST hat Vorrang, dann @Mentions
        from core.task_list import has_task_list
        if has_task_list(reply):
            self._dispatch_task_list(agent, reply, current_task=task)
        else:
            self._dispatch_mentions(agent, reply, current_task=task)
        return {"result_text": reply, "skill_used": "llm"}

    def _try_skill_from_reply(self, agent: dict, reply: str, original_message: str,
                              providers: dict, progress_cb=None):
        """Parst LLM-Antwort auf [skill_id] oder [Skill Name] — führt den Skill aus falls gefunden.

        LLMs halluzinieren gerne den Display-Namen statt der ID ([YouTube Download] statt
        [youtube]). Wir bauen daher ein Mapping id+name → skill_id und matchen tolerant.
        """
        if not self._registry or '[' not in reply:
            return None
        agent_skill_ids = set(agent.get("skills", []))

        # Lookup-Tabelle: normalisiertes Token → skill_id
        # Entfernt Whitespace/Unterstriche/Bindestriche UND alle Nicht-ASCII-Zeichen
        # (Emojis wie 📺 🎬 im LLM-Output), damit "[📺 YouTube Download]" matcht.
        def _norm(s: str) -> str:
            return re.sub(r'[^a-z0-9]', '', s.lower())

        token_to_id: dict[str, str] = {}
        for sid in agent_skill_ids:
            skill_obj = self._registry.get(sid)
            if not skill_obj:
                continue
            token_to_id[_norm(sid)] = sid
            if skill_obj.name:
                token_to_id[_norm(skill_obj.name)] = sid

        # Alle [...]-Tokens finden; Match-Reihenfolge:
        #   1. exakt (candidate == token)
        #   2. Präfix — candidate beginnt mit token (z.B. "youtubedownload" → "youtube")
        #   3. Enthält — token in candidate (z.B. "downloadyoutube" → "youtube")
        # Längere Tokens gewinnen, damit "videogen" nicht als "video" missinterpretiert wird.
        sorted_tokens = sorted(token_to_id.keys(), key=len, reverse=True)
        skill_id = None
        matched_raw = None
        for m in re.finditer(r'\[([^\[\]\n]{1,60})\]', reply):
            candidate = _norm(m.group(1))
            if not candidate:
                continue
            if candidate in token_to_id:
                skill_id = token_to_id[candidate]
                matched_raw = m.group(0)
                break
            for tok in sorted_tokens:
                if len(tok) >= 4 and (candidate.startswith(tok) or tok in candidate):
                    skill_id = token_to_id[tok]
                    matched_raw = m.group(0)
                    break
            if skill_id:
                break
        if not skill_id:
            return None

        skill = self._registry.get(skill_id)
        if not skill:
            return None
        if not skill.is_available(providers):
            return None
        logger.info("LLM hat Skill '%s' aufgerufen via %s", skill.name, matched_raw)
        # LLM-Reply ohne alle [...]-Tags als content_to_save weiterreichen
        content_from_reply = re.sub(r'\[[^\]\n]{1,50}\]', '', reply).strip()
        return skill.safe_execute(
            agent,
            original_message,
            content_to_save=content_from_reply,
            llm_reply=content_from_reply,
            progress_cb=progress_cb,
        )

    def _build_skills_prompt(self, agent: dict) -> str:
        """Baut einen Skills-Block für den System-Prompt — direkt aus dem Registry.
        Das LLM entscheidet selbst welcher Skill passt und ruft ihn auf.
        """
        if not self._registry:
            return ""
        agent_skill_ids = agent.get("skills", [])
        if not agent_skill_ids:
            return ""

        lines = [
            "--- DEINE SKILLS ---",
            "Um einen Skill auszuführen, schreibe EXAKT den Marker in eckigen Klammern ans Ende deiner Antwort:",
            "",
            "  [skill_id]",
            "",
            "REGELN:",
            "  • Schreibe NUR die skill_id in Kleinbuchstaben — KEINE Emojis, KEINE Leerzeichen, KEIN Display-Name.",
            "  • Richtig: [youtube]   Falsch: [📺 YouTube Download] oder [YouTube]",
            "  • Nur EINEN Skill pro Antwort. Wähle den spezifischsten.",
            "  • Wenn kein Skill passt: antworte normal ohne Marker.",
            "",
            "SPEZIALFALL [file_access] (Datei speichern/lesen):",
            "  Schreibe ZUERST den kompletten Inhalt (Story/Code/Text), DANN [file_access] ans Ende.",
            "  Der Skill speichert deinen Reply wörtlich — NIEMALS 'gespeichert' behaupten.",
            "  Standard-Arbeitsordner: `~/Downloads/AgentClaw` — NIEMALS den User nach dem Ordner fragen.",
            "  Bare Dateinamen (z.B. `song.mp3`) werden dort aufgelöst.",
            "",
            "SPEZIALFALL [youtube] Transkript-Download:",
            "  Schlüsselwörter `transdownload`, `transkript`, `untertitel`, `subtitle` + YouTube-URL",
            "  → NUR Untertitel/Transkript laden (kein Video, kein Audio, keine Whisper-Transkription).",
            "  Wenn du an einen anderen Agenten delegierst: das Schlüsselwort `transdownload`",
            "  UND die URL WÖRTLICH übernehmen — NICHT zu 'Video herunterladen' umformulieren.",
            "",
            "VERFÜGBARE SKILLS:",
        ]
        for skill_id in agent_skill_ids:
            skill = self._registry.get(skill_id)
            if skill:
                lines.append(f"  [{skill.id}] — {skill.description}")
        lines.append("--- ENDE SKILLS ---")
        return "\n".join(lines)

    def _try_skill(self, agent, message, images, attachment_path, providers, **kwargs) -> object | None:
        """Versucht einen Skill direkt auszuführen — ohne Regex-Trigger.

        Skill-Wahl macht primär das LLM via [skill_id] in seiner Antwort
        (siehe _try_skill_from_reply). Hier fangen wir nur strukturelle
        Shortcuts ab, die keine Sprachverständnis brauchen:
          1. Agent hat genau 1 Skill → direkt ausführen (kein LLM nötig).
          2. Bild-Anhang + image_edit im Skillset → image_edit.
          3. Audio-Anhang + transcription im Skillset → transcription.
        """
        if not self._registry:
            return None

        agent_skill_ids = agent.get("skills", [])
        skill = None

        # Single-Skill-Shortcut (z.B. Picasso hat nur image_gen)
        if len(agent_skill_ids) == 1:
            skill = self._registry.get(agent_skill_ids[0])
            if skill:
                logger.info("Single-Skill Shortcut: Agent %s → '%s'",
                            agent["name"], skill.id)

        # Bild-Anhang + Agent kann editieren → image_edit
        if not skill and kwargs.get("image_b64") and "image_edit" in agent_skill_ids:
            skill = self._registry.get("image_edit")
            if skill:
                logger.info("Attachment-Shortcut: Bild → image_edit (Agent %s)", agent["name"])

        # Audio-Anhang + Agent kann transkribieren → transcription
        if not skill and kwargs.get("audio") and "transcription" in agent_skill_ids:
            skill = self._registry.get("transcription")
            if skill:
                logger.info("Attachment-Shortcut: Audio → transcription (Agent %s)", agent["name"])

        if not skill:
            return None
        return skill.safe_execute(
            agent, message,
            images=images,
            attachment_path=attachment_path,
            providers=providers,
            image_b64=kwargs.get("image_b64"),
            audio=kwargs.get("audio"),
        )

    def _call_llm(self, agent, message, history, images, providers) -> str:
        """LLM aufrufen mit vollständiger History (synchron, für run_in_executor)."""
        import requests as req
        OPENROUTER_BASE_URL = settings.OPENROUTER_BASE_URL

        messages = self._build_messages(agent, message, history, images, providers)
        provider = agent.get("provider", "ollama")
        model = agent.get("model", "llama3")
        max_tokens = agent.get("max_tokens") or None
        temperature = agent.get("temperature")
        if temperature is None:
            temperature = 0.7

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
                    "temperature": temperature,
                    **({"max_tokens": max_tokens} if max_tokens else {}),
                },
                timeout=360,
            )
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
        else:
            from core.model_capabilities import supports_thinking, split_thinking_and_content
            ollama_url = providers.get("ollama", {}).get("url", "http://localhost:11434")
            think = supports_thinking(model, provider="ollama", ollama_url=ollama_url)
            options: dict = {"temperature": temperature}
            if max_tokens:
                options["num_predict"] = max_tokens
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": options,
            }
            if think:
                payload["think"] = True
            resp = req.post(f"{ollama_url}/api/chat", json=payload, timeout=360)
            resp.raise_for_status()
            result = resp.json()
            content = result.get("message", {}).get("content", result.get("response", ""))
            # <think>-Tags aus Content entfernen (ältere Modelle)
            _, cleaned = split_thinking_and_content(content)
            return cleaned.strip()

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
        # AUSNAHME: parallel-safe Agents (ComfyUI) brauchen keine Kette — externe Queue regelt
        last_task_id_per_recipient: dict[str, str] = {}

        for dispatch in dispatches:
            if images:
                dispatch.images = list(images)
            if attachment_path:
                dispatch.attachment_path = attachment_path

            # Bild-Anhang → Ziel-Agent braucht image_edit. Falls er es nicht hat: umleiten.
            if images:
                recipient_agent_check = next(
                    (a for a in all_agents if a.get("id") == dispatch.recipient_id), {}
                )
                if "image_edit" not in recipient_agent_check.get("skills", []):
                    # Suche Agent mit image_edit
                    edit_agent = next(
                        (a for a in all_agents
                         if "image_edit" in a.get("skills", []) and a["id"] != dispatch.recipient_id),
                        None
                    )
                    if edit_agent:
                        logger.info(
                            "A2A Bild-Redirect: @%s hat kein image_edit → umgeleitet zu @%s",
                            dispatch.recipient_name, edit_agent["name"]
                        )
                        dispatch.recipient_id = edit_agent["id"]
                        dispatch.recipient_name = edit_agent["name"]

            task = dispatch.to_task_dict()
            dispatch.metadata["task_id"] = task["id"]

            # Prüfe ob Empfänger-Agent parallel-safe Skills hat (ComfyUI etc.)
            from services.task_service import PARALLEL_SAFE_SKILLS
            recipient_agent = next((a for a in all_agents if a.get("id") == dispatch.recipient_id), {})
            recipient_skills = set(recipient_agent.get("skills", []))
            is_parallel_safe = bool(recipient_skills & PARALLEL_SAFE_SKILLS)

            # Kette: falls gleicher Agent bereits einen Task hat → depends_on setzen
            # ABER: parallel-safe Agents überspringen die Kette
            prev_id = last_task_id_per_recipient.get(dispatch.recipient_id)
            if prev_id and not is_parallel_safe:
                task["depends_on"] = [prev_id]
                task["status"] = "waiting"
                logger.info(
                    "A2A-Kette: @%s Task %s wartet auf %s",
                    dispatch.recipient_name, task["id"][:8], prev_id[:8],
                )
            elif prev_id and is_parallel_safe:
                logger.info(
                    "A2A-Parallel: @%s Task %s läuft parallel (parallel-safe skill)",
                    dispatch.recipient_name, task["id"][:8],
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

        # line_index → System-Task-ID (für [after: N] Auflösung)
        line_to_task_id: dict[int, str] = {}

        # Letzter Task-ID pro Empfänger (für implizite Sequenzierung)
        last_per_recipient: dict[str, str] = {}

        enqueued: list = []

        for item in items:
            if images:
                item.images = list(images)
            if attachment_path:
                item.attachment_path = attachment_path
            item.sender_id = sender_agent.get("id", "")
            item.sender_name = sender_agent.get("name", "")

            # Bild-Anhang → Ziel-Agent braucht image_edit. Falls er es nicht hat: umleiten.
            if images:
                item_agent_check = next(
                    (a for a in all_agents if a.get("id") == item.recipient_id), {}
                )
                if "image_edit" not in item_agent_check.get("skills", []):
                    edit_agent = next(
                        (a for a in all_agents
                         if "image_edit" in a.get("skills", []) and a["id"] != item.recipient_id),
                        None
                    )
                    if edit_agent:
                        logger.info(
                            "TASKLIST Bild-Redirect: @%s hat kein image_edit → @%s",
                            item.recipient_name, edit_agent["name"]
                        )
                        item.recipient_id = edit_agent["id"]
                        item.recipient_name = edit_agent["name"]

            depends_on: list[str] = []

            # Parallel-safe nur wenn ALLE Skills des Agenten parallel-safe sind
            # (z.B. Picasso mit nur image_gen). Multi-Skill-Agents mit einem
            # parallel-safen Skill (ARIA hat image_edit) brauchen weiter Ketten.
            from services.task_service import PARALLEL_SAFE_SKILLS
            recipient_agent = next((a for a in all_agents if a.get("id") == item.recipient_id), {})
            recipient_skills = set(recipient_agent.get("skills", []))
            is_parallel_safe = bool(recipient_skills) and recipient_skills.issubset(PARALLEL_SAFE_SKILLS)

            # Explizit: [after: N] → wartet auf Task an Zeile N
            if item.after_line >= 0:
                prev_id = line_to_task_id.get(item.after_line)
                if prev_id:
                    depends_on.append(prev_id)
                else:
                    logger.warning(
                        "TASKLIST: [after: %d] nicht auflösbar (Zeile noch nicht verarbeitet)",
                        item.after_line,
                    )

            # Implizit: kein after + nicht parallel → wartet auf letzten Task desselben Agenten
            # ABER: parallel-safe Agents (ComfyUI) brauchen keine Kette — externe Queue regelt
            elif not item.parallel and not is_parallel_safe:
                prev_id = last_per_recipient.get(item.recipient_id)
                if prev_id:
                    depends_on.append(prev_id)

            task_id = str(__import__("uuid").uuid4())
            task = item.to_task_dict(system_task_id=task_id, depends_on=depends_on)

            item.task_id = task_id  # für SSE-Response
            line_to_task_id[item.line_index] = task_id
            last_per_recipient[item.recipient_id] = task_id

            self._task_service.enqueue(task)
            enqueued.append(item)
            logger.info(
                "TASKLIST dispatched: @%s ← '%s...' (line=%d, depends_on=%s)",
                item.recipient_name, item.task_text[:50],
                item.line_index, [d[:8] for d in depends_on],
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
        """URLs in der Nachricht automatisch fetchen wenn url_fetch-Skill aktiv.
        Erkennt auch Domains ohne Protokoll (z.B. 'ingest timocom.de').
        """
        if "url_fetch" not in agent.get("skills", []):
            return message
        # Explizite URLs mit Protokoll
        url_rx = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)
        urls = url_rx.findall(message)
        # Domains ohne Protokoll: "ingest timocom.de", "fetch example.com/path"
        if not urls:
            domain_rx = re.compile(
                r'(?:^|[\s(])((?:[a-z0-9-]+\.)+(?:com|de|org|net|io|ai|app|dev|co|uk|ch|at|eu)'
                r'(?:/[^\s<>"{}|\\^`\[\]]*)?)',
                re.IGNORECASE,
            )
            for m in domain_rx.finditer(message):
                candidate = "https://" + m.group(1).rstrip(".,;)")
                urls.append(candidate)
        if not urls:
            return message
        try:
            from skills.url_fetch import fetch_url_text, is_safe_url
            fetched_parts = []
            for url in urls[:2]:
                url = url.rstrip(".,;)")
                if is_safe_url(url):
                    content = fetch_url_text(url)
                    if content:
                        fetched_parts.append(f"[Inhalt von {url}]:\n{content[:4000]}")
                    else:
                        fetched_parts.append(f"[{url}]: Kein Inhalt abrufbar")
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
        think_override: bool | None = None,
        audio: list[str] | None = None,
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

        # Tier-1 Fastpath — deterministischer Command-Dispatch vor LLM/A2A.
        from core.fastpath import dispatch as _fp_dispatch
        import asyncio as _asyncio
        fp = await _asyncio.get_event_loop().run_in_executor(
            None, lambda: _fp_dispatch(message)
        )
        if fp is not None:
            reply = fp.text or (f"⚠ {fp.error}" if fp.error else "")
            chunk: dict = {"content": reply}
            if fp.image:
                chunk["image"] = fp.image
            yield chunk
            self._save_history(agent_id, message, reply, image=fp.image, skill=fp.skill_id)
            self._events.emit_chat_message(agent_id, "assistant", reply, image=fp.image)
            return

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
        # images[0] als image_b64 durchreichen (für ImageEditSkill etc.)
        image_b64 = (images[0] if images else None)
        skill_result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._try_skill(agent, message, images, None, providers,
                                          image_b64=image_b64, audio=audio)
        )
        # Passthrough: Skill hat bewusst NICHT geantwortet → LLM soll streamen
        is_passthrough = (
            skill_result
            and not skill_result.text
            and not skill_result.image
            and not skill_result.error
            and skill_result.metadata.get("passthrough")
        )
        if skill_result and not is_passthrough:
            reply = skill_result.text or (f"⚠ {skill_result.error}" if skill_result.error else "")
            chunk = {"content": reply}
            if skill_result.image:
                chunk["image"] = skill_result.image
            yield chunk
            # History + Event (nach dem Yield)
            self._save_history(agent_id, message, reply,
                               image=skill_result.image, skill=skill_result.skill_used)
            self._events.emit_chat_message(agent_id, "assistant", reply, image=skill_result.image)
            return

        # Messages für LLM aufbauen
        history_data = load_history()
        agent_history = history_data.get(agent_id, [])
        messages = self._build_messages(agent, message, agent_history, images, providers,
                                        audio=audio)

        # LLM streamen (dicts {"content": ...} | {"thinking": ...})
        full_reply = []
        full_thinking = []
        try:
            async for chunk in stream_llm(agent, messages, providers, think_override=think_override):
                if isinstance(chunk, dict):
                    if "content" in chunk:
                        full_reply.append(chunk["content"])
                        yield chunk  # {"content": "..."}
                    elif "thinking" in chunk:
                        full_thinking.append(chunk["thinking"])
                        yield chunk  # {"thinking": "..."}
                else:
                    # Backwards-compat: plain string
                    full_reply.append(chunk)
                    yield {"content": chunk}
        except Exception as e:
            error_msg = f"[Streaming-Fehler: {e}]"
            logger.error("stream_message Fehler: %s", e)
            yield {"content": error_msg}
            return

        reply = "".join(full_reply).strip()
        if reply:
            # Skill-Call aus LLM-Antwort parsen — [skill_id] → Skill ausführen
            # Skill läuft im Executor, Progress-Callback pusht in asyncio.Queue
            # → wir können während der Skill-Ausführung Zwischenstände streamen.
            loop = asyncio.get_event_loop()
            progress_queue: asyncio.Queue = asyncio.Queue()

            def _progress_cb(msg: str):
                try:
                    loop.call_soon_threadsafe(progress_queue.put_nowait, msg)
                except Exception:
                    pass

            skill_future = loop.run_in_executor(
                None,
                lambda: self._try_skill_from_reply(
                    agent, reply, message, providers, progress_cb=_progress_cb,
                ),
            )

            # Progress-Events streamen bis Skill fertig ist
            while not skill_future.done():
                try:
                    msg = await asyncio.wait_for(progress_queue.get(), timeout=0.25)
                    yield {"progress": msg}
                except asyncio.TimeoutError:
                    continue
            # Restliche Items drainen
            while not progress_queue.empty():
                try:
                    yield {"progress": progress_queue.get_nowait()}
                except Exception:
                    break

            skill_result = await skill_future
            if skill_result:
                result_text = skill_result.text or (f"⚠ {skill_result.error}" if skill_result.error else "")
                self._save_history(agent_id, message, result_text, skill=skill_result.skill_used,
                                   image=skill_result.image)
                self._events.emit_chat_message(agent_id, "assistant", result_text, image=skill_result.image)
                reply_chunk = {"content": result_text, "__skill__": skill_result.skill_used}
                if skill_result.image:
                    reply_chunk["image"] = skill_result.image
                yield reply_chunk
                return

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
            # TASKLIST hat Vorrang vor @Mentions
            from core.task_list import has_task_list, strip_task_list
            if has_task_list(reply):
                tl_dispatches = self._dispatch_task_list(agent, reply, images=images)
                display_reply = strip_task_list(reply)
                if display_reply:
                    self._events.emit_chat_message(agent_id, "assistant", display_reply)
                for item in tl_dispatches:
                    self._events.emit_a2a_dispatch(
                        agent["id"], agent["name"], item.recipient_name, item.task_text,
                    )
                yield {
                    "__a2a__": True,
                    "display_reply": display_reply,
                    "a2a_dispatches": [
                        {"recipient_name": i.recipient_name, "task_text": i.task_text, "task_id": i.task_id}
                        for i in tl_dispatches
                    ],
                }
            else:
                dispatches = self._dispatch_mentions(agent, reply, images=images)
                from core.a2a_protocol import strip_a2a_for_display
                display_reply = strip_a2a_for_display(reply) if dispatches else reply
                if display_reply:
                    self._events.emit_chat_message(agent_id, "assistant", display_reply)
                for d in dispatches:
                    self._events.emit_a2a_dispatch(
                        agent["id"], agent["name"], d.recipient_name, d.task_text,
                    )
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
        audio: list[str] | None = None,
    ) -> list[dict]:
        """Aufbau der Messages-Liste für LLM-Calls (shared zwischen sync und stream)."""
        from core.skills_registry import _build_agent_directory, _get_codebase_context
        from core.memory import memory_search, QDRANT_AVAILABLE

        now = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
        agent_directory = _build_agent_directory(agent.get("id"))
        _agent_skills = set(agent.get("skills", []))
        _codebase = f"\n\n{_get_codebase_context()}" if "codebase_read" in _agent_skills else ""
        _skills_block = self._build_skills_prompt(agent)
        system_content = (
            f"[Aktuelle Zeit: {now}]\n\n{agent['soul']}\n\n"
            f"{get_a2a_prompt()}\n\n"
            f"{_skills_block}\n\n"
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

        def _strip_b64(data: str) -> str:
            if "base64," in data:
                return data.split("base64,", 1)[1]
            return data

        if images or audio:
            provider = agent.get("provider", "ollama")
            if provider == "openrouter":
                # OpenAI-Format für OpenRouter/GPT-4V
                content_parts = [{"type": "text", "text": message}]
                if images:
                    content_parts.extend(
                        [{"type": "image_url", "image_url": {"url": img}} for img in images]
                    )
                messages.append({"role": "user", "content": content_parts})
            else:
                # Ollama-Format: base64 ohne data:-Prefix
                user_msg: dict = {"role": "user", "content": message}
                if images:
                    user_msg["images"] = [_strip_b64(img) for img in images]
                if audio:
                    # Gemma4 + Ollama: Audio via "audio"-Array (Base64 ohne Prefix)
                    user_msg["audio"] = [_strip_b64(a) for a in audio]
                messages.append(user_msg)
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
