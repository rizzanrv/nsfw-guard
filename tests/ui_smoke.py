"""Manual smoke test for the desktop UI.

Opens the real window, drives a round trip through the JavaScript bridge, reads
back what the page actually sees and saves a screenshot of the desktop.

    python tests/ui_smoke.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import webview  # noqa: E402

from nsfw_guard.core import VERSION, Engine, Settings  # noqa: E402
from nsfw_guard.server import WEBUI, Api  # noqa: E402

SHOT = os.path.join(ROOT, "ui_smoke.png")
results = {}


def window_rect(title: str):
    """Screen rectangle of a top level window (physical pixels), or None."""
    try:
        import ctypes
        from ctypes import wintypes
        handle = ctypes.windll.user32.FindWindowW(None, title)
        if not handle:
            return None
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def screenshot(title: str = "nsfw guard — smoke test"):
    """Capture just the application window when possible, the full screen otherwise."""
    try:
        import cv2
        import mss
        import numpy as np
        rect = window_rect(title)
        with mss.mss() as session:
            if rect:
                left, top, right, bottom = rect
                region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
            else:
                region = session.monitors[1]
            image = np.array(session.grab(region))
        cv2.imwrite(SHOT, image)
        results["screenshot"] = SHOT
    except Exception as exc:
        results["screenshot"] = "failed: %s" % exc


def probe(window):
    time.sleep(7)
    window.evaluate_js("window.pywebview.api.get_state().then(s => { window.__probe = s; }); 1")
    window.evaluate_js("window.pywebview.api.update_settings({threshold: 0.55})"
                       ".then(s => { window.__action = s; }); 1")
    time.sleep(4)
    checks = {
        "frames": "document.getElementById('stat-frames').textContent",
        "monitor_options": "document.getElementById('monitor-select').options.length",
        "class_chips": "document.getElementById('class-chips').childElementCount",
        "toasts": "document.querySelectorAll('.toast').length",
        "scroll_y": "window.scrollY",
        "diag": "JSON.stringify(window.__nsfwGuard || null)",
        "bridge_version": "window.__probe ? window.__probe.version : 'NO_STATE'",
        "roundtrip_threshold": "window.__action ? window.__action.threshold : 'NO_ACTION'",
        "views": "document.querySelectorAll('.view').length",
        "active_view": "document.querySelector('.view.is-active').dataset.view",
        "tray_field": "String(window.__probe.tray)",
        "autostart_field": "String(window.__probe.autostart)",
        "icons": "document.querySelectorAll('svg.i use').length",
        "nav_items": "document.querySelectorAll('.nav-item').length",
        "tiles": "document.querySelectorAll('.tile').length",
        "status_watch": "document.getElementById('status-watch').textContent",
    }
    for key, js in checks.items():
        try:
            results[key] = window.evaluate_js(js)
        except Exception as exc:
            results[key] = "eval failed: %s" % exc
    screenshot()
    window.destroy()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import faulthandler
    faulthandler.dump_traceback_later(14, exit=False, file=sys.stderr)
    engine = Engine(Settings())
    api = Api(engine)

    # wrap the bridged methods so every call is traced, hangs included
    for name in ("get_state", "update_settings", "analyse_screen", "analyse_clipboard", "open_data_dir"):
        original = getattr(api, name)

        def logged(*args, _name=name, _original=original, **kwargs):
            print("PY: %s -> enter" % _name, flush=True)
            started = time.time()
            try:
                value = _original(*args, **kwargs)
            except Exception as exc:
                print("PY: %s -> raised %r" % (_name, exc), flush=True)
                raise
            preview = str(value)[:120].replace("\n", " ")
            print("PY: %s -> done in %.2fs %s" % (_name, time.time() - started, preview), flush=True)
            return value

        setattr(api, name, logged)
    window = webview.create_window(
        "nsfw guard — smoke test", os.path.join(WEBUI, "index.html"),
        js_api=api, width=1200, height=880, background_color="#0a0a0b")
    api._window = window
    threading.Thread(target=engine.load_model, name="model", daemon=True).start()
    webview.start(probe, window)

    for key in sorted(results):
        print("%-20s %s" % (key, results[key]))
    ok = (results.get("bridge_version") == VERSION
          and results.get("roundtrip_threshold") == 0.55
          and results.get("tiles") == 4)
    print("smoke:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
