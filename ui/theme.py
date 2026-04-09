"""
ui/theme.py — AgentClaw Dark-Theme für NiceGUI.
"""
from nicegui import ui

COLORS = {
    "bg": "#050a06",
    "surface": "#0a0f0a",
    "surface2": "#111811",
    "border": "#1a2e1a",
    "accent": "#00e676",
    "accent_dim": "#00a854",
    "text": "#e0e0e0",
    "text_muted": "#666d66",
    "error": "#ff5252",
    "warning": "#ffc107",
    "info": "#64b5f6",
}


def apply_theme():
    """AgentClaw Dark-Theme auf NiceGUI anwenden."""
    ui.colors(
        primary=COLORS["accent"],
        secondary=COLORS["accent_dim"],
        accent=COLORS["accent"],
        dark=COLORS["bg"],
        positive="#00e676",
        negative="#ff5252",
        info="#64b5f6",
        warning="#ffc107",
    )
    ui.add_css(f"""
        :root {{
            --ac-bg: {COLORS["bg"]};
            --ac-surface: {COLORS["surface"]};
            --ac-surface2: {COLORS["surface2"]};
            --ac-border: {COLORS["border"]};
            --ac-accent: {COLORS["accent"]};
            --ac-text: {COLORS["text"]};
            --ac-text-muted: {COLORS["text_muted"]};
        }}

        body, .q-page, html {{
            background: var(--ac-bg) !important;
            color: var(--ac-text) !important;
            font-family: 'JetBrains Mono', 'Fira Code', monospace, sans-serif;
        }}

        .q-header {{
            background: var(--ac-surface) !important;
            border-bottom: 1px solid var(--ac-border) !important;
        }}

        .q-drawer {{
            background: var(--ac-bg) !important;
            border-right: 1px solid var(--ac-border) !important;
        }}

        .q-card {{
            background: var(--ac-surface) !important;
            border: 1px solid var(--ac-border) !important;
            border-radius: 8px !important;
        }}

        .q-card:hover {{
            border-color: var(--ac-accent) !important;
            transition: border-color 0.2s;
        }}

        .q-btn.q-btn--flat {{
            color: var(--ac-text-muted) !important;
        }}

        .q-btn.q-btn--flat:hover {{
            color: var(--ac-accent) !important;
            background: rgba(0, 230, 118, 0.08) !important;
        }}

        .q-input .q-field__control {{
            background: var(--ac-surface2) !important;
            border-color: var(--ac-border) !important;
        }}

        .q-textarea .q-field__control {{
            background: var(--ac-surface2) !important;
        }}

        .ac-nav-btn {{
            color: var(--ac-text-muted) !important;
            font-size: 0.85rem;
            letter-spacing: 0.05em;
        }}

        .ac-nav-btn.active {{
            color: var(--ac-accent) !important;
        }}

        .ac-logo {{
            color: var(--ac-accent) !important;
            font-size: 1.1rem;
            font-weight: 700;
            letter-spacing: 0.15em;
        }}

        .ac-agent-name {{
            color: var(--ac-text) !important;
            font-weight: 600;
        }}

        .ac-role {{
            color: var(--ac-text-muted) !important;
            font-size: 0.8rem;
        }}

        .ac-message-user {{
            background: rgba(0, 230, 118, 0.08) !important;
            border-left: 3px solid var(--ac-accent) !important;
            border-radius: 0 6px 6px 0 !important;
            padding: 12px 16px !important;
        }}

        .ac-message-assistant {{
            background: var(--ac-surface) !important;
            border-left: 3px solid var(--ac-surface2) !important;
            border-radius: 0 6px 6px 0 !important;
            padding: 12px 16px !important;
        }}

        .ac-activity-badge {{
            background: rgba(0, 230, 118, 0.15) !important;
            color: var(--ac-accent) !important;
            border: 1px solid rgba(0, 230, 118, 0.3) !important;
            border-radius: 4px !important;
            font-size: 0.75rem;
            padding: 2px 8px;
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.6; }}
        }}

        .scrollbar-dark::-webkit-scrollbar {{ width: 4px; }}
        .scrollbar-dark::-webkit-scrollbar-track {{ background: var(--ac-bg); }}
        .scrollbar-dark::-webkit-scrollbar-thumb {{ background: var(--ac-border); border-radius: 2px; }}
    """)
