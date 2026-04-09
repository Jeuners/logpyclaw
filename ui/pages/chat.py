"""
ui/pages/chat.py — Chat-Interface für einen Agenten.
"""
import logging
from nicegui import ui, app
from ui.layout import create_layout
from ui.theme import apply_theme
from ui.components.chat_message import ChatMessage

logger = logging.getLogger(__name__)


@ui.page("/chat")
def chat_redirect():
    """Chat ohne Agent-ID — zum ersten Agenten oder zur Startseite."""
    from services import get_services
    services = get_services()
    agents = services.agents.list_all()
    if agents:
        ui.navigate.to(f"/chat/{agents[0]['id']}")
    else:
        ui.navigate.to("/")


@ui.page("/chat/{agent_id}")
async def chat_page(agent_id: str):
    apply_theme()

    from services import get_services
    services = get_services()
    agent = services.agents.get(agent_id)

    if not agent:
        create_layout("chat")
        ui.label("Agent nicht gefunden").classes("text-red-500 text-xl p-8")
        return

    create_layout("chat")
    color = agent.get("color", "#00e676")

    with ui.column().classes("w-full max-w-5xl mx-auto h-full flex flex-col px-4 pb-4"):
        # Agent-Header
        with ui.card().classes("w-full p-4 mb-4"):
            with ui.row().classes("items-center gap-4"):
                ui.avatar(agent["name"][0].upper(), color=color, text_color="black") \
                    .classes("text-2xl")
                with ui.column().classes("gap-0 flex-1"):
                    ui.label(agent["name"]).classes("text-xl font-bold")
                    if agent.get("role"):
                        ui.label(agent["role"]).classes("text-gray-500")
                with ui.row().classes("gap-2"):
                    ui.button(icon="delete_sweep", on_click=lambda: _clear_history(agent_id)) \
                        .props("flat dense").tooltip("History löschen")
                    ui.button(icon="edit", on_click=lambda: _edit_agent(agent)) \
                        .props("flat dense").tooltip("Agent bearbeiten")

        # Nachrichten-Container (scrollbar)
        messages_container = ui.scroll_area().classes("flex-1 w-full min-h-0")
        with messages_container:
            msg_col = ui.column().classes("w-full gap-2 p-2")
            _load_history(msg_col, agent_id)

        # Eingabe
        with ui.row().classes("w-full gap-2 items-end pt-2"):
            _uploaded_image = {"data": None}

            def handle_upload(e):
                import base64
                data = base64.b64encode(e.content.read()).decode()
                mime = e.type or "image/png"
                _uploaded_image["data"] = f"data:{mime};base64,{data}"
                ui.notify("Bild geladen", type="positive")

            text_input = ui.textarea(placeholder="Nachricht eingeben...") \
                .classes("flex-1").props("rows=2 autogrow outlined dense")
            text_input.on("keydown.enter.ctrl", lambda: _send(agent_id, text_input, msg_col, _uploaded_image))

            with ui.column().classes("gap-1 shrink-0"):
                ui.upload(on_upload=handle_upload, auto_upload=True) \
                    .props("flat dense accept='image/*'").tooltip("Bild anhängen")
                ui.button(icon="send",
                          on_click=lambda: _send(agent_id, text_input, msg_col, _uploaded_image)) \
                    .props("flat").classes("text-[#00e676]")


def _load_history(container, agent_id: str):
    from services import get_services
    try:
        services = get_services()
        history = services.agents.get_history(agent_id)
        with container:
            if not history:
                ui.label("Noch keine Nachrichten. Starte eine Unterhaltung!") \
                    .classes("text-gray-500 text-sm italic text-center py-8 w-full")
                return
            for msg in history[-50:]:  # letzte 50 Nachrichten
                ChatMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    image=msg.get("image") or msg.get("task_image"),
                    skill=msg.get("skill_used"),
                )
    except Exception as e:
        logger.error("History laden fehlgeschlagen: %s", e)


