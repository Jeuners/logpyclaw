"""
storage/nodes.py — MARTIN M2M peer node registry.
Verwaltet bekannte Peer-Nodes (andere MARTIN-Instanzen im Netzwerk).
"""
import os
import socket
from datetime import datetime, timedelta

from core.config import NODES_FILE, _read_json, _write_json
from core.state import _nodes_lock


def load_nodes() -> list:
    with _nodes_lock:
        return _read_json(NODES_FILE, [])


def save_nodes(nodes: list):
    tmp = NODES_FILE + ".tmp"
    with _nodes_lock:
        _write_json(tmp, nodes)
        os.replace(tmp, NODES_FILE)


def get_node_by_alias(alias: str) -> dict | None:
    """Findet einen Node anhand von node_id oder alias."""
    for n in load_nodes():
        if n.get("node_id") == alias or n.get("alias") == alias:
            return n
    return None


def update_node_cache(node_id: str, agent_cards: list):
    """Aktualisiert den Agent-Cache für einen Node (Thread-safe)."""
    with _nodes_lock:
        nodes = _read_json(NODES_FILE, [])
        for n in nodes:
            if n["node_id"] == node_id:
                n["agent_cache"] = agent_cards
                n["agent_cache_ttl"] = (
                    datetime.now() + timedelta(minutes=15)
                ).isoformat()
                n["last_seen"] = datetime.now().isoformat()
                n["status"] = "online"
                break
        tmp = NODES_FILE + ".tmp"
        _write_json(tmp, nodes)
        os.replace(tmp, NODES_FILE)


def mark_node_offline(node_id: str):
    """Markiert einen Node als offline (Cache bleibt erhalten)."""
    with _nodes_lock:
        nodes = _read_json(NODES_FILE, [])
        for n in nodes:
            if n["node_id"] == node_id:
                n["status"] = "offline"
                break
        tmp = NODES_FILE + ".tmp"
        _write_json(tmp, nodes)
        os.replace(tmp, NODES_FILE)


def get_self_identity(providers: dict) -> dict:
    """Gibt die eigene Node-Identität aus providers zurück (mit Defaults)."""
    m2m = providers.get("martin_m2m", {})
    hostname = socket.gethostname()
    return {
        "node_id": m2m.get("node_id") or hostname,
        "node_name": m2m.get("node_name") or hostname,
        "public_url": m2m.get("public_url") or "",
        "enabled": m2m.get("enabled", False),
    }
