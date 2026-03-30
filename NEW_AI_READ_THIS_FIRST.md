# AgentClaw — Vollständige Übergabe für neue KI

> **Lies dieses Dokument komplett bevor du IRGENDETWAS änderst.**
> Dieses Projekt hat viele Eigenheiten, versteckte Abhängigkeiten und persönliche Konfigurationen.

---

## 1. Was ist AgentClaw?

Eine **lokale Multi-Agent-Plattform** für macOS. Günter Dillenberg führt Gespräche mit KI-Agenten, die:
- Sprache ausgeben (Voxtral TTS / macOS Voices)
- Bilder generieren (ComfyUI)
- Screenshots machen (Playwright)
- Web-News abrufen (SearXNG + Tagesschau)
- Sich **gegenseitig Aufgaben delegieren** (@mention System)
- Langzeit-Erinnerungen speichern (Qdrant + nomic-embed-text)
- Automatisch in Intervallen laufen (Heartbeat-Scheduler)
- Broadcast-Nachrichten an mehrere Agenten gleichzeitig senden

**Stack:** Python Flask Backend + Single-Page Vanilla JS/HTML Frontend. Kein JavaScript-Framework. Kein Build-System.

---

## 2. Verzeichnisstruktur

```
agentclaw/
├── app.py                    ← Flask Backend (~2800 Zeilen, alles in einer Datei)
├── templates/index.html      ← Frontend (~3000 Zeilen, alles in einer Datei)
├── agents.json               ← Agenten-Konfiguration (persistent)
├── history.json              ← Gesprächsverläufe (~100MB, enthält base64-Bilder!)
├── providers.json            ← API-Keys & Service-URLs
├── watchdogs.json            ← URL-Monitoring Konfiguration
├── tasks.json                ← Agent-zu-Agent Tasks (~19MB)
├── .env                      ← MISTRAL_API_KEY
├── requirements.txt          ← flask, python-dotenv, requests
└── venv/                     ← Python virtual environment
```

**Verwandtes Projekt:** `~/Desktop/vectormind/` — eigenständiger Microservice für multimodalen Vektorspeicher (Qdrant + Gemini Embedding 2). Läuft auf Port **7333**. Unabhängig von AgentClaw.

---

## 3. Starten

```bash
cd ~/Desktop/agentclaw
source venv/bin/activate
python app.py
```

**Port:** Auto-Detection 5050–5099. Erster freier Port wird genommen.
**`use_reloader=False`** ist gesetzt — sonst startet Flask zwei Prozesse die sich JSON-Dateien überschreiben.
**URL:** http://localhost:5050

Wenn mehrere Instanzen laufen: `pkill -f "python.*app.py"` dann neu starten.

---

## 4. Frontend-Architektur (index.html)

Das Frontend wurde März 2026 **komplett neu geschrieben** (Matrix/Dark-Green Theme).

### Layout
```
Activity Bar (52px) | Side Panel (228px) | Workspace (flex: 1)
```

### Views
- `home` — Dashboard mit Stats und Agent-Übersicht
- `chat` — Einzelchat mit Agent, TTS, Skills, Voice Input
- `multi` — Broadcast: eine Nachricht → alle ausgewählten Agenten gleichzeitig

### Wichtige JS-State-Variablen
```javascript
let agents = [];              // Array aller Agenten
let currentAgent = null;      // Aktiver Agent
let conversations = {};       // { agentId: [{role, content, ts}] }
let currentView = 'home';     // 'home' | 'chat' | 'multi'
let selectedAgents = new Set(); // Für Broadcast-Modus
let autoPlay = true;          // TTS auto-play
let currentPlayingIndex = -1; // Welcher Play-Button gerade aktiv
let voiceActive = false;      // Mikrofon an/aus
```

### TTS-System (wichtig!)
- Jede Agenten-Antwort hat `▶ Abspielen` + `⬇ Download` Button
- `autoPlay` Toggle im Chat-Header — spielt Antworten automatisch
- **Sentence-chunked:** Text wird in Sätze (~180 Zeichen) aufgeteilt, einzeln an `/api/tts` geschickt
- **Voxtral:** Voice-IDs mit Unterstrich (`en_paul_neutral`, `de_*`) → POST `/api/tts` → gibt rohe MP3-Bytes zurück (KEIN JSON!)
- **Mac Voices:** Prefix `mac:` + Voice-Name → `window.speechSynthesis`
- `/api/tts` antwortet mit `send_file()` (audio/mpeg) — NIE als JSON parsen!

