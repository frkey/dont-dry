"""High-level printing: inspect the printer, pick a format, send the page and
wait for the job to finish."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from PIL import Image, ImageOps

from . import page, pwg
from .config import Config
from .ipp import (JOB_STATES, PRINTER_STATES, TAG_KEYWORD, Attr, IPPAnswerLost, IPPClient,
                  IPPError, IPPRefused, Resolution)

log = logging.getLogger(__name__)

PREFERRED_FORMATS = ["application/pdf", "image/pwg-raster", "image/jpeg"]
REQUESTED_ATTRIBUTES = [
    "printer-name", "printer-make-and-model", "printer-state", "printer-state-reasons",
    "printer-state-message", "printer-is-accepting-jobs", "document-format-supported",
    "pwg-raster-document-resolution-supported", "pwg-raster-document-type-supported",
    "media-supported", "media-default", "print-color-mode-supported",
    "marker-names", "marker-levels", "marker-colors", "marker-low-levels",
]
# Print-Job answers that can go away on their own (the printer is busy, warming up, jammed...)
RETRYABLE_STATUSES = {0x0502, 0x0504, 0x0505, 0x0506, 0x0507}
JOB_NOT_FOUND = 0x0406
POLL_EVERY = 3  # seconds between job state checks
FINAL_JOB_STATES = ("completed", "canceled", "aborted")
# What a PrintResult can say happened; each one has a "result.<reason>" catalog sentence that
# notifications show above the technical message
REASONS = (
    "completed", "accepted", "unreachable", "not_accepting", "no_format", "bad_document",
    "rejected", "answer_lost", "canceled", "aborted", "timeout", "stopped", "unknown",
    "unexpected",
)

Wait = Callable[[float], bool]
"""Sleeps for the given seconds; returns False when dont-dry is stopping."""


def plain_wait(seconds: float) -> bool:
    """A Wait that just sleeps (for one-off commands)."""
    time.sleep(seconds)
    return True


class PrintError(Exception):
    pass


@dataclass
class Marker:
    name: str
    level: int | None  # percent, None when unknown
    color: str


def _text(value: Any) -> str:
    """Text from the printer without control characters (escape sequences, newlines), so it
    can neither rewrite a terminal nor forge a log line. Any whitespace becomes a plain space."""
    return "".join(ch if ch.isprintable() else " " if ch.isspace() else ""
                   for ch in str(value))


def _enum(value: Any) -> int | None:
    """An enum value as the printer sent it, or None for any other type (a collection, say)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class PrinterInfo:
    name: str
    model: str
    state: str
    state_reasons: list[str]
    state_message: str
    accepting_jobs: bool
    formats: list[str]
    pwg_resolutions: list[Resolution]
    pwg_types: list[str]
    media: list[str]
    color_modes: list[str]
    markers: list[Marker] = field(default_factory=list)

    @classmethod
    def from_attributes(cls, attrs: dict[str, Any]) -> PrinterInfo:
        def first(name: str, default: Any = "") -> Any:
            values = attrs.get(name) or [default]
            return values[0] if values[0] is not None else default

        def many(name: str) -> list[Any]:
            return [v for v in attrs.get(name, []) if v is not None]

        def texts(name: str) -> list[str]:
            return [_text(v) for v in attrs.get(name, []) if isinstance(v, str)]

        def text(name: str) -> str:
            return (texts(name) or [""])[0]

        names = texts("marker-names")
        levels = attrs.get("marker-levels", [])
        colors = texts("marker-colors")
        markers = []
        for i, name in enumerate(names):
            level = levels[i] if i < len(levels) else None
            # negative levels mean "unknown" / "some ink remaining"
            markers.append(Marker(name, level if isinstance(level, int) and level >= 0 else None,
                                  colors[i] if i < len(colors) else ""))
        return cls(
            name=text("printer-name"),
            model=text("printer-make-and-model"),
            state=PRINTER_STATES.get(_enum(first("printer-state", None)), "unknown"),
            state_reasons=texts("printer-state-reasons"),
            state_message=text("printer-state-message"),
            accepting_jobs=first("printer-is-accepting-jobs", True) is not False,
            formats=texts("document-format-supported"),
            pwg_resolutions=[r for r in many("pwg-raster-document-resolution-supported")
                             if isinstance(r, Resolution)],
            pwg_types=texts("pwg-raster-document-type-supported"),
            media=texts("media-supported"),
            color_modes=texts("print-color-mode-supported"),
            markers=markers,
        )

    @property
    def problems(self) -> list[str]:
        """State reasons that mean trouble (ignores 'none' and info-level reasons)."""
        return [r for r in self.state_reasons
                if r != "none" and not r.endswith("-report")]


