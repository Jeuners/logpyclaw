"""
ui/components/task_monitor.py — Task-Status Tabelle (Live).
"""
import logging
from nicegui import ui

logger = logging.getLogger(__name__)

STATUS_COLORS = {
    "submitted": "blue",
    "working": "orange",
    "queued": "grey",
    "completed": "green",
    "failed": "red",
    "canceled": "grey",
}


class TaskMonitor:
    def __init__(self):
        columns = [
            {"name": "status", "label": "Status", "field": "status", "align": "left"},
            {"name": "agent", "label": "Agent", "field": "agent", "align": "left"},
            {"name": "message", "label": "Aufgabe", "field": "message", "align": "left"},
            {"name": "skill", "label": "Skill", "field": "skill", "align": "left"},
            {"name": "created", "label": "Zeit", "field": "created", "align": "left"},
        ]
        self._table = ui.table(columns=columns, rows=[], row_key="id") \
            .classes("w-full").props("dense flat")
        ui.timer(3.0, self.refresh)

    def refresh(self):
        from services import get_services
        try:
            services = get_services()
            tasks = services.tasks.list_all()
            rows = []
            for t in sorted(tasks, key=lambda x: x.get("created_at", ""), reverse=True)[:50]:
                rows.append({
                    "id": t["id"],
                    "status": t.get("status", "?"),
                    "agent": t.get("recipient_agent_name", "?"),
                    "message": t.get("message", "")[:60],
                    "skill": t.get("skill_used") or "-",
                    "created": t.get("created_at", "")[:16].replace("T", " "),
                })
            self._table.rows = rows
            self._table.update()
        except Exception as e:
            logger.warning("TaskMonitor refresh Fehler: %s", e)
