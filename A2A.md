# AgentClaw A2A Protocol Documentation

## Overview

AgentClaw implements the Google Agent-to-Agent (A2A) Protocol for inter-agent communication.

## Task States

```
submitted → working → input-required → completed/failed/canceled/rejected/auth-required
```

| State | Description | Cancelable |
|-------|-------------|------------|
| `submitted` | Task received, waiting for processing | ✅ |
| `working` | Task is actively being processed | ✅ |
| `input-required` | Agent needs additional input from client | ✅ |
| `completed` | Task completed successfully | ❌ |
| `failed` | Task failed with error | ❌ |
| `canceled` | Task was canceled by client | ❌ |
| `rejected` | Task rejected (e.g., unsupported) | ❌ |

## API Endpoints

### Agent Discovery

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/a2a/agents` | GET | Get all agents with skills (A2A format) |
| `/api/agents/list` | GET | Get all agents (compact format) |
| `/api/agents/cards` | GET | Get all agent cards |
| `/api/a2a/agents/<agent_id>/card` | GET | Get extended agent card |

### Task Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tasks` | POST | Create new task |
| `/api/a2a/tasks` | GET | List tasks with pagination |
| `/api/tasks/<task_id>` | GET | Get task status |
| `/api/a2a/tasks/<task_id>/cancel` | POST | Cancel a task |
| `/api/a2a/tasks/<task_id>/subscribe` | GET | SSE streaming for task updates |
| `/api/a2a/tasks/<task_id>/pushConfig` | POST | Create push notification config |
| `/api/a2a/tasks/<task_id>/input` | POST | Set task to input-required state |

## Agent Directory

Every agent receives the agent directory in its system prompt via `_build_agent_directory()`:

```
--- AGENT NETWORK ---
Du bist Teil eines Multi-Agent-Systems. Du kannst Tasks jederzeit an andere Agents delegieren.
Delegations-Syntax: @AgentName <Aufgabe>

VERFÜGBARE AGENTS:
  • Picasso (DU) — Skills: 📸 Screenshot, 🎨 Image Gen
  • Fotograf — Skills: 🎨 Image Gen, ✏️ Prompt Optimize
  • MARTIN — Skills: 🧠 Memory, 🌐 Web Search
...
```

## Communication Prompt

Each agent receives `A2A_COMMUNICATION_PROMPT` with behavior rules:

```
--- A2A KOMMUNIKATION ---
Du bist Teil des AgentClaw Multi-Agent-Systems. Agents kommunizieren über das A2A-Protokoll.

VERHALTENSREGELN:
1. Antworte NUR wenn du direkt angesprochen wirst oder eigenständig handeln musst.
2. Wenn ein Task nicht zu deinen Skills passt, delegiere an den passenden Agenten.
3. Antworte präzise und minimal — keine langen Erklärungen.

DELEGIERUNG (@Mention):
  • Schreibe @AgentName gefolgt von deiner Anfrage
  • Beispiel: "@Fotograf generiere ein Bild von einer Katze"
  • Der Ziel-Agent übernimmt und liefert das Ergebnis zurück
...
```

## Delegation Flow

1. **User mentions agent**: `@Fotograf generiere ein Bild`
2. **Frontend detects**: `_MENTION_RX` matches `@AgentName`
3. **Task created**: `_dispatch_mentions_from_reply()` creates task
4. **Processing**: `process_task()` executes in background thread
5. **Result**: Status `working` → `completed`/`failed`
6. **History**: Both sender and recipient get result in history

## Data Model

### Task Object
```json
{
  "id": "uuid",
  "contextId": "uuid",
  "sender_agent_id": "uuid",
  "recipient_agent_id": "uuid",
  "message": "string",
  "status": "submitted|working|completed|failed|canceled|input-required",
  "skill_used": "string|null",
  "result_text": "string|null",
  "result_image": "base64|null",
  "artifacts": [],
  "history": [],
  "created_at": "ISO8601",
  "completed_at": "ISO8601|null"
}
```

### Agent Object
```json
{
  "id": "uuid",
  "name": "string",
  "role": "string",
  "skills": ["image_gen", "web_search", "memory", ...],
  "provider": "ollama|openrouter",
  "model": "string"
}
```

## Skills

| Skill | Description |
|-------|-------------|
| `image_gen` | Image generation via ComfyUI |
| `image_edit` | Image editing |
| `web_search` | Web search via SearXNG |
| `memory` | Long-term memory via Qdrant |
| `gmail` | Gmail integration |
| `telegram` | Telegram bot |
| `prompt_optimize` | Prompt optimization |
| `url_fetch` | URL content fetching |
| `screenshot` | Screenshot capture |

## Error Handling

- **400**: Bad request (missing fields, invalid state)
- **404**: Task/Agent not found
- **405**: Method not allowed

## Testing

```bash
# Get agents
curl http://localhost:5050/api/a2a/agents

# Create task
curl -X POST http://localhost:5050/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "sender_agent_id": "...",
    "recipient_agent_id": "...",
    "message": "Sag Hallo"
  }'

# Check status
curl http://localhost:5050/api/tasks/<task_id>

# Cancel task
curl -X POST http://localhost:5050/api/a2a/tasks/<task_id>/cancel

# SSE streaming
curl http://localhost:5050/api/a2a/tasks/<task_id>/subscribe
```
