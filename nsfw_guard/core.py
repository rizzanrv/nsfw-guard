"""nsfw-guard core engine.

Fully offline on-device NSFW (nudity) detection for screens and images.
No network access anywhere in this package: the ONNX model ships inside the
`nudenet` wheel and inference runs on CPU through onnxruntime.

Public API used by the UI, CLI and tools:
    Detector          - model wrapper (single + multi-exposure inference)
    Settings          - user settings container
    Engine            - screen watching, alerts, curtain, hotkey, stats
    capture_screen()  - grab a monitor as an BGRA numpy array
    load_image()      - read an image file into a BGR array
    list_monitors()   - enumerate monitors
"""

from __future__ import annotations

import json
import os
import platform
import queue
import sys
import threading
import time

try:
    import ctypes
except ImportError:  # pragma: no cover - non-Windows
    ctypes = None

APP_NAME = "nsfw-guard"
VERSION = "1.0.0"

# --------------------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------------------


def data_dir() -> str:
    """User data directory (logs, optional flagged frames). Override with NSFW_GUARD_HOME."""
    env = os.environ.get("NSFW_GUARD_HOME")
    if env:
        path = os.path.abspath(env)
    elif platform.system() == "Windows":
        path = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
    else:
        path = os.path.join(os.path.expanduser("~"), ".local", "share", APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def log_path() -> str:
    return os.path.join(data_dir(), "events.log")


def flagged_dir() -> str:
    path = os.path.join(data_dir(), "flagged")
    os.makedirs(path, exist_ok=True)
    return path


# --------------------------------------------------------------------------------------
# classes (order matters: it is the model's output order)
# --------------------------------------------------------------------------------------

ALL_CLASSES = [
    "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED",
    "ANUS_EXPOSED", "FEET_EXPOSED", "BELLY_COVERED", "FEET_COVERED",
    "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE", "BELLY_EXPOSED",
    "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED",
    "BUTTOCKS_COVERED",
]

#: classes treated as explicit by default
DEFAULT_EXPLICIT = [
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
]

#: human readable names (UI). English + Russian pairs.
CLASS_LABEL = {
    "FEMALE_GENITALIA_EXPOSED": "female genitalia exposed",
    "MALE_GENITALIA_EXPOSED": "male genitalia exposed",
    "FEMALE_BREAST_EXPOSED": "female breast exposed",
    "BUTTOCKS_EXPOSED": "buttocks exposed",
    "ANUS_EXPOSED": "anus exposed",
    "MALE_BREAST_EXPOSED": "male breast exposed",
    "BELLY_EXPOSED": "belly exposed",
    "FEET_EXPOSED": "feet exposed",
    "ARMPITS_EXPOSED": "armpits exposed",
    "FACE_FEMALE": "face (female)",
    "FACE_MALE": "face (male)",
}

HOTKEY_VK = 0x53            # 'S'
HOTKEY_MODS = (0x11, 0x12)  # Ctrl + Alt
HOTKEY_TEXT = "Ctrl+Alt+S"

# --------------------------------------------------------------------------------------
# low level helpers
# --------------------------------------------------------------------------------------


def _mss_session():
    import mss
    factory = getattr(mss, "MSS", None) or getattr(mss, "mss")
    return factory()


def list_monitors() -> list:
    """Enumerate monitors: index 0 = all monitors combined, 1..N = individual."""
    with _mss_session() as sct:
        return [{"index": i, "left": m["left"], "top": m["top"],
                 "width": m["width"], "height": m["height"]}
                for i, m in enumerate(sct.monitors)]


def capture_screen(monitor: int = 1):
    """Grab a monitor as a BGRA numpy array (no disk writes)."""
    import numpy as np
    with _mss_session() as sct:
        monitors = sct.monitors
        idx = monitor if 0 <= monitor < len(monitors) else 0
        return np.array(sct.grab(monitors[idx]))


def decode_image(data: bytes):
    """Decode raw image bytes into a BGR numpy array."""
    import cv2
    import numpy as np
    if not data:
        raise ValueError("empty image data")
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("unsupported or corrupted image format")
    return arr


def load_image(path: str):
    with open(path, "rb") as fh:
        return decode_image(fh.read())


def clipboard_image():
    """Image (or image file) currently in the Windows clipboard, or None."""
    from PIL import ImageGrab
    import numpy as np
    data = ImageGrab.grabclipboard()
    if data is None:
        return None
    if isinstance(data, list):
        for item in data:
            if os.path.isfile(item):
                return load_image(item)
        return None
    rgb = np.array(data.convert("RGB"))
    return rgb[:, :, ::-1].copy()


def hotkey_pressed(vk: int = HOTKEY_VK, mods=HOTKEY_MODS) -> bool:
    """True while the hotkey combo is held down (polled, no global hooks)."""
    if ctypes is None:
        return False
    try:
        user32 = ctypes.windll.user32
        for code in mods:
            if not (user32.GetAsyncKeyState(code) & 0x8000):
                return False
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def log_event(source: str, result: dict, note: str = "") -> None:
    """Append one line to the local event log (no frames, only metadata)."""
    try:
        top = result.get("top") or {}
        line = "[%s] source=%s verdict=%s score=%.3f class=%s ms=%d passes=%s thr=%.2f %s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), source,
            "NSFW" if result.get("verdict") else "ok",
            float(top.get("score", 0.0)), top.get("class", "-"),
            int(result.get("ms", 0)), result.get("passes", 1),
            float(result.get("threshold", 0.0)), note)
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def read_log(limit: int = 60) -> list:
    try:
        with open(log_path(), "r", encoding="utf-8") as fh:
            return fh.read().splitlines()[-int(limit):]
    except Exception:
        return []


