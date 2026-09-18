"""Build the Windows executables and name the artifacts for a release.

    python tools/build_exe.py                # folder build (fast start) -> zip
    python tools/build_exe.py --onefile      # single executable -> .exe
    python tools/build_exe.py --both         # both artifacts (what CI does)

Artifacts land in dist/: nsfw-guard-<version>-win64.zip and
nsfw-guard-<version>-portable.exe. Needs PyInstaller (pip install pyinstaller).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "packaging", "nsfw-guard.spec")
DIST = os.path.join(ROOT, "dist")


def version() -> str:
    sys.path.insert(0, ROOT)
    from nsfw_guard.core import VERSION
    return VERSION


def run_pyinstaller(onefile: bool):
    env = dict(os.environ)
    env["NSFW_GUARD_ONEFILE"] = "1" if onefile else "0"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC]
    print("$", " ".join(command), "NSFW_GUARD_ONEFILE=%s" % env["NSFW_GUARD_ONEFILE"], flush=True)
    subprocess.check_call(command, cwd=ROOT, env=env)


def make_zip(folder: str, target: str) -> str:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for base, _dirs, files in os.walk(folder):
            for name in files:
                path = os.path.join(base, name)
                archive.write(path, os.path.relpath(path, os.path.dirname(folder)))
    return target


def size_mb(path: str) -> float:
    if os.path.isdir(path):
        total = sum(os.path.getsize(os.path.join(base, name))
                    for base, _d, files in os.walk(path) for name in files)
    else:
        total = os.path.getsize(path)
    return total / 1048576.0


def build_folder(tag: str) -> str:
    run_pyinstaller(onefile=False)
    folder = os.path.join(DIST, "nsfw-guard")
    if not os.path.isdir(folder):
        raise SystemExit("PyInstaller did not produce " + folder)
    target = os.path.join(DIST, "nsfw-guard-%s-win64.zip" % tag)
    make_zip(folder, target)
    shutil.rmtree(folder, ignore_errors=True)
    print("folder build  %-42s %7.1f MB (packed)" % (target, size_mb(target)), flush=True)
    return target


def build_onefile(tag: str) -> str:
    run_pyinstaller(onefile=True)
    source = os.path.join(DIST, "nsfw-guard.exe")
    if not os.path.isfile(source):
        raise SystemExit("PyInstaller did not produce " + source)
    target = os.path.join(DIST, "nsfw-guard-%s-portable.exe" % tag)
    shutil.move(source, target)
    print("onefile build %-42s %7.1f MB" % (target, size_mb(target)), flush=True)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="build the nsfw-guard executables")
    parser.add_argument("--onefile", action="store_true", help="single .exe only")
    parser.add_argument("--folder", action="store_true", help="folder build only")
    parser.add_argument("--both", action="store_true", help="both artifacts")
    args = parser.parse_args(argv)

    tag = version()
    print("nsfw guard %s -> %s" % (tag, DIST), flush=True)
    os.makedirs(DIST, exist_ok=True)

    if args.onefile or args.both:
        build_onefile(tag)
    if args.folder or args.both or not (args.onefile or args.folder):
        build_folder(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
