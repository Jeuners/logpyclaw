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
| `auth-required` | Authentication required to continue | ❌ |

## API Endpoints

### Agent Discovery

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents` | GET | Get all agents |
| `/api/agents/cards` | GET | Get all agent cards with skills |
| `/api/agents/capabilities` | GET | Filter agents by skill |
| `/api/skills` | GET | Get all available skills |

### Agent Settings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents/<id>/settings` | PUT | Update agent name, role, model, etc. |
| `/api/agents/<id>/voice` | PUT | Set agent TTS voice |
| `/api/agents/<id>/skills` | PUT | Enable/disable skills |
| `/api/agents/<id>/heartbeat` | PUT | Configure heartbeat (auto-task) |
| `/api/agents/<id>/heartbeat/run` | POST | Trigger heartbeat manually |
| `/api/agents/<id>/dream` | PUT | Configure dream (memory cleanup) |
| `/api/agents/<id>/dream/run` | POST | Run dream cycle manually |

### Task Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tasks` | POST | Create new task |
| `/api/tasks` | GET | List tasks with pagination |
| `/api/tasks/<task_id>` | GET | Get task status |
| `/api/tasks/<task_id>` | DELETE | Delete a task |
| `/api/tasks/<task_id>/cancel` | POST | Cancel a task |

### Skills API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/skills` | GET | List all skills with availability |
| `/api/skills/<skill>/check` | GET | Check if skill is available |

### Memory & Documents

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/memory/<agent_id>` | GET | Get agent memories from Qdrant |
| `/api/memory/<agent_id>` | DELETE | Clear all memories |
| `/api/memory/<agent_id>/document` | POST | Upload PDF/image to vector store |
| `/api/history/<agent_id>` | GET/DELETE | Chat history |

### Other Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Chat with an agent |
| `/api/prompt/optimize` | POST | Optimize a prompt |
| `/api/image/edit` | POST | Edit an image |
| `/api/tagesschau` | GET | German news feed |
| `/api/hackernews` | GET | Hacker News feed |
| `/api/screenshot` | POST | Take website screenshot |

## Agent Directory

Every agent receives the agent directory in its system prompt via `_build_agent_directory()`:

```
--- AGENT NETWORK ---
Du bist Teil eines Multi-Agent-Systems. Du kannst Tasks jederzeit an andere Agents delegieren.
Delegations-Syntax: @AgentName <Aufgabe>

VERFÜGBARE AGENTS:
  • Picasso (DU) — Skills: 📸 Screenshot, 🎨 Image Gen
  • LISA — Skills: 📰 Tagesschau News, 🧠 Memory
  • MARTIN — Skills: 🧠 Memory, ✏️ Prompt Optimize, 🎩 Hacker News
  • Flo — Skills: 🎨 Image Gen, ✏️ Prompt Optimize
  • Jan — Skills: 📸 Screenshot, 🎨 Image Gen
  • Fotograf — Skills: 🎨 Image Gen
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

HERKUNFT:
  • Bei @Mention von User: Task kommt vom User (sender_agent_id = "user")
  • Bei @Mention aus einem anderen Agent: Task vom Sender-Agent
