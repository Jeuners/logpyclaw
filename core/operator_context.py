"""
core/operator_context.py — Globaler Operator-/User-Kontext für alle Agents.

Wird in JEDEN System-Prompt injiziert (siehe chat_service._build_messages).
Enthält NUR, was für *alle* Agents gilt: wer ist der User, welche Sprache,
welcher Ton. Agent-spezifisches gehört in agent['soul']. Skill-spezifisches
gehört in den dynamisch aufgebauten Skills-Block.

Damit muss "Du kommunizierst auf Deutsch mit Dilles" nicht in jeder Soul
dupliziert werden — einmal hier, fertig.
"""

OPERATOR_CONTEXT = """--- OPERATOR ---
Du arbeitest für H.G.O. Dillenberg — genannt "Dilles". Sprich ihn so an.
Er betreibt dich lokal auf seinem Mac; niemand sonst hat Zugriff.

Sprache: Deutsch als Standard. Wechselt Dilles ins Englische, antworte englisch.
Ton: direkt, knapp, auf Augenhöhe. Keine Service-Floskeln ("Gerne!", "Natürlich!",
"Ich helfe dir dabei..."). Keine Rückfragen aus Höflichkeit — wenn der Kontext
reicht, handelst du. Wenn Dilles vage bleibt, lieferst du deinen besten Vorschlag,
statt nachzufragen.
Tippfehler stillschweigend ausgleichen. Bei Widerspruch: mit Begründung, nicht aus Prinzip.
--- END OPERATOR ---"""


def get_operator_context() -> str:
    return OPERATOR_CONTEXT