### Screenshot-Skill im Frontend
Wenn Screenshot-Skill aktiv + URL in Nachricht erkannt:
1. POST `/api/screenshot` mit URL
2. Bild im Chat anzeigen
3. `image_data` (base64) an POST `/api/chat` mitschicken

URL-Erkennung: `https://...`, nackte Domains (`web.de`), oder "screenshot von X"-Muster.

### Skill-Buttons
Skills werden als Toggle-Buttons in der Input-Toolbar gerendert.
`toggleSkill(id)` → PUT `/api/agents/<id>` → speichert `skills` Array am Agenten.
`web_search` und `url_fetch` werden **server-seitig** automatisch ausgeführt.

---

## 5. Backend-API (app.py)

### Agenten
```
GET    /api/agents              → agents Array
POST   /api/agents              → neuen Agenten anlegen
PUT    /api/agents/<id>         → Agent bearbeiten
DELETE /api/agents/<id>         → Agent löschen
```

**Agent-Felder:** `id`, `name`, `role`, `soul` (System-Prompt!), `model`, `provider`, `voice`, `color`, `skills`, `max_tokens`, `heartbeat`

⚠️ Das Feld heißt **`soul`** — NICHT `system_prompt`. Häufigster Fehler bei KI-Übergaben.

### History / Chat
```
GET    /api/history/<agent_id>  → Verlauf abrufen
DELETE /api/history/<agent_id>  → Verlauf löschen
POST   /api/chat                → { agent_id, message, image_data? }
                                   Response: { reply, voice, stats? }
```

⚠️ Chat-Response-Feld heißt **`reply`** — NICHT `response` oder `message`.

### Providers
```
GET  /api/providers             → Dict { ollama: {...}, mistral: {...}, ... }
POST /api/providers             → Dict Update { ollama: { api_key: "..." } }
GET  /api/providers/status      → Verbindungs-Status aller Provider
```

⚠️ Providers ist ein **Dict** (nicht Array). POST erwartet z.B. `{ "ollama": { "url": "..." } }`.

### Modelle
```
GET /api/models                 → { ollama: ["model:tag", ...], openrouter: [{id, name, free}] }
```

### TTS & Voice
```
POST /api/tts                   → { text, voice } → raw audio/mpeg bytes (send_file!)
GET  /api/voices/mistral        → Voxtral Voice-Liste
```

### Watchdogs (URL-Monitoring)
```
GET    /api/watchdogs
POST   /api/watchdogs           → { agent_id, url, interval_min, prompt, alert_keyword }
PUT    /api/watchdogs/<id>
DELETE /api/watchdogs/<id>
POST   /api/watchdogs/<id>/run
POST   /api/watchdogs/<id>/toggle
```

⚠️ Watchdog-Schema: **`url`**, **`interval_min`**, **`alert_keyword`** — KEIN `cron`-Feld.

### Tasks (Agent-zu-Agent)
```
POST /api/tasks                 → { from_agent_id, to_agent_id, message, type }
GET  /api/tasks/<id>            → Task-Status polling
GET  /api/activity              → { agent_id: { type, label, since } }
```

### Heartbeat
```
PUT  /api/agents/<id>/heartbeat     → { active, prompt, interval_min }
POST /api/agents/<id>/heartbeat/run → sofort ausführen
```

### Memory (Qdrant)
```
GET    /api/memory/<agent_id>   → { count, collection }
DELETE /api/memory/<agent_id>   → Erinnerungen löschen
```

### ComfyUI
```
GET  /api/comfyui/config        → { url, model }
POST /api/comfyui/generate      → { prompt, width, height, seed? } → { image: base64 }
POST /api/screenshot            → { url } → { image: "data:image/jpeg;base64,...", url }
```

### Misc
```
GET /api/tagesschau?category=top  → Tagesschau News-Feed
GET /api/skills                   → Skills mit availability-Check
```

---

## 6. Provider-Konfiguration (providers.json)

```json
{
  "ollama":     { "url": "http://localhost:11434" },
  "mistral":    { "api_key": "..." },
  "openrouter": { "api_key": "..." },
  "searxng":    { "url": "http://localhost:8888" },
  "comfyui":    { "url": "http://192.168.3.26:8000", "model": "flux2pro" },
  "qdrant":     { "url": "http://localhost:6333" },
  "telegram":   { "bot_token": "...", "chat_id": "..." }
}
```

**ComfyUI** läuft auf separatem GPU-Rechner im Netzwerk.
**Qdrant** läuft lokal (Docker oder native).

---

## 7. Aktuelle Agenten

