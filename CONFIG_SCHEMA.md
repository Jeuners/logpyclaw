# AgentClaw v2 — Configuration Schema

## Übersicht
Alle Konfigurationen werden über Umgebungsvariablen (.env) oder `config/settings.py` (Pydantic) definiert.
Die Quelle der Wahrheit ist `.env.example`.

## Konfigurationshierarchie (Precedence)

1. **Explizite Argument-Übergabe** (z.B. CLI-Flags) — höchste Priorität
2. **Env-Variablen** (aus .env oder Shell)
3. **Defaults in settings.py** — niedrigste Priorität

## Alle Einstellungen

### App-Konfiguration

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `AGENTCLAW_PORT` | int | 5050 | Port auf dem die App läuft |
| `AGENTCLAW_HOST` | str | 0.0.0.0 | Host binding (0.0.0.0 = öffentlich) |
| `AGENTCLAW_DEBUG` | bool | false | Debug-Modus (Reload, Verbose Logging) |
| `AGENTCLAW_NATIVE_MODE` | bool | true | Nutze pywebview für Desktop-App (False = Browser) |
| `AGENTCLAW_SECRET_KEY` | str | — | Geheimschlüssel für Session-Encryption (mindestens 32 Zeichen) |

### Logging

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `AGENTCLAW_LOG_LEVEL` | str | INFO | Log-Level: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `AGENTCLAW_LOG_FILE` | str | agentclaw.log | Logfile-Pfad |
| `AGENTCLAW_LOG_FORMAT` | str | json | Format: json, text |
| `AGENTCLAW_LOG_CONSOLE` | bool | true | Logs auch zur Console ausgeben |

### Lokale Services (Connectivity)

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `AGENTCLAW_OLLAMA_URL` | str | http://localhost:11434 | Ollama LLM Server |
| `AGENTCLAW_OLLAMA_MODEL` | str | llama2 | Standard Ollama Modell |
| `AGENTCLAW_COMFYUI_URL` | str | http://localhost:8188 | ComfyUI Server (Image/Video Gen) |
| `AGENTCLAW_COMFYUI_TIMEOUT_SECONDS` | int | 300 | Timeout für ComfyUI Workflows |
| `AGENTCLAW_QDRANT_URL` | str | http://localhost:6333 | Qdrant Vector DB |
| `AGENTCLAW_QDRANT_API_KEY` | str | (leer) | Qdrant API Key (optional) |
| `AGENTCLAW_REDIS_HOST` | str | localhost | Redis Server Host |
| `AGENTCLAW_REDIS_PORT` | int | 6379 | Redis Server Port |
| `AGENTCLAW_REDIS_DB` | int | 0 | Redis DB Index |
| `AGENTCLAW_REDIS_PASSWORD` | str | (leer) | Redis Password (optional) |

### Performance & Limits

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `AGENTCLAW_TASK_TIMEOUT_SECONDS` | int | 1200 | Max. Dauer für Task-Ausführung (20 Min) |
| `AGENTCLAW_MAX_HISTORY_PER_AGENT` | int | 50 | Max. Chat-Messages pro Agent (dann Cleanup) |
| `AGENTCLAW_MAX_CONCURRENT_TASKS` | int | 5 | Max. parallel laufende Tasks |
| `AGENTCLAW_SKILL_EXECUTION_TIMEOUT_SECONDS` | int | 300 | Max. Dauer für Skill-Ausführung |
| `AGENTCLAW_MAX_RETRIES` | int | 3 | Wiederholungen bei Fehler |

### Cloud Provider Credentials

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `OPENAI_API_KEY` | str | — | OpenAI API Key (sk-...) |
| `MISTRAL_API_KEY` | str | — | Mistral API Key (für Voxtral TTS) |
| `OPENROUTER_API_KEY` | str | — | OpenRouter API Key (für Cloud-Modelle) |
| `YOUTUBE_API_KEY` | str | — | YouTube Data API Key |
| `TELEGRAM_BOT_TOKEN` | str | — | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | str | — | Telegram Chat ID für Notifications |
| `GMAIL_USER_EMAIL` | str | — | Gmail E-Mail Adresse |
| `GMAIL_APP_PASSWORD` | str | — | Gmail App-Passwort (nicht Main-Passwort!) |
| `LINKEDIN_EMAIL` | str | — | LinkedIn E-Mail |
| `LINKEDIN_PASSWORD` | str | — | LinkedIn Passwort |

### Feature Flags (Skills aktivieren/deaktivieren)

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `AGENTCLAW_ENABLE_MEMORY_SKILL` | bool | true | Memory/Vector-DB Skill |
| `AGENTCLAW_ENABLE_WEBSEARCH_SKILL` | bool | true | Web-Suche Skill |
| `AGENTCLAW_ENABLE_FILE_SKILL` | bool | true | File-Access Skill |
| `AGENTCLAW_ENABLE_IMAGE_GENERATION` | bool | true | Image Generation (ComfyUI) |
| `AGENTCLAW_ENABLE_VIDEO_GENERATION` | bool | true | Video Generation (ComfyUI) |
| `AGENTCLAW_ENABLE_YOUTUBE_SKILL` | bool | true | YouTube Video-Download |
| `AGENTCLAW_ENABLE_TELEGRAM_SKILL` | bool | true | Telegram Integration |
| `AGENTCLAW_ENABLE_GMAIL_SKILL` | bool | true | Gmail Integration |
| `AGENTCLAW_ENABLE_LINKEDIN_SKILL` | bool | true | LinkedIn Integration |

