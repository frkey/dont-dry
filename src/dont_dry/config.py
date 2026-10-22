"""Configuration from environment variables.

Every value is checked when dont-dry starts, so a typo stops the scheduler right away instead
of on the day of the next print.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import i18n

FORMATS = ("auto", "application/pdf", "image/pwg-raster", "image/jpeg")
PAGE_SIZES = ("a4", "letter")
NOTIFY_ON = ("failure", "always", "never")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
PRINTER_SCHEMES = ("ipp", "ipps", "http", "https")

DEFAULT_SCHEDULE = "0 9 * * 1,4"
URI_EXAMPLE = "ipp://192.168.1.50/ipp/print"


class ConfigError(ValueError):
    pass


def _str(name: str, default: str = "") -> str:
    """The variable without surrounding spaces; unset and empty both mean the default."""
    return os.environ.get(name, "").strip() or default


def _choice(name: str, default: str, choices: tuple[str, ...], *, upper: bool = False) -> str:
    value = _str(name, default)
    value = value.upper() if upper else value.lower()
    if value not in choices:
        raise ConfigError(f"{name} must be one of {', '.join(choices)}, got {value!r}")
    return value


def _bool(name: str, default: bool) -> bool:
    value = _str(name).lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {value!r}")


def _int(name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = _str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigError(f"{name} must be {bounds}, got {value}")
    return value


def _url(name: str, schemes: tuple[str, ...], example: str) -> str:
    value = _str(name)
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme.lower() not in schemes or not parts.hostname:
        raise ConfigError(f"{name} must be a {' or '.join(s + '://' for s in schemes)} URL "
                          f"(example: {example}), got {value!r}")
    try:
        parts.port  # noqa: B018 - raises ValueError for a port that is not a number
    except ValueError:
        raise ConfigError(f"{name} has an invalid port: {value!r}") from None
    return value


def _locale() -> str:
    # Not LANGUAGE: gettext uses that one, and desktops set it to lists such as "en_US:en"
    value = _str("LOCALE", i18n.DEFAULT_LOCALE)
    locale = i18n.normalize(value)
    if locale is None:
        raise ConfigError(f"LOCALE must be one of {', '.join(i18n.LOCALES)}, got {value!r}")
    return locale


def _default_state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local",
                                                            "state")
    return os.path.join(base, "dont-dry")


@dataclass(frozen=True)
class Config:
    printer_uri: str
    schedule: str
    timezone: ZoneInfo
    document_format: str
    page_size: str
    page_title: str
    custom_page: str
    run_on_start: bool
    retries: int
    retry_delay: int
    job_timeout: int
    verify_tls: bool
    low_ink_threshold: int
    notify_url: str
    notify_on: str
    notify_token: str
    locale: str
    log_level: str
    state_dir: str

    @classmethod
    def from_env(cls) -> Config:
        tz_name = _str("TZ", "UTC")
        try:
            timezone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"unknown time zone TZ={tz_name!r} "
                              "(use a name such as America/Sao_Paulo or Europe/Berlin)") from None
        locale = _locale()

        return cls(
            printer_uri=_url("PRINTER_URI", PRINTER_SCHEMES, URI_EXAMPLE),
            schedule=_str("SCHEDULE", DEFAULT_SCHEDULE),
            timezone=timezone,
            document_format=_choice("DOCUMENT_FORMAT", "auto", FORMATS),
            page_size=_choice("PAGE_SIZE", "a4", PAGE_SIZES),
            page_title=_str("PAGE_TITLE") or i18n.Translator(locale)("page.title"),
            custom_page=_str("CUSTOM_PAGE"),
            run_on_start=_bool("RUN_ON_START", False),
            retries=_int("RETRIES", 3, maximum=20),
            retry_delay=_int("RETRY_DELAY", 300),
            job_timeout=_int("JOB_TIMEOUT", 300, minimum=10),
            verify_tls=_bool("TLS_VERIFY", False),
            low_ink_threshold=_int("LOW_INK_THRESHOLD", 10, maximum=100),
            notify_url=_url("NOTIFY_URL", ("http", "https"), "https://ntfy.sh/my-topic"),
            notify_on=_choice("NOTIFY_ON", "failure", NOTIFY_ON),
            notify_token=_str("NOTIFY_TOKEN"),
            locale=locale,
            log_level=_choice("LOG_LEVEL", "INFO", LOG_LEVELS, upper=True),
            state_dir=_str("STATE_DIR") or _default_state_dir(),
        )

    def require_printer(self) -> str:
        if not self.printer_uri:
            raise ConfigError(f"PRINTER_URI is not set (example: {URI_EXAMPLE})")
        return self.printer_uri
