"""
Generate every brand asset the application and installer need.

Sources, in order of preference:
  1. assets/brand/sheetclub_logo_light.png   the supplied SheetClub artwork
  2. the brand colours in app/branding.py    procedural fallback

Produces:
  assets/icon.ico          application + installer icon, 16 -> 256 px
  assets/logo_mark.png     square mark for the sidebar and About tile
  assets/logo.png          wide lockup, transparent background
  assets/splash.png        start-up splash
  installer/wizard.bmp     Inno Setup side banner
  installer/wizard_small.bmp

    python tools/make_assets.py            create what is missing
    python tools/make_assets.py --force    regenerate everything
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import branding                                        # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    print("Pillow is required:  pip install Pillow")
    raise SystemExit(1)

ASSETS = ROOT / "assets"
BRAND = ASSETS / "brand"
INSTALLER = ROOT / "installer"
ASSETS.mkdir(parents=True, exist_ok=True)
(ASSETS / "fonts").mkdir(exist_ok=True)
INSTALLER.mkdir(parents=True, exist_ok=True)

FORCE = "--force" in sys.argv

SOURCE_LOGO = BRAND / "sheetclub_logo_light.png"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def load_font(size: int, bold: bool = True):
    names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
             ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def linear_gradient(size: tuple[int, int], a: str, b: str,
                    diagonal: bool = True) -> Image.Image:
    """Two-stop gradient, drawn small and upscaled — fast and smooth."""
    w, h = size
    ca, cb = rgb(a), rgb(b)
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = ((x + y) / 126.0) if diagonal else (y / 63.0)
            px[x, y] = (round(ca[0] + (cb[0] - ca[0]) * t),
                        round(ca[1] + (cb[1] - ca[1]) * t),
                        round(ca[2] + (cb[2] - ca[2]) * t))
    return small.resize((w, h), Image.BICUBIC)


def rounded_mask(size: int, radius_ratio: float = 0.225, ss: int = 4) -> Image.Image:
    """Anti-aliased rounded-square mask via supersampling."""
    big = size * ss
    m = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, big - 1, big - 1],
                        radius=int(big * radius_ratio), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def trim_white(img: Image.Image, tol: int = 246) -> Image.Image:
    """Make a white background transparent and crop to the artwork."""
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r >= tol and g >= tol and b >= tol:
                px[x, y] = (r, g, b, 0)
    return img.crop(img.getbbox() or (0, 0, w, h))


def source_logo() -> Image.Image | None:
    if SOURCE_LOGO.is_file():
        try:
            return Image.open(SOURCE_LOGO).convert("RGBA")
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# the application icon
# ---------------------------------------------------------------------------
# A document page carrying the SheetClub grid, on the brand's green gradient.
# Drawn at 4x and downsampled so the curves stay clean at 16 px.

def draw_icon(size: int, detail: bool = True) -> Image.Image:
    """
    A white document with a folded corner on the SheetClub green gradient,
    banded with a bold "PDF" ribbon.

    The ribbon is what makes this read as a PDF tool rather than a spreadsheet
    one, and it survives downscaling: at 16 px the letters dissolve but the
    solid bar across a white page still says "document format".
    """
    ss = 4
    S = size * ss
    tile = linear_gradient((S, S), branding.BRAND_DEEP, branding.BRAND_BRIGHT)
    icon = tile.convert("RGBA")
    icon.putalpha(rounded_mask(size, ss=4).resize((S, S), Image.LANCZOS))

    # ---- the document page ------------------------------------------------
    # Small renderings get a proportionally larger page: at 16-32 px the tile
    # padding eats the artwork, and a bigger page keeps the silhouette legible.
    if detail:
        wf, hf, tf, foldf = 0.54, 0.66, 0.17, 0.30
    else:
        wf, hf, tf, foldf = 0.64, 0.76, 0.12, 0.32

    pw, ph = int(S * wf), int(S * hf)
    px0 = (S - pw) // 2
    py0 = int(S * tf)
    fold = int(pw * foldf)

    page = [
        (px0, py0),
        (px0 + pw - fold, py0),
        (px0 + pw, py0 + fold),
        (px0 + pw, py0 + ph),
        (px0, py0 + ph),
    ]

    shadow = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon(
        [(x + int(S * 0.010), y + int(S * 0.020)) for x, y in page],
        fill=(0, 26, 16, 105))
    shadow = shadow.filter(ImageFilter.GaussianBlur(S * 0.014))
    icon = Image.alpha_composite(icon, shadow)
    d = ImageDraw.Draw(icon)

    d.polygon(page, fill=(255, 255, 255, 255))
    d.polygon([(px0 + pw - fold, py0),
               (px0 + pw, py0 + fold),
               (px0 + pw - fold, py0 + fold)],
              fill=(198, 226, 199, 255))

    deep = rgb(branding.BRAND_DEEP)
    accent = rgb(branding.BRAND_ACCENT)

    # ---- content lines above the ribbon -----------------------------------
    if detail:
        lx0 = px0 + int(pw * 0.15)
        lx1 = px0 + pw - int(pw * 0.15)
        lh = max(1, int(ph * 0.035))
        widths = (1.0, 0.82, 0.92)
        for i, frac in enumerate(widths):
            y = py0 + int(ph * (0.30 + i * 0.095))
            d.rounded_rectangle(
                [lx0, y, lx0 + (lx1 - lx0) * frac, y + lh],
                radius=lh // 2, fill=(176, 199, 186, 255))

    # ---- the PDF ribbon ---------------------------------------------------
    if detail:
        ry0 = py0 + int(ph * 0.60)
        ry1 = py0 + int(ph * 0.855)
    else:
        # thicker band at small sizes so it survives downsampling
        ry0 = py0 + int(ph * 0.55)
        ry1 = py0 + int(ph * 0.88)
    rx0 = px0 - int(pw * 0.10)
    rx1 = px0 + pw + int(pw * 0.10)

    band = linear_gradient((rx1 - rx0, ry1 - ry0),
                           branding.BRAND_DEEP, branding.BRAND_ACCENT).convert("RGBA")
    icon.paste(band, (rx0, ry0))
    d = ImageDraw.Draw(icon)

    # folded ribbon tails, so it reads as a band wrapped around the page
    tail = int(pw * 0.10)
    d.polygon([(rx0, ry0), (rx0 + tail, ry0), (rx0 + tail, ry0 - tail)],
              fill=tuple(int(c * 0.55) for c in deep) + (255,))
    d.polygon([(rx1, ry0), (rx1 - tail, ry0), (rx1 - tail, ry0 - tail)],
              fill=tuple(int(c * 0.55) for c in deep) + (255,))

    if detail:
        label = "PDF"
        fs = int((ry1 - ry0) * 0.72)
        font = load_font(fs)
        bbox = d.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((S - tw) / 2 - bbox[0],
                ry0 + ((ry1 - ry0) - th) / 2 - bbox[1]),
               label, font=font, fill=(255, 255, 255, 255))

    return icon.resize((size, size), Image.LANCZOS)


def make_icon() -> None:
    target = ASSETS / branding.ICON_FILE
    if target.exists() and not FORCE:
        print(f"keep    {target.name}")
        return
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    frames = [draw_icon(s, detail=s >= 40) for s in sizes]
    frames[-1].save(target, format="ICO", sizes=[(s, s) for s in sizes])
    # a PNG copy is handy for docs and the web
    frames[-1].save(ASSETS / "icon_256.png", "PNG")
    print(f"created {target.name}  ({len(sizes)} sizes)")


# ---------------------------------------------------------------------------
# logo mark + lockup
# ---------------------------------------------------------------------------

def make_logo_mark() -> None:
    target = ASSETS / branding.LOGO_MARK_FILE
    if target.exists() and not FORCE:
        print(f"keep    {target.name}")
        return

    src = source_logo()
    if src is not None:
        # the S sits in the upper portion of the supplied square artwork
        w, h = src.size
        mark = trim_white(src.crop((int(w * 0.28), int(h * 0.17),
                                    int(w * 0.74), int(h * 0.58))))
        canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        scale = min(460 / mark.width, 460 / mark.height)
        mark = mark.resize((max(1, int(mark.width * scale)),
                            max(1, int(mark.height * scale))), Image.LANCZOS)
        canvas.paste(mark, ((512 - mark.width) // 2, (512 - mark.height) // 2), mark)
        canvas.save(target, "PNG")
        print(f"created {target.name}  (from the SheetClub artwork)")
        return

    draw_icon(512).save(target, "PNG")
    print(f"created {target.name}  (generated)")


def make_logo() -> None:
    target = ASSETS / branding.LOGO_FILE
    if target.exists() and not FORCE:
        print(f"keep    {target.name}")
        return

    src = source_logo()
    if src is not None:
        lockup = trim_white(src)
        scale = min(760 / lockup.width, 300 / lockup.height)
        lockup = lockup.resize((int(lockup.width * scale),
                                int(lockup.height * scale)), Image.LANCZOS)
        canvas = Image.new("RGBA", (lockup.width + 24, lockup.height + 24), (0, 0, 0, 0))
        canvas.paste(lockup, (12, 12), lockup)
        canvas.save(target, "PNG")
        print(f"created {target.name}  (from the SheetClub artwork)")
        return

    w, h = 640, 160
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mark = draw_icon(112)
    img.paste(mark, (12, 24), mark)
    d = ImageDraw.Draw(img)
    d.text((146, 40), branding.COMPANY_NAME, font=load_font(40),
           fill=rgb(branding.BRAND_NAVY) + (255,))
    d.text((148, 92), branding.APP_NAME, font=load_font(20, bold=False),
           fill=rgb(branding.BRAND_ACCENT) + (255,))
    img.save(target, "PNG")
    print(f"created {target.name}  (generated)")


# ---------------------------------------------------------------------------
# splash + installer art
# ---------------------------------------------------------------------------

def make_splash() -> None:
    target = ASSETS / branding.SPLASH_FILE
    if target.exists() and not FORCE:
        print(f"keep    {target.name}")
        return

    w, h = 560, 320
    img = linear_gradient((w, h), branding.BRAND_DEEP, branding.BRAND_ACCENT).convert("RGBA")

    mark_path = ASSETS / branding.LOGO_MARK_FILE
    if mark_path.is_file():
        mark = Image.open(mark_path).convert("RGBA")
        mark.thumbnail((96, 96), Image.LANCZOS)
        img.paste(mark, (44, 46), mark)
    else:
        m = draw_icon(96)
        img.paste(m, (44, 46), m)

    d = ImageDraw.Draw(img)
    d.text((44, 168), branding.APP_NAME, font=load_font(32), fill=(255, 255, 255, 255))
    d.text((46, 212), branding.APP_TAGLINE, font=load_font(16, bold=False),
           fill=(255, 255, 255, 220))
    d.line([(46, 250), (w - 46, 250)], fill=(255, 255, 255, 60), width=1)
    d.text((46, 264), f"{branding.COMPANY_NAME}   ·   {branding.version_string()}",
           font=load_font(13, bold=False), fill=(255, 255, 255, 200))
    img.convert("RGB").save(target, "PNG")
    print(f"created {target.name}")


def make_installer_images() -> None:
    big, small = INSTALLER / "wizard.bmp", INSTALLER / "wizard_small.bmp"

    if not big.exists() or FORCE:
        w, h = 164, 314
        img = linear_gradient((w, h), branding.BRAND_DEEP, branding.BRAND_ACCENT,
                              diagonal=False).convert("RGBA")
        mark = draw_icon(76)
        img.paste(mark, ((w - 76) // 2, 42), mark)
        d = ImageDraw.Draw(img)
        d.text((16, 146), branding.COMPANY_NAME, font=load_font(19),
               fill=(255, 255, 255, 255))
        d.text((16, 172), "PDF Master", font=load_font(14, bold=False),
               fill=(255, 255, 255, 225))
        d.text((16, 190), "Toolkit", font=load_font(14, bold=False),
               fill=(255, 255, 255, 225))
        d.line([(16, 218), (w - 16, 218)], fill=(255, 255, 255, 70), width=1)
        d.text((16, 228), branding.APP_TAGLINE.replace(" ", "\n", 1),
               font=load_font(10, bold=False), fill=(255, 255, 255, 190))
        img.convert("RGB").save(big, "BMP")
        print(f"created {big.name}")

    if not small.exists() or FORCE:
        img = Image.new("RGB", (55, 58), rgb(branding.BRAND_DEEP))
        mark = draw_icon(50)
        img.paste(mark, (2, 4), mark)
        img.save(small, "BMP")
        print(f"created {small.name}")


if __name__ == "__main__":
    print(f"Brand assets for {branding.APP_NAME} — {branding.COMPANY_NAME}")
    print(f"  accent {branding.BRAND_ACCENT}   deep {branding.BRAND_DEEP}   "
          f"bright {branding.BRAND_BRIGHT}")
    print(f"  source artwork: {'found' if SOURCE_LOGO.is_file() else 'not present, generating'}")
    print()
    make_icon()
    make_logo_mark()
    make_logo()
    make_splash()
    make_installer_images()
    print("\nDone.")
