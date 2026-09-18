"""Click-through curtain overlay.

Draws blurred copies of the flagged regions exactly over those regions, with a
chroma-key background so every other pixel stays visible and clickable. Runs as a
short-lived separate process:

    python -m nsfw_guard.curtain --seconds 4 --src 1920x1200 --patch 100,200,C:\\...\\p.png
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="nsfw-guard curtain overlay")
    parser.add_argument("--patch", action="append", default=[],
                        help="x,y,path/to/patch.png (repeatable)")
    parser.add_argument("--seconds", type=float, default=4.0, help="time on screen")
    parser.add_argument("--src", default="", help="source frame size, WxH (for DPI scaling)")
    parser.add_argument("--key", default="#ff00fe", help="chroma key colour")
    args = parser.parse_args(argv)

    patches = []
    for raw in args.patch:
        parts = raw.split(",", 2)
        if len(parts) != 3:
            continue
        try:
            patches.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    if not patches:
        return 1

    src_w = src_h = 0
    if "x" in args.src:
        try:
            src_w, src_h = [int(v) for v in args.src.split("x", 1)]
        except ValueError:
            src_w = src_h = 0

    try:
        import tkinter as tk
    except Exception:
        return 1

    try:
        root = tk.Tk()
    except Exception:
        return 1

    root.overrideredirect(True)
    width = root.winfo_screenwidth()
    height = root.winfo_screenheight()
    root.geometry("%dx%d+0+0" % (width, height))
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", args.key)
    except Exception:
        pass

    canvas = tk.Canvas(root, width=width, height=height, bg=args.key, highlightthickness=0)
    canvas.pack()

    scale = 1.0
    if src_w and src_h:
        scale = min(width / float(src_w), height / float(src_h))

    keep = []
    for x, y, path in patches:
        try:
            photo = tk.PhotoImage(file=path)
        except Exception:
            continue
        if abs(scale - 1.0) > 1e-3:
            factor = max(1, int(round(1.0 / scale)))
            photo = photo.zoom(factor) if scale < 1.0 else photo.subsample(int(round(scale)))
        canvas.create_image(int(x * scale), int(y * scale), anchor="nw", image=photo)
        keep.append(photo)

    root.after(int(max(1.0, float(args.seconds)) * 1000), root.destroy)
    try:
        root.mainloop()
    except Exception:
        pass

    for _x, _y, path in patches:
        try:
            os.remove(path)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
