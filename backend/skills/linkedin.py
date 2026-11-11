"""
backend/skills/linkedin.py — LinkedIn Post-Generator + Publisher.

Modi:
  - generate:  Erstellt einen LinkedIn-Post via Ollama LLM
  - post:      Postet direkt auf LinkedIn via API (benötigt LINKEDIN_ACCESS_TOKEN)
  - trending:  Generiert Post aus aktuellem RSS-Top-Thema

Trigger-Beispiele:
  "linkedin post über KI-Agenten"
  "erstelle linkedin beitrag zu claude code"
  "poste auf linkedin: ..."
  "linkedin trending"
"""
from __future__ import annotations

import re

import httpx

from backend.core.logging import get_logger
from backend.skills import Skill, SkillConfigField

log = get_logger("logpyclaw.linkedin")

_SYSTEM_PROMPT = """Du bist ein LinkedIn-Content-Experte.
Erstelle einen professionellen, authentischen LinkedIn-Post auf Deutsch.

Format:
- Zeile 1: Starker Hook (1 Satz, provokant oder überraschend, endet mit 📌 oder ähnlichem Emoji)
- Leerzeile
- 3-4 kurze Absätze ODER 4-6 Bullet-Points (▸)
- Leerzeile
- Call-to-Action (Frage an die Leser)
- Leerzeile
- 4-6 relevante Hashtags

Stil: professionell aber persönlich, keine Marketing-Floskeln, konkret und meinungsstark.
Länge: 150-250 Wörter.
Antworte NUR mit dem Post-Text, kein Kommentar davor/danach."""

_POST_RX      = re.compile(r"\b(poste|post|veröffentliche|publish)\b.*?linkedin", re.I)
_GENERATE_RX  = re.compile(r"\b(erstell\w*|schreib\w*|generi\w*|mach\w*)\b.*?linkedin", re.I)
_TRENDING_RX  = re.compile(r"\b(trending|aktuell|news|rss)\b", re.I)
_TOPIC_RX     = re.compile(r"(?:über|zu|about|on|topic|thema)[:\s]+(.+?)(?:\.|$)", re.I)
_EDIT_RX      = re.compile(r"\b(edit|editier\w*|ändere?|update|bearbeit\w*)\b.*?linkedin", re.I)
_DELETE_RX    = re.compile(r"\b(lösch\w*|delete|entfern\w*|remove)\b.*?linkedin", re.I)
_SHARE_ID_RX  = re.compile(r"(?:urn:li:share:|share:|post[:\s]+)(\d{10,})", re.I)


