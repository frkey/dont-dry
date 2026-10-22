import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dont_dry import notify


@pytest.fixture
def webhook():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((dict(self.headers), body.decode()))
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/topic", received
    server.shutdown()
    server.server_close()


def test_sends_an_ntfy_style_post(webhook):
    url, received = webhook
    notify.send(url, "dont-dry: print FAILED", "job 1 aborted\nInk: Black: 3%", token="s3cret",
                failed=True)
    ((headers, body),) = received
    assert headers["Title"] == "dont-dry: print FAILED"
    assert headers["Priority"] == "high" and headers["Tags"] == "warning"
    assert headers["Authorization"] == "Bearer s3cret"
    assert body == "job 1 aborted\nInk: Black: 3%"


def test_no_token_means_no_authorization_header(webhook):
    url, received = webhook
    notify.send(url, "dont-dry: page printed", "ok")
    assert "Authorization" not in received[0][0]


def test_a_failed_notification_never_raises(caplog):
    notify.send("http://127.0.0.1:1/topic", "title", "message", token="s3cret")
    assert "could not send notification" in caplog.text
    assert "s3cret" not in caplog.text


def test_a_non_ascii_title_is_sent_as_an_encoded_word(webhook):
    from email.header import decode_header
    url, received = webhook
    notify.send(url, "dont-dry: a impressão FALHOU", "Detalhes: ...")
    title = received[0][0]["Title"]
    assert title.isascii() and title.startswith("=?utf-8?b?")
    ((raw, charset),) = decode_header(title)
    assert raw.decode(charset) == "dont-dry: a impressão FALHOU"


def test_redirects_are_not_followed(webhook, caplog):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    target, received = webhook

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sent = notify.send(f"http://127.0.0.1:{server.server_address[1]}/topic", "t", "m",
                           token="s3cret")
    finally:
        server.shutdown()
        server.server_close()
    assert sent is False
    assert received == []                          # the token never reached the target
    assert "302" in caplog.text and "s3cret" not in caplog.text


def test_send_says_whether_the_server_took_it(webhook):
    url, _ = webhook
    assert notify.send(url, "t", "m") is True
    assert notify.send("http://127.0.0.1:1/topic", "t", "m") is False
