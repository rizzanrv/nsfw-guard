"""Tray icon: live status, quick actions, notifications.

pystray runs on its own thread with its own message loop; the application and
the engine stay unaware of tray details - everything they expose is read through
the `shell` object handed to :class:`TrayIcon`.

States
    idle   - nothing is being watched
    watch  - screen watching is on
    alert  - a hit happened in the last ALERT_SECONDS seconds
    muted  - notifications are switched off
"""

from __future__ import annotations

import threading
import time

try:
    import pystray
    from pystray import Menu, MenuItem
except Exception:            # pragma: no cover - optional dependency
    pystray = None
    Menu = MenuItem = None

from .assets import tray_path

ALERT_SECONDS = 25.0
REFRESH_SECONDS = 1.5
FALLBACK_COLORS = {
    "idle": (120, 128, 142),
    "watch": (78, 141, 233),
    "alert": (226, 66, 56),
    "muted": (96, 100, 110),
}


def _fallback_image(state):
    """Drawn dot used when an icon asset is missing, so the tray never dies."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, 29, 29], fill=FALLBACK_COLORS.get(state, FALLBACK_COLORS["idle"]) + (255,))
    draw.ellipse([12, 12, 19, 19], fill=(250, 250, 252, 255))
    return img


def load_image(state: str):
    try:
        from PIL import Image
        return Image.open(tray_path(state)).convert("RGBA")
    except Exception:
        return _fallback_image(state)


class TrayIcon:
    """Owns the tray icon. All public methods are safe to call from any thread."""

    def __init__(self, engine, shell):
        self._engine = engine
        self._shell = shell
        self._icon = None
        self._thread = None
        self._stop = threading.Event()
        self._images = {}
        self._state = ""
        self._title = ""
        self._alert_until = 0.0

    # -------------------------------------------------------------- lifecycle
    @property
    def available(self) -> bool:
        return pystray is not None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        """Show the icon (idempotent). Returns False when pystray is unavailable."""
        if not self.available:
            return False
        if self.running:
            return True
        self._state = self.state()
        self._title = self._tooltip()
        try:
            self._icon = pystray.Icon("nsfw-guard", self._image(self._state),
                                      self._title, self._menu())
        except Exception:
            self._icon = None
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tray", daemon=True)
        self._thread.start()
        threading.Thread(target=self._refresh_loop, name="tray-status", daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop.set()
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def _loop(self) -> None:
        try:
            self._icon.run()
        except Exception:
            pass

    def _refresh_loop(self) -> None:
        while not self._stop.wait(REFRESH_SECONDS):
            self.refresh()

    # ------------------------------------------------------------- appearance
    def _image(self, state: str):
        if state not in self._images:
            self._images[state] = load_image(state)
        return self._images[state]

    def _settings(self, key, default=None):
        try:
            return self._engine.settings.get(key, default)
        except Exception:
            return default

    def _watching(self) -> bool:
        try:
            return bool(self._engine.watching())
        except Exception:
            return False

    def _ready(self) -> bool:
        try:
            return bool(self._engine.ready())
        except Exception:
            return False

    def state(self) -> str:
        if self._alert_until > time.time():
            return "alert"
        if not self._settings("notify", True):
            return "muted"
        return "watch" if self._watching() else "idle"

    def _tooltip(self) -> str:
        try:
            stats = self._engine.stats()
        except Exception:
            return "nsfw guard"
        if stats.get("error"):
            head = "модель не загрузилась"
        elif stats.get("ready"):
            head = "слежение включено" if stats.get("watching") else "слежение выключено"
        else:
            head = "модель загружается"
        return "nsfw guard — %s\nкадров %s · находок %s" % (
            head, stats.get("frames", 0), stats.get("hits", 0))

    def refresh(self) -> None:
        """Repaint icon and tooltip from the current engine state."""
        icon = self._icon
        if icon is None:
            return
        state = self.state()
        if state != self._state:
            self._state = state
            try:
                icon.icon = self._image(state)
            except Exception:
                pass
            try:
                icon.update_menu()
            except Exception:
                pass
        title = self._tooltip()
        if title != self._title:
            self._title = title
            try:
                icon.title = title
            except Exception:
                pass

    # ------------------------------------------------------------------ menu
    def _window_label(self, item=None) -> str:
        try:
            visible = bool(self._shell.window_visible())
        except Exception:
            visible = True
        return "Скрыть окно" if visible else "Показать окно"

    def _menu(self):
        """The tray menu. Checkbox states are read live, every refresh."""
        if Menu is None:
            return None
        return Menu(
            MenuItem(lambda item: self._window_label(), self._call("toggle_window"), default=True),
            MenuItem("Проверить экран сейчас", self._call("check_now"),
                     enabled=lambda item: self._ready()),
            MenuItem("Слежение за экраном", self._call("toggle_watch"),
                     checked=lambda item: self._watching()),
            Menu.SEPARATOR,
            MenuItem("Сворачивать в трей при закрытии", self._call("toggle", "tray_close"),
                     checked=lambda item: bool(self._settings("tray_close", True))),
            MenuItem("Уведомления о находках", self._call("toggle", "notify"),
                     checked=lambda item: bool(self._settings("notify", True))),
            MenuItem("Запускать при входе в Windows", self._call("toggle_autostart"),
                     checked=lambda item: self._autostart(),
                     enabled=lambda item: bool(getattr(self._shell, "autostart_supported", True))),
            Menu.SEPARATOR,
            MenuItem("Открыть папку данных", self._call("open_data_dir")),
            MenuItem("Настройки", self._call("open_settings")),
            Menu.SEPARATOR,
            MenuItem("Выход", self._call("quit")),
        )

    def _autostart(self) -> bool:
        try:
            return bool(self._shell.autostart())
        except Exception:
            return False

    def _call(self, name, *args):
        """Build a menu action that never lets an exception kill the tray thread."""
        def action(icon=None, item=None):
            handler = getattr(self._shell, name, None)
            if handler is None:
                return
            try:
                handler(*args)
            except Exception:
                pass
            finally:
                self.refresh()
        return action

    # ----------------------------------------------------------- notifications
    def notify(self, title: str, text: str = "") -> bool:
        """Balloon tip in the notification area (Windows keeps it in the history)."""
        if not self._settings("notify", True):
            return False
        icon = self._icon
        if icon is None:
            return False
        try:
            icon.notify(text or title, title)
            return True
        except Exception:
            return False

    def announce(self, result, source: str = "") -> None:
        """Engine hook: called for every hit, whatever the source."""
        self._alert_until = time.time() + ALERT_SECONDS
        self.refresh()
        if self._settings("notify", True) and not self._shell.window_visible():
            top = (result or {}).get("top") or {}
            label = str(top.get("class") or "").replace("_", " ").lower()
            score = float(top.get("score") or 0.0)
            self.notify("Найден откровенный контент",
                        "%s · %.2f · %s" % (label or "detection", score, source or "экран"))