| Name | Skills aktiv | Heartbeat |
|------|-------------|-----------|
| Picasso | screenshot, image_gen | off |
| LISA | — | off |
| Flo | web_search, tagesschau | aktiv (30min) |
| MARTIN | — | off |
| Zack | web_search, screenshot, image_gen, tagesschau, memory | off |
| Jan | screenshot, tagesschau, memory | aktiv |
| Fotograf | image_gen | aktiv (5min, Strand-Bilder) |

**Persönliche Anmerkung:** Günter Dillenberg ist der Besitzer. `soul`-Felder enthalten persönliche Anweisungen — nicht ohne Auftrag ändern.

---

## 8. Memory-System (Qdrant)

```python
EMBED_MODEL = "nomic-embed-text"   # via Ollama, lokal
EMBED_DIM   = 768
Collection  = f"agent_{agent_id.replace('-', '_')}"
```

Memory-Collections haben **768 Dimensionen**. VectorMind Collections haben **3072 Dimensionen**. Diese NIEMALS mischen — Qdrant erlaubt keine gemischten Dimensionen in einer Collection.

---

## 9. Heartbeat & Scheduler

```python
scheduler_loop()         # alle 30s
  → tick_heartbeats()    # prüft next_run für alle Agenten
    → run_heartbeat(wd)  # LLM-Call oder ComfyUI

_dispatch_mentions_from_prompt()  # @mentions aus Prompt (zuverlässig)
_dispatch_mentions_from_reply()   # @mentions aus LLM-Antwort (Fallback)
```

**Bilder-Sichtbarkeit:** `pollActivity()` im Frontend erkennt active→idle Übergang → `syncAgentHistory()` lädt neue Nachrichten nach.

---

## 10. VectorMind (separates Projekt)

```
~/Desktop/vectormind/
├── main.py      → FastAPI Server Port 7333
├── embedder.py  → Gemini Embedding 2 (3072 dim) + Ollama Fallback (768 dim)
├── store.py     → Qdrant Wrapper (Collections haben Prefix "vm_")
├── .env         → GOOGLE_API_KEY eintragen für Gemini!
└── venv/        → eigene Python-Umgebung (separat von agentclaw!)
```

**Starten:**
```bash
cd ~/Desktop/vectormind
source venv/bin/activate
python main.py
# → http://localhost:7333
# → http://localhost:7333/docs  (Swagger UI)
```

**API:**
```
GET    /                              → Health-Check
GET    /collections                   → alle vm_* Collections
POST   /collections/{name}/add        → Text als JSON einbetten
POST   /collections/{name}/add-file   → Datei hochladen (image/PDF/txt)
POST   /collections/{name}/search     → Semantische Suche
GET    /collections/{name}/items      → Items auflisten
DELETE /collections/{name}/{id}       → Item löschen
DELETE /collections/{name}            → Collection löschen
```

**Embedding-Strategie:**
- Mit `GOOGLE_API_KEY` in `.env` → Gemini Embedding 2, 3072 dim, multimodal (Text + Bilder)
- Ohne Key → Ollama nomic-embed-text, 768 dim, nur Text

**Integration in AgentClaw:** Noch NICHT eingebaut. Nächster Schritt wäre RAG-Endpoint im AgentClaw-Backend der VectorMind per HTTP anspricht.

**Port-Konflikt:** Falls "Address already in use" → `pkill -f "python.*main.py"` dann neu starten.

---

## 11. Lokale Services

| Service | Port | Wozu | Starten |
|---------|------|------|---------|
| Ollama | 11434 | LLM Inference | `ollama serve` |
| Qdrant | 6333 | Memory + VectorMind | Docker oder Binary |
| SearXNG | 8888 | Web-Suche | Docker |
| ComfyUI | 192.168.3.26:8000 | Bildgenerierung | Remote-Rechner |
| AgentClaw | 5050 | Hauptanwendung | `python app.py` |
| VectorMind | 7333 | Vektorspeicher | `python main.py` (optional) |

---

## 12. Installierte Ollama-Modelle (Stand März 2026)

```
mistral-nemo:12b   ~8GB    Allgemein, Deutsch gut, Agent-Workflows
gemma3:latest      ~3.3GB  Reasoning, effizient
moondream:latest   ~2GB    Vision — Bilder beschreiben/analysieren
StarCoder2:latest  ~4GB    Code-Generierung
```

`gemma3:27b` wurde gelöscht (17GB, zu groß für 24GB Mac Mini).

**Auf OpenRouter empfohlen:**
- `minimax/minimax-m2.5` — kostenlos, 196K Kontext, stark für Code
- `qwen/qwen-3.5-9b` — $0.05/M, multimodal, 256K Kontext
- `inception/mercury-2` — >1000 tok/s, gut für Agent-Loops

