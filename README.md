# LogpyClaw v3

CDC-native Multi-Agent-System mit zeitdilatations-bewusstem Routing.

## Architektur

LogpyClaw v3 verwendet eine **Causal-Dilation Clock (CDC)** auf jeder internen
Message — als Pflichtfeld, nicht als optionale Metadata. Jeder Agent trägt ein
`(Vector, Dilation)`-Tupel, das kausal Ordnung und subjektive Eigenzeit (τ)
erfasst. Der **Conductor** dispatcht Messages zwischen Agenten, der
**MartinAgent** übernimmt Operator-Routing und QC-Loops, und der
**A2A-Gateway** übersetzt zwischen dem externen A2A-Protokoll und CDC.

```
User → MartinAgent → Conductor → Zielagent → Antwort
              ↓
         QC-Loop (Auditor-Delegation, Score ≥ 7)
```

---

## Setup

**Voraussetzungen:** Python 3.12+, [Ollama](https://ollama.com) lokal oder remote

```bash
# 1. Repository klonen
git clone <repo-url>
cd agentclaw-v3

# 2. Virtualenv erstellen und Abhängigkeiten installieren
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Konfiguration (optional — alle Werte haben Defaults)
cp .env.example .env   # falls vorhanden, sonst manuell erstellen
```

**.env Beispiel:**

```env
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:e4b
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
WEB_BRIDGE_TOKEN=secret
DB_URL=sqlite:///./logpyclaw.db
MARTIN_QC_ENABLED=true
MARTIN_QC_MIN_SCORE=7
MARTIN_QC_MAX_RETRIES=2
MARTIN_QC_AUDITOR_ID=
```

---

## Start

```bash
./start.sh
```

Oder manuell:

```bash
source .venv/bin/activate
uvicorn backend.app:app --host 0.0.0.0 --port 6060 --reload
```

Frontend: http://localhost:6060  
API-Docs: http://localhost:6060/docs  
Health: `curl http://localhost:6060/ping`

---

## Konfiguration

| Env-Variable | Default | Beschreibung |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama-Endpunkt |
| `OLLAMA_MODEL` | `gemma4:e4b` | Standard-Modell |
| `ANTHROPIC_API_KEY` | — | Für Anthropic-Agenten |
| `OPENAI_API_KEY` | — | Für OpenAI-Agenten |
| `WEB_BRIDGE_TOKEN` | — | Auth-Token für `/ext/dilles/v1/*` |
| `DB_URL` | `sqlite:///./logpyclaw.db` | SQLAlchemy-URL |
| `MARTIN_QC_ENABLED` | `true` | Martin QC-Loop aktivieren |
| `MARTIN_QC_MIN_SCORE` | `7` | Minimum-Score (1–10) |
| `MARTIN_QC_MAX_RETRIES` | `2` | Max. Retry-Versuche |
| `MARTIN_QC_AUDITOR_ID` | — | Agent-ID des Auditors (leer = kein QC) |

---

## Skills

Skills sind ausführbare Fähigkeiten, die Agenten über den `SkillAgent` aufrufen.

| Skill-ID | Klasse | Beschreibung |
|---|---|---|
| `websearch` | `WebSearchSkill` | DuckDuckGo Instant Answers (kein API-Key) |
| `whatsapp` | `WhatsAppSkill` | WhatsApp-Nachrichten senden (wacli) |
| `comfyui` | `ComfyUISkill` | Bildgenerierung via lokalen ComfyUI-Server |

Skill aufrufen via Chat:

```
#skill:websearch Python asyncio tutorial
```

Neuen Skill hinzufügen: Klasse in `backend/skills/` anlegen, von `Skill` erben,
in `app.py` mit `conductor.register(SkillAgent(MySkill()))` registrieren.

---

## Martin-Befehle

Martin ist der Operator-Agent und versteht folgende Routing-Syntax:

```
# Direkte Agent-Adressierung
@agent:alice Erkläre mir CDC in 3 Sätzen

# Skill-Routing
#skill:websearch Aktuelle Python 3.13 Features

# Fraktions-Routing (an ersten Agenten der Fraktion)
#faction:makers Schreibe eine Fibonacci-Funktion

# Normaler Text → LLM-Router (Ollama) wählt Zielagenten automatisch
Wer ist Einstein?
```

QC-Loop: Wenn `MARTIN_QC_AUDITOR_ID` gesetzt ist, delegiert Martin nach
jeder Antwort an den Auditor-Agenten (Score-Abfrage 1–10). Bei Score < `MARTIN_QC_MIN_SCORE`
wird der Task mit verbessertem Prompt wiederholt (max. `MARTIN_QC_MAX_RETRIES` Mal).

---

## Dateistruktur

```
backend/
├── core/
│   ├── cdc.py              # CausalDilationClock (V,D)-Tupel
│   ├── protocol.py         # Message, TaskRecord, MessageType
│   ├── logging.py          # get_logger(), LogBroadcaster (SSE)
│   └── team_protocol.py    # Team, TeamMessage, γ_ij-Matrix
├── agents/
│   ├── base.py             # AsyncAgent ABC
│   ├── conductor.py        # Mission-Dispatcher + Watchdog
│   ├── martin.py           # Operator-Agent (Routing + QC)
│   ├── llm_agent.py        # Ollama / Anthropic / OpenAI
│   ├── skill_agent.py      # Skill-Wrapper-Agent
│   └── a2a_gateway.py      # A2A↔CDC-Übersetzer
├── api/
│   ├── agents.py           # GET /api/agents, POST /api/agents/spawn
│   ├── chat.py             # POST /api/chat, SSE /api/chat/stream
│   ├── missions.py         # /api/missions/*, /spacetime
│   ├── logs.py             # SSE /api/logs (Live-Log-Stream)
│   ├── factions.py         # GET /api/factions
│   ├── teams.py            # /api/teams/*
│   ├── web_bridge.py       # /ext/dilles/v1/* (dillenberg.net)
│   └── a2a/
│       └── gateway_router.py  # /a2a/tasks/send, /.well-known/agent.json
├── skills/
│   ├── __init__.py         # Skill ABC
│   ├── websearch.py        # DuckDuckGo
│   ├── whatsapp.py         # WhatsApp via wacli
│   └── comfyui.py          # ComfyUI Bildgenerierung
├── storage/
│   ├── mission_store.py    # In-Memory Traces + SSE-Queues
│   └── sqlite_store.py     # SQLite-Persistenz
├── i18n/                   # en.py / de.py + t()-Funktion
└── config.py               # pydantic-settings Settings
frontend/
└── index.html              # Single-File-Frontend (kein Build-Step)
tests/
└── test_*.py
```

---

## Protokolle

### CDC (Causal-Dilation Clock)

Jede Message trägt `clock: { vector: {...}, dilation: {...} }`.

- `vector`: Lamport-style kausale Ordnung pro Agent
- `dilation`: Kumulative Eigenzeit τ (Operation-Count gewichtet)

4 Relationen: `ORDERED` | `CAUSAL_DRIFT` | `CONCURRENT_DRIFT` | `INCONSISTENT`

### A2A Gateway

```
External A2A client  →  POST /a2a/tasks/send
                              ↓
                      A2AGatewayAgent
                      wrap_a2a_task() → CDC Message
                              ↓
                      Conductor.dispatch()
                              ↓
                      Interner Agent (Alice, etc.)
                              ↓
                      unwrap_cdc_response() → A2A Artifact
```

---

## Entwicklung

```bash
# Tests
python -m pytest tests/ -v

# Linting
ruff check backend/ tests/
ruff check backend/ tests/ --fix

# Server neu starten (Port 6060)
lsof -ti :6060 | xargs kill -9 2>/dev/null; ./start.sh
```

Coding-Konventionen: siehe [STYLEBOOK.md](STYLEBOOK.md).

---

## Multilingual

Default: **Englisch**. Deutsch unterstützt.

- Backend: `t(key, locale)` in `backend/i18n/` — Keys in `en.py` / `de.py` eintragen
- Frontend: `I18N`-Objekt + `data-i18n="key"`-Attribute — Toggle EN|DE im Header
