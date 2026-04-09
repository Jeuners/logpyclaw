"""
ui/layout.py — Haupt-Layout: Header-Navigation (44px).
Alle Pages nutzen create_layout() als Wrapper.
"""
import logging
from nicegui import ui

logger = logging.getLogger(__name__)


def create_layout(page_name: str = "home"):
    """Erstellt den Header. Muss am Anfang jeder Page aufgerufen werden."""
    ui.dark_mode(True)

    with ui.header().classes("items-center justify-between px-3 py-0").style(
        "height: 44px; min-height: 44px; background: #070d08; "
        "border-bottom: 1px solid #0f2010;"
    ):
        # Logo
        with ui.row().classes("items-center gap-2 shrink-0"):
            ui.icon("precision_manufacturing").style("color: #00e676; font-size: 18px;")
            with ui.column().classes("gap-0"):
                ui.html('<span class="ac-logo">AGENT CLAW</span>')

        # Navigation
        with ui.row().classes("items-center gap-1"):
            _nav_button("Home",     "home",       "/",        page_name)
            _nav_button("Chat",     "chat",       "/chat",    page_name)
            _nav_button("Tasks",    "assignment", "/tasks",   page_name)
            _nav_button("Settings", "settings",   "/settings", page_name)

        # Rechte Seite: Utilities
        with ui.row().classes("items-center gap-1 ml-auto shrink-0"):
            _icon_button("memory",  "/memory",  "Memory")
            _icon_button("backup",  "/backup",  "Backup")
            _icon_button("hub",     "/network", "M2M Netzwerk")


def _nav_button(label: str, icon_name: str, path: str, current: str):
    is_active = (
        (path == "/" and current == "home") or
        (path != "/" and current in path)
    )
    cls = "ac-nav-btn" + (" active" if is_active else "")

    with ui.button(on_click=lambda p=path: ui.navigate.to(p)) \
            .props("flat no-caps dense").classes(cls):
        with ui.row().classes("items-center gap-1"):
            ui.icon(icon_name).style("font-size: 14px;")
            ui.label(label)


def _icon_button(icon_name: str, path: str, tooltip_text: str):
    ui.button(icon=icon_name, on_click=lambda p=path: ui.navigate.to(p)) \
        .props("flat dense round") \
        .style("color: #3a5a3a;") \
        .tooltip(tooltip_text)
