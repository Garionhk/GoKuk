"""Build the Gokuk and Gokuk Setup icon packs.

    python tools/make_icons.py

If ``Gokuk-Icon.png`` (a square master with transparency) sits in the project
root it is used; otherwise a placeholder mark is drawn: an orange disc with a
beamed pair of notes on the app's near-black. Setup gets the same mark with a
download arrow badge, so the two are easy to tell apart in a folder.

Writes assets/icons/Gokuk.ico, GokukSetup.ico (nine resolutions each) and PNGs.
Sizes below 48 px get a light unsharp mask, as in EasyAI's icon builder.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "Gokuk-Icon.png"
OUT = ROOT / "assets" / "icons"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SHARPEN_BELOW = 48

ACCENT = (255, 107, 26, 255)
ACCENT_DOWN = (224, 90, 18, 255)
BG = (14, 14, 17, 255)
INK = (18, 8, 0, 255)


def placeholder(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024
    d.rounded_rectangle((40 * s, 40 * s, 984 * s, 984 * s), radius=220 * s, fill=BG)
    d.ellipse((150 * s, 150 * s, 874 * s, 874 * s), fill=ACCENT)
    # Beamed eighth notes.
    stem_w = 46 * s
    d.polygon([(420 * s, 300 * s), (720 * s, 240 * s), (720 * s, 330 * s), (420 * s, 390 * s)], fill=INK)
    d.rectangle((420 * s - stem_w, 300 * s, 420 * s, 640 * s), fill=INK)
    d.rectangle((720 * s - stem_w, 240 * s, 720 * s, 580 * s), fill=INK)
    d.ellipse((260 * s, 560 * s, 420 * s, 700 * s), fill=INK)
    d.ellipse((560 * s, 500 * s, 720 * s, 640 * s), fill=INK)
    return img


def with_badge(base: Image.Image) -> Image.Image:
    img = base.copy()
    size = img.width
    d = ImageDraw.Draw(img)
    s = size / 1024
    d.ellipse((600 * s, 600 * s, 1010 * s, 1010 * s), fill=BG)
    d.ellipse((640 * s, 640 * s, 970 * s, 970 * s), fill=(243, 243, 245, 255))
    d.rectangle((775 * s, 700 * s, 835 * s, 850 * s), fill=ACCENT_DOWN)
    d.polygon([(710 * s, 830 * s), (900 * s, 830 * s), (805 * s, 925 * s)], fill=ACCENT_DOWN)
    return img


def resized(image: Image.Image, size: int) -> Image.Image:
    out = image.resize((size, size), Image.LANCZOS)
    if size < SHARPEN_BELOW:
        out = out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=90, threshold=0))
    return out


def write(name: str, master: Image.Image) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = [resized(master, n) for n in ICO_SIZES]
    frames[-1].save(OUT / f"{name}.ico", format="ICO", sizes=[(n, n) for n in ICO_SIZES],
                    append_images=frames[:-1])
    for n in (16, 32, 48, 256):
        resized(master, n).save(OUT / f"{name}-{n}.png")
    master.resize((1024, 1024), Image.LANCZOS).save(OUT / f"{name}-1024.png")
    print(f"wrote {name}.ico and PNGs")


def main() -> int:
    master = Image.open(MASTER).convert("RGBA") if MASTER.is_file() else placeholder()
    write("Gokuk", master)
    write("GokukSetup", with_badge(master))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
