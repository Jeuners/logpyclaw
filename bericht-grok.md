# Bericht: LogpyClaw v3 — Projektanalyse (Grok, April 2026)

**Erstellt von:** Grok 4.3 (xAI)  
**Datum:** 2026-05 (basierend auf aktuellem Workspace)  
**Auftrag:** Reine Bestandsaufnahme ohne jegliche Code-Änderungen.  
**Ziel:** Vollständiger, strukturierter Bericht über das aktuelle Projekt in `/Users/jeuner/Desktop/agentclaw-v3`.

---

## 1. Projekt-Identifikation

- **Name:** LogpyClaw v3 (auch „agentclaw-v3“)
- **Version:** 0.1.0 (pyproject.toml) / API 3.0.0
- **Technologie-Stack:**
  - Backend: Python 3.12+, FastAPI + Uvicorn, SQLModel/SQLAlchemy (SQLite), httpx, APScheduler, pqcrypto (ML-DSA-65)
  - Frontend: Single-File HTML/JS (kein Build-Step), JetBrains-Mono-Ästhetik, SVG-Spacetime-Visualisierung
  - Protokoll: Eigenes **Causal-Dilation Clock (CDC)** als Pflichtfeld auf jeder Message
- **Einstiegspunkt:** `./start.sh` → uvicorn `backend.app:app` auf Port 6060
- **Hauptdokumente:** `README.md`, `STYLEBOOK.md`, `docs/DESIGN.md`, `docs/whatsapp-sync.md`

**Kurzbeschreibung (aus README):**  
„CDC-native Multi-Agent-System mit zeitdilatations-bewusstem Routing.“  
MartinAgent als Operator + QC-Loop, Conductor als Dispatcher, A2A-Gateway als Brücke zu externen Protokollen.

---

## 2. Kernarchitektur & Philosophie

### 2.1 Causal-Dilation Clock (CDC) — `backend/core/cdc.py`
Jede interne `Message` trägt **zwingend** ein `(Vector, Dilation)`-Tupel:

- **Vector:** Lamport-Style Kausalordnung pro Agent (int)
- **Dilation (τ):** Kumulative Eigenzeit (float, ops/s gewichtet)
- **4 Relationen** (`CDCRelation`):
  - `ORDERED`
  - `CAUSAL_DRIFT`
  - `CONCURRENT_DRIFT`
  - `INCONSISTENT` (immer Bug)

Methoden: `tick()`, `merge()`, `relate()`, `llm_summary()`, `transform()` mit γ-Matrix.

CDC ist **nicht optional** — wird in `AsyncAgent.advance_clock()` bei jedem Schritt aktualisiert.

### 2.2 Message-Protokoll — `backend/core/protocol.py`
- `Message` mit `msg_id`, `mission_id`, `task_id`, `parent_task_id`, `clock`, Payload, optional PQC-Feldern (chain_idx, prev_hash, sig ML-DSA-65)
- Factory-Methoden: `request()`, `response()`, `error()`, `heartbeat()`
- `TaskRecord` + `TaskState` (CREATED → ASSIGNED → RUNNING → terminal)
- ID-Prefixe: `t_`, `m_`, `mis_`, `team_`, `agent:`, `ext:`, `skill:`

### 2.3 Fraktionssystem — `backend/core/faction_protocol.py`
Sechs persistente Fraktionen (Identity, nicht Role):

| Fraktion     | Archetyp     | Rolle                          | Farbe (UI)     |
|--------------|--------------|--------------------------------|----------------|
| operators    | OPERATORS    | Routing, QC, Meta (Martin)     | Gold           |
| makers       | MAKERS       | Generative Arbeit (Code/Bild)  | Violett        |
| auditors     | AUDITORS     | QC, Review, Score 1-10         | Orange         |
| gatherers    | GATHERERS    | Recherche, Web, Mail, Files    | Emerald        |
| scribes      | SCRIBES      | Gedächtnis, History, Summary   | Skyblue        |
| guardians    | GUARDIANS    | Policy, Safety, Grenzen        | Rot            |

- `FactionCharter` (mission_lens, do/don’t, delegation_policy)
- `FactionRelation` mit gerichtetem Trust (Beta-Prior) + γ (EWMA)
- `ADVERSARIAL`-Stance → nur über Martin-Bridge (`_faction.requires_bridge`)
- `FactionRegistry` (Singleton) mit `load_defaults()`

