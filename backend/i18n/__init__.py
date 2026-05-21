"""
backend/i18n — Einfaches dict-basiertes Übersetzungssystem.
Default: English. Unterstützt: en, de.
"""
from __future__ import annotations

from backend.i18n import en, de

_LOCALES: dict[str, dict[str, str]] = {
    "en": en.STRINGS,
    "de": de.STRINGS,
}

_DEFAULT = "en"


def t(key: str, locale: str = _DEFAULT, **kwargs: object) -> str:
    """Gibt die übersetzte Zeichenkette zurück. Fällt auf Englisch zurück."""
    strings = _LOCALES.get(locale, _LOCALES[_DEFAULT])
    text = strings.get(key) or _LOCALES[_DEFAULT].get(key) or key
    return text.format(**kwargs) if kwargs else text


def locale_from_header(accept_language: str | None) -> str:
    """Extrahiert Locale aus Accept-Language Header. Default: 'en'."""
    if not accept_language:
        return _DEFAULT
    lang = accept_language.split(",")[0].split(";")[0].strip().lower()
    if lang.startswith("de"):
        return "de"
    return _DEFAULT
