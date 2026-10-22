"""Minimal IPP/1.1 client (RFC 8010 / 8011) using only the standard library.

Supports just what dont-dry needs: Get-Printer-Attributes, Print-Job and
Get-Job-Attributes over ipp:// (HTTP) or ipps:// (HTTPS).
"""

from __future__ import annotations

import contextlib
import http.client
import socket
import ssl
import struct
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# A printer's answer to the attributes dont-dry asks for is a few KB; anything this big is broken
MAX_RESPONSE = 4 * 1024 * 1024
DEFAULT_PATH = "/ipp/print"

# Operation ids
OP_PRINT_JOB = 0x0002
OP_GET_JOB_ATTRIBUTES = 0x0009
OP_GET_PRINTER_ATTRIBUTES = 0x000B

# Delimiter tags
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED_GROUP = 0x05

# Value tags
TAG_UNSUPPORTED = 0x10
TAG_UNKNOWN = 0x12
TAG_NO_VALUE = 0x13
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_OCTET_STRING = 0x30
TAG_DATETIME = 0x31
TAG_RESOLUTION = 0x32
TAG_RANGE = 0x33
TAG_BEGIN_COLLECTION = 0x34
TAG_TEXT_LANG = 0x35
TAG_NAME_LANG = 0x36
TAG_END_COLLECTION = 0x37
TAG_TEXT = 0x41
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_URI_SCHEME = 0x46
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MIME = 0x49
TAG_MEMBER_NAME = 0x4A

GROUP_NAMES = {
    TAG_OPERATION: "operation",
    TAG_JOB: "job",
    TAG_PRINTER: "printer",
    TAG_UNSUPPORTED_GROUP: "unsupported",
}

STATUS_NAMES = {
    0x0000: "successful-ok",
    0x0001: "successful-ok-ignored-or-substituted-attributes",
    0x0002: "successful-ok-conflicting-attributes",
    0x0400: "client-error-bad-request",
    0x0401: "client-error-forbidden",
    0x0402: "client-error-not-authenticated",
    0x0403: "client-error-not-authorized",
    0x0404: "client-error-not-possible",
    0x0405: "client-error-timeout",
    0x0406: "client-error-not-found",
    0x040A: "client-error-document-format-not-supported",
    0x040B: "client-error-attributes-or-values-not-supported",
    0x0500: "server-error-internal-error",
    0x0501: "server-error-operation-not-supported",
    0x0502: "server-error-service-unavailable",
    0x0503: "server-error-version-not-supported",
    0x0504: "server-error-device-error",
    0x0505: "server-error-temporary-error",
    0x0506: "server-error-not-accepting-jobs",
    0x0507: "server-error-busy",
}

JOB_STATES = {
    3: "pending",
    4: "pending-held",
    5: "processing",
    6: "processing-stopped",
    7: "canceled",
    8: "aborted",
    9: "completed",
}

PRINTER_STATES = {3: "idle", 4: "processing", 5: "stopped"}


class IPPError(Exception):
    """Transport or protocol failure talking to the printer."""


class IPPAnswerLost(IPPError):
    """The request went out, but no usable answer came back: the printer may have acted on it
    (for Print-Job, the page may be printing)."""


class IPPRefused(IPPError):
    """The printer's web server refused the request (an HTTP 4xx such as 401 for a login):
    nothing was done, and trying again would not help."""


@dataclass(frozen=True)
class Resolution:
    x: int
    y: int
    units: int  # 3 = dots per inch, 4 = dots per cm

    @property
    def dpi(self) -> tuple[int, int]:
        if self.units == 4:
            return round(self.x * 2.54), round(self.y * 2.54)
        return self.x, self.y

    def __str__(self) -> str:
        x, y = self.dpi
        return f"{x}dpi" if x == y else f"{x}x{y}dpi"


@dataclass
class Attr:
    """An attribute to send: name, value tag and one or more values."""

    name: str
    tag: int
    values: list[Any]

    def __init__(self, name: str, tag: int, *values: Any):
        self.name = name
        self.tag = tag
        self.values = list(values)


@dataclass
class Response:
    status: int
    request_id: int
    groups: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status < 0x0100

    @property
    def status_name(self) -> str:
        return STATUS_NAMES.get(self.status, f"0x{self.status:04x}")

    def group(self, name: str) -> dict[str, Any]:
        """All attributes of the first group with this name (empty if absent)."""
        for group_name, attrs in self.groups:
            if group_name == name:
                return attrs
        return {}

    @property
    def status_message(self) -> str | None:
        msg = self.group("operation").get("status-message")
        return msg[0] if msg else None


# --------------------------------------------------------------------------
# Encoding


