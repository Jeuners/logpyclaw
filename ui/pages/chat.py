"""
ui/pages/chat.py — Chat-Interface.
Layout: Links Agenten-Sidebar (228px) | Rechts Chat-Bereich.

Architektur (v3 — full client-side):
- Send/Streaming komplett via JavaScript fetch() + SSE
- Kein Python Event-Handler für Send nötig (umgeht core.loop Bug)
- NiceGUI nur für Page-Render + Layout
- Navigation via <a href>
"""
import logging
import json
import html as html_mod
import re

from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)


@ui.page("/chat/{agent_id}")
def chat_page(agent_id: str):
    apply_theme()
    create_layout("chat")

    from services import get_services
    services = get_services()
    agent = services.agents.get(agent_id)
    agents = services.agents.list_all()
    agents_sorted = sorted(agents, key=lambda a: (not a.get("favorite"), a.get("name", "").lower()))

    if not agent:
        with ui.column().classes("items-center justify-center w-full").style("height: calc(100vh - 44px)"):
            ui.icon("person_off").style("font-size: 48px; color: #3a5a3a;")
            ui.label("Agent nicht gefunden").style("color: #ef4444; font-size: 18px; margin-top: 12px;")
        return

    agent_name = agent.get("name", "?")

    # ─── CSS Overrides ───────────────────────────────────────────────────
    ui.add_css("""
        .q-page { min-height: unset !important; }
        .q-page-container { padding-bottom: 0 !important; }
        body > div#app > div { height: 100vh; overflow: hidden; }
    """)

    # ─── 2-Spalten-Layout ────────────────────────────────────────────────
    with ui.element("div").style(
        "display: flex; width: 100%; height: calc(100vh - 44px); overflow: hidden; gap: 0;"
    ):
        # Linke Sidebar
        with ui.element("div").style(
            "width: 228px; min-width: 228px; flex-shrink: 0; "
            "background: #070d08; border-right: 1px solid #0f2010; "
            "display: flex; flex-direction: column; overflow: hidden;"
        ):
            with ui.element("div").style(
                "padding: 10px 14px 8px; font-size: 10px; font-weight: 700; "
                "color: #3a5a3a; text-transform: uppercase; letter-spacing: 1.2px; "
                "font-family: 'SF Mono','Fira Code',monospace; flex-shrink: 0; "
                "border-bottom: 1px solid #0f2010; display: flex; "
                "align-items: center; justify-content: space-between;"
            ):
                ui.label("Agenten")
                ui.button(icon="add", on_click=_show_create_dialog) \
                    .props("flat dense round") \
                    .style("color: #00e676; font-size: 12px; width: 24px; height: 24px;") \
                    .tooltip("Neuer Agent")

            with ui.scroll_area().style("flex: 1; min-height: 0;"):
                with ui.column().style("padding: 4px 6px; gap: 0;"):
                    for ag in agents_sorted:
                        _render_sidebar_agent(ag, agent_id)

        # Rechter Bereich: Chat
        with ui.element("div").style(
            "flex: 1; display: flex; flex-direction: column; overflow: hidden; background: #050a06;"
        ):
            _render_chat_topbar(agent, agent_id, agent.get("color", "#00e676"))

            # Messages Container
            messages_html = _build_history_html(agent_id)
            ui.html(
                f'<div id="ac-scroll" style="flex:1;overflow-y:auto;min-height:0">'
                f'<div id="ac-messages" style="padding:16px 24px;display:flex;flex-direction:column;gap:10px">'
                f'{messages_html}'
                f'</div></div>'
            ).style("flex:1;display:flex;flex-direction:column;min-height:0;overflow:hidden")

            # Input Area — reines HTML, Event-Handling komplett via JS
            ui.html(f'''
                <div style="padding:12px 20px 16px;border-top:1px solid #0f2010;background:#070d08;flex-shrink:0">
                    <div style="display:flex;gap:8px;align-items:flex-end">
                        <textarea id="ac-input" rows="1" placeholder="Nachricht… (Enter senden, Shift+Enter Umbruch)"
                            style="flex:1;background:#0d1a0e;border:1px solid #182e18;border-radius:8px;
                            color:#e4f4e4;font-size:14px;padding:10px 12px;resize:none;outline:none;
                            font-family:inherit;line-height:1.5;min-height:40px;max-height:160px"></textarea>
                        <button id="ac-send-btn"
                            style="width:40px;height:40px;border-radius:20px;background:#00e676;color:#000;
                            border:none;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center">
                            <span class="material-icons" style="font-size:20px">send</span>
                        </button>
                    </div>
                </div>
            ''').style("flex-shrink:0")

    # ─── JavaScript: ALLES client-seitig ─────────────────────────────────
    # Wird als <script> in den <head> injected
    escaped_agent_id = json.dumps(agent_id)
    escaped_agent_name = json.dumps(html_mod.escape(agent_name))
    ui.add_head_html(f"""<script>
    window._ac = {{
        sending: false,
        agentId: {escaped_agent_id},
        agentName: {escaped_agent_name},

        init: function() {{
            // Auto-grow textarea
            const ta = document.getElementById('ac-input');
            if (!ta) return;
            ta.addEventListener('input', function() {{
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 160) + 'px';
            }});
            // Enter = send, Shift+Enter = newline
            ta.addEventListener('keydown', function(e) {{
                if (e.key === 'Enter' && !e.shiftKey) {{
                    e.preventDefault();
                    window._ac.send();
                }}
            }});
            // Send-Button (onclick wird von Vue sanitisiert, daher addEventListener)
            const btn = document.getElementById('ac-send-btn');
            if (btn) {{
                btn.addEventListener('click', function(e) {{
                    e.preventDefault();
                    e.stopPropagation();
                    window._ac.send();
                }});
            }}
            // Scroll to bottom
            setTimeout(() => this.scroll(), 300);
        }},

        send: function() {{
            if (this.sending) return;
            const ta = document.getElementById('ac-input');
            if (!ta) return;
            const msg = ta.value.trim();
            if (!msg) return;

            this.sending = true;
            ta.value = '';
            ta.style.height = 'auto';

            // User-Nachricht anzeigen
            this.addMsg('user', this.escHtml(msg));

            // Typing-Indicator
            this.addTyping();

            // SSE-Stream starten
            const url = '/api/chat/stream?agent_id=' + encodeURIComponent(this.agentId)
                      + '&message=' + encodeURIComponent(msg);

            let accumulated = '';
            let replyStarted = false;

            fetch(url).then(response => {{
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                const processStream = () => {{
                    reader.read().then(({{ done, value }}) => {{
                        if (done) {{
                            this.sending = false;
                            this.removeTyping();
                            if (accumulated && !replyStarted) {{
                                // Shouldn't happen, but safety
                                this.addReply(accumulated);
                            }}
                            this.scroll();
                            return;
                        }}

                        buffer += decoder.decode(value, {{ stream: true }});
                        const lines = buffer.split('\\n');
                        buffer = lines.pop(); // Keep incomplete line

                        for (const line of lines) {{
                            if (!line.startsWith('data: ')) continue;
                            try {{
                                const data = JSON.parse(line.substring(6));

                                if (data.error) {{
                                    this.removeTyping();
                                    this.addError(data.error);
                                    this.sending = false;
                                    return;
                                }}

                                if (data.chunk) {{
                                    accumulated += data.chunk;
                                    if (!replyStarted) {{
                                        replyStarted = true;
                                        this.removeTyping();
                                        this.startReply();
                                    }}
                                    this.updateReply(accumulated);
                                }}

                                if (data.done) {{
                                    this.removeTyping();
                                    const displayReply = data.display_reply || accumulated;
                                    if (displayReply) {{
                                        if (!replyStarted) this.startReply();
                                        this.finishReply(displayReply);
                                    }}
                                    // A2A dispatches
                                    if (data.a2a_dispatches) {{
                                        for (const d of data.a2a_dispatches) {{
                                            this.addA2A(this.agentName, this.escHtml(d.recipient_name), this.escHtml(d.task_text.substring(0, 180)));
                                        }}
                                    }}
                                    this.sending = false;
                                    this.scroll();
                                }}
                            }} catch(e) {{ /* skip parse errors */ }}
                        }}

                        processStream();
                    }}).catch(err => {{
                        this.sending = false;
                        this.removeTyping();
                        this.addError(String(err));
                    }});
                }};

                processStream();
            }}).catch(err => {{
                this.sending = false;
                this.removeTyping();
                this.addError(String(err));
            }});
        }},

        // ─── DOM Helpers ─────────────────────────────────────────────
        addMsg: function(role, html, image) {{
            const c = document.getElementById('ac-messages');
            if (!c) return;
            const isUser = role === 'user';
            const align = isUser ? 'flex-end' : 'flex-start';
            const bbl = isUser
                ? 'background:rgba(0,230,118,.08);border:1px solid #182e18;color:#e4f4e4;border-bottom-right-radius:3px;'
                : 'background:#0d1a0e;border:1px solid #0f2010;color:#b8d4b8;border-bottom-left-radius:3px;';
            const label = isUser ? 'Du' : 'Assistant';
            let h = '<div style="display:flex;flex-direction:column;gap:3px;max-width:820px;align-self:'+align+';align-items:'+align+';width:100%">';
            h += '<span style="font-size:10px;font-family:monospace;color:#3a5a3a;padding:0 4px">'+label+'</span>';
            if (image) h += '<img src="'+image+'" style="max-width:320px;border-radius:8px;margin-bottom:4px">';
            if (html) h += '<div style="padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;word-break:break-word;'+bbl+'">'+html+'</div>';
            h += '</div>';
            // Remove empty hint if present
            const hint = document.getElementById('ac-empty-hint');
            if (hint) hint.remove();
            c.insertAdjacentHTML('beforeend', h);
            this.scroll();
        }},

        addTyping: function() {{
            const c = document.getElementById('ac-messages');
            if (!c) return;
            c.insertAdjacentHTML('beforeend',
                '<div id="ac-typing" style="align-self:flex-start;padding:4px">' +
                '<span style="font-size:14px;color:#3a5a3a;animation:ac-pulse 1.2s ease-in-out infinite">● ● ●</span></div>');
            this.scroll();
        }},

        removeTyping: function() {{
            const el = document.getElementById('ac-typing');
            if (el) el.remove();
        }},

        startReply: function() {{
            const c = document.getElementById('ac-messages');
            if (!c) return;
            c.insertAdjacentHTML('beforeend',
                '<div id="ac-reply-wrap" style="display:flex;flex-direction:column;gap:3px;max-width:820px;align-self:flex-start;align-items:flex-start;width:100%">' +
                '<span style="font-size:10px;font-family:monospace;color:#3a5a3a;padding:0 4px">Assistant</span>' +
                '<div id="ac-reply" style="padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;word-break:break-word;' +
                'background:#0d1a0e;border:1px solid #0f2010;color:#b8d4b8;min-width:40px;white-space:pre-wrap"></div></div>');
        }},

        updateReply: function(text) {{
            const el = document.getElementById('ac-reply');
            if (el) {{ el.textContent = text; this.scroll(); }}
        }},

        finishReply: function(text) {{
            const el = document.getElementById('ac-reply');
            if (el) {{
                el.style.whiteSpace = 'normal';
                el.innerHTML = this.renderMd(text);
                el.removeAttribute('id');
            }}
            const wrap = document.getElementById('ac-reply-wrap');
            if (wrap) wrap.removeAttribute('id');
            this.scroll();
        }},

        addReply: function(text) {{
            this.startReply();
            this.finishReply(text);
        }},

        addError: function(msg) {{
            const c = document.getElementById('ac-messages');
            if (!c) return;
            c.insertAdjacentHTML('beforeend',
                '<div style="padding:10px 14px;border-radius:10px;font-size:14px;background:rgba(239,68,68,.08);' +
                'border:1px solid rgba(239,68,68,.3);color:#ef4444;align-self:flex-start;max-width:820px">' +
                '<strong>Fehler:</strong> ' + this.escHtml(msg) + '</div>');
            this.scroll();
        }},

        addA2A: function(sender, recipient, task) {{
            const c = document.getElementById('ac-messages');
            if (!c) return;
            let h = '<div style="border-left:3px solid #00bcd4;background:rgba(0,188,212,0.06);border-radius:0 6px 6px 0;padding:8px 12px;align-self:flex-start;max-width:600px">';
            h += '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">';
            h += '<span class="material-icons" style="font-size:14px;color:#00bcd4">arrow_forward</span>';
            h += '<span style="font-size:12px;font-weight:600;color:#00bcd4">'+sender+' → @'+recipient+'</span>';
            h += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid #00bcd4;color:#00bcd4;font-family:monospace">A2A</span>';
            h += '</div><span style="font-size:12px;color:#3a5a3a;line-height:1.5">'+task+'</span></div>';
            c.insertAdjacentHTML('beforeend', h);
            this.scroll();
        }},

        scroll: function() {{
            const el = document.getElementById('ac-scroll');
            if (el) setTimeout(() => {{ el.scrollTop = el.scrollHeight; }}, 50);
        }},

        escHtml: function(s) {{
            const d = document.createElement('div');
            d.textContent = s;
            return d.innerHTML;
        }},

        renderMd: function(text) {{
            let t = this.escHtml(text);
            // Code blocks
            t = t.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<pre style="background:#0a150b;padding:8px;border-radius:4px;overflow-x:auto;font-size:12px"><code>$2</code></pre>');
            // Inline code
            t = t.replace(/`([^`]+)`/g, '<code style="background:#0a150b;padding:1px 4px;border-radius:3px;font-size:12px">$1</code>');
            // Bold
            t = t.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
            // Italic
            t = t.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
            // Links
            t = t.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2" target="_blank" style="color:#00e676">$1</a>');
            // Newlines
            t = t.replace(/\\n/g, '<br>');
            return t;
        }}
    }};

    // Init nach DOM ready
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', () => window._ac.init());
    }} else {{
        setTimeout(() => window._ac.init(), 200);
    }}
    </script>""")


