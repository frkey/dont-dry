"""Renders the maintenance test page.

The page exercises every ink channel: solid blocks, gradients, mixed colors
and fine nozzle-check lines (gaps in these lines reveal clogged nozzles).
"""

from __future__ import annotations

import io
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from .i18n import DEFAULT_LOCALE, Translator

# Page sizes in inches
PAGE_SIZES = {
    "a4": (8.27, 11.69),
    "letter": (8.5, 11.0),
}

CYAN = (0, 174, 239)
MAGENTA = (236, 0, 140)
YELLOW = (255, 242, 0)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREY = (110, 110, 110)

# (catalog key of the label, color)
CHANNELS = [("page.cyan", CYAN), ("page.magenta", MAGENTA), ("page.yellow", YELLOW),
            ("page.black", BLACK)]
MIXES = [("page.red", (237, 28, 36)), ("page.green", (0, 166, 81)),
         ("page.blue", (46, 49, 146)), ("page.composite_black", (35, 31, 32))]

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in BOLD_CANDIDATES if bold else FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _blend(color: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    """Mix `color` with white: t=0 -> white, t=1 -> full color."""
    return tuple(round(255 + (c - 255) * t) for c in color)  # type: ignore[return-value]


def render(*, dpi: int = 300, page_size: str = "a4", title: str | None = None,
           when: datetime | None = None, locale: str = DEFAULT_LOCALE) -> Image.Image:
    t = Translator(locale)
    width_in, height_in = PAGE_SIZES[page_size]
    width, height = round(width_in * dpi), round(height_in * dpi)
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)

    def px(inches: float) -> int:
        return round(inches * dpi)

    margin = px(0.6)
    usable = width - 2 * margin
    when = when or datetime.now()

    # Title
    y = px(0.6)
    draw.text((margin, y), title or t("page.title"), fill=BLACK, font=_font(px(0.22), bold=True))
    y += px(0.36)
    draw.text((margin, y), t("page.subtitle", when=t.datetime(when)), fill=GREY,
              font=_font(px(0.11)))
    y += px(0.4)

    label_font = _font(px(0.1))
    gap = px(0.15)

    # 1. Solid blocks, one per ink
    block_w = (usable - 3 * gap) // 4
    block_h = px(1.0)
    for i, (label, color) in enumerate(CHANNELS):
        x = margin + i * (block_w + gap)
        draw.rectangle([x, y, x + block_w, y + block_h], fill=color)
        draw.text((x, y + block_h + px(0.05)), t(label), fill=BLACK, font=label_font)
    y += block_h + px(0.4)

    # 2. Gradients, one bar per ink
    steps = 20
    bar_h = px(0.28)
    step_w = usable / steps
    for _, color in CHANNELS:
        for s in range(steps):
            x0 = margin + round(s * step_w)
            x1 = margin + round((s + 1) * step_w)
            draw.rectangle([x0, y, x1, y + bar_h], fill=_blend(color, (s + 1) / steps))
        y += bar_h + px(0.08)
    y += px(0.3)

    # 3. Mixed colors
    mix_h = px(0.65)
    for i, (label, color) in enumerate(MIXES):
        x = margin + i * (block_w + gap)
        draw.rectangle([x, y, x + block_w, y + mix_h], fill=color)
        draw.text((x, y + mix_h + px(0.05)), t(label), fill=BLACK, font=label_font)
    y += mix_h + px(0.45)

    # 4. Nozzle-check lines
    draw.text((margin, y), t("page.nozzle_check"), fill=BLACK, font=label_font)
    y += px(0.22)
    line_w = max(1, round(dpi / 150))
    spacing = px(0.08)
    line_h = px(0.3)
    for _, color in CHANNELS:
        x = margin
        while x < margin + usable:
            draw.rectangle([x, y, x + line_w - 1, y + line_h], fill=color)
            x += spacing
        y += line_h + px(0.12)

    return image


# Pillow stores an RGB page in a PDF as a JPEG too. JPEG's default 4:2:0 chroma subsampling
# halves the color resolution and smears the thin colored lines the nozzle check depends on
# (yellow on white is almost all chroma), so both formats keep full color resolution.
JPEG_OPTIONS = {"quality": 95, "subsampling": 0}


def to_pdf(image: Image.Image, dpi: int) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PDF", resolution=dpi, **JPEG_OPTIONS)
    return buf.getvalue()


def to_jpeg(image: Image.Image, dpi: int) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", dpi=(dpi, dpi), **JPEG_OPTIONS)
    return buf.getvalue()