### 2.4 Team-Protokoll — `backend/core/team_protocol.py`
Ergänzung zu Point-to-Point-A2A:

- `Team` mit `MemberRecord` (busy, reachable, avg_rate, clock)
- Gemeinsame `team_clock` (Max-Merge)
- `gamma_matrix` (γ_ij = rate_i / rate_j)
- Drift-kompensierter Dispatcher: `recommend_next()` / `recommend_details()`
- `TeamMessage` erweitert normale Message

### 2.5 MartinAgent (Operator) — `backend/agents/martin.py`
Kern-Routing- und QC-Instanz:

- Syntax: `@agent:alice`, `#skill:websearch`, `#faction:makers`
- LLM-Planner (`_make_planner_fn` in app.py) mit Groq (llama-3.3-70b) → multi-step `DelegationStep` mit `depends_on`
- **QC-Loop:** Nach Maker-Delegation → Auditor (Score 1-10). Unter `min_score` → Retry (max_retries). Skills sind von QC ausgenommen.
- Cross-Faction-Bridge für ADVERSARIAL

### 2.6 Conductor — `backend/agents/conductor.py`
- Agent-Registry + Dispatch
- Mission-Lifecycle + Watchdog (Timeout 900s default)
- `start_mission()` + `dispatch()`
- Persistent + In-Memory Store

### 2.7 AsyncAgent Base — `backend/agents/base.py`
Jeder Agent muss:
1. `advance_clock(incoming)` als ersten Schritt im `handle()`
2. Nur async I/O (kein `requests`, kein `time.sleep`)
3. `Message.response/error` zurückgeben

---

## 3. Agenten & Skills (agents.yaml + Boot)

### 3.1 Statische Agenten (agents.yaml)
- `agent:echo` (Test)
- `agent:alice` (LLM, Groq llama-3.3-70b, makers)
- `agent:martin` (mit QC auf alice)
- `agent:coder` (niedrige Temperatur)
- `agent:claude` (SSH-Wrapper zu lokalem `claude` CLI, Opus-4-7, hoher Qualitätsanspruch)
- `a2a:gateway`

### 3.2 Skills (19 Stück, alle über `SkillAgent` registriert)
Alle erben von `backend.skills.Skill` (`execute(query) → str`, zustandslos, CONFIG_FIELDS-System).

Wichtige Skills:
- `websearch`, `wikipedia`, `youtube`, `rss`
- `comfyui` + `ltxvideo` (lokaler ComfyUI, z-image-turbo Workflow, 1200×675)
- `whatsapp` (wacli + persistenter macOS LaunchAgent)
- `gmail`, `linkedin`, `telegram`
- `browser`, `chrome_browser` (Chrome-Extension)
- `coding`, `file`, `deploy`, `urlfetch`, `transcription` (Whisper)
- `dream` (interner Service)

Registrierung zentral in `backend/app.py:_boot_agents()` via `skill_map`.

### 3.3 LLM-Agenten — `backend/agents/llm_agent.py` + `claude_agent.py`
- Multi-Provider: Ollama, Groq, Anthropic, OpenAI, OpenRouter
- `ClaudeSSHAgent`: ruft lokales `claude` CLI via SSH-ähnlichem Wrapper auf (Timeout 120s)

---

## 4. API & Endpunkte (FastAPI)

Alle Router unter `/api` (außer A2A + Web-Bridge):

