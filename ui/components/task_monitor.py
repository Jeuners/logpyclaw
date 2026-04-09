"""
ui/components/task_monitor.py — Task-Status Tabelle (Live).
"""
import logging
from nicegui import ui

logger = logging.getLogger(__name__)

STATUS_ICON = {
    "submitted": ("pending",       "#64b5f6"),
    "queued":    ("schedule",      "#ffc107"),
    "working":   ("autorenew",     "#ff6b35"),
    "completed": ("check_circle",  "#00e676"),
    "failed":    ("error",         "#ef4444"),
    "canceled":  ("cancel",        "#3a5a3a"),
    "rejected":  ("block",         "#ef4444"),
}


class TaskMonitor:
    def __init__(self):
        self._container = ui.element("div").style("width: 100%;")
        self.refresh()
        ui.timer(3.0, self.refresh)

    def refresh(self):
        from services import get_services
        try:
            services = get_services()
            tasks = services.tasks.list_all()
            tasks_sorted = sorted(
                tasks,
                key=lambda x: x.get("created_at", ""),
                reverse=True
            )[:40]

            self._container.clear()
            with self._container:
                if not tasks_sorted:
                    with ui.element("div").style(
                        "text-align: center; padding: 32px; color: #3a5a3a;"
                    ):
                        ui.icon("task_alt").style("font-size: 36px; color: #182e18;")
                        ui.label("Keine Tasks vorhanden").style(
                            "display: block; margin-top: 8px; font-size: 13px;"
                        )
                    return

                # Header-Zeile
                with ui.element("div").style(
                    "display: grid; "
                    "grid-template-columns: 100px 130px 1fr 80px 130px; "
                    "gap: 8px; padding: 8px 12px; "
                    "border-bottom: 1px solid #0f2010; "
                    "font-size: 10px; color: #3a5a3a; "
                    "text-transform: uppercase; letter-spacing: 0.8px; "
                    "font-family: 'SF Mono',monospace;"
                ):
                    for col in ["Status", "Agent", "Aufgabe", "Skill", "Zeit"]:
                        ui.label(col)

                # Task-Zeilen
                for t in tasks_sorted:
                    _render_task_row(t)

        except Exception as e:
            logger.warning("TaskMonitor refresh Fehler: %s", e)


def _render_task_row(task: dict):
    status = task.get("status", "?")
    icon_name, icon_color = STATUS_ICON.get(status, ("help", "#3a5a3a"))
    agent_name = task.get("recipient_agent_name") or task.get("agent_name", "?")
    message = task.get("message", "")[:60]
    skill = task.get("skill_used") or "-"
    created = task.get("created_at", "")[:16].replace("T", " ")

    with ui.element("div").style(
        "display: grid; "
        "grid-template-columns: 100px 130px 1fr 80px 130px; "
        "gap: 8px; padding: 8px 12px; "
        "border-bottom: 1px solid #0a150a; "
        "align-items: center; transition: background .1s;"
    ).classes("task-row"):
        # Status
        with ui.row().style("align-items: center; gap: 4px;"):
            ui.icon(icon_name).style(f"font-size: 14px; color: {icon_color};")
            ui.label(status).style(
                f"font-size: 10px; color: {icon_color}; "
                f"font-family: 'SF Mono',monospace;"
            )

        # Agent
        ui.label(agent_name).style(
            "font-size: 12px; color: #b8d4b8; overflow: hidden; "
            "text-overflow: ellipsis; white-space: nowrap;"
        )

        # Nachricht
        ui.label(message).style(
            "font-size: 12px; color: #3a5a3a; overflow: hidden; "
            "text-overflow: ellipsis; white-space: nowrap;"
        )

        # Skill
        if skill and skill != "-":
            ui.label(skill).style(
                "font-size: 10px; font-family: 'SF Mono',monospace; "
                "padding: 1px 5px; border-radius: 3px; "
                "background: rgba(0,230,118,0.08); color: #00e676; "
                "border: 1px solid rgba(0,230,118,0.15);"
            )
        else:
            ui.label("-").style("font-size: 11px; color: #3a5a3a;")

        # Zeit
        ui.label(created).style(
            "font-size: 10px; color: #3a5a3a; "
            "font-family: 'SF Mono',monospace;"
        )

    ui.add_css("""
        .task-row:hover { background: #0a150a !important; }
    """)
