# LogpyClaw v3

CDC-natives Agenten-Protokoll mit zentralem Orchestrator und
zeitdilatations-bewusstem Routing — die Vorstufe zu einem Multi-Agent-System,
bei dem Zeit, Vertrauen und Kausalität im Protokoll stecken statt im Framework.

> **Zur Freigabe** — Der Code ist frei ([MIT-Lizenz](LICENSE)). Die zugrunde
> liegende Idee — Causal-Dilation Clock und Fraktionsmodell — ist und bleibt
> mein gedankliches Werk; wer die Konzepte weiterträgt, möge auf dieses
> Projekt verweisen. Ich gebe den Code frei, weil er mir geholfen hat, Sinn
> zu verstehen. Vielleicht hilft er auch dem einen oder anderen.
>
> *„Erst durch Zeit und Raum bin ich mir bewusst bewusst."* ;)

## Architektur

LogpyClaw v3 verwendet eine **Causal-Dilation Clock (CDC)** auf jeder internen
Message — als Pflichtfeld, nicht als optionale Metadata. Jeder Agent trägt ein
`(Vector, τ, Rate)`-Tripel: kausale Ordnung, kumulative Eigenzeit (τ) und
Momentanrate (ops/s, EWMA-geglättet). Der **Conductor** dispatcht Messages
zwischen Agenten und verdrahtet dabei das Fraktionssystem (Envelope,
Trust-Learning, Adversarial-Bridge), der **MartinAgent** übernimmt
Operator-Routing und QC-Loops, und der **A2A-Gateway** übersetzt zwischen dem
externen A2A-Protokoll und CDC.

```
User → MartinAgent → Conductor → Zielagent → Antwort
              ↓
         QC-Loop (Auditor-Delegation, Score ≥ 7)
```

---

## Einordnung — was das ist, und was (noch) nicht

Der Begriff "Multi-Agent-System" wird in der Branche oft überdehnt, deshalb
hier die ehrliche Einordnung: Zur Laufzeit ist LogpyClaw v3 heute ein
**zentral orchestriertes System** — ein Conductor dispatcht, Martin routet,
die Topologie ist sternförmig (Request/Response). Die eigene Evaluation
belegt das: alle klassifizierbaren Message-Paare stehen in der Relation
ORDERED, echte Nebenläufigkeit entsteht erst mit den parallelen Plan-Wellen.
Wer ein emergentes System erwartet, in dem Agenten spontan miteinander
verhandeln, findet hier (noch) keines.

Der Unterschied zu einem reinen "Agent Manager + Tool Layer" liegt im
**Protokoll**: CDC auf jeder Message, gerichtetes gelerntes Vertrauen zwischen
Fraktionen, Adversarial-Bridges und ein A2A-Gateway sind für Peer-Verkehr
gebaut — die Infrastruktur ist da, der Dispatcher nutzt sie erst teilweise.
Der Weg zu "echt": agenten-initiierte Missionen, parallele autonome Branches,
Peer-Dispatch ohne Operator. Der CDC-Klassifikator ist dabei das Messgerät,
an dem sich dieser Übergang ablesen lassen wird.

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

# Normaler Text → LLM-Planner/Router wählt Zielagenten automatisch
Wer ist Einstein?
```

**Explizite Adressierung gewinnt immer**: Enthält die Nachricht `@agent:`,
`#skill:` oder `#faction:`, wird direkt delegiert — der LLM-Planner wird
übersprungen und kann die Original-Spezifikation nicht umschreiben.

QC-Loop: Wenn `MARTIN_QC_AUDITOR_ID` gesetzt ist, delegiert Martin nach
jeder Antwort an den Auditor-Agenten (Score-Abfrage 1–10, der Auditor sieht
Aufgabe UND Ergebnis). Bei Score < `MARTIN_QC_MIN_SCORE` wird der Task mit
verbessertem Prompt wiederholt (max. `MARTIN_QC_MAX_RETRIES` Mal). Fällt der
Auditor aus, wird durchgewunken statt teuer zu retryen. Skills sind vom QC
ausgenommen (deterministisch).

