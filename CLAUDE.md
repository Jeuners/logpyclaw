# AgentClaw v2 — Claude Code Kontext

## Was ist das?
Lokales Multi-Agent-AI-System für macOS. NiceGUI + FastAPI Backend.
Kein Cloud-Dienst. Läuft vollständig lokal auf Port **5050**.

## Schnellstart
```bash
source .venv/bin/activate
python app.py
# → http://localhost:5050                    (Hauptapp)
# → http://localhost:5050/lab               (Communication Lab)
# → http://localhost:5050/lab/spacetime     (Spacetime-Visualisierung)
# → http://localhost:5050/ltx-batch        (Video Batch Renderer)
# → http://localhost:5050/static/about.html (Projektübersicht)

curl -s http://localhost:5050/ping   # → {"pong":true}

# Server-Neustart (bei laufendem Prozess):
lsof -ti :5050 | xargs kill -9; sleep 1; nohup python app.py > /tmp/agentclaw.log 2>&1 &
```

## Wichtige Dateien
| Datei | Beschreibung |
|---|---|
| `app.py` | NiceGUI + FastAPI Einstiegspunkt (Port 5050) |
| `ui/pages/chat.py` | Chat-Interface (JS-basiertes Send/Streaming) |
| `ui/pages/home.py` | Dashboard mit Agent-Cards |
| `ui/pages/ltx_batch.py` | LTX 2.3 Batch Video Renderer UI |
| `ui/layout.py` | Header-Navigation mit About-Link |
| `ui/theme.py` | CSS-Theme |
| `api/ltx_batch.py` | LTX Batch API (Prepare/Render/QC/Concat) |
| `api/temporal.py` | Temporal-API (Eigenzeit-Endpoints) |
| `core/causal_dilation_clock.py` | CDC-Implementierung (§3.4 Paper) |
| `core/temporal_policy.py` | Temporal-Policy für Agenten |
| `services/` | ServiceContainer (DI) |
| `storage/database.py` | SQLite + JSON-Migration |
| `lab/` | Communication Lab (isoliert, Mock-Agenten) |
| `static/about.html` | Projekt-Erklärungsseite (Stärken/Schwächen/Roadmap) |
| `static/kids-explainer.html` | Erklärvideo für Nicht-Techniker (DE) |
| `scripts/cleanup.sh` | Wartungsscript (LTX-Jobs, Logs, DB, Cache) |

## Architektur
- **NiceGUI 3.10.0 + Python 3.14** — Server-seitige reaktive UI
- **FastAPI** — REST-API für A2A, M2M, externe Clients
- **24 FastAPI-Router** in `api/`
- **A2A Delegation:** `@AgentName Task` in Antworten → automatischer Dispatch
- **SSE Streaming:** `/api/chat/stream` für Token-by-Token Chat
- **Navigation:** `<a href>` für Links (NICHT `ui.navigate.to()`)
- **Port:** 5050

## ⚠️ KRITISCH: NiceGUI core.loop Bug (v1.89)
**NiceGUI 3.10 + Python 3.14 hat einen fundamentalen Bug:**
`ui.run_javascript()`, Element-Updates, Timer-Erstellung und `ui.notify()`
funktionieren **NICHT** aus Event-Handlern (`on_click`, `on_keydown` etc.).

**Lösung:** Komplett client-seitiges JavaScript:
- Send/Streaming via `fetch()` + `ReadableStream` zum SSE-Endpoint
- DOM-Updates via `insertAdjacentHTML()`
- Event-Listener via `addEventListener()` (NICHT inline `onclick` — Vue sanitisiert das!)
- NiceGUI nur für initiales Page-Rendering

---

## Communication Lab (`/lab`)
**Isoliertes Subpaket** zum Testen von A2A/M2M Protokollen. Kein LLM, keine DB.

```
lab/
├── core/
│   ├── protocol.py       # Message-Spec + LabClock (wraps core CDC)
│   ├── mock_agent.py     # Inbox-basierte Test-Agenten (7 Policies)
│   ├── conductor.py      # Mission-Orchestrator + Watchdog
│   ├── store.py          # In-Memory State (nie DB!)
│   └── tracer.py         # SSE Live-Events
├── ui/
│   ├── lab_page.py       # /lab — 3-Spalten-Layout
│   └── spacetime_page.py # /lab/spacetime — Spacetime-Diagramm
└── api/
    ├── lab_router.py          # /api/lab/*
    └── dilation_demo_router.py
```

