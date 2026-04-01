# AgentClaw — Vollständige Übergabe für neue KI

> **Lies dieses Dokument komplett bevor du IRGENDETWAS änderst.**
> Dieses Projekt hat viele Eigenheiten, versteckte Abhängigkeiten und persönliche Konfigurationen.

---

## 1. Was ist AgentClaw?

Eine **lokale Multi-Agent-Plattform** für macOS. Agenten können:
- Sprache ausgeben (Voxtral TTS / macOS Voices)
- Bilder generieren (ComfyUI)
- Screenshots machen (Playwright)
- Web-News abrufen (Tagesschau, Hacker News)
- Sich **gegenseitig Aufgaben delegieren** (@mention System)
- Langzeit-Erinnerungen speichern (Qdrant + nomic-embed-text)
- Automatisch in Intervallen laufen (Heartbeat-Scheduler)
- Broadcast-Nachrichten an mehrere Agenten gleichzeitig senden

**Stack:** Python Flask Backend + Single-Page Vanilla JS/HTML Frontend. Kein JavaScript-Framework. Kein Build-System.

---

## 2. Verzeichnisstruktur

```
agentclaw/
├── app.py                    ← Flask Backend (~5000 Zeilen, alles in einer Datei)
├── templates/index.html      ← Frontend (~4500 Zeilen, alles in einer Datei)
├── agents.json               ← Agenten-Konfiguration (persistent)
├── history.json              ← Gesprächsverläufe (~100MB, enthält base64-Bilder!)
├── providers.json            ← API-Keys & Service-URLs
├── watchdogs.json            ← URL-Monitoring Konfiguration
├── tasks.json                ← Agent-zu-Agent Tasks
├── requirements.txt          ← flask, flask-socketio, python-socketio, eventlet, python-dotenv, requests
└── venv/                     ← Python virtual environment
```

---

## 3. Starten

```bash
cd ~/Desktop/agentclaw
source venv/bin/activate
python app.py
```

**Port:** 5050 (fest)
**URL:** http://localhost:5050

WebSocket: `socketio.run()` mit `async_mode="threading"` für Background Tasks.

Wenn mehrere Instanzen laufen: `pkill -f "python.*app.py"` dann neu starten.

---

## 4. Wichtige Felder (häufigste Fehler!)

- **`soul`** — System-Prompt des Agenten (NICHT `system_prompt`)
- **`reply`** — Chat-Response-Feld (NICHT `response` oder `message`)
- **`/api/tts`** gibt **rohe MP3-Bytes** zurück — NIEMALS als JSON parsen, immer `fetch().blob()`
- **Providers = Dict** — POST erwartet `{ "ollama": {...} }` nicht Array
- **Watchdogs: `url`, `interval_min`, `alert_keyword`** — KEIN `cron`-Feld

---

## 5. Skills (Übersicht)

| Skill | Beschreibung |
|-------|-------------|
| `image_gen` | ComfyUI Bildgenerierung |
| `image_edit` | ComfyUI Bildbearbeitung |
| `screenshot` | Playwright Screenshots |
| `url_fetch` | URL Inhalte abrufen |
| `tagesschau` | Deutsche Nachrichten |
| `hackernews` | Hacker News Top Stories |
| `memory` | Qdrant Vector Memory |
| `document_memory` | PDF/Image Upload zu Qdrant |
| `prompt_optimize` | Prompt Optimierung (RTF, TAG, BAB, CARE, RISE) |
| `telegram` | Telegram Bot senden/empfangen |
| `gmail` | Gmail senden/abrufen |

---

## 6. WebSocket / Socket.IO (Echtzeit)

```python
# Backend
from flask_socketio import SocketIO, emit, join_room, leave_room
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# Events:
# - agent_activity: Agent start/stop working
# - task_result: A2A Task abgeschlossen
# - heartbeat_result: Heartbeat Output
# - chat_message: Neue Chatnachricht
# - error: Fehler
```

