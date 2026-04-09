"""
api/backup.py — Backup und Restore von Agenten-, Provider- und Watchdog-Daten.
"""
import json
import logging
import os
import shutil
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import (
    AGENTS_FILE, BACKUP_DIR, HISTORY_FILE, PROVIDERS_FILE,
    TASKS_FILE, WATCHDOGS_FILE,
)
from core.errors import AgentClawError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["backup"])

os.makedirs(BACKUP_DIR, exist_ok=True)


class RestoreRequest(BaseModel):
    backup_name: str


@router.post("/backup")
async def create_backup():
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

    shutil.make_archive(backup_path, "zip", backup_path)
    shutil.rmtree(backup_path)
    zip_path = os.path.join(BACKUP_DIR, f"{backup_name}.zip")

    return {"ok": True, "backup_file": f"{backup_name}.zip", "path": zip_path}


@router.get("/backup/list")
async def list_backups():
    backups = []
    for f in os.listdir(BACKUP_DIR):
        if f.endswith(".zip"):
            fpath = os.path.join(BACKUP_DIR, f)
            backups.append({
                "name": f,
                "size": os.path.getsize(fpath),
                "modified": datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat(),
            })
    return sorted(backups, key=lambda x: x["modified"], reverse=True)


@router.post("/backup/restore")
async def restore_backup(body: RestoreRequest):
    zip_path = os.path.join(BACKUP_DIR, body.backup_name)
    if not os.path.exists(zip_path):
        raise AgentClawError("Backup nicht gefunden", status_code=404)

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
    return {"ok": True, "message": "Backup restored. Bitte Server neustarten."}


@router.get("/backup/download/{name}")
async def download_backup(name: str):
    # Prevent path traversal
    safe_name = os.path.basename(name)
    path = os.path.join(BACKUP_DIR, safe_name)
    if not os.path.exists(path):
        raise AgentClawError("Nicht gefunden", status_code=404)
    return FileResponse(path, filename=safe_name, media_type="application/zip")
