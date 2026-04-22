"""Unit-Tests für core.intent_detector."""
from core.intent_detector import detect_execution_intent, is_execution_intent


# ── Positive Execution-Intents ────────────────────────────────────────────
class TestExecutionIntents:
    def test_python_tool_german(self):
        intent = detect_execution_intent(
            "Führe das Python-Tool ~/Downloads/AgentClaw/projects/yt-transcribe/transcribe.py aus"
        )
        assert intent.kind == "python"
        assert intent.confidence >= 0.85
        assert intent.target and intent.target.endswith(".py")

    def test_python_direct_invocation(self):
        intent = detect_execution_intent("python3 /tmp/foo.py")
        assert intent.kind == "python"
        assert intent.confidence >= 0.85

    def test_execute_english(self):
        intent = detect_execution_intent("Execute the script /tmp/deploy.sh")
        assert intent.kind == "shell"
        assert intent.confidence >= 0.8
        assert intent.target == "/tmp/deploy.sh"

    def test_shell_code_fence(self):
        intent = detect_execution_intent("```bash\necho hi\n```")
        assert intent.kind == "shell"
        assert intent.confidence >= 0.9

    def test_starte_skript_path(self):
        intent = detect_execution_intent("Starte das Skript /opt/run.sh bitte")
        assert intent.kind == "shell"
        assert intent.confidence >= 0.8


# ── Read-Intents (dürfen NICHT als Execution zählen) ─────────────────────
class TestReadIntents:
    def test_lese_datei(self):
        intent = detect_execution_intent("Lese die Datei /Users/x/bar.txt")
        assert intent.kind == "read"
        assert not is_execution_intent(intent.matched_phrase)  # unrelated sanity

    def test_zeige_inhalt(self):
        intent = detect_execution_intent("Zeige mir den Inhalt von foo.py")
        assert intent.kind == "read"

    def test_beschreibe_bild(self):
        intent = detect_execution_intent("Beschreibe das Bild")
        assert intent.kind == "read"


# ── Negative / Ambigue Fälle ─────────────────────────────────────────────
class TestNonExecution:
    def test_smalltalk(self):
        intent = detect_execution_intent("Wie geht es dir?")
        assert intent.kind == "unknown"
        assert intent.confidence == 0.0

    def test_starte_server_weak(self):
        """'Starte den Server' ohne Pfad → schwache Confidence, nicht als exec zu werten."""
        intent = detect_execution_intent("Starte den Server")
        assert intent.confidence < 0.7
        assert not is_execution_intent("Starte den Server")

    def test_empty_input(self):
        intent = detect_execution_intent("")
        assert intent.kind == "unknown"
        assert intent.confidence == 0.0

    def test_none_input(self):
        intent = detect_execution_intent(None)  # type: ignore[arg-type]
        assert intent.kind == "unknown"


# ── is_execution_intent Convenience ──────────────────────────────────────
class TestIsExecutionIntent:
    def test_positive(self):
        assert is_execution_intent("Führe /tmp/x.py aus")

    def test_read_not_exec(self):
        assert not is_execution_intent("Lese /tmp/x.py")

    def test_threshold_default(self):
        # 'Starte den Server' liefert confidence 0.5 → unter default 0.7
        assert not is_execution_intent("Starte den Server")

    def test_threshold_custom(self):
        assert is_execution_intent("Starte den Server", threshold=0.4)
