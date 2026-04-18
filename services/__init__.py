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
from services.whatsapp_watcher import WhatsAppWatcherService
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
        self.whatsapp_watcher = WhatsAppWatcherService()

        # Cross-references für bidirektionale Dependencies
        self.chat.set_task_service(self.tasks)
        self.tasks.set_dispatcher(self.chat)   # Skill-Check + LLM-Fallback für A2A-Tasks
        self.tasks.set_chat_service(self.chat) # Operator-Supervisor-Callback Re-Entry
        self.heartbeat.set_task_service(self.tasks)

        # WhatsApp Watcher starten
        self.whatsapp_watcher.start()

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

# Skill-Definitionen: (label, module_path, class_name).
# Reihenfolge = Lade-Reihenfolge. Fehler wird soft geloggt — einzelner
# Skill-Fehler soll App-Startup nicht blockieren.
_SKILL_DEFS: list[tuple[str, str, str]] = [
    ("ComfyUI/ImageGen",  "skills.comfyui",             "ImageGenSkill"),
    ("ComfyUI/VideoGen",  "skills.comfyui",             "VideoGenSkill"),
    ("ComfyUI/ImageEdit", "skills.comfyui",             "ImageEditSkill"),
    ("ComfyUI/TalkingVideo","skills.comfyui",           "TalkingVideoSkill"),
    ("YouTube",           "skills.youtube_skill",       "YouTubeSkill"),
    ("Transcription",     "skills.transcription_skill", "TranscriptionSkill"),
    ("FileAccess",        "skills.file_skill",          "FileAccessSkill"),
    ("LinkedIn",          "skills.linkedin_skill",      "LinkedInSkill"),
    ("PromptOptimize",    "skills.prompt_optimize",     "PromptOptimizeSkill"),
    ("UrlFetch",          "skills.url_fetch",           "UrlFetchSkill"),
    ("MacMail",           "mac_mail.skill",             "MacMailSkill"),
    ("Coding",            "skills.coding_skill",        "CodingSkill"),
    ("ChromeBrowser",     "skills.chrome_browser",      "ChromeBrowserSkill"),
    ("HackerNews",        "skills.hacker_news",         "HackerNewsSkill"),
    ("Tagesschau",        "skills.tagesschau",          "TagesschauSkill"),
    ("WhatsApp",          "skills.whatsapp",            "WhatsAppSkill"),
]


def _register_skills(registry: SkillRegistry):
    """Alle Skills ins Registry registrieren (soft-fail pro Skill)."""
    import importlib

    for label, module_path, class_name in _SKILL_DEFS:
        try:
            module = importlib.import_module(module_path)
            skill_cls = getattr(module, class_name)
            registry.register(skill_cls())
        except Exception as e:
            logger.warning("%s Skill nicht geladen: %s", label, e)

    logger.info("Skills registriert: %s", [s.id for s in registry.all()])