async def _send(agent_id: str, text_input, container, uploaded_image: dict):
    """
    Sendet eine Chat-Nachricht und zeigt die Antwort via Streaming an.
    Nutzt GET /api/chat/stream (SSE) für Token-by-Token Updates.
    Fallback auf blocking POST /api/chat bei Bild-Uploads (Multimodal).
    """
    import asyncio
    import json
    import urllib.parse

    message = text_input.value.strip()
    if not message:
        return
    text_input.value = ""
    images = [uploaded_image["data"]] if uploaded_image.get("data") else None
    uploaded_image["data"] = None

    # User-Message anzeigen
    with container:
        ChatMessage(role="user", content=message, image=images[0] if images else None)

    # Streaming-Antwort-Label (wird inkrementell befüllt)
    with container:
        reply_label = ui.markdown("").classes("text-sm max-w-full")

    accumulated = []

    try:
        if images:
            # Bilder: Streaming nicht unterstützt → blockierender Fallback
            loop = asyncio.get_event_loop()
            from services import get_services
            from core.thread_pools import CHAT_POOL
            services = get_services()
            result = await loop.run_in_executor(
                CHAT_POOL,
                lambda: services.chat.handle_message(agent_id, message, images=images)
            )
            reply_label.content = result.get("reply", "")
            if result.get("image"):
                with container:
                    ChatMessage(role="assistant", content="", image=result["image"])
                reply_label.delete()
        else:
            # Text: Streaming via SSE
            import httpx
            params = urllib.parse.urlencode({"agent_id": agent_id, "message": message})
            stream_url = f"http://localhost:5050/api/chat/stream?{params}"

            timeout = httpx.Timeout(connect=5.0, read=360.0, write=5.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", stream_url) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if data.get("done"):
                            # A2A-Dispatches verarbeiten
                            a2a = data.get("a2a_dispatches", [])
                            display_reply = data.get("display_reply")
                            if a2a:
                                # Display-Reply bereinigen (ohne @Mention-Blöcke)
                                if display_reply is not None:
                                    reply_label.content = display_reply
                                else:
                                    reply_label.content = "".join(accumulated)
                                if not display_reply:
                                    # Nur Delegation, kein eigener Text → Label entfernen
                                    reply_label.delete()
                                # Delegation-Cards für jede A2A-Delegation
                                with container:
                                    for d in a2a:
                                        _render_a2a_card(
                                            agent.get("name", ""),
                                            d["recipient_name"],
                                            d["task_text"],
                                        )
                            break
                        if data.get("error"):
                            reply_label.content = f"**Fehler:** {data['error']}"
                            break
                        chunk = data.get("chunk", "")
                        if chunk:
                            accumulated.append(chunk)
                            # Markdown re-render bei jedem Chunk
                            reply_label.content = "".join(accumulated)

    except Exception as e:
        reply_label.content = f"**Fehler:** {str(e)}"
        logger.error("Chat-Streaming-Fehler: %s", e)


def _clear_history(agent_id: str):
    from services import get_services
    services = get_services()
    services.agents.clear_history(agent_id)
    ui.navigate.reload()
    ui.notify("History gelöscht", type="positive")


def _render_a2a_card(sender_name: str, recipient_name: str, task_text: str):
    """
    Rendert eine saubere A2A-Delegations-Card im Chat.
    Erscheint anstelle des rohen @Mention-Textes.
    """
    preview = task_text[:180] + ("..." if len(task_text) > 180 else "")
    with ui.card().classes(
        "w-full my-1 px-4 py-3 border-l-4 border-blue-400 bg-blue-50 dark:bg-blue-900/20"
    ):
        with ui.row().classes("items-center gap-2 mb-1"):
            ui.icon("arrow_forward").classes("text-blue-500 text-sm")
            ui.label(f"{sender_name}  →  @{recipient_name}") \
                .classes("text-blue-700 dark:text-blue-300 font-semibold text-sm")
            ui.chip("A2A", color="blue").props("dense outline").classes("text-xs")
        ui.label(preview).classes("text-gray-600 dark:text-gray-300 text-xs leading-relaxed")


def _edit_agent(agent: dict):
    from ui.dialogs.agent_form import AgentFormDialog
    AgentFormDialog(agent=agent, on_save=lambda: ui.navigate.reload())
