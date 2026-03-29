# AgentClaw — Übergabe-Dokument für neue KI

> Lies dieses Dokument vollständig bevor du irgendwas änderst.

## Was ist AgentClaw?

Eine lokale Multi-Agent-Plattform. Benutzer führt Gespräche mit KI-Agenten, die Aufgaben autonom delegieren, Bilder generieren, News abrufen, Screenshots machen und sich gegenseitig Aufgaben schicken können.

**Stack:** Flask (Python) Backend + Single-Page HTML/JS Frontend (kein Framework, vanilla JS).

---

## Architektur-Überblick

```
app.py                     ← Flask Backend (alles in einer Datei)
templates/index.html       ← Frontend (alles in einer Datei, ~3500 Zeilen)
agents.json                ← Agenten-Konfiguration (persistent)
history.json               ← Gesprächsverläufe (persistent, enthält base64-Bilder!)
providers.json             ← API-Keys & URLs (Ollama, Mistral, OpenRouter, ComfyUI, etc.)
watchdogs.json             ← Watchdog-Konfiguration
tasks.json                 ← Laufende/abgeschlossene Tasks (enthält base64-Bilder!)
```

### In-Memory Datenstrukturen (Backend)

```python
_TASKS: dict       # { task_id: task_dict } — Agent-zu-Agent Aufgaben
_ACTIVITY: dict    # { agent_id: {type, label, since} } — Live-Aktivitätsstatus
_cache: dict       # { "agents"|"history"|"providers"|"watchdogs": data } — Datei-Cache
```

Alle Schreibzugriffe gehen durch `_write_json()` + separaten Lock pro Datei.

---

## Port-Konfiguration

Der Server findet beim Start automatisch den ersten freien Port ab 5050:

```python
# app.py, Ende der Datei
port = 5050
while port < 5100:
    try: socket.bind(('', port)); break
    except OSError: port += 1
app.run(debug=True, port=port, use_reloader=False)
```

**Starten:** `python3 app.py`
**use_reloader=False** ist wichtig — sonst startet Flask zwei Instanzen die sich gegenseitig die agents.json überschreiben.

---

## Agenten-System

Jeder Agent hat:
- `id`, `name`, `role`, `soul` (System-Prompt), `model`, `provider`, `voice`, `skills`, `color`
- `max_tokens` (optional, None = Modell-Standard)
- `heartbeat` (optional): `{ active, prompt, interval_min, next_run, last_run, last_result }`

### Aktuelle Agenten

| Name | Rolle | Skills | Heartbeat |
|------|-------|--------|-----------|
| Picasso | Creative Artist | screenshot, image_gen | off |
| LISA | Document & File Manager | — | off |
| Flo | Communication Manager | web_search, tagesschau | **aktiv** (30min) |
| MARTIN | AI Orchestrator | — | off |
| Zack | Fun | web_search, screenshot, image_gen, tagesschau, memory | off |
| Jan | Journalist & News | screenshot, tagesschau, memory | **aktiv** |
| Fotograf | — | image_gen | **aktiv** (5min, Strand-Fotos) |

---

## Agent-zu-Agent Protokoll

**User-getriggert:** User schreibt `@Picasso erstelle ein Bild von X`
→ Frontend erkennt @mention → `handleAgentMention()` → `POST /api/tasks` → polling

**KI-getriggert (aus Heartbeat):** Flo's Heartbeat-Prompt enthält `@Picasso`
→ `run_heartbeat()` ruft LLM auf → `_dispatch_mentions_from_prompt()` liest @mentions
aus dem PROMPT (nicht aus der LLM-Antwort!) → erstellt Task für Picasso

**KI-getriggert (aus Chat):** Wenn LLM-Antwort `@AgentName` enthält
→ `dispatchReplyMentions()` im Frontend

### Task-Lifecycle

```
POST /api/tasks → task{status:"pending"} → process_task() thread
                                         → status:"processing"
                                         → image_gen ODER llm
                                         → result_image / result_text
                                         → save to history (beide Agenten)
                                         → status:"done"
GET /api/tasks/<id> → polling im Frontend
```

---

## Heartbeat-System

```python
scheduler_loop() → tick_heartbeats() alle 30s → run_heartbeat(agent)
```

- `run_heartbeat()` prüft `hb["next_run"]`
- Wenn `image_gen` Skill: ComfyUI direkt, kein LLM, + zufällige Location/Mood/Style Modifier
- Sonst: LLM-Call mit `prompt_for_llm` (ohne @mentions), dann `_dispatch_mentions_from_prompt()`

**Wichtig:** `next_run = None` = sofort beim nächsten Tick ausführen.

