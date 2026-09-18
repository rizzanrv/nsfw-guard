"""Desktop shell: a native WebView2 window with the dark interface, plus a tray
icon so the app can keep working in the background.

Front-end assets are loaded straight from the package directory - no HTTP server,
no open ports, nothing to expose. The engine is bridged into JavaScript through
pywebview's js_api; the tray drives the same engine through a small shell object,
so the window and the tray can never disagree about the state.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import threading

from . import autostart
from .assets import icon_path
from .core import (  # noqa: F401
    ALL_CLASSES,
    APP_NAME,
    VERSION,
    Engine,
    Settings,
    data_dir,
    list_monitors,
    log_line,
)
from .tray import TrayIcon

WEBUI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
WINDOW_TITLE = "nsfw guard"


class Api:
    """Exposed to the page as window.pywebview.api.*

    Only public *methods* become callable from JavaScript: keep every attribute
    private, otherwise pywebview tries to introspect (and serialise) it on the
    UI thread and the bridge dies with a recursion error.
    """

    def __init__(self, engine: Engine, shell=None):
        self._engine = engine
        self._shell = shell
        self._window = None

    # ------------------------------------------------------------------ state
    def get_state(self):
        try:
            payload = self._engine.stats()
        except Exception:
            import traceback
            return {"bridge_error": traceback.format_exc(), "ready": False,
                    "frames": 0, "hits": 0, "watching": False,
                    "settings": self._engine.settings.to_dict(), "last": None,
                    "events": [], "log": [], "classes": ALL_CLASSES,
                    "class_labels": {}, "default_explicit": [],
                    "hotkey": "", "data_dir": "", "version": VERSION, "monitors": []}
        try:
            payload["monitors"] = list_monitors()
        except Exception as exc:
            payload["monitors"] = []
            payload["monitors_error"] = str(exc)
        payload["tray"] = bool(self._shell and self._shell.tray_running())
        payload["autostart"] = autostart.is_enabled()
        payload["autostart_supported"] = autostart.supported()
        if self._shell is not None:
            payload["window_visible"] = self._shell.window_visible()
        return payload

    def update_settings(self, data=None):
        self._engine.settings.update(data or {})
        self._engine.settings.save()
        return self._engine.stats()["settings"]

    def reset_settings(self):
        self._engine.settings.update(Settings.DEFAULTS)
        self._engine.settings.save()
        return self._engine.stats()["settings"]

    def set_autostart(self, flag=None):
        """Turn the logon entry on/off. Returns the state that is in effect."""
        if flag is None:
            return {"autostart": autostart.is_enabled()}
        return {"autostart": autostart.set_enabled(bool(flag)), "changed": True}

    def hide_window(self):
        if self._shell is not None:
            self._shell.hide_window()
        return True

    def show_window(self):
        if self._shell is not None:
            self._shell.show_window()
        return True

    # --------------------------------------------------------------- analysis
    def analyse_screen(self, monitor=None):
        if monitor is not None:
            try:
                self._engine.settings.update({"monitor": int(monitor)})
                self._engine.settings.save()
            except Exception:
                pass
        return self._engine.analyse_screen("screen")

    def analyse_clipboard(self):
        return self._engine.analyse_clipboard()

    def analyse_file(self, name, base64_data):
        try:
            raw = base64_data.split(",", 1)[1] if "," in base64_data[:64] else base64_data
            return self._engine.analyse_bytes(base64.b64decode(raw), name or "upload")
        except Exception as exc:
            return {"error": "cannot read the file: %s" % exc, "source": "file"}

    def pick_file(self):
        """Native open-file dialog; returns the analysed result or an error."""
        try:
            import webview
            windows = webview.windows
            window = windows[0] if windows else None
            if window is None:
                return {"error": "no window"}
            paths = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Images (*.jpg;*.jpeg;*.png;*.bmp;*.webp;*.gif)", "All files (*.*)"))
            if not paths:
                return {"error": "", "cancelled": True, "source": "file"}
            path = paths[0] if isinstance(paths, (list, tuple)) else paths
            return self._engine.analyse_file(path)
        except Exception as exc:
            return {"error": "file dialog failed: %s" % exc, "source": "file"}

    # ---------------------------------------------------------------- control
    def start_watch(self):
        self._engine.start_watch()
        return self._engine.stats()

    def stop_watch(self):
        self._engine.stop_watch()
        return self._engine.stats()

    def open_data_dir(self):
        try:
            path = data_dir()
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - local folder
            return path
        except Exception as exc:
            return "error: %s" % exc

    def quit(self):
        if self._shell is not None:
            self._shell.quit()
        else:
            try:
                self._engine.shutdown()
            finally:
                if self._window is not None:
                    self._window.destroy()
        return True


class Shell:
    """Window and tray glue: every action the tray menu can trigger lives here."""

    def __init__(self, engine: Engine, settings: Settings):
        self._engine = engine
        self._settings = settings
        self._window = None
        self._tray = None
        self._visible = True
        self._hint_shown = False
        self.quitting = False

    def attach(self, window, tray):
        self._window = window
        self._tray = tray

    # -------------------------------------------------------------- state
    def tray_running(self) -> bool:
        return bool(self._tray and self._tray.running)

    def window_visible(self) -> bool:
        window = self._window
        if window is None:
            return False
        try:
            native = getattr(window, "native", None)
            if native is not None:
                return bool(native.Visible)
        except Exception:
            pass
        return self._visible

    @property
    def autostart(self):
        return autostart.is_enabled

    @property
    def autostart_supported(self) -> bool:
        return autostart.supported()

    # ------------------------------------------------------------- window
    def show_window(self):
        window = self._window
        if window is None:
            return
        try:
            window.show()
            window.restore()
        except Exception:
            pass
        try:
            window.native.Activate()          # raise it above other windows
        except Exception:
            pass
        self._visible = True

    def hide_window(self):
        window = self._window
        if window is None:
            return
        try:
            window.hide()
            self._visible = False
        except Exception:
            pass

    def toggle_window(self):
        if self.window_visible():
            self.hide_window()
        else:
            self.show_window()

    def on_closing(self, *_args, **_kwargs) -> bool:
        """Return False to cancel the close and retreat to the tray instead."""
        if self.quitting or not self.tray_running():
            return True
        if not self._settings.get("tray_close", True):
            return True
        self.hide_window()
        log_line("window hidden to tray")
        if not self._hint_shown:
            self._hint_shown = True
            self._tray.notify("nsfw guard продолжает работать",
                              "Окно скрыто в трей: проверки и слежение идут дальше.")
        return False

    # -------------------------------------------------------------- actions
    def check_now(self):
        """Analyse the screen without blocking the tray thread."""
        threading.Thread(target=self._engine.analyse_screen, args=("tray",),
                         name="tray-check", daemon=True).start()

    def toggle_watch(self):
        if self._engine.watching():
            self._engine.stop_watch()
        else:
            self._engine.start_watch()

    def toggle(self, key, value=None):
        current = bool(self._settings.get(key, True))
        target = (not current) if value is None else bool(value)
        self._settings.update({key: target})
        self._settings.save()
        return target

    def toggle_autostart(self, value=None):
        target = (not autostart.is_enabled()) if value is None else bool(value)
        autostart.set_enabled(target)
        return autostart.is_enabled()

    def open_data_dir(self):
        try:
            path = data_dir()
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - local folder
            return path
        except Exception as exc:
            return "error: %s" % exc

    def open_settings(self):
        """Show the window and ask the page to switch to the settings view."""
        self.show_window()
        window = self._window
        if window is not None:
            try:
                window.evaluate_js("window.__nsfwGuardGoto && window.__nsfwGuardGoto('settings')")
            except Exception:
                pass

    def quit(self):
        self.quitting = True
        if self._tray is not None:
            self._tray.stop()
        try:
            self._engine.shutdown()
        except Exception:
            pass
        window = self._window
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass


def run(args) -> int:
    try:
        import webview
    except ImportError:
        print("pywebview is required for the desktop UI.\n"
              "Install it with:  python -m pip install pywebview\n"
              "Or run the terminal interface:  python -m nsfw_guard --cli --help")
        return 1

    settings = Settings.load()
    settings.update({k: v for k, v in vars(args).items() if k in Settings.DEFAULTS})
    engine = Engine(settings)
    shell = Shell(engine, settings)
    api = Api(engine, shell)

    log_line("start %s (frozen=%s, python=%s, hidden=%s)" % (
        VERSION, bool(getattr(sys, "frozen", False)), sys.version.split()[0],
        bool(getattr(args, "tray", False))))

    start_hidden = bool(getattr(args, "tray", False))
    window = webview.create_window(
        WINDOW_TITLE,
        os.path.join(WEBUI, "index.html"),
        js_api=api,
        width=1200,
        height=880,
        min_size=(1024, 700),
        background_color="#0d0e11",
        text_select=False,
        hidden=start_hidden,
    )
    api._window = window

    tray = None
    if not getattr(args, "no_tray", False):
        tray = TrayIcon(engine, shell)
        if tray.start():
            engine.alert_hooks.append(tray.announce)
            log_line("tray icon created")
        else:
            tray = None
            log_line("tray unavailable (pystray missing or the shell refused the icon)")
    shell.attach(window, tray)

    if start_hidden and tray is None:      # no tray: never start invisible
        try:
            window.show()
        except Exception:
            pass

    try:
        window.events.closing += shell.on_closing
    except Exception:
        pass

    threading.Thread(target=engine.load_model, name="model", daemon=True).start()
    engine.start_hotkey()

    def after_start():
        if tray is not None and start_hidden:
            tray.notify("nsfw guard запущен в фоне",
                        "Значок в трее. Ctrl+Alt+S — проверить экран, меню значка — выход.")

    try:
        webview.start(after_start, debug=bool(getattr(args, "debug", False)),
                      private_mode=False, icon=icon_path())
    finally:
        if tray is not None:
            tray.stop()
        engine.shutdown()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nsfw-guard",
        description="%s %s - offline nudity detection for screens, files and the clipboard"
                    % (APP_NAME, VERSION))
    parser.add_argument("--debug", action="store_true", help="open the web inspector")
    parser.add_argument("--tray", action="store_true",
                        help="start hidden, working from the notification area")
    parser.add_argument("--no-tray", dest="no_tray", action="store_true",
                        help="do not create a tray icon (close really closes)")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
