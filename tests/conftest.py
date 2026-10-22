import pytest

# Every variable dont-dry reads (plus the one its default STATE_DIR depends on)
SETTINGS = (
    "PRINTER_URI", "SCHEDULE", "TZ", "RUN_ON_START", "PAGE_SIZE", "PAGE_TITLE", "DOCUMENT_FORMAT",
    "CUSTOM_PAGE", "RETRIES", "RETRY_DELAY", "JOB_TIMEOUT", "TLS_VERIFY", "LOW_INK_THRESHOLD",
    "NOTIFY_URL", "NOTIFY_TOKEN", "NOTIFY_ON", "LOCALE", "CATCH_UP", "LOG_LEVEL", "STATE_DIR",
    "XDG_STATE_HOME",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Tests never see the developer's own settings, and state goes to a temporary folder."""
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
