"""Frozen entry point.

PyInstaller runs a plain script, where ``python -m nsfw_guard`` is not in play,
so this keeps the package context intact and hands over to the normal CLI.

    nsfw-guard.exe              # window with the tray in the background
    nsfw-guard.exe --tray       # starts hidden, tray only
    nsfw-guard.exe --curtain …  # internal: the click-through overlay
    nsfw-guard.exe --cli …      # terminal interface
"""

from __future__ import annotations

import os
import sys


def _ensure_streams():
    """A windowed build has no console: keep stray writes from exploding."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass


def main() -> int:
    import multiprocessing

    multiprocessing.freeze_support()
    _ensure_streams()
    from nsfw_guard.__main__ import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