# ─── History als HTML rendern ────────────────────────────────────────────────


def _build_history_html(agent_id: str) -> str:
    from services import get_services
    try:
        services = get_services()
        history = services.agents.get_history(agent_id)
        if not history:
            return (
                '<div id="ac-empty-hint" style="color:#3a5a3a;font-size:13px;font-style:italic;'
                'text-align:center;padding:32px 0;width:100%">'
                'Noch keine Nachrichten. Starte eine Unterhaltung!</div>'
            )
        parts = []
        for msg in history[-50:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            image = msg.get("image") or msg.get("task_image")
            skill = msg.get("skill_used")
            if not content and not image:
                continue
            parts.append(_msg_to_html(role, content, image, skill))
        return "\n".join(parts)
    except Exception as e:
        logger.error("History laden fehlgeschlagen: %s", e)
        return ""


def _msg_to_html(role: str, content: str, image=None, skill=None) -> str:
    is_user = role == "user"
    align = "flex-end" if is_user else "flex-start"
    bbl = (
        "background:rgba(0,230,118,.08);border:1px solid #182e18;color:#e4f4e4;border-bottom-right-radius:3px;"
        if is_user else
        "background:#0d1a0e;border:1px solid #0f2010;color:#b8d4b8;border-bottom-left-radius:3px;"
    )
    label = "Du" if is_user else "Assistant"
    skill_text = f" · {html_mod.escape(skill)}" if skill else ""

    h = (
        f'<div style="display:flex;flex-direction:column;gap:3px;max-width:820px;'
        f'align-self:{align};align-items:{align};width:100%">'
        f'<span style="font-size:10px;font-family:monospace;color:#3a5a3a;padding:0 4px">'
        f'{label}{skill_text}</span>'
    )
    if image:
        h += f'<img src="{html_mod.escape(image)}" style="max-width:320px;border-radius:8px;margin-bottom:4px">'
    if content:
        rendered = _simple_md(content)
        h += (
            f'<div style="padding:10px 14px;border-radius:10px;font-size:14px;'
            f'line-height:1.6;word-break:break-word;{bbl}">{rendered}</div>'
        )
    h += '</div>'
    return h


def _simple_md(text: str) -> str:
    t = html_mod.escape(text)
    t = re.sub(r'```(\w*)\n(.*?)```', lambda m: f'<pre style="background:#0a150b;padding:8px;border-radius:4px;overflow-x:auto;font-size:12px"><code>{m.group(2)}</code></pre>', t, flags=re.DOTALL)
    t = re.sub(r'`([^`]+)`', r'<code style="background:#0a150b;padding:1px 4px;border-radius:3px;font-size:12px">\1</code>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'\*(.+?)\*', r'<em>\1</em>', t)
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank" style="color:#00e676">\1</a>', t)
    t = t.replace('\n', '<br>')
    return t


# ─── Sidebar Agent ────────────────────────────────────────────────────────────


def _render_sidebar_agent(agent: dict, current_agent_id: str):
    ag_id = agent["id"]
    name = agent.get("name", "?")
    color = agent.get("color", "#00e676")
    model = agent.get("model", "")
    is_fav = agent.get("favorite", False)
    is_selected = ag_id == current_agent_id
    initials = name[:2].upper() if len(name) >= 2 else name[0].upper()

    border_style = "border-left: 2px solid #ffd700;" if is_fav else "border-left: 2px solid transparent;"
    bg_style = (
        f"background: rgba(0,230,118,.08); {border_style}"
        if is_selected else f"background: transparent; {border_style}"
    )

    with ui.element("a").props(f'href="/chat/{ag_id}"').style(
        f"display: flex; align-items: center; gap: 8px; padding: 8px; "
        f"border-radius: 6px; cursor: pointer; transition: background .12s; "
        f"margin-bottom: 2px; text-decoration: none; {bg_style}"
    ).classes("ac-agent-item"):
        with ui.element("div").style(
            f"width: 30px; height: 30px; border-radius: 50%; background: {color}; "
            f"display: flex; align-items: center; justify-content: center; "
            f"font-size: 11px; font-weight: 700; color: #000; flex-shrink: 0; text-transform: uppercase;"
        ):
            ui.label(initials)
        with ui.column().style("flex: 1; min-width: 0; gap: 0;"):
            name_color = "color: #00e676;" if is_selected else "color: #b8d4b8;"
            ui.label(name).style(
                f"font-size: 13px; font-weight: 500; {name_color} "
                f"overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            )
            if model:
                short = model.split(":")[-1][:12] if ":" in model else model[:12]
                ui.label(short).style("font-size: 9px; color: #3a5a3a; font-family: monospace;")
        if is_fav:
            ui.icon("star").style("font-size: 12px; color: #ffd700; flex-shrink: 0;")


# ─── Chat Topbar ──────────────────────────────────────────────────────────────


def _render_chat_topbar(agent: dict, agent_id: str, color: str):
    name = agent.get("name", "?")
    role = agent.get("role", "")
    skills = agent.get("skills", [])
    model = agent.get("model", "")

    with ui.element("div").style(
        "display: flex; align-items: center; gap: 16px; padding: 0 24px; "
        "height: 72px; background: #070d08; border-bottom: 1px solid #0f2010; flex-shrink: 0;"
    ):
        initials = name[:2].upper() if len(name) >= 2 else name[0].upper()
        with ui.element("div").style(
            f"width: 44px; height: 44px; border-radius: 50%; background: {color}; "
            f"display: flex; align-items: center; justify-content: center; "
            f"font-size: 18px; font-weight: 700; color: #000; flex-shrink: 0;"
        ):
            ui.label(initials)

        with ui.column().style("gap: 2px; flex: 1; min-width: 0;"):
            with ui.row().classes("items-center gap-2"):
                ui.label(name).style("font-size: 16px; font-weight: 600; color: #e4f4e4;")
                if agent.get("favorite"):
                    ui.icon("star").style("font-size: 14px; color: #ffd700;")
            if role:
                ui.label(role).style("font-size: 12px; color: #3a5a3a; font-family: monospace;")

        if skills:
            with ui.row().style("gap: 4px; flex-wrap: wrap; max-width: 300px;"):
                for sk in skills[:5]:
                    ui.label(sk).style(
                        "font-size: 10px; font-family: monospace; padding: 2px 6px; border-radius: 3px; "
                        "background: rgba(0,230,118,0.08); color: #00e676; border: 1px solid rgba(0,230,118,0.2);"
                    )

        with ui.row().style("gap: 6px; margin-left: auto; flex-shrink: 0;"):
            if model:
                ui.label(model.split("/")[-1][:16]).style(
                    "font-size: 10px; font-family: monospace; padding: 2px 7px; border-radius: 3px; "
                    "background: #0f2010; color: #3a5a3a; border: 1px solid #182e18; align-self: center;"
                )
            _topbar_btn("delete_sweep", "History", lambda: _clear_history(agent_id))
            _topbar_btn("edit", "Bearbeiten", lambda: _edit_agent(agent))
            _topbar_btn("task_alt", "Tasks", _show_task_monitor)


def _topbar_btn(icon_name: str, tooltip_text: str, callback):
    ui.button(icon=icon_name, on_click=callback) \
        .props("flat dense round") \
        .style("color: #3a5a3a; width: 32px; height: 32px;") \
        .tooltip(tooltip_text)


# ─── Dialog-Aktionen ──────────────────────────────────────────────────────────


def _clear_history(agent_id: str):
    from services import get_services
    services = get_services()
    services.agents.clear_history(agent_id)
    # Redirect via JS-freien Weg: einfach einen Link klicken lassen
    ui.navigate.to(f"/chat/{agent_id}")


def _edit_agent(agent: dict):
    from ui.dialogs.agent_form import AgentFormDialog
    AgentFormDialog(agent=agent, on_save=lambda: ui.navigate.to(f"/chat/{agent['id']}"))


def _show_create_dialog():
    from ui.dialogs.agent_form import AgentFormDialog
    AgentFormDialog(on_save=lambda: ui.navigate.to("/"))


def _show_task_monitor():
    from ui.components.task_monitor import TaskMonitor
    with ui.dialog().props("maximized").style("background: #070d08;") as dlg:
        dlg.open()
        with ui.card().style(
            "width: 900px; max-width: 95vw; background: #070d08; border: 1px solid #182e18;"
        ):
            with ui.row().style(
                "align-items: center; justify-content: space-between; "
                "padding: 16px 20px; border-bottom: 1px solid #0f2010;"
            ):
                with ui.row().style("gap: 8px; align-items: center;"):
                    ui.icon("task_alt").style("color: #00e676; font-size: 20px;")
                    ui.label("Task Monitor").style("font-size: 16px; font-weight: 600; color: #e4f4e4;")
                ui.button(icon="close", on_click=dlg.close).props("flat dense round").style("color: #3a5a3a;")
            with ui.element("div").style("padding: 16px;"):
                TaskMonitor()
