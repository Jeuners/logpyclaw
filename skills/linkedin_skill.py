"""
skills/linkedin_skill.py — LinkedIn Post & Scheduling via LinkedIn API v2
Auth: OAuth 2.0 Access Token (einmalig in Provider Settings eintragen)
Endpoints: /v2/ugcPosts (posten), /v2/me (eigenes Profil)
Scheduling: intern via AgentClaw Watchdog-System (geplante Posts in scheduled_linkedin.json)
"""
import os
import re
import json
import uuid
from datetime import datetime, timedelta

import requests

LINKEDIN_API = "https://api.linkedin.com/v2"
LINKEDIN_REST = "https://api.linkedin.com/rest"  # neue Posts API (ab 2023)

LI_TRIGGERS = re.compile(
    r"\b(linkedin|linked.in)\b.*\b(post|artikel|poste|publish|veröffentlich|schedule|plan|erstell|schreib)\b|"
    r"\b(poste|publish|veröffentlich)\b.*\b(linkedin|linked.in)\b|"
    r"\b(plane?|schedule)\b.*\b(linkedin|post|artikel)\b|"
    r"\blinkedin\s+post\b|"
    r"\blinkedin\s+artikel\b",
    re.IGNORECASE,
)

# Pfad für geplante Posts
_SCHEDULE_FILE = os.path.expanduser("~/Downloads/AgentClaw/linkedin_scheduled.json")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_token(providers: dict) -> str:
    return providers.get("linkedin", {}).get("access_token", "")


def _get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202503",  # neue Posts API braucht Versions-Header
    }


# ── Profile ───────────────────────────────────────────────────────────────────

def _get_member_id(token: str, providers: dict) -> str | None:
    """
    Gibt die LinkedIn Member ID zurück.
    1. Aus providers.json (manuell eingetragen oder gecacht)
    2. Via Token-Introspection (liefert leider keine Member-ID)
    3. Fallback: None → Nutzer muss ID manuell eintragen
    """
    # Gecachte ID aus providers
    stored = providers.get("linkedin", {}).get("member_id", "")
    if stored:
        return stored

    # LinkedIn Default Tier erlaubt kein /v2/me mehr —
    # Member ID muss einmalig manuell in Provider Settings eingetragen werden.
    # Zu finden unter: linkedin.com/in/<dein-profil> → rechte Maustaste → "Seite inspizieren"
    # oder: linkedin.com/in/<profil> → URL nach Klick auf "Kontakt" enthält die ID
    return None


# ── Post ──────────────────────────────────────────────────────────────────────

def _upload_image(token: str, author_urn: str, image_b64: str) -> str | None:
    """Upload image to LinkedIn, return image URN or None on failure."""
    import base64
    try:
        # Step 1: Initialize upload
        init_resp = requests.post(
            f"{LINKEDIN_REST}/images?action=initializeUpload",
            headers=_get_headers(token),
            json={"initializeUploadRequest": {"owner": author_urn}},
            timeout=15,
        )
        if not init_resp.ok:
            print(f"[LinkedIn] initializeUpload failed: {init_resp.status_code} {init_resp.text[:200]}", flush=True)
            return None
        data = init_resp.json().get("value", {})
        upload_url = data.get("uploadUrl")
        image_urn = data.get("image")
        if not upload_url or not image_urn:
            print(f"[LinkedIn] No uploadUrl or image URN in response: {data}", flush=True)
            return None

        # Step 2: Upload binary
        if "," in image_b64:
            raw_b64 = image_b64.split(",", 1)[1]
        else:
            raw_b64 = image_b64
        img_bytes = base64.b64decode(raw_b64)
        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        }
        up_resp = requests.put(upload_url, headers=upload_headers, data=img_bytes, timeout=60)
        if not up_resp.ok:
            print(f"[LinkedIn] Image upload PUT failed: {up_resp.status_code}", flush=True)
            return None
        print(f"[LinkedIn] Image uploaded: {image_urn}", flush=True)
        return image_urn
    except Exception as e:
        print(f"[LinkedIn] _upload_image error: {e}", flush=True)
        return None


