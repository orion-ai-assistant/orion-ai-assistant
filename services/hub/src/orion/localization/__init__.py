"""Translations for API data shared by all clients."""

import json
import re
from functools import lru_cache
from pathlib import Path

_LOCALES = Path(__file__).parent / "locales"


@lru_cache(maxsize=32)
def _messages(language: str) -> dict[str, str]:
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", language):
        return {}
    path = _LOCALES / f"{language}.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def language_of(preference: str | None) -> str:
    """Select a supported language from an Accept-Language value or locale tag."""
    for part in (preference or "").split(","):
        tag = part.split(";", 1)[0].strip().lower().replace("_", "-")
        if not tag:
            continue
        if _messages(tag):
            return tag
        base = tag.split("-", 1)[0]
        if _messages(base):
            return base
    return "en"


def translate(key: str, preference: str | None = None, fallback: str | None = None) -> str:
    language = language_of(preference)
    return _messages(language).get(key, _messages("en").get(key, fallback or key))
