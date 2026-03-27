# AgentClaw

Lokaler Voice-Chat mit mehreren KI-Agenten, verschiedenen Stimmen und Provider-Support.

## Features

- Mehrere Agenten mit eigener Persönlichkeit (Soul), Stimme, Farbe und Modell
- **Ollama** — lokale LLMs (gemma3, mistral-nemo, llama3.1, ...)
- **OpenRouter** — Zugang zu hunderten Cloud-Modellen (inkl. Free-Tier)
- **Mistral Voxtral TTS** — hochwertige Sprachausgabe
- **macOS System Voices** — lokale TTS ohne API
- Spracherkennung via Web Speech API
- Provider-Admin-Panel mit Status-Indikatoren
- Chat-Verlauf pro Agent

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Öffne http://localhost:5050

### Voraussetzungen

- [Ollama](https://ollama.ai) installiert und laufend (`ollama serve`)
- Modelle laden: `ollama pull gemma3`
- API Keys im ⚙️ Provider-Panel eintragen

---

## 🚧 Web-Suche — UNFERTIG

> **Status: Experimentell, funktioniert nicht zuverlässig**

### Was gebaut wurde

- **SearXNG** (selbst gehostet via Docker) als lokale Suchmaschine
- Automatisches Fetchen von URLs die der User in Nachrichten schickt
- Keyword-basierter Trigger (suche, news, aktuell, wer ist, ...)

### Probleme

- **Keyword-Erkennung zu simpel** — sucht manchmal wenn nicht nötig, manchmal gar nicht
- **Modelle können Suchergebnisse nicht gut verarbeiten** — werden einfach in den System-Prompt injiziert, kein echtes Tool-Calling
- **Halluzinationen** — Modell ignoriert manchmal die Ergebnisse und erfindet trotzdem Antworten
- **URL-Fetching unzuverlässig** — JS-heavy Seiten liefern leere Inhalte (kein Browser/JS-Rendering)
- **Kein Streaming** — bei Web-Suche + langem Context spürbar langsamer

### Was es bräuchte

- Echtes **Tool-Calling** (Function Calling) damit das Modell selbst entscheidet wann es sucht
- **Playwright/Puppeteer** für JS-Seiten
- Bessere Context-Aufbereitung der Suchergebnisse
- Modelle mit nativer Search-Unterstützung (z.B. Perplexity Sonar)

### SearXNG starten

```bash
mkdir -p /tmp/searxng-config
cat > /tmp/searxng-config/settings.yml << EOF
use_default_settings: true
server:
  secret_key: "dein-secret-key"
  limiter: false
search:
  formats:
    - html
    - json
EOF

docker run -d --name searxng -p 8888:8080 \
  -v /tmp/searxng-config:/etc/searxng \
  searxng/searxng
```

---

## Provider

| Provider | Zweck | Lokal? |
|---|---|---|
| Ollama | LLM Chat | ✅ |
| macOS Voices | TTS | ✅ |
| SearXNG | Web-Suche | ✅ (Docker) |
| Mistral | TTS (Voxtral) | ❌ |
| OpenRouter | LLM Chat (Cloud-Modelle) | ❌ |
