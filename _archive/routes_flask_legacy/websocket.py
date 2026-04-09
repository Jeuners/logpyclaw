"""
routes/websocket.py — WebSocket Event Handler und Emit-Hilfsfunktionen.

Verwendung in app.py:
    from routes.websocket import register_ws_handlers, emit_agent_activity, ...
    register_ws_handlers(socketio)
"""
import uuid
from datetime import datetime

from flask import request
from flask_socketio import emit, join_room, leave_room

from core.state import _USERS
from storage.agents import load_agents


def _get_socketio():
    """Lazy import um zirkuläre Imports zu vermeiden."""
    import app as _app
    return _app.socketio


def register_ws_handlers(socketio):
    """Registriert alle @socketio.on() Handler auf der übergebenen socketio-Instanz."""

    @socketio.on("connect", namespace="/ws")
    def ws_connect():
        sid = request.sid
        _USERS[sid] = {"agent_ids": []}
        print(f"[WS] Client connected: {sid}", flush=True)
        emit("connected", {"sid": sid, "type": "connected"})

    @socketio.on("disconnect", namespace="/ws")
    def ws_disconnect():
        sid = request.sid
        _USERS.pop(sid, None)
        print(f"[WS] Client disconnected: {sid}", flush=True)

    @socketio.on("join_agent", namespace="/ws")
    def ws_join_agent(data):
        sid = request.sid
        agent_id = data.get("agent_id")
        room = f"agent_{agent_id}"
        join_room(room)
        if sid in _USERS and agent_id not in _USERS[sid]["agent_ids"]:
            _USERS[sid]["agent_ids"].append(agent_id)
        print(f"[WS] {sid} joined room {room}", flush=True)
        emit("joined", {"agent_id": agent_id, "room": room, "type": "joined"})

    @socketio.on("leave_agent", namespace="/ws")
    def ws_leave_agent(data):
        sid = request.sid
        agent_id = data.get("agent_id")
        room = f"agent_{agent_id}"
        leave_room(room)
        if sid in _USERS and agent_id in _USERS[sid]["agent_ids"]:
            _USERS[sid]["agent_ids"].remove(agent_id)
        print(f"[WS] {sid} left room {room}", flush=True)
        emit("left", {"agent_id": agent_id, "type": "left"})

    @socketio.on("join_all", namespace="/ws")
    def ws_join_all(data):
        """Join all agent rooms at once."""
        sid = request.sid
        for a in load_agents():
            room = f"agent_{a['id']}"
            join_room(room)
        _USERS[sid] = {"agent_ids": [a["id"] for a in load_agents()]}
        print(f"[WS] {sid} joined all agent rooms", flush=True)
        emit("joined_all", {"agents": [a["id"] for a in load_agents()], "type": "joined_all"})


# ─── Emit Helper Functions ───────────────────────────────────────────────────

def ws_emit(event, data, room=None, broadcast=False):
    """Emit event to specific room, or broadcast to all."""
    sio = _get_socketio()
    if room:
        sio.emit(event, data, room=room, namespace="/ws")
    elif broadcast:
        sio.emit(event, data, namespace="/ws")
    else:
        sio.emit(event, data, namespace="/ws")


def emit_agent_activity(agent_id, atype, label, status):
    """Broadcast agent activity to all subscribers."""
    try:
        data = {"agent_id": agent_id, "type": atype, "label": label, "status": status}
        _get_socketio().emit("agent_activity", data, namespace="/ws")
    except Exception as e:
        print(f"[WS] emit_agent_activity error: {e}", flush=True)


def emit_task_result(task_id, agent_id, result_text, result_image, status, error=None):
    """Send task result to relevant room and broadcast."""
    try:
        has_image = bool(result_image)
        img_size = len(result_image) // 1024 if result_image else 0
        print(
            f"[WS] emit_task_result: task={task_id}, agent={agent_id}, has_image={has_image} ({img_size}KB), status={status}",
            flush=True,
        )
        data = {
            "task_id": task_id,
            "agent_id": agent_id,
            "result_text": result_text,
            "result_image": result_image,
            "status": status,
            "error": error,
        }
        _get_socketio().emit("task_result", data, namespace="/ws")
        print("[WS] emit_task_result SUCCESS", flush=True)
    except Exception as e:
        print(f"[WS] emit_task_result error: {e}", flush=True)


def emit_chat_message(agent_id, role, content, message_id=None):
    """Broadcast a new chat message."""
    try:
        data = {
            "agent_id": agent_id,
            "role": role,
            "content": content,
            "message_id": message_id or str(uuid.uuid4()),
            "ts": datetime.now().isoformat(),
        }
        _get_socketio().emit("chat_message", data, namespace="/ws")
    except Exception as e:
        print(f"[WS] emit_chat_message error: {e}", flush=True)


def emit_heartbeat_result(agent_id, result):
    """Send heartbeat output to room and broadcast."""
    try:
        data = {
            "agent_id": agent_id,
            "result": result,
            "ts": datetime.now().isoformat(),
        }
        _get_socketio().emit("heartbeat_result", data, namespace="/ws")
    except Exception as e:
        print(f"[WS] emit_heartbeat_result error: {e}", flush=True)


def emit_error(message, room=None):
    """Send error to client(s)."""
    try:
        data = {"error": message, "ts": datetime.now().isoformat()}
        _get_socketio().emit("error", data, namespace="/ws")
    except Exception as e:
        print(f"[WS] emit_error error: {e}", flush=True)