@dataclass
class PrintResult:
    success: bool
    message: str
    job_id: int | None = None
    document_format: str = ""
    printer: PrinterInfo | None = None
    # True only when no job reached the printer and trying again later may work. A job the
    # printer accepted is never resent: if it aborted, printing it again would waste ink.
    retryable: bool = False
    reason: str = ""  # one of REASONS; `message` holds the technical detail, in English

    @property
    def reached_printer(self) -> bool:
        """The printer took a job, so a page may have come out even if it did not finish."""
        return self.success or self.job_id is not None or self.reason == "answer_lost"


def inspect(client: IPPClient) -> PrinterInfo:
    return PrinterInfo.from_attributes(client.get_printer_attributes(REQUESTED_ATTRIBUTES))


def choose_format(info: PrinterInfo, preference: str) -> str:
    if preference != "auto":
        if info.formats and preference not in info.formats:
            log.warning("printer does not list %s as supported; trying it anyway", preference)
        return preference
    for fmt in PREFERRED_FORMATS:
        if fmt in info.formats:
            if fmt == "image/pwg-raster" and (not _pwg_type(info) or _pwg_dpi(info) is None):
                continue
            return fmt
    raise PrintError(
        "printer supports none of " + ", ".join(PREFERRED_FORMATS)
        + f" (it lists: {', '.join(info.formats) or 'nothing'})")


def _pwg_type(info: PrinterInfo) -> str | None:
    for doc_type in ("srgb_8", "rgb_8", "sgray_8"):
        if doc_type in info.pwg_types:
            return doc_type
    return None if info.pwg_types else "srgb_8"


def _pwg_dpi(info: PrinterInfo) -> int | None:
    """The resolution to render PWG Raster at, or None when the printer lists only non-square
    ones (dont-dry renders square pixels)."""
    dpis = sorted({r.dpi[0] for r in info.pwg_resolutions if r.dpi[0] == r.dpi[1]})
    if not dpis:
        return None if info.pwg_resolutions else 300
    if 300 in dpis:
        return 300
    at_least = [d for d in dpis if d >= 300]
    return at_least[0] if at_least else dpis[-1]


