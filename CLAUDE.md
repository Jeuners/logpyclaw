# AgentClaw — Claude Code Kontext

## Was ist das?
Lokales Multi-Agent-AI-System für macOS. Flask-Backend + Single-Page-App (HTML/JS in `templates/index.html`).

## Wichtige Dateien
| Datei | Beschreibung |
|---|---|
| `app.py` | Flask-Backend, alle API-Routen, Skills, WebSocket (5400+ Zeilen) |
| `templates/index.html` | Komplette Frontend-SPA mit inline JS + CSS (4600+ Zeilen) |
| `static/css/style.css` | Externes CSS — **noch nicht im Template verlinkt** |
| `main_app.py` | macOS-Einstiegspunkt via pywebview |
| `agents.json` | Agent-Konfigurationen |
| `history.json` | Chat-Historien |
| `providers.json` | API-Keys und Provider-Settings |

## Architektur
- **WebSocket Namespace:** immer `/ws` — kein anderer Namespace
- **Threading:** `async_mode='threading'` in SocketIO — kein Eventloop, kein asyncio nötig
- **A2A Delegation:** Agent antwortet mit `@AgentName Task` → `dispatchReplyMentions()` erkennt und dispatcht
- **Datei-Locks:** `_agents_lock`, `_history_lock`, `_tasks_lock` für JSON-Schreibops nutzen

## Bekannte Offene Punkte
- Keine bekannten CSS/Style-Probleme

## Dev-Workflow
```bash
# App starten
python app.py

# Läuft auf
http://localhost:5001
```

## Skills-System
Skills werden in `SKILLS`-Liste in `app.py` definiert und haben `id`, `name`, `icon`, `description`, `requires`.
Aktivierung per Agent-Settings → `agent.skills = ["url_fetch", "memory", ...]`.