def save_flagged(image, source: str = "screen"):
    """Store the frame that triggered an alert (opt-in, off by default)."""
    try:
        import cv2
        name = time.strftime("%Y%m%d-%H%M%S") + "-" + source + ".png"
        path = os.path.join(flagged_dir(), name)
        cv2.imwrite(path, image)
        return path
    except Exception:
        return None


def _iou(a, b) -> float:
    ax, ay, aw, ah = [float(v) for v in a]
    bx, by, bw, bh = [float(v) for v in b]
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0

class Detector:
    """NudeNet 320n wrapper with optional multi-exposure inference."""

    _lut = {}   # gamma LUT cache, shared between instances

    def __init__(self, threshold: float = 0.35, explicit=None, resolution: int = 320):
        from nudenet import NudeDetector
        self.model = NudeDetector(inference_resolution=resolution)
        self.threshold = float(threshold)
        self.explicit = set(explicit or DEFAULT_EXPLICIT)

    @staticmethod
    def brighten(image, gain: float):
        """Gamma lift used by the multi-exposure pass (dark frames)."""
        if gain == 1.0:
            return image
        import numpy as np
        lut = Detector._lut.get(gain)
        if lut is None:
            lut = np.array([min(255, int(round(255.0 * ((i / 255.0) ** (1.0 / float(gain))))))
                            for i in range(256)], np.uint8)
            Detector._lut[gain] = lut
        return lut[image]

    def analyse(self, image, multi: bool = False, gains=(1.0, 1.8, 3.2)) -> dict:
        """Detect on the frame as-is and (when multi) on gamma-lifted copies.

        Results are merged by class + IoU so a detection that fades out in a dark
        frame is still reported (max score wins)."""
        t0 = time.time()
        passes = list(gains) if multi else [1.0]
        merged = []
        for gain in passes:
            frame = image if gain == 1.0 else self.brighten(image, gain)
            for det in self.model.detect(frame):
                box = det.get("box") or [0, 0, 0, 0]
                found = None
                for i, prev in enumerate(merged):
                    if prev.get("class") == det.get("class") and _iou(prev["box"], box) >= 0.5:
                        found = i
                        break
                if found is None:
                    merged.append(det)
                elif float(det.get("score", 0)) > float(merged[found].get("score", 0)):
                    merged[found] = det
        flagged = [d for d in merged
                   if d.get("class") in self.explicit and float(d.get("score", 0)) >= self.threshold]
        flagged.sort(key=lambda d: -float(d["score"]))
        top = flagged[0] if flagged else (max(merged, key=lambda d: float(d["score"])) if merged else None)
        return {
            "verdict": bool(flagged),
            "flagged": flagged,
            "detections": merged,
            "top": top,
            "score": float(top["score"]) if top else 0.0,
            "ms": int((time.time() - t0) * 1000),
            "threshold": self.threshold,
            "explicit_classes": sorted(self.explicit),
            "passes": len(passes),
        }


