"""
L2-Tests für ChatService — Message-Flow, @Mention-Dispatch, Task-Integration.

Verwendet mock_llm um das LLM deterministisch zu simulieren,
sync_spawn + clean_tasks damit dispatchte Tasks direkt landen.
"""
import pytest

from core.state import _TASKS


# ── handle_message: Basis-Flow ───────────────────────────────────────────────

def test_handle_message_returns_llm_reply(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """Einfache User-Nachricht → LLM liefert Antwort, reply wird returned."""
    mock_llm.set_reply("Hallo zurück!")
    agent = make_agent("Reply")

    result = container.chat.handle_message(agent["id"], "Hallo")

    assert result["agent_id"] == agent["id"]
    assert result["reply"] == "Hallo zurück!"
    assert result["skill"] is None
    assert result["image"] is None


def test_handle_message_stores_history(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """Nach handle_message steht User + Assistant in agent_service.get_history()."""
    mock_llm.set_reply("Gemerkt.")
    agent = make_agent("Memory")

    container.chat.handle_message(agent["id"], "Merk dir: Apfel")

    history = container.agents.get_history(agent["id"])
    # Erwartet: mind. 2 Einträge (user + assistant)
    assert len(history) >= 2
    roles = [h["role"] for h in history[-2:]]
    assert "user" in roles
    assert "assistant" in roles


# ── @Mention Dispatch → TaskService ──────────────────────────────────────────

def test_mention_in_llm_reply_dispatches_a2a_task(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """
    LLM-Reply enthält @Recipient — sollte einen Task einreihen
    UND reply (strip_a2a_for_display) dürfte die Mention nicht mehr enthalten.
    """
    recipient = make_agent("Receiver")
    sender = make_agent("Sender")

    # Mock soll direkt eine @Mention zurückgeben
    mock_llm.set_reply(f"@{recipient['name']} Schreib bitte einen Bericht.")

    result = container.chat.handle_message(sender["id"], "Beauftrage Receiver")

    # Task wurde erzeugt
    tasks_for_recipient = [
        t for t in _TASKS.values() if t.get("recipient_agent_id") == recipient["id"]
    ]
    assert len(tasks_for_recipient) >= 1, "Kein Task für Receiver erstellt"

    # Displayreply enthält die Mention nicht mehr (strip_a2a_for_display)
    assert f"@{recipient['name']}" not in result["reply"]

    # a2a_dispatches enthält den Recipient
    assert result["a2a_dispatches"]
    assert result["a2a_dispatches"][0]["recipient_name"] == recipient["name"]


def test_mention_to_nonexistent_agent_does_not_crash(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """@Phantasie → keine Exception; kein Task für unbekannten Empfänger."""
    sender = make_agent("Speaker")
    mock_llm.set_reply("@GhostAgent Tu irgendwas.")

    result = container.chat.handle_message(sender["id"], "Test")

    # Kein Crash; reply kommt durch
    assert "reply" in result
    # Keine A2A-Dispatches da Empfänger nicht existiert
    assert result["a2a_dispatches"] == []


# ── _build_skills_prompt ─────────────────────────────────────────────────────

def test_build_skills_prompt_lists_agent_skills(
    container, make_agent
):
    """_build_skills_prompt gibt Text mit den Skills des Agents zurück."""
    agent = make_agent("Skiller", skills=["url_fetch", "file_access"])
    prompt = container.chat._build_skills_prompt(agent)
    assert isinstance(prompt, str)
    # Mindestens einer der Skills sollte im Prompt erwähnt sein
    assert "url_fetch" in prompt or "file_access" in prompt


# ── Self-Mention wird nicht dispatched ──────────────────────────────────────

def test_self_mention_is_not_dispatched(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """@SelfName in eigener Reply darf keinen Self-Task erzeugen (Schleifen-Schutz)."""
    agent = make_agent("Selfish")
    mock_llm.set_reply(f"@{agent['name']} Mach es selbst.")

    container.chat.handle_message(agent["id"], "Test")

    tasks_for_self = [
        t for t in _TASKS.values() if t.get("recipient_agent_id") == agent["id"]
    ]
    assert tasks_for_self == [], "Self-Mention hat Task erzeugt (darf nicht)"
