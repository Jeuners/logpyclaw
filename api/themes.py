"""
api/themes.py — Theme-Management API.
"""
from fastapi import APIRouter, HTTPException
from ui.theme import list_themes, set_active_theme

router = APIRouter(prefix="/api/themes", tags=["themes"])


@router.get("")
def get_themes():
    return list_themes()


@router.put("/{name}")
def activate_theme(name: str):
    if not set_active_theme(name):
        raise HTTPException(404, f"Theme '{name}' nicht gefunden")
    return {"ok": True, "active": name}