class Settings:
    """User settings shared by the UI, CLI and engine (plain dict underneath)."""

    DEFAULTS = {
        "threshold": 0.35,
        "monitor": 1,
        "interval": 3.0,
        "multi": True,
        "curtain": True,
        "curtain_seconds": 4,
        "sound": True,
        "save": False,
        "log_all": False,
        "explicit": list(DEFAULT_EXPLICIT),
    }

    def __init__(self, **kwargs):
        self._data = dict(self.DEFAULTS)
        self._data["explicit"] = list(DEFAULT_EXPLICIT)
        self.update(kwargs)

    def update(self, data: dict) -> dict:
        for key, value in (data or {}).items():
            if key == "explicit":
                keep = [c for c in (value or []) if c in ALL_CLASSES]
                self._data["explicit"] = keep or list(DEFAULT_EXPLICIT)
            elif key == "threshold":
                self._data["threshold"] = min(0.95, max(0.05, float(value)))
            elif key == "interval":
                self._data["interval"] = min(120.0, max(1.0, float(value)))
            elif key == "curtain_seconds":
                self._data["curtain_seconds"] = min(30, max(1, int(value)))
            elif key in self.DEFAULTS:
                self._data[key] = value
        return self.to_dict()

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        return dict(self._data)

CURTAIN_KEY = "#ff00fe"   # chroma key: those pixels are transparent and click-through


def purge_curtain_files(folder: str, older_than: float = 120.0) -> None:
    """Remove leftover curtain patch files from previous runs."""
    try:
        now = time.time()
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if now - os.path.getmtime(path) > older_than:
                    os.remove(path)
            except Exception:
                continue
    except Exception:
        pass


def make_curtain_patches(image, detections, settings) -> list:
    """Blur+darken only the flagged boxes and save them as small PNG patches."""
    try:
        from PIL import Image, ImageFilter
        import cv2
        threshold = float(settings.get("threshold", 0.35))
        explicit = set(settings.get("explicit") or DEFAULT_EXPLICIT)
        boxes = []
        for det in detections:
            if det.get("class") in explicit and float(det.get("score", 0)) >= threshold:
                x, y, w, h = [int(v) for v in det["box"]]
                pad = max(12, int(0.12 * max(w, h)))
                boxes.append((max(0, x - pad), max(0, y - pad), w + 2 * pad, h + 2 * pad))
        if not boxes:
            return []
        if image.ndim == 3 and image.shape[2] == 4:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
        else:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        shot = Image.fromarray(rgb)
        out_dir = os.path.join(data_dir(), "curtain")
        os.makedirs(out_dir, exist_ok=True)
        purge_curtain_files(out_dir)
        stamp = str(int(time.time() * 1000))
        patches = []
        for i, (x, y, w, h) in enumerate(boxes):
            x2, y2 = min(shot.width, x + w), min(shot.height, y + h)
            if x2 <= x or y2 <= y:
                continue
            patch = shot.crop((x, y, x2, y2)).filter(ImageFilter.GaussianBlur(10))
            patch = patch.point(lambda p: int(p * 0.7))
            path = os.path.join(out_dir, "%s-%02d.png" % (stamp, i))
            patch.save(path, "PNG")
            patches.append({"x": int(x), "y": int(y), "path": path})
        return patches
    except Exception:
        return []