def _create_post(token: str, author_urn: str, text: str, visibility: str = "PUBLIC",
                 draft: bool = False, image_urn: str = None) -> dict:
    """Erstellt einen LinkedIn-Post via neue Posts API (2023+)."""
    payload = {
        "author": author_urn,
        "commentary": text,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "DRAFT" if draft else "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if image_urn:
        payload["content"] = {"media": {"id": image_urn}}
    try:
        r = requests.post(
            f"{LINKEDIN_REST}/posts",
            headers=_get_headers(token),
            json=payload,
            timeout=15,
        )
        if r.ok:
            post_id = r.headers.get("x-restli-id") or ""
            return {"ok": True, "post_id": post_id}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Scheduling ────────────────────────────────────────────────────────────────

def _load_scheduled() -> list:
    try:
        with open(_SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_scheduled(posts: list):
    os.makedirs(os.path.dirname(_SCHEDULE_FILE), exist_ok=True)
    with open(_SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)


def schedule_post(text: str, scheduled_at: str, providers: dict) -> str:
    """Plant einen Post für einen späteren Zeitpunkt."""
    posts = _load_scheduled()
    entry = {
        "id": str(uuid.uuid4()),
        "text": text,
        "scheduled_at": scheduled_at,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    }
    posts.append(entry)
    _save_scheduled(posts)
    return (
        f"📅 **LinkedIn-Post geplant**\n\n"
        f"**Zeitpunkt:** {scheduled_at}\n"
        f"**ID:** `{entry['id'][:8]}`\n\n"
        f"**Text:**\n{text[:300]}{'...' if len(text) > 300 else ''}"
    )


def list_scheduled() -> str:
    """Zeigt alle geplanten Posts."""
    posts = [p for p in _load_scheduled() if p.get("status") == "pending"]
    if not posts:
        return "📭 Keine geplanten LinkedIn-Posts."
    lines = []
    for p in posts:
        lines.append(
            f"- **{p['scheduled_at'][:16]}** · `{p['id'][:8]}` · {p['text'][:80]}..."
        )
    return f"📅 **Geplante LinkedIn-Posts ({len(posts)}):**\n\n" + "\n".join(lines)


def process_scheduled_posts(providers: dict) -> list:
    """Wird vom Scheduler aufgerufen — veröffentlicht fällige Posts. Gibt Liste veröffentlichter Post-IDs zurück."""
    token = _get_token(providers)
    if not token:
        return []

    posts = _load_scheduled()
    now = datetime.now().isoformat()
    published = []

    member_id = _get_member_id(token, providers)
    if not member_id:
        return []

    for p in posts:
        if p.get("status") != "pending":
            continue
        if p.get("scheduled_at", "9999") > now:
            continue
        # Fällig — veröffentlichen
        author_urn = f"urn:li:person:{member_id}"
        print(f"[LinkedIn] Geplanter Post fällig: {p['id'][:8]}", flush=True)
        result = _create_post(token, author_urn, p["text"])
        if result["ok"]:
            p["status"] = "published"
            p["published_at"] = now
            p["post_id"] = result.get("post_id", "")
            published.append(p["id"])
            print(f"[LinkedIn] Geplanter Post veröffentlicht: {p['id'][:8]}", flush=True)
        else:
            p["status"] = "failed"
            p["error"] = result.get("error", "")
            print(f"[LinkedIn] Fehler bei geplantem Post {p['id'][:8]}: {p['error']}", flush=True)

    _save_scheduled(posts)
    return published


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_schedule_time(message: str) -> str | None:
    """
    Erkennt Zeitangaben in der Message:
    - "morgen um 9 Uhr" → ISO
    - "Freitag 14:00" → ISO
    - "in 2 Stunden" → ISO
    - "2026-04-07 10:00" → direkt
    """
    now = datetime.now()

    # ISO direkt: 2026-04-07 10:00
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})", message)
    if m:
        return f"{m.group(1)}T{m.group(2)}:00"

    # "in N Stunden/Minuten"
    m = re.search(r"in\s+(\d+)\s+(stunden?|minuten?|hours?|minutes?)", message, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if "stunde" in unit or "hour" in unit:
            return (now + timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M:00")
        else:
            return (now + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:00")

    # "morgen um HH:MM" oder "morgen HH:MM"
    m = re.search(r"morgen\s+(?:um\s+)?(\d{1,2})[:\.]?(\d{0,2})", message, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=h, minute=mi, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:00")

    # Wochentag: "Freitag 14:00" / "Friday at 2pm"
    days_de = {"montag":0,"dienstag":1,"mittwoch":2,"donnerstag":3,"freitag":4,"samstag":5,"sonntag":6}
    days_en = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
    days = {**days_de, **days_en}
    m = re.search(
        r"(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"\s+(?:um\s+|at\s+)?(\d{1,2})[:\.]?(\d{0,2})",
        message, re.IGNORECASE,
    )
    if m:
        target_wd = days[m.group(1).lower()]
        h = int(m.group(2))
        mi = int(m.group(3)) if m.group(3) else 0
        days_ahead = (target_wd - now.weekday()) % 7 or 7
        target = now + timedelta(days=days_ahead)
        return target.replace(hour=h, minute=mi, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:00")

    return None


def _extract_post_text(message: str) -> str:
    """Extrahiert den eigentlichen Post-Text aus der Message."""
    # Alles nach "Post:" / "Text:" / Anführungszeichen
    m = re.search(r'(?:post|text|inhalt|content)\s*[:]\s*["\']?(.+)', message, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip().strip('"\'')

    # Anführungszeichen
    m = re.search(r'["\u201c\u201e](.+?)["\u201d\u201c]', message, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Alles nach "linkedin post" / "poste"
    m = re.search(r'(?:linkedin\s+post|poste\s+auf\s+linkedin|veröffentliche)[:\s]+(.+)',
                  message, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()

    # Fallback: gesamte Message (LLM hat Text direkt übergeben)
    return message.strip()


# ── Hauptfunktion ─────────────────────────────────────────────────────────────

def run_linkedin(message: str, providers: dict, image_b64: str = None) -> str:
    """Hauptfunktion — wird aus process_task() aufgerufen."""
    token = _get_token(providers)
    if not token:
        return (
            "❌ **LinkedIn nicht konfiguriert.**\n\n"
            "Bitte Access Token in ⚙️ Provider Settings eintragen:\n"
            "1. LinkedIn Developer App → OAuth 2.0 tools → Token generieren\n"
            "2. Scopes: `w_member_social`, `r_profile_basicinfo`\n"
            "3. Token + Member ID in Provider Settings eintragen"
        )

    msg_lower = message.lower()

    # Liste geplante Posts
    if re.search(r"\b(liste|zeig|show|list)\b.*\b(geplant|scheduled|plan)\b", msg_lower):
        return list_scheduled()

    # Post-Text extrahieren
    post_text = _extract_post_text(message)
    if not post_text or len(post_text) < 5:
        return "❓ Kein Post-Text erkannt. Beispiel: `Poste auf LinkedIn: \"Mein toller Post...\"`"

    # Scheduling?
    scheduled_at = _parse_schedule_time(message)
    schedule_keywords = re.search(
        r"\b(morgen|später|schedule|plan|freitag|montag|dienstag|mittwoch|donnerstag|samstag|sonntag|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|in\s+\d+\s+stunden?)\b",
        message, re.IGNORECASE,
    )
    if scheduled_at and schedule_keywords:
        return schedule_post(post_text, scheduled_at, providers)

    # Member ID ermitteln
    member_id = _get_member_id(token, providers)
    if not member_id:
        return (
            "❌ **LinkedIn Member ID fehlt.**\n\n"
            "LinkedIn Default Tier Apps dürfen Profil-Infos nicht automatisch abrufen.\n"
            "Bitte einmalig die Member ID in ⚙️ Provider Settings → LinkedIn eintragen:\n\n"
            "**So findest du deine Member ID:**\n"
            "1. Gehe auf dein LinkedIn-Profil\n"
            "2. Klick auf \"Kontaktinfo\"\n"
            "3. Die URL enthält: `linkedin.com/in/...` — **nicht** die ID\n"
            "4. Alternativ: Browser DevTools → Network → beliebige API-Anfrage → `memberId` suchen\n"
            "5. Oder: linkedin.com/in/<dein-profil>?overlay=contact-info → URL-Parameter `profileId`"
        )

    is_draft = bool(re.search(r"\b(entwurf|draft|als\s+entwurf|save\s+as\s+draft)\b", message, re.IGNORECASE))
    author_urn = f"urn:li:person:{member_id}"

    # Upload image if provided
    image_urn = None
    if image_b64:
        print("[LinkedIn] Uploading attached image…", flush=True)
        image_urn = _upload_image(token, author_urn, image_b64)
        if not image_urn:
            print("[LinkedIn] Image upload failed, posting without image", flush=True)

    result = _create_post(token, author_urn, post_text, draft=is_draft, image_urn=image_urn)

    if result["ok"]:
        img_note = " 🖼️ with image" if image_urn else ""
        if is_draft:
            return (
                f"📝 **LinkedIn draft saved{img_note}!**\n\n"
                f"**Post-ID:** `{result.get('post_id', '')}`\n\n"
                f"**Text:**\n{post_text[:500]}{'...' if len(post_text) > 500 else ''}"
            )
        return (
            f"✅ **LinkedIn post published{img_note}!**\n\n"
            f"**Post-ID:** `{result.get('post_id', '')}`\n\n"
            f"**Text:**\n{post_text[:500]}{'...' if len(post_text) > 500 else ''}"
        )
    # Hinweis bei Author-Fehler
    if "author" in result.get("error", "").lower():
        return (
            f"❌ LinkedIn-Fehler: Member ID stimmt nicht.\n"
            f"Aktuelle ID: `{member_id}`\n"
            f"Bitte in ⚙️ Provider Settings → LinkedIn korrigieren."
        )
    return f"❌ LinkedIn-Fehler: {result['error']}"


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult
from storage.providers import load_providers


class LinkedInSkill(BaseSkill):
    id = "linkedin"
    name = "LinkedIn"
    icon = "business"
    description = "Posts and schedules content on LinkedIn."
    triggers = [
        r"\b(linkedin|linked-in)\b",
        r"\b(post\w*|share|teile|veröffentlich\w*)\b.{0,30}\b(linkedin|post|beitrag)\b",
    ]
    requires = ["linkedin"]

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        providers = load_providers()
        image_b64 = context.get("image_b64")
        try:
            result = run_linkedin(message, providers, image_b64=image_b64)
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
