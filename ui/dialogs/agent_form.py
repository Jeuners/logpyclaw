"""
ui/dialogs/agent_form.py — Agent erstellen / bearbeiten Dialog.
"""
import logging
from nicegui import ui
from typing import Callable

logger = logging.getLogger(__name__)


class AgentFormDialog:
    def __init__(self, agent: dict | None = None, on_save: Callable | None = None):
        self._agent = agent
        self._on_save = on_save
        self._show()

    def _show(self):
        is_edit = self._agent is not None
        title = "Agent bearbeiten" if is_edit else "Neuer Agent"
        a = self._agent or {}

        from services import get_services
        services = get_services()
        all_skills = services.registry.all()

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl p-6"):
            ui.label(title).classes("text-xl font-bold mb-4")

            with ui.column().classes("w-full gap-4"):
                name_input = ui.input("Name", value=a.get("name", "")) \
                    .classes("w-full").props("outlined")
                role_input = ui.input("Rolle", value=a.get("role", ""),
                                      placeholder="z.B. Redakteur, Forscher, Artist") \
                    .classes("w-full").props("outlined")
                soul_input = ui.textarea("System-Prompt (Soul)",
                                         value=a.get("soul", ""),
                                         placeholder="Beschreibe die Persönlichkeit und Aufgaben des Agenten...") \
                    .classes("w-full").props("rows=5 outlined")

                with ui.row().classes("gap-4 w-full"):
                    model_input = ui.input("Modell", value=a.get("model", "llama3")) \
                        .classes("flex-1").props("outlined")
                    provider_select = ui.select(
                        ["ollama", "openrouter", "mistral", "google"],
                        label="Provider",
                        value=a.get("provider", "ollama"),
                    ).classes("flex-1")

                # Farbe
                color_input = ui.color_input("Farbe", value=a.get("color", "#00e676")) \
                    .classes("w-full")

                # Skills
                ui.label("Skills").classes("text-sm font-semibold")
                enabled_skills = set(a.get("skills", []))
                skill_checks = {}
                with ui.row().classes("flex-wrap gap-2"):
                    for skill in all_skills:
                        checked = skill.id in enabled_skills
                        cb = ui.checkbox(skill.name, value=checked)
                        skill_checks[skill.id] = cb

                # Favorit
                fav_check = ui.checkbox("Favorit", value=a.get("favorite", False))

            with ui.row().classes("justify-end gap-2 mt-6"):
                ui.button("Abbrechen", on_click=dialog.close).props("flat")
                ui.button("Speichern", on_click=lambda: self._save(
                    dialog,
                    name_input.value,
                    role_input.value,
                    soul_input.value,
                    model_input.value,
                    provider_select.value,
                    color_input.value,
                    {sid: cb.value for sid, cb in skill_checks.items()},
                    fav_check.value,
                )).props("flat").classes("text-[#00e676]")

        dialog.open()

    def _save(self, dialog, name, role, soul, model, provider, color, skill_map, favorite):
        from services import get_services
        services = get_services()
        selected_skills = [sid for sid, checked in skill_map.items() if checked]
        data = {
            "name": name,
            "role": role,
            "soul": soul,
            "model": model,
            "provider": provider,
            "color": color,
            "skills": selected_skills,
            "favorite": favorite,
        }
        try:
            if self._agent:
                services.agents.update(self._agent["id"], data)
                ui.notify(f"Agent '{name}' aktualisiert", type="positive")
            else:
                services.agents.create(data)
                ui.notify(f"Agent '{name}' erstellt", type="positive")
            dialog.close()
            if self._on_save:
                self._on_save()
        except Exception as e:
            ui.notify(f"Fehler: {str(e)}", type="negative")
            logger.error("Agent-Save Fehler: %s", e)