def _encode_value(tag: int, value: Any) -> bytes:
    if tag in (TAG_INTEGER, TAG_ENUM):
        return struct.pack(">i", int(value))
    if tag == TAG_BOOLEAN:
        return b"\x01" if value else b"\x00"
    if tag == TAG_RESOLUTION:
        res: Resolution = value
        return struct.pack(">iib", res.x, res.y, res.units)
    if tag == TAG_RANGE:
        lo, hi = value
        return struct.pack(">ii", lo, hi)
    if tag in (TAG_NO_VALUE, TAG_UNKNOWN, TAG_UNSUPPORTED):
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def _encode_attr(attr: Attr) -> bytes:
    out = bytearray()
    for index, value in enumerate(attr.values):
        name = attr.name.encode("ascii") if index == 0 else b""
        data = _encode_value(attr.tag, value)
        out += struct.pack(">BH", attr.tag, len(name)) + name
        out += struct.pack(">H", len(data)) + data
    return bytes(out)


def encode_request(
    operation: int,
    request_id: int,
    operation_attrs: list[Attr],
    job_attrs: list[Attr] | None = None,
    version: tuple[int, int] = (1, 1),
) -> bytes:
    out = bytearray(struct.pack(">BBHi", version[0], version[1], operation, request_id))
    out.append(TAG_OPERATION)
    for attr in operation_attrs:
        out += _encode_attr(attr)
    if job_attrs:
        out.append(TAG_JOB)
        for attr in job_attrs:
            out += _encode_attr(attr)
    out.append(TAG_END)
    return bytes(out)


# --------------------------------------------------------------------------
# Decoding


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise IPPError("truncated IPP response")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def peek(self) -> int:
        if self.pos >= len(self.data):
            raise IPPError("truncated IPP response")
        return self.data[self.pos]


def _decode_value(tag: int, raw: bytes) -> Any:
    if tag in (TAG_INTEGER, TAG_ENUM):
        return struct.unpack(">i", raw)[0] if len(raw) == 4 else None
    if tag == TAG_BOOLEAN:
        return bool(raw[0]) if raw else None
    if tag == TAG_RESOLUTION and len(raw) == 9:
        x, y, units = struct.unpack(">iib", raw)
        return Resolution(x, y, units)
    if tag == TAG_RANGE and len(raw) == 8:
        return struct.unpack(">ii", raw)
    if tag in (TAG_TEXT_LANG, TAG_NAME_LANG) and len(raw) >= 4:
        lang_len = struct.unpack(">H", raw[:2])[0]
        text_len = struct.unpack(">H", raw[2 + lang_len : 4 + lang_len])[0]
        return raw[4 + lang_len : 4 + lang_len + text_len].decode("utf-8", "replace")
    if tag in (TAG_NO_VALUE, TAG_UNKNOWN, TAG_UNSUPPORTED):
        return None
    if tag in (TAG_OCTET_STRING, TAG_DATETIME):
        return raw
    return raw.decode("utf-8", "replace")


def _read_collection(reader: _Reader) -> dict[str, Any]:
    """Read collection members after a begCollection value, up to endCollection."""
    result: dict[str, Any] = {}
    member: str | None = None
    while True:
        tag = reader.u8()
        reader.take(reader.u16())  # names inside collections are always empty
        raw = reader.take(reader.u16())
        if tag == TAG_END_COLLECTION:
            return result
        if tag == TAG_MEMBER_NAME:
            member = raw.decode("utf-8", "replace")
            result[member] = []
            continue
        value = _read_collection(reader) if tag == TAG_BEGIN_COLLECTION else _decode_value(tag, raw)
        if member is not None:
            result[member].append(value)


def decode_response(data: bytes) -> Response:
    return decode_message(data)[0]


def decode_message(data: bytes) -> tuple[Response, int]:
    """Decode an IPP message. Returns it plus the offset where attached data
    (the document, for requests) begins. For requests, `status` holds the
    operation id. Raises IPPError for anything malformed."""
    try:
        return _decode_message(data)
    except (struct.error, IndexError, RecursionError) as exc:
        raise IPPError(f"malformed IPP message: {exc}") from None


def _decode_message(data: bytes) -> tuple[Response, int]:
    reader = _Reader(data)
    reader.take(2)  # version
    status = reader.u16()
    request_id = struct.unpack(">i", reader.take(4))[0]
    response = Response(status, request_id)
    current: dict[str, Any] | None = None
    last_name: str | None = None
    while True:
        tag = reader.u8()
        if tag == TAG_END:
            break
        if tag < 0x10:  # new attribute group
            current = {}
            response.groups.append((GROUP_NAMES.get(tag, f"group-{tag}"), current))
            last_name = None
            continue
        name = reader.take(reader.u16()).decode("utf-8", "replace")
        raw = reader.take(reader.u16())
        value = _read_collection(reader) if tag == TAG_BEGIN_COLLECTION else _decode_value(tag, raw)
        if current is None:
            raise IPPError("attribute outside of a group")
        if name:
            last_name = name
            current[name] = [value]
        elif last_name is not None:
            current[last_name].append(value)
    return response, reader.pos


# --------------------------------------------------------------------------
# Client


