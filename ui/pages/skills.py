"""
ui/pages/skills.py — Skill-Übersicht: alle registrierten Skills mit Triggern,
Requirements, Provider-Verfügbarkeit und Agent-Usage.
"""
import html as _html
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)


@ui.page("/skills")
def skills_page():
    apply_theme()
    create_layout("skills")

    with ui.element("div").style(
        "height: calc(100vh - 44px); overflow-y: auto; "
        "background: #050a06; padding: 24px; "
        "width: 100%; box-sizing: border-box;"
    ):
        # Header
        with ui.column().style("gap: 2px; margin-bottom: 20px;"):
            ui.label("Skills").style(
                "font-size: 22px; font-weight: 700; color: #e4f4e4;"
            )
            ui.label("Alle registrierten Skills, Trigger und Provider").style(
                "font-size: 12px; color: #3a5a3a; font-family: 'SF Mono',monospace;"
            )

        # Summary-Zeile
        summary = ui.row().style("gap: 12px; margin-bottom: 20px; flex-wrap: wrap;")

        # Skill-Grid
        grid = ui.element("div").style(
            "display: grid; "
            "grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); "
            "gap: 12px;"
        )

        _load_skills(summary, grid)

    ui.add_css("""
        .skill-card { transition: border-color .15s, box-shadow .15s; }
        .skill-card:hover {
            border-color: #00e676 !important;
            box-shadow: 0 0 20px rgba(0, 230, 118, 0.08) !important;
        }
        /* Collapsible Trigger-Block */
        .trig-det { margin: 0; padding: 0; }
        .trig-det > summary { list-style: none; cursor: pointer; user-select: none; }
        .trig-det > summary::-webkit-details-marker { display: none; }
        .trig-sum {
            display: flex; align-items: center; justify-content: space-between;
            font-size: 10px; color: #3a5a3a; text-transform: uppercase;
            letter-spacing: 0.3px; font-family: 'SF Mono',monospace;
            padding: 4px 0; transition: color .15s;
        }
        .trig-sum:hover { color: #b8d4b8; }
        .trig-caret { transition: transform .15s; display: inline-block; font-size: 9px; }
        .trig-det[open] .trig-caret { transform: rotate(90deg); color: #00e676; }
        .trig-det[open] .trig-sum { color: #b8d4b8; }
        .trig-body {
            display: flex; flex-direction: column; gap: 4px; margin-top: 6px;
        }
        .trig-chip {
            font-size: 11px; font-family: 'SF Mono',monospace;
            color: #b8d4b8; padding: 4px 8px;
            background: #0f2010; border-radius: 4px;
            word-break: break-all; line-height: 1.3; display: block;
        }
    """)


def _load_skills(summary_container, grid_container):
    from services import get_services
    try:
        services = get_services()
        skills = services.registry.all()
        agents = services.agents.list_all()
        providers = _load_providers()
    except Exception as e:
        logger.error("Skills laden: %s", e)
        with grid_container:
            ui.label(f"Fehler: {e}").style("color: #ef4444;")
        return

    # Agent-Usage pro Skill
    usage: dict[str, list[dict]] = {}
    for ag in agents:
        for sk_id in ag.get("skills", []):
            usage.setdefault(sk_id, []).append(ag)

    # Summary-Kacheln
    total = len(skills)
    available = sum(1 for s in skills if s.is_available(providers))
    unused = sum(1 for s in skills if not usage.get(s.id))

    with summary_container:
        _stat_card("extension",     str(total),     "Skills",        "#00e676")
        _stat_card("check_circle",  str(available), "Verfügbar",     "#22c55e")
        _stat_card("power_off",     str(total - available), "Fehlt Provider", "#ff6b35")
        _stat_card("visibility_off",str(unused),    "Ungenutzt",     "#3a5a3a")

    # Skill-Cards
    with grid_container:
        for skill in sorted(skills, key=lambda s: s.name.lower()):
            _render_skill_card(skill, providers, usage.get(skill.id, []))


