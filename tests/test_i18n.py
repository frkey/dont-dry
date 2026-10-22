import string
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from PIL import ImageDraw

from dont_dry import i18n, page
from dont_dry.config import Config, ConfigError
from dont_dry.i18n import LOCALES, Translator, catalog
from dont_dry.ipp import PRINTER_STATES
from dont_dry.printer import REASONS

SP = ZoneInfo("America/Sao_Paulo")
REFERENCE = catalog(i18n.DEFAULT_LOCALE)


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


@pytest.mark.parametrize("locale", LOCALES)
def test_every_catalog_has_the_reference_keys(locale):
    assert catalog(locale).keys() == REFERENCE.keys()


@pytest.mark.parametrize("locale", LOCALES)
def test_translations_use_the_same_placeholders(locale):
    for key, template in catalog(locale).items():
        if not key.startswith("format."):  # date formats pick their own fields on purpose
            assert _fields(template) == _fields(REFERENCE[key]), key


def test_every_outcome_and_printer_state_has_a_sentence():
    for reason in (*REASONS, "printed", "failed"):
        assert f"result.{reason}" in REFERENCE
    for state in (*PRINTER_STATES.values(), "unknown"):
        assert f"state.{state}" in REFERENCE


@pytest.mark.parametrize("locale,when,expected", [
    ("en-US", datetime(2026, 10, 22, 9, 5, tzinfo=SP), "Oct 22, 2026 9:05 AM"),
    ("en-US", datetime(2026, 10, 22, 0, 30, tzinfo=SP), "Oct 22, 2026 12:30 AM"),
    ("en-US", datetime(2026, 10, 22, 12, 0, tzinfo=SP), "Oct 22, 2026 12:00 PM"),
    ("pt-BR", datetime(2026, 10, 22, 9, 5, tzinfo=SP), "22/10/2026 09:05"),
    ("pt-BR", datetime(2026, 3, 1, 21, 40, tzinfo=SP), "01/03/2026 21:40"),
])
def test_dates_follow_the_locale(locale, when, expected):
    assert Translator(locale).datetime(when) == expected


@pytest.mark.parametrize("locale,expected", [
    ("en-US", "Sat, Oct 24, 2026 9:00 AM -03"),
    ("pt-BR", "sáb, 24/10/2026 09:00 -03"),
])
def test_schedule_times_have_a_weekday_and_zone(locale, expected):
    when = datetime(2026, 10, 24, 9, 0, tzinfo=SP)
    assert Translator(locale).datetime(when, "format.schedule") == expected


@pytest.mark.parametrize("locale", LOCALES)
def test_month_and_weekday_lists_are_complete(locale):
    t = Translator(locale)
    assert len(t("format.months").split()) == 12
    assert len(t("format.weekdays").split()) == 7


def test_a_key_missing_from_a_translation_falls_back_to_english(monkeypatch):
    t = Translator("pt-BR")
    monkeypatch.delitem(t._catalog, "page.title")
    assert t("page.title") == "dont-dry maintenance page"


# -- LOCALE ---------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("", "en-US"), ("pt-BR", "pt-BR"), ("pt_br", "pt-BR"), (" EN-us ", "en-US"),
])
def test_locale_setting(monkeypatch, value, expected):
    monkeypatch.setenv("LOCALE", value)
    assert Config.from_env().locale == expected


def test_unknown_locale_is_refused(monkeypatch):
    monkeypatch.setenv("LOCALE", "es-ES")
    with pytest.raises(ConfigError, match="LOCALE must be one of en-US, pt-BR"):
        Config.from_env()


def test_default_page_title_follows_the_locale(monkeypatch):
    monkeypatch.setenv("LOCALE", "pt-BR")
    assert Config.from_env().page_title == "página de manutenção dont-dry"
    monkeypatch.setenv("PAGE_TITLE", "Epson do escritório")
    assert Config.from_env().page_title == "Epson do escritório"


# -- the page -------------------------------------------------------------------


def _drawn_text(monkeypatch, **render_args) -> list[str]:
    texts = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        texts.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    page.render(dpi=50, when=datetime(2026, 10, 22, 9, 0, tzinfo=SP), **render_args)
    return texts


def test_page_in_portuguese(monkeypatch):
    texts = _drawn_text(monkeypatch, locale="pt-BR")
    assert texts[0] == "página de manutenção dont-dry"
    assert texts[1] == ("Evita que as cabeças de impressão sequem. "
                        "Impresso pelo dont-dry em 22/10/2026 09:00.")
    assert {"Ciano", "Amarelo", "Preto", "Preto composto"} <= set(texts)
    assert "Linhas finas: falhas ou linhas faltando indicam bicos entupidos." in texts


def test_page_in_english_by_default(monkeypatch):
    texts = _drawn_text(monkeypatch, title="Office Epson")
    assert texts[0] == "Office Epson"
    assert texts[1].endswith("Printed by dont-dry on Oct 22, 2026 9:00 AM.")
    assert {"Cyan", "Yellow", "Composite black"} <= set(texts)
