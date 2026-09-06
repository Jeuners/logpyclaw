"""Monotone Laufzeitmessung; unabhängig von CDC und historischen Signaturen."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from backend.core.protocol import Message, MessageType

_CURRENT: ContextVar[CallTiming | None] = ContextVar("call_timing", default=None)


def agent_identity(agent: Any) -> dict[str, str]:
    """Bindet Messwerte an Modell und Konfiguration, ohne Zugangsdaten auszugeben."""
    provider = str(getattr(agent, "provider", type(agent).__name__))
    model = str(getattr(agent, "model", getattr(agent, "_model", "")))
    endpoint = str(getattr(agent, "ollama_url", getattr(agent, "_bin", provider)))
    skill = getattr(agent, "_skill", None)
    skill_config = (
        {
            key: value
            for key, value in vars(skill).items()
            if isinstance(value, (str, int, float, bool, type(None)))
        }
        if skill is not None
        else {}
    )
    if skill is not None:
        endpoint = str(getattr(skill, "endpoint", getattr(skill, "_endpoint", endpoint)))
    backend_id = hashlib.sha256(f"{provider}|{endpoint}".encode()).hexdigest()[:16]
    config = {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "temperature": getattr(agent, "temperature", None),
        "max_tokens": getattr(agent, "max_tokens", None),
        "reasoning_max_tokens": getattr(agent, "reasoning_max_tokens", None),
        "model_revision": getattr(agent, "model_revision", None),
        "soul": getattr(agent, "soul", getattr(agent, "_goal", "")),
        "skill_config": skill_config,
    }
    config_id = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    return {"provider": provider, "model": model, "backend_id": backend_id, "config_id": config_id}


def _union_duration(intervals: list[tuple[float, float]]) -> float:
    end = -math.inf
    total = 0.0
    for start, stop in sorted(intervals):
        total += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return total


@dataclass
class CallTiming:
    """Messgrenzen eines Handler-Aufrufs, inklusive geschachtelter Wartephasen."""

    clock: Callable[[], float]
    started: float
    handle_started: float
    intervals: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    routing: dict[str, Any] | None = None

    def finish(self, finished: float) -> dict[str, Any]:
        model = self.intervals.get("model", [])
        delegation = self.intervals.get("delegation", [])
        tool = self.intervals.get("tool", [])
        handle_s = max(0.0, finished - self.handle_started)
        result = {
            "schema_version": 1,
            "total_s": max(0.0, finished - self.started),
            "prepare_s": max(0.0, self.handle_started - self.started),
            "handle_s": handle_s,
            "model_request_s": _union_duration(model),
            "delegation_wait_s": _union_duration(delegation),
            "tool_request_s": _union_duration(tool),
            "other_handle_s": max(0.0, handle_s - _union_duration(model + delegation + tool)),
        }
        if self.routing is not None:
            result["routing"] = self.routing
        return result


def current_timing() -> CallTiming | None:
    return _CURRENT.get()


@contextmanager
def timing_span(kind: str) -> Iterator[None]:
    """Misst einen beobachtbaren Request/Wait, keine geschätzte CPU-Zeit."""
    current = _CURRENT.get()
    if current is None:
        yield
        return
    started = current.clock()
    try:
        yield
    finally:
        current.intervals.setdefault(kind, []).append((started, current.clock()))


class TimingRegistry:
    """Begrenzte prozesslokale Messreihen pro Agent und Konfigurationsepoche."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        window: int = 64,
        max_age_s: float = 1800,
        min_samples: int = 3,
    ) -> None:
        if (
            window < 1
            or min_samples < 1
            or min_samples > window
            or not math.isfinite(max_age_s)
            or max_age_s <= 0
        ):
            raise ValueError("invalid timing window, age or minimum sample count")
        self.clock = clock or time.monotonic
        self.window = window
        self.max_age_s = max_age_s
        self.min_samples = min_samples
        self._epochs: dict[str, tuple[dict[str, str], deque]] = {}

    def forget(self, agent_id: str) -> None:
        self._epochs.pop(agent_id, None)

    def _epoch(self, agent: Any) -> tuple[dict[str, str], deque]:
        identity = agent_identity(agent)
        existing = self._epochs.get(agent.agent_id)
        if existing is None or existing[0] != identity:
            existing = (identity, deque(maxlen=self.window))
            self._epochs[agent.agent_id] = existing
        return existing

    async def observe(
        self,
        agent: Any,
        operation: Callable[[], Awaitable[Message]],
        *,
        started: float | None = None,
    ) -> Message:
        """Erfasst auch Fehler/Abbrüche, ohne sie als schnelle Erfolge zu verwenden."""
        identity, samples = self._epoch(agent)
        begin = self.clock()
        call = CallTiming(self.clock, begin if started is None else started, begin)
        token = _CURRENT.set(call)
        success = False
        response = None
        try:
            response = await operation()
            qc = response.payload.get("_qc")
            success = response.type == MessageType.RESPONSE and (
                qc is None or isinstance(qc, dict) and qc.get("passed", True)
            )
            return response
        finally:
            finished = self.clock()
            measured = call.finish(finished)
            measured["identity"] = dict(identity)
            measured["success"] = bool(success)
            samples.append((finished, bool(success), measured))
            if response is not None:
                response.payload["_timing"] = measured
            _CURRENT.reset(token)

    def summary(self, agent: Any) -> dict[str, Any]:
        identity, samples = self._epoch(agent)
        now = self.clock()
        recent = [s for s in samples if 0 <= now - s[0] <= self.max_age_s]
        successful = [s for s in recent if s[1]]
        durations = sorted(s[2]["total_s"] for s in successful)
        n = len(durations)
        status = (
            "ready"
            if n >= self.min_samples
            else "insufficient"
            if n
            else "stale"
            if samples and not recent
            else "unknown"
        )
        return {
            "identity": dict(identity),
            "status": status,
            "samples": n,
            "attempts": len(recent),
            "failures": sum(not s[1] for s in recent),
            "window": self.window,
            "max_age_s": self.max_age_s,
            "age_s": max(0.0, now - successful[-1][0]) if successful else None,
            "median_s": statistics.median(durations) if n else None,
            "p90_s": durations[math.ceil(0.9 * n) - 1] if n else None,
            "range_s": [durations[0], durations[-1]] if n else None,
        }


