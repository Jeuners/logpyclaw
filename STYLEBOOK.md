# LogpyClaw v3 — Stylebook

Verbindliche Konventionen für alle Beiträge zum Projekt.  
Stand: 2026-05 · Python 3.12 · FastAPI · asyncio

---

## Skill-Interface

Jeder Skill erbt von `backend.skills.Skill`.

```python
from backend.skills import Skill

class MySkill(Skill):
    skill_id = "myskill"          # lowercase, keine Leerzeichen, keine Bindestriche
    description = "Kurzbeschreibung (1 Satz)"

    async def execute(self, query: str) -> str:
        ...
```

Regeln:
- `skill_id` = lowercase, nur `[a-z0-9_]`, keine Leerzeichen
- `execute(query: str) -> str` — immer String zurück, **nie Exception werfen**
- Fehler als `"[SkillName] Fehler: <details>"` zurückgeben
- Alle externen HTTP-Calls mit `timeout=` (max 30 s) absichern
- Skills sind zustandslos; kein Instanz-State der zwischen Calls überlebt

Registrierung in `app.py`:

```python
conductor.register(SkillAgent(MySkill()))
```

---

## Agent-Pattern

Agenten erben von `backend.agents.base.AsyncAgent`.

```python
from backend.agents.base import AsyncAgent
from backend.core.protocol import Message

class MyAgent(AsyncAgent):
    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        content = msg.payload.get("content", "")
        # ... verarbeiten ...
        return Message.response(msg, result, clock=clock)
```

Regeln:
- `advance_clock(msg.clock)` immer als ersten Call — sonst falsche CDC-Ordnung
- Fehler: `Message.error(msg, "Beschreibung", clock=clock)` zurückgeben
- Keine sync I/O (`requests`, `open()`, `time.sleep()`) in `async def handle()`
- Sub-Delegation über `self.conductor.dispatch(sub_msg)`, nicht direkt `agent.handle()`
- Timeouts via `asyncio.wait_for(..., timeout=x)` bei allen externen Calls

---

## CDC-Regeln

Die Causal-Dilation Clock ist **Pflichtfeld** auf jeder internen Message.

```
clock = self.advance_clock(msg.clock)   # immer zuerst
sub   = Message.request(..., clock=self.advance_clock())
```

4 CDC-Relationen:
| Relation | Bedeutung |
|---|---|
| `ORDERED` | Kausal + temporal geordnet — Normal |
| `CAUSAL_DRIFT` | Kausal geordnet, temporal divergent — Warnung |
| `CONCURRENT_DRIFT` | Nebenläufig mit Divergenz — Watch |
| `INCONSISTENT` | V und D widersprechen sich — Bug |

- `CAUSAL_DRIFT` und `CONCURRENT_DRIFT` müssen geloggt werden (WARNING)
- `INCONSISTENT` ist immer ein Bug — ERROR + Untersuchung

---

## Fraktions-System

Sechs Fraktionen mit eigenen `mission_lens`-Werten (Charter):

| Fraktion | Rolle |
|---|---|
| `operators` | Meta-Sicht, Routing, QC-Loops (Martin) |
| `makers` | Implementierung, Code-Erstellung |
| `auditors` | Qualitätsprüfung, Score-Vergabe |
| `gatherers` | Recherche, Websuche, Datenabruf |
| `guardians` | Sicherheit, Compliance, Grenzen |
| `scribes` | Dokumentation, Zusammenfassung |

Cross-faction-Traffic via Martin-Bridge (`_faction.requires_bridge = True`).

---

## Logging-Konventionen

```python
from backend.core.logging import get_logger
log = get_logger(__name__)

log.debug("detail: %s", var)        # nur für Entwicklung relevant
log.info("key event: %s", id)       # wichtige Zustandsübergänge
log.warning("drift: %s", relation)  # unerwartetes, aber kein Fehler
log.error("dispatch failed: %s", e) # Fehler, kein Crash
```

- `get_logger(__name__)` — immer den Modulnamen verwenden
- Kein `print()` im Backend-Code (ausser `__main__`-Blöcke)
- Keine sensiblen Daten (Keys, Tokens) in Log-Nachrichten
- Log-Level-Guidance: DEBUG = Trace-Level; INFO = Milestones; WARNING = Drift/Retry; ERROR = Failures

---

## API-Routen

Alle Routen in eigenen Dateien unter `backend/api/`.

```python
from fastapi import APIRouter, Request
router = APIRouter()

@router.get("/resource")
async def get_resource(request: Request):
    conductor = request.app.state.conductor
    ...
```

- Prefix immer in `app.py` gesetzt (`prefix="/api"`)
- SSE-Endpoints: `media_type="text/event-stream"` + Header `Cache-Control: no-cache`
- Heartbeats bei SSE-Streams alle 5 s als `": heartbeat\n\n"`

---

## Test-Anforderungen

- Tests liegen in `tests/` mit dem Prefix `test_`
- Framework: `pytest` + `pytest-asyncio`
- Jedes neue Modul bekommt eine Testdatei: `tests/test_<modul>.py`
- Mock-Pattern: `unittest.mock.AsyncMock` für async-Abhängigkeiten
- Kein echter Netzwerk-Traffic in Unit-Tests (httpx mock oder vcr)

```bash
python -m pytest tests/ -v
```

---

## Coding-Konventionen

- **Python 3.12** — `from __future__ import annotations` in jedem Modul
- **ruff** als Linter/Formatter: `line-length = 100`
- Keine sync I/O in async Funktionen
- Timeouts überall wo externe Calls passieren (`timeout=` bei httpx, `asyncio.wait_for`)
- Dataclasses mit `@dataclass` für reine Datenstrukturen (nicht Pydantic)
- Pydantic-Models nur in API-Layer (Request/Response-Schemas)
- Imports: stdlib → third-party → local (ruff isort-Style)
- Docstrings: kurzer Ein-Zeiler, dann optionaler Detailblock (kein reST/sphinx)
- Keine `global`-Variablen ausser Singleton-Pattern mit `_instance`
- Singleton-Pattern: `@classmethod get(cls)` wie in `LogBroadcaster`

### Verbotene Muster

```python
# Nicht:
import requests                    # → httpx (async)
time.sleep(x)                     # → asyncio.sleep(x)
threading.Thread(...)             # → asyncio.create_task(...)
print(...)                        # → get_logger(__name__)
except Exception: pass            # → mindestens log.warning(...)
```

---

## Dateistruktur

```
backend/
  core/          # CDC, Protocol, Logging — keine externen Dependencies
  agents/        # AsyncAgent-Implementierungen
  api/           # FastAPI-Router (je Ressource eine Datei)
  skills/        # Skill-Implementierungen
  storage/       # MissionStore, SQLite-Layer
  i18n/          # en.py / de.py + t()-Funktion
frontend/
  index.html     # Single-File-Frontend (kein Build-Step)
tests/
  test_*.py
```
