"""Translations for what users see: the printed page, notifications and command output.

Log lines and error details stay in English. Each language is a flat JSON catalog in
``locales/``; en-US is the reference, and every catalog has the same keys (a test checks it).
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import cache
from importlib.resources import files
from typing import Any

LOCALES = ("en-US", "pt-BR")
DEFAULT_LOCALE = "en-US"


@cache
def catalog(locale: str) -> dict[str, str]:
    path = files("dont_dry") / "locales" / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(locale: str) -> str | None:
    """The supported locale that ``locale`` names (any case, ``-`` or ``_``), or None."""
    wanted = locale.strip().replace("_", "-").lower()
    return next((known for known in LOCALES if known.lower() == wanted), None)


class Translator:
    def __init__(self, locale: str = DEFAULT_LOCALE):
        self.locale = locale
        self._catalog = catalog(locale)

    def __call__(self, key: str, **values: Any) -> str:
        template = self._catalog.get(key)
        if template is None:  # a new key not translated yet: English beats a crash
            template = catalog(DEFAULT_LOCALE)[key]
        return template.format(**values)

    def datetime(self, when: datetime, key: str = "format.date_time") -> str:
        """``when`` in this locale's style, from a ``format.*`` template (no OS locale needed)."""
        return self(
            key,
            y=when.year, mo=f"{when.month:02}", dd=f"{when.day:02}", d=when.day,
            HH=f"{when.hour:02}", h12=when.hour % 12 or 12, mm=f"{when.minute:02}",
            ampm=self("format.am" if when.hour < 12 else "format.pm"),
            mon=self("format.months").split()[when.month - 1],
            wd=self("format.weekdays").split()[when.weekday()],
            tz=when.tzname() or "",
        ).strip()
