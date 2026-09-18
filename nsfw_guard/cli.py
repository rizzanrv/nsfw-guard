"""Terminal interface: analyse files, screenshots or watch the screen."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .core import Engine, Settings, list_monitors


def _print_result(source, result):
    if result.get("error"):
        print("[%s] %s: ERROR %s" % (time.strftime("%H:%M:%S"), source, result["error"]))
        return
    top = result.get("top") or {}
    print("[%s] %s: %s (score %.3f, %d ms, %s passes)" % (
        time.strftime("%H:%M:%S"), source,
        "NSFW" if result["verdict"] else "clean",
        float(top.get("score", 0.0)), int(result.get("ms", 0)), result.get("passes", 1)))
    flagged_ids = set(id(d) for d in result.get("flagged") or [])
    for det in (result.get("flagged") or [])[:5]:
        print("    FLAGGED  %-26s %.3f box=%s" % (det["class"], det["score"], det["box"]))
    for det in (result.get("detections") or [])[:8]:
        if id(det) not in flagged_ids:
            print("    other    %-26s %.3f" % (det["class"], det["score"]))


def build_parser():
    p = argparse.ArgumentParser(prog="nsfw-guard --cli",
                                description="Offline nudity detector (terminal mode)")
    p.add_argument("--file", help="analyse an image file")
    p.add_argument("--screen", action="store_true", help="grab one screenshot and analyse it")
    p.add_argument("--clipboard", action="store_true", help="analyse the image in the clipboard")
    p.add_argument("--watch", action="store_true", help="watch the screen until Ctrl+C")
    p.add_argument("--list-monitors", action="store_true", help="list monitors and exit")
    p.add_argument("--monitor", type=int, default=1, help="0 = all screens, 1..N = one screen")
    p.add_argument("--interval", type=float, default=3.0, help="watch interval, seconds")
    p.add_argument("--threshold", type=float, default=0.35, help="detection threshold")
    p.add_argument("--classes", help="comma separated class list")
    p.add_argument("--json", action="store_true", help="machine readable output")
    p.add_argument("--no-multi", dest="multi", action="store_false", default=True,
                   help="disable multi-exposure inference")
    p.add_argument("--save-flagged", action="store_true", help="store frames that trigger")
    p.add_argument("--verbose", action="store_true", help="print every frame in --watch mode")
    return p


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)

    if args.list_monitors:
        for mon in list_monitors():
            name = "all screens" if mon["index"] == 0 else "screen %d" % mon["index"]
            print("%d: %-11s %dx%d  (%d,%d)" % (
                mon["index"], name, mon["width"], mon["height"], mon["left"], mon["top"]))
        return 0

    explicit = [c.strip().upper() for c in args.classes.split(",")] if args.classes else None
    settings = Settings(threshold=args.threshold, monitor=args.monitor, interval=args.interval,
                        multi=args.multi, save=args.save_flagged, explicit=explicit)
    engine = Engine(settings)
    engine.load_model()
    if not engine.ready():
        print("model failed to load: %s" % engine.error)
        return 1

    def emit(source, result):
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_result(source, result)

    if args.file:
        result = engine.analyse_file(args.file)
        emit("file:" + os.path.basename(args.file), result)
        return 0 if not result.get("error") else 1

    if args.clipboard:
        result = engine.analyse_clipboard()
        emit("clipboard", result)
        return 0 if not result.get("error") else 1

    if args.screen:
        result = engine.analyse_screen("screen")
        emit("screen:%d" % args.monitor, result)
        return 0 if not result.get("error") else 1

    if args.watch:
        engine.settings.update({"curtain": False, "sound": False})
        print("watching: every %ss, monitor %s, threshold %.2f, multi-exposure %s (Ctrl+C to stop)" % (
            settings.get("interval"), settings.get("monitor"), settings.get("threshold"),
            "on" if settings.get("multi") else "off"))
        try:
            while True:
                result = engine.analyse_screen("screen-watch", keep_thumb=False)
                if result.get("error"):
                    print("error: %s" % result["error"])
                    time.sleep(2)
                    continue
                if result["verdict"]:
                    print()
                    _print_result("screen-watch", result)
                elif args.verbose:
                    print("[%s] frame %d: clean (%.3f)" % (
                        time.strftime("%H:%M:%S"), engine.frames, result.get("score", 0.0)))
                time.sleep(max(0.5, float(settings.get("interval"))))
        except KeyboardInterrupt:
            print("\nstopped. frames: %d, hits: %d" % (engine.frames, engine.hits))
        return 0

    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
