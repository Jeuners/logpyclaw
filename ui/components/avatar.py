"""
ui/components/avatar.py — Zentrale Avatar-Darstellung für alle Agenten.

Eine Quelle der Wahrheit: `agent['avatar']` kann sein
  - "" / None        → farbiger Initialen-Kreis (Fallback)
  - "data:image/..." → Base64-Bild
  - "/static/..."    → Pfad zu statischer Datei (gemountet)
  - "http(s)://..."  → externe URL
  - "🤖" / Emoji     → 1–2 Zeichen werden als Zeichen-Avatar gerendert

Benutzung:
    ui.html(render_avatar(agent, size=44))
"""
from __future__ import annotations
import html as _html


def _esc(s: str) -> str:
    return _html.escape(s or "", quote=True)


def _is_image_src(avatar: str) -> bool:
    return bool(avatar) and (
        avatar.startswith("data:image/")
        or avatar.startswith("/")
        or avatar.startswith("http://")
        or avatar.startswith("https://")
    )


def render_avatar(agent: dict, size: int = 44, *, ring: bool = True) -> str:
    """Gibt den Avatar als HTML-String zurück (für `ui.html(...)`).

    Args:
        agent:  Agent-Dict (muss 'name', 'color' und optional 'avatar' enthalten).
        size:   Kantenlänge in px.
        ring:   Wenn True, wird bei Bild-Avataren ein farbiger Ring (agent.color) gesetzt.
    """
    name = agent.get("name", "?") or "?"
    color = agent.get("color", "#00e676") or "#00e676"
    avatar = (agent.get("avatar") or "").strip()

    base = (
        f"width:{size}px;height:{size}px;border-radius:50%;"
        f"flex-shrink:0;display:block;box-sizing:border-box;"
    )

    # Bild-Avatar
    if _is_image_src(avatar):
        border = f"border:2px solid {_esc(color)};" if ring else ""
        return (
            f'<img src="{_esc(avatar)}" alt="{_esc(name)}" '
            f'style="{base}object-fit:cover;{border}"/>'
        )

    # Zeichen-Avatar (Emoji oder Initialen)
    if avatar and len(avatar) <= 4:
        glyph = _esc(avatar)
        bg = color
        fs = int(size * 0.55)
    else:
        glyph = _esc(name[:2].upper() if len(name) >= 2 else name[:1].upper())
        bg = color
        fs = int(size * 0.38)

    return (
        f'<div style="{base}background:{_esc(bg)};color:#000;'
        f'display:flex;align-items:center;justify-content:center;'
        f'font-size:{fs}px;font-weight:700;text-transform:uppercase;'
        f'line-height:1">{glyph}</div>'
    )
