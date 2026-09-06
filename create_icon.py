"""Genere icon.ico (multi-tailles) et icon.png pour BIG DL.

Le logo est le monogramme DL, encre sombre sur cuivre, pas une fleche de
telechargement. Deux raisons :

- La fleche vers un receptacle est le glyphe universel du telechargement :
  celui de tous les navigateurs. Elle nomme la categorie, pas l'application.
- Mesure a 16 px (barre des taches) : un dessin clair sur fond sombre perd ses
  contours contre une barre elle-meme sombre. Un aplat de couleur garde sa
  silhouette et reste identifiable meme quand les lettres ne se lisent plus.

Le cuivre est celui de l'interface (--accent), pour que l'icone et la fenetre
parlent la meme langue. Le violet reste vivant ailleurs : c'est --brand, la
couleur du « DL » dans le logo textuel de l'application et de la page web.

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

# Polices grasses par ordre de preference. Une graisse tres lourde est ce qui
# fait tenir les lettres a 16 px ; une graisse normale s'y dissout.
# Noms nus : Pillow cherche dans %SystemRoot%\Fonts, ce qui evite de coder en
# dur un C:\Windows qui n'est pas garanti.
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
        'Aucune police grasse trouvee parmi : ' + ', '.join(FONT_CANDIDATES)
    )


def _fit(draw, text, path, target_w, ceiling):
    """Plus grande taille dont le texte tient dans target_w."""
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

    # Fond : degrade cuivre dans un carre arrondi
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

    # Monogramme centre optiquement sur sa boite d'encre, pas sur la ligne de
    # base : sinon les lettres flottent vers le haut.
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
    """Ecrit un .ico dont chaque entree est un PNG.

    Pillow sait ecrire un ICO, mais re-echantillonne depuis une seule image :
    on perd le dessin ajuste taille par taille, qui est justement ce qui rend
    le 16 px lisible.
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
        # 0 signifie 256 dans l'en-tete ICO
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
    # Relatifs au script et non au dossier courant : lance d'ailleurs, il
    # deposait les fichiers a cote de l'appelant et sautait docs/ en silence.
    root = Path(__file__).resolve().parent
    sizes = [16, 32, 48, 64, 128, 256]
    font_path = _font_path()
    images = [create_icon_image(s, font_path) for s in sizes]

    save_ico(images, root / 'icon.ico')
    big = images[sizes.index(128)]
    # Les trois consommateurs de l'icone : l'executable (icon.ico), l'onglet de
    # la fenetre (static/icon.png) et la page GitHub (docs/icon.png). Oublier le
    # deuxieme laissait l'ancien logo violet dans l'application elle-meme.
    for target in (root / 'icon.png', root / 'static' / 'icon.png', root / 'docs' / 'icon.png'):
        if target.parent.is_dir():
            big.save(target)
    print('icon.ico (%d tailles), icon.png, static/icon.png et docs/icon.png generes'
          % len(sizes))
