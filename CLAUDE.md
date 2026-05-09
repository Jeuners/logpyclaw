# AgentClaw v2 — Claude Code Kontext

## Was ist das?
Lokales Multi-Agent-AI-System für macOS. NiceGUI + FastAPI Backend.

## Wichtige Dateien
| Datei | Beschreibung |
|---|---|
| `app.py` | NiceGUI + FastAPI Einstiegspunkt |
| `ui/pages/chat.py` | Chat-Interface (JS-basiertes Send/Streaming) |
| `ui/pages/home.py` | Dashboard mit Agent-Cards |
| `ui/pages/ltx_batch.py` | LTX 2.3 Batch Video Renderer UI |
| `ui/layout.py` | Header-Navigation (44px) |
| `ui/theme.py` | CSS-Theme |
| `api/` | 22 FastAPI-Router |
| `api/ltx_batch.py` | LTX Batch API (Prepare/Render/QC) |
| `api/lab_router.py` | Communication Lab API |
| `services/` | ServiceContainer (DI) |
| `storage/database.py` | SQLite + JSON-Migration |
| `core/causal_dilation_clock.py` | CDC-Implementierung (§3.4 Paper) |
| `lab/` | Communication Lab (isoliert, Mock-Agenten) |

## Architektur
- **NiceGUI 3.10.0 + Python 3.14** — Server-seitige reaktive UI
- **FastAPI** — REST-API für A2A, M2M, externe Clients
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
- Event-Listener via `addEventListener()` (NICHT inline `onclick`)
- NiceGUI nur für initiales Page-Rendering

## Communication Lab (`/lab`)
**Isoliertes Subpaket** zum Testen von A2A/M2M Protokollen. Kein LLM, keine DB.

```
lab/
├── core/
│   ├── protocol.py     # Message-Spec + LabClock (wraps core CDC)
│   ├── mock_agent.py   # Inbox-basierte Test-Agenten
│   ├── conductor.py    # Mission-Orchestrator + Watchdog
│   ├── store.py        # In-Memory State (nie DB!)
│   └── tracer.py       # SSE Live-Events
├── api/lab_router.py   # /api/lab/*
└── ui/lab_page.py      # /lab
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
curl -X POST /api/lab/agents/spawn -d '{
  "name":"martin","policy":"qc_delegator",
  "delegates_to":["executor"],"qc_agent":"reviewer",
  "qc_rate":0.6,"qc_min_score":7,"qc_max_retries":2
}'
```

### Causal-Dilation Clock (Bauchzeitgefühl)
Jede Message trägt eine CDC-Uhr:
- `vector` — Kausalordnung (Lamport-style)
- `dilation` — Eigenzeit-Rate (ops/s) pro Agent
- `llm_summary()` → `"martin:fast(ez=4,rate=2.6) | reviewer:normal(ez=2,rate=1.3)"`

`lab/core/protocol.py::CausalDilationClock` extends `core/causal_dilation_clock.py` — **kein Duplikat**.

Temporal Summary per Mission: `GET /api/lab/missions/{id}/temporal`

## LTX Batch (`/ltx-batch`)
WAV + Bild → 9s Segmente → Ollama Prompts → ComfyUI LTX 2.3

**ComfyUI:** `http://192.168.4.15:8000`  
**Whisper:** WAV → Transkript als Prompt-Basis  
**Frame-Chaining:** Letztes Frame von Video N → Startbild für Video N+1

## Dev-Workflow
```bash
source .venv/bin/activate
python app.py
# → http://localhost:5050
# → http://localhost:5050/lab
# → http://localhost:5050/ltx-batch

curl -s http://localhost:5050/ping
curl -s http://localhost:5050/api/lab/agents
```

## Skills-System
19 Skills: image_gen, video_gen, image_edit, talking_video, youtube, telegram,
transcription, file_access, linkedin, prompt_optimize, url_fetch, mac_mail,
coding, chrome_browser, hacker_news, tagesschau, whatsapp, wiki_read, web_search.
