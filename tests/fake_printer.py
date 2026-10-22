"""A tiny in-process IPP printer for tests.

Run on its own for manual tests and CI, it listens until stopped and prints each job:

    python -m tests.fake_printer [PORT] [HOST]    # default 8631 on 127.0.0.1
"""

from __future__ import annotations

import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dont_dry import ipp
from dont_dry.ipp import Attr, Resolution


def encode_response(status: int, request_id: int,
                    groups: list[tuple[int, list[Attr]]]) -> bytes:
    out = bytearray(struct.pack(">BBHi", 1, 1, status, request_id))
    for tag, attrs in groups:
        out.append(tag)
        for attr in attrs:
            out += ipp._encode_attr(attr)
    out.append(ipp.TAG_END)
    return bytes(out)


class FakePrinter:
    def __init__(self, formats=("application/pdf", "image/pwg-raster", "image/jpeg"),
                 pwg_types=("srgb_8", "sgray_8"), accepting=True, final_state=9,
                 reject_status=None, host="127.0.0.1", port=0, on_job=None,
                 drop_print_answer=False):
        self.formats = list(formats)
        self.pwg_types = list(pwg_types)
        self.accepting = accepting
        self.final_state = final_state
        self.reject_status = reject_status
        self.on_job = on_job  # called with each job before Print-Job is answered
        self.drop_print_answer = drop_print_answer  # keep the job, then hang up without answering
        self.jobs: list[dict] = []
        self.requests: list[int] = []
        self.printer_uris: list[str] = []

        printer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                request, offset = ipp.decode_message(body)
                payload = printer.handle(request, body[offset:])
                if printer.drop_print_answer and request.status == ipp.OP_PRINT_JOB:
                    self.close_connection = True
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/ipp")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def uri(self) -> str:
        host, port = self.server.server_address[:2]
        return f"ipp://{host}:{port}/ipp/print"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def _operation_group(self) -> tuple[int, list[Attr]]:
        return (ipp.TAG_OPERATION, [
            Attr("attributes-charset", ipp.TAG_CHARSET, "utf-8"),
            Attr("attributes-natural-language", ipp.TAG_LANGUAGE, "en"),
        ])

    def handle(self, request: ipp.Response, document: bytes) -> bytes:
        op = request.status
        self.requests.append(op)
        self.printer_uris.append(request.group("operation")["printer-uri"][0])
        rid = request.request_id
        if op == ipp.OP_GET_PRINTER_ATTRIBUTES:
            return encode_response(0, rid, [self._operation_group(), (ipp.TAG_PRINTER, [
                Attr("printer-name", ipp.TAG_NAME, "Fake Tank"),
                Attr("printer-make-and-model", ipp.TAG_TEXT, "HP Fake Tank 720"),
                Attr("printer-state", ipp.TAG_ENUM, 3),
                Attr("printer-state-reasons", ipp.TAG_KEYWORD, "none"),
                Attr("printer-is-accepting-jobs", ipp.TAG_BOOLEAN, self.accepting),
                Attr("document-format-supported", ipp.TAG_MIME, *self.formats),
                Attr("pwg-raster-document-resolution-supported", ipp.TAG_RESOLUTION,
                     Resolution(300, 300, 3), Resolution(600, 600, 3)),
                Attr("pwg-raster-document-type-supported", ipp.TAG_KEYWORD, *self.pwg_types),
                Attr("media-supported", ipp.TAG_KEYWORD, "iso_a4_210x297mm",
                     "na_letter_8.5x11in"),
                Attr("print-color-mode-supported", ipp.TAG_KEYWORD, "monochrome", "color"),
                Attr("marker-names", ipp.TAG_NAME, "Cyan", "Magenta", "Yellow", "Black"),
                Attr("marker-levels", ipp.TAG_INTEGER, 80, 5, 60, -3),
                Attr("marker-colors", ipp.TAG_NAME, "#00FFFF", "#FF00FF", "#FFFF00", "#000000"),
            ])])
        if op == ipp.OP_PRINT_JOB:
            if self.reject_status:
                return encode_response(self.reject_status, rid, [self._operation_group()])
            job_id = len(self.jobs) + 1
            job = {
                "id": job_id,
                "format": request.group("operation")["document-format"][0],
                "job_attrs": request.group("job"),
                "document": document,
            }
            self.jobs.append(job)
            if self.on_job:
                self.on_job(job)
            return encode_response(0, rid, [self._operation_group(), (ipp.TAG_JOB, [
                Attr("job-id", ipp.TAG_INTEGER, job_id),
                Attr("job-state", ipp.TAG_ENUM, 3),
            ])])
        if op == ipp.OP_GET_JOB_ATTRIBUTES:
            return encode_response(0, rid, [self._operation_group(), (ipp.TAG_JOB, [
                Attr("job-state", ipp.TAG_ENUM, self.final_state),
                Attr("job-state-reasons", ipp.TAG_KEYWORD, "job-completed-successfully"
                     if self.final_state == 9 else "aborted-by-system"),
            ])])
        return encode_response(0x0501, rid, [self._operation_group()])


if __name__ == "__main__":
    import sys
    import threading

    def report(job: dict) -> None:
        print(f"job {job['id']}: {job['format']}, {len(job['document'])} bytes", flush=True)

    printer = FakePrinter(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8631,
                          host=sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1", on_job=report)
    with printer:
        print(f"fake printer at {printer.uri}", flush=True)
        threading.Event().wait()  # until stopped