### Mock-Agent Policies
| Policy | Verhalten |
|---|---|
| `echo` | Sofort done |
| `slow` | delay_sec warten, dann done |
| `delegator` | Delegiert an delegates_to[], aggregiert |
| `silent` | Antwortet nie (Watchdog-Test) |
| `flaky` | error_prob Fehlerrate |
| `reviewer` | Bewertet Ergebnis mit QC-Score 1-10 |
| `qc_delegator` | Executor + optionaler Reviewer (qc_rate=0.6) |

### QC-Doppel-Agenten Prinzip
```
Auftrag → qc_delegator → executor (Arbeit)
                              ↓ Ergebnis
              60% der Fälle: → reviewer (Score 1-10)
                              ↓ Score ≥ min_score?
                         Ja  → approved + zurück
                         Nein → retry (max qc_max_retries)
```

**Spawn-Beispiel:**
```bash
curl -X POST http://localhost:5050/api/lab/agents/spawn \
  -H "Content-Type: application/json" \
  -d '{"name":"martin","policy":"qc_delegator","delegates_to":["executor"],
       "qc_agent":"reviewer","qc_rate":0.6,"qc_min_score":7,"qc_max_retries":2}'

curl -X POST http://localhost:5050/api/lab/missions/start \
  -H "Content-Type: application/json" \
  -d '{"title":"Test","start_agent":"martin","initial_content":"Mache X"}'
```

### Lab API Endpoints
| Endpoint | Beschreibung |
|---|---|
| `GET /api/lab/agents` | Alle Agenten |
| `POST /api/lab/agents/spawn` | Agent spawnen |
| `DELETE /api/lab/agents/{name}` | Agent entfernen |
| `POST /api/lab/missions/start` | Mission starten |
| `GET /api/lab/missions` | Alle Missionen |
| `GET /api/lab/missions/{id}/trace` | Message-Verlauf |
| `GET /api/lab/missions/{id}/temporal` | LLM-lesbares Zeitgefühl |
| `GET /api/lab/missions/{id}/spacetime` | Spacetime-Daten (nodes, edges, drift) |
| `GET /api/lab/missions/{id}/stream` | SSE Live-Stream |
| `GET /api/lab/scheduler/recommend` | Drift-kompensierter Scheduler (γ_ij) |
| `POST /api/lab/reset` | Lab komplett zurücksetzen |

---

## Causal-Dilation Clock (CDC)
**Paper:** https://github.com/Jeuners/Time_Dilation_in_LLM_Agent_Systems

### Kernkonzept (§3.4 Paper)
Jede Message trägt eine CDC-Uhr als **(V, D)-Tupel**:
- `vector` — Kausalordnung (Lamport-style Vector-Clock)
- `dilation` — Eigenzeit-Rate (ops/s) pro Agent

### Core-Implementierung (`core/causal_dilation_clock.py`)
```python
cdc = CausalDilationClock()
cdc.tick("agent_a", op_weight=1.0)    # Eigenzeit erhöhen
cdc.merge(other_cdc)                   # Max-Merge zweier Clocks
relation = cdc.relate(other)           # CDCRelation enum
cdc.transform(other, gamma=1.5)        # γ_ij Frame-Transformation
cdc.to_dict() / CausalDilationClock.from_dict(d)
```

**CDCRelation Werte:** `ORDERED`, `CAUSAL_DRIFT`, `CONCURRENT_DRIFT`, `INCONSISTENT`

### Lab-Erweiterung (`lab/core/protocol.py::CausalDilationClock`)
Extends core CDC — **kein Duplikat**:
- `tick_lab(agent_id, rate)` — Lab-Tick mit ops/s-Rate
- `llm_summary()` → `"martin:fast(ez=4,rate=2.6) | reviewer:normal(ez=2,rate=1.3)"`
- `relate_lab(other)` → String

### Drift-kompensierter Scheduler
`GET /api/lab/scheduler/recommend?candidates=worker,reviewer`
- Berechnet γ_ij (relative Dilation = avg_rate / 1.0) aus Missions-History
- Empfiehlt Agenten mit minimalem Drift + nicht busy
- Gibt `gamma`, `drift_score`, `recommendation_score` zurück

