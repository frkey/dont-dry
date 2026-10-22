"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, notify, page, pwg, state
from .config import Config, ConfigError
from .i18n import Translator
from .ipp import IPPClient, IPPError
from .printer import (PrinterInfo, PrintError, PrintResult, Wait, choose_format, inspect,
                      plain_wait, print_page)
from .schedule import Cron, CronError

log = logging.getLogger("dont-dry")

HEARTBEAT_EVERY = 30  # seconds
# The healthcheck allows a few missed heartbeats: one IPP request can block for a minute or two
HEARTBEAT_STALE = HEARTBEAT_EVERY * 6
# No second page this soon after one reached the printer, whatever asks for it (a restart loop,
# the clock stepping back). Shorter than schedule.MIN_GAP, so it never skips a scheduled print.
RECENT_PRINT = timedelta(hours=1)
RENDER_FORMATS = {".pdf": "pdf", ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".pwg": "pwg"}


# --------------------------------------------------------------------------
# helpers


def _ink_summary(info: PrinterInfo) -> str:
    parts = [f"{m.name}: {m.level}%" if m.level is not None else f"{m.name}: ?"
             for m in info.markers]
    return ", ".join(parts)


def _low_ink(info: PrinterInfo, threshold: int) -> list[str]:
    return [m.name for m in info.markers if m.level is not None and m.level <= threshold]


def _print_once(cfg: Config, wait: Wait) -> PrintResult:
    try:
        return print_page(cfg, wait=wait)
    except Exception as exc:  # a bug must not stop the scheduler; the report says what happened
        log.exception("unexpected error while printing")
        return PrintResult(False, f"unexpected error: {type(exc).__name__}: {exc}",
                           reason="unexpected")


def _attempt(cfg: Config, retries: int, wait: Wait = plain_wait) -> PrintResult:
    result = PrintResult(False, "not attempted")
    for attempt in range(retries + 1):
        if attempt:
            log.info("retrying in %ss (attempt %d of %d)", cfg.retry_delay, attempt + 1,
                     retries + 1)
            if not wait(cfg.retry_delay):
                break
        result = _print_once(cfg, wait)
        if result.success:
            break
        log.error("print failed: %s", result.message)
        if not result.retryable:
            break
    return result


def _notification(t: Translator, result: PrintResult, low: list[str]) -> tuple[str, str]:
    """(title, body) in the configured language; the technical detail stays as it is."""
    if not result.success:
        title = t("notify.title.failed")
    elif low:
        title = t("notify.title.low_ink")
    else:
        title = t("notify.title.printed")
    reason = result.reason or ("printed" if result.success else "failed")
    lines = [t(f"result.{reason}"), t("notify.details", message=result.message)]
    if result.printer and result.printer.markers:
        lines.append(t("notify.ink", levels=_ink_summary(result.printer)))
    if low:
        lines.append(t("notify.low_ink", names=", ".join(low)))
    return title, "\n".join(lines)


def _last_sent(last: dict) -> str | None:
    """When a page last reached the printer, from last-run.json (0.1.0 kept only the last run)."""
    if last.get("last_sent"):
        return str(last["last_sent"])
    return str(last["time"]) if last.get("time") and (last.get("sent") or last.get("success")) \
        else None


def _report(cfg: Config, result: PrintResult) -> None:
    now = datetime.now(cfg.timezone)
    previous = state.read(cfg.state_dir, state.LAST_RUN) or {}
    low: list[str] = []
    if result.printer and result.printer.markers:
        low = _low_ink(result.printer, cfg.low_ink_threshold)
        if low:
            log.warning("low ink: %s", ", ".join(low))
    if result.success:
        log.info("done: %s", result.message)
    state.write(cfg.state_dir, state.LAST_RUN, {
        "time": now.isoformat(),
        "success": result.success,
        "sent": result.reached_printer,
        "last_sent": now.isoformat() if result.reached_printer else _last_sent(previous),
        "message": result.message,
        "job_id": result.job_id,
        "document_format": result.document_format,
        "ink": {m.name: m.level for m in result.printer.markers} if result.printer else {},
    })
    if cfg.notify_on == "never" or not cfg.notify_url:
        return
    if cfg.notify_on == "always" or not result.success or low:
        title, body = _notification(Translator(cfg.locale), result, low)
        notify.send(cfg.notify_url, title, body, token=cfg.notify_token,
                    failed=not result.success)


def _when(dt: datetime) -> str:
    """For log lines, which stay in English."""
    return dt.strftime("%a %Y-%m-%d %H:%M %Z")


