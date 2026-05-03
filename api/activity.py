"""
api/activity.py — Activity-Feed (Live-Signal pro Agent für UI-Polling).
"""
from fastapi import APIRouter

from core.state import _ACTIVITY, _activity_lock

router = APIRouter(prefix="/api", tags=["activity"])


@router.get("/activity")
async def get_activity():
    with _activity_lock:
        return dict(_ACTIVITY)
