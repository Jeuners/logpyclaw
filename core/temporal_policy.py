"""
core/temporal_policy.py — Re-Synchronisations-Policy (§4.3).

Definiert wann ein Task mit dilatierter Eigenzeit *trotzdem* ausgeführt werden
darf, und wann er besser abgelehnt wird. Drei Modi:

- ``LOG_ONLY``       : Drift wird nur protokolliert, Task läuft weiter.
- ``REJECT_ON_DRIFT``: Drift > Threshold → Task scheitert kontrolliert.
- ``RECALIBRATE``    : Drift > Threshold → Task wird mit aktualisiertem
                       Reference-Now neu eingereiht (Caller-Verantwortung).

Default-Policy aus §4.3:
- Read-only Skills + LLM-Antworten   → LOG_ONLY
- Side-Effect-Skills (Email, Posts)  → REJECT_ON_DRIFT (Threshold 600s)

Side-Effect-Liste ist konservativ — nur Skills, deren Aktion *sichtbar nach
außen* dringt (Mail-Versand, Social-Posts, externe APIs mit Mutation).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Iterable, Optional


class TemporalPolicy(Enum):
    LOG_ONLY = "log_only"
    REJECT_ON_DRIFT = "reject_on_drift"
    RECALIBRATE = "recalibrate"


# Skills mit *externen Seiteneffekten* — Default REJECT_ON_DRIFT (§4.3, „for
# actions with external side effects, the proposed default is reject-on-drift-
# above-threshold"). Konservativ: nur klare Send-Aktionen, kein Read-only.
SIDE_EFFECT_SKILLS: frozenset[str] = frozenset({
    "gmail",
    "mac_mail",
    "telegram",
    "linkedin",
    "whatsapp",
})

# Default-Schwellwert in Sekunden. ARIA-Chats laufen in <60s, Heartbeats in
# Minuten — 600s gibt LLM-Reasoning genug Spielraum, fängt aber Tasks ab,
# die stundenalte reference_now haben (z.B. wenn ein Operator einen Plan
# überraschend spät freigibt).
DEFAULT_DRIFT_THRESHOLD_SECONDS: float = 600.0


def drift_seconds(reference: Optional[str], wall: Optional[str]) -> Optional[float]:
    """|wall − reference| in Sekunden. None bei fehlenden/kaputten Werten."""
    if not reference or not wall:
        return None
    try:
        d = (datetime.fromisoformat(wall) - datetime.fromisoformat(reference)).total_seconds()
        return abs(d)
    except (ValueError, TypeError):
        return None


def agent_has_side_effect_skill(agent: dict, side_effect_skills: Iterable[str] = SIDE_EFFECT_SKILLS) -> bool:
    """True wenn der Agent mindestens ein Side-Effect-Skill aktiv hat."""
    if not agent:
        return False
    skills = set(agent.get("skills") or [])
    return bool(skills & set(side_effect_skills))


def policy_for_task(
    task: dict,
    agent: dict | None = None,
    side_effect_skills: Iterable[str] = SIDE_EFFECT_SKILLS,
) -> TemporalPolicy:
    """Bestimmt die Policy für einen Task.

    Vorrang:
    1. Task-Feld ``temporal_policy`` (wenn vom Caller explizit gesetzt)
    2. Agent hat Side-Effect-Skill → REJECT_ON_DRIFT
    3. Default → LOG_ONLY
    """
    explicit = task.get("temporal_policy")
    if explicit:
        try:
            return TemporalPolicy(explicit)
        except ValueError:
            pass
    if agent_has_side_effect_skill(agent or {}, side_effect_skills):
        return TemporalPolicy.REJECT_ON_DRIFT
    return TemporalPolicy.LOG_ONLY


def evaluate_drift(
    task: dict,
    agent: dict | None = None,
    threshold_seconds: float = DEFAULT_DRIFT_THRESHOLD_SECONDS,
    side_effect_skills: Iterable[str] = SIDE_EFFECT_SKILLS,
    now: Optional[datetime] = None,
) -> dict:
    """Wertet die Drift-Situation für einen Task aus.

    Returns:
        {
          "policy":         TemporalPolicy,
          "drift_seconds":  float | None,
          "threshold":      float,
          "should_reject":  bool,
          "reason":         str,            # human-readable
        }
    """
    policy = policy_for_task(task, agent, side_effect_skills)
    ref = task.get("reference_now")
    wall = (now or datetime.now()).isoformat()
    d = drift_seconds(ref, wall)
    should_reject = (
        policy == TemporalPolicy.REJECT_ON_DRIFT
        and d is not None
        and d > threshold_seconds
    )
    if d is None:
        reason = "no reference_now — task pre-dates eigenzeit instrumentation"
    elif should_reject:
        reason = (
            f"drift {d:.1f}s exceeds {threshold_seconds:.0f}s threshold "
            f"for side-effect skill ({policy.value})"
        )
    else:
        reason = (
            f"drift {d:.1f}s within tolerance ({policy.value}, "
            f"threshold {threshold_seconds:.0f}s)"
        )
    return {
        "policy": policy,
        "drift_seconds": d,
        "threshold": threshold_seconds,
        "should_reject": should_reject,
        "reason": reason,
    }
