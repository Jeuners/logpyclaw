"""
api/stats.py — Statistiken und Debug-Logging.
"""
import logging
from datetime import datetime

import core.state as _cstate
from core.state import _TASKS, _tasks_lock
from fastapi import APIRouter
from pydantic import BaseModel
from storage.agents import load_agents

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["stats"])


class DebugToggle(BaseModel):
    enabled: bool | None = None


@router.get("/debug")
async def get_debug():
    return {"debug_log": _cstate._DEBUG_LOG}


@router.post("/debug")
async def toggle_debug(body: DebugToggle = None):
    if body and body.enabled is not None:
        _cstate._DEBUG_LOG = body.enabled
    else:
        _cstate._DEBUG_LOG = not _cstate._DEBUG_LOG
    logger.info("Debug logging %s", "ON" if _cstate._DEBUG_LOG else "OFF")
    return {"debug_log": _cstate._DEBUG_LOG}


@router.get("/stats")
async def get_stats():
    """Aggregierte Statistiken für das Dashboard."""
    with _tasks_lock:
        tasks = list(_TASKS.values())

    agents = load_agents()
    agent_map = {a["id"]: a["name"] for a in agents}

    total = len(tasks)
    status_counts: dict = {}
    skill_counts: dict = {}
    agent_task_counts: dict = {}
    agent_success_counts: dict = {}
    errors_by_agent: dict = {}
    recent_tasks = []
    durations = []

    for t in tasks:
        s = t.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

        skill = t.get("skill_used") or "chat"
        skill_counts[skill] = skill_counts.get(skill, 0) + 1

        rid = t.get("recipient_agent_id", "")
        rname = t.get("recipient_agent_name") or agent_map.get(rid, rid)
        agent_task_counts[rname] = agent_task_counts.get(rname, 0) + 1
        if s == "completed":
            agent_success_counts[rname] = agent_success_counts.get(rname, 0) + 1
        elif s == "failed":
            errors_by_agent[rname] = errors_by_agent.get(rname, 0) + 1

        if t.get("created_at") and t.get("completed_at"):
            try:
                created   = datetime.fromisoformat(t["created_at"])
                completed = datetime.fromisoformat(t["completed_at"])
                durations.append((completed - created).total_seconds())
            except Exception:
                pass

    avg_duration = round(sum(durations) / len(durations), 1) if durations else 0
    completed_n  = status_counts.get("completed", 0)
    failed_n     = status_counts.get("failed", 0)
    success_rate = round(completed_n / (completed_n + failed_n) * 100, 1) if (completed_n + failed_n) > 0 else 0

    agent_stats = []
    for name, count in sorted(agent_task_counts.items(), key=lambda x: -x[1]):
        succ = agent_success_counts.get(name, 0)
        errs = errors_by_agent.get(name, 0)
        rate = round(succ / count * 100, 1) if count > 0 else 0
        agent_stats.append({
            "name": name, "total": count,
            "completed": succ, "failed": errs, "success_rate": rate,
        })

    top_skills = sorted(
        [{"skill": k, "count": v} for k, v in skill_counts.items()],
        key=lambda x: -x["count"],
    )

    for t in sorted(tasks, key=lambda t: t.get("created_at", ""), reverse=True)[:10]:
        recent_tasks.append({
            "id": t["id"][:8],
            "sender":    t.get("sender_agent_name", "?"),
            "recipient": t.get("recipient_agent_name", "?"),
            "skill":     t.get("skill_used") or "chat",
            "status":    t.get("status", "?"),
            "message":   (t.get("message") or "")[:60],
            "created_at": t.get("created_at", ""),
        })

    return {
        "total_tasks":      total,
        "status_counts":    status_counts,
        "success_rate":     success_rate,
        "avg_duration_sec": avg_duration,
        "top_skills":       top_skills,
        "agent_stats":      agent_stats,
        "recent_tasks":     recent_tasks,
    }
