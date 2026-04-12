"""
services/__init__.py — Dependency Injection Container.
Alle Services werden einmal initialisiert und als Singleton gehalten.
"""
import logging
from services.agent_service import AgentService
from services.chat_service import ChatService
from services.task_service import TaskService
from services.heartbeat_service import HeartbeatService
from services.watchdog_service import WatchdogService
from services.m2m_service import M2MService
from services.event_service import EventService
from skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

class ServiceContainer:
    def __init__(self):
        self.registry = SkillRegistry()
        self.events = EventService()
        self.agents = AgentService()
        self.tasks = TaskService(self.agents, self.events)
        self.chat = ChatService(self.registry, self.agents, self.events)
        self.heartbeat = HeartbeatService(self.agents, self.events, self.registry)
        self.watchdog = WatchdogService(self.agents, self.events)
        self.m2m = M2MService(self.agents, self.events)

        # Cross-references für bidirektionale Dependencies
        self.chat.set_task_service(self.tasks)
        self.tasks.set_dispatcher(self.chat)   # Skill-Check + LLM-Fallback für A2A-Tasks
        self.heartbeat.set_task_service(self.tasks)

        logger.info("ServiceContainer initialisiert")

    def cleanup(self):
        logger.info("ServiceContainer cleanup")

_container: ServiceContainer | None = None

def init_services() -> ServiceContainer:
    global _container
    _container = ServiceContainer()
    _register_skills(_container.registry)
    return _container

def get_services() -> ServiceContainer:
    if _container is None:
        raise RuntimeError("Services nicht initialisiert — init_services() zuerst aufrufen")
    return _container

def _register_skills(registry: SkillRegistry):
    """Alle Skill-Instanzen im Registry registrieren."""
    try:
        from skills.comfyui import ImageGenSkill, VideoGenSkill, ImageEditSkill
        registry.register(ImageGenSkill())
        registry.register(VideoGenSkill())
        registry.register(ImageEditSkill())
    except Exception as e:
        logger.warning("ComfyUI Skills nicht geladen: %s", e)

    try:
        from skills.youtube_skill import YouTubeSkill
        registry.register(YouTubeSkill())
    except Exception as e:
        logger.warning("YouTube Skill nicht geladen: %s", e)

    try:
        from skills.telegram_skill import TelegramSkill
        registry.register(TelegramSkill())
    except Exception as e:
        logger.warning("Telegram Skill nicht geladen: %s", e)

    try:
        from skills.gmail_skill import GmailSkill
        registry.register(GmailSkill())
    except Exception as e:
        logger.warning("Gmail Skill nicht geladen: %s", e)

    try:
        from skills.transcription_skill import TranscriptionSkill
        registry.register(TranscriptionSkill())
    except Exception as e:
        logger.warning("Transcription Skill nicht geladen: %s", e)

    try:
        from skills.file_skill import FileAccessSkill
        registry.register(FileAccessSkill())
    except Exception as e:
        logger.warning("File Skill nicht geladen: %s", e)

    try:
        from skills.linkedin_skill import LinkedInSkill
        registry.register(LinkedInSkill())
    except Exception as e:
        logger.warning("LinkedIn Skill nicht geladen: %s", e)

    try:
        from skills.prompt_optimize import PromptOptimizeSkill
        registry.register(PromptOptimizeSkill())
    except Exception as e:
        logger.warning("PromptOptimize Skill nicht geladen: %s", e)

    try:
        from skills.url_fetch import UrlFetchSkill
        registry.register(UrlFetchSkill())
    except Exception as e:
        logger.warning("UrlFetch Skill nicht geladen: %s", e)

    try:
        from mac_mail.skill import MacMailSkill
        registry.register(MacMailSkill())
    except Exception as e:
        logger.warning("MacMail Skill nicht geladen: %s", e)

    try:
        from skills.coding_skill import CodingSkill
        registry.register(CodingSkill())
    except Exception as e:
        logger.warning("Coding Skill nicht geladen: %s", e)

    try:
        from skills.screenshot_skill import ScreenshotSkill
        registry.register(ScreenshotSkill())
    except Exception as e:
        logger.warning("Screenshot Skill nicht geladen: %s", e)

    logger.info("Skills registriert: %s", [s.id for s in registry.all()])