### Spacetime-Diagramm (`/lab/spacetime`)
Interaktives SVG-Diagramm aus echten Mission-Daten:
- **X-Achse** = Agenten (farbige Weltlinien)
- **Y-Achse** = Eigenzeit (ops-Ticks, nicht Wanduhrzeit!)
- **Pfeile** = Messages (Bezier-Kurven, farbcodiert nach Typ)
- **Rot gestrichelt** = CAUSAL_DRIFT-Kanten
- **Button** „🎯 Scheduler-Empfehlung" zeigt γ_ij-Karten für alle Agenten

---

## LTX Batch (`/ltx-batch`)
WAV + Bild → 9s Segmente → Ollama Prompts → ComfyUI LTX 2.3

**ComfyUI:** `http://192.168.4.15:8000`
**Ollama:** `http://localhost:11434` (Modell: `gemma4:e4b`)
**Vision:** `moondream:latest` (3.5s, zuverlässig — NICHT gemma4 für Bilder verwenden!)

### LTX API Endpoints
| Endpoint | Beschreibung |
|---|---|
| `POST /api/ltx-batch/prepare` | WAV + Bild hochladen, Segmente erstellen |
| `POST /api/ltx-batch/render/{job_id}` | Rendern starten |
| `GET /api/ltx-batch/status/{job_id}` | Job-Status |
| `GET /api/ltx-batch/stream/{job_id}` | SSE Live-Updates |
| `POST /api/ltx-batch/concat/{job_id}` | Fertige Segmente → Master-MP4 |
| `GET /api/ltx-batch/master/{job_id}/{filename}` | Master-Video abrufen |

### Frame-Chaining
Letztes Frame von Video N → Startbild für Video N+1:
`ffmpeg -sseof -0.5 -i video.mp4 -frames:v 1 last_frame.png`

### Daten-Pfade
- Events/Persistenz: `data/ltx_batch/{job_id}.jsonl`
- Job-Dir: `data/ltx_batch/{job_id}/` (chunks/, source.wav, source.png, state.json)
- Master-Videos: `data/ltx_batch/{job_id}/master_{ts}.mp4`

### Nachträglicher Concat
```bash
curl -X POST http://localhost:5050/api/ltx-batch/concat/{job_id}
# Lädt alle fertigen Segmente von ComfyUI, ffmpeg concat, → Master-MP4
```

### Cleanup
```bash
bash scripts/cleanup.sh           # Echtes Löschen (LTX-Jobs, Log, DB-VACUUM, pycache)
bash scripts/cleanup.sh --dry-run # Nur anzeigen was gelöscht würde
```

---

## Skills-System
19 Skills registriert bei Start:
`image_gen, video_gen, image_edit, talking_video, youtube, transcription,
file_access, linkedin, prompt_optimize, url_fetch, mac_mail, coding,
chrome_browser, hacker_news, tagesschau, whatsapp, wiki_read, web_search, wikipedia`

---

## Dev-Workflow
```bash
source .venv/bin/activate
python app.py

# Tests
python -m pytest tests/ -x -q

# Logs
tail -f /tmp/agentclaw.log
tail -f agentclaw.log

# Health-Check
curl -s http://localhost:5050/ping
curl -s http://localhost:5050/api/lab/agents
```

## Git
```bash
git add -A && git commit -m "feat: ..." && git push
# Remote: https://github.com/Jeuners/agentclaw.git
```

## Externe Ressourcen
- **CDC-Paper/Explainer:** https://github.com/Jeuners/Time_Dilation_in_LLM_Agent_Systems
- **ComfyUI:** http://192.168.4.15:8000
- **Ollama:** http://localhost:11434

---

## Bekannte Bugs / Workarounds
| Bug | Workaround |
|---|---|
| NiceGUI core.loop Bug (v1.89) | Alle interaktiven Features als JS fetch() + DOM-Manipulation |
| `disconnect async handler error` in Logs | Harmlos, NiceGUI-intern, ignorieren |
| gemma4:e4b hängt bei Bildanalyse | moondream:latest verwenden (3.5s) |
| Lab-State geht bei Neustart verloren | In-Memory only — kein Disk-Persist im Lab |
| LTX video_url fehlt nach Neustart | state.json enthält video_url; Concat liest daraus |
| inline onclick in ui.html() | Von Vue sanitisiert → addEventListener() verwenden |
