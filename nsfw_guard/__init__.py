"""nsfw-guard: fully offline nudity detection for screens and images.

This package performs all analysis locally: the ONNX model ships inside the
`nudenet` wheel, inference runs on CPU, and no code path performs network I/O.
"""

from .core import (  # noqa: F401
    ALL_CLASSES,
    APP_NAME,
    CLASS_LABEL,
    DEFAULT_EXPLICIT,
    HOTKEY_TEXT,
    VERSION,
    Detector,
    Engine,
    Settings,
    capture_screen,
    clipboard_image,
    data_dir,
    decode_image,
    list_monitors,
    load_image,
    log_event,
    read_log,
)

__all__ = [
    "ALL_CLASSES",
    "APP_NAME",
    "CLASS_LABEL",
    "DEFAULT_EXPLICIT",
    "HOTKEY_TEXT",
    "VERSION",
    "Detector",
    "Engine",
    "Settings",
    "capture_screen",
    "clipboard_image",
    "data_dir",
    "decode_image",
    "list_monitors",
    "load_image",
    "log_event",
    "read_log",
]
