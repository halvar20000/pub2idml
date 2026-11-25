#!/usr/bin/env python3
"""Draw the pub2idml icon (".pub" arrow "IDML") with Pillow and write it as PNG, ICO or an image object.

    python3 packaging/icon.py --png icon.png [--size 256]
    python3 packaging/icon.py --ico icon.ico

SPDX-License-Identifier: MIT
"""
import argparse
import os

TEAL = (14, 116, 144, 255)
FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def draw(size: int = 1024):
    from PIL import Image, ImageDraw, ImageFont

    s = 1024  # draw large, scale at the end
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = 100
    d.rounded_rectangle((m, m, s - m, s - m), radius=180, fill=(255, 255, 255, 255))
    d.rounded_rectangle((m, m, s - m, s // 2), radius=180, fill=TEAL)
    d.rectangle((m, s // 2 - 180, s - m, s // 2), fill=TEAL)
    d.rounded_rectangle((m, m, s - m, s - m), radius=180, outline=(200, 200, 200, 255), width=6)

    def font(px):
        for f in FONTS:
            if os.path.exists(f):
                return ImageFont.truetype(f, px)
        return ImageFont.load_default(size=px)

    def centered(text, y, px, fill):
        f = font(px)
        w = d.textlength(text, font=f)
        d.text(((s - w) / 2, y), text, font=f, fill=fill)

    centered(".pub", 200, 190, (255, 255, 255, 255))
    cx, top, bottom = s // 2, s // 2 + 30, s // 2 + 150
    d.rectangle((cx - 22, top, cx + 22, bottom - 40), fill=TEAL)
    d.polygon([(cx - 80, bottom - 60), (cx + 80, bottom - 60), (cx, bottom + 20)], fill=TEAL)
    centered("IDML", s // 2 + 200, 190, (40, 40, 40, 255))
    return img if size == s else img.resize((size, size), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png")
    ap.add_argument("--ico")
    ap.add_argument("--size", type=int, default=256)
    a = ap.parse_args()
    if a.png:
        draw(a.size).save(a.png)
    if a.ico:
        draw(256).save(a.ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