```javascript
// Frontend
socket = io('/ws', { transports: ['websocket', 'polling'] });
socket.on('agent_activity', (data) => { ... });
socket.on('task_result', (data) => { ... });
```

**Background Tasks:** `spawn_background(target, *args)` nutzt `threading.Thread` (NICHT eventlet greenlets).

---

## 7. Agent-zu-Agent (A2A) Protocol

### Task States
```
submitted → working → input-required → completed/failed/canceled/rejected/auth-required
```

### Task Processing
```python
process_task(task_id)  # Background Worker
├── activity_start()    # WebSocket broadcast
├── Skill detection (image_gen, telegram, gmail, hackernews, etc.)
├── Execute skill
├── activity_end()      # WebSocket broadcast
├── emit_task_result()  # WebSocket broadcast
└── Save to history
```

### Dispatch
- `_dispatch_mentions_from_prompt()` — @mentions im Heartbeat-Prompt
- `_dispatch_mentions_from_reply()` — @mentions in LLM-Antworten

---

## 8. Dream Agent (Memory Cleanup)

```python
def run_dream_for_agent(agent_id):
    """Löscht Memory-Einträge älter als retention_days aus Qdrant."""
    client = get_qdrant()
    retention_days = agent.get("dream", {}).get("retention_days", 30)
    cutoff = datetime.now() - timedelta(days=retention_days)
    # ... lösche alte Einträge
```

**API:**
- `PUT /api/agents/<id>/dream` — Dream konfigurieren (active, retention_days)
- `POST /api/agents/<id>/dream/run` — Sofort ausführen

---

## 9. Provider-Konfiguration (providers.json)

```json
{
  "ollama":     { "url": "http://localhost:11434" },
  "mistral":    { "api_key": "..." },
  "openrouter": { "api_key": "..." },
  "google":     { "api_key": "..." },
  "comfyui":    { "url": "http://localhost:8188", "model": "flux2pro" },
  "qdrant":     { "url": "http://localhost:6333" },
  "telegram":   { "bot_token": "...", "chat_id": "..." }
}
```

---

## 10. Lokale Services

| Service | Port | Starten |
|---------|------|---------|
| Ollama | 11434 | `ollama serve` |
| Qdrant | 6333 | Docker oder Binary |
| ComfyUI | 8188 | Lokal oder Remote |
| AgentClaw | 5050 | `python app.py` |

---

## 11. Datenbanken

- **Ollama Embeddings:** `nomic-embed-text`, 768 Dimensionen
- **Qdrant:** Vector Memory pro Agent (`agent_{id}`)

---

## 12. Bekannte Fallstricke

1. **`soul`** nicht `system_prompt`
2. **`reply`** nicht `response`
3. **`/api/tts`** gibt Bytes — als blob parsen
4. **Providers = Dict** nicht Array
5. **history.json sehr groß** — enthält base64 Bilder
6. **Mehrere Server** → `pkill -f "python.*app.py"`
7. **Telegram Polling** — kann deaktiviert werden in `scheduler_loop()`

---

## 13. Änderungen April 2026

- WebSocket Support via Flask-SocketIO
- Image Placeholder mit Loading-Spinner
- Hacker News Skill implementiert
- Dream Agent für Memory Cleanup
- Telegram Polling optional (disabled per default)
- Fixed threading für Background Tasks

---

## 14. Regeln für die übernehmende KI

1. **Erst lesen, dann schreiben.** Write-Tool schlägt fehl ohne vorheriges Read.
2. **app.py NIEMALS komplett neu schreiben.** 5000+ Zeilen, nur gezielt editieren.
3. **Syntax prüfen:** `python -c "import py_compile; py_compile.compile('app.py')"`
4. **Venv aktivieren:** `source venv/bin/activate`
5. **Git nach Änderungen:** `git add -p && git commit -m "..."`
6. **Persönliche Daten:** agents.json und history.json nicht publik machen.
7. **Port-Konflikte:** `pkill -f "python.*app.py"` bei "Address already in use".
