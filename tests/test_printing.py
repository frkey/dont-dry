import pytest

from dont_dry import ipp, pwg
from dont_dry.config import Config
from dont_dry.printer import print_page

from .fake_printer import FakePrinter


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    monkeypatch.setenv("JOB_TIMEOUT", "10")

    def make(printer: FakePrinter, **extra) -> Config:
        monkeypatch.setenv("PRINTER_URI", printer.uri)
        for key, value in extra.items():
            monkeypatch.setenv(key, value)
        return Config.from_env()

    return make


def test_prefers_pdf_and_sends_job_attributes(env):
    with FakePrinter() as printer:
        result = print_page(env(printer))
    assert result.success, result.message
    assert result.reason == "completed"
    job = printer.jobs[0]
    assert job["format"] == "application/pdf"
    assert job["document"].startswith(b"%PDF")
    assert job["job_attrs"]["media"] == ["iso_a4_210x297mm"]
    assert job["job_attrs"]["print-color-mode"] == ["color"]
    assert [m.level for m in result.printer.markers] == [80, 5, 60, None]


def test_falls_back_to_color_pwg_raster(env):
    with FakePrinter(formats=["image/pwg-raster", "image/urf"]) as printer:
        result = print_page(env(printer))
    assert result.success, result.message
    job = printer.jobs[0]
    assert job["format"] == "image/pwg-raster"
    (decoded,) = pwg.decode(job["document"])
    assert decoded.dpi == 300
    assert decoded.color_space == pwg.COLOR_SPACE_SRGB
    assert decoded.page_size_name == "iso_a4_210x297mm"


def test_letter_page_size(env):
    with FakePrinter(formats=["image/pwg-raster"]) as printer:
        result = print_page(env(printer, PAGE_SIZE="letter"))
    assert result.success
    (decoded,) = pwg.decode(printer.jobs[0]["document"])
    assert (decoded.width, decoded.height) == (2550, 3300)
    assert printer.jobs[0]["job_attrs"]["media"] == ["na_letter_8.5x11in"]


def test_forced_jpeg(env):
    with FakePrinter() as printer:
        result = print_page(env(printer, DOCUMENT_FORMAT="image/jpeg"))
    assert result.success
    assert printer.jobs[0]["document"][:2] == b"\xff\xd8"


def test_no_usable_format(env):
    with FakePrinter(formats=["image/urf"]) as printer:
        result = print_page(env(printer))
    assert not result.success
    assert "supports none" in result.message
    assert result.reason == "no_format"
    assert printer.jobs == []


def test_not_accepting_jobs(env):
    with FakePrinter(accepting=False) as printer:
        result = print_page(env(printer))
    assert not result.success
    assert printer.jobs == []


def test_rejected_job(env):
    with FakePrinter(reject_status=0x0506) as printer:
        result = print_page(env(printer))
    assert not result.success
    assert "server-error-not-accepting-jobs" in result.message
    assert result.reason == "rejected"


def test_aborted_job(env):
    with FakePrinter(final_state=8) as printer:
        result = print_page(env(printer))
    assert not result.success
    assert result.job_id == 1
    assert "aborted" in result.message
    assert result.reason == "aborted"


def test_unreachable_printer(env, monkeypatch, tmp_path):
    monkeypatch.setenv("PRINTER_URI", "ipp://127.0.0.1:1/ipp/print")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    result = print_page(Config.from_env())
    assert not result.success
    assert "cannot reach printer" in result.message
    assert result.reason == "unreachable"


def test_custom_png_page(env, tmp_path):
    from PIL import Image
    custom = tmp_path / "logo.png"
    Image.new("RGB", (400, 200), (255, 0, 0)).save(custom)
    with FakePrinter(formats=["image/jpeg"]) as printer:
        result = print_page(env(printer, CUSTOM_PAGE=str(custom)))
    assert result.success
    assert printer.jobs[0]["format"] == "image/jpeg"