```

## Delegation Flow

1. **User mentions agent**: `@Fotograf generiere ein Bild`
2. **Frontend detects**: `_MENTION_RX` matches `@AgentName` in input
3. **Task created**: `_dispatch_mentions_from_input()` creates task
4. **Processing**: `process_task()` executes in background thread
5. **Result**: Status `working` → `completed`/`failed`
6. **History**: Both sender and recipient get result in history

### Reply Delegation

Agents can also delegate to other agents by mentioning them in their reply:

1. **Agent replies**: `@Fotograf generiere ein Bild von einer Katze`
2. **Frontend detects**: `dispatchReplyMentions()` finds `@AgentName` in reply
3. **Task created**: `_dispatch_mentions_from_prompt()` creates task
4. **Processing**: Same as above

## Data Model

### Task Object
```json
{
  "id": "uuid",
  "sender_agent_id": "uuid",
  "sender_agent_name": "string",
  "recipient_agent_id": "uuid",
  "recipient_agent_name": "string",
  "message": "string",
  "status": "submitted|working|completed|failed|canceled|rejected|input-required",
  "skill_used": "string|null",
  "result_text": "string|null",
  "result_image": "base64|null",
  "error": "string|null",
  "created_at": "ISO8601",
  "completed_at": "ISO8601|null",
  "timeout_at": "ISO8601"
}
```

### Agent Object
```json
{
  "id": "uuid",
  "name": "string",
  "role": "string",
  "provider": "ollama|mistral|openrouter|google",
  "model": "string",
  "soul": "string (system prompt)",
  "skills": ["image_gen", "memory", "telegram", ...],
  "voice": "string|null",
  "avatar": "base64|null",
  "color": "hex|null",
  "heartbeat": {
    "active": "boolean",
    "interval_min": "number",
    "prompt": "string"
  },
  "dream": {
    "active": "boolean",
    "retention_days": "number"
  }
}
```

## Skills

| Skill | Description | Requires |
|-------|-------------|----------|
| `image_gen` | Image generation via ComfyUI | ComfyUI |
| `image_edit` | Image editing | ComfyUI |
| `memory` | Long-term memory via Qdrant | Qdrant |
| `document_memory` | PDF/image upload to vector store | Qdrant + Google API |
| `telegram` | Telegram bot | Telegram API |
| `gmail` | Gmail integration | Gmail API |
| `prompt_optimize` | Prompt optimization via Ollama | Ollama |
| `url_fetch` | URL content fetching | None |
| `screenshot` | Screenshot capture via Playwright | Playwright |
| `tagesschau` | German news feed | None |
| `hackernews` | Hacker News feed | None |
| `telegram_incoming` | Receive Telegram messages | Telegram API |

## Heartbeat

Agents can run periodic tasks automatically:

- **Interval**: 1-1440 minutes
- **Trigger**: Backend scheduler checks every minute
- **Use case**: Daily status updates, data fetching, delegation to other agents

## Dream (Memory Cleanup)

Agents with memory skill can auto-cleanup old memories:

- **Retention**: 1-365 days
- **Trigger**: Daily via `_run_dream_cycle()`
- **Action**: Deletes vector entries older than retention period

## Error Handling

| Status Code | Description |
|------------|-------------|
| 400 | Bad request (missing fields, invalid state) |
| 404 | Task/Agent not found |
| 405 | Method not allowed |
| 503 | Service unavailable (e.g., Qdrant not running) |

## Testing

```bash
# Get all agents
curl http://localhost:5050/api/agents

# Get all skills
curl http://localhost:5050/api/skills

# Chat with agent (also handles @Mentions)
curl -X POST http://localhost:5050/api/chat \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "...", "message": "@Fotograf generiere ein Bild"}'

# Create task directly
curl -X POST http://localhost:5050/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "sender_agent_id": "...",
    "recipient_agent_id": "...",
    "message": "Sag Hallo"
  }'

# Check task status
curl http://localhost:5050/api/tasks/<task_id>

# Cancel task
curl -X POST http://localhost:5050/api/tasks/<task_id>/cancel

# Upload document to memory
curl -X POST http://localhost:5050/api/memory/<agent_id>/document \
  -F "file=@document.pdf"
```

## Memory Clear Trigger

Agents with memory skill can have their memory cleared:

```
User: "vergiss das", "lösche memory", "clear memory"
→ Memory collection for that agent is deleted from Qdrant
```

## Voice Language

Speech recognition language can be set per-browser:
- Stored in localStorage as `voiceLang`
- Options: de-DE, en-US, es-ES, fr-FR
- Auto-detected from browser language on first visit
