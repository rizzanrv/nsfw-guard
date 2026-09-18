"""Paths of the packaged image assets (window icon, tray states).

Works both from a source checkout and from a frozen executable: when packaged,
the package directory lives inside the PyInstaller bundle and these paths follow
it automatically.
"""

from __future__ import annotations

import os

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def asset_path(name: str) -> str:
    return os.path.join(ASSETS, name)


def icon_path() -> str:
    """Multi-size .ico for the window, the taskbar and Explorer shortcuts."""
    return asset_path("app.ico")


def tray_path(state: str = "idle") -> str:
    """Single-state tray image (32 px PNG)."""
    return asset_path("tray-%s.png" % state)
