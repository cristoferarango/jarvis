"""Genera assets/crisvis.ico (anillos de reactor). `uv run --no-sync python scripts/hacer_icono.py`."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512
CYAN = (90, 220, 255, 255)
CORE = (220, 250, 255, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = SIZE // 2
    d.ellipse((8, 8, SIZE - 8, SIZE - 8), fill=(8, 18, 30, 255))
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for r, w in ((200, 22), (140, 14)):
        g.ellipse((c - r, c - r, c + r, c + r), outline=CYAN, width=w)
    g.ellipse((c - 62, c - 62, c + 62, c + 62), fill=CYAN)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
    for r, w in ((200, 16), (140, 9)):
        d.ellipse((c - r, c - r, c + r, c + r), outline=CYAN, width=w)
    for angle in range(0, 360, 45):
        d.arc((c - 175, c - 175, c + 175, c + 175), angle + 6, angle + 34, fill=CYAN, width=18)
    d.ellipse((c - 56, c - 56, c + 56, c + 56), fill=CORE)
    return img


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "assets" / "crisvis.ico"
    out.parent.mkdir(exist_ok=True)
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