Multi-Step-Pläne laufen in parallelen Wellen entlang `depends_on`
(max. 20 Steps pro Plan, max. 4 parallel — DoS-Schutz).

Fraktionssystem im Dispatch: Der Conductor baut automatisch das
FactionEnvelope, lernt Trust/γ aus jedem Outcome und leitet ADVERSARIAL-Verkehr
über Martins Operator-Bridge um (fail-closed: ohne Bridge wird abgelehnt).

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

Jede Message trägt `clock: { vector: {...}, tau: {...}, dilation: {...} }`.

- `vector`: Lamport-style kausale Ordnung pro Agent
- `tau`: Kumulative Eigenzeit τ pro Agent (Σ op_weights, merge: max)
- `dilation`: Momentanrate in ops/s (EWMA, merge: aktuellerer Vector-Stand gewinnt)

4 Relationen (Vergleich auf τ-Basis): `ORDERED` | `CAUSAL_DRIFT` | `CONCURRENT_DRIFT` | `INCONSISTENT`

Cross-faction-Drift wird über `classify_drift()` (faction_protocol) reklassifiziert:
`EXPECTED_DRIFT` (kausal geordnet, Tempo-Verhältnis ≈ gelerntes γ) und
`FACTION_RACE` (nebenläufig, strukturell erwartet) lösen keinen Alarm aus.

Hinweis: `signing_payload()` kanonisiert weiterhin nur `vector` + `dilation` —
`tau` ist abgeleitete Buchhaltung. So bleiben alte PQC-Hash-Chains verifizierbar.
`verify_chain()` ist fail-closed: eine Mission ganz ohne Signaturen gilt nicht als valid.

### Trust & γ — die Mathematik

Damit das Fraktionsmodell nicht philosophisch bleibt, hier die exakten
Update-Regeln (alles in `backend/core/faction_protocol.py`):

**Vertrauen** ist eine geglättete Erfolgsrate mit Beta(1,1)-Prior (Laplace):

```
trust = (S + 1) / (S + F + 2)
```

Eigenschaften: beschränkt auf (0,1), Startwert 0.5, konvergiert gegen die
empirische Erfolgsrate. `success` ist definiert als RESPONSE statt ERROR der
abgeschlossenen Interaktion; gelernt wird automatisch bei jedem
Cross-Faction-Dispatch im Conductor.

**Vertrauen altert** (Evidenz-Halbwertszeit, Default 7 Tage): vor jedem
Update werden S und F mit `0.5^(Δt/T½)` abgezinst.

```
S ← S·0.5^(Δt/T½) + outcome,   F ← F·0.5^(Δt/T½) + (1−outcome)
```

Der Erwartungswert bleibt erhalten, aber die effektive Stichprobengröße
sinkt — frische Evidenz bewegt altes Vertrauen wieder, und ohne Kontakt
kehrt trust beim nächsten Update Richtung Prior zurück. "Verworfen" wird
Vertrauen also nie schlagartig, es verjährt kontinuierlich.

**γ (Tempo-Verhältnis source/target)** ist ein EWMA über beobachtete
Raten-Verhältnisse aus den CDC-Clocks beider Seiten:

```
γ ← (1−α)·γ + α·(rate_source / rate_target),   α = 0.2
```

Gewicht einer k Updates alten Beobachtung: `α(1−α)^k` — effektives
Gedächtnis ≈ 1/α = 5 Interaktionen. Startwert 1.0 (keine relative Dilation).
`classify_drift()` nutzt γ, um Cross-Faction-Drift als EXPECTED_DRIFT zu
reklassifizieren, wenn das beobachtete Verhältnis innerhalb der
Tempo-Toleranz der Empfänger-Fraktion liegt.

**Sicherheitseigenschaft**: trust beeinflusst ausschließlich
Routing-Prioritäten. Die Bridge-Pflicht für adversariale Paare hängt an der
`stance` (Policy, nicht gelernt) — hohes Vertrauen kann keine adversariale
Schranke freischalten, und die Bridge ist fail-closed.

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
