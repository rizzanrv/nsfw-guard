# PyInstaller spec for nsfw guard.
#
#   pyinstaller packaging/nsfw-guard.spec                     -> dist/nsfw-guard/ (folder)
#   set NSFW_GUARD_ONEFILE=1 && pyinstaller packaging/nsfw-guard.spec  -> dist/nsfw-guard.exe
#
# The folder build starts instantly and can open the curtain overlay without
# unpacking; the one-file build is a single executable that unpacks to %TEMP% on
# every start. Both ship the same model (nudenet's 320n.onnx) and the same UI.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ONEFILE = os.environ.get("NSFW_GUARD_ONEFILE", "0") == "1"
ICON = os.path.join(ROOT, "nsfw_guard", "assets", "app.ico")

datas = []
datas += collect_data_files("nudenet")                                   # 320n.onnx
datas += [(os.path.join(ROOT, "nsfw_guard", "webui"), "nsfw_guard/webui")]
datas += [(os.path.join(ROOT, "nsfw_guard", "assets"), "nsfw_guard/assets")]

binaries = collect_dynamic_libs("onnxruntime")

hiddenimports = [
    "webview.platforms.winforms",        # WebView2/WinForms backend
    "webview.platforms.edgechromium",
    "pystray._win32",                    # notification area
    "clr", "clr_loader", "pythonnet",    # pywebview's .NET bridge
    "tkinter",                           # curtain overlay
    "winreg", "winsound",
    "PIL.Image", "PIL.ImageDraw", "PIL.ImageFilter",
]
hiddenimports += collect_submodules("pystray")

excludes = [
    "matplotlib", "scipy", "pandas", "IPython", "pytest", "torch", "torchvision",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "ultralytics",
]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="nsfw-guard",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
        version=os.path.join(SPECPATH, "version_info.txt"),
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="nsfw-guard",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
        version=os.path.join(SPECPATH, "version_info.txt"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="nsfw-guard",
    )
