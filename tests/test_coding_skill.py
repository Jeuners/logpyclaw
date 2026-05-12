"""
tests/test_coding_skill.py — CodingSkill execution guards.

Stellt sicher dass CodingSkill nicht stillschweigend „passthrough"-mäßig
durchläuft, wenn keine Datei wirklich geschrieben wurde — sonst markiert
der Operator-Loop (siehe services/task_service.py::_complete) den Task
fälschlich als completed, obwohl das versprochene Deliverable fehlt.
"""
from skills.coding_skill import CodingSkill


def test_passthrough_marks_executed_false():
    """Wenn weder list-Befehl noch Markdown-File-Blocks im Reply sind,
    muss `metadata.executed == False` gesetzt sein → halted_no_exec."""
    skill = CodingSkill()
    agent = {"name": "Tester", "skills": ["coding"]}
    result = skill.execute(agent, "irgendwas ohne files", content_to_save="")
    assert result.metadata is not None
    assert result.metadata.get("passthrough") is True
    assert result.metadata.get("executed") is False, (
        "Passthrough darf nicht als ausgeführt gelten — sonst hält halted_no_exec-Guard "
        "den Task fälschlich für erfolgreich."
    )


def test_passthrough_with_only_annotation_marks_halted():
    """LLM-Reply mit nur „[coding]" Annotation (kein Markdown-Block) →
    keine Files extrahiert → executed=False. Genauer Bug aus WILD-Run."""
    skill = CodingSkill()
    agent = {"name": "CodeCraft", "skills": ["coding"]}
    llm_reply = (
        "Ich baue die HTML-Datei mit allen Styles und Inhalten in einem einzigen "
        "Schritt. Da es sich um eine komplexe, responsive Struktur handelt, nutze "
        "ich den `coding`-Skill, um das Projekt `wild-newspaper` anzulegen.\n\n"
        "[coding]"
    )
    result = skill.execute(agent, "erstelle WILD newspaper", content_to_save=llm_reply)
    assert result.metadata.get("executed") is False
    assert result.text is None  # kein file_output → kein result_text


def test_real_files_succeed():
    """Wenn der LLM-Reply echte Markdown-File-Blocks enthält, muss der
    Skill normal ausführen (executed bleibt unset oder True)."""
    skill = CodingSkill()
    agent = {"name": "CodeCraft", "skills": ["coding"]}
    llm_reply = (
        "Hier ist das Projekt:\n\n"
        "### index.html\n"
        "```html\n"
        "<html><body>Hello</body></html>\n"
        "```\n"
    )
    result = skill.execute(agent, "baue eine Mini-Site", content_to_save=llm_reply)
    # Mit echten Files: metadata.files_written muss gesetzt sein
    assert result.metadata is not None
    assert result.metadata.get("files_written", 0) >= 1
    # executed darf NICHT False sein (entweder True oder nicht gesetzt)
    assert result.metadata.get("executed") is not False
