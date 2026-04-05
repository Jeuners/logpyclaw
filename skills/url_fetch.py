"""URL fetching skill with SSRF protection."""
import re
import ipaddress
import socket
from html.parser import HTMLParser

import requests


def is_safe_url(url: str) -> bool:
    """
    SSRF protection: block requests to private/internal networks.
    Allows only public routable IPs and HTTPS/HTTP to the open internet.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # Block obvious internal hostnames
        blocked_hosts = {"localhost", "metadata.google.internal"}
        if hostname.lower() in blocked_hosts:
            return False
        # Resolve to IP and check if private/loopback/link-local
        ip = ipaddress.ip_address(socket.gethostbyname(hostname))
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
        return True
    except Exception:
        return False


def fetch_url_text(url: str, max_chars: int = 4000) -> str:
    """Fetch a URL and return plain text content."""
    if not is_safe_url(url):
        return f"[Blocked: '{url}' targets a private or internal network address]"
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "footer", "head"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "footer", "head"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    t = data.strip()
                    if t:
                        self.parts.append(t)

        p = TextExtractor()
        p.feed(resp.text)
        text = " ".join(p.parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[Fehler beim Laden: {e}]"
