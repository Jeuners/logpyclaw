"""
routes/inbox.py — Agent Inbox API.
"""
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from storage.agents import load_agents, save_agents

bp = Blueprint("inbox", __name__)


def _socketio():
    """Lazy import von socketio um zirkuläre Imports zu vermeiden."""
    from app import socketio
    return socketio


@bp.route("/api/agents/<agent_id>/inbox", methods=["GET"])
def get_agent_inbox(agent_id):
    agents = load_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return jsonify({"error": "Agent nicht gefunden"}), 404
    inbox = agent.get("inbox", [])
    inbox.sort(key=lambda x: (x.get("priority", 0), x.get("added_at", "")))
    return jsonify(inbox)


@bp.route("/api/agents/<agent_id>/inbox", methods=["POST"])
def add_inbox_item(agent_id):
    data = request.json or {}
    task_text = (data.get("task") or "").strip()
    if not task_text:
        return jsonify({"error": "Kein Task-Text"}), 400
    agents = load_agents()
    idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
    if idx is None:
        return jsonify({"error": "Agent nicht gefunden"}), 404
    item = {
        "id": str(uuid.uuid4()),
        "task": task_text,
        "added_by": data.get("added_by", "User"),
        "added_at": datetime.now().isoformat(),
        "priority": int(data.get("priority", 0)),
    }
    agents[idx].setdefault("inbox", []).append(item)
    save_agents(agents)
    _socketio().emit("inbox_updated", {"agent_id": agent_id, "inbox": agents[idx]["inbox"]}, namespace="/ws")
    return jsonify(item), 201


@bp.route("/api/agents/<agent_id>/inbox/<item_id>", methods=["DELETE"])
def delete_inbox_item(agent_id, item_id):
    agents = load_agents()
    idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
    if idx is None:
        return jsonify({"error": "Agent nicht gefunden"}), 404
    inbox = agents[idx].get("inbox", [])
    agents[idx]["inbox"] = [i for i in inbox if i["id"] != item_id]
    save_agents(agents)
    _socketio().emit("inbox_updated", {"agent_id": agent_id, "inbox": agents[idx]["inbox"]}, namespace="/ws")
    return jsonify({"ok": True})


@bp.route("/api/agents/<agent_id>/inbox/clear", methods=["DELETE"])
def clear_agent_inbox(agent_id):
    agents = load_agents()
    idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
    if idx is None:
        return jsonify({"error": "Agent nicht gefunden"}), 404
    agents[idx]["inbox"] = []
    save_agents(agents)
    _socketio().emit("inbox_updated", {"agent_id": agent_id, "inbox": []}, namespace="/ws")
    return jsonify({"ok": True})