class LinkedInSkill(Skill):
    skill_id    = "linkedin"
    description = "Erstellt und postet LinkedIn-Beiträge via Ollama + LinkedIn API."
    CONFIG_FIELDS = (
        SkillConfigField("access_token",  env="LINKEDIN_ACCESS_TOKEN",  secret=True),
        SkillConfigField("client_id",     env="LINKEDIN_CLIENT_ID"),
        SkillConfigField("client_secret", env="LINKEDIN_CLIENT_SECRET", secret=True),
        SkillConfigField("person_urn",    env="LINKEDIN_PERSON_URN"),
        SkillConfigField("ollama_url",    env="OLLAMA_URL",    default="http://localhost:11434"),
        SkillConfigField("ollama_model",  env="OLLAMA_MODEL",  default="gemma4:e4b"),
    )

    async def execute(self, query: str) -> str:
        # Delete: "lösche linkedin post 7464305182709903360"
        if _DELETE_RX.search(query):
            return await self._handle_delete(query)

        # Edit: "editiere linkedin post 7464305182709903360: neuer text"
        if _EDIT_RX.search(query):
            return await self._handle_edit(query)

        # Trending → Top-RSS-Thema nehmen
        if _TRENDING_RX.search(query):
            topic = await self._top_rss_topic()
            return await self._generate_and_maybe_post(topic, query)

        # Direkter Post-Text nach "poste auf linkedin: <text>"
        m = re.search(r"(?:poste|publish|post).*?linkedin[:\s]+(.+)", query, re.I | re.DOTALL)
        if m:
            text = m.group(1).strip()
            if _POST_RX.search(query) and self.config.get("access_token"):
                return await self._post_to_linkedin(text)
            return f"📋 **Post bereit:**\n\n{text}\n\n_(Kein LINKEDIN_ACCESS_TOKEN → nicht gepostet)_"

        # Topic extrahieren
        topic = self._extract_topic(query)
        if not topic:
            return (
                "[LinkedIn] Bitte ein Thema angeben.\n"
                "Beispiele:\n"
                "- `linkedin post über KI-Agenten`\n"
                "- `erstelle linkedin beitrag zu claude code`\n"
                "- `linkedin trending` — nutzt aktuellen RSS-Top-Hit\n"
                "- `editiere linkedin post 7464305182709903360: neuer text`"
            )

        return await self._generate_and_maybe_post(topic, query)

    async def _handle_edit(self, query: str) -> str:
        """Editiert einen bestehenden LinkedIn-Post. Query enthält Share-ID und neuen Text."""
        token = self.config.get("access_token", "")
        if not token:
            return "❌ LINKEDIN_ACCESS_TOKEN nicht gesetzt."

        m_id = _SHARE_ID_RX.search(query)
        if not m_id:
            return (
                "❌ Keine Post-ID gefunden.\n"
                "Format: `editiere linkedin post <ID>: <neuer text>`\n"
                "Die ID steht in der Post-URL: `linkedin.com/feed/update/urn:li:share:**ID**`"
            )
        share_id = m_id.group(1)

        # Neuen Text nach dem Doppelpunkt hinter der ID
        m_text = re.search(r"(?:post\s+" + share_id + r"|" + share_id + r")[:\s]+(.+)", query, re.I | re.DOTALL)
        if not m_text:
            return f"❌ Kein neuer Text nach der Post-ID `{share_id}` gefunden."
        new_text = m_text.group(1).strip()

        return await self._edit_post(token, share_id, new_text)

    async def _handle_delete(self, query: str) -> str:
        """Löscht einen LinkedIn-Post anhand seiner Share-ID."""
        token = self.config.get("access_token", "")
        if not token:
            return "❌ LINKEDIN_ACCESS_TOKEN nicht gesetzt."

        m_id = _SHARE_ID_RX.search(query)
        if not m_id:
            return (
                "❌ Keine Post-ID gefunden.\n"
                "Format: `lösche linkedin post <ID>`\n"
                "Die ID steht in der Post-URL: `linkedin.com/feed/update/urn:li:share:**ID**`"
            )
        share_id = m_id.group(1)
        return await self._delete_post(token, share_id)

    # ── Core ─────────────────────────────────────────────────────────────────

    async def _generate_and_maybe_post(self, topic: str, query: str) -> str:
        post = await self._generate_post(topic)

        # Direkt posten wenn explizit gewünscht + Token vorhanden
        if _POST_RX.search(query) and self.config.get("access_token"):
            result = await self._post_to_linkedin(post)
            return f"📝 **Generierter Post:**\n\n{post}\n\n---\n{result}"

        import urllib.parse
        li_url = "https://www.linkedin.com/feed/?shareActive=true&text=" + urllib.parse.quote(post)
        return (
            f"📝 **LinkedIn-Post zu: {topic}**\n\n{post}\n\n"
            f"---\n🔗 [Direkt auf LinkedIn öffnen]({li_url})"
        )

    async def _generate_post(self, topic: str) -> str:
        user_msg = f"Erstelle einen LinkedIn-Post zum Thema: {topic}"
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.config['ollama_url']}/api/chat",
                json={
                    "model":    self.config["ollama_model"],
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                    "stream":  False,
                    "options": {"temperature": 0.75},
                },
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()

    async def _post_to_linkedin(self, text: str) -> str:
        token = self.config.get("access_token", "")
        if not token:
            return "❌ LINKEDIN_ACCESS_TOKEN nicht gesetzt."

        is_session_cookie = token.startswith("WPL_AP")

        if is_session_cookie:
            return await self._post_via_cookie(token, text)

        # Offizieller OAuth-Weg (AQ... Token)
        urn = (
            self.config.get("person_urn", "")
            or await self._get_person_urn_introspect(token)
            or await self._get_person_urn(token)
        )
        if not urn:
            return (
                "❌ **LinkedIn Member-URN nicht ermittelbar.**\n\n"
                "Setze eine der folgenden Optionen:\n"
                "1. `LINKEDIN_CLIENT_SECRET=...` in `.env` → automatische Ermittlung via Token-Introspection\n"
                "2. `LINKEDIN_PERSON_URN=<zahl>` in `.env` → deine numerische LinkedIn Member-ID\n\n"
                "Die Member-ID findest du im Browser unter F12 → Network → `voyager/api/me` → "
                "`entityUrn: \"urn:li:member:<ZAHL>\"`"
            )
        return await self._post_via_oauth(token, urn, text)

    async def _post_via_cookie(self, cookie: str, text: str) -> str:
        """Postet via LinkedIn Web-Session (WPL_AP1 Cookie)."""
        # CSRF-Token aus Cookie ableiten
        csrf = "ajax:0000000000000000"
        headers = {
            "cookie":           f"li_at={cookie}; JSESSIONID=\"{csrf}\"",
            "csrf-token":       csrf,
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang":        "de_DE",
            "x-li-track":       '{"clientVersion":"1.13.15300"}',
            "content-type":     "application/json",
            "accept":           "application/vnd.linkedin.normalized+json+2.1",
            "user-agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "origin":           "https://www.linkedin.com",
            "referer":          "https://www.linkedin.com/feed/",
        }
        # Zuerst eigene URN holen
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            me = await client.get("https://www.linkedin.com/voyager/api/me")
            if not me.is_success:
                return f"❌ Session abgelaufen oder ungültig (HTTP {me.status_code}). Token erneuern."
            data = me.json()
            mini = data.get("included", [{}])[0]
            urn = mini.get("entityUrn", "").replace("urn:li:fs_miniProfile:", "")
            if not urn:
                return "❌ Eigene LinkedIn-URN nicht ermittelbar. Token möglicherweise abgelaufen."

        # Post abschicken
        payload = {
            "visibleToGuest": True,
            "externalAudienceProviders": [],
            "commentaryV2": {"text": text, "attributesV2": []},
            "origin": "FEED",
            "allowedCommentersScope": "ALL",
            "postState": "PUBLISHED",
            "author": f"urn:li:member:{urn}",
            "media": [],
        }
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            r = await client.post(
                "https://www.linkedin.com/voyager/api/contentcreation/normShares",
                json=payload,
            )
            if r.is_success:
                return "✅ **Auf LinkedIn gepostet!** (via Web-Session)"
            return f"❌ Post fehlgeschlagen (HTTP {r.status_code}): {r.text[:300]}"

    async def _post_via_oauth(self, token: str, urn: str, text: str) -> str:
        """Postet via LinkedIn /rest/posts API (OAuth Bearer Token)."""
        person_id = await self._resolve_person_id(token, urn)
        if not person_id:
            return f"❌ LinkedIn Person-ID konnte nicht aufgelöst werden (URN: {urn})."

        payload = {
            "author":         f"urn:li:person:{person_id}",
            "lifecycleState": "PUBLISHED",
            "visibility":     "PUBLIC",
            "commentary":     text,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.linkedin.com/rest/posts",
                json=payload,
                headers={
                    "Authorization":  f"Bearer {token}",
                    "Content-Type":   "application/json",
                    "LinkedIn-Version": "202502",
                },
            )
            if r.status_code in (200, 201):
                share_urn = r.headers.get("x-linkedin-id", "")
                share_id  = share_urn.split(":")[-1] if share_urn else ""
                edit_hint = f"\n📎 Post-ID: `{share_id}` — zum Editieren: `editiere linkedin post {share_id}: <neuer text>`" if share_id else ""
                return f"✅ **Auf LinkedIn gepostet!**{edit_hint}"
            return f"❌ LinkedIn API Fehler {r.status_code}: {r.text[:300]}"

    async def _edit_post(self, token: str, share_id: str, new_text: str) -> str:
        """Editiert einen bestehenden LinkedIn-Post via PATCH /rest/posts/{urn}."""
        import urllib.parse
        encoded_urn = urllib.parse.quote(f"urn:li:share:{share_id}", safe="")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.linkedin.com/rest/posts/{encoded_urn}",
                json={"patch": {"$set": {"commentary": new_text}}},
                headers={
                    "Authorization":    f"Bearer {token}",
                    "Content-Type":     "application/json",
                    "LinkedIn-Version": "202502",
                    "X-RestLi-Method":  "PARTIAL_UPDATE",
                },
            )
            if r.status_code in (200, 204):
                return f"✅ **Post editiert!**\n\n{new_text}"
            return f"❌ Edit fehlgeschlagen (HTTP {r.status_code}): {r.text[:300]}"

    async def _delete_post(self, token: str, share_id: str) -> str:
        """Löscht einen LinkedIn-Post via DELETE /rest/posts/{urn}."""
        import urllib.parse
        encoded_urn = urllib.parse.quote(f"urn:li:share:{share_id}", safe="")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.delete(
                f"https://api.linkedin.com/rest/posts/{encoded_urn}",
                headers={
                    "Authorization":    f"Bearer {token}",
                    "LinkedIn-Version": "202502",
                },
            )
            if r.status_code in (200, 204):
                return f"✅ **Post `{share_id}` gelöscht.**"
            return f"❌ Löschen fehlgeschlagen (HTTP {r.status_code}): {r.text[:200]}"

    async def _resolve_person_id(self, token: str, urn: str) -> str | None:
        """
        Gibt die alphanumerische LinkedIn Person-ID zurück.
        Falls urn bereits alphanumerisch ist, direkt zurückgeben.
        Falls numerisch (member ID), Bootstrap via /rest/posts 422 Fehler.
        """
        if not urn.isdigit():
            return urn

        # Numerische Member-ID → Bootstrap: POST mit urn:li:member:{id} → 422 enthält urn:li:person:{id}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.linkedin.com/rest/posts",
                json={
                    "author": f"urn:li:member:{urn}",
                    "lifecycleState": "PUBLISHED",
                    "visibility": "PUBLIC",
                    "commentary": ".",
                    "distribution": {
                        "feedDistribution": "MAIN_FEED",
                        "targetEntities": [],
                        "thirdPartyDistributionChannels": [],
                    },
                },
                headers={
                    "Authorization":  f"Bearer {token}",
                    "Content-Type":   "application/json",
                    "LinkedIn-Version": "202502",
                },
            )
            if r.status_code == 422:
                import re as _re
                m = _re.search(r"urn:li:person:([A-Za-z0-9_-]+)", r.text)
                if m:
                    return m.group(1)
        return None

    async def _get_person_urn_introspect(self, token: str) -> str | None:
        """Holt Member-ID via OAuth2 Token-Introspection (braucht client_id + client_secret)."""
        client_id = self.config.get("client_id", "")
        client_secret = self.config.get("client_secret", "")
        if not client_id or not client_secret:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    "https://www.linkedin.com/oauth/v2/introspectToken",
                    data={"token": token, "client_id": client_id, "client_secret": client_secret},
                )
                if r.ok:
                    data = r.json()
                    # sub enthält die numerische Member-ID
                    sub = data.get("sub", "") or data.get("auth_type_member_id", "")
                    if sub:
                        return str(sub)
        except Exception:
            log.exception("LinkedIn Token-Introspection fehlgeschlagen")
        return None

    async def _get_person_urn(self, token: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.linkedin.com/v2/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.ok:
                    return r.json().get("id")
        except Exception:
            log.exception("LinkedIn /v2/me URN-Abruf fehlgeschlagen")
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _top_rss_topic() -> str:
        try:
            from backend.services.rss import get_entries
            entries = get_entries(limit=5)
            if entries:
                return entries[0].title
        except Exception:
            log.exception("RSS-Top-Thema konnte nicht ermittelt werden — Fallback-Thema")
        return "KI und Automatisierung"

    @staticmethod
    def _extract_topic(query: str) -> str | None:
        m = _TOPIC_RX.search(query)
        if m:
            return m.group(1).strip()
        # Fallback: alles nach "linkedin [post|beitrag|...]"
        m = re.search(
            r"linkedin\s+(?:post|beitrag|artikel|content|text)?\s*(?:über|zu|about|on|:)?\s*(.+)",
            query, re.I,
        )
        if m:
            topic = m.group(1).strip()
            if len(topic) > 2:
                return topic
        return None