def routing_context(
    registry: TimingRegistry, agents: Sequence[Any], *, enabled: bool, max_chars: int = 2400
) -> tuple[str, list[dict[str, Any]]]:
    """Begrenzte Beobachtungen; Eignung und explizite Ziele haben immer Vorrang."""
    if not enabled:
        return "", []
    header = (
        "Gemessene Aktionslatenzen (gesamte Dispatch-Zeit bis Handler-Ende):\n"
        "Nur bei fachlich geeigneten Alternativen berücksichtigen. Explizite Ziele und "
        "Routing-Regeln gehen vor. Unterschiedliche Aufgaben sind nicht direkt vergleichbar. "
        "Keine Deadline-Garantie: p90/Spanne sind Beobachtungen, keine Konfidenzintervalle. "
        "Fehlende, alte oder wenige Messwerte bedeuten unbekannt, nicht schnell.\n"
    )
    if max_chars < len(header) + 100:
        return "", []
    block = header
    evidence = []
    # Verfügbare Messreihen zuerst, ohne Agenten anhand ihrer Geschwindigkeit
    # vorzusortieren. Unbekannte dürfen den begrenzten Block nicht auffüllen.
    summaries = [(agent, registry.summary(agent)) for agent in agents]
    summaries.sort(key=lambda pair: pair[1]["status"] != "ready")
    for agent, summary in summaries:
        # JSON escaping keeps agent/model names on one line even with unusual config.
        label = json.dumps(agent.agent_id, ensure_ascii=True)[:100]
        model = json.dumps(summary["identity"]["model"], ensure_ascii=True)[:100]
        line = f"- {label} Modell={model} Backend={summary['identity']['backend_id']}: "
        if summary["status"] == "ready":
            low, high = summary["range_s"]
            line += (
                f"Median={summary['median_s']:.3f}s p90={summary['p90_s']:.3f}s "
                f"Spanne={low:.3f}..{high:.3f}s n={summary['samples']} "
                f"Alter={summary['age_s']:.1f}s Fehler={summary['failures']}\n"
            )
        else:
            line += f"unbekannt ({summary['status']}, n={summary['samples']})\n"
        if len(block) + len(line) > max_chars:
            break
        block += line
        evidence.append({"agent_id": agent.agent_id, **summary})
    return block, evidence
