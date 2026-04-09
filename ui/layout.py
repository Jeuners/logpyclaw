"""
ui/layout.py — Haupt-Layout: Header + Left-Drawer + Content.
Alle Pages nutzen create_layout() als Wrapper.
"""
import logging
from nicegui import ui

logger = logging.getLogger(__name__)

_current_page = "home"


def create_layout(page_name: str = "home"):
    """Erstellt das Haupt-Layout. Muss am Anfang jeder Page aufgerufen werden."""
    global _current_page
    _current_page = page_name

    # Dark Mode aktivieren
    ui.dark_mode(True)

    # Header
    with ui.header().classes("items-center justify-between px-4 py-2 gap-4"):
        # Logo
        with ui.row().classes("items-center gap-2"):
            ui.icon("precision_manufacturing").classes("text-[#00e676] text-2xl")
            ui.label("AGENT CLAW").classes("ac-logo")

        # Navigation
        with ui.row().classes("gap-1"):
            _nav_button("Home", "home", "/", page_name)
            _nav_button("Chat", "chat", "/chat", page_name)
            _nav_button("Tasks", "assignment", "/tasks", page_name)
            _nav_button("Settings", "settings", "/settings", page_name)

        # Rechte Seite: Utilities
        with ui.row().classes("gap-1 ml-auto"):
            ui.button(icon="memory", on_click=lambda: ui.navigate.to("/memory")) \
                .props("flat dense").tooltip("Memory")
            ui.button(icon="backup", on_click=lambda: ui.navigate.to("/backup")) \
                .props("flat dense").tooltip("Backup")
            ui.button(icon="hub", on_click=lambda: ui.navigate.to("/network")) \
                .props("flat dense").tooltip("M2M Netzwerk")


def _nav_button(label: str, icon: str, path: str, current: str):
    is_active = current in path or path == "/" and current == "home"
    cls = "ac-nav-btn" + (" active" if is_active else "")
    ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p)) \
        .props("flat no-caps").classes(cls)
