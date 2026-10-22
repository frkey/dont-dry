"""Optional notifications: a plain-text POST, compatible with ntfy.sh and
most generic webhooks."""

from __future__ import annotations

import base64
import logging
import urllib.request

log = logging.getLogger(__name__)


def _header(value: str) -> str:
    """HTTP headers are Latin-1 on the wire, so anything else goes as an RFC 2047 encoded word
    (ntfy decodes those): "impressão" would otherwise arrive garbled."""
    if value.isascii():
        return value
    return "=?utf-8?b?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """urllib would follow a redirect as a GET without the body but with the Authorization
    header, to whatever host it names: refuse instead (a 3xx becomes an HTTPError)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def send(url: str, title: str, message: str, *, token: str = "", failed: bool = False) -> bool:
    """Posts the notification; True when the server accepted it. Never raises."""
    if not url:
        return False
    headers = {
        "Title": _header(title),
        "Tags": "warning" if failed else "printer",
        "Priority": "high" if failed else "default",
        "Content-Type": "text/plain; charset=utf-8",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers,
                                     method="POST")
    try:
        with _opener.open(request, timeout=15) as response:
            response.read()
    except Exception as exc:  # notifications must never break printing
        log.warning("could not send notification: %s", exc)
        return False
    return True