class IPPClient:
    def __init__(self, uri: str, *, verify_tls: bool = False, timeout: float = 60.0,
                 deadline: float = 120.0, user: str = "dont-dry"):
        """`timeout` limits each network operation; `deadline`, a whole request."""
        parts = urlsplit(uri)
        if parts.scheme not in ("ipp", "ipps", "http", "https"):
            raise ValueError(f"unsupported printer URI scheme: {uri!r} (use ipp:// or ipps://)")
        if not parts.hostname:
            raise ValueError(f"printer URI has no host: {uri!r}")
        self.path = parts.path or DEFAULT_PATH
        # printer-uri must name the same resource the request is posted to
        self.uri = urlunsplit(parts._replace(path=self.path))
        self.secure = parts.scheme in ("ipps", "https")
        default_port = 631 if parts.scheme in ("ipp", "ipps") else (443 if self.secure else 80)
        self.host = parts.hostname
        self.port = parts.port or default_port
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.deadline = deadline
        self.user = user
        self._request_id = 0

    def _connection(self) -> http.client.HTTPConnection:
        if self.secure:
            context = ssl.create_default_context()
            if not self.verify_tls:  # printers almost always use self-signed certs
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout,
                                               context=context)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def _base_attrs(self) -> list[Attr]:
        return [
            Attr("attributes-charset", TAG_CHARSET, "utf-8"),
            Attr("attributes-natural-language", TAG_LANGUAGE, "en"),
            Attr("printer-uri", TAG_URI, self.uri),
            Attr("requesting-user-name", TAG_NAME, self.user),
        ]

    def request(self, operation: int, operation_attrs: list[Attr],
                job_attrs: list[Attr] | None = None, document: bytes = b"") -> Response:
        self._request_id += 1
        body = encode_request(operation, self._request_id, operation_attrs, job_attrs) + document
        conn = self._connection()
        # A printer that answers a byte at a time never trips the per-operation timeout: past
        # the deadline, shut the socket down, which wakes up whatever is blocked on it
        expired = threading.Event()
        timer = threading.Timer(self.deadline, self._expire, (conn, expired))
        timer.daemon = True
        timer.start()
        try:
            try:
                conn.connect()  # nothing was sent yet: a failure here is safe to retry
            except (OSError, http.client.HTTPException) as exc:
                raise IPPError(f"cannot reach printer at {self.host}:{self.port}: {exc}") from exc
            try:
                conn.request("POST", self.path, body=body, headers={
                    "Content-Type": "application/ipp",
                    "Content-Length": str(len(body)),
                })
                http_response = conn.getresponse()
                payload = http_response.read(MAX_RESPONSE + 1)
            except (OSError, http.client.HTTPException) as exc:
                why = f"no complete answer within {self.deadline:g} s" if expired.is_set() else exc
                raise IPPAnswerLost(f"lost the printer's answer ({self.host}:{self.port}): "
                                    f"{why}") from exc
        finally:
            timer.cancel()
            conn.close()
        if 400 <= http_response.status < 500 and http_response.status != 408:
            raise IPPRefused(f"printer answered HTTP {http_response.status} "
                             f"{http_response.reason}")
        if http_response.status != 200:
            raise IPPAnswerLost(f"printer answered HTTP {http_response.status} "
                                f"{http_response.reason}")
        if len(payload) > MAX_RESPONSE:
            raise IPPAnswerLost(f"printer answer is larger than {MAX_RESPONSE // 1024 // 1024} MB")
        try:
            return decode_response(payload)
        except IPPError as exc:
            raise IPPAnswerLost(str(exc)) from None

    @staticmethod
    def _expire(conn: http.client.HTTPConnection, expired: threading.Event) -> None:
        expired.set()
        sock = conn.sock  # once: the request may be closing it right now
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    # -- operations ---------------------------------------------------------

    def get_printer_attributes(self, requested: list[str] | None = None) -> dict[str, Any]:
        attrs = self._base_attrs()
        attrs.append(Attr("requested-attributes", TAG_KEYWORD, *(requested or ["all"])))
        response = self.request(OP_GET_PRINTER_ATTRIBUTES, attrs)
        if not response.ok:
            raise IPPError(f"Get-Printer-Attributes failed: {response.status_name}"
                           + (f" ({response.status_message})" if response.status_message else ""))
        return response.group("printer")

    def print_job(self, document: bytes, document_format: str, job_name: str,
                  job_attrs: list[Attr] | None = None) -> Response:
        attrs = self._base_attrs()
        attrs.append(Attr("job-name", TAG_NAME, job_name))
        attrs.append(Attr("document-format", TAG_MIME, document_format))
        return self.request(OP_PRINT_JOB, attrs, job_attrs, document)

    def get_job_attributes(self, job_id: int) -> Response:
        attrs = self._base_attrs()
        attrs.insert(3, Attr("job-id", TAG_INTEGER, job_id))
        attrs.append(Attr("requested-attributes", TAG_KEYWORD,
                          "job-state", "job-state-reasons", "job-state-message"))
        return self.request(OP_GET_JOB_ATTRIBUTES, attrs)
