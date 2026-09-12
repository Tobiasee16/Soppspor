"""App icons for the installable build.

    python scripts/make_icons.py

Drawn rather than drafted in a design tool so the set can be regenerated when
the palette moves, and so there is no binary blob in the repo whose provenance
nobody remembers. The mark is the map itself: nested suitability zones in the
same single-hue blue ramp the app uses, on its own off-white ground. No red or
green anywhere - CLAUDE.md forbids that ramp in the product, and an icon is the
most-seen surface it has.
"""
from __future__ import annotations

import math
import pathlib
import sys

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "icons"

GROUND = (247, 247, 250)
BANDS = [("#9cc4ef", 1.00), ("#4a8ada", 0.68), ("#123f86", 0.38)]
SS = 4  # supersample, then downsample: the blobs need clean edges


def blob(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
         fill: tuple[int, int, int], seed: float) -> None:
    """A soft irregular shape, so the mark reads as terrain and not a target."""
    pts = []
    for i in range(72):
        a = i / 72 * 2 * math.pi
        wob = (1
               + 0.13 * math.sin(3 * a + seed)
               + 0.07 * math.sin(5 * a - seed * 1.7)
               + 0.04 * math.sin(7 * a + seed * 0.6))
        pts.append((cx + math.cos(a) * r * wob, cy + math.sin(a) * r * wob))
    draw.polygon(pts, fill=fill)


def icon(size: int, maskable: bool = False) -> Image.Image:
    s = size * SS
    im = Image.new("RGB", (s, s), GROUND)
    d = ImageDraw.Draw(im)
    # A maskable icon can be cropped to a circle by the launcher, so everything
    # meaningful has to sit inside the middle 80%.
    scale = 0.62 if maskable else 0.78
    cx, cy = s * 0.5, s * 0.52
    for i, (hexcol, rel) in enumerate(BANDS):
        rgb = tuple(int(hexcol[j:j + 2], 16) for j in (1, 3, 5))
        blob(d, cx, cy, s * 0.5 * scale * rel, rgb, seed=i * 2.1 + 0.4)
    return im.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    for size in (180, 192, 512):
        p = OUT / f"icon-{size}.png"
        icon(size).save(p, "PNG", optimize=True)
        made.append(p)
    p = OUT / "icon-maskable-512.png"
    icon(512, maskable=True).save(p, "PNG", optimize=True)
    made.append(p)

    for p in made:
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size / 1024:.0f} kB")
    print(f"{len(made)} icons written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
