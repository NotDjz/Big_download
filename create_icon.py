"""Generate icon.ico (multi-size) and icon.png for BIG DL.

The logo is a DL monogram, dark ink on copper, rather than a download arrow.
Two reasons:

- An arrow pointing into a tray is the universal download glyph, the one every
  browser uses. It names the category, not the application.
- Measured at 16 px, the taskbar size: a light mark on a dark ground loses its
  edges against a taskbar that is itself dark. A solid colour field keeps its
  silhouette and stays recognisable even once the letters stop reading.

The copper is the interface's own (--accent), so the icon and the window speak
the same language. Violet lives on elsewhere: it is --brand, the colour of the
"DL" in the application's wordmark and on the web page.

    py create_icon.py
"""
import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SS = 8                              # supersampling avant reduction
COPPER_TOP = (242, 155, 83, 255)
COPPER_BOT = (229, 137, 63, 255)
INK = (26, 18, 7, 255)

# Bold faces in order of preference. A very heavy weight is what holds the
# letters together at 16 px; a regular weight dissolves there.
# Bare names: Pillow looks in %SystemRoot%\Fonts, which avoids hardcoding a
# C:\Windows that is not guaranteed to exist.
FONT_CANDIDATES = (
    'seguibl.ttf',      # Segoe UI Black
    'ariblk.ttf',       # Arial Black
    'segoeuib.ttf',     # Segoe UI Bold
    'arialbd.ttf',      # Arial Bold
)


def _font_path():
    for name in FONT_CANDIDATES:
        try:
            ImageFont.truetype(name, 12)
            return name
        except OSError:
            continue
    raise SystemExit(
        'No bold font found among: ' + ', '.join(FONT_CANDIDATES)
    )


def _fit(draw, text, path, target_w, ceiling):
    """The largest size whose text still fits inside target_w."""
    lo, hi = 8, ceiling
    best = ImageFont.truetype(path, 8)
    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(path, mid)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= target_w:
            best, lo = font, mid + 1
        else:
            hi = mid - 1
    return best


def create_icon_image(size, font_path, text='DL'):
    hi = size * SS
    pad = max(SS, int(hi * 0.02))

    # Ground: a copper gradient inside a rounded square
    bg = Image.new('RGBA', (hi, hi))
    draw = ImageDraw.Draw(bg)
    for y in range(hi):
        t = y / max(1, hi - 1)
        draw.line(
            [(0, y), (hi - 1, y)],
            fill=tuple(int(a + (b - a) * t) for a, b in zip(COPPER_TOP, COPPER_BOT)),
        )
    mask = Image.new('L', (hi, hi), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [pad, pad, hi - pad - 1, hi - pad - 1], radius=int(hi * 0.22), fill=255)
    bg.putalpha(mask)

    # The monogram is centred optically on its ink box, not on the baseline:
    # otherwise the letters float upwards.
    layer = Image.new('RGBA', (hi, hi), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _fit(d, text, font_path, int(hi * 0.72), hi)
    box = d.textbbox((0, 0), text, font=font)
    d.text(
        ((hi - (box[2] - box[0])) / 2 - box[0], (hi - (box[3] - box[1])) / 2 - box[1]),
        text, font=font, fill=INK,
    )
    img = Image.alpha_composite(bg, layer)

    return img.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(images, path):
    """Write an .ico whose every entry is a PNG.

    Pillow can write an ICO, but it resamples from a single image: that loses
    the per-size drawing, which is exactly what makes the 16 px legible.
    """
    buf = io.BytesIO()
    buf.write(struct.pack('<HHH', 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    entries, payloads = [], []
    for im in images:
        png = io.BytesIO()
        im.save(png, format='PNG')
        data = png.getvalue()
        payloads.append(data)
        # 0 means 256 in the ICO header
        w = im.width if im.width < 256 else 0
        h = im.height if im.height < 256 else 0
        entries.append(struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for entry in entries:
        buf.write(entry)
    for data in payloads:
        buf.write(data)
    Path(path).write_bytes(buf.getvalue())


if __name__ == '__main__':
    # Relative to the script and not to the working directory: run from
    # elsewhere, it dropped the files beside the caller and skipped docs/
    # without a word.
    root = Path(__file__).resolve().parent
    sizes = [16, 32, 48, 64, 128, 256]
    font_path = _font_path()
    images = [create_icon_image(s, font_path) for s in sizes]

    save_ico(images, root / 'icon.ico')
    big = images[sizes.index(128)]
    # The icon's three consumers: the executable (icon.ico), the window's own
    # tab (static/icon.png) and the GitHub page (docs/icon.png). Forgetting the
    # second left the old violet logo inside the application itself.
    for target in (root / 'icon.png', root / 'static' / 'icon.png', root / 'docs' / 'icon.png'):
        if target.parent.is_dir():
            big.save(target)
    print('icon.ico (%d sizes), icon.png, static/icon.png and docs/icon.png written'
          % len(sizes))
