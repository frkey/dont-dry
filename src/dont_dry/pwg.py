"""PWG Raster (PWG 5102.4) encoder and decoder.

PWG Raster is the one document format every IPP Everywhere printer must
accept, so it is the fallback when a printer does not take PDF.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from PIL import Image

SYNC = b"RaS2"
HEADER_SIZE = 1796

COLOR_SPACE_RGB = 1
COLOR_SPACE_SGRAY = 18
COLOR_SPACE_SRGB = 19

# document type keyword -> (PIL mode, PWG color space, bytes per pixel)
DOCUMENT_TYPES = {
    "srgb_8": ("RGB", COLOR_SPACE_SRGB, 3),
    "rgb_8": ("RGB", COLOR_SPACE_RGB, 3),
    "sgray_8": ("L", COLOR_SPACE_SGRAY, 1),
}

# PWG media size names for the page sizes dont-dry renders
PAGE_SIZE_NAMES = {
    "a4": "iso_a4_210x297mm",
    "letter": "na_letter_8.5x11in",
}


def _cstr(text: str, size: int = 64) -> bytes:
    data = text.encode("ascii")[: size - 1]
    return data + b"\x00" * (size - len(data))


def build_header(*, width: int, height: int, dpi: int, doc_type: str,
                 page_size_name: str, total_pages: int = 1) -> bytes:
    _, color_space, bpp = DOCUMENT_TYPES[doc_type]
    points_w = round(width * 72 / dpi)
    points_h = round(height * 72 / dpi)

    header = bytearray(HEADER_SIZE)
    header[0:64] = _cstr("PwgRaster")
    # 64 MediaColor, 128 MediaType, 192 PrintContentOptimize: left empty

    def put(offset: int, value: int) -> None:
        struct.pack_into(">I", header, offset, value)

    put(276, dpi)               # HWResolution x
    put(280, dpi)               # HWResolution y
    put(340, 1)                 # NumCopies
    put(352, points_w)          # PageSize width (points)
    put(356, points_h)          # PageSize height (points)
    put(372, width)             # Width (pixels)
    put(376, height)            # Height (pixels)
    put(384, 8)                 # BitsPerColor
    put(388, 8 * bpp)           # BitsPerPixel
    put(392, width * bpp)       # BytesPerLine
    put(396, 0)                 # ColorOrder: chunky
    put(400, color_space)       # ColorSpace
    put(420, bpp)               # NumColors
    put(452, total_pages)       # TotalPageCount
    put(456, 1)                 # CrossFeedTransform
    put(460, 1)                 # FeedTransform
    header[1732:1796] = _cstr(page_size_name)
    return bytes(header)


def _encode_line(row: bytes, bpp: int) -> bytes:
    """PackBits-style pixel compression used by PWG Raster."""
    out = bytearray()
    n_pixels = len(row) // bpp
    i = 0
    while i < n_pixels:
        start = i * bpp
        pixel = row[start : start + bpp]
        # length of the run of identical pixels starting at i
        run = 1
        while (i + run < n_pixels and run < 128
               and row[(i + run) * bpp : (i + run + 1) * bpp] == pixel):
            run += 1
        if run > 1 or i + 1 == n_pixels:
            out.append(run - 1)
            out += pixel
            i += run
            continue
        # literal block: gather pixels until a repeat begins (max 128)
        j = i
        while j < n_pixels and j - i < 128:
            here = row[j * bpp : (j + 1) * bpp]
            if j + 1 < n_pixels and row[(j + 1) * bpp : (j + 2) * bpp] == here:
                break
            j += 1
        count = j - i
        if count == 1:  # a single pixel must be coded as a run of one
            out.append(0)
            out += pixel
        else:
            out.append(257 - count)
            out += row[start : j * bpp]
        i = j
    return bytes(out)


def encode(image: Image.Image, *, dpi: int, doc_type: str = "srgb_8",
           page_size_name: str = PAGE_SIZE_NAMES["a4"]) -> bytes:
    """Encode a single-page image as a PWG Raster document."""
    mode, _, bpp = DOCUMENT_TYPES[doc_type]
    image = image.convert(mode)
    width, height = image.size
    data = image.tobytes()
    line_len = width * bpp

    out = bytearray(SYNC)
    out += build_header(width=width, height=height, dpi=dpi, doc_type=doc_type,
                        page_size_name=page_size_name)
    y = 0
    while y < height:
        row = data[y * line_len : (y + 1) * line_len]
        repeat = 1
        while (y + repeat < height and repeat < 256
               and data[(y + repeat) * line_len : (y + repeat + 1) * line_len] == row):
            repeat += 1
        out.append(repeat - 1)
        out += _encode_line(row, bpp)
        y += repeat
    return bytes(out)


@dataclass
class DecodedPage:
    width: int
    height: int
    dpi: int
    bits_per_pixel: int
    color_space: int
    page_size_name: str
    pixels: bytes


def _u32(header: bytes, offset: int) -> int:
    return struct.unpack_from(">I", header, offset)[0]


def decode(data: bytes) -> list[DecodedPage]:
    """Decode a PWG Raster stream (used by the tests to verify the encoder)."""
    if data[:4] != SYNC:
        raise ValueError("not a PWG Raster stream")
    pos = 4
    pages = []
    while pos < len(data):
        header = data[pos : pos + HEADER_SIZE]
        pos += HEADER_SIZE
        width, height, bpp_bits = _u32(header, 372), _u32(header, 376), _u32(header, 388)
        bpp = bpp_bits // 8
        line_len = width * bpp
        pixels = bytearray()
        y = 0
        while y < height:
            repeat = data[pos] + 1
            pos += 1
            line = bytearray()
            while len(line) < line_len:
                control = data[pos]
                pos += 1
                if control <= 127:
                    line += data[pos : pos + bpp] * (control + 1)
                    pos += bpp
                elif control >= 129:
                    count = 257 - control
                    line += data[pos : pos + count * bpp]
                    pos += count * bpp
                else:
                    raise ValueError("invalid control byte 128")
            if len(line) != line_len:
                raise ValueError("line overflow")
            pixels += bytes(line) * repeat
            y += repeat
        pages.append(DecodedPage(width, height, _u32(header, 276), bpp_bits, _u32(header, 400),
                                 header[1732:1796].rstrip(b"\x00").decode(), bytes(pixels)))
    return pages