def _load_providers() -> dict:
    import json
    try:
        from core.config import PROVIDERS_FILE
        with open(PROVIDERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _stat_card(icon_name: str, value: str, label: str, color: str):
    with ui.element("div").style(
        "background: #070d08; border: 1px solid #0f2010; border-radius: 8px; "
        "padding: 12px 16px; min-width: 110px;"
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


def _render_skill_card(skill, providers: dict, agents: list[dict]):
    available = skill.is_available(providers)
    status_color = "#22c55e" if available else "#ff6b35"
    status_label = "Verfügbar" if available else "Provider fehlt"

    with ui.element("div").classes("skill-card").style(
        "background: #070d08; border: 1px solid #0f2010; border-radius: 8px; "
        "padding: 16px; display: flex; flex-direction: column; gap: 10px;"
    ):
        # Header: Icon + Name + Status
        with ui.row().style("align-items: flex-start; gap: 10px;"):
            with ui.element("div").style(
                "width: 36px; height: 36px; border-radius: 8px; "
                "background: rgba(0,230,118,0.08); border: 1px solid rgba(0,230,118,0.15); "
                "display: flex; align-items: center; justify-content: center; flex-shrink: 0;"
            ):
                ui.icon(skill.icon or "build").style(
                    "font-size: 18px; color: #00e676;"
                )
            with ui.column().style("gap: 2px; flex: 1; min-width: 0;"):
                ui.label(skill.name or skill.id).style(
                    "font-size: 14px; font-weight: 600; color: #e4f4e4; "
                    "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                )
                ui.label(skill.id).style(
                    "font-size: 10px; color: #3a5a3a; "
                    "font-family: 'SF Mono',monospace;"
                )
            with ui.row().style("align-items: center; gap: 4px; flex-shrink: 0;"):
                ui.element("div").style(
                    f"width: 7px; height: 7px; border-radius: 50%; background: {status_color};"
                )
                ui.label(status_label).style(
                    f"font-size: 10px; color: {status_color}; "
                    f"text-transform: uppercase; letter-spacing: 0.3px; "
                    f"font-family: 'SF Mono',monospace;"
                )

        # Beschreibung
        if skill.description:
            ui.label(skill.description).style(
                "font-size: 12px; color: #b8d4b8; line-height: 1.4;"
            )

        # Requirements
        if skill.requires:
            with ui.row().style("align-items: center; gap: 4px; flex-wrap: wrap;"):
                ui.label("Requires:").style(
                    "font-size: 10px; color: #3a5a3a; text-transform: uppercase; "
                    "letter-spacing: 0.3px; font-family: 'SF Mono',monospace;"
                )
                for req in skill.requires:
                    has_it = bool(providers.get(req))
                    col = "#22c55e" if has_it else "#ff6b35"
                    bg = "rgba(34,197,94,0.08)" if has_it else "rgba(255,107,53,0.08)"
                    border = "rgba(34,197,94,0.2)" if has_it else "rgba(255,107,53,0.25)"
                    ui.label(req).style(
                        f"font-size: 10px; font-family: 'SF Mono',monospace; "
                        f"padding: 2px 6px; border-radius: 3px; "
                        f"background: {bg}; color: {col}; "
                        f"border: 1px solid {border};"
                    )

        # Trigger — collapsible via native <details> (kein NiceGUI-Event-Handler nötig,
        # umgeht core.loop-Bug in 3.10 + Py3.14)
        triggers = getattr(skill, "triggers", []) or []
        if triggers:
            chips = "".join(
                f'<code class="trig-chip">{_html.escape(str(t))}</code>'
                for t in triggers
            )
            ui.html(
                f'<details class="trig-det">'
                f'<summary class="trig-sum">'
                f'<span>Trigger ({len(triggers)})</span>'
                f'<span class="trig-caret">▸</span>'
                f'</summary>'
                f'<div class="trig-body">{chips}</div>'
                f'</details>'
            )

        # Agent-Usage
        with ui.row().style(
            "align-items: center; gap: 4px; flex-wrap: wrap; "
            "border-top: 1px solid #0f2010; padding-top: 10px;"
        ):
            ui.label(f"Genutzt von {len(agents)}:").style(
                "font-size: 10px; color: #3a5a3a; text-transform: uppercase; "
                "letter-spacing: 0.3px; font-family: 'SF Mono',monospace;"
            )
            if not agents:
                ui.label("—").style("font-size: 11px; color: #3a5a3a;")
            else:
                for ag in agents[:5]:
                    color = ag.get("color", "#00e676")
                    name = ag.get("name", "?")
                    ui.html(
                        f'<a href="/chat/{ag["id"]}" '
                        f'style="font-size:10px;font-family:\'SF Mono\',monospace;'
                        f'padding:2px 6px;border-radius:3px;'
                        f'background:rgba(255,255,255,0.04);color:{color};'
                        f'border:1px solid rgba(255,255,255,0.06);'
                        f'text-decoration:none;">{name}</a>'
                    )
                if len(agents) > 5:
                    ui.label(f"+{len(agents) - 5}").style(
                        "font-size: 10px; color: #3a5a3a; padding: 2px 4px;"
                    )
