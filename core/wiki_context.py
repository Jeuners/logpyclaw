"""
core/wiki_context.py — Ambient Wiki-Context Injection.

Sucht bei jeder User-Nachricht automatisch passende Wiki-Seiten und
injiziert relevante Exzerpte in den System-Prompt. So kennen Agenten
das Wiki-Wissen "ambient", ohne dass der User explizit "lies wiki X"
tippen muss.

Strategie:
  1. Keywords aus der Message extrahieren (>=4 Zeichen, ohne Stopwords).
  2. Jede .md im Wiki scoren:
       Slug-Match (+10 pro Keyword)
       Content-Frequency (+1 pro Vorkommen, capped)
  3. Top-N Seiten (default 2) als Exzerpt in den System-Prompt hängen.
  4. Skip wenn Agent kein wiki_read-Skill hat, bei direkten Wiki-Commands,
     und bei system-generierten Supervisor-Callback-Messages.
"""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# Deutsche + englische Stopwords. Bewusst kurz gehalten — wir filtern eh
# schon alles unter 4 Zeichen raus. Nur die wirklich lauten Wörter.
_STOPWORDS = frozenset("""
aber auch alle alles allem allen beim dann damit dass dein deine diese dieser
dieses dort durch eine einem einen einer eines etwa etwas haben hatte hatten
hier immer jene jener kein keine keiner konnte können machen mehr muss müssen
nach nicht noch oder ohne sein seine seiner sich solch solche sondern unter
viel viele waren werden wenn wieder wird wurde wurden zwar dich sich mich uns

about above after again before being below between doing does during each
every from have having just more most other over same some such than that
then there these they this those through under very were what when which
while with would your yours
""".split())

# Direkt-Command-Matches: wenn der User sowieso die Wiki liest, kein Ambient nötig
_DIRECT_CMD_RX = re.compile(
    r"\b(?:liste[nt]?|list|zeig\w*|show|lese?|lies|read|öffne|open)\b"
    r".{0,30}\bwiki\b",
    re.IGNORECASE,
)
_TOPIC_ASK_RX = re.compile(
    r"\bwas\s+(?:steht|gibt\s+es|weiß)\s+(?:im\s+wiki\s+)?(?:zu|über)\b",
    re.IGNORECASE,
)
# Keyword-Extraktion: Wörter mit mindestens 4 Zeichen, inkl. Bindestrich
_KEYWORD_RX = re.compile(r"[a-zA-ZäöüÄÖÜß][\w\-]{3,}")


def _wiki_base() -> str | None:
    """Liest wiki_dir aus dem Wiki-Agent. None wenn nicht konfiguriert."""
    try:
        from storage.agents import load_agents
    except Exception:
        return None
    for a in load_agents():
        if a.get("name", "").lower() == "wiki":
            d = (a.get("wiki_dir") or "").strip()
            if d:
                expanded = os.path.expanduser(d)
                if os.path.isdir(expanded):
                    return expanded
    return None


def _extract_keywords(message: str) -> list[str]:
    """Keywords aus der Message: >=4 Zeichen, lowercase, ohne Stopwords, dedupe."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in _KEYWORD_RX.findall(message):
        t = tok.lower()
        if t in _STOPWORDS or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= 15:
            break
    return out


@lru_cache(maxsize=1)
def _index_pages(base: str, mtime_key: float) -> list[tuple[str, str]]:
    """Liest alle .md-Dateien einmal ein. mtime_key invalidiert den Cache
    wenn im Wiki-Verzeichnis was geändert wurde."""
    pages: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(base):
        for f in sorted(files):
            if not f.lower().endswith(".md"):
                continue
            full = os.path.join(root, f)
            try:
                with open(full, encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                continue
            rel = os.path.relpath(full, base)
            pages.append((rel, content))
    return pages


def _wiki_mtime_key(base: str) -> float:
    """Summe aller mtimes im Wiki-Dir — Fingerprint für Cache-Invalidation."""
    total = 0.0
    try:
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.lower().endswith(".md"):
                    total += os.path.getmtime(os.path.join(root, f))
    except Exception:
        return 0.0
    return total


def _score_page(rel: str, content: str, keywords: list[str]) -> int:
    """Scoring: Slug-Match (stark) + Content-Frequency (schwach, capped bei 5 pro kw)."""
    slug = os.path.basename(rel)[:-3].lower()
    content_l = content.lower()
    score = 0
    for kw in keywords:
        if kw in slug:
            score += 10
        freq = content_l.count(kw)
        score += min(freq, 5)
    return score


def _make_excerpt(content: str, keywords: list[str], max_len: int = 400) -> str:
    """Sucht den ersten Absatz der mindestens ein Keyword enthält.
    Fallback: erste N Zeichen des Docs."""
    for para in content.split("\n\n"):
        para_l = para.lower()
        if any(kw in para_l for kw in keywords):
            p = para.strip()
            if len(p) > max_len:
                p = p[:max_len].rstrip() + "…"
            return p
    stub = content.strip()[:max_len]
    if len(content) > max_len:
        stub += "…"
    return stub


def find_relevant_pages(message: str, max_pages: int = 2) -> list[tuple[str, str]]:
    """Gibt [(page_rel, excerpt), ...] nach Relevanz sortiert zurück."""
    base = _wiki_base()
    if not base:
        return []
    keywords = _extract_keywords(message)
    if not keywords:
        return []

    pages = _index_pages(base, _wiki_mtime_key(base))
    scored: list[tuple[int, str, str]] = []
    for rel, content in pages:
        s = _score_page(rel, content, keywords)
        if s >= 10:  # Mindest-Score: mindestens ein Slug-Match
            scored.append((s, rel, content))

    scored.sort(key=lambda t: -t[0])
    return [(rel, _make_excerpt(content, keywords)) for _, rel, content in scored[:max_pages]]


def build_wiki_context_block(message: str, agent: dict) -> str:
    """Markdown-Block zur Injektion in den System-Prompt, oder leer.

    Skip-Conditions:
      - Agent hat kein wiki_read-Skill (kennt das Wiki nicht)
      - Message ist ein direkter Wiki-Command (Skill feuert sowieso)
      - Message ist ein Supervisor-Callback (system-generiert)
      - Message zu kurz (< 12 Zeichen)
      - Keine relevante Seite gefunden
    """
    if "wiki_read" not in agent.get("skills", []):
        return ""
    if not message or len(message.strip()) < 12:
        return ""
    if message.lstrip().startswith("[SUPERVISOR-CALLBACK"):
        return ""
    if _DIRECT_CMD_RX.search(message) or _TOPIC_ASK_RX.search(message):
        return ""

    try:
        pages = find_relevant_pages(message, max_pages=2)
    except Exception as e:
        logger.warning("wiki_context: find_relevant_pages failed: %s", e)
        return ""

    if not pages:
        return ""

    lines = ["## 📚 Wiki-Kontext (automatisch abgerufen)"]
    lines.append(
        "Folgende Wiki-Seiten scheinen relevant für die aktuelle Frage. "
        "Nutze sie als Referenz, wenn sie passen. Volle Seite bei Bedarf: "
        "`lies wiki <seitenname>`.\n"
    )
    for rel, excerpt in pages:
        lines.append(f"### 📄 {rel}")
        lines.append(excerpt)
        lines.append("")
    block = "\n".join(lines).rstrip()
    logger.info(
        "wiki_context: injected %d page(s) for agent '%s' — pages: %s",
        len(pages), agent.get("name", "?"), [p[0] for p in pages],
    )
    return block
