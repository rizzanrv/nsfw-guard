"""Start with Windows.

The single system-level side effect the project has: one value under
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, written only when the user
turns autostart on - from the tray menu or the settings screen. Nothing else is
touched, nothing leaves the machine.

    from nsfw_guard import autostart
    autostart.set_enabled(True)
"""

from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "nsfw-guard"


def supported() -> bool:
    return sys.platform == "win32"


def _pythonw() -> str:
    """Same interpreter without a console window (pythonw.exe next to python.exe)."""
    python = sys.executable or "python"
    windowed = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.isfile(windowed):
        return windowed
    return python


def launch_command() -> str:
    """Command Windows runs at logon. Always starts hidden, straight into the tray."""
    if getattr(sys, "frozen", False):
        return '"%s" --tray' % sys.executable
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return ('"%s" -c "import sys;sys.path.insert(0,r\'%s\');'
            'from nsfw_guard.__main__ import main;main([\'--tray\'])"' % (_pythonw(), root))


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    return bool(str(value).strip())


def enable() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
        return True
    except OSError:
        return False


def disable() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(flag: bool) -> bool:
    """Turn autostart on/off; returns the state that is in effect afterwards."""
    enable() if flag else disable()
    return is_enabled()
