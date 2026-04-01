<div align="center">

# 🦀 AgentClaw

**Build, test and talk to your AI agents — locally**
*Configure, experiment and deploy AI agents with personality, voice and skills — 100% local, GDPR-ready by design*

[![Python](https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![macOS](https://img.shields.io/badge/macOS-12%2B-000000?style=flat-square&logo=apple&logoColor=white)](https://www.apple.com/macos)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What is AgentClaw?

AgentClaw is a **self-hosted, privacy-first multi-agent AI platform** that runs entirely on your Mac. Create multiple AI agents with distinct personalities, voices, and skill sets. They can chat, search the web, take screenshots, generate images, remember past conversations, and even delegate tasks to each other.

No cloud subscriptions required — use local models via [Ollama](https://ollama.com) or connect to OpenRouter / Mistral for more power.

---

## Features

### Multi-Agent System
- Create unlimited AI agents with unique personalities (system prompts), colors, and voices
- Each agent has its own conversation history and long-term memory
- Agents can delegate tasks to each other via `@mentions`

### Voice I/O
- **Text-to-Speech**: Mistral Voxtral API or native macOS system voices
- **Voice Input**: Web Speech API for hands-free interaction
- Per-message play buttons + auto-play toggle
- Sentence-chunked streaming for natural speech rhythm

### Skills

| Skill | Description |
|-------|-------------|
| 🔗 **URL Reader** | Auto-fetches and summarizes any URL in a message |
| 📸 **Screenshot** | Takes browser screenshots via Playwright |
| 🎨 **Image Generation** | Generates images via ComfyUI (Flux, etc.) |
| ✏️ **Image Editing** | Edits uploaded images via ComfyUI / FireRed |
| 📰 **Tagesschau News** | Fetches latest German news from Tagesschau API |
| 🎩 **Hacker News** | Fetches top stories from Hacker News |
| 🧠 **Long-Term Memory** | Stores and recalls context using Qdrant vector DB |
| 📄 **Document Memory** | Upload PDFs/images - stored as vectors (requires Google API) |
| ✨ **Prompt Optimizer** | Optimizes prompts using RTF, TAG, BAB, CARE, RISE frameworks |
| ✈️ **Telegram** | Sends/receives messages and images via Telegram bot |
| 🌙 **Dream Agent** | Daily memory optimization - removes old entries, resolves contradictions |

### Autonomous Agents (Heartbeat)
- Configure a **heartbeat** schedule for any agent (e.g. every 15 minutes)
- Agents execute their task independently without user interaction
- Ideal for news summaries, monitoring, periodic reports

### Broadcast Mode
- Send one message to multiple agents simultaneously
- Collect all their responses in a unified view

### Native macOS App
- Runs as a standalone `.app` bundle — no browser or terminal needed
- Built with [pywebview](https://pywebview.flowrl.com) + [py2app](https://py2app.readthedocs.io)
- Lightweight native WebKit window — no Electron overhead

---

## UI Layout

```
┌──────────────────────────────────────────────────────────┐
│  Activity Bar │  Side Panel    │  Workspace              │
│  (52px)       │  (228px)       │  (flex)                 │
│               │                │                         │
│  🏠 Home      │  Agents List   │  Chat / Dashboard       │
│  💬 Chat      │  + New Agent   │  Multi-Broadcast        │
│  📡 Broadcast │  Search        │  Agent Settings         │
│  🔭 Watchdog  │  Filters       │                         │
│  ⚙️  Settings  │                │                         │
└──────────────────────────────────────────────────────────┘
```

Dark matrix theme — `#050a06` background, `#00e676` green accents.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) (for local models)
- macOS 12+ (for native app features)

### 1. Clone & Install

```bash
git clone https://github.com/Jeuners/agentclaw.git
cd agentclaw

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Pull a Model

```bash
ollama pull gemma3          # recommended default
ollama pull mistral-nemo    # for more capable tasks
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env and add your API keys
```

```env
MISTRAL_API_KEY=your_key_here      # optional — for Voxtral TTS
```

### 4. Run

```bash
python app.py
```

Open **http://localhost:5050** in your browser. Done.

---

## macOS App Build

Build a standalone `.app` that runs without a terminal:

```bash
# Install build tools (if not already in venv)
pip install py2app pywebview

# Generate app icon
python make_icon.py

# Build .app bundle
python setup.py py2app
```

Output: `dist/AgentClaw.app` — drag to `/Applications` and double-click to launch.

> The `.app` bundles Python + all dependencies (~175 MB). No separate Python installation required on the target machine.

**Distribute to another Mac:**
```bash
# Zip first — much faster than copying the folder directly
zip -r AgentClaw.zip dist/AgentClaw.app
# Copy via AirDrop, SMB share, or scp
```

---

## Configuration

### providers.json

Configure your AI providers and external services (also editable via the UI under ⚙️ Settings):

```json
{
  "ollama":     { "url": "http://localhost:11434" },
  "openrouter": { "api_key": "sk-or-v1-..." },
  "mistral":    { "api_key": "sk-..." },
  "searxng":    { "url": "http://localhost:8888" },
  "qdrant":     { "url": "http://localhost:6333" },
  "comfyui":    { "url": "http://localhost:8000" },
  "telegram":   { "bot_token": "...", "chat_id": "..." }
}
```

### Optional Services via Docker

**Web Search (SearXNG)**
```bash
docker run -d --name searxng -p 8888:8080 searxng/searxng
```

**Vector Memory (Qdrant)**
```bash
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant
```

**Screenshots (Playwright)**
```bash
playwright install chromium
```

---

## Architecture

```
agentclaw/
├── app.py              # Flask backend (~3000 lines) — all API routes & logic
├── main_app.py         # macOS app entry point (pywebview window)
├── setup.py            # py2app build configuration
├── make_icon.py        # App icon generator
├── templates/
│   └── index.html      # Full frontend (~3000 lines, vanilla JS/HTML/CSS)
├── static/             # CSS & JS assets
├── agents.json         # Agent definitions (auto-created, gitignored)
├── history.json        # Chat history (auto-created, gitignored)
├── providers.json      # Service config (auto-created, gitignored)
├── watchdogs.json      # URL monitors (auto-created)
└── tasks.json          # Agent task queue (auto-created)
```

### API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/agents` | List all agents |
| `POST` | `/api/agents` | Create agent |
| `PUT` | `/api/agents/<id>` | Update agent |
| `POST` | `/api/chat` | Send message (supports image uploads) |
| `GET` | `/api/history/<id>` | Get conversation history |
| `POST` | `/api/tts` | Generate speech → returns raw MP3 bytes |
| `POST` | `/api/screenshot` | Screenshot a URL via Playwright |
| `GET` | `/api/providers` | Get provider config (returns dict, not array) |
| `GET` | `/api/models` | List available models |
| `GET` | `/api/skills` | List skills + availability status |
| `GET` | `/api/watchdogs` | URL monitoring list |
| `GET` | `/api/activity` | Live agent activity feed |

> **TTS note:** `/api/tts` returns raw `audio/mpeg` bytes via `send_file()`. Always consume with `fetch().blob()` — never `.json()`.

### LLM Provider Support

| Provider | Type | Notes |
|----------|------|-------|
| **Ollama** | Local | Fully private, no API key needed |
| **OpenRouter** | Cloud | 100+ models, free tier available |
| **Mistral** | Cloud | Required for Voxtral TTS voices |

---

## Creating Agents

Agents are configured via the UI or directly in `agents.json`:

```json
{
  "id": "uuid-here",
  "name": "Aria",
  "soul": "You are Aria, a creative assistant who loves design and visual arts.",
  "model": "gemma3:latest",
  "provider": "ollama",
  "voice": "en_paul_neutral",
  "color": "#00e676",
  "skills": ["web_search", "screenshot", "memory"],
  "heartbeat": {
    "active": false,
    "interval_min": 60,
    "prompt": "Summarize the latest AI news and save key findings."
  }
}
```

### Agent-to-Agent Delegation

Agents can delegate tasks to each other via `@mentions`:

```
User:   "MARTIN, ask @Picasso to generate a sunset image"
MARTIN: creates task → Picasso generates image → result returned to chat
```

---

## Watchdog (URL Monitoring)

Monitor any URL and get AI-analyzed alerts:

- Set a URL, check interval, alert keyword, and which agent should analyze it
- Agent reads the page on each check and flags changes or keywords
- Results appear in the Watchdog panel

---

## Privacy & GDPR

AgentClaw is **GDPR-ready by design** — not as an afterthought.

- All conversations stay **local by default** (Ollama) — no data leaves your machine
- No cloud processing, no third-party logging, no training on your data
- API keys stored in `.env` — never committed to git
- `agents.json`, `history.json`, `providers.json` are in `.gitignore`
- No telemetry, no tracking, no external calls unless you explicitly configure them
- Cloud providers (OpenRouter, Mistral) are **opt-in only**

> Built for use cases where data privacy isn't optional — agencies, freelancers, and businesses operating under GDPR.

---

## Roadmap

- [ ] VectorMind integration (Gemini Embedding 2 + Qdrant multimodal)
- [ ] Auto-update mechanism for macOS app
- [ ] Agent import/export
- [ ] Voice wake word detection
- [ ] Plugin system for custom skills
- [ ] Windows / Linux support

---

## Contributing

Pull requests welcome. For major changes please open an issue first.

1. Fork the repo
2. Create your branch: `git checkout -b feature/my-feature`
3. Commit your changes
4. Push and open a PR

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">
Built with AI + coffee on a Mac Mini M4
</div>
