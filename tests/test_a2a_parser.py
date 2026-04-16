"""
tests/test_a2a_parser.py — A2A-Mention-Parser Tests.

Testet core.a2a_protocol.parse_a2a_dispatches:
- Findet alle Mentions (nicht nur erste)
- Selbstreferenz ignoriert
- Code-Blöcke / Tabellen werden maskiert
- Fuzzy-Matching mit Umlauten
"""
from core.a2a_protocol import parse_a2a_dispatches


SENDER = {"id": "aria", "name": "ARIA"}
BOB = {"id": "bob", "name": "Bob"}
LISA = {"id": "lisa", "name": "Lisa"}
MUELLER = {"id": "mueller", "name": "Müller"}
AGENTS = [SENDER, BOB, LISA, MUELLER]


def test_single_mention():
    reply = "Ich frage @Bob ob er die Datei ansehen kann bitte."
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    assert len(out) == 1
    assert out[0].recipient_id == "bob"
    assert "Datei ansehen" in out[0].task_text


def test_multiple_mentions():
    reply = (
        "Ich verteile das jetzt.\n"
        "@Bob schau bitte die Logs an und melde dich mit Ergebnis\n"
        "@Lisa prüfe die Netzwerk-Latenz und gib mir einen Report"
    )
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    ids = sorted(d.recipient_id for d in out)
    assert ids == ["bob", "lisa"]


def test_self_reference_ignored():
    reply = "@ARIA ich delegiere das an mich selbst, bitte ignoriere diese lange Nachricht"
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    assert out == []


def test_unknown_agent_ignored():
    reply = "@Xavier bitte das erledigen und so weiter lange Nachricht"
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    assert out == []


def test_umlaut_fuzzy_match():
    reply = "@Mueller schau bitte nach und gib Bescheid nach Prüfung"
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    assert len(out) == 1
    assert out[0].recipient_id == "mueller"


def test_code_block_ignored():
    reply = (
        "Hier ist Code:\n"
        "```\n"
        "@Bob das ist nur ein Beispiel innerhalb eines Code-Blocks\n"
        "```\n"
        "Das war's eigentlich schon heute."
    )
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    assert out == []


def test_markdown_table_ignored():
    reply = (
        "| Spalte | Wert |\n"
        "| --- | --- |\n"
        "| @Bob | demo |\n"
    )
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    assert out == []


def test_too_short_task_skipped():
    # Task-Text < 10 Zeichen → überspringen
    reply = "@Bob ok"
    out = parse_a2a_dispatches(reply, SENDER, AGENTS)
    assert out == []


def test_no_mentions_returns_empty():
    out = parse_a2a_dispatches("Reiner Text ohne Mentions.", SENDER, AGENTS)
    assert out == []


def test_empty_reply_returns_empty():
    assert parse_a2a_dispatches("", SENDER, AGENTS) == []
    assert parse_a2a_dispatches("Hi @Bob", SENDER, []) == []


def test_delegation_depth_increments():
    reply = "@Bob schau dir das kurz an und gib mir Rückmeldung"
    out = parse_a2a_dispatches(reply, SENDER, AGENTS, sender_delegation_depth=2)
    assert out[0].delegation_depth == 3
