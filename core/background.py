"""
core/background.py — Background-Thread Helper.

Warum eigenes Modul?
- `spawn_background` hat semantisch nichts mit "Config" zu tun.
- Extraktion entkoppelt services.task_service / api.agents / services.watchdog
  von core.config (kein Import-Zyklus-Risiko beim späteren Config-Refactor).
- `core.config.spawn_background` bleibt als Re-Export bestehen, damit alte
  Importe weiter funktionieren.
"""
import threading


def spawn_background(target, *args, **kwargs):
    """
    Spawn a daemon thread for background tasks.
    Daemon=True → Thread wird automatisch beendet wenn der Haupt-Prozess endet.
    """
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
