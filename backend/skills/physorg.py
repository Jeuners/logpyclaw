"""
backend/skills/physorg.py — Wissenschaftsnews von phys.org.

Drei Modi (automatisch erkannt aus der Query):
  1. Artikel-Volltext — wenn eine phys.org/news/…html-URL enthalten ist:
       Titel + Fließtext extrahieren (für Zusammenfassung/Analyse).
  2. Nach Kategorie — Keyword-Erkennung (Physik, Weltraum, Nano, Erde, Bio, Chemie):
       neueste Artikel aus dem passenden RSS-Feed.
  3. Neueste (Default) — Haupt-Feed, Top-Artikel als Liste.

Trigger-Beispiele:
  "phys.org neueste"
  "phys.org weltraum news"
  "phys.org https://phys.org/news/2026-06-...html"
"""
from __future__ import annotations

import html as _html
import re

import httpx

from backend.skills import Skill

_BASE = "https://phys.org"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# Kategorie → RSS-Feed-Slug (alle als HTTP 200 verifiziert).
_CATEGORIES: list[tuple[str, str, str]] = [
    ("space-news",     "🚀", r"\b(weltraum|space|astronom\w*|raumfahrt|kosmos|planet\w*|mars|mond|moon|galax\w*|nasa|stern\w*|asteroid\w*)\b"),
    ("nanotech-news",  "🔬", r"\b(nano\w*)\b"),
    ("chemistry-news", "⚗️", r"\b(chemie|chemistry|molek\w*|molecul\w*|reaktion|katalys\w*)\b"),
    ("earth-news",     "🌍", r"\b(erde|earth|klima\w*|climate|geo\w*|umwelt|environment|ozean|ocean|vulkan|volcan\w*)\b"),
    ("biology-news",   "🧬", r"\b(bio\w*|leben|life|zell\w*|gen\w*|evolution|tier\w*|pflanz\w*|animal|plant)\b"),
    ("physics-news",   "⚛️", r"\b(physik|physics|quanten|quantum|teilchen|particle|laser|relativ\w*)\b"),
]

_ARTICLE_RX = re.compile(r"https?://phys\.org/news/[^\s\"'<>]+\.html", re.I)
_TAG_RX = re.compile(r"<[^>]+>")
# Spenden-/Ad-/Redaktions-Boilerplate, die phys.org zwischen den Text mischt
_JUNK_RX = re.compile(
    r"(Ad-Free Account|contribution helps|keep the service running|Sign in with|"
    r"newsletter|Science X|editorial process|fact-checked|peer-reviewed|proofread|"
    r"trusted source|This document is subject to copyright|Use this form|"
    r"Your feedback|Your email address|Your message)",
    re.I,
)


def _strip(s: str) -> str:
    s = _html.unescape(_TAG_RX.sub(" ", s or ""))
    s = s.replace("\\n", " ").replace("\\t", " ").replace("\\/", "/")
    return re.sub(r"\s+", " ", s).strip()


class PhysOrgSkill(Skill):
    skill_id = "physorg"
    description = (
        "Wissenschaftsnews von phys.org: neueste Artikel, nach Thema "
        "(Physik/Weltraum/Nano/Erde/Bio/Chemie), oder Volltext eines Artikels (URL)."
    )

    async def execute(self, query: str) -> str:
        try:
            m = _ARTICLE_RX.search(query)
            if m:
                return await self._article(m.group(0))
            slug, icon = self._detect_category(query)
            return await self._feed(slug, icon)
        except httpx.HTTPError as e:
            return f"[phys.org] Netzwerk-Fehler: {e}"
        except Exception as e:
            return f"[phys.org] Fehler: {e}"

    # ── Mode 2/3: Feed-Liste ───────────────────────────────────────────────────
    @staticmethod
    def _detect_category(query: str) -> tuple[str, str]:
        for slug, icon, pat in _CATEGORIES:
            if re.search(pat, query, re.I):
                return slug, icon
        return "", "🧪"  # leer = Haupt-Feed

    async def _feed(self, slug: str, icon: str) -> str:
        url = f"{_BASE}/rss-feed/{slug}/" if slug else f"{_BASE}/rss-feed/"
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": _UA}, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            xml = r.text

        items = re.findall(r"<item\b[^>]*>(.*?)</item>", xml, re.S | re.I)[:10]
        if not items:
            return f"[phys.org] Keine Artikel im Feed gefunden ({slug or 'haupt'})."

        title_label = slug.replace("-news", "").replace("-", " ").title() if slug else "Latest Science News"
        lines = [f"{icon} **phys.org — {title_label}** (Top {len(items)})\n"]
        for i, it in enumerate(items, 1):
            def grab(tag):
                mm = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", it, re.S | re.I)
                return _strip(mm.group(1)) if mm else ""
            title = grab("title")
            link = grab("link") or grab("guid")
            cat = grab("category")
            pub = grab("pubDate")[:16]
            desc = grab("description")
            snip = (desc[:160] + "…") if len(desc) > 160 else desc
            meta = " · ".join(x for x in (cat, pub) if x)
            lines.append(
                f"**{i}. {title}**\n"
                f"   {meta}\n"
                f"   {snip}\n"
                f"   🔗 {link}"
            )
        return "\n\n".join(lines)

    # ── Mode 1: Artikel-Volltext ────────────────────────────────────────────────
    async def _article(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": _UA}, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            page = r.text

        tm = re.search(r"<meta property=\"og:title\" content=\"(.*?)\"", page)
        title = _strip(tm.group(1)) if tm else "phys.org Artikel"
        dm = re.search(r"<meta property=\"og:description\" content=\"(.*?)\"", page)
        lead = _strip(dm.group(1)) if dm else ""

        # Fließtext: <p>-Absätze im Artikel-Bereich, vor den Quellen-/Zitat-Blöcken
        region = page
        rm = re.search(r'class="article-main"(.*?)(?:Journal information|More information:|Citation:|class="article-banner|<footer)', page, re.S | re.I)
        if rm:
            region = rm.group(1)
        paras = [_strip(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", region, re.S | re.I)]
        paras = [p for p in paras if len(p) > 50 and not _JUNK_RX.search(p)]
        body = "\n\n".join(paras)[:3500]

        out = [f"📄 **{title}**", f"🔗 {url}"]
        if lead:
            out += ["", f"*{lead}*"]
        if body:
            out += ["", body]
        elif not lead:
            out += ["", "(Kein Fließtext extrahierbar — Seitenstruktur abweichend.)"]
        return "\n".join(out)