---

## Bild-Sichtbarkeit (gelöst)

Problem: Heartbeat generiert Bild, speichert in history, aber Chat zeigt es nicht.

Fix: `pollActivity()` erkennt active→idle Übergang. Für den aktuell geöffneten Agenten ruft `syncAgentHistory()` die neueste History ab und hängt neue Nachrichten an den Chat an.

```javascript
// Wenn Agent gerade aktiv war und jetzt fertig ist:
const justFinished = Object.keys(agentActivity).filter(id => !data[id]);
if (justFinished.includes(currentAgent.id)) await syncAgentHistory(currentAgent.id);
// Andere Agenten: Cache invalidieren
for (const id of justFinished) delete conversations[id];
```

History-Einträge mit `_fromServer: true` markieren → `syncAgentHistory` zählt die und holt nur neue.

---

## Provider-Konfiguration

```json
ollama:     http://localhost:11434   (lokal)
mistral:    API-Key in .env / providers.json
openrouter: API-Key in providers.json
searxng:    http://localhost:8888   (lokal, für web_search)
comfyui:    http://192.168.3.26:8000 (Netzwerk-GPU-Rechner)
qdrant:     http://localhost:6333   (lokal, für memory skill)
```

ComfyUI nutzt das Workflow `build_z_image_turbo_workflow()` — Modell: `z_image_turbo_bf16.safetensors`.

---

## Skills

| ID | Was es tut | Voraussetzung |
|----|-----------|---------------|
| `web_search` | SearXNG-Suche, wird auto-getriggert bei News/Aktualität-Keywords | SearXNG lokal |
| `url_fetch` | Liest URL-Inhalte aus Nachrichten | — |
| `screenshot` | Playwright Browser-Screenshot | Playwright installiert |
| `image_gen` | ComfyUI Bildgenerierung | ComfyUI erreichbar |
| `tagesschau` | Ruft tagesschau.de API ab | — |
| `memory` | Qdrant Langzeitspeicher (nomic-embed-text Embeddings via Ollama) | Qdrant + Ollama |

---

## Bekannte Eigenheiten

1. **history.json wird sehr groß** — enthält rohe base64-Bilder (1-5MB pro Bild). Bei vielen Heartbeats wächst sie schnell. Evtl. Bilder separat speichern.

2. **Flo halluziniert Nachrichten** — hat `tagesschau` Skill, aber `run_heartbeat()` nutzt den Skill nicht automatisch. Der LLM erfindet News. Fix: In `run_heartbeat()` den tagesschau-Skill vor dem LLM-Call ausführen und als Kontext injizieren.

3. **Heartbeat-Prompt @mentions** — werden aus dem PROMPT gelesen (zuverlässig), nicht aus der LLM-Antwort (unzuverlässig). Der LLM-Reply wird als Task-Nachricht an den Zielagenten geschickt.

4. **Mehrere Server-Instanzen** — `use_reloader=False` verhindert Doppelstart. Wenn trotzdem mehrere laufen: `pkill -f "python3 app.py"` dann neu starten.

5. **Cache-Konsistenz** — `load_agents()` gibt Cache-Referenz zurück (kein Copy). Direkte Mutation des Dicts aktualisiert den Cache. Immer `save_agents()` aufrufen um auf Disk zu schreiben.

6. **max_tokens=None** = kein Limit setzen (Modell-Standard). Gut für längere Heartbeat-Antworten.

---

## Offene Punkte / TODO

- [ ] Flo's tagesschau-Skill in `run_heartbeat()` tatsächlich ausführen (nicht nur halluzinieren lassen)
- [ ] history.json optimieren: Bilder als separate Dateien, history nur Referenz speichern
- [ ] Pagination für lange Histories im Frontend
- [ ] Conversation-Memory Bounds (MAX_HISTORY_PER_AGENT = 500 bereits gesetzt)
- [ ] Fotograf's Bilder im Frontend direkt sehen (syncAgentHistory sollte greifen)

---

## Letzte große Änderungen (diese Session)

- `_dispatch_mentions_from_prompt()` — Heartbeat dispatcht Tasks via @mention aus Prompt
- `_dispatch_mentions_from_reply()` — Fallback: aus LLM-Antwort
- `syncAgentHistory()` + `pollActivity()` Fix — neue Bilder erscheinen automatisch im Chat
- Phone Simulator entfernt
- Max Tokens Feld im Agent-Edit (leer = kein Limit)
- Heartbeat-Prompt: textarea statt input (größer)
- Port-Auto-Detect 5050–5099, `use_reloader=False`