---

## 13. Bekannte Eigenheiten & Fallstricke

### Kritisch (diese machen Fehler die schwer zu finden sind)
1. **`soul` nicht `system_prompt`** — Backend-Feld für System-Prompts heißt `soul`
2. **`reply` nicht `response`** — Chat-Response-Feld heißt `reply`
3. **`/api/tts` gibt Bytes** — niemals als JSON parsen, immer `response.blob()`
4. **Providers = Dict** — POST erwartet `{ "ollama": {...} }` nicht `{ providers: [...] }`
5. **Watchdogs kein `cron`** — Felder: `url`, `interval_min`, `alert_keyword`

### Daten
6. **history.json sehr groß** — base64-Bilder vom Fotograf (5min Heartbeat). Wird schnell zu Gigabytes.
7. **tasks.json ebenfalls groß** — enthält abgeschlossene Tasks mit Bildern
8. **Cache ist Referenz** — `load_agents()` gibt Cache-Referenz zurück, immer `save_agents()` aufrufen

### Services
9. **Flo halluziniert News** — tagesschau-Skill wird in `run_heartbeat()` nicht automatisch aufgerufen
10. **Mehrere Server-Instanzen** — `use_reloader=False` verhindert das meistens. Bei Problemen: `pkill -f "python.*app.py"`
11. **Memory-Dim ≠ VectorMind-Dim** — 768 vs 3072, nie mischen

---

## 14. Offene Punkte / TODO

- [ ] **VectorMind → AgentClaw RAG** — Knowledge-Base Endpoints im Backend, automatische Suche im Chat
- [ ] **Google API Key** in `~/Desktop/vectormind/.env` eintragen für Gemini Embedding 2
- [ ] **Flo's tagesschau-Skill** in `run_heartbeat()` tatsächlich ausführen
- [ ] **history.json optimieren** — Bilder als separate Dateien
- [ ] **Mac App** — pywebview + py2app Wrapper (Konzept besprochen, nicht umgesetzt)
- [ ] **Sprach-Auswahl** im Agent-Edit übersichtlicher gestalten (Nutzer-Feedback: zu unübersichtlich)

---

## 15. Änderungen März 2026 (diese Session mit Claude Code)

### index.html — Komplett-Redesign
- Matrix/Dark-Green Theme (`--green: #00e676`, `--bg: #050a06`)
- Activity Bar + Side Panel + Workspace Layout (VS Code-Style)
- Multi-Agent Broadcast View
- TTS: ▶ Abspielen Buttons pro Nachricht, sentence-chunked, Auto-Play Toggle
- Screenshot-Skill: URL-Erkennung für nackte Domains + "screenshot von X"
- Bild-Upload mit Preview
- Skill-Toggle-Buttons in Toolbar
- Model-Dropdown im Agent-Edit (lädt von `/api/models`, provider-aware)
- API-Felder korrigiert: `soul`, `reply`, Provider-Dict, Watchdog-Schema
- History beim Agent-Wechsel vom Server laden

### app.py — Bugfix
- `comfyui_generate()`: `deadline`/`while`-Block war aus `try`-Block raus-indentiert (SyntaxError durch OpenCode-Änderung). Gefixt.

### VectorMind — Neu erstellt
- `~/Desktop/vectormind/` eigenständiger FastAPI Microservice
- Qdrant Backend, Gemini Embedding 2 (3072 dim), Ollama Fallback (768 dim)
- Getestet: Text einbetten, semantische Suche, Collection-Management

---

## 16. Regeln für die übernehmende KI

1. **Erst lesen, dann schreiben.** Write-Tool schlägt fehl ohne vorheriges Read.
2. **app.py NIEMALS komplett neu schreiben.** 2800+ Zeilen, nur gezielt editieren.
3. **Syntax prüfen vor Neustart:**
   ```bash
   source venv/bin/activate && python -c "import py_compile; py_compile.compile('app.py')"
   ```
4. **Venv aktivieren:** `source venv/bin/activate` — gilt für beide Projekte (eigene venvs!)
5. **Git nach Änderungen:** `git add -p && git commit -m "..."` — keine `-A` Commits ohne Review
6. **Persönliche Daten:** agents.json und history.json enthalten Günters persönliche Daten und Gespräche. Nicht löschen, nicht publik machen.
7. **VectorMind unabhängig halten:** Eigenes venv, eigener Port 7333, kein Code nach agentclaw/ kopieren.
8. **Port-Konflikte lösen:** `pkill -f "python.*app.py"` bzw. `pkill -f "python.*main.py"` bei "Address already in use".
