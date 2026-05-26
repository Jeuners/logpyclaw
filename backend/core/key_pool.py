"""
backend/core/key_pool.py — Round-Robin-Key-Pool für externe APIs.

Verteilt API-Calls gleichmäßig über alle konfigurierten Keys,
sodass Rate-Limits mehrerer Accounts addiert werden.
"""

from __future__ import annotations

import itertools
import threading


class KeyPool:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self._cycle = itertools.cycle(keys) if keys else None
        self._lock = threading.Lock()

    def next(self) -> str:
        if not self._cycle:
            return ""
        with self._lock:
            return next(self._cycle)

    def __len__(self) -> int:
        return len(self._keys)

    def __bool__(self) -> bool:
        return bool(self._keys)


_groq_pool: KeyPool | None = None
_pool_lock = threading.Lock()


def get_groq_key() -> str:
    global _groq_pool
    with _pool_lock:
        if _groq_pool is None:
            from backend.config import get_settings
            _groq_pool = KeyPool(get_settings().groq_key_pool)
    return _groq_pool.next()


def groq_pool_size() -> int:
    global _groq_pool
    if _groq_pool is None:
        from backend.config import get_settings
        _groq_pool = KeyPool(get_settings().groq_key_pool)
    return len(_groq_pool)
