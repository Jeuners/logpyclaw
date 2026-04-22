"""
core/routing.py — Deterministischer Router für A2A-Delegation.

Läuft VOR dem LLM-Call. Wenn eine Nachricht auf ein bekanntes Pattern passt,
wird direkt der richtige Agent ermittelt — kein LLM-Raten nötig.

Längster Regex-Match gewinnt (spezifischste Regel hat Vorrang).
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# (regex-pattern, agent-name) — längster Match gewinnt
# Reihenfolge spielt keine Rolle, da alle geprüft und der längste Match gewählt wird.
ROUTING_RULES: list[tuple[str, str]] = [
    # LinkedIn / Profile-Analysen / Webseiten → ARIA
    (r"\blinkedin\.com\b",                                          "ARIA"),
    (r"\bhttps?://\S*linkedin\S*",                                   "ARIA"),
    (r"\b(?:analysier|bewerte|beurteile)\b.{0,50}https?://",        "ARIA"),
    (r"\banalysier\w*\b.{0,50}(?:\w[\w-]*\.)+(?:de|com|org|net|io|at|ch)\b",  "ARIA"),
    (r"\b(?:bewerte|beurteile)\w*\b.{0,50}(?:\w[\w-]*\.)+(?:de|com|org|net|io|at|ch)\b", "ARIA"),
    (r"\b(?:schaue?|schau\s+mal)\b.{0,50}(?:\w[\w-]*\.)+(?:de|com|org|net|io|at|ch)\b", "ARIA"),
    (r"\b(?:seo|social[\s\-]?media|profil[\s\-]?analyse)\b",        "ARIA"),
    (r"\b(?:scrappe?|lies|lese|lad|öffne)\b.{0,30}https?://",       "ARIA"),

    # Code / Technisches → CodeCraft
    (r"\b(?:code|programmier|bug|debug|refactor|deploy|script)\b",  "CodeCraft"),
    (r"\b(?:python|javascript|typescript|html|css|sql|bash)\b",      "CodeCraft"),
    (r"\b(?:fehler\s+im\s+code|code\s+verbessern|funktion\s+schreiben)\b", "CodeCraft"),

    # Video / YouTube / Transkript → Video-Agent
    (r"\b(?:video|animier|clip|reel|film)\b",                       "Video-Agent"),
    (r"\b(?:transdownload|transkript|transcript|untertitel|subtitle|captions?)\b", "Video-Agent"),
    (r"youtu\.?be",                                                  "Video-Agent"),

    # Bilder → Picasso
    (r"\b(?:bild\s+generi|foto\s+erstell|zeichn|male\s+|artwork|illustr)\b", "Image-Agent"),
    (r"\bgenerate\s+(?:an?\s+)?image\b",                            "Image-Agent"),
    (r"\b(?:erstelle?|generier)\w*\b.{0,20}\b(?:bild|foto|image)\b","Image-Agent"),

    # E-Mail → Mailbox
    (r"\b(?:mail|e-mail|email|gmail|smtp)\b",                       "Mailbox"),

    # Allgemeinwissen / Recherche / Begriffserklärungen → Recon
    # (Wiki-Agent ist das PROJEKT-Wiki, nicht Wikipedia — daher hier NICHT routen)
    (r"\b(?:erkl[äa]r|was\s+ist|definition|wikipedia|wikidata)\b",  "Recon"),
    (r"\b(?:such\w*|search|google|recherchier\w*)\b.{0,30}\b(?:im\s+web|online|nach)\b", "Recon"),
    (r"\bweb[\s\-]?search\b",                                        "Recon"),
    # Alltagswissen / How-To-Fragen → Recon (web_search liefert Rezepte/Anleitungen)
    (r"\bwie\s+(?:backe|koche|mach|repariere|bastle|baue|pflanze|funktioniert|heißt)\b", "Recon"),
    (r"\b(?:rezept|anleitung|tutorial)\s+(?:für|zu|von)\b",          "Recon"),
    (r"\b(?:wer|wann|wo)\s+(?:ist|war|hat|ging|kommt)\b",             "Recon"),

    # Memory / Notizen → DREAM
    (r"\b(?:merk\s+(?:dir|es)|speicher\s+das|erinnerung|notiz)\b",  "DREAM"),
    (r"\bmerke\b.{0,10}\bdir\b",                                     "DREAM"),
]


def find_target_agent(message: str, all_agents: list[dict]) -> Optional[dict]:
    """
    Sucht den passendsten Agenten anhand deterministischer Regeln.

    Längster Regex-Match gewinnt → spezifischste Regel hat Vorrang.
    Gibt None zurück wenn kein Match → LLM entscheidet weiterhin.
    """
    agent_map = {a["name"].lower(): a for a in all_agents if a.get("name")}
    best_agent: Optional[dict] = None
    best_len = 0

    for pattern, agent_name in ROUTING_RULES:
        m = re.search(pattern, message, re.IGNORECASE)
        if m and len(m.group(0)) > best_len:
            target = agent_map.get(agent_name.lower())
            if target:
                best_agent = target
                best_len = len(m.group(0))

    if best_agent:
        logger.info(
            "DeterministicRouter: '%s...' → @%s (match_len=%d)",
            message[:50], best_agent["name"], best_len
        )
    return best_agent


# Patterns die direkt einem Skill des Ziel-Agenten entsprechen.
# Wenn keiner matcht → Router prefixt mit einem deterministischen Skill-Trigger,
# damit der Ziel-Agent nicht ins LLM-Halluzinieren fällt.
_RECON_SKILL_HINTS: list[tuple[str, str]] = [
    # (pattern, prefix oder None — None = original lassen)
    (r"\bwikipedia\b",                                                       ""),
    (r"\b(?:such\w*|search|google|duckduckgo|recherchier\w*|web[\s\-]?search)\b", ""),
    # Fallback für alle anderen Recon-Routen → web_search
    (r".*",                                                                  "suche im web nach "),
]


def reformulate_for_agent(message: str, target_agent_name: str) -> str:
    """
    Präpariert die Message für den Ziel-Agenten, damit dessen deterministische
    Skill-Trigger matchen. Verhindert LLM-Halluzination bei nicht-triggernden
    Nachrichten (z.B. 'wie backe ich brot' → 'suche im web nach wie backe ich brot').
    """
    if target_agent_name.lower() != "recon":
        return message
    # Finde den ersten passenden Hint
    for pattern, prefix in _RECON_SKILL_HINTS:
        if re.search(pattern, message, re.IGNORECASE):
            if prefix:
                logger.info(
                    "reformulate: '%s...' → prefix '%s' (für @%s)",
                    message[:50], prefix, target_agent_name
                )
                return prefix + message
            return message
    return message


def build_routing_table_for_prompt(all_agents: list[dict]) -> str:
    """
    Generiert eine lesbare Routing-Tabelle für den LLM-System-Prompt.
    So ist der Prompt immer synchron mit ROUTING_RULES.
    """
    # Sammle Agent → Pattern-Beschreibungen
    agent_tasks: dict[str, list[str]] = {}
    descriptions = {
        "ARIA":        "LinkedIn, SEO, Profil-Analyse, Webseiten-Analyse, Social Media",
        "CodeCraft":   "Code, Programmierung, Bugs, Scripts, technische Probleme",
        "Video-Agent": "Videos, Animationen, Clips, Reels, YouTube-Download, Transkript/Untertitel (transdownload)",
        "Image-Agent":     "Bilder generieren, Fotos, Illustrationen, Artwork",
        "Mailbox":     "E-Mails lesen und versenden",
        "Recon":       "Web-Recherche, Wikipedia, Begriffserklärungen, Definitionen, URL-Inhalte",
        "Wiki":        "Projekt-internes Wiki pflegen (ingest/query/lint im AgentClaw-Wiki)",
        "DREAM":       "Erinnerungen, Notizen, Memory speichern",
    }
    agent_names = {a["name"] for a in all_agents}
    lines = ["ROUTING TABLE (immer einhalten):"]
    for agent_name, desc in descriptions.items():
        if agent_name in agent_names:
            lines.append(f"  @{agent_name}: {desc}")
    return "\n".join(lines)
