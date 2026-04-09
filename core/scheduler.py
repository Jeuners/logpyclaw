"""
core/scheduler.py — Zentraler Background-Scheduler.
Extrahiert aus app.py: scheduler_loop() + alle tick_*() Funktionen.
Läuft als asyncio.Task in NiceGUI's Eventloop.
"""
import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services import ServiceContainer

from core.thread_pools import BACKGROUND_POOL

logger = logging.getLogger(__name__)


def _tick_linkedin_scheduled():
    """LinkedIn geplante Posts verarbeiten (wird im Executor aufgerufen)."""
    try:
        from skills.linkedin_skill import process_scheduled_posts
        from storage.providers import load_providers
        process_scheduled_posts(load_providers())
    except Exception as e:
        logger.warning("LinkedIn Scheduler Fehler: %s", e)


class Scheduler:
    def __init__(self, container: "ServiceContainer"):
        self._container = container
        self._running = False
        self._tick = 0

    async def run(self):
        self._running = True
        logger.info("Scheduler gestartet")
        loop = asyncio.get_event_loop()
        while self._running:
            await asyncio.sleep(5)  # Schnelles Tick alle 5s
            self._tick += 1
            try:
                # Jedes Tick (5s): Queue-Check (schnell, kein blocking)
                self._container.tasks.tick_queue()
                # tick_telegram() ist synchron — in Background-Pool, nicht Chat-Pool
                loop.run_in_executor(BACKGROUND_POOL, self._container.chat.tick_telegram)

                # Langsames Tick (60s = jedes 12. Tick)
                if self._tick % 12 == 0:
                    # heartbeat.tick() + watchdog.tick() spawnen nur Threads (schnell)
                    self._container.heartbeat.tick()
                    self._container.watchdog.tick()
                    # m2m + linkedin → Background-Pool (HTTP, langsam)
                    loop.run_in_executor(BACKGROUND_POOL, self._container.m2m.tick_peers)
                    loop.run_in_executor(BACKGROUND_POOL, _tick_linkedin_scheduled)
                    self._container.events.activity_cleanup()
                    self._container.tasks.cleanup_old()

                # Stündliche Event-Log-Rotation (alle 720 Ticks = 60min)
                if self._tick % 720 == 0:
                    loop.run_in_executor(BACKGROUND_POOL, self._container.events.rotate_log)

            except Exception:
                logger.exception("Scheduler-Tick %d fehlgeschlagen", self._tick)

    def stop(self):
        self._running = False
        logger.info("Scheduler gestoppt")


_scheduler: Scheduler | None = None


async def start_scheduler():
    """Wird von app.py als on_startup aufgerufen."""
    global _scheduler
    from services import get_services
    container = get_services()
    _scheduler = Scheduler(container)
    asyncio.create_task(_scheduler.run())
    logger.info("Scheduler-Task erstellt")


def stop_scheduler():
    if _scheduler:
        _scheduler.stop()
