"""
routes/backup.py — Backup und Restore von Agenten-, Provider- und Watchdog-Daten.
"""
import json
import os
import shutil
import zipfile
from datetime import datetime

from flask import Blueprint, jsonify, request, send_file

from core.config import (
    AGENTS_FILE, BACKUP_DIR, HISTORY_FILE, PROVIDERS_FILE,
    TASKS_FILE, WATCHDOGS_FILE,
)

bp = Blueprint("backup", __name__)

os.makedirs(BACKUP_DIR, exist_ok=True)


@bp.route("/api/backup", methods=["POST"])
def create_backup():
    """Erstellt einen vollständigen Backup des aktuellen States."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"agentclaw_backup_{timestamp}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    os.makedirs(backup_path, exist_ok=True)

    files_to_backup = [
        ("agents.json", AGENTS_FILE),
        ("providers.json", PROVIDERS_FILE),
        ("watchdogs.json", WATCHDOGS_FILE),
    ]

    for name, src_path in files_to_backup:
        dst = os.path.join(backup_path, name)
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst)

    manifest = {
        "version": "1.0",
        "created": datetime.now().isoformat(),
        "includes_history": False,
        "includes_tasks": False,
        "note": "Agenten, Provider und Watchdogs. History/Tasks ausgeschlossen wegen Größe.",
    }
    with open(os.path.join(backup_path, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    zip_path = os.path.join(BACKUP_DIR, f"{backup_name}.zip")
    shutil.make_archive(backup_path, "zip", backup_path)
    shutil.rmtree(backup_path)

    return jsonify({"ok": True, "backup_file": f"{backup_name}.zip", "path": zip_path})


@bp.route("/api/backup/list", methods=["GET"])
def list_backups():
    backups = []
    for f in os.listdir(BACKUP_DIR):
        if f.endswith(".zip"):
            fpath = os.path.join(BACKUP_DIR, f)
            backups.append({
                "name": f,
                "size": os.path.getsize(fpath),
                "modified": datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat(),
            })
    return jsonify(sorted(backups, key=lambda x: x["modified"], reverse=True))


@bp.route("/api/backup/restore", methods=["POST"])
def restore_backup():
    data = request.json
    backup_name = data.get("backup_name")

    if not backup_name:
        return jsonify({"error": "backup_name required"}), 400

    zip_path = os.path.join(BACKUP_DIR, backup_name)
    if not os.path.exists(zip_path):
        return jsonify({"error": "Backup nicht gefunden"}), 404

    extract_path = os.path.join(BACKUP_DIR, "restore_temp")
    if os.path.exists(extract_path):
        shutil.rmtree(extract_path)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_path)

    files_to_restore = [
        ("agents.json", AGENTS_FILE),
        ("history.json", HISTORY_FILE),
        ("providers.json", PROVIDERS_FILE),
        ("tasks.json", TASKS_FILE),
        ("watchdogs.json", WATCHDOGS_FILE),
    ]

    for fname, dst_path in files_to_restore:
        src = os.path.join(extract_path, fname)
        if os.path.exists(src):
            shutil.copy2(src, dst_path)

    shutil.rmtree(extract_path)

    return jsonify({"ok": True, "message": "Backup restored. Bitte Server neustarten."})


@bp.route("/api/backup/download/<name>", methods=["GET"])
def download_backup(name):
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        return jsonify({"error": "Nicht gefunden"}), 404
    return send_file(path, as_attachment=True)
