"""
ui/pages/home.py — Dashboard: schöne Agent-Cards + Stats.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)


@ui.page("/")
def home_page():
    apply_theme()
    create_layout("home")

    with ui.element("div").style(
        "height: calc(100vh - 44px); overflow-y: auto; "
        "background: #050a06; padding: 24px; "
        "width: 100%; box-sizing: border-box;"
    ):
        # ─── Header ──────────────────────────────────────────────────────────
        with ui.row().style(
            "align-items: center; justify-content: space-between; "
            "margin-bottom: 24px; flex-wrap: wrap; gap: 12px;"
        ):
            with ui.column().style("gap: 2px;"):
                ui.label("Dashboard").style(
                    "font-size: 22px; font-weight: 700; color: #e4f4e4;"
                )
                ui.label("Agent-Übersicht & Aktivitäten").style(
                    "font-size: 12px; color: #3a5a3a; "
                    "font-family: 'SF Mono',monospace;"
                )

            ui.html('''<a href="/agent/new" style="height:36px;padding:0 16px;border-radius:18px;
                background:rgba(0,230,118,0.1);color:#00e676;
                border:1px solid rgba(0,230,118,0.3);font-size:13px;
                font-weight:500;text-decoration:none;display:inline-flex;
                align-items:center;gap:6px">
                <span class="material-icons" style="font-size:16px">add</span>
                + Neuer Agent
            </a>''')

        # ─── Stats-Zeile ──────────────────────────────────────────────────────
        stats_row = ui.row().style("gap: 12px; margin-bottom: 24px; flex-wrap: wrap;")
        with stats_row:
            _stats_placeholder = ui.row().style("gap: 12px;")
        _load_stats(_stats_placeholder)

        # ─── Agent-Grid ───────────────────────────────────────────────────────
        with ui.row().style("align-items: center; gap: 8px; margin-bottom: 16px;"):
            ui.icon("group").style("font-size: 18px; color: #00e676;")
            ui.label("Meine Agenten").style(
                "font-size: 14px; font-weight: 700; color: #b8d4b8; "
                "text-transform: uppercase; letter-spacing: 0.5px;"
            )

        grid = ui.element("div").style(
            "display: grid; "
            "grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); "
            "gap: 12px;"
        )
        _load_agents(grid)

        # ─── Activity Feed ────────────────────────────────────────────────────
        ui.element("div").style(
            "height: 1px; background: #0f2010; margin: 32px 0 20px;"
        )
        with ui.row().style("align-items: center; gap: 8px; margin-bottom: 16px;"):
            ui.icon("bolt").style("font-size: 18px; color: #00e676;")
            ui.label("Live-Aktivität").style(
                "font-size: 14px; font-weight: 700; color: #b8d4b8; "
                "text-transform: uppercase; letter-spacing: 0.5px;"
            )

        from ui.components.activity_feed import ActivityFeed
        activity = ActivityFeed()
        ui.timer(2.0, activity.refresh)


def _load_stats(container):
    """Lädt und zeigt Stats-Kacheln."""
    from services import get_services
    try:
        services = get_services()
        agents = services.agents.list_all()
        tasks = services.tasks.list_all() if hasattr(services, 'tasks') else []

        active = sum(1 for a in agents if a.get("status") == "working")
        done = sum(1 for t in tasks if t.get("status") == "completed")
        pending = sum(1 for t in tasks if t.get("status") in ("submitted", "queued", "working"))

        stats = [
            ("group",     str(len(agents)), "Agenten",   "#00e676"),
            ("bolt",      str(active),      "Aktiv",     "#00e676"),
            ("task_alt",  str(done),        "Erledigt",  "#22c55e"),
            ("hourglass", str(pending),     "Ausstehend","#ff6b35"),
        ]

        container.clear()
        with container:
            for icon_name, value, label, color in stats:
                _stat_card(icon_name, value, label, color)
    except Exception as e:
        logger.debug("Stats laden: %s", e)


def _stat_card(icon_name: str, value: str, label: str, color: str):
    with ui.element("div").style(
        f"background: #070d08; border: 1px solid #0f2010; border-radius: 8px; "
        f"padding: 12px 16px; min-width: 110px;"
    ):
        with ui.row().style("align-items: center; gap: 8px;"):
            ui.icon(icon_name).style(f"font-size: 16px; color: {color};")
            ui.label(value).style(
                f"font-size: 22px; font-weight: 700; color: {color}; "
                f"font-family: 'SF Mono',monospace; line-height: 1;"
            )
        ui.label(label).style(
            "font-size: 10px; color: #3a5a3a; margin-top: 2px; "
            "text-transform: uppercase; letter-spacing: 0.5px; "
            "font-family: 'SF Mono',monospace;"
        )


def _load_agents(container):
    from services import get_services
    try:
        services = get_services()
        agents = services.agents.list_all()
        container.clear()
        with container:
            if not agents:
                with ui.element("div").style(
                    "grid-column: 1/-1; text-align: center; padding: 48px 0;"
                ):
                    ui.icon("group_add").style("font-size: 48px; color: #182e18;")
                    ui.label("Noch keine Agenten").style(
                        "font-size: 16px; color: #3a5a3a; margin-top: 12px; display: block;"
                    )
                    ui.label("Erstelle deinen ersten Agenten!").style(
                        "font-size: 13px; color: #3a5a3a; margin-top: 4px; display: block;"
                    )
                return

            for agent in sorted(agents, key=lambda a: (not a.get("favorite"), a.get("name", "").lower())):
                _render_agent_card(agent)
    except Exception as e:
        logger.error("Agenten laden fehlgeschlagen: %s", e)


def _render_agent_card(agent: dict):
    """Schöne Agent-Karte im alten Stil."""
    ag_id = agent["id"]
    name = agent.get("name", "?")
    role = agent.get("role", "")
    color = agent.get("color", "#00e676")
    model = agent.get("model", "")
    skills = agent.get("skills", [])
    is_fav = agent.get("favorite", False)
    status = agent.get("status", "idle")

    # Initiale (bis zu 2 Zeichen)
    initials = name[:2].upper() if len(name) >= 2 else name[0].upper()
    short_model = ""
    if model:
        short_model = model.split(":")[-1][:14] if ":" in model else model[:14]

    fav_border = "border-left: 3px solid #ffd700;" if is_fav else ""
    fav_glow = "background: linear-gradient(135deg, rgba(255,215,0,.04) 0%, transparent 60%);" if is_fav else ""

    card = ui.element("a").props(f'href="/chat/{ag_id}"').style(
        f"background: #070d08; border: 1px solid #0f2010; border-radius: 8px; "
        f"cursor: pointer; transition: border-color .15s, box-shadow .15s, transform .1s; "
        f"padding: 16px; text-decoration: none; display: block; {fav_border} {fav_glow}"
    ).classes("agent-home-card")

    with card:
        # ─── Karten-Header ─────────────────────────────────────────────────
        with ui.row().style("align-items: flex-start; gap: 12px; margin-bottom: 12px;"):
            # Avatar
            with ui.element("div").style(
                f"width: 44px; height: 44px; border-radius: 50%; background: {color}; "
                f"display: flex; align-items: center; justify-content: center; "
                f"font-size: 16px; font-weight: 700; color: #000; flex-shrink: 0; "
                f"text-transform: uppercase; border: 2px solid rgba(255,255,255,0.1);"
            ):
                ui.label(initials)

            # Name + Rolle
            with ui.column().style("gap: 2px; flex: 1; min-width: 0;"):
                with ui.row().style("align-items: center; gap: 6px; flex-wrap: wrap;"):
                    ui.label(name).style(
                        "font-size: 15px; font-weight: 600; color: #e4f4e4; "
                        "overflow: hidden; text-overflow: ellipsis; white-space: nowrap; "
                        "max-width: 180px;"
                    )
                    if is_fav:
                        ui.icon("star").style("font-size: 13px; color: #ffd700;")

                if role:
                    ui.label(role).style(
                        "font-size: 11px; color: #3a5a3a; "
                        "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                    )

            # Status-Dot
            dot_color = "#00e676" if status == "working" else "#182e18"
            dot_glow = "box-shadow: 0 0 5px #00e676;" if status == "working" else ""
            ui.element("div").style(
                f"width: 8px; height: 8px; border-radius: 50%; "
                f"background: {dot_color}; {dot_glow} flex-shrink: 0; margin-top: 4px;"
            )

        # ─── Skill-Badges ──────────────────────────────────────────────────
        if skills:
            with ui.row().style("gap: 4px; flex-wrap: wrap; margin-bottom: 10px;"):
                for sk in skills[:4]:
                    ui.label(sk).style(
                        "font-size: 10px; font-family: 'SF Mono',monospace; "
                        "padding: 2px 6px; border-radius: 3px; "
                        "background: rgba(0,230,118,0.08); color: #00e676; "
                        "border: 1px solid rgba(0,230,118,0.15);"
                    )
                if len(skills) > 4:
                    ui.label(f"+{len(skills) - 4}").style(
                        "font-size: 10px; color: #3a5a3a; padding: 2px 4px;"
                    )

        # ─── Footer: Model + Buttons ───────────────────────────────────────
        with ui.row().style(
            "align-items: center; justify-content: space-between; "
            "border-top: 1px solid #0f2010; padding-top: 10px; margin-top: 4px;"
        ):
            if short_model:
                ui.label(short_model).style(
                    "font-size: 10px; font-family: 'SF Mono',monospace; "
                    "color: #3a5a3a; padding: 2px 6px; background: #0f2010; "
                    "border-radius: 3px;"
                )
            else:
                ui.element("div")  # Spacer

            with ui.row().style("align-items: center; gap: 10px;"):
                ui.html(f'''<a href="/agent/new?clone={ag_id}"
                    onclick="event.stopPropagation()"
                    title="Agent duplizieren"
                    style="display:inline-flex;align-items:center;color:#3a5a3a;
                           text-decoration:none;font-size:14px;line-height:1;
                           transition:color .15s"
                    onmouseover="this.style.color=\'#00e676\'"
                    onmouseout="this.style.color=\'#3a5a3a\'">
                    <span class="material-icons" style="font-size:15px">content_copy</span>
                </a>''')
                ui.label("Chat →").style(
                    "font-size: 11px; color: #00e676; font-weight: 600; "
                    "letter-spacing: 0.3px;"
                )

    # Hover-Effekt via injected CSS
    ui.add_css("""
        .agent-home-card:hover {
            border-color: #00e676 !important;
            box-shadow: 0 0 20px rgba(0, 230, 118, 0.08) !important;
            transform: translateY(-2px);
        }
    """)


