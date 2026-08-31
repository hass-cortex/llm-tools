"""Generate the llm_tools brand assets.

    uv run --with cairosvg --with pillow python scripts/generate_brand.py

Rasterisers are not in the dev group on purpose: cairo is a system dependency
and nothing in CI needs it. Output is deterministic — re-running reproduces the
committed PNGs byte for byte.

Palette and layout are sampled from the sibling integrations' assets, which are
the only surviving source: primary #0078D4 / wordmark #1B2838 on light,
primary #339AF0 / wordmark #FFFFFF on dark; logo 850x200 with a 138px mark
inset 31px and the wordmark baseline at y=130.
"""

from __future__ import annotations

import io
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "custom_components/llm_tools/brand"
FONT = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"

LIGHT = {"mark": "#0078D4", "text": (0x1B, 0x28, 0x38, 255)}
DARK = {"mark": "#339AF0", "text": (0xFF, 0xFF, 0xFF, 255)}

# 3x3 lattice: the tool catalogue, with the one cell `call_tool` runs lit.
CELL, GAP, N = 64, 18, 3
SPAN = N * CELL + (N - 1) * GAP  # 228
ORIGIN = (256 - SPAN) / 2  # 14
RX, STROKE = 16, 8
# Alpha falls off from the lit centre outward, echoing the siblings' rings.
# Manhattan, not Chebyshev: the latter ties all eight neighbours at 1 and the
# falloff silently disappears.
ALPHA = {1: 0.58, 2: 0.30}  # edge cells, then corners


def mark_svg(colour: str) -> str:
    cells = []
    for row in range(N):
        for col in range(N):
            x = ORIGIN + col * (CELL + GAP)
            y = ORIGIN + row * (CELL + GAP)
            ring = abs(row - 1) + abs(col - 1)
            if ring == 0:
                continue
            cells.append(
                f'<rect x="{x + STROKE / 2}" y="{y + STROKE / 2}" '
                f'width="{CELL - STROKE}" height="{CELL - STROKE}" rx="{RX - STROKE / 2}" '
                f'fill="none" stroke="{colour}" stroke-width="{STROKE}" '
                f'opacity="{ALPHA[ring]}"/>'
            )
    cx = ORIGIN + 1 * (CELL + GAP)
    lit = (
        f'<rect x="{cx}" y="{cx}" width="{CELL}" height="{CELL}" rx="{RX}" '
        f'fill="{colour}"/>'
    )
    # Run: a play glyph, knocked out of the lit cell.
    c = cx + CELL / 2
    tri = (
        f'<path d="M {c - 9} {c - 13} L {c + 13} {c} L {c - 9} {c + 13} Z" '
        f'fill="#ffffff" stroke="#ffffff" stroke-width="5" stroke-linejoin="round"/>'
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
        'width="256" height="256">' + "".join(cells) + lit + tri + "</svg>"
    )


def render_mark(colour: str, size: int) -> Image.Image:
    png = cairosvg.svg2png(
        bytestring=mark_svg(colour).encode(), output_width=size, output_height=size
    )
    return Image.open(io.BytesIO(png)).convert("RGBA")


# Logo geometry, measured off the siblings at @2x: the canvas is 200 tall and
# trimmed to the ink (their widths run 733-1166 with the name), the mark is a
# 138px square inset at 31,31, and the wordmark's ink starts at x=220 on a
# baseline of 131 with a 65px cap height.
LOGO_H, MARK_BOX, MARK_INSET = 200, 138, 31
TEXT_X, BASELINE, CAP, RIGHT_MARGIN = 220, 131, 65, 54
TEXT = "LLM Tools"


def fit_font(cap_px: float) -> ImageFont.FreeTypeFont:
    """Size the face by measuring its cap height, not by assuming a ratio."""
    size = max(1, round(cap_px / 0.727))
    for _ in range(16):
        font = ImageFont.truetype(FONT, size)
        _, top, _, bottom = font.getbbox("LLM")  # caps only: no ascender overshoot
        cap = bottom - top
        if cap == round(cap_px):
            break
        size += 1 if cap < cap_px else -1
    return font


def _ink(img: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of what actually got drawn."""
    box = img.getchannel("A").point(lambda v: 255 if v > 30 else 0).getbbox()
    assert box is not None
    return box


def render_logo(theme: dict, scale: float) -> Image.Image:
    """Place the wordmark by measuring the raster, not by trusting a metric.

    `ImageDraw.textbbox` reports a layout box that sits ~6px left of Inter's
    real ink here, so the target positions are hit by drawing once, measuring,
    and shifting by the difference.
    """
    font = fit_font(CAP * scale)
    height = round(LOGO_H * scale)
    target_x, target_bottom = round(TEXT_X * scale), round((BASELINE - 1) * scale)

    scratch = Image.new("RGBA", (4000, height * 3), (0, 0, 0, 0))
    ImageDraw.Draw(scratch).text(
        (target_x, target_bottom), TEXT, font=font, fill=theme["text"], anchor="ls"
    )
    x0, y0, x1, y1 = _ink(scratch)
    dx, dy = target_x - x0, target_bottom - (y1 - 1)

    width = round((x1 - x0) + target_x + RIGHT_MARGIN * scale)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # The mark's artwork does not fill its own canvas, so scale by the content
    # fraction and back the inset out — pasting the raw square lands it small
    # and 7px inboard of where the siblings put it.
    render_px = round(MARK_BOX * scale * 256 / SPAN)
    offset = round(MARK_INSET * scale - render_px * ORIGIN / 256)
    img.alpha_composite(render_mark(theme["mark"], render_px), (offset, offset))

    ImageDraw.Draw(img).text(
        (target_x + dx, target_bottom + dy),
        TEXT,
        font=font,
        fill=theme["text"],
        anchor="ls",
    )
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for prefix, theme in (("", LIGHT), ("dark_", DARK)):
        for suffix, px in (("", 128), ("@2x", 256)):
            render_mark(theme["mark"], px).save(OUT / f"{prefix}icon{suffix}.png")
        for suffix, scale in (("", 0.5), ("@2x", 1.0)):
            render_logo(theme, scale).save(OUT / f"{prefix}logo{suffix}.png")
    for f in sorted(OUT.iterdir()):
        im = Image.open(f)
        print(f"{f.name:22} {im.size} {im.mode}")


if __name__ == "__main__":
    main()
