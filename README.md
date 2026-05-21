# LogpyClaw v3

CDC-native multi-agent system with time-dilation-aware coordination.

## What makes it different

LogpyClaw v3 uses a **Causal-Dilation Clock (CDC)** on every message — not as optional metadata, but as a first-class protocol field. Each agent carries a `(Vector, Dilation)` tuple that tracks both causal ordering and subjective eigenzeit (operation rate). This enables:

- **Team scheduling** that compensates for agent drift (γ_ij matrix)
- **Spacetime visualization** of agent interactions over eigenzeit
- **A2A compatibility** via a dedicated Gateway Agent that translates externally

## Quick Start

```bash
# Requires Python 3.12+, port 5050
source .venv/bin/activate
uvicorn backend.app:app --host 0.0.0.0 --port 5050

curl http://localhost:5050/ping
# → {"pong": true, "version": "3.0.0"}
```

Open http://localhost:5050

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `gemma4:e4b` | Default Ollama model |
| `ANTHROPIC_API_KEY` | — | For Anthropic agents |
| `OPENAI_API_KEY` | — | For OpenAI agents |
| `WEB_BRIDGE_TOKEN` | — | Token for /ext/dilles/v1/* |

## Architecture

```
backend/
├── core/
│   ├── cdc.py              # CausalDilationClock (V,D)-tuple
│   ├── protocol.py         # Message, TaskRecord, MessageType
│   └── team_protocol.py    # Team, TeamMessage, γ_ij matrix
├── agents/
│   ├── base.py             # AsyncAgent ABC
│   ├── conductor.py        # Mission dispatcher + watchdog
│   ├── llm_agent.py        # Ollama / Anthropic / OpenAI
│   └── a2a_gateway.py      # A2A↔CDC translator
├── api/
│   ├── agents.py           # GET /api/agents
│   ├── chat.py             # POST /api/chat, SSE /api/chat/stream
│   ├── missions.py         # /api/missions/*, /spacetime
│   ├── web_bridge.py       # /ext/dilles/v1/* (dillenberg.net)
│   └── a2a/
│       └── gateway_router.py  # /a2a/tasks/send, /.well-known/agent.json
├── i18n/                   # en/de translations, t() function
└── storage/
    └── mission_store.py    # In-memory traces, SSE queues
```

## Protocols

### CDC (Causal-Dilation Clock)
Each message carries `clock: { vector: {...}, dilation: {...} }`.
- `vector`: Lamport-style causal ordering per agent
- `dilation`: Cumulative eigenzeit τ (operation count weighted by rate)

4 relations: `ORDERED` | `CAUSAL_DRIFT` | `CONCURRENT_DRIFT` | `INCONSISTENT`

### Team Protocol
Teams extend the CDC protocol with a shared team clock and γ_ij matrix.
The drift-compensated scheduler picks the least-drifted available agent.

### A2A Gateway
The `A2AGatewayAgent` speaks Google A2A externally and CDC internally.

```
External A2A client  →  POST /a2a/tasks/send
                         ↓
                   A2A Gateway Agent
                   wrap_a2a_task() → CDC Message
                         ↓
                   Conductor.dispatch()
                         ↓
                   Internal agent (Alice, etc.)
                         ↓
                   unwrap_cdc_response() → A2A Artifact
```

## Development

```bash
# Tests
python -m pytest tests/ -v

# Linting
ruff check backend/ tests/
ruff check backend/ tests/ --fix

# Server restart
lsof -ti :5050 | xargs kill -9; uvicorn backend.app:app --port 5050
```

## Multilingual

Default: **English**. German supported.
- Backend: `t(key, locale)` in `backend/i18n/` — add keys to `en.py` / `de.py`
- Frontend: `I18N` object + `data-i18n="key"` attributes — toggle EN|DE in header

## Design System

See [docs/DESIGN.md](docs/DESIGN.md).

## Roadmap

- [ ] Phase 4: Vue 3 + Vite frontend
- [ ] Phase 5: Google A2A full spec + dillenberg.net web bridge
- [ ] Martin: LLM Operator agent with CDC-aware team dispatch
- [ ] SQLite persistence for missions/traces
