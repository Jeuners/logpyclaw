# AgentClaw v2 — Claude Code Kontext

## Was ist das?
Lokales Multi-Agent-AI-System für macOS. NiceGUI + FastAPI Backend.

## Wichtige Dateien
| Datei | Beschreibung |
|---|---|
| `app.py` | NiceGUI + FastAPI Einstiegspunkt (248 Zeilen) |
| `ui/pages/chat.py` | Chat-Interface (JS-basiertes Send/Streaming) |
| `ui/pages/home.py` | Dashboard mit Agent-Cards |
| `ui/layout.py` | Header-Navigation (44px) |
| `ui/theme.py` | CSS-Theme |
| `api/` | 17 FastAPI-Router |
| `services/` | ServiceContainer (DI) |
| `storage/database.py` | SQLite + JSON-Migration |

## Architektur
- **NiceGUI 3.10.0 + Python 3.14** — Server-seitige reaktive UI
- **FastAPI** — REST-API für A2A, M2M, externe Clients
- **A2A Delegation:** `@AgentName Task` in Antworten → automatischer Dispatch
- **SSE Streaming:** `/api/chat/stream` für Token-by-Token Chat
- **Navigation:** `<a href>` für Links (NICHT `ui.navigate.to()`)

## ⚠️ KRITISCH: NiceGUI core.loop Bug (v1.89)
**NiceGUI 3.10 + Python 3.14 hat einen fundamentalen Bug:**
`ui.run_javascript()`, Element-Updates, Timer-Erstellung und `ui.notify()` 
funktionieren **NICHT** aus Event-Handlern (`on_click`, `on_keydown` etc.).

**Fehler:** `AssertionError: core.loop is not None` in `background_tasks.create()`

**Lösung für Chat:** Komplett client-seitiges JavaScript:
- Send/Streaming via `fetch()` + `ReadableStream` zum SSE-Endpoint
- DOM-Updates via `insertAdjacentHTML()` 
- Event-Listener via `addEventListener()` (NICHT inline `onclick`)
- NiceGUI nur für initiales Page-Rendering

**Merke:** Bei JEDEM neuen interaktiven Feature prüfen ob es aus einem 
Event-Handler aufgerufen wird. Wenn ja → JavaScript-Lösung verwenden!

## Dev-Workflow
```bash
# App starten
source .venv/bin/activate
python app.py

# Läuft auf
http://localhost:5050

# Tests
curl -s http://localhost:5050/ping
curl -s http://localhost:5050/api/agents | python -m json.tool
```

## Skills-System
12 Skills in `services/`: image_gen, video_gen, image_edit, youtube, telegram, 
gmail, transcription, file_access, linkedin, prompt_optimize, url_fetch, mac_mail.
