"""
ui/theme.py — AgentClaw Dark-Theme für NiceGUI.
Orientiert sich 1:1 am alten static/css/style.css Design.
"""
from nicegui import ui

COLORS = {
    "bg":          "#050a06",
    "bg2":         "#070d08",
    "bg3":         "#0d1a0e",
    "bg4":         "#111f12",
    "bghov":       "#141f14",
    "b1":          "#0f2010",
    "b2":          "#182e18",
    "green":       "#00e676",
    "gg":          "rgba(0,230,118,.08)",
    "gm":          "rgba(0,230,118,.18)",
    "text":        "#b8d4b8",
    "textbr":      "#e4f4e4",
    "textdim":     "#3a5a3a",
    "mono":        "'SF Mono','Fira Code','Consolas',monospace",
    "orange":      "#ff6b35",
    "odim":        "rgba(255,107,53,.14)",
    "purple":      "#8b5cf6",
    "red":         "#ef4444",
    "cyan":        "#00bcd4",
}


def apply_theme():
    """AgentClaw Dark-Theme auf NiceGUI anwenden."""
    ui.colors(
        primary=COLORS["green"],
        secondary="#00a854",
        accent=COLORS["green"],
        dark=COLORS["bg"],
        positive="#00e676",
        negative="#ef4444",
        info="#64b5f6",
        warning="#ffc107",
    )

    ui.add_css(f"""
        /* ─── CSS Variables ──────────────────────────────── */
        :root {{
            --bg:      {COLORS["bg"]};
            --bg2:     {COLORS["bg2"]};
            --bg3:     {COLORS["bg3"]};
            --bg4:     {COLORS["bg4"]};
            --bghov:   {COLORS["bghov"]};
            --b1:      {COLORS["b1"]};
            --b2:      {COLORS["b2"]};
            --green:   {COLORS["green"]};
            --gg:      {COLORS["gg"]};
            --gm:      {COLORS["gm"]};
            --text:    {COLORS["text"]};
            --textbr:  {COLORS["textbr"]};
            --textdim: {COLORS["textdim"]};
            --mono:    {COLORS["mono"]};
            --orange:  {COLORS["orange"]};
            --odim:    {COLORS["odim"]};
            --purple:  {COLORS["purple"]};
            --red:     {COLORS["red"]};
            --cyan:    {COLORS["cyan"]};
        }}

        /* ─── Base ───────────────────────────────────────── */
        html, body {{
            background: var(--bg) !important;
            color: var(--text) !important;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            overflow: hidden;
            height: 100%;
        }}

        .q-page {{ background: var(--bg) !important; }}

        ::-webkit-scrollbar {{ width: 4px; height: 4px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background: var(--b2); border-radius: 2px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--textdim); }}

        /* ─── Header ─────────────────────────────────────── */
        .q-header {{
            background: var(--bg2) !important;
            border-bottom: 1px solid var(--b1) !important;
            min-height: 44px !important;
            height: 44px !important;
        }}

        /* ─── Nav Buttons ────────────────────────────────── */
        .ac-nav-btn {{
            height: 32px !important;
            padding: 0 8px !important;
            border-radius: 6px !important;
            font-size: 10px !important;
            font-weight: 600 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.5px !important;
            color: var(--textdim) !important;
            transition: background .15s, color .15s !important;
        }}
        .ac-nav-btn:hover {{
            background: var(--gg) !important;
            color: var(--text) !important;
        }}
        .ac-nav-btn.active {{
            background: var(--gg) !important;
            color: var(--green) !important;
            box-shadow: inset 0 -2px 0 var(--green) !important;
        }}
        .ac-logo {{
            color: var(--green) !important;
            font-size: 12px !important;
            font-weight: 700 !important;
            font-family: var(--mono) !important;
            letter-spacing: 1.5px !important;
            text-shadow: 0 0 8px var(--green);
        }}

        /* ─── Layout ─────────────────────────────────────── */
        .q-page-container {{
            padding-top: 44px !important;
            padding-bottom: 0 !important;
            width: 100% !important;
            max-width: 100% !important;
        }}

        .q-page {{
            width: 100% !important;
            max-width: 100% !important;
            min-height: unset !important;
        }}

        /* NiceGUI root-Container */
        #app, #app > div, .q-layout {{
            width: 100% !important;
            max-width: 100% !important;
        }}

        .ac-layout {{
            display: flex;
            height: calc(100vh - 44px);
            overflow: hidden;
            gap: 0;
        }}

        /* ─── Agent Sidebar ──────────────────────────────── */
        .ac-sidebar {{
            width: 228px;
            min-width: 228px;
            flex-shrink: 0;
            background: var(--bg2);
            border-right: 1px solid var(--b1);
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}

        .ac-sidebar-head {{
            padding: 12px 14px 6px;
            font-size: 10px;
            font-weight: 700;
            color: var(--textdim);
            text-transform: uppercase;
            letter-spacing: 1.2px;
            font-family: var(--mono);
            flex-shrink: 0;
            border-bottom: 1px solid var(--b1);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .ac-agent-list {{
            flex: 1;
            overflow-y: auto;
            min-height: 0;
            padding: 4px 6px;
        }}

        .ac-agent-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 8px;
            border-radius: 6px;
            cursor: pointer;
            transition: background .12s;
            border: 1px solid transparent;
            margin-bottom: 2px;
        }}

        .ac-agent-item:hover {{ background: var(--bghov); }}

        .ac-agent-item.selected {{
            background: var(--gg);
            border-color: var(--b2);
        }}

        .ac-agent-item.selected .ac-agent-item-name {{
            color: var(--green) !important;
        }}

        .ac-agent-item.fav-item {{
            border-left: 2px solid #ffd700;
            background: linear-gradient(90deg, rgba(255,215,0,.05) 0%, transparent 100%);
        }}

        .ac-agent-item-name {{
            font-size: 13px;
            font-weight: 500;
            color: var(--text);
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .ac-agent-item-model {{
            font-size: 9px;
            font-family: var(--mono);
            color: var(--textdim);
            background: var(--b1);
            padding: 1px 4px;
            border-radius: 3px;
            flex-shrink: 0;
        }}

        .ac-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--textdim);
            flex-shrink: 0;
        }}

        .ac-dot.online {{ background: var(--green); box-shadow: 0 0 5px var(--green); }}

        .ac-avatar {{
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            font-weight: 700;
            color: #fff;
            flex-shrink: 0;
            text-transform: uppercase;
        }}

        /* ─── Chat Area ──────────────────────────────────── */
        .ac-chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            background: var(--bg);
        }}

        .ac-chat-topbar {{
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 0 24px;
            height: 72px;
            background: var(--bg2);
            border-bottom: 1px solid var(--b1);
            flex-shrink: 0;
        }}

        .ac-chat-topbar-avatar {{
            width: 44px;
            height: 44px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            flex-shrink: 0;
        }}

        .ac-chat-topbar-name {{
            font-size: 16px;
            font-weight: 600;
            color: var(--textbr);
        }}

        .ac-chat-topbar-role {{
            font-size: 12px;
            color: var(--textdim);
            font-family: var(--mono);
        }}

        .ac-topbar-badge {{
            font-size: 10px;
            font-family: var(--mono);
            padding: 2px 7px;
            border-radius: 3px;
            background: var(--b1);
            color: var(--textdim);
            border: 1px solid var(--b2);
        }}

        .ac-topbar-btn {{
            height: 30px;
            padding: 0 12px;
            border-radius: 15px;
            border: 1px solid var(--b2);
            background: var(--bg3);
            color: var(--textdim);
            font-size: 11px;
            font-weight: 500;
            cursor: pointer;
            transition: all .2s;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }}
        .ac-topbar-btn:hover {{
            background: var(--b1);
            color: var(--textbr);
            border-color: var(--green);
            transform: translateY(-1px);
        }}

        /* ─── Chat Messages ──────────────────────────────── */
        .ac-messages {{
            flex: 1;
            overflow-y: auto;
            padding: 16px 20px;
            min-height: 0;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}

        .ac-msg {{
            display: flex;
            flex-direction: column;
            gap: 3px;
            max-width: 820px;
        }}

        .ac-msg.user {{ align-self: flex-end; align-items: flex-end; }}
        .ac-msg.assistant {{ align-self: flex-start; align-items: flex-start; }}

        .ac-msg-meta {{
            font-size: 10px;
            font-family: var(--mono);
            color: var(--textdim);
            padding: 0 4px;
        }}

        .ac-bubble {{
            padding: 10px 14px;
            border-radius: 10px;
            font-size: 14px;
            line-height: 1.6;
            word-break: break-word;
        }}

        .ac-msg.user .ac-bubble {{
            background: var(--gg);
            border: 1px solid var(--b2);
            color: var(--textbr);
            border-bottom-right-radius: 3px;
        }}

        .ac-msg.assistant .ac-bubble {{
            background: var(--bg3);
            border: 1px solid var(--b1);
            color: var(--text);
            border-bottom-left-radius: 3px;
        }}

        .ac-bubble p {{ margin: 0 0 6px; }}
        .ac-bubble p:last-child {{ margin: 0; }}
        .ac-bubble pre {{
            background: var(--b1);
            padding: 10px;
            border-radius: 6px;
            overflow-x: auto;
            font-family: var(--mono);
            font-size: 12px;
            margin: 8px 0;
        }}
        .ac-bubble code {{
            font-family: var(--mono);
            font-size: 12px;
            background: var(--b1);
            padding: 1px 4px;
            border-radius: 3px;
        }}

        /* ─── Chat Input ─────────────────────────────────── */
        .ac-input-area {{
            padding: 12px 20px 16px;
            border-top: 1px solid var(--b1);
            background: var(--bg2);
            flex-shrink: 0;
        }}

        .ac-input-row {{
            display: flex;
            gap: 8px;
            align-items: flex-end;
        }}

        .ac-input-box {{
            flex: 1;
            background: var(--bg3) !important;
            border: 1px solid var(--b2) !important;
            border-radius: 8px !important;
        }}

        .ac-input-box .q-field__control {{
            background: var(--bg3) !important;
        }}

        .ac-send-btn {{
            width: 40px !important;
            height: 40px !important;
            border-radius: 20px !important;
            background: var(--green) !important;
            color: #000 !important;
            flex-shrink: 0;
        }}

        .ac-send-btn:hover {{
            box-shadow: 0 0 12px rgba(0, 230, 118, 0.4) !important;
            transform: translateY(-1px);
        }}

        /* ─── Skill Badges ───────────────────────────────── */
        .ac-skill-badge {{
            font-size: 10px;
            font-family: var(--mono);
            padding: 2px 6px;
            border-radius: 3px;
            background: rgba(0, 230, 118, 0.08);
            color: var(--green);
            border: 1px solid rgba(0, 230, 118, 0.2);
        }}

        /* ─── Agent Cards (Home) ─────────────────────────── */
        .ac-card {{
            background: var(--bg2) !important;
            border: 1px solid var(--b1) !important;
            border-radius: 8px !important;
            cursor: pointer;
            transition: border-color .15s, box-shadow .15s, transform .1s;
        }}

        .ac-card:hover {{
            border-color: var(--green) !important;
            box-shadow: 0 0 20px rgba(0, 230, 118, 0.08) !important;
            transform: translateY(-1px);
        }}

        .ac-card.selected {{
            border-color: var(--green) !important;
            box-shadow: 0 0 16px rgba(0, 230, 118, 0.15) !important;
        }}

        /* ─── Quasar overrides ───────────────────────────── */
        .q-card {{
            background: var(--bg2) !important;
            border: 1px solid var(--b1) !important;
            border-radius: 8px !important;
        }}

        .q-table {{
            background: var(--bg2) !important;
        }}

        .q-table th {{
            color: var(--textdim) !important;
            font-size: 10px !important;
            text-transform: uppercase !important;
            letter-spacing: 0.8px !important;
            font-family: var(--mono) !important;
        }}

        .q-table td {{ color: var(--text) !important; font-size: 12px !important; }}
        .q-table tr:hover td {{ background: var(--bghov) !important; }}
        .q-table__bottom {{ background: var(--bg2) !important; }}

        .q-input .q-field__control, .q-textarea .q-field__control {{
            background: var(--bg3) !important;
            border-color: var(--b2) !important;
        }}

        .q-dialog .q-card {{
            background: var(--bg3) !important;
            border: 1px solid var(--b2) !important;
        }}

        .q-badge {{ font-family: var(--mono) !important; }}
        .q-separator {{ background: var(--b1) !important; }}

        /* ─── Smooth scrolling ───────────────────────────── */
        .ac-messages, .ac-agent-list {{
            scroll-behavior: smooth;
        }}

        /* ─── A2A Delegation Card ────────────────────────── */
        .ac-a2a-card {{
            border-left: 3px solid var(--cyan);
            background: rgba(0, 188, 212, 0.06);
            border-radius: 0 6px 6px 0;
            padding: 8px 12px;
        }}

        /* ─── Typing indicator ───────────────────────────── */
        @keyframes ac-pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.4; }}
        }}

        .ac-typing {{
            animation: ac-pulse 1.2s ease-in-out infinite;
            color: var(--textdim);
            font-family: var(--mono);
            font-size: 12px;
        }}
    """)
