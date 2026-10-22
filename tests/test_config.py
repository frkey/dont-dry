import pytest

from dont_dry.config import Config, ConfigError


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("STATE_DIR")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    cfg = Config.from_env()
    assert cfg.printer_uri == ""
    assert cfg.schedule == "0 9 * * 1,4"
    assert cfg.timezone.key == "UTC"
    assert (cfg.document_format, cfg.page_size, cfg.notify_on) == ("auto", "a4", "failure")
    assert (cfg.retries, cfg.retry_delay, cfg.job_timeout) == (3, 300, 300)
    assert cfg.verify_tls is False and cfg.run_on_start is False
    assert cfg.log_level == "INFO"
    assert cfg.state_dir == str(tmp_path / "dont-dry")


def test_values_are_trimmed_and_normalized(monkeypatch):
    monkeypatch.setenv("PRINTER_URI", "  ipps://printer.lan:8631/ipp/print ")
    monkeypatch.setenv("PAGE_SIZE", "Letter")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("RUN_ON_START", "YES")
    monkeypatch.setenv("SCHEDULE", "   ")  # empty means the default
    cfg = Config.from_env()
    assert cfg.printer_uri == "ipps://printer.lan:8631/ipp/print"
    assert cfg.page_size == "letter" and cfg.log_level == "DEBUG" and cfg.run_on_start
    assert cfg.schedule == "0 9 * * 1,4"


@pytest.mark.parametrize("name,value,message", [
    ("PRINTER_URI", "192.168.1.50", "ipp://"),             # no scheme: would fail on print day
    ("PRINTER_URI", "ipp:///ipp/print", "ipp://"),         # no host
    ("PRINTER_URI", "ipp://printer:abc/ipp/print", "port"),
    ("NOTIFY_URL", "file:///etc/passwd", "https://"),
    ("TZ", "Mars/Olympus_Mons", "time zone"),
    ("PAGE_SIZE", "a3", "PAGE_SIZE"),
    ("DOCUMENT_FORMAT", "image/urf", "DOCUMENT_FORMAT"),
    ("NOTIFY_ON", "sometimes", "NOTIFY_ON"),
    ("LOG_LEVEL", "verbose", "LOG_LEVEL"),
    ("RUN_ON_START", "maybe", "true or false"),
    ("RETRIES", "three", "integer"),
    ("RETRIES", "-1", "between 0 and 20"),
    ("JOB_TIMEOUT", "5", ">= 10"),
    ("LOW_INK_THRESHOLD", "101", "between 0 and 100"),
])
def test_invalid_values(monkeypatch, name, value, message):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=message.replace("/", r"\/")):
        Config.from_env()


def test_printer_is_required_only_where_needed():
    cfg = Config.from_env()
    with pytest.raises(ConfigError, match="PRINTER_URI is not set"):
        cfg.require_printer()