### Persistenz

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `AGENTCLAW_SAVE_CHAT_HISTORY` | bool | true | Chat-Historien speichern |
| `AGENTCLAW_SAVE_TASKS` | bool | true | Task-Definitionen speichern |
| `AGENTCLAW_HISTORY_DIR` | str | ./data/history | Verzeichnis für Chat-Historien |
| `AGENTCLAW_TASKS_DIR` | str | ./data/tasks | Verzeichnis für Task-JSONs |
| `AGENTCLAW_CONFIG_DIR` | str | ./data/config | Verzeichnis für Agent-Configs |

### Security (CORS)

| Variable | Typ | Default | Beschreibung |
|----------|-----|---------|---|
| `AGENTCLAW_CORS_ORIGINS` | list[str] | [] | Allowed CORS Origins (z.B. http://localhost:3000) |
| `AGENTCLAW_CORS_ALLOW_CREDENTIALS` | bool | true | Cookies in CORS erlauben |
| `AGENTCLAW_CORS_ALLOW_METHODS` | list[str] | * | Erlaubte HTTP-Methoden |

## Beispiel .env Konfigurationen

### Lokal (Development)
```env
AGENTCLAW_PORT=5050
AGENTCLAW_DEBUG=true
AGENTCLAW_LOG_LEVEL=DEBUG
AGENTCLAW_NATIVE_MODE=false
AGENTCLAW_SECRET_KEY=dev-secret-key-not-secure
```

### Production Server
```env
AGENTCLAW_PORT=8000
AGENTCLAW_HOST=0.0.0.0
AGENTCLAW_DEBUG=false
AGENTCLAW_LOG_LEVEL=INFO
AGENTCLAW_NATIVE_MODE=false
AGENTCLAW_SECRET_KEY=<random-32-char-string>
AGENTCLAW_CORS_ORIGINS=["https://myapp.com"]
```

### Minimal (nur Cloud, keine lokalen Services)
```env
AGENTCLAW_PORT=5050
AGENTCLAW_ENABLE_IMAGE_GENERATION=false
AGENTCLAW_ENABLE_VIDEO_GENERATION=false
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=...
```

## Umgebungs-Spezifische Konfiguration

### macOS (Native Desktop App)
```env
AGENTCLAW_NATIVE_MODE=true
AGENTCLAW_HOST=127.0.0.1
AGENTCLAW_OLLAMA_URL=http://localhost:11434  # Ollama läuft lokal
```

### Docker Container
```env
AGENTCLAW_HOST=0.0.0.0
AGENTCLAW_NATIVE_MODE=false
AGENTCLAW_OLLAMA_URL=http://ollama:11434     # Service-Name statt localhost
AGENTCLAW_REDIS_HOST=redis                   # Service-Name
AGENTCLAW_QDRANT_URL=http://qdrant:6333      # Service-Name
```

### Kubernetes/Cloud
```env
AGENTCLAW_PORT=${PORT}                        # Inject vom System
AGENTCLAW_HOST=0.0.0.0
AGENTCLAW_DEBUG=false
AGENTCLAW_LOG_LEVEL=WARNING
AGENTCLAW_OLLAMA_URL=http://ollama-service:11434
# ... etc
```

## Validation & Fehlerbehandlung

### Pydantic Settings (config/settings.py)
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PORT: int = 5050
    HOST: str = "0.0.0.0"
    SECRET_KEY: str  # Erforderlich, kein Default

    class Config:
        env_prefix = "AGENTCLAW_"
        env_file = ".env"
```

### Fehler bei Start
```
ValueError: AGENTCLAW_SECRET_KEY not set in .env
# Lösung: AGENTCLAW_SECRET_KEY in .env hinzufügen
```

### Fehler bei Service-Verbindung
```
ConnectionError: http://localhost:11434 not reachable
# Lösung: AGENTCLAW_ENABLE_IMAGE_GENERATION=false oder Ollama starten
```

## Best Practices

1. **Secrets niemals in Code**
   ```env
   # Gut
   OPENAI_API_KEY=${OPENAI_API_KEY}

   # Schlecht
   OPENAI_API_KEY=sk-123456...  # in Git commitet!
   ```

2. **.env in .gitignore**
   ```bash
   echo ".env" >> .gitignore
   # .env.example bleibt im Git für Dokumentation
   ```

3. **Environment-spezifische Configs**
   ```bash
   .env           # Local only (gitignore)
   .env.example   # Vorlage (in Git)
   .env.prod      # Production secrets (external)
   ```

4. **Startup überprüfen**
   ```bash
   # Alle Env-Vars vor App-Start loggen
   python -c "from config.settings import settings; print(settings)"
   ```

## Lese-Reihenfolge beim Start

1. `.env` Datei laden (mit `python-dotenv`)
2. Env-Variablen parsen mit Pydantic
3. Defaults einsetzen für nicht-spezifizierte Werte
4. Validation durchführen
5. Logger mit geladenen Settings konfigurieren
6. App starten

```python
# In app_new.py
from dotenv import load_dotenv
load_dotenv()  # <- .env wird gelesen

from config.settings import settings  # <- Pydantic parst
setup_logging()  # <- Nutzt settings.LOG_LEVEL
```