class Engine:
    """Analysis engine: model, watching, alerts, curtain, hotkey, stats."""

    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.detector = None
        self.error = None
        self.lock = threading.RLock()
        self.frames = 0
        self.hits = 0
        self.last = None
        self.events = []
        self._watch_stop = threading.Event()
        self._watch_thread = None
        self._hotkey_stop = threading.Event()
        self._hotkey_thread = None
        self._hotkey_latch = False
        self._curtain = None

    # ------------------------------------------------------------------ model
    def load_model(self):
        try:
            detector = Detector(threshold=self.settings.get("threshold"),
                                explicit=self.settings.get("explicit"))
            with self.lock:
                self.detector = detector
                self.error = None
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
        return self.detector

    def ready(self) -> bool:
        return self.detector is not None

    # ------------------------------------------------------------- analysis
    def analyse(self, image, source="screen", keep_thumb=True) -> dict:
        if not self.ready():
            return {"error": "model is still loading", "source": source}
        with self.lock:
            self.detector.threshold = float(self.settings.get("threshold"))
            self.detector.explicit = set(self.settings.get("explicit") or DEFAULT_EXPLICIT)
            result = self.detector.analyse(image, multi=bool(self.settings.get("multi")))
        self.frames += 1
        if result["verdict"]:
            self.hits += 1
            log_event(source, result)
            self.push_event(source, result)
            self.alert(result, image, source)
        elif self.settings.get("log_all"):
            log_event(source, result)
        result["source"] = source
        result["time"] = time.strftime("%H:%M:%S")
        if keep_thumb:
            result["thumbnail"] = self.thumbnail(image, result["detections"])
        with self.lock:
            self.last = result
        return result

    def analyse_screen(self, source="screen", monitor=None, keep_thumb=True) -> dict:
        try:
            index = int(self.settings.get("monitor", 1)) if monitor is None else int(monitor)
            image = capture_screen(index)
        except Exception as exc:
            return {"error": "screen capture failed: %s" % exc, "source": source}
        return self.analyse(image, source, keep_thumb=keep_thumb)

    def analyse_file(self, path: str) -> dict:
        try:
            image = load_image(path)
        except Exception as exc:
            return {"error": str(exc), "source": "file"}
        return self.analyse(image, "file:" + os.path.basename(path))

    def analyse_bytes(self, data: bytes, name: str = "upload") -> dict:
        try:
            image = decode_image(data)
        except Exception as exc:
            return {"error": str(exc), "source": "file:" + name}
        return self.analyse(image, "file:" + name)

    def analyse_clipboard(self) -> dict:
        try:
            image = clipboard_image()
        except Exception as exc:
            return {"error": "clipboard: %s" % exc, "source": "clipboard"}
        if image is None:
            return {"error": "no image in clipboard", "source": "clipboard"}
        return self.analyse(image, "clipboard")

    # -------------------------------------------------------------- helpers
    def thumbnail(self, image, detections=None, max_width=900):
        """Small base64 JPEG of the frame with boxes drawn, for the UI preview."""
        try:
            import base64
            import io
            import cv2
            from PIL import Image, ImageDraw
            if image.ndim == 3 and image.shape[2] == 4:
                rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
            else:
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            draw = ImageDraw.Draw(pil)
            threshold = float(self.settings.get("threshold", 0.35))
            explicit = set(self.settings.get("explicit") or DEFAULT_EXPLICIT)
            for det in detections or []:
                x, y, w, h = [int(v) for v in det["box"]]
                bad = det.get("class") in explicit and float(det.get("score", 0)) >= threshold
                color = (255, 69, 58) if bad else (48, 209, 88)
                draw.rectangle([x, y, x + w, y + h], outline=color, width=3)
                draw.text((x + 4, max(0, y - 13)),
                          "%s %.2f" % (det.get("class"), float(det.get("score", 0))), fill=color)
            if pil.width > max_width:
                ratio = max_width / float(pil.width)
                pil = pil.resize((max_width, max(1, int(pil.height * ratio))), Image.LANCZOS)
            buffer = io.BytesIO()
            pil.save(buffer, "JPEG", quality=82)
            return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return None

    def push_event(self, source, result):
        top = result.get("top") or {}
        with self.lock:
            self.events.insert(0, {
                "time": time.strftime("%H:%M:%S"),
                "source": source,
                "class": top.get("class"),
                "score": round(float(top.get("score", 0.0)), 3),
                "verdict": bool(result.get("verdict")),
            })
            del self.events[40:]

    def alert(self, result, image, source):
        if self.settings.get("sound"):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                pass
        if self.settings.get("save"):
            path = save_flagged(image, source.split(":")[0])
            if path:
                log_event(source, result, "saved=" + os.path.basename(path))
        if self.settings.get("curtain"):
            self.show_curtain(image, result.get("flagged") or [])

    def show_curtain(self, image, detections) -> bool:
        """Spawn the native click-through curtain process (blurred patches only)."""
        patches = make_curtain_patches(image, detections, self.settings)
        if not patches:
            return False
        try:
            import subprocess
            try:
                height, width = image.shape[:2]
                size = "%dx%d" % (width, height)
            except Exception:
                size = ""
            args = [sys.executable, "-m", "nsfw_guard.curtain",
                    "--seconds", str(self.settings.get("curtain_seconds", 4))]
            if size:
                args += ["--src", size]
            for patch in patches:
                args += ["--patch", "%d,%d,%s" % (patch["x"], patch["y"], patch["path"])]
            env = dict(os.environ)
            package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
            if self._curtain is not None and self._curtain.poll() is None:
                try:
                    self._curtain.terminate()
                except Exception:
                    pass
            self._curtain = subprocess.Popen(
                args, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------- watching
    def start_watch(self) -> bool:
        if self._watch_thread and self._watch_thread.is_alive():
            return True
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(target=self._watch_loop, name="watch", daemon=True)
        self._watch_thread.start()
        return True

    def stop_watch(self) -> bool:
        self._watch_stop.set()
        return True

    def watching(self) -> bool:
        return bool(self._watch_thread and self._watch_thread.is_alive())

    def _watch_loop(self):
        while not self._watch_stop.is_set():
            self.analyse_screen("screen-watch")
            interval = float(self.settings.get("interval", 3.0))
            waited = 0.0
            while waited < interval and not self._watch_stop.is_set():
                time.sleep(0.1)
                waited += 0.1
                self._check_hotkey()

    # --------------------------------------------------------------- hotkey
    def start_hotkey(self) -> bool:
        if self._hotkey_thread and self._hotkey_thread.is_alive():
            return True
        self._hotkey_stop.clear()
        self._hotkey_thread = threading.Thread(target=self._hotkey_loop, name="hotkey", daemon=True)
        self._hotkey_thread.start()
        return True

    def _hotkey_loop(self):
        while not self._hotkey_stop.is_set():
            self._check_hotkey()
            time.sleep(0.1)

    def _check_hotkey(self):
        down = hotkey_pressed()
        if down and not self._hotkey_latch:
            self._hotkey_latch = True
            self.analyse_screen("hotkey")
        elif not down:
            self._hotkey_latch = False

    # ---------------------------------------------------------------- stats
    def stats(self) -> dict:
        last = self.last or {}
        top = last.get("top") or {}
        payload = {
            "ready": self.ready(),
            "error": self.error,
            "watching": self.watching(),
            "frames": self.frames,
            "hits": self.hits,
            "settings": self.settings.to_dict(),
            "events": list(self.events[:25]),
            "log": read_log(40),
            "hotkey": HOTKEY_TEXT,
            "data_dir": data_dir(),
            "version": VERSION,
            "classes": ALL_CLASSES,
            "class_labels": CLASS_LABEL,
            "default_explicit": list(DEFAULT_EXPLICIT),
            "last": None,
        }
        if last:
            payload["last"] = {
                "time": last.get("time"),
                "source": last.get("source"),
                "verdict": bool(last.get("verdict")),
                "class": top.get("class"),
                "score": round(float(top.get("score", 0.0)), 3),
                "ms": last.get("ms"),
                "passes": last.get("passes"),
                "detections": last.get("detections", []),
                "flagged": last.get("flagged", []),
                "thumbnail": last.get("thumbnail"),
            }
        return payload

    def shutdown(self):
        self.stop_watch()
        self._hotkey_stop.set()
        if self._curtain is not None and self._curtain.poll() is None:
            try:
                self._curtain.terminate()
            except Exception:
                pass
