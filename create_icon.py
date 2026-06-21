from PIL import Image, ImageDraw, ImageFont
import struct, io

def create_icon_image(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rectangle background - dark like the app
    pad = int(size * 0.06)
    radius = int(size * 0.18)
    draw.rounded_rectangle([pad, pad, size - pad, size - pad],
                           radius=radius, fill=(15, 15, 30, 255))

    # Gradient overlay (purple to blue) - draw horizontal bands
    for y in range(pad + radius, size - pad - radius):
        t = (y - pad) / (size - 2 * pad)
        r = int(192 * (1 - t) + 96 * t)
        g = int(132 * (1 - t) + 165 * t)
        b = int(252 * (1 - t) + 250 * t)
        alpha = int(40 + 25 * t)
        draw.line([(pad + radius // 2, y), (size - pad - radius // 2, y)],
                  fill=(r, g, b, alpha))

    # Text "BD" in bold white
    font_size = int(size * 0.48)
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    text = "BD"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1] - int(size * 0.02)

    # Gradient text effect: draw with purple-blue gradient
    text_layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)

    # Create gradient mask for text
    gradient = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / size
        r = int(192 * (1 - t) + 96 * t)
        g = int(132 * (1 - t) + 165 * t)
        b = int(252 * (1 - t) + 250 * t)
        grad_draw.line([(0, y), (size, y)], fill=(r, g, b, 255))

    # Composite: use text as mask over gradient
    text_mask = text_layer.split()[3]
    gradient_text = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    gradient_text.paste(gradient, mask=text_mask)

    img = Image.alpha_composite(img, gradient_text)

    # Small download arrow below text
    arrow_size = int(size * 0.12)
    cx = size // 2
    cy = ty + th + int(size * 0.06)
    arrow_draw = ImageDraw.Draw(img)
    # Arrow shaft
    shaft_w = max(2, int(size * 0.03))
    arrow_draw.rectangle([cx - shaft_w, cy - arrow_size // 2, cx + shaft_w, cy + arrow_size // 3],
                         fill=(96, 165, 250, 200))
    # Arrow head
    arrow_draw.polygon([
        (cx - arrow_size // 2, cy + arrow_size // 4),
        (cx + arrow_size // 2, cy + arrow_size // 4),
        (cx, cy + arrow_size),
    ], fill=(96, 165, 250, 200))

    return img


def save_ico(images, path):
    """Save multiple PIL images as a .ico file."""
    ico_buf = io.BytesIO()
    # ICO header: reserved(2), type=1(2), count(2)
    ico_buf.write(struct.pack('<HHH', 0, 1, len(images)))

    data_offset = 6 + 16 * len(images)
    entries = []
    png_datas = []

    for img in images:
        png_buf = io.BytesIO()
        img.save(png_buf, format='PNG')
        png_data = png_buf.getvalue()
        png_datas.append(png_data)

        w = img.width if img.width < 256 else 0
        h = img.height if img.height < 256 else 0
        entry = struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(png_data), data_offset)
        entries.append(entry)
        data_offset += len(png_data)

    for entry in entries:
        ico_buf.write(entry)
    for png_data in png_datas:
        ico_buf.write(png_data)

    with open(path, 'wb') as f:
        f.write(ico_buf.getvalue())


if __name__ == '__main__':
    sizes = [16, 32, 48, 64, 128, 256]
    images = [create_icon_image(s) for s in sizes]
    save_ico(images, 'icon.ico')
    images[4].save('icon.png')  # 128px for pywebview
    print(f"icon.ico and icon.png created ({len(sizes)} sizes)")