def test_ipp_roundtrip_with_collection():
    attrs = [ipp.Attr("attributes-charset", ipp.TAG_CHARSET, "utf-8"),
             ipp.Attr("copies", ipp.TAG_INTEGER, 2),
             ipp.Attr("print-scaling", ipp.TAG_KEYWORD, "fit", "fill")]
    message = ipp.encode_request(ipp.OP_PRINT_JOB, 7, attrs) + b"DOC"
    decoded, offset = ipp.decode_message(message)
    assert decoded.status == ipp.OP_PRINT_JOB and decoded.request_id == 7
    assert decoded.group("operation")["print-scaling"] == ["fit", "fill"]
    assert message[offset:] == b"DOC"

    # media-col = { media-size = { x-dimension = 21000 } }
    def raw(tag, name, value=b""):
        n = name.encode()
        return bytes([tag]) + len(n).to_bytes(2, "big") + n + len(value).to_bytes(2, "big") + value
    body = (b"\x01\x01\x00\x00\x00\x00\x00\x01" + b"\x04"
            + raw(ipp.TAG_BEGIN_COLLECTION, "media-col")
            + raw(ipp.TAG_MEMBER_NAME, "", b"media-size")
            + raw(ipp.TAG_BEGIN_COLLECTION, "")
            + raw(ipp.TAG_MEMBER_NAME, "", b"x-dimension")
            + raw(ipp.TAG_INTEGER, "", (21000).to_bytes(4, "big"))
            + raw(ipp.TAG_END_COLLECTION, "")
            + raw(ipp.TAG_END_COLLECTION, "")
            + raw(ipp.TAG_KEYWORD, "after", b"ok")
            + b"\x03")
    response = ipp.decode_response(body)
    printer = response.group("printer")
    assert printer["media-col"] == [{"media-size": [{"x-dimension": [21000]}]}]
    assert printer["after"] == ["ok"]


# -- retries --------------------------------------------------------------------


def test_only_failures_before_a_job_are_retryable(env, monkeypatch):
    monkeypatch.setenv("PRINTER_URI", "ipp://127.0.0.1:1/ipp/print")
    assert print_page(Config.from_env()).retryable              # printer unreachable
    with FakePrinter(accepting=False) as printer:
        assert print_page(env(printer)).retryable               # may accept jobs later
    with FakePrinter(reject_status=0x0507) as printer:
        assert print_page(env(printer)).retryable               # server-error-busy
    with FakePrinter(reject_status=0x040A) as printer:
        assert not print_page(env(printer)).retryable           # format not supported
    with FakePrinter(formats=["image/urf"]) as printer:
        assert not print_page(env(printer)).retryable           # nothing dont-dry can send
    with FakePrinter(final_state=8) as printer:
        assert not print_page(env(printer)).retryable           # aborted: resending wastes ink


def test_stopping_ends_the_wait_for_a_job(env):
    with FakePrinter(final_state=5) as printer:  # stays "processing"
        result = print_page(env(printer), wait=lambda seconds: False)
    assert not result.success and not result.retryable
    assert "stopped before the job finished" in result.message
    assert result.reason == "stopped"


# -- documents ------------------------------------------------------------------


def test_pdf_keeps_full_color_resolution(env):
    import io

    from PIL import Image, JpegImagePlugin
    with FakePrinter() as printer:
        print_page(env(printer))
    pdf = printer.jobs[0]["document"]
    jpeg = Image.open(io.BytesIO(pdf[pdf.index(b"\xff\xd8"):pdf.rindex(b"\xff\xd9") + 2]))
    assert JpegImagePlugin.get_sampling(jpeg) == 0  # 4:4:4, no chroma subsampling


def test_custom_image_is_scaled_up_to_the_page(env, tmp_path):
    from PIL import Image
    custom = tmp_path / "small.png"
    Image.new("RGB", (100, 50), (255, 0, 0)).save(custom)
    with FakePrinter(formats=["image/pwg-raster"]) as printer:
        assert print_page(env(printer, CUSTOM_PAGE=str(custom))).success
    (decoded,) = pwg.decode(printer.jobs[0]["document"])
    middle = decoded.height // 2 * decoded.width * 3
    assert decoded.pixels[middle:middle + 3] == b"\xff\x00\x00"  # red reaches the left edge


def test_custom_pdf_needs_a_pdf_printer(env, tmp_path):
    custom = tmp_path / "page.pdf"
    custom.write_bytes(b"%PDF-1.4 fake")
    with FakePrinter(formats=["image/pwg-raster"]) as printer:
        result = print_page(env(printer, CUSTOM_PAGE=str(custom)))
    assert not result.success and "does not accept PDF" in result.message
    assert result.reason == "bad_document"


# -- IPP ------------------------------------------------------------------------


