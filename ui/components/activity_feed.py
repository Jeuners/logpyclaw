"""
ui/components/activity_feed.py — Live-Aktivitäten der Agenten.
"""
import logging
from nicegui import ui

logger = logging.getLogger(__name__)


class ActivityFeed:
    def __init__(self, max_items: int = 12):
        self._max_items = max_items
        self._container = ui.element("div").style(
            "display: flex; flex-direction: column; gap: 4px; width: 100%;"
        )
        self.refresh()

    def refresh(self):
        from services import get_services
        try:
            services = get_services()
            tasks = services.tasks.list_all()
            active = sorted(
                [t for t in tasks if t.get("status") in ("working", "submitted", "queued")],
                key=lambda x: x.get("created_at", ""),
                reverse=True
            )[:self._max_items]

            self._container.clear()
            with self._container:
                if not active:
                    with ui.element("div").style("padding: 16px; text-align: center;"):
                        ui.label("Keine aktiven Aufgaben").style(
                            "font-size: 12px; color: #3a5a3a; font-style: italic;"
                        )
                    return

                for task in active:
                    _render_activity_item(task)

        except Exception as e:
            logger.debug("ActivityFeed refresh: %s", e)


def _render_activity_item(task: dict):
    status = task.get("status", "?")
    agent = task.get("recipient_agent_name") or task.get("agent_name", "?")
    message = task.get("message", "")[:80]
    skill = task.get("skill_used")

    STATUS_STYLES = {
        "working":   ("#ff6b35", "autorenew"),
        "submitted": ("#64b5f6", "pending"),
        "queued":    ("#ffc107", "schedule"),
    }
    color, icon_name = STATUS_STYLES.get(status, ("#3a5a3a", "help"))
    is_working = status == "working"
    pulse = "animation: ac-pulse 1.2s ease-in-out infinite;" if is_working else ""

    with ui.element("div").style(
        "display: flex; align-items: center; gap: 8px; "
        "padding: 7px 10px; border-radius: 6px; "
        "background: rgba(10,16,12,0.97); "
        "border: 1px solid rgba(0,230,118,0.1); "
        "font-family: 'SF Mono',monospace; font-size: 11px; "
        "color: #b8d4b8;"
    ):
        ui.icon(icon_name).style(
            f"font-size: 14px; color: {color}; flex-shrink: 0; {pulse}"
        )
        ui.label(agent).style(
            f"color: {color}; font-weight: 700; min-width: 50px; "
            "white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 80px;"
        )
        ui.label("·").style("color: #182e18;")
        ui.label(message).style(
            "flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
        )
        if skill:
            ui.label(skill).style(
                "font-size: 9px; padding: 1px 5px; border-radius: 3px; "
                "background: rgba(0,230,118,0.08); color: #00e676; "
                "border: 1px solid rgba(0,230,118,0.15); flex-shrink: 0;"
            )
