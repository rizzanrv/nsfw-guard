"""Draw the application icon set.

Pillow only, no external assets: the shield, the lens and every export come from
the same geometry, so the tray, the window, the Explorer entry and the executable
never drift apart.

    python tools/make_icons.py            # refresh nsfw_guard/assets/*
    python tools/make_icons.py --preview  # also drop a contact sheet next to it

Exports
    assets/app.ico          16/20/24/32/40/48/64/128/256 - window, exe, shortcuts
    assets/icon-256.png     README and other documentation
    assets/tray-*.png       32 px tray states (idle, watch, alert, muted)
"""

from __future__ import annotations

import argparse
import math
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "nsfw_guard", "assets")

SS = 4                # supersampling factor: draw big, shrink for smooth edges
CANVAS = 256          # logical size of the master drawing

STATE_COLORS = {      # state -> gradient (top, bottom) of the shield
    "idle": ((150, 158, 173), (104, 112, 128)),
    "watch": ((108, 165, 255), (58, 118, 226)),
    "alert": ((255, 122, 108), (215, 55, 46)),
    "muted": ((124, 130, 142), (84, 89, 99)),
}

TILE_TOP = (30, 33, 40)
TILE_BOTTOM = (13, 15, 19)
INK = (11, 13, 17)


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _vertical_gradient(size, top, bottom):
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        grad.putpixel((0, y), _lerp(top, bottom, y / float(max(1, size - 1))))
    return grad.resize((size, size), Image.NEAREST)


def _quad(p0, p1, p2, steps):
    """Sampled quadratic bezier (used for the tapered flanks of the shield)."""
    out = []
    for i in range(1, steps + 1):
        t = i / float(steps)
        u = 1.0 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def _shield_points(size, top=None, bottom=None, half=None, steps=48):
    """Shield outline: rounded shoulders, short vertical flanks, pointed tip."""
    cx = size * 0.5
    top = size * 0.13 if top is None else top
    bottom = size * 0.885 if bottom is None else bottom
    half = size * 0.335 if half is None else half
    shoulder = size * 0.062
    flank_end = top + (bottom - top) * 0.52      # where the taper starts

    right = []
    for i in range(17):                          # rounded shoulder
        rad = math.radians((i / 16.0) * 90.0)
        right.append((cx + half - shoulder + shoulder * math.sin(rad),
                      top + shoulder - shoulder * math.cos(rad)))
    right.append((cx + half, flank_end))         # straight flank
    right += _quad((cx + half, flank_end),
                   (cx + half * 0.95, bottom - (bottom - flank_end) * 0.56),
                   (cx, bottom - size * 0.008), steps)   # taut taper to the tip
    return right + [(2 * cx - x, y) for (x, y) in reversed(right)]


def _lens_points(size, cx, cy, half_width, half_height, steps=60):
    """Lens (eye) outline from two mirrored arcs."""
    upper, lower = [], []
    for i in range(steps + 1):
        x = cx - half_width + 2 * half_width * (i / float(steps))
        norm = abs((x - cx) / half_width)
        bulge = half_height * math.sqrt(max(0.0, 1.0 - norm ** 2))
        upper.append((x, cy - bulge))
        lower.append((x, cy + bulge))
    return upper + list(reversed(lower))


def draw_shield(size, colour, eye=True, slash=False):
    """Shield mark on a transparent background; colour is a (top, bottom) pair."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    outline = _shield_points(size)
    body = Image.new("L", (size, size), 0)
    ImageDraw.Draw(body).polygon(outline, fill=255)
    img.paste(_vertical_gradient(size, colour[0], colour[1]).convert("RGBA"), (0, 0), body)

    edge = Image.new("L", (size, size), 0)
    ImageDraw.Draw(edge).line(outline + [outline[0]], fill=110, width=max(1, int(size * 0.022)))
    img.alpha_composite(Image.composite(
        Image.new("RGBA", (size, size), INK + (255,)),
        Image.new("RGBA", (size, size), (0, 0, 0, 0)), edge))

    if eye:
        cx, cy = size * 0.5, size * 0.455
        lens = Image.new("L", (size, size), 0)
        ImageDraw.Draw(lens).polygon(
            _lens_points(size, cx, cy, size * 0.185, size * 0.115), fill=255)
        img.paste(Image.new("RGBA", (size, size), INK + (255,)), (0, 0), lens)
        pupil = Image.new("L", (size, size), 0)
        ImageDraw.Draw(pupil).ellipse(
            [cx - size * 0.058, cy - size * 0.058, cx + size * 0.058, cy + size * 0.058], fill=255)
        img.paste(Image.new("RGBA", (size, size), (247, 250, 253, 255)), (0, 0), pupil)

    if slash:
        cut = Image.new("L", (size, size), 0)
        ImageDraw.Draw(cut).line([(size * 0.24, size * 0.80), (size * 0.76, size * 0.22)],
                                 fill=255, width=int(size * 0.085))
        img.paste(Image.new("RGBA", (size, size), (0, 0, 0, 0)), (0, 0), cut)
    return img


def draw_tile(size, colour, eye=True):
    """Dark rounded tile with the shield on it - window, taskbar, Explorer, exe."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    body = Image.new("L", (size, size), 0)
    ImageDraw.Draw(body).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=int(size * 0.235), fill=255)
    img.paste(_vertical_gradient(size, TILE_TOP, TILE_BOTTOM).convert("RGBA"), (0, 0), body)
    inner = size * 0.66
    shield = draw_shield(int(inner), colour, eye=eye)
    img.alpha_composite(shield, (int((size - inner) / 2),
                                 int((size - inner) / 2) - int(size * 0.005)))
    return img


def build(preview=False):
    os.makedirs(ASSETS, exist_ok=True)
    master = draw_tile(CANVAS, STATE_COLORS["idle"])
    written = []

    ico = os.path.join(ASSETS, "app.ico")
    master.resize((256, 256), Image.LANCZOS).save(
        ico, format="ICO", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
                                  (48, 48), (64, 64), (128, 128), (256, 256)])
    written.append(ico)

    png = os.path.join(ASSETS, "icon-256.png")
    master.resize((256, 256), Image.LANCZOS).save(png)
    written.append(png)

    for state, colour in STATE_COLORS.items():
        mark = draw_shield(128 * SS, colour, slash=(state == "muted")).resize((32, 32), Image.LANCZOS)
        path = os.path.join(ASSETS, "tray-%s.png" % state)
        mark.save(path)
        written.append(path)

    if preview:
        sheet = Image.new("RGBA", (128 * len(STATE_COLORS) + 48, 160), (18, 20, 24, 255))
        for i, state in enumerate(STATE_COLORS):
            mark = draw_shield(96 * SS, STATE_COLORS[state], slash=(state == "muted"))
            sheet.alpha_composite(mark.resize((96, 96), Image.LANCZOS), (16 + i * 128, 32))
        sheet.alpha_composite(draw_tile(96, STATE_COLORS["idle"]),
                              (16 + len(STATE_COLORS) * 128, 32))
        path = os.path.join(ASSETS, "preview.png")
        sheet.save(path)
        written.append(path)

    for path in written:
        print("%-46s %7.1f KB" % (os.path.relpath(path, ROOT), os.path.getsize(path) / 1024.0))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="draw the nsfw-guard icon set")
    parser.add_argument("--preview", action="store_true", help="also write assets/preview.png")
    args = parser.parse_args(argv)
    return build(preview=args.preview)


if __name__ == "__main__":
    sys.exit(main())
