# AgentClaw v2 — Quick Reference Card

## Schnelle Übersicht für häufige Aufgaben

### 1. App Starten (Erste Minute)

```bash
# In venv gehen
source venv_v2/bin/activate

# App starten
python app_new.py

# Browser öffnet sich auf http://localhost:5050
```

### 2. Konfiguration Ändern

```bash
# .env öffnen
nano .env

# Wichtigste Einstellungen:
AGENTCLAW_PORT=5050              # Port ändern
AGENTCLAW_DEBUG=true             # Debug an/aus
AGENTCLAW_LOG_LEVEL=DEBUG        # Verbosity

# Mit Lokalem Ollama
AGENTCLAW_OLLAMA_URL=http://localhost:11434

# Mit Cloud API
OPENAI_API_KEY=sk-...
```

### 3. Abhängigkeiten Installieren

```bash
# Neue venv mit v2 Dependencies
python -m venv venv_v2
source venv_v2/bin/activate
pip install -r requirements_new.txt
```

### 4. Probleme Beheben

```bash
# Logs überprüfen
tail -f agentclaw.log

# Spezifische Fehler filtern
grep ERROR agentclaw.log

# Alle 50 letzten Zeilen
tail -50 agentclaw.log

# Health Check
curl http://localhost:5050/health

# Port bereits in Benutzung
lsof -i :5050
```

### 5. Neue API Endpoint hinzufügen

```python
# api/my_router.py
from fastapi import APIRouter

router = APIRouter(prefix="/my_endpoint", tags=["my_endpoint"])

@router.get("/")
async def my_endpoint():
    return {"result": "ok"}

# In app_new.py hinzufügen:
from api import my_router
app.include_router(my_router.router, prefix="/api")
```

### 6. Neue NiceGUI Page hinzufügen

```python
# ui/pages/my_page.py
from nicegui import ui

@ui.page("/my_page")
async def my_page():
    ui.label("Hello from my page")
    
    async def on_click():
        result = await some_async_operation()
        ui.notify(f"Result: {result}")
    
    ui.button("Click Me").on_click(on_click)

# Wird automatisch registriert wenn imported in app_new.py
```

### 7. Neue Skill hinzufügen

```python
# skills/my_skill.py
from skills.base import BaseSkill, SkillResult

class MySkill(BaseSkill):
    id = "my_skill"
    name = "My Skill"
    description = "..."
    requires = []

    async def execute(self, **kwargs) -> SkillResult:
        try:
            result = await do_something()
            return SkillResult(success=True, data=result)
        except Exception as e:
            return SkillResult(success=False, error=str(e))

# Registrieren in skills/registry.py
```

### 8. Test schreiben

```python
# tests/test_chat.py
import pytest
from services.chat import ChatService

@pytest.mark.asyncio
async def test_handle_message():
    chat = ChatService(agents, tasks)
    result = await chat.handle_message(agent_id="test", message="hello")
    assert result is not None
    assert len(result) > 0

# Laufen mit: pytest tests/
```

### 9. Logging in Code

```python
import logging

logger = logging.getLogger(__name__)

# Standard Levels
logger.debug("Detaillierte Debug-Infos")
logger.info("Wichtige Infos")
logger.warning("Warnung - etwas ist komisch")
logger.error("Fehler - etwas ging schief")
logger.critical("Kritischer Fehler - App könnte brechen")

# Mit Exception
try:
    something_dangerous()
except Exception as e:
    logger.error("Fehler:", exc_info=e)  # Full Stack Trace
```

### 10. Error Handling

```python
from core.errors import AgentClawError

try:
    result = await service.method()
except ServiceError as e:
    logger.error(f"Service Error: {e}")
    raise AgentClawError(
        message="Chat fehlgeschlagen",
        status_code=500,
        details={"error": str(e)}
    )

# Error Handler in app_new.py fängt es automatisch
```

---

## Häufige Commands

```bash
# Starten
python app_new.py

# Dependencies updaten
pip install -r requirements_new.txt --upgrade

# Logs live anschauen
tail -f agentclaw.log

# Nur Fehler anschauen
grep "ERROR\|CRITICAL" agentclaw.log

# Health Check
curl http://localhost:5050/health

# API Endpoints auflisten
curl http://localhost:5050/api/agents

# Settings überprüfen
python -c "from config.settings import settings; print(vars(settings))"

# Venv aktivieren (Linux/macOS)
source venv_v2/bin/activate

# Venv aktivieren (Windows)
venv_v2\Scripts\activate

# Venv deaktivieren
deactivate
```

---

## Datei-Locations

| Was | Wo |
|---|---|
| App Einstiegspunkt | `app_new.py` |
| Konfiguration | `.env` + `config/settings.py` |
| API Router | `api/*.py` |
| UI Pages | `ui/pages/*.py` |
| Services | `services/*.py` |
| Skills | `skills/*.py` |
| Logs | `agentclaw.log` |
| Datenbank | `data/` |

---

## Config Schnell-Referenz

```env
# Minimal
AGENTCLAW_PORT=5050
AGENTCLAW_SECRET_KEY=dev-secret

# Development
AGENTCLAW_DEBUG=true
AGENTCLAW_LOG_LEVEL=DEBUG

# Production
AGENTCLAW_DEBUG=false
AGENTCLAW_LOG_LEVEL=WARNING

# Mit Ollama
AGENTCLAW_OLLAMA_URL=http://localhost:11434

# Mit ComfyUI
AGENTCLAW_COMFYUI_URL=http://localhost:8188

# Mit OpenAI
OPENAI_API_KEY=sk-...
```

---

## Troubleshooting Schnell-Anleitung

| Problem | Lösung |
|---|---|
| "Port bereits in Benutzung" | `lsof -i :5050` oder `AGENTCLAW_PORT=5051` nutzen |
| "Module not found" | `pip install -r requirements_new.txt` |
| "Settings nicht geladen" | `.env` überprüfen, `AGENTCLAW_SECRET_KEY` setzen |
| "Ollama nicht erreichbar" | `AGENTCLAW_ENABLE_IMAGE_GENERATION=false` oder Ollama starten |
| "Browser zeigt 404" | Logs überprüfen, Cache leeren (Ctrl+Shift+Del) |
| "Zu viele Logs" | `AGENTCLAW_LOG_LEVEL=WARNING` setzen |

---

## Next: Mehr Informationen

- **Full Setup:** Siehe STARTUP_CHECKLIST.md
- **Architecture:** Siehe TECHNICAL_SUMMARY.md
- **Konfiguration:** Siehe CONFIG_SCHEMA.md
- **Migration:** Siehe MIGRATION_v2.md
