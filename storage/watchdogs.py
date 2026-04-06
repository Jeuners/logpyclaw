"""
storage/watchdogs.py — Watchdog-Konfiguration laden und speichern.
"""
from core.config import WATCHDOGS_FILE, _read_json, _write_json
from core.state import _watchdogs_lock


def load_watchdogs() -> list:
    with _watchdogs_lock:
        return _read_json(WATCHDOGS_FILE, [])


def save_watchdogs(watchdogs: list):
    with _watchdogs_lock:
        _write_json(WATCHDOGS_FILE, watchdogs)


def update_watchdog_field(wd_id: str, **kwargs):
    watchdogs = load_watchdogs()
    for wd in watchdogs:
        if wd["id"] == wd_id:
            wd.update(kwargs)
            break
    save_watchdogs(watchdogs)
