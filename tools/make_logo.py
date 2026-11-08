"""Draws the dexSTASH logo (an open cardboard box of cards, backs showing, one yellow divider) into
app/static/: the header and tab-bar SVG, the favicon, the home-screen icons (with Android's maskable and
monochrome ones) and the iPhone and iPad launch images. Original art on a 32 x 32 grid; run it again after
changing the drawing or the colours:

    python tools/make_logo.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pwa import SPLASH  # noqa: E402  the launch image sizes, which the page's tags list too

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
O = (31, 21, 48)  # outline
LIGHT, MID, DARK, INSIDE = (226, 176, 110), (190, 128, 66), (128, 78, 40), (70, 42, 28)  # cardboard
PURPLE, LAV, Y, CREAM = (123, 75, 214), (160, 130, 230), (255, 204, 51), (255, 243, 196)  # card backs
CARDS = [PURPLE, PURPLE, Y, PURPLE, PURPLE]  # front to back; the yellow one is the divider
TILE = (237, 231, 252)  # behind the logo on home-screen icons, where a see-through corner would show black
DARK_PAGE = (17, 17, 19)  # the app's Dark page colour (#111113), behind the launch images
N = 32
L, B, R, F = (3, 13), (16, 7), (28, 13), (16, 19)  # the box's top corners: left, back, right, front
L2, F2, R2 = (3, 25), (16, 31), (28, 25)  # and its bottom corners


def _poly(d, pts, fill):
    d.polygon(pts, fill=fill, outline=O)


def draw() -> Image.Image:
    """The logo at 32 x 32, one pixel per grid square."""
    im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _poly(d, [L, B, (11, 1), (0, 7)], LIGHT)  # the two back flaps
    _poly(d, [B, R, (31, 7), (21, 1)], DARK)
    _poly(d, [L, B, R, F], INSIDE)
    _poly(d, [B, R, (28, 16), (16, 10)], MID)
    for i in reversed(range(len(CARDS))):  # cards stand parallel to the front-left side, filed front to back
        s = 2 + 2 * i
        p1, p2 = (L[0] + 2 + s, L[1] + 1 - s // 2), (F[0] - 1 + s, F[1] - 1 - s // 2)
        top1, top2 = (p1[0], p1[1] - 4), (p2[0], p2[1] - 4)
        d.polygon([p1, p2, top2, top1], fill=CARDS[i], outline=O)
        d.line([(top1[0] + 1, top1[1] + 1), (top2[0] - 1, top2[1] + 1)], fill=CREAM if CARDS[i] == Y else LAV)
    _poly(d, [L, F, F2, L2], DARK)  # the front sides
    _poly(d, [F, R, R2, F2], LIGHT)
    d.line([(20, 25), (25, 22)], fill=MID)  # tape
    d.line([(20, 27), (24, 25)], fill=MID)
    _poly(d, [L, F, (12, 24), (0, 17)], MID)  # the two front flaps
    _poly(d, [F, R, (31, 18), (20, 24)], LIGHT)
    return im


def png(im: Image.Image, scale: int, size: int | None = None, tile: bool = False) -> Image.Image:
    """Each grid square as scale x scale pixels (nearest neighbour, so it stays crisp), centred on size."""
    size = size or N * scale
    out = Image.new("RGBA", (size, size), TILE + (255,) if tile else (0, 0, 0, 0))
    big = im.resize((N * scale, N * scale), Image.NEAREST)
    off = (size - N * scale) // 2
    out.paste(big, (off, off), big)
    return out


def svg(im: Image.Image) -> str:
    """One rect per run of same-coloured pixels in a row, so the file stays small."""
    rects = []
    for y in range(N):
        x = 0
        while x < N:
            c = im.getpixel((x, y))
            if c[3] == 0:
                x += 1
                continue
            end = x
            while end + 1 < N and im.getpixel((end + 1, y)) == c:
                end += 1
            rects.append(f'<rect x="{x}" y="{y}" width="{end - x + 1}" height="1" fill="#{"%02x%02x%02x" % c[:3]}"/>')
            x = end + 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {N} {N}" shape-rendering="crispEdges">'
            f"<title>dexSTASH</title>{''.join(rects)}</svg>\n")


if __name__ == "__main__":
    im = draw()
    (STATIC / "logo.svg").write_text(svg(im))
    im.save(STATIC / "favicon-32.png")
    # Home-screen icons: the logo on a tile with a margin, in whole-pixel steps.
    png(im, 5, 180, tile=True).save(STATIC / "icon-180.png")
    png(im, 5, 192, tile=True).save(STATIC / "icon-192.png")
    png(im, 14, 512, tile=True).save(STATIC / "icon-512.png")
    png(im, 4, 152, tile=True).save(STATIC / "icon-152.png")  # iPad
    png(im, 5, 167, tile=True).save(STATIC / "icon-167.png")  # iPad Pro
    # Android: the logo inside the round mask's safe circle (the middle 80%), and a one-colour version for
    # themed icons.
    png(im, 9, 512, tile=True).save(STATIC / "icon-maskable-512.png")
    shape = png(im, 9, 512).getchannel("A")
    mono = Image.new("RGBA", shape.size, (255, 255, 255, 0))
    mono.putalpha(shape)
    mono.save(STATIC / "icon-monochrome-512.png")
    (STATIC / "splash").mkdir(exist_ok=True)
    for w, h, ratio in SPLASH:  # the box only, on the Dark page colour, about a fifth of the width
        page = Image.new("RGB", (w, h), DARK_PAGE)
        logo = png(im, max(1, w // 5 // N))
        page.paste(logo, ((w - logo.width) // 2, (h - logo.height) // 2), logo)
        page.save(STATIC / "splash" / f"splash-{w}x{h}.png", optimize=True)
    print("written to", STATIC)
