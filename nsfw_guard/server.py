"""Desktop shell: a native WebView2 window with the dark, Apple-like interface.

Front-end assets are loaded straight from the package directory - no HTTP server,
no open ports, nothing to expose. The Python engine is bridged into JavaScript
through pywebview's js_api.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import threading

from .core import (  # noqa: F401
    ALL_CLASSES,
    APP_NAME,
    VERSION,
    Engine,
    Settings,
    list_monitors,
)

WEBUI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
WINDOW_TITLE = "nsfw guard"


class Api:
    """Exposed to the page as window.pywebview.api.*

    Only public *methods* become callable from JavaScript: keep every attribute
    private, otherwise pywebview tries to introspect (and serialise) it on the
    UI thread and the bridge dies with a recursion error.
    """

    def __init__(self, engine: Engine):
        self._engine = engine
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
        return payload

    def update_settings(self, data=None):
        self._engine.settings.update(data or {})
        return self._engine.stats()["settings"]

    def reset_settings(self):
        self._engine.settings.update(Settings.DEFAULTS)
        return self._engine.stats()["settings"]

    # --------------------------------------------------------------- analysis
    def analyse_screen(self):
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
            from .core import data_dir
            path = data_dir()
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - local folder
            return path
        except Exception as exc:
            return "error: %s" % exc

    def quit(self):
        try:
            self._engine.shutdown()
        finally:
            if self._window is not None:
                self._window.destroy()
        return True


def run(args) -> int:
    try:
        import webview
    except ImportError:
        print("pywebview is required for the desktop UI.\n"
              "Install it with:  python -m pip install pywebview\n"
              "Or run the terminal interface:  python -m nsfw_guard --cli --help")
        return 1

    settings = Settings(**{k: v for k, v in vars(args).items() if k in Settings.DEFAULTS})
    engine = Engine(settings)
    api = Api(engine)

    window = webview.create_window(
        WINDOW_TITLE,
        os.path.join(WEBUI, "index.html"),
        js_api=api,
        width=1200,
        height=880,
        min_size=(1020, 720),
        background_color="#0a0a0b",
        text_select=False,
    )
    api._window = window

    threading.Thread(target=engine.load_model, name="model", daemon=True).start()
    engine.start_hotkey()

    def on_closed():
        engine.shutdown()

    try:
        window.events.closed += on_closed
    except Exception:
        pass

    webview.start(debug=bool(getattr(args, "debug", False)), private_mode=False)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nsfw-guard", description="%s %s" % (APP_NAME, VERSION))
    parser.add_argument("--debug", action="store_true", help="open the web inspector")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
