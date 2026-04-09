"""
ui/components/activity_feed.py — Live Activity Feed.
"""
import logging
from nicegui import ui

logger = logging.getLogger(__name__)


class ActivityFeed:
    def __init__(self):
        self._container = ui.column().classes("w-full gap-2")
        self.refresh()

    def refresh(self):
        from services import get_services
        try:
            services = get_services()
            activity = services.events.get_all_activity()
            self._container.clear()
            with self._container:
                if not activity:
                    ui.label("Keine aktiven Agenten").classes("text-gray-500 text-sm italic")
                    return
                for agent_id, info in activity.items():
                    with ui.row().classes("items-center gap-3 w-full"):
                        ui.spinner(size="xs").classes("text-[#00e676]")
                        with ui.column().classes("gap-0 flex-1"):
                            ui.label(info.get("label", "Arbeitet...")).classes("text-sm")
                            atype = info.get("type", "task")
                            since = info.get("since", "")[:16].replace("T", " ")
                            ui.label(f"{atype} • {since}").classes("text-xs text-gray-500")
        except Exception as e:
            logger.warning("ActivityFeed refresh Fehler: %s", e)
