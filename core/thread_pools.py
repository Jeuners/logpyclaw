"""
core/thread_pools.py — Dedizierte ThreadPoolExecutors pro Aufgabentyp.

Problem vorher:
  - api/chat.py und core/scheduler.py nutzten run_in_executor(None, ...) →
    alle landen im asyncio-Default-Pool (CPU*5 Worker, shared)
  - Heartbeat-LLM-Calls blockierten User-Chat-Calls im gleichen Pool

Lösung:
  - CHAT_POOL:      User-Chat-Anfragen (groß, user-facing, höchste Prio)
  - TASK_POOL:      A2A-Task-Processing (mittel)
  - BACKGROUND_POOL: Heartbeat, M2M, Telegram-Polling (klein, low-priority)

Usage in async context:
  loop = asyncio.get_event_loop()
  result = await loop.run_in_executor(CHAT_POOL, blocking_fn)
"""
import os
from concurrent.futures import ThreadPoolExecutor

# Anzahl Worker basierend auf CPU-Anzahl
_cpu = os.cpu_count() or 4

# User-Chat: großzügig dimensioniert — User-Anfragen sollen sofort bedient werden
CHAT_POOL = ThreadPoolExecutor(
    max_workers=max(8, _cpu * 2),
    thread_name_prefix="agentclaw-chat",
)

# A2A-Tasks: mittelgroß — parallele Agent-zu-Agent Kommunikation
TASK_POOL = ThreadPoolExecutor(
    max_workers=max(5, _cpu),
    thread_name_prefix="agentclaw-task",
)

# Background: Heartbeat, Telegram, M2M-Sync — nie den Chat blockieren
BACKGROUND_POOL = ThreadPoolExecutor(
    max_workers=3,
    thread_name_prefix="agentclaw-bg",
)


def shutdown_all(wait: bool = True):
    """Alle Pools beim App-Shutdown sauber beenden."""
    for pool in (CHAT_POOL, TASK_POOL, BACKGROUND_POOL):
        try:
            pool.shutdown(wait=wait)
        except Exception:
            pass