def test_printer_uri_without_path_gets_the_default_one(env, monkeypatch):
    with FakePrinter() as printer:
        uri = printer.uri.removesuffix("/ipp/print")
        monkeypatch.setenv("PRINTER_URI", uri)
        assert print_page(Config.from_env()).success
    assert set(printer.printer_uris) == {uri + "/ipp/print"}


@pytest.mark.parametrize("body", [
    b"\x01\x01\x00\x00\x00\x00",                                        # truncated header
    b"\x01\x01\x00\x00\x00\x00\x00\x01\x04\x21\x00\x01a\x00\x08",      # value cut short
    b"\x01\x01\x00\x00\x00\x00\x00\x01\x04\x35\x00\x01a\x00\x04\x00\x05ab\x03",  # bad text length
])
def test_malformed_responses_raise_ipp_errors(body):
    with pytest.raises(ipp.IPPError):
        ipp.decode_response(body)


def test_oversized_response_is_refused(env, monkeypatch):
    monkeypatch.setattr(ipp, "MAX_RESPONSE", 100)
    with FakePrinter() as printer:
        result = print_page(env(printer))
    assert not result.success and "larger than" in result.message


def test_a_job_that_never_finishes_times_out(env, monkeypatch):
    import time
    from types import SimpleNamespace

    from dont_dry import printer as printer_module
    clock = iter([0, 0])  # the deadline, one poll, then far past it
    monkeypatch.setattr(printer_module, "time",
                        SimpleNamespace(monotonic=lambda: next(clock, 1000), sleep=time.sleep))
    with FakePrinter(final_state=5) as printer:
        result = print_page(env(printer), wait=lambda seconds: True)
    assert not result.success and result.reason == "timeout"
    assert "still processing after 10s" in result.message


def test_fake_printer_reports_a_job_before_answering(env):
    # CI greps the standalone fake printer's log right after a print: the job must be reported
    # before the printer answers Print-Job, never later
    seen = []
    with FakePrinter(on_job=seen.append) as printer:
        result = print_page(env(printer))
        assert result.success
        assert [job["id"] for job in seen] == [1]


# -- a lost answer, odd attributes, slow printers -------------------------------------


def test_a_lost_print_job_answer_is_never_resent(env):
    from dont_dry import cli
    with FakePrinter(drop_print_answer=True) as printer:
        cfg = env(printer, RETRIES="3", RETRY_DELAY="0")
        result = cli._attempt(cfg, cfg.retries)
    assert len(printer.jobs) == 1                       # one page, even with three retries left
    assert not result.success and not result.retryable
    assert result.reason == "answer_lost" and result.reached_printer


def test_odd_attribute_types_are_unknown_not_a_crash():
    from dont_dry.printer import PrinterInfo
    info = PrinterInfo.from_attributes({
        "printer-state": [{"member": [1]}],
        "printer-state-reasons": [{"x": [1]}, "media-empty-error"],
        "printer-name": ["Evil\x1b[2J\x07Printer\nFAKE LOG LINE"],
        "printer-is-accepting-jobs": [True],
    })
    assert info.state == "unknown"
    assert info.state_reasons == ["media-empty-error"]
    assert info.name == "Evil[2JPrinterFAKE LOG LINE"   # no escape, bell or newline left


def test_odd_job_state_is_unknown_not_a_crash():
    from dont_dry.ipp import Response
    from dont_dry.printer import _wait_for_job

    class Client:
        def get_job_attributes(self, job_id):
            return Response(0, 1, [("job", {"job-state": [{"x": [9]}],
                                            "job-state-reasons": [{"y": [1]}, "none"]})])

    reason, state, _ = _wait_for_job(Client(), 1, timeout=10, wait=lambda seconds: False)
    assert (reason, state) == ("stopped", "unknown")


def test_a_printer_that_answers_too_slowly_hits_the_deadline():
    import socket
    import threading
    import time

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def drip():  # accepts, then sends one header byte every 0.1 s, forever
        conn, _ = server.accept()
        conn.recv(65536)
        with conn:
            try:
                while True:
                    conn.sendall(b"X")
                    time.sleep(0.1)
            except OSError:
                pass

    threading.Thread(target=drip, daemon=True).start()
    client = ipp.IPPClient(f"ipp://127.0.0.1:{server.getsockname()[1]}/ipp/print",
                           timeout=5, deadline=1)
    started = time.monotonic()
    with pytest.raises(ipp.IPPError, match="within 1 s"):
        client.get_printer_attributes(["printer-state"])
    assert time.monotonic() - started < 4
    server.close()
