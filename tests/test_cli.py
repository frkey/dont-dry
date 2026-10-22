import time
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dont_dry import __version__, cli, state
from dont_dry.config import Config
from dont_dry.printer import PrinterInfo, PrintResult

from .fake_printer import FakePrinter


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("PRINTER_URI", "ipp://127.0.0.1:1/ipp/print")
    monkeypatch.setenv("RETRY_DELAY", "0")
    return Config.from_env()


def test_installed_version_matches_the_package():
    assert version("dont-dry") == __version__


# -- commands -------------------------------------------------------------------


@pytest.mark.parametrize("locale,header,weekday", [
    ("en-US", "Schedule '0 9 * * mon' in America/Sao_Paulo:", "  Mon, "),
    ("pt-BR", "Agendamento '0 9 * * mon' no fuso America/Sao_Paulo:", "  seg, "),
])
def test_next_lists_upcoming_prints(monkeypatch, capsys, locale, header, weekday):
    monkeypatch.setenv("SCHEDULE", "0 9 * * mon")
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    monkeypatch.setenv("LOCALE", locale)
    assert cli.main(["next", "-n", "3"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == header
    assert len(lines) == 4 and all(line.startswith(weekday) for line in lines[1:])


@pytest.mark.parametrize("locale,expected", [
    ("en-US", [
        "Printer:        Fake Tank  (HP Fake Tank 720)",
        "State:          idle  [none]",
        "Accepting jobs: yes",
        "Formats:        application/pdf, image/pwg-raster, image/jpeg",
        "PWG raster:     300dpi, 600dpi; srgb_8, sgray_8",
        "Ink:            Cyan: 80%, Magenta: 5%, Yellow: 60%, Black: ?",
        "dont-dry would send: application/pdf",
    ]),
    ("pt-BR", [
        "Impressora:       Fake Tank  (HP Fake Tank 720)",
        "Estado:           pronta  [none]",
        "Aceita trabalhos: sim",
        "Formatos:         application/pdf, image/pwg-raster, image/jpeg",
        "PWG raster:       300dpi, 600dpi; srgb_8, sgray_8",
        "Tinta:            Cyan: 80%, Magenta: 5%, Yellow: 60%, Black: ?",
        "O dont-dry enviaria: application/pdf",
    ]),
])
def test_info_describes_the_printer(monkeypatch, capsys, locale, expected):
    monkeypatch.setenv("LOCALE", locale)
    with FakePrinter() as printer:
        monkeypatch.setenv("PRINTER_URI", printer.uri)
        assert cli.main(["info"]) == 0
    assert capsys.readouterr().out.splitlines() == expected


def test_info_names_the_problem(monkeypatch, capsys):
    with FakePrinter(formats=["image/urf"]) as printer:
        monkeypatch.setenv("PRINTER_URI", printer.uri)
        assert cli.main(["info"]) == 1
    assert capsys.readouterr().out.splitlines()[-1].startswith("Problem:        printer supports")


@pytest.mark.parametrize("name,magic", [
    ("page.pdf", b"%PDF"), ("page.png", b"\x89PNG"), ("page.jpg", b"\xff\xd8"),
    ("page.pwg", b"RaS2"),
])
def test_render_writes_each_format(tmp_path, capsys, name, magic):
    out = tmp_path / name
    assert cli.main(["render", str(out), "--dpi", "50"]) == 0
    assert out.read_bytes().startswith(magic)
    assert f"wrote {out}" in capsys.readouterr().out


def test_render_speaks_the_locale(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOCALE", "pt-BR")
    assert cli.main(["render", str(tmp_path / "pagina.png"), "--dpi", "50"]) == 0
    assert capsys.readouterr().out.startswith(f"Arquivo salvo: {tmp_path / 'pagina.png'} (png, ")


def test_render_rejects_a_silly_resolution(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main(["render", str(tmp_path / "page.pdf"), "--dpi", "0"])
    assert exc.value.code == 2


@pytest.mark.parametrize("env,command", [
    ({"TZ": "Nowhere/Town"}, "next"),
    ({"LOG_LEVEL": "loud"}, "next"),
    ({"PRINTER_URI": "192.168.1.50"}, "run"),
    ({}, "run"),                                   # PRINTER_URI missing
    ({"PRINTER_URI": "ipp://p/ipp/print", "SCHEDULE": "0 0 31 2 *"}, "run"),  # never fires
])
def test_bad_settings_exit_with_2_and_say_why(monkeypatch, caplog, env, command):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert cli.main([command]) == 2
    assert caplog.records and caplog.records[-1].levelname == "ERROR"


def test_healthcheck_follows_the_heartbeat(cfg):
    assert cli.main(["healthcheck"]) == 1                          # never started
    state.write(cfg.state_dir, state.HEARTBEAT, {"time": time.time()})
    assert cli.main(["healthcheck"]) == 0
    state.write(cfg.state_dir, state.HEARTBEAT, {"time": time.time() - cli.HEARTBEAT_STALE - 1})
    assert cli.main(["healthcheck"]) == 1
    (Path(cfg.state_dir) / state.HEARTBEAT).write_text("{not json")
    assert cli.main(["healthcheck"]) == 1


# -- retries --------------------------------------------------------------------


def _fake_prints(monkeypatch, *results):
    calls = []

    def fake(cfg, *, wait):
        calls.append(cfg)
        return results[len(calls) - 1]

    monkeypatch.setattr(cli, "print_page", fake)
    return calls


def test_retries_failures_that_may_go_away(cfg, monkeypatch):
    calls = _fake_prints(monkeypatch, PrintResult(False, "asleep", retryable=True),
                         PrintResult(False, "asleep", retryable=True), PrintResult(True, "ok"))
    assert cli._attempt(cfg, retries=3).success
    assert len(calls) == 3


def test_never_retries_a_job_the_printer_accepted(cfg, monkeypatch):
    calls = _fake_prints(monkeypatch, PrintResult(False, "job 1 aborted", job_id=1))
    assert not cli._attempt(cfg, retries=3).success
    assert len(calls) == 1


def test_stopping_cancels_the_remaining_retries(cfg, monkeypatch):
    calls = _fake_prints(monkeypatch, PrintResult(False, "asleep", retryable=True))
    result = cli._attempt(cfg, retries=3, wait=lambda seconds: False)
    assert result.message == "asleep" and len(calls) == 1


def test_an_unexpected_error_becomes_a_failed_result(cfg, monkeypatch, caplog):
    def broken(cfg, *, wait):
        raise ZeroDivisionError("oops")

    monkeypatch.setattr(cli, "print_page", broken)
    result = cli._attempt(cfg, retries=3)
    assert not result.success and not result.retryable
    assert "ZeroDivisionError: oops" in result.message
    assert "unexpected error" in caplog.text


# -- scheduler ------------------------------------------------------------------


def test_wait_keeps_the_heartbeat_and_stops_early(cfg):
    scheduler = cli.Scheduler(cfg)
    assert scheduler.wait(0.01) is True
    assert state.read(cfg.state_dir, state.HEARTBEAT)["time"] == pytest.approx(time.time(), abs=5)
    scheduler.stopping.set()
    started = time.monotonic()
    assert scheduler.wait(60) is False
    assert time.monotonic() - started < 1


def test_run_prints_when_due_then_stops(cfg, monkeypatch):
    scheduler = cli.Scheduler(cfg)
    printed = []

    def print_now():
        printed.append(scheduler.next_run)
        scheduler.stopping.set()

    monkeypatch.setattr(scheduler, "print_now", print_now)
    monkeypatch.setattr(scheduler.cron, "next_after", lambda after: after)  # due right away
    assert scheduler.run() == 0
    assert len(printed) == 1
    assert state.read(cfg.state_dir, state.HEARTBEAT)["next_run"] == printed[0].isoformat()


def test_run_on_start_prints_before_the_first_wait(monkeypatch, cfg):
    monkeypatch.setenv("RUN_ON_START", "true")
    scheduler = cli.Scheduler(Config.from_env())
    monkeypatch.setattr(scheduler, "print_now", scheduler.stopping.set)
    assert scheduler.run() == 0
    assert scheduler.next_run is None


# -- reports --------------------------------------------------------------------


def _printer(levels):
    return PrinterInfo.from_attributes({"marker-names": ["Cyan", "Black"],
                                        "marker-levels": levels})


@pytest.mark.parametrize("notify_on,result,title", [
    ("failure", PrintResult(False, "job 1 aborted"), "dont-dry: print FAILED"),
    ("failure", PrintResult(True, "ok", printer=_printer([50, 50])), None),
    ("failure", PrintResult(True, "ok", printer=_printer([50, 4])),
     "dont-dry: page printed, ink low"),
    ("always", PrintResult(True, "ok"), "dont-dry: page printed"),
    ("never", PrintResult(False, "job 1 aborted"), None),
])
def test_report_notifies_as_configured(monkeypatch, notify_on, result, title):
    monkeypatch.setenv("NOTIFY_URL", "https://ntfy.example/topic")
    monkeypatch.setenv("NOTIFY_ON", notify_on)
    cfg = Config.from_env()
    sent = []
    monkeypatch.setattr(cli.notify, "send", lambda url, title, message, **kw: sent.append(title))
    cli._report(cfg, result)
    assert sent == ([title] if title else [])
    last = state.read(cfg.state_dir, state.LAST_RUN)
    assert last["success"] is result.success and last["message"] == result.message


@pytest.mark.parametrize("locale,title,body", [
    ("en-US", "dont-dry: print FAILED", [
        "dont-dry could not reach the printer.",
        "Details: cannot reach printer at 192.168.1.50:631: timed out",
        "Ink: Cyan: 50%, Black: 4%",
        "Low ink: Black",
    ]),
    ("pt-BR", "dont-dry: a impressão FALHOU", [
        "O dont-dry não conseguiu falar com a impressora.",
        "Detalhes: cannot reach printer at 192.168.1.50:631: timed out",
        "Tinta: Cyan: 50%, Black: 4%",
        "Tinta baixa: Black",
    ]),
])
def test_notification_speaks_the_locale(monkeypatch, locale, title, body):
    monkeypatch.setenv("NOTIFY_URL", "https://ntfy.example/topic")
    monkeypatch.setenv("LOCALE", locale)
    sent = []
    monkeypatch.setattr(cli.notify, "send",
                        lambda url, title, message, **kw: sent.append((title, message)))
    result = PrintResult(False, "cannot reach printer at 192.168.1.50:631: timed out",
                         printer=_printer([50, 4]), retryable=True, reason="unreachable")
    cli._report(Config.from_env(), result)
    assert sent == [(title, "\n".join(body))]


def test_notification_without_a_reason_still_says_what_happened():
    t = cli.Translator("pt-BR")
    title, body = cli._notification(t, PrintResult(True, "ok"), [])
    assert title == "dont-dry: página impressa"
    assert body == "A página foi impressa.\nDetalhes: ok"


# -- never more pages than scheduled ---------------------------------------------


def test_a_schedule_that_never_fires_prints_nothing_even_with_run_on_start(monkeypatch, cfg):
    monkeypatch.setenv("SCHEDULE", "0 9 30 2 *")
    monkeypatch.setenv("RUN_ON_START", "true")
    calls = _fake_prints(monkeypatch, PrintResult(True, "ok"))
    assert cli.main(["run"]) == 2
    assert calls == []


def test_a_schedule_that_prints_too_often_is_refused(monkeypatch, cfg, caplog):
    monkeypatch.setenv("SCHEDULE", "* 9 * * 1")
    # without the check the loop would wait for Monday: make any wait stop it instead
    monkeypatch.setattr(cli.Scheduler, "wait", lambda self, seconds: self.stopping.set())
    assert cli.main(["run"]) == 2
    assert "less than 3 hours apart" in caplog.text


def test_no_second_print_right_after_one_reached_the_printer(cfg, monkeypatch):
    calls = _fake_prints(monkeypatch, PrintResult(True, "job 1 completed", job_id=1),
                         PrintResult(True, "job 2 completed", job_id=2))
    scheduler = cli.Scheduler(cfg)
    scheduler.print_now()
    scheduler.print_now()  # a restart loop or a clock step back: not again within the hour
    assert len(calls) == 1
    assert state.read(cfg.state_dir, state.LAST_RUN)["sent"] is True


def test_a_print_long_ago_or_a_failure_does_not_block_the_next(cfg, monkeypatch):
    state.write(cfg.state_dir, state.LAST_RUN, {"time": "2020-01-01T09:00:00+00:00",
                                                "sent": True})
    calls = _fake_prints(monkeypatch, PrintResult(False, "asleep", retryable=False),
                         PrintResult(True, "job 1 completed", job_id=1))
    scheduler = cli.Scheduler(cfg)
    scheduler.print_now()  # last success was years ago: prints (and fails)
    scheduler.print_now()  # a failure that never reached the printer does not block
    assert len(calls) == 2


def test_the_run_loop_waits_by_the_clock_not_the_wall(cfg, monkeypatch):
    # next_run in the repeated DST hour must be compared as an instant
    scheduler = cli.Scheduler(cfg)
    past = datetime.fromtimestamp(time.time() - 60, ZoneInfo("Europe/Berlin"))
    assert scheduler.seconds_until(past) < 0


# -- catching up on a missed print --------------------------------------------------


def _run_once(cfg, monkeypatch):
    """Runs the scheduler until its first wait; returns how many times it printed."""
    scheduler = cli.Scheduler(cfg)
    printed = []
    monkeypatch.setattr(scheduler, "print_now", lambda: printed.append(1))
    monkeypatch.setattr(scheduler, "wait", lambda seconds: scheduler.stopping.set())
    scheduler.run()
    return len(printed)


def _last_run(cfg, **fields):
    state.write(cfg.state_dir, state.LAST_RUN, {"time": "2026-10-01T09:00:00-03:00",
                                                "sent": False, **fields})


def test_catches_up_on_a_print_missed_while_down(cfg, monkeypatch):
    _last_run(cfg, last_sent="2020-01-01T09:00:00+00:00")  # long before the last scheduled time
    assert _run_once(cfg, monkeypatch) == 1


def test_no_catch_up_when_the_last_scheduled_print_went_out(cfg, monkeypatch):
    _last_run(cfg, last_sent=datetime.now(cfg.timezone).isoformat())
    assert _run_once(cfg, monkeypatch) == 0


def test_no_catch_up_on_the_very_first_start(cfg, monkeypatch):
    assert _run_once(cfg, monkeypatch) == 0


def test_catch_up_can_be_turned_off(monkeypatch, cfg):
    monkeypatch.setenv("CATCH_UP", "false")
    cfg = Config.from_env()
    _last_run(cfg, last_sent="2020-01-01T09:00:00+00:00")
    assert _run_once(cfg, monkeypatch) == 0


def test_last_sent_survives_a_failed_attempt(cfg):
    cli._report(cfg, PrintResult(True, "job 1 completed", job_id=1))
    sent = state.read(cfg.state_dir, state.LAST_RUN)["last_sent"]
    cli._report(cfg, PrintResult(False, "asleep"))
    last = state.read(cfg.state_dir, state.LAST_RUN)
    assert last["sent"] is False and last["last_sent"] == sent


def test_a_print_cut_short_by_a_restart_is_not_caught_up(cfg, monkeypatch):
    # the old process died after sending, before writing last-run.json
    _last_run(cfg, last_sent="2020-01-01T09:00:00+00:00")
    state.write(cfg.state_dir, state.SENDING, {"time": datetime.now(cfg.timezone).isoformat()})
    assert _run_once(cfg, monkeypatch) == 0
    assert cli._sent_recently(cfg) is not None  # and it counts as a page that may be out


def test_the_sending_mark_is_cleared_after_the_report(cfg, monkeypatch):
    _fake_prints(monkeypatch, PrintResult(False, "asleep"))
    cli.Scheduler(cfg).print_now()
    assert state.read(cfg.state_dir, state.SENDING) is None


# -- notifications ------------------------------------------------------------------


def test_low_ink_notifies_when_it_drops_not_at_every_print(monkeypatch):
    monkeypatch.setenv("NOTIFY_URL", "https://ntfy.example/topic")
    cfg = Config.from_env()
    sent = []
    monkeypatch.setattr(cli.notify, "send", lambda url, title, message, **kw: sent.append(title))
    for levels in ([50, 8], [50, 8], [50, 5], [50, 90], [50, 8]):
        cli._report(cfg, PrintResult(True, "ok", printer=_printer(levels)))
    # 8: first drop; 8 again: quiet; 5: further drop; 90: refilled, quiet; 8: low again
    assert sent == ["dont-dry: page printed, ink low"] * 3


def test_notify_test_sends_a_translated_test(monkeypatch, capsys):
    monkeypatch.setenv("NOTIFY_URL", "https://ntfy.example/topic")
    monkeypatch.setenv("LOCALE", "pt-BR")
    sent = []
    monkeypatch.setattr(cli.notify, "send",
                        lambda url, title, message, **kw: sent.append((title, message)) or True)
    assert cli.main(["notify-test"]) == 0
    assert sent == [("dont-dry: notificação de teste",
                     "Se você está lendo isto, as notificações do dont-dry funcionam.")]
    assert "enviada" in capsys.readouterr().out


def test_notify_test_fails_without_a_url_or_when_refused(monkeypatch, capsys):
    assert cli.main(["notify-test"]) == 2                # NOTIFY_URL is not set
    monkeypatch.setenv("NOTIFY_URL", "https://ntfy.example/topic")
    monkeypatch.setattr(cli.notify, "send", lambda *a, **kw: False)
    assert cli.main(["notify-test"]) == 1