| Bereich       | Datei                    | Wichtige Routen                                      |
|---------------|--------------------------|------------------------------------------------------|
| Agents        | agents.py                | GET /agents, GET /{id}, POST /spawn                  |
| Chat          | chat.py                  | POST /chat, GET /chat/stream (SSE)                   |
| Missions      | missions.py              | CRUD + /trace + /spacetime + /verify (PQC)           |
| Factions      | factions.py              | GET /factions, POST /stance, POST /outcome           |
| Teams         | teams.py                 | CRUD + /members + /recommend                         |
| Logs          | logs.py                  | GET /logs (SSE, Live-Log-Broadcast)                  |
| Dreams        | dreams.py                | POST /dreams/trigger, GET /dreams                    |
| Files         | files.py                 | /api/fs (ls/read)                                    |
| RSS           | rss.py                   | Feeds + Entries + manuell fetch                      |
| Keys          | keys.py                  | Signer Public Keys (PQC Trust-Anchor)                |
| Deploys       | deploys.py               | Liste + Undeploy                                     |
| A2A           | a2a/gateway_router.py    | /.well-known/agent.json, /a2a/tasks/send             |
| Web-Bridge    | web_bridge.py            | /ext/dilles/v1/* (dillenberg.net, Token-Auth)        |
| Chrome        | chrome_ws.py             | WebSocket + /command                                 |

Zusätzlich: `/ping`, `/api/status`, Static-Mount von `frontend/`.

---

## 5. Storage & Persistenz

- **MissionStore** (`mission_store.py`): In-Memory, Append-only Traces, SSE-Queues pro Mission, Watchdog-fähig
- **PersistentMissionStore** (`sqlite_store.py`): SQLModel-Tabellen `missions`, `messages`, `tasks`. Lädt beim Boot in Memory-Cache. SSE bleibt transient.
- DB: `logpyclaw.db` (SQLite) + Backup `logpyclaw.db.bak.pre-pqc...`

---

## 6. Besondere Features

### 6.1 Post-Quantum Audit-Trail (`pqsign.py`)
- ML-DSA-65 (FIPS 204) pro Message
- Hash-Chain: `msg_hash = SHA-256(prev_hash || canonical_json(payload + clock))`
- Keys in `keys/signer-*.{pub,sk}`
- API: `/api/keys/signer`, `/api/missions/{id}/verify`

### 6.2 Täglicher Dream-Cycle (APScheduler, 03:00)
- Jeder LLM-Agent träumt → Prompt → ComfyUI → `dreams/YYYY-MM-DD/agent_*.{json,png}`
- Siehe `dreams/`-Ordner (Beispiele vorhanden)

### 6.3 Spacetime-Visualisierung (Frontend)
- Y = Eigenzeit τ, X = Agenten-Weltlinien
- Farbkodierung nach CDC-Relation (Violett = ordered, Rot = drift)
- Live über SSE

### 6.4 Chrome-Extension (`integrations/chrome-extension/`)
- Manifest V3, Content-Script + Background + Popup
- Ermöglicht `chrome_browser` Skill (Tab-Steuerung, Scripting)

### 6.5 WhatsApp-Integration
- `wacli` + macOS LaunchAgent (`com.agentclaw.wacli-sync.plist`)
- Skill: `whatsapp` (feste Gruppe „H.G.O.D.“)

---

## 7. Frontend (`frontend/index.html`)
- Dark Terminal-Design (aktualisierte Tokens vs. DESIGN.md)
- Layout: Header + Sidebar (Fraktion-Tabs + Agent-Cards) + Main (Tabs: Chat, Missions, Spacetime, Factions, Teams, Logs, Dreams, Files)
- Reine JS (kein Framework), i18n (EN/DE Toggle)
- Echtzeit: SSE für Chat-Stream, Logs, Mission-Progress, Spacetime-Updates
- Starke visuelle Semantik für CDC (Farben, Glow, dashed Drift-Linien)

Zusätzliche HTML-Dateien: `award.html`, `logpy-grid.html` (experimentell?).

---

## 8. Konfiguration & Betrieb

**Wichtige .env-Variablen** (siehe `.env.example`):
- LLM: `OLLAMA_*`, `GROQ_API_KEY`/`GROQ_API_KEYS`, `ANTHROPIC_API_KEY` etc.
- `WEB_BRIDGE_TOKEN`
- `MARTIN_QC_*` + `MARTIN_QC_AUDITOR_ID`
- `COMFYUI_URL` (Default 192.168.4.15:8000)

**Start:** `./start.sh` (aktiviert .venv, uvicorn --reload)

**Tests:** `python -m pytest tests/ -v` (13 Test-Dateien, gute Abdeckung von CDC, Protocol, Martin-Routing+QC, Factions, Teams, PQC, Skills, SQLite)

**Lint:** ruff (line-length 100)

**STYLEBOOK.md** ist verbindlich (kein sync I/O in async, keine print(), etc.).

---

## 9. Tests — Überblick

Starke Test-Suite:
- `test_cdc.py`, `test_protocol.py`, `test_pqsign.py` (Chain-Tampering-Tests!)
- `test_martin.py` (Routing, QC-Loop, Bridge, CDC-Clock)
- `test_api_factions.py`, `test_api_teams.py`
- `test_skills.py`, `test_sqlite_store.py`, `test_faction_protocol.py`, `test_team_protocol.py`, `test_agents.py`

---

## 10. Sonstige Artefakte

- `keys/` — PQC-Signer-Keypairs
- `dreams/` — Täglich generierte Agent-Träume (JSON + PNG)
- `bin/logpyclaw-verify` — vermutlich PQC-Verify-Helfer
- `logpyclaw.db.bak.pre-pqc.*` — Pre-PQC Backup
- `frontend/screenshots/` — 3 Aufnahmen der UI

---

## 11. Design- & Entwicklungsnotizen (aus DESIGN.md + STYLEBOOK)

- Design-System: Strenge Monospace-Ästhetik, 4-6-8-12-14-16-20px Spacing, CDC-spezifische Farbsemantik (Indigo Request, Emerald Response, Red = Drift)
- Phase-4-Notiz: Geplante Vue-Migration (noch nicht umgesetzt — Single-File bleibt)
- Verbotene Muster strikt eingehalten (siehe STYLEBOOK)
- Fraktions- und Team-Protokolle sind bewusst über Standard-A2A hinaus erweitert (γ-Matrizen, Drift-Kompensation, Trust-Learning)

---

## 12. Bewertung & Charakteristik (Grok-Analyse)

**Stärken:**
- Sehr kohärentes, theoretisch fundiertes Protokolldesign (CDC + Fraktionen + Teams + PQC)
- Klare Trennung Operator (Martin) vs. Domänen-Arbeit
- Hohe Code-Qualität + verbindliches STYLEBOOK
- Gute Testabdeckung auch für kryptographische und zeitliche Aspekte
- Praktische Integrationen (WhatsApp-Daemon, Chrome-Ext, ComfyUI, Claude-CLI-Wrapper)
- Einzigartige „Spacetime“- und „Dream“-Features mit hohem Wiedererkennungswert

**Besonderheiten / Potenzial:**
- Das System ist stark „philosophisch“ geprägt (Eigenzeit, Drift als Feature, Fraktions-Identität)
- Single-File-Frontend ist wartbar, aber bei weiterem Wachstum limitierend
- PQC ist bereits produktiv in der Audit-Chain (nicht nur Demo)
- Der LLM-Planner in `app.py` ist relativ groß und hardcodiert — könnte in eigenen Agent ausgelagert werden
- Keine sichtbaren AGENTS.md / Claude.md im Repo (globale ~/.claude wird respektiert)

**Risiken (rein beobachtet):**
- Keine .env.example in manchen Deploy-Szenarien dokumentiert
- ComfyUI-Endpunkt hard-default auf internes Netz
- WhatsApp-Daemon ist macOS-spezifisch (LaunchAgent)

---

## 13. Empfohlene nächste Schritte (observational, keine Handlungsaufforderung)

1. **Dokumentation:** `AGENTS.md` oder `CONTRIBUTING.md` im Repo für neue Entwickler.
2. **Frontend:** Prüfung, ob die geplante Vue-Migration noch relevant ist.
3. **PQC-Verify-Tool:** `bin/logpyclaw-verify` könnte als CLI für externe Auditoren ausgebaut werden.
4. **Multi-Node:** Aktuell Single-Node (Conductor + Store lokal). Spätere Verteilung über A2A wäre konsequent.
5. **Dream-Qualität:** Die generierten Träume sind ein starkes Markenzeichen — Qualitäts-Feedback-Loop wäre denkbar.

---

## Fazit

LogpyClaw v3 ist ein **außergewöhnlich durchdachtes, kohärentes Multi-Agent-System**, das nicht nur „Agenten orchestriert“, sondern ein eigenes **physikalisches Zeitmodell (CDC)**, soziale Strukturen (Fraktionen) und post-quanten-sichere Auditierbarkeit mitbringt. 

Der Code ist sauber, getestet und folgt strengen Konventionen. Das Projekt wirkt reif für experimentelle Produktionseinsätze in Nischen (kreative Workflows, Research-Agenten, interne Tooling mit hohen Audit-Ansprüchen).

Der Single-File-Frontend-Ansatz + die terminal-nahe Ästhetik passen perfekt zur „LogpyClaw“-Identität.

**Keine Dateien wurden verändert.** Dieser Bericht ist die einzige neue Artefakt-Datei (`bericht-grok.md`).

---

*Ende des Berichts — Grok 4.3, xAI, April 2026*