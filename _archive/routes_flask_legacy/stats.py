"""
routes/stats.py — Statistiken und Debug-Logging.
"""
from datetime import datetime

from flask import Blueprint, jsonify, request

import core.state as _cstate
from core.state import _TASKS, _tasks_lock
from storage.agents import load_agents

bp = Blueprint("stats", __name__)


@bp.route("/api/debug", methods=["GET", "POST"])
def toggle_debug():
    """GET = aktueller Status, POST = Debug-Logging umschalten."""
    if request.method == "POST":
        data = request.json or {}
        if "enabled" in data:
            _cstate._DEBUG_LOG = bool(data["enabled"])
        else:
            _cstate._DEBUG_LOG = not _cstate._DEBUG_LOG
        print(f"[Debug] logging {'ON' if _cstate._DEBUG_LOG else 'OFF'}", flush=True)
    return jsonify({"debug_log": _cstate._DEBUG_LOG})


@bp.route("/api/stats", methods=["GET"])
def get_stats():
    """Aggregierte Statistiken für das Dashboard."""
    with _tasks_lock:
        tasks = list(_TASKS.values())

    agents = load_agents()
    agent_map = {a["id"]: a["name"] for a in agents}

    total = len(tasks)
    status_counts = {}
    skill_counts = {}
    agent_task_counts = {}
    agent_success_counts = {}
    errors_by_agent = {}
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
                created = datetime.fromisoformat(t["created_at"])
                completed = datetime.fromisoformat(t["completed_at"])
                durations.append((completed - created).total_seconds())
            except Exception:
                pass

    avg_duration = round(sum(durations) / len(durations), 1) if durations else 0

    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    success_rate = round(completed / (completed + failed) * 100, 1) if (completed + failed) > 0 else 0

    agent_stats = []
    for name, count in sorted(agent_task_counts.items(), key=lambda x: -x[1]):
        succ = agent_success_counts.get(name, 0)
        errs = errors_by_agent.get(name, 0)
        rate = round(succ / count * 100, 1) if count > 0 else 0
        agent_stats.append({"name": name, "total": count, "completed": succ, "failed": errs, "success_rate": rate})

    top_skills = sorted(
        [{"skill": k, "count": v} for k, v in skill_counts.items()],
        key=lambda x: -x["count"],
    )

    sorted_tasks = sorted(tasks, key=lambda t: t.get("created_at", ""), reverse=True)
    for t in sorted_tasks[:10]:
        recent_tasks.append({
            "id": t["id"][:8],
            "sender": t.get("sender_agent_name", "?"),
            "recipient": t.get("recipient_agent_name", "?"),
            "skill": t.get("skill_used") or "chat",
            "status": t.get("status", "?"),
            "message": (t.get("message") or "")[:60],
            "created_at": t.get("created_at", ""),
        })

    return jsonify({
        "total_tasks": total,
        "status_counts": status_counts,
        "success_rate": success_rate,
        "avg_duration_sec": avg_duration,
        "top_skills": top_skills,
        "agent_stats": agent_stats,
        "recent_tasks": recent_tasks,
    })
