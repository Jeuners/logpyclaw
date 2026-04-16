"""
services/heartbeat_service.py — Heartbeat und Dream-Zyklen.
Extrahiert aus app.py: run_heartbeat(), tick_heartbeats(), run_dream_for_agent().
"""
import logging
import re
import random
import subprocess
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from storage.agents import load_agents
from storage.history import load_history, save_history
from core.config import spawn_background
from core.state import MAC_MAIL_TRIGGERS

if TYPE_CHECKING:
    from services.agent_service import AgentService
    from services.event_service import EventService
    from skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_MENTION_RX = re.compile(
    r"@([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\- ]{1,40}?)(?=\s|$|[,.:!?])",
    re.UNICODE
)


class HeartbeatService:
    def __init__(self, agents: "AgentService", events: "EventService", registry: "SkillRegistry"):
        self._agents = agents
        self._events = events
        self._registry = registry
        self._task_service = None

    def set_task_service(self, ts):
        """TaskService registrieren."""
        self._task_service = ts

    def tick(self):
        """Alle Agenten mit aktivem Heartbeat prüfen und ggf. triggern."""
        agents = load_agents()
        now = datetime.now().isoformat()
        for agent in agents:
            hb = agent.get("heartbeat", {})
            if not hb.get("active"):
                continue
            next_run = hb.get("next_run", "")
            if next_run and now < next_run:
                continue
            # Nächsten Run berechnen
            interval_min = hb.get("interval_min", 60)
            next_dt = (datetime.now() + timedelta(minutes=interval_min)).isoformat()
            self._agents.patch_heartbeat(agent["id"], next_run=next_dt)
            spawn_background(self.run, agent["id"])

    def run(self, agent_id: str):
        """Heartbeat ausführen."""
        agents = load_agents()
        agent = next((a for a in agents if a["id"] == agent_id), None)
        if not agent:
            logger.warning("Heartbeat: Agent %s nicht gefunden", agent_id)
            return

        hb = agent.get("heartbeat", {})
        prompt = hb.get("prompt", "").strip() or "Kurzer Statusbericht."
        skills = set(agent.get("skills", []))

        logger.info("Heartbeat: Agent '%s' — %s", agent["name"], prompt[:60])
        self._events.activity_start(agent_id, "heartbeat", prompt[:60])

        try:
            history = load_history()
            if agent_id not in history:
                history[agent_id] = []
            ts = datetime.now().isoformat()

            if "image_gen" in skills:
                result_image, short = self._run_image_heartbeat(agent, prompt)
                from skills.comfyui import _make_thumbnail
                thumb = _make_thumbnail(result_image)
                history[agent_id].append({
                    "role": "assistant",
                    "content": "💓 **Heartbeat** — Bild generiert",
                    "task_image": thumb,
                    "ts": ts,
                    "heartbeat": True,
                })
            elif "mac_mail" in skills and MAC_MAIL_TRIGGERS.search(prompt):
                from mac_mail.skill import _run_mac_mail
                reply = _run_mac_mail(prompt)
                short = reply[:120]
                history[agent_id].append({
                    "role": "assistant",
                    "content": f"💓 **Heartbeat**\n\n{reply}",
                    "ts": ts,
                    "heartbeat": True,
                })
            else:
                from core.llm import call_agent_text
                prompt_for_llm = _MENTION_RX.sub("", prompt).strip()
                reply = call_agent_text(agent, "[Heartbeat]", prompt_for_llm)
                short = reply[:120]
                history[agent_id].append({
                    "role": "assistant",
                    "content": f"💓 **Heartbeat**\n\n{reply}",
                    "ts": ts,
                    "heartbeat": True,
                })
                # Mentions dispatchen
                clean_reply = re.sub(r"^\s*\(.*?\)\s*", "", reply, flags=re.DOTALL).strip()
                if self._task_service and _MENTION_RX.search(clean_reply or reply):
                    self._dispatch_heartbeat_mentions(agent, clean_reply or reply)

            save_history(history)
            self._agents.patch_heartbeat(agent_id, last_run=ts, last_result=short[:300])
            self._events.emit_heartbeat_result(agent_id, locals().get("reply", short))
            self._notify_macos(agent["name"], short)
            logger.info("Heartbeat done: '%s'", agent["name"])

        except Exception as e:
            logger.exception("Heartbeat Fehler '%s'", agent["name"])
        finally:
            self._events.activity_end(agent_id)

    def _run_image_heartbeat(self, agent, prompt) -> tuple:
        """Bild-Heartbeat generieren."""
        moods = ["golden hour", "blue hour", "dramatic stormy sky", "misty morning fog", "neon night light"]
        styles = ["35mm film grain", "cinematic wide angle", "hyper-realistic", "long exposure"]
        rnd = random.Random()
        img_prompt = (
            f"{prompt.rstrip('.')} — "
            f"{rnd.choice(moods)}, {rnd.choice(styles)}, "
            f"photorealistic, 4k, no text, no words"
        )
        from skills.comfyui import run_comfyui_sync
        result_image = run_comfyui_sync(img_prompt)
        return result_image, f"Bild: {img_prompt[:60]}..."

    def _dispatch_heartbeat_mentions(self, sender_agent, reply: str):
        """Mentions aus Heartbeat-Reply als Tasks dispatchen (via A2A-Protokoll)."""
        from core.a2a_protocol import parse_a2a_dispatches
        from storage.agents import load_agents
        all_agents = load_agents()
        dispatches = parse_a2a_dispatches(reply, sender_agent, all_agents)
        for dispatch in dispatches:
            dispatch.priority = 3  # Heartbeat = niedrigere Priorität als User-Chat
            task = dispatch.to_task_dict()
            self._task_service.enqueue(task)

    def _notify_macos(self, agent_name: str, short: str):
        """macOS-Benachrichtigung senden."""
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{short[:100]}" with title "💓 {agent_name}" sound name "Ping"'],
                timeout=5, capture_output=True,
            )
        except Exception:
            pass

    def run_dream(self, agent_id: str):
        """Dream-Zyklus: Memory optimieren."""
        from core.memory import run_dream_for_agent
        spawn_background(run_dream_for_agent, agent_id)
