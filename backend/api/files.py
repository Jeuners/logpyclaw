"""
backend/api/files.py — REST-Endpunkte für den Frontend-File-Explorer.

GET /api/fs?path=~           → Verzeichnis-Listing (JSON)
GET /api/fs/read?path=...    → Dateiinhalt (JSON, max 512KB)
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()

_BLOCKED = {"/etc/shadow", "/etc/passwd", "/proc", "/sys", "/dev"}
_MAX_PREVIEW = 512 * 1024  # 512 KB


def _safe(path: str) -> str:
    real = os.path.realpath(os.path.expanduser(path or "~"))
    for b in _BLOCKED:
        if real.startswith(b):
            raise PermissionError(f"Blocked: {real}")
    return real


def _fmt(n: int) -> str:
    if n < 1024:     return f"{n} B"
    if n < 1024**2:  return f"{n // 1024} KB"
    return f"{n // 1024 // 1024} MB"


@router.get("/api/fs")
async def list_dir(path: str = Query(default="~")):
    try:
        real = _safe(path)
        if not os.path.isdir(real):
            return JSONResponse(status_code=400, content={"error": "Not a directory"})

        raw = os.listdir(real)
        entries = sorted(
            raw,
            key=lambda x: (not os.path.isdir(os.path.join(real, x)), x.lower()),
        )
        result = []
        for name in entries[:300]:
            full = os.path.join(real, name)
            try:
                stat = os.stat(full)
                is_dir = os.path.isdir(full)
                result.append({
                    "name": name,
                    "path": full,
                    "is_dir": is_dir,
                    "size": stat.st_size if not is_dir else None,
                    "size_fmt": _fmt(stat.st_size) if not is_dir else None,
                    "mtime": stat.st_mtime,
                })
            except Exception:
                result.append({"name": name, "path": full, "is_dir": False,
                                "size": None, "size_fmt": None, "mtime": None})

        parent = os.path.dirname(real)
        return {
            "path": real,
            "parent": parent if parent != real else None,
            "entries": result,
            "total": len(raw),
        }
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/api/fs/read")
async def read_file(path: str = Query(...)):
    try:
        real = _safe(path)
        if not os.path.exists(real):
            return JSONResponse(status_code=404, content={"error": "Not found"})
        if os.path.isdir(real):
            return JSONResponse(status_code=400, content={"error": "Is a directory"})

        size = os.path.getsize(real)
        if size > _MAX_PREVIEW:
            return {"path": real, "content": None, "size": size,
                    "size_fmt": _fmt(size), "too_large": True}

        try:
            content = open(real, encoding="utf-8", errors="replace").read()
            binary = False
        except Exception:
            content = None
            binary = True

        ext = os.path.splitext(real)[1].lstrip(".").lower()
        return {
            "path": real,
            "name": os.path.basename(real),
            "ext": ext,
            "content": content,
            "size": size,
            "size_fmt": _fmt(size),
            "too_large": False,
            "binary": binary,
        }
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
