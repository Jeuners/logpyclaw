"""
skills/wiki_read.py — Read-only Wiki-Zugriff für alle Agenten.

Im Gegensatz zu file_access (voller Read/Write + agent-spezifisches wiki_dir)
liest dieser Skill ausschließlich aus dem zentralen Wiki des Wiki-Agenten.
So können MARTIN, CodeCraft, ARIA etc. Wiki-Inhalte konsultieren, aber das
Wiki-Schreiben bleibt exklusive Domäne des Wiki-Agenten.

Trigger (werden über registry.find_matching deterministisch matched):
    "liste wiki", "zeige wiki"              → list_wiki_pages()
    "lies wiki <name>", "öffne wiki <name>" → read_wiki_page(name)
    "was steht im wiki zu <topic>"          → read best-match page
"""
import logging
import os
import re

from skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)


def _resolve_wiki_dir() -> str | None:
    """Liest wiki_dir aus dem Wiki-Agent in data/agents.json.
    So müssen wir den Pfad nicht hardcoden — und wenn der User das
    Wiki umzieht, folgt der Skill automatisch.
    """
    try:
        from storage.agents import load_agents
    except Exception:
        return None
    for a in load_agents():
        if a.get("name", "").lower() == "wiki":
            raw = (a.get("wiki_dir") or "").strip()
            if raw:
                return os.path.expanduser(raw)
    return None


def _safe_rel(rel: str) -> str | None:
    """Verhindert Path-Traversal. Gibt sauberen relativen Pfad zurück oder None."""
    rel = (rel or "").strip().strip("/").strip()
    if not rel:
        return None
    # Kein ..  kein absoluter Pfad
    if ".." in rel.split("/") or rel.startswith("/"):
        return None
    return rel


def list_wiki_pages(base: str) -> str:
    if not os.path.isdir(base):
        return f"📂 Wiki-Verzeichnis nicht gefunden: `{base}`"
    lines: list[str] = []
    for root, _dirs, files in sorted(os.walk(base)):
        for f in sorted(files):
            if not f.lower().endswith(".md"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base)
            size_kb = os.path.getsize(full) / 1024
            lines.append(f"- `{rel}` ({size_kb:.1f} KB)")
    if not lines:
        return f"📂 Wiki `{base}` ist leer."
    return f"📂 **Wiki `{base}`:**\n\n" + "\n".join(lines[:200])


def read_wiki_page(base: str, rel: str) -> str:
    safe = _safe_rel(rel)
    if not safe:
        return f"❌ Ungültiger Pfad: `{rel}`"
    # .md automatisch anhängen wenn fehlt
    if not safe.lower().endswith(".md"):
        safe = safe + ".md"
    # pages/ Prefix probieren wenn es nicht direkt findbar ist
    candidates = [safe]
    if not safe.startswith("pages/"):
        candidates.append(f"pages/{safe}")
    for cand in candidates:
        full = os.path.join(base, cand)
        if os.path.isfile(full):
            try:
                with open(full, encoding="utf-8") as fh:
                    body = fh.read()
                if len(body) > 20_000:
                    body = body[:20_000] + "\n\n…(gekürzt)"
                return f"📄 **{cand}**:\n\n{body}"
            except Exception as e:
                return f"❌ Lese-Fehler `{cand}`: {e}"
    return f"❌ Wiki-Seite nicht gefunden: `{rel}` (probiert: {', '.join(candidates)})"


def best_match_page(base: str, topic: str) -> str:
    """Fuzzy: sucht .md-Dateien deren Slug den Topic enthält."""
    topic_norm = re.sub(r"[^\w]+", "-", topic.lower()).strip("-")
    if not topic_norm or not os.path.isdir(base):
        return f"❌ Kein Treffer für: `{topic}`"
    hits: list[str] = []
    for root, _dirs, files in os.walk(base):
        for f in files:
            if not f.lower().endswith(".md"):
                continue
            slug = f[:-3].lower()
            if topic_norm in slug or slug in topic_norm:
                rel = os.path.relpath(os.path.join(root, f), base)
                hits.append(rel)
    if not hits:
        return f"❌ Kein Wiki-Eintrag zu `{topic}` gefunden."
    # Nimm den ersten Treffer, listen die anderen als Hinweis
    primary = read_wiki_page(base, hits[0])
    if len(hits) > 1:
        others = ", ".join(f"`{h}`" for h in hits[1:5])
        primary += f"\n\n— Weitere Treffer: {others}"
    return primary


_LIST_RX = re.compile(
    r"\b(?:liste[nt]?|list|zeig\w*|show|ls)\b.{0,30}\bwiki\b", re.IGNORECASE
)
_READ_RX = re.compile(
    r"\b(?:lese?|lies|read|öffne|open|zeige?|show)\s+wiki\s+([\w\-\./]+)",
    re.IGNORECASE,
)
_TOPIC_RX = re.compile(
    r"\bwas\s+(?:steht|gibt\s+es|weiß\s+das\s+wiki)\s+(?:im\s+wiki\s+)?(?:zu|über|zum\s+thema)\s+(.+?)[\?\.!]*$",
    re.IGNORECASE,
)


def run_wiki_read(message: str) -> str | None:
    base = _resolve_wiki_dir()
    if not base:
        return None

    # Listing
    if _LIST_RX.search(message):
        return list_wiki_pages(base)

    # Named page read
    m = _READ_RX.search(message)
    if m:
        return read_wiki_page(base, m.group(1))

    # Topic-Fuzzy
    m = _TOPIC_RX.search(message)
    if m:
        return best_match_page(base, m.group(1).strip())

    return None


class WikiReadSkill(BaseSkill):
    id = "wiki_read"
    name = "Wiki Read"
    icon = "menu_book"
    description = (
        "Liest aus dem zentralen AgentClaw-Wiki. Read-only — nur der Wiki-Agent "
        "selbst darf schreiben. Nutzbar für MARTIN, CodeCraft, ARIA und andere, "
        "die Wiki-Wissen konsultieren sollen."
    )
    triggers = [
        r"\b(?:liste[nt]?|list|zeig\w*|show|ls)\b.{0,30}\bwiki\b",
        r"\b(?:lese?|lies|read|öffne|open|zeige?|show)\s+wiki\s+[\w\-\./]+",
        r"\bwas\s+(?:steht|gibt\s+es)\s+(?:im\s+wiki\s+)?(?:zu|über|zum\s+thema)\b",
    ]
    requires: list[str] = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        try:
            result = run_wiki_read(message)
            if result is None:
                return SkillResult(text=None, skill_used=self.id, metadata={"passthrough": True})
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            logger.exception("wiki_read failed")
            return SkillResult(error=str(e), skill_used=self.id)
