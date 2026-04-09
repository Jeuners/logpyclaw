"""
ui/pages/settings.py — Provider-Konfiguration und App-Einstellungen.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)


@ui.page("/settings")
def settings_page():
    apply_theme()
    create_layout("settings")

    with ui.column().classes("w-full max-w-4xl mx-auto p-6 gap-6"):
        ui.label("Einstellungen").classes("text-2xl font-bold")

        # Tabs für verschiedene Einstellungsbereiche
        with ui.tabs().classes("w-full") as tabs:
            tab_providers = ui.tab("Providers")
            tab_app = ui.tab("App")
            tab_debug = ui.tab("Debug")

        with ui.tab_panels(tabs, value=tab_providers).classes("w-full"):
            with ui.tab_panel(tab_providers):
                _render_providers()
            with ui.tab_panel(tab_app):
                _render_app_settings()
            with ui.tab_panel(tab_debug):
                _render_debug()


def _render_providers():
    from services import get_services
    from storage.providers import save_providers
    services = get_services()
    providers = services.agents.get_providers()

    provider_configs = [
        ("ollama", "Ollama", [("url", "Server URL", "http://localhost:11434")]),
        ("openrouter", "OpenRouter", [("api_key", "API Key", "sk-or-...")]),
        ("mistral", "Mistral AI", [("api_key", "API Key", "...")]),
        ("google_api", "Google API", [("api_key", "API Key", "...")]),
        ("telegram", "Telegram", [
            ("bot_token", "Bot Token", "123456:ABC..."),
            ("chat_id", "Chat ID", "-100..."),
        ]),
        ("gmail", "Gmail", [
            ("email", "E-Mail", "user@gmail.com"),
            ("app_password", "App-Passwort", "xxxx xxxx xxxx xxxx"),
        ]),
        ("comfyui", "ComfyUI", [
            ("url", "Server URL", "http://localhost:8188"),
            ("model", "Modell", "flux2pro"),
        ]),
        ("qdrant", "Qdrant (Memory)", [("url", "Server URL", "http://localhost:6333")]),
    ]

    for provider_id, title, fields in provider_configs:
        cfg = providers.get(provider_id, {})
        with ui.expansion(title).classes("w-full"):
            with ui.column().classes("w-full gap-3 p-2"):
                inputs = {}
                for field_id, label, placeholder in fields:
                    is_secret = "key" in field_id.lower() or "password" in field_id.lower() or "token" in field_id.lower()
                    val = cfg.get(field_id, "")
                    inp = ui.input(
                        label=label,
                        value=val,
                        placeholder=placeholder,
                        password=is_secret,
                        password_toggle_button=is_secret,
                    ).classes("w-full")
                    inputs[field_id] = inp

                def save_provider(pid=provider_id, inp_refs=inputs):
                    p = services.agents.get_providers()
                    if pid not in p:
                        p[pid] = {}
                    for fid, inp_ref in inp_refs.items():
                        p[pid][fid] = inp_ref.value
                    save_providers(p)
                    ui.notify(f"{title} gespeichert", type="positive")

                ui.button("Speichern", on_click=save_provider) \
                    .props("flat").classes("text-[#00e676] mt-2")


def _render_app_settings():
    from config.settings import settings
    with ui.column().classes("gap-4"):
        ui.label(f"Port: {settings.PORT}").classes("text-gray-400")
        ui.label(f"Native Mode: {settings.NATIVE_MODE}").classes("text-gray-400")
        ui.label(f"Debug: {settings.DEBUG}").classes("text-gray-400")
        ui.label("Konfiguration über .env Datei im Projektordner ändern.") \
            .classes("text-gray-500 text-sm italic")


def _render_debug():
    from services import get_services
    services = get_services()

    async def run_health():
        import httpx
        from config.settings import settings
        results = {}
        for name, url in [("Ollama", settings.OLLAMA_URL + "/api/tags"),
                          ("Qdrant", settings.QDRANT_URL + "/collections")]:
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    r = await c.get(url)
                    results[name] = "OK" if r.status_code < 400 else f"{r.status_code}"
            except Exception as e:
                results[name] = str(e)[:50]
        for n, s in results.items():
            ui.notify(f"{n}: {s}")

    ui.button("Health-Check", on_click=run_health).props("flat").classes("text-[#00e676]")

    tasks = services.tasks.list_all()
    agents = services.agents.list_all()
    ui.label(f"Agents: {len(agents)} | Tasks: {len(tasks)}").classes("text-gray-400 mt-4")