def _sent_recently(cfg: Config) -> timedelta | None:
    """How long ago the last page reached the printer, when that was less than RECENT_PRINT
    ago. A time far in the future (a clock that was reset) does not count."""
    last = state.read(cfg.state_dir, state.LAST_RUN) or {}
    if not last.get("sent"):
        return None
    try:
        age = datetime.now(cfg.timezone) - datetime.fromisoformat(str(last.get("time")))
    except (TypeError, ValueError):
        return None
    return age if -RECENT_PRINT < age < RECENT_PRINT else None


class Scheduler:
    """The `run` loop. Every wait (until the next print, between retries, for a job to finish)
    keeps the heartbeat fresh for the healthcheck and ends early on SIGTERM or SIGINT."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cron = Cron(cfg.schedule)
        # before anything prints: a schedule that never fires, or fires minutes apart, is a
        # configuration error (exit code 2, which the systemd unit does not restart)
        self.cron.check(datetime.now(cfg.timezone))
        self.stopping = threading.Event()
        self.next_run: datetime | None = None
        self._last_beat: float | None = None

    def handle_signals(self) -> None:
        def handler(signum: int, frame: object) -> None:  # noqa: ARG001
            log.info("received %s, stopping", signal.Signals(signum).name)
            self.stopping.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def heartbeat(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._last_beat is not None and now - self._last_beat < HEARTBEAT_EVERY:
            return
        self._last_beat = now
        state.write(self.cfg.state_dir, state.HEARTBEAT, {
            "time": time.time(),
            "next_run": self.next_run.isoformat() if self.next_run else None,
        })

    def wait(self, seconds: float) -> bool:
        """Sleeps up to `seconds`. Returns False as soon as dont-dry is stopping."""
        deadline = time.monotonic() + seconds
        while not self.stopping.is_set():
            self.heartbeat()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            self.stopping.wait(min(remaining, HEARTBEAT_EVERY))
        return False

    def print_now(self) -> None:
        age = _sent_recently(self.cfg)
        if age is not None:
            log.warning("a page reached the printer %d minutes ago; not printing again so soon",
                        max(0, age.total_seconds() // 60))
            return
        _report(self.cfg, _attempt(self.cfg, self.cfg.retries, self.wait))

    @staticmethod
    def seconds_until(when: datetime) -> float:
        """Compared as instants: subtracting two datetimes of one zone compares wall clocks,
        which is wrong in the hour that repeats when daylight saving time ends."""
        return when.timestamp() - time.time()

    def missed_print(self) -> datetime | None:
        """The last scheduled print, when no page reached the printer since (dont-dry was down).
        None on the very first start: with no record, there is nothing to catch up on."""
        last = state.read(self.cfg.state_dir, state.LAST_RUN)
        if last is None:
            return None
        previous = self.cron.previous_at_or_before(datetime.now(self.cfg.timezone))
        if previous is None:
            return None
        sent = _last_sent(last)
        try:
            if sent and datetime.fromisoformat(sent).timestamp() >= previous.timestamp():
                return None
        except ValueError:
            pass  # unreadable: catching up once is safer than skipping a whole interval
        return previous

    def run(self) -> int:
        cfg = self.cfg
        log.info("dont-dry %s: printer %s, schedule %r (%s)", __version__, cfg.printer_uri,
                 cfg.schedule, cfg.timezone.key)
        self.heartbeat(force=True)
        if cfg.run_on_start:
            log.info("RUN_ON_START is set, printing now")
            self.print_now()
        elif cfg.catch_up and (missed := self.missed_print()) is not None:
            log.info("catching up on the print missed at %s", _when(missed))
            self.print_now()
        while not self.stopping.is_set():
            self.next_run = self.cron.next_after(datetime.now(cfg.timezone))
            log.info("next print: %s", _when(self.next_run))
            self.heartbeat(force=True)
            # Wake up at least every heartbeat and compare with the wall clock again, so a clock
            # change (NTP, a suspended VM) does not shift the print time
            while not self.stopping.is_set():
                remaining = self.seconds_until(self.next_run)
                if remaining <= 0:
                    self.print_now()
                    break
                self.wait(min(remaining, HEARTBEAT_EVERY))
        log.info("stopped")
        return 0


# --------------------------------------------------------------------------
# commands


def cmd_print(cfg: Config, args: argparse.Namespace) -> int:
    cfg.require_printer()
    result = _attempt(cfg, cfg.retries if args.retry else 0)
    _report(cfg, result)
    return 0 if result.success else 1


def _print_rows(rows: list[tuple[str, str]]) -> None:
    width = max(len(label) for label, _ in rows) + 1
    for label, value in rows:
        print(f"{label:<{width}}{value}")


def cmd_info(cfg: Config, args: argparse.Namespace) -> int:
    t = Translator(cfg.locale)
    client = IPPClient(cfg.require_printer(), verify_tls=cfg.verify_tls)
    info = inspect(client)
    rows = [
        (t("info.printer"), f"{info.name}  ({info.model})"),
        (t("info.state"), t(f"state.{info.state}")
         + (f"  [{', '.join(info.state_reasons)}]" if info.state_reasons else "")),
    ]
    if info.state_message:
        rows.append((t("info.message"), info.state_message))
    rows.append((t("info.accepting"), t("info.yes" if info.accepting_jobs else "info.no")))
    rows.append((t("info.formats"), ", ".join(info.formats) or "-"))
    if info.pwg_resolutions or info.pwg_types:
        rows.append((t("info.pwg"), f"{', '.join(map(str, info.pwg_resolutions)) or '-'}; "
                                    f"{', '.join(info.pwg_types) or '-'}"))
    if info.markers:
        rows.append((t("info.ink"), _ink_summary(info)))
    try:
        fmt = choose_format(info, cfg.document_format)
    except PrintError as exc:
        _print_rows(rows + [(t("info.problem"), str(exc))])
        return 1
    _print_rows(rows)
    print(t("info.would_send", format=fmt))
    return 0


def cmd_render(cfg: Config, args: argparse.Namespace) -> int:
    out = Path(args.output)
    fmt = args.format or RENDER_FORMATS.get(out.suffix.lower(), "pdf")
    image = page.render(dpi=args.dpi, page_size=cfg.page_size, title=cfg.page_title,
                        when=datetime.now(cfg.timezone), locale=cfg.locale)
    if fmt == "pdf":
        out.write_bytes(page.to_pdf(image, args.dpi))
    elif fmt == "jpeg":
        out.write_bytes(page.to_jpeg(image, args.dpi))
    elif fmt == "png":
        image.save(out, format="PNG", dpi=(args.dpi, args.dpi))
    elif fmt == "pwg":
        out.write_bytes(pwg.encode(image, dpi=args.dpi,
                                   page_size_name=pwg.PAGE_SIZE_NAMES[cfg.page_size]))
    print(Translator(cfg.locale)("render.wrote", path=out, format=fmt,
                                 size=out.stat().st_size // 1024))
    return 0


def cmd_next(cfg: Config, args: argparse.Namespace) -> int:
    t = Translator(cfg.locale)
    cron = Cron(cfg.schedule)
    when = datetime.now(cfg.timezone)
    cron.check(when)
    print(t("next.header", schedule=cfg.schedule, timezone=cfg.timezone.key))
    for _ in range(args.count):
        when = cron.next_after(when)
        print("  " + t.datetime(when, "format.schedule"))
    return 0


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    cfg.require_printer()
    scheduler = Scheduler(cfg)
    scheduler.handle_signals()
    return scheduler.run()


def cmd_healthcheck(cfg: Config, args: argparse.Namespace) -> int:
    beat = (state.read(cfg.state_dir, state.HEARTBEAT) or {}).get("time")
    if not isinstance(beat, (int, float)):
        return 1
    return 0 if time.time() - beat < HEARTBEAT_STALE else 1


# --------------------------------------------------------------------------


def _int_between(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return value
    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dont-dry",
        description="Print a maintenance page on a schedule so inkjet heads don't dry out. "
                    "Settings come from environment variables (see .env.example).")
    parser.add_argument("--version", action="version", version=f"dont-dry {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("run", help="run the scheduler (default)")
    p = sub.add_parser("print", help="print the maintenance page now")
    p.add_argument("--retry", action="store_true", help="retry on failure like the scheduler")
    sub.add_parser("info", help="show printer status, formats and ink levels")
    p = sub.add_parser("render", help="save the test page to a file (no printer needed)")
    p.add_argument("output", help="output file (.pdf, .jpg, .png or .pwg)")
    p.add_argument("--format", choices=["pdf", "jpeg", "png", "pwg"])
    p.add_argument("--dpi", type=_int_between(50, 1200), default=150)
    p = sub.add_parser("next", help="list the next scheduled print times")
    p.add_argument("-n", "--count", type=_int_between(1, 100), default=5)
    sub.add_parser("healthcheck", help="exit 0 if the scheduler is alive (for Docker)")
    return parser


COMMANDS = {
    "run": cmd_run, "print": cmd_print, "info": cmd_info, "render": cmd_render,
    "next": cmd_next, "healthcheck": cmd_healthcheck,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    try:
        cfg = Config.from_env()
        logging.getLogger().setLevel(cfg.log_level)
        return COMMANDS[args.command or "run"](cfg, args)
    except (ConfigError, CronError, IPPError, PrintError, OSError) as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130
