# AgentClaw v2 — Index

## Schnellstart

```bash
source venv_v2/bin/activate
python app_new.py
# → http://localhost:5050
```

## Dokumentation

| Datei | Inhalt |
|---|---|
| [CLAUDE.md](./CLAUDE.md) | Architektur-Überblick für AI-Assistenten |
| [QUICK_REFERENCE.md](./QUICK_REFERENCE.md) | Häufige Aufgaben & Commands |
| [README.md](./README.md) | Allgemeine Projekt-Übersicht |
| [CONFIG_SCHEMA.md](./CONFIG_SCHEMA.md) | Alle Konfigurationsoptionen |
| [A2A.md](./A2A.md) | Agent-to-Agent Protokoll (A2A) |
| [PROTOCOL.md](./PROTOCOL.md) | API-Protokoll-Dokumentation |
| [dream.md](./dream.md) | Dream-Funktion Konzept |

## Einstiegspunkte

| Datei | Zweck |
|---|---|
| [app_new.py](./app_new.py) | Haupteinstiegspunkt (NiceGUI + FastAPI) |
| [requirements_new.txt](./requirements_new.txt) | Python Dependencies |
| [.env.example](./.env.example) | Konfigurationsvorlage |

## TODO / Geplant

- [ ] **Qdrant Vector Store** — Skill-Capability-Discovery + semantisches A2A-Routing
  - Agenten-Fähigkeiten als Embeddings in Qdrant speichern
  - Tasks per Cosine-Similarity zum passendsten Agenten routen
  - Memory-Kontext über Qdrant (statt reinem JSON-File)
- [ ] **Streaming-Bilder** — Multimodale Inputs auch über SSE streamen
- [ ] **Agent-Memory-Kompression** — Auto-Zusammenfassung langer Historien (>100 Messages)
- [ ] **Web-UI für Watchdogs** — Watchdog-Verwaltung im NiceGUI-Frontend
- [ ] **py2app Build** — macOS .app Bundle mit neuem Stack

---

**Status:** ✓ v2 Migration abgeschlossen — FastAPI + NiceGUI + asyncio
