from PIL import Image, ImageDraw
import struct, io


def create_icon_image(size):
    """Reproduce the BIG DL logo: white download arrow on purple gradient rounded square."""
    # Use 4x supersampling for clean anti-aliasing
    ss = 4
    hi = size * ss
    img = Image.new('RGBA', (hi, hi), (0, 0, 0, 0))

    pad = max(ss, int(hi * 0.02))
    radius = int(hi * 0.22)

    # Purple gradient background (#8B6EFF -> #6B45E8)
    bg = Image.new('RGBA', (hi, hi), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    for y in range(hi):
        t = y / max(1, hi - 1)
        r = int(139 + (107 - 139) * t)
        g = int(110 + (69 - 110) * t)
        b = int(255 + (232 - 255) * t)
        bg_draw.line([(0, y), (hi - 1, y)], fill=(r, g, b, 255))

    mask = Image.new('L', (hi, hi), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [pad, pad, hi - pad - 1, hi - pad - 1], radius=radius, fill=255)
    bg.putalpha(mask)
    img = Image.alpha_composite(img, bg)

    # Draw download icon
    layer = Image.new('RGBA', (hi, hi), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    s = hi / 68.0
    white = (255, 255, 255, 255)

    # Vertical bar (pill): rect x=28 y=10 w=12 h=30 rx=6
    draw.rounded_rectangle(
        [28*s, 10*s, 40*s, 40*s], radius=max(1, int(6*s)), fill=white)

    # Triangle down: 34,52  18,34  50,34
    draw.polygon([(34*s, 52*s), (18*s, 34*s), (50*s, 34*s)], fill=white)

    # Tray - draw as filled polygon (thick U-shape)
    sw = 5 * s  # stroke width
    hw = sw / 2
    # Outer path (outside of the U)
    # Inner path (inside of the U)
    # Left arm outer: x=14-hw, from y=49 to y=58+hw (bottom)
    # Bottom outer: from x=14-hw to x=54+hw, y=58+hw
    # Right arm outer: x=54+hw, from y=58+hw to y=49
    # Then inner path reverses
    lo = 14 * s  # left x
    ro = 54 * s  # right x
    top_y = 49 * s
    bot_y = 58 * s
    cr = 4 * s  # corner radius

    # Draw the tray as two rounded rects minus the inside
    # Simpler approach: draw thick lines with round caps
    # Left vertical
    draw.line([(lo, top_y), (lo, bot_y - cr)], fill=white, width=max(1, round(sw)))
    # Bottom horizontal
    draw.line([(lo + cr, bot_y), (ro - cr, bot_y)], fill=white, width=max(1, round(sw)))
    # Right vertical
    draw.line([(ro, bot_y - cr), (ro, top_y)], fill=white, width=max(1, round(sw)))
    # Round corners at bottom-left and bottom-right
    corner_w = max(1, round(sw))
    # Bottom-left corner
    draw.arc([lo - hw, bot_y - 2*cr, lo + 2*cr + hw, bot_y + hw],
             start=90, end=180, fill=white, width=corner_w)
    # Bottom-right corner
    draw.arc([ro - 2*cr - hw, bot_y - 2*cr, ro + hw, bot_y + hw],
             start=0, end=90, fill=white, width=corner_w)
    # Round end caps on top of vertical lines
    cap_r = sw / 2
    draw.ellipse([lo - cap_r, top_y - cap_r, lo + cap_r, top_y + cap_r], fill=white)
    draw.ellipse([ro - cap_r, top_y - cap_r, ro + cap_r, top_y + cap_r], fill=white)

    img = Image.alpha_composite(img, layer)

    # Downsample with high-quality filter
    img = img.resize((size, size), Image.LANCZOS)
    return img


def save_ico(images, path):
    ico_buf = io.BytesIO()
    ico_buf.write(struct.pack('<HHH', 0, 1, len(images)))
    data_offset = 6 + 16 * len(images)
    entries = []
    png_datas = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        data = buf.getvalue()
        png_datas.append(data)
        w = im.width if im.width < 256 else 0
        h = im.height if im.height < 256 else 0
        entries.append(struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(data), data_offset))
        data_offset += len(data)
    for e in entries:
        ico_buf.write(e)
    for d in png_datas:
        ico_buf.write(d)
    with open(path, 'wb') as f:
        f.write(ico_buf.getvalue())


if __name__ == '__main__':
    sizes = [16, 32, 48, 64, 128, 256]
    images = [create_icon_image(s) for s in sizes]
    save_ico(images, 'icon.ico')
    images[4].save('icon.png')
    print(f"icon.ico and icon.png created ({len(sizes)} sizes)")