def _load_custom_page(path: str) -> tuple[str, bytes | Image.Image]:
    """Returns ('pdf', bytes) for PDFs or ('image', Image) for pictures."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data.startswith(b"%PDF"):
        return "pdf", data
    try:
        image = Image.open(path)
        image.load()
    except Exception as exc:
        raise PrintError(f"CUSTOM_PAGE {path} is neither a PDF nor an image: {exc}") from exc
    return "image", _flatten(ImageOps.exif_transpose(image))


def _flatten(image: Image.Image) -> Image.Image:
    """RGB on white: converting a transparent image straight to RGB paints its transparent
    parts black, a page of wasted ink."""
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        white = Image.new("RGB", rgba.size, page.WHITE)
        white.paste(rgba, mask=rgba.getchannel("A"))
        return white
    return image.convert("RGB")


def build_document(cfg: Config, info: PrinterInfo, fmt: str) -> bytes:
    page_size_name = pwg.PAGE_SIZE_NAMES[cfg.page_size]
    custom: tuple[str, bytes | Image.Image] | None = None
    if cfg.custom_page:
        custom = _load_custom_page(cfg.custom_page)
        if custom[0] == "pdf":
            if fmt != "application/pdf":
                raise PrintError("CUSTOM_PAGE is a PDF but the printer does not accept PDF; "
                                 "use a PNG or JPEG image instead")
            return custom[1]  # type: ignore[return-value]

    dpi = (_pwg_dpi(info) or 300) if fmt == "image/pwg-raster" else 300
    if custom:
        image = _fit_to_page(custom[1], cfg.page_size, dpi)  # type: ignore[arg-type]
    else:
        image = page.render(dpi=dpi, page_size=cfg.page_size, title=cfg.page_title,
                            when=datetime.now(cfg.timezone), locale=cfg.locale)

    if fmt == "application/pdf":
        return page.to_pdf(image, dpi)
    if fmt == "image/jpeg":
        return page.to_jpeg(image, dpi)
    if fmt == "image/pwg-raster":
        doc_type = _pwg_type(info) or "srgb_8"
        if doc_type == "sgray_8":
            log.warning("printer only accepts grayscale PWG raster; colors will print as grey")
        return pwg.encode(image, dpi=dpi, doc_type=doc_type, page_size_name=page_size_name)
    raise PrintError(f"don't know how to produce {fmt}")


def _fit_to_page(image: Image.Image, page_size: str, dpi: int) -> Image.Image:
    """Scales the image up or down to fill the page (keeping its proportions) and centers it."""
    width_in, height_in = page.PAGE_SIZES[page_size]
    size = (round(width_in * dpi), round(height_in * dpi))
    canvas = Image.new("RGB", size, page.WHITE)
    fitted = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def _job_attributes(cfg: Config, info: PrinterInfo) -> list[Attr]:
    attrs = []
    media = pwg.PAGE_SIZE_NAMES[cfg.page_size]
    if media in info.media:
        attrs.append(Attr("media", TAG_KEYWORD, media))
    if "color" in info.color_modes:
        attrs.append(Attr("print-color-mode", TAG_KEYWORD, "color"))
    return attrs


def _wait_for_job(client: IPPClient, job_id: int, timeout: int,
                  wait: Wait = plain_wait) -> tuple[str, str, str]:
    """Poll until the job reaches a final state. Returns (reason, state, detail)."""
    deadline = time.monotonic() + timeout
    state, detail = "pending", ""
    while time.monotonic() < deadline:
        response = client.get_job_attributes(job_id)
        if response.status == JOB_NOT_FOUND:  # job already purged: assume it printed
            return "completed", "completed", "job no longer listed by the printer"
        if not response.ok:
            return "unknown", "unknown", f"Get-Job-Attributes failed: {response.status_name}"
        job = response.group("job")
        state = JOB_STATES.get(_enum((job.get("job-state") or [None])[0]), "unknown")
        reasons = [_text(r) for r in job.get("job-state-reasons", [])
                   if isinstance(r, str) and r and r != "none"]
        detail = ", ".join(reasons)
        if state in FINAL_JOB_STATES:
            return state, state, detail
        if not wait(POLL_EVERY):
            return "stopped", state, "dont-dry stopped before the job finished"
    return ("timeout", state,
            f"still {state} after {timeout}s" + (f" ({detail})" if detail else ""))


def print_page(cfg: Config, *, wait: Wait = plain_wait) -> PrintResult:
    """Sends one page and follows the job. Failures come back as a PrintResult, not raised."""
    client = IPPClient(cfg.require_printer(), verify_tls=cfg.verify_tls)
    try:
        info = inspect(client)
    except IPPError as exc:
        return PrintResult(False, str(exc), retryable=not isinstance(exc, IPPRefused),
                           reason="unreachable")

    log.info("printer: %s (%s), state %s%s", info.name or "?", info.model or "?", info.state,
             f" [{', '.join(info.problems)}]" if info.problems else "")
    if not info.accepting_jobs:
        reasons = f" ({', '.join(info.problems)})" if info.problems else ""
        return PrintResult(False, f"printer is not accepting jobs{reasons}", printer=info,
                           retryable=True, reason="not_accepting")

    try:
        fmt = choose_format(info, cfg.document_format)
    except PrintError as exc:
        return PrintResult(False, str(exc), printer=info, reason="no_format")
    try:
        document = build_document(cfg, info, fmt)
    except (PrintError, OSError) as exc:
        return PrintResult(False, str(exc), document_format=fmt, printer=info,
                           reason="bad_document")

    log.info("sending %s (%d KB)", fmt, len(document) // 1024)
    try:
        response = client.print_job(document, fmt, cfg.page_title, _job_attributes(cfg, info))
    except IPPAnswerLost as exc:
        # The whole page went out: the printer may be printing it, so it is never sent again
        return PrintResult(False, f"{exc}; the page may have printed, so it was not sent again",
                           document_format=fmt, printer=info, reason="answer_lost")
    except IPPRefused as exc:
        return PrintResult(False, f"printer refused the job: {exc}", document_format=fmt,
                           printer=info, reason="rejected")
    except IPPError as exc:
        return PrintResult(False, str(exc), document_format=fmt, printer=info, retryable=True,
                           reason="unreachable")
    if not response.ok:
        detail = f" ({_text(response.status_message)})" if response.status_message else ""
        return PrintResult(False, f"printer rejected the job: {response.status_name}{detail}",
                           document_format=fmt, printer=info,
                           retryable=response.status in RETRYABLE_STATUSES, reason="rejected")

    job_id = (response.group("job").get("job-id") or [None])[0]
    if job_id is None:
        return PrintResult(True, "job accepted (printer returned no job id)",
                           document_format=fmt, printer=info, reason="accepted")
    log.info("job %s accepted, waiting for it to finish", job_id)
    try:
        reason, state, detail = _wait_for_job(client, job_id, cfg.job_timeout, wait)
    except IPPError as exc:
        return PrintResult(True, f"job {job_id} accepted; could not follow it: {exc}",
                           job_id, fmt, info, reason="accepted")
    message = f"job {job_id} {state}" + (f" ({detail})" if detail else "")
    return PrintResult(reason == "completed", message, job_id, fmt, info, reason=reason)
