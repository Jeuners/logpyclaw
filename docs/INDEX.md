# AgentClaw v2 — Dokumentations-Index

## Schnellstart

```bash
git clone https://github.com/Jeuners/agentclaw.git
cd agentclaw
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
./agentclaw.sh start
# → http://localhost:5050
```

## Dokumentation

| Datei | Inhalt |
|---|---|
| [QUICK_REFERENCE.md](./QUICK_REFERENCE.md) | Installation, Commands, Skills, Chain-Syntax |
| [A2A.md](./A2A.md) | Agent-to-Agent Protokoll & Task-Chain |
| [CONFIG_SCHEMA.md](./CONFIG_SCHEMA.md) | Konfigurationsoptionen (providers.json) |
| [PROTOCOL.md](./PROTOCOL.md) | REST-API Protokoll-Dokumentation |
| [BUG-v189-nicegui-core-loop.md](./BUG-v189-nicegui-core-loop.md) | NiceGUI + Python 3.14 Bug & Workaround |

## Einstiegspunkte

| Datei | Zweck |
|---|---|
| `app.py` | Haupteinstiegspunkt (NiceGUI + FastAPI) |
| `agentclaw.sh` | Start/Stop/Restart/Logs Skript |
| `requirements.txt` | Python-Abhängigkeiten |
| `config/providers.json` | API-Keys & Service-URLs |
| `data/agentclaw.db` | SQLite-Datenbank (Agents, History, Tasks) |

## Architektur

```
app.py
├── api/          — 17 FastAPI-Router (chat, tasks, agents, a2a, ...)
├── services/     — Business-Logic (ChatService, TaskService, SkillRegistry)
├── skills/       — 14 Skills (screenshot, file_access, image_gen, ...)
├── core/         — LLM-Calls, A2A-Protokoll, Scheduler, Task-List-Parser
├── storage/      — SQLite + JSON (agents, history, tasks, providers)
└── ui/           — NiceGUI Pages (chat, home, tasks, settings, agent_edit)
```

## Skills (14 aktiv)

| Skill | Trigger | Beschreibung |
|---|---|---|
| `screenshot` | `screenshot https://...` | Playwright-Screenshot, optional `als datei.png` |
| `file_access` | `speichere als X.md`, `lies X.md` | Dateien lesen/schreiben, Wiki-Modus |
| `url_fetch` | URL in Nachricht | Webseite fetchen + Text extrahieren |
| `chrome_browser` | `chrome navigate/click/...` | Chrome via Extension steuern |
| `image_gen` | `erstelle bild von ...` | Bildgenerierung via ComfyUI |
| `image_edit` | Bild-Upload + Anweisung | Bildbearbeitung via ComfyUI |
| `video_gen` | `erstelle video ...` | Videogenerierung via ComfyUI |
| `transcription` | Audio-Upload | Whisper Transkription |
| `linkedin` | `linkedin post ...` | LinkedIn-Beiträge posten |
| `prompt_optimize` | `optimiere prompt ...` | Prompt-Verbesserung via LLM |
| `coding` | Code-Anfragen | Code-Generierung (CodeCraft) |
| `hacker_news` | `hacker news`, `hn` | Top Stories von HN |
| `tagesschau` | `nachrichten`, `tagesschau` | Deutsche Nachrichten |
| `whatsapp` | `whatsapp an ...` | WhatsApp via wacli |

## Task-Chain System

```
User → "1. @X ...\n2. @Y ..." → Chain erstellt
→ Tasks mit depends_on verkettet
→ Kontext aus vorherigen Schritten weitergereicht
→ Live-Status in Chain-Karte (SSE)
→ Nach Abschluss: Ergebnis in Chain-Karte + History
```

Separator in Task-Messages: `---\nDeine Aufgabe:` trennt Kontext von eigentlicher Aufgabe.
Skills und Trigger-Matching arbeiten nur gegen den Teil nach dem Separator.

## Offene Punkte / Geplant

- [ ] Wiki-Agent: automatisches Ingest + Indexing von Webseiten
- [ ] Task-Queue-System mit Busy-Feedback (Plan in `~/.claude/plans/`)
- [ ] A2A-Fixes: `_TASKS` Init, `_save_tasks()` Atomic Write (Plan in `~/.claude/plans/`)
- [ ] Screenshot-Bilder direkt in Chain-HTML einbetten (Workaround: `als datei.png`)
- [ ] py2app Build für macOS .app Bundle
