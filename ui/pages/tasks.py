"""
ui/pages/tasks.py — Task-Monitoring und Multi-Agent Dispatch.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme
from ui.components.task_monitor import TaskMonitor

logger = logging.getLogger(__name__)


@ui.page("/tasks")
def tasks_page():
    apply_theme()
    create_layout("tasks")

    with ui.column().classes("w-full max-w-7xl mx-auto p-6 gap-6"):
        # Header
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Task-Monitor").classes("text-2xl font-bold")
            ui.button("+ Multi-Agent Task", icon="add", on_click=_show_dispatch_dialog) \
                .props("flat").classes("text-[#00e676]")

        # Stats
        _render_stats()

        # Task-Tabelle
        ui.label("Aktive & vergangene Tasks").classes("text-lg font-semibold")
        TaskMonitor()


def _render_stats():
    from services import get_services
    try:
        services = get_services()
        tasks = services.tasks.list_all()
        active = [t for t in tasks if t.get("status") in ("submitted", "working", "queued")]
        done = [t for t in tasks if t.get("status") == "completed"]
        failed = [t for t in tasks if t.get("status") == "failed"]

        with ui.row().classes("gap-4 w-full"):
            _stat_card("Aktiv", len(active), "bolt", "#00e676")
            _stat_card("Abgeschlossen", len(done), "check_circle", "#64b5f6")
            _stat_card("Fehlgeschlagen", len(failed), "error", "#ff5252")
            _stat_card("Gesamt", len(tasks), "list", "#808080")
    except Exception as e:
        logger.warning("Stats laden fehlgeschlagen: %s", e)


def _stat_card(label: str, value: int, icon: str, color: str):
    with ui.card().classes("p-4 flex-1"):
        with ui.row().classes("items-center gap-3"):
            ui.icon(icon).classes(f"text-3xl").style(f"color: {color}")
            with ui.column().classes("gap-0"):
                ui.label(str(value)).classes("text-2xl font-bold")
                ui.label(label).classes("text-sm text-gray-500")


def _show_dispatch_dialog():
    """Multi-Agent Task-Dispatch Dialog.

    ⚠ BEKANNTER BUG (NiceGUI 3.10 + Python 3.14):
    Die on_click-Handler unten rufen ui.notify(), dialog.close() und
    ui.navigate.reload() auf — alle drei schlagen aus Event-Handler-Kontext
    mit `AssertionError: core.loop is not None` fehl. Der Task wird zwar
    korrekt enqueued, aber die UI reagiert nicht sichtbar.
    TODO: In HTML+fetch umbauen analog zu ui/pages/memory.py / settings.py.
    Siehe CLAUDE.md → "NiceGUI core.loop Bug".
    """
    from services import get_services
    services = get_services()
    agents = services.agents.list_all()

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg p-6"):
        ui.label("Multi-Agent Task").classes("text-xl font-bold mb-4")

        agent_options = {a["name"]: a["id"] for a in agents}
        agent_select = ui.select(
            list(agent_options.keys()),
            label="Empfänger-Agent",
        ).classes("w-full")

        message_input = ui.textarea(label="Aufgabe", placeholder="Was soll der Agent tun?") \
            .classes("w-full").props("rows=4 outlined")

        with ui.row().classes("justify-end gap-2 mt-4"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Senden", on_click=lambda: _dispatch_task(
                agent_options.get(agent_select.value, ""),
                message_input.value,
                dialog,
            )).classes("text-[#00e676]").props("flat")

    dialog.open()


def _dispatch_task(agent_id: str, message: str, dialog):
    if not agent_id or not message.strip():
        ui.notify("Bitte Agent und Aufgabe angeben", type="warning")
        return
    import uuid
    from datetime import datetime, timedelta
    from services import get_services
    services = get_services()
    agent = services.agents.get(agent_id)
    if not agent:
        ui.notify("Agent nicht gefunden", type="negative")
        return
    now = datetime.now()
    task = {
        "id": str(uuid.uuid4()),
        "sender_agent_id": "user",
        "sender_agent_name": "User",
        "recipient_agent_id": agent_id,
        "recipient_agent_name": agent["name"],
        "message": message.strip(),
        "skill_used": None,
        "result_text": None,
        "result_image": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=1210)).isoformat(),
        "delegation_depth": 0,
    }
    services.tasks.enqueue(task)
    dialog.close()
    ui.notify(f"Task an {agent['name']} gesendet", type="positive")
    ui.navigate.reload()
