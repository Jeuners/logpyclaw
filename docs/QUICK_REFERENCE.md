# AgentClaw v2 — Quick Reference

## Start / Stop

```bash
./agentclaw.sh start     # im Hintergrund starten
./agentclaw.sh stop      # stoppen
./agentclaw.sh restart   # neu starten
./agentclaw.sh status    # Status & PID
./agentclaw.sh logs      # Live-Log

# Oder direkt:
source .venv/bin/activate
python app.py
# → http://localhost:5050
```

## Installation (neuer Mac)

```bash
git clone https://github.com/Jeuners/agentclaw.git
cd agentclaw
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # für Screenshots

# Dienste
brew install ollama
ollama pull gemma3:4b              # oder gewünschtes Modell
docker run -d -p 6333:6333 qdrant/qdrant   # Vektor-Memory

# Konfiguration vom alten Mac kopieren
cp /alter/mac/agentclaw/config/providers.json ./config/
cp /alter/mac/agentclaw/data/agentclaw.db   ./data/

./agentclaw.sh start
```

## Konfiguration

Alle API-Keys und URLs in `config/providers.json` (über UI: Einstellungen → Provider):

```json
{
  "openrouter": { "api_key": "sk-or-..." },
  "ollama":     { "url": "http://localhost:11434" },
  "comfyui":    { "url": "http://192.168.x.x:8188" }
}
```

## Task-Chain (mehrstufige Aufgaben)

Nummerierte Liste mit `@AgentName` → automatische sequenzielle Chain:

```
1. @MARTIN screenshot https://example.com als example.png
2. @MARTIN screenshot https://other.com als other.png
3. @MARTIN Erstelle bericht.html mit example.png und other.png [file_access]
```

- Jeder Schritt wartet auf den vorherigen (`depends_on`)
- Ergebnisse werden als Kontext weitergereicht (`---\nDeine Aufgabe:`)
- Live-Status in der Chain-Karte im Chat

## Screenshot-Skill

```
screenshot https://example.com             # Screenshot anzeigen
screenshot https://example.com als foo.png # Screenshot + als PNG speichern
```

PNG wird in `~/Downloads/AgentClaw/` (oder `wiki_dir` des Agents) gespeichert.

## File-Access-Skill

```
[file_access]   # am Ende einer LLM-Antwort → speichert Inhalt automatisch
                # Dateiname aus "Speichere als X.md" im Task-Text erkannt

lies index.md           # Datei lesen
liste dateien           # Dateien auflisten
liste wiki              # Wiki-Verzeichnis anzeigen
```

### Wiki-Modus
Agent bekommt `wiki_dir` → Subdirectories erlaubt (`pages/`, etc.):
```
wiki_dir: /Users/name/Documents/MeinWiki
```

## A2A — Agent-zu-Agent

```
@AgentName Aufgabe              # direkter Auftrag
@AgentName /chain               # explizite Chain

TASKLIST-Format (LLM-generiert):
[tasklist]
AndererAgent: Erstelle Bild von X
NochEinAgent: Schreibe Text über Y
[/tasklist]
```

## Neuen Skill erstellen

```python
# skills/my_skill.py
from skills.base import BaseSkill, SkillResult

class MySkill(BaseSkill):
    id = "my_skill"
    name = "My Skill"
    icon = "star"
    description = "Kurzbeschreibung für LLM"
    triggers = [r"\bmein_trigger\b"]
    requires = []              # z.B. ["openai"] für API-abhängige Skills

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        return SkillResult(text="Ergebnis", skill_used=self.id)
```

In `services/__init__.py` registrieren.

## Neue API-Route

```python
# api/my_router.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/my_endpoint")
async def my_endpoint():
    return {"ok": True}

# In app.py:
app.include_router(my_router.router, prefix="/api")
```

## Debugging

```bash
# Live-Logs
./agentclaw.sh logs
tail -f /tmp/agentclaw.log

# Health Check
curl http://localhost:5050/ping

# Agents auflisten
curl -s http://localhost:5050/api/agents | python -m json.tool

# Port belegt?
lsof -i :5050
```

## Datei-Übersicht

| Was | Wo |
|---|---|
| Einstiegspunkt | `app.py` |
| FastAPI-Router | `api/*.py` |
| NiceGUI-Pages | `ui/pages/*.py` |
| Business-Logic | `services/*.py` |
| Skills | `skills/*.py` |
| Datenbank | `data/agentclaw.db` |
| Konfiguration | `config/providers.json` |
| Start-Skript | `agentclaw.sh` |
| Logs | `data/agentclaw.log` |

## Bekannte Einschränkungen

| Problem | Workaround |
|---|---|
| NiceGUI core.loop Bug (Python 3.14) | Chat komplett via JS/fetch gelöst, kein `ui.run_javascript()` aus Event-Handlern |
| Screenshots in Chain-HTML | `als datei.png` speichern, dann `<img src="datei.png">` referenzieren |
| Chain-Tasks kein `_try_skill_from_reply` nativ | `execute()` ruft `_try_skill_from_reply` jetzt auf — LLM kann `[file_access]` nutzen |
