"""Headless smoke tests: no GUI, no network, real inference on a synthetic frame.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from nsfw_guard import core  # noqa: E402


def synthetic_frame(width=640, height=480, brightness=1.0):
    """Skin-tone blob on noise: a hard negative by construction."""
    import cv2
    rng = np.random.default_rng(7)
    image = rng.integers(40, 90, (height, width, 3), dtype=np.uint8)
    cv2.ellipse(image, (width // 2, height // 2), (110, 150), 0, 0, 360, (140, 165, 205), -1)
    if brightness != 1.0:
        image = np.clip(image.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
    return image


class SettingsTest(unittest.TestCase):
    def test_clamping(self):
        settings = core.Settings(threshold=5.0, interval=0.0)
        self.assertLessEqual(settings.get("threshold"), 0.95)
        self.assertGreaterEqual(settings.get("interval"), 1.0)

    def test_explicit_filtering(self):
        settings = core.Settings(explicit=["NOPE", "BUTTOCKS_EXPOSED"])
        self.assertEqual(settings.get("explicit"), ["BUTTOCKS_EXPOSED"])

    def test_explicit_never_empty(self):
        settings = core.Settings(explicit=["NOPE"])
        self.assertEqual(settings.get("explicit"), core.DEFAULT_EXPLICIT)

    def test_unknown_keys_ignored(self):
        settings = core.Settings(totally_unknown=1)
        self.assertIsNone(settings.get("totally_unknown"))


class HelpersTest(unittest.TestCase):
    def test_decode_rejects_garbage(self):
        with self.assertRaises(ValueError):
            core.decode_image(b"not an image")

    def test_iou(self):
        self.assertAlmostEqual(core._iou([0, 0, 10, 10], [0, 0, 10, 10]), 1.0)
        self.assertEqual(core._iou([0, 0, 10, 10], [50, 50, 10, 10]), 0.0)

    def test_data_dir_is_writable(self):
        path = core.data_dir()
        self.assertTrue(os.path.isdir(path))
        probe = os.path.join(path, "write-probe.tmp")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)

    def test_monitors_are_reported(self):
        monitors = core.list_monitors()
        self.assertGreaterEqual(len(monitors), 1)
        self.assertIn("width", monitors[0])

    def test_curtain_patches_for_clean_frame(self):
        self.assertEqual(core.make_curtain_patches(synthetic_frame(), [], core.Settings()), [])


class ModelTest(unittest.TestCase):
    """Real inference on a synthetic frame (CPU)."""

    @classmethod
    def setUpClass(cls):
        cls.detector = core.Detector(threshold=0.35)

    def test_multi_exposure_runs_three_passes(self):
        result = self.detector.analyse(synthetic_frame(), multi=True)
        self.assertEqual(result["passes"], 3)
        self.assertIn("verdict", result)
        self.assertGreaterEqual(result["ms"], 0)

    def test_single_pass(self):
        self.assertEqual(self.detector.analyse(synthetic_frame(), multi=False)["passes"], 1)

    def test_threshold_filters_flags(self):
        self.detector.threshold = 0.999
        try:
            self.assertFalse(self.detector.analyse(synthetic_frame(), multi=True)["verdict"])
        finally:
            self.detector.threshold = 0.35

    def test_dark_frame_is_handled(self):
        result = self.detector.analyse(synthetic_frame(brightness=0.2), multi=True)
        self.assertIsNone(result.get("error"))
        self.assertEqual(result["passes"], 3)

    def test_brighten_lut(self):
        import numpy as np
        lifted = core.Detector.brighten(np.array([[[10, 20, 30]]], dtype=np.uint8), 1.8)
        self.assertGreater(int(lifted[0, 0, 0]), 10)


class EngineTest(unittest.TestCase):
    def test_stats_shape(self):
        engine = core.Engine(core.Settings())
        stats = engine.stats()
        for key in ("ready", "watching", "frames", "hits", "settings", "events", "classes", "hotkey"):
            self.assertIn(key, stats)
        self.assertEqual(len(stats["classes"]), 18)
        self.assertFalse(stats["ready"])
        engine.shutdown()

    def test_watch_lifecycle(self):
        engine = core.Engine(core.Settings(interval=1.0))
        engine.load_model()
        self.assertTrue(engine.ready())
        engine.start_watch()
        self.assertTrue(engine.watching())
        engine.stop_watch()
        engine.shutdown()


class PersistenceTest(unittest.TestCase):
    """Settings and hooks that make the background mode work."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nsfw-guard-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.previous = os.environ.get("NSFW_GUARD_HOME")
        os.environ["NSFW_GUARD_HOME"] = self.tmp
        self.addCleanup(self._restore)

    def _restore(self):
        if self.previous is None:
            os.environ.pop("NSFW_GUARD_HOME", None)
        else:
            os.environ["NSFW_GUARD_HOME"] = self.previous

    def test_settings_round_trip(self):
        saved = core.Settings(threshold=0.5, tray_close=False, notify=False, interval=5.0)
        self.assertTrue(saved.save())
        loaded = core.Settings.load()
        self.assertEqual(loaded.get("threshold"), 0.5)
        self.assertEqual(loaded.get("interval"), 5.0)
        self.assertFalse(loaded.get("tray_close"))
        self.assertFalse(loaded.get("notify"))

    def test_broken_settings_file_falls_back_to_defaults(self):
        with open(core.settings_path(), "w", encoding="utf-8") as handle:
            handle.write("{ not json at all")
        self.assertEqual(core.Settings.load().get("threshold"), core.Settings.DEFAULTS["threshold"])

    def test_alert_hooks_fire_on_every_hit(self):
        engine = core.Engine(core.Settings(curtain=False, sound=False))
        seen = []
        engine.alert_hooks.append(lambda result, source: seen.append(source))
        engine.alert({"verdict": True, "top": {"class": "BUTTOCKS_EXPOSED", "score": 0.9}, "flagged": []},
                     None, "unit-test")
        self.assertEqual(seen, ["unit-test"])
        engine.shutdown()


class IconsTest(unittest.TestCase):
    def test_assets_are_packaged(self):
        from nsfw_guard import assets
        for name in ("app.ico", "icon-256.png", "tray-idle.png", "tray-watch.png",
                     "tray-alert.png", "tray-muted.png"):
            self.assertTrue(os.path.isfile(assets.asset_path(name)), name)

    def test_tray_images_load(self):
        from nsfw_guard import tray
        for state in ("idle", "watch", "alert", "muted"):
            image = tray.load_image(state)
            self.assertEqual(image.size, (32, 32))
            self.assertEqual(image.mode, "RGBA")


class AutostartTest(unittest.TestCase):
    def test_launch_command_starts_hidden_in_the_tray(self):
        from nsfw_guard import autostart
        command = autostart.launch_command()
        self.assertTrue(command.startswith('"'))
        self.assertIn("--tray", command)
        self.assertIn("nsfw_guard", command)

    def test_registry_is_left_alone_off_windows(self):
        from nsfw_guard import autostart
        if not autostart.supported():
            self.assertFalse(autostart.is_enabled())
            self.assertFalse(autostart.set_enabled(True))


class TrayTest(unittest.TestCase):
    """The tray is driven by a tiny shell interface: check it without a desktop."""

    class FakeEngine:
        def __init__(self, settings):
            self.settings = settings
            self.watching_now = False

        def ready(self):
            return True

        def watching(self):
            return self.watching_now

        def stats(self):
            return {"ready": True, "watching": self.watching_now, "frames": 4, "hits": 2, "error": None}

    class FakeShell:
        def __init__(self):
            self.calls = []
            self.visible = True
            self.autostart_enabled = False
            self.autostart_supported = True

        def window_visible(self):
            return self.visible

        def toggle_window(self):
            self.visible = not self.visible
            self.calls.append("toggle_window")

        def check_now(self):
            self.calls.append("check_now")

        def toggle_watch(self):
            self.calls.append("toggle_watch")

        def toggle(self, key, value=None):
            self.calls.append("toggle:" + key)

        def open_data_dir(self):
            self.calls.append("open_data_dir")

        def open_settings(self):
            self.calls.append("open_settings")

        def quit(self):
            self.calls.append("quit")

        def autostart(self):
            return self.autostart_enabled

    def make(self):
        from nsfw_guard import tray
        settings = core.Settings()
        engine = self.FakeEngine(settings)
        shell = self.FakeShell()
        return tray.TrayIcon(engine, shell), engine, shell

    def test_state_follows_watching_and_alerts(self):
        icon, engine, _shell = self.make()
        self.assertEqual(icon.state(), "idle")
        engine.watching_now = True
        self.assertEqual(icon.state(), "watch")
        icon.announce({"top": {"class": "FEMALE_BREAST_EXPOSED", "score": 0.8}}, "unit")
        self.assertEqual(icon.state(), "alert")

    def test_notifications_can_be_switched_off(self):
        icon, _engine, _shell = self.make()
        icon._engine.settings.update({"notify": False})
        self.assertEqual(icon.state(), "muted")
        self.assertFalse(icon.notify("title", "text"))

    def test_menu_is_built_and_actions_reach_the_shell(self):
        icon, _engine, shell = self.make()
        menu = icon._menu()
        self.assertIsNotNone(menu)
        labels = [item.text for item in menu.items]
        self.assertEqual(len(menu.items), 12)        # 9 actions + 3 separators
        for expected in ("Проверить экран сейчас", "Слежение за экраном", "Настройки", "Выход"):
            self.assertIn(expected, labels)
        icon._call("toggle_window")()
        icon._call("check_now")()
        icon._call("open_settings")()
        self.assertEqual(shell.calls, ["toggle_window", "check_now", "open_settings"])

    def test_unknown_action_does_not_raise(self):
        icon, _engine, _shell = self.make()
        icon._call("no_such_action")()               # must stay quiet
        self.assertIn("nsfw guard", icon._tooltip())

    def test_headless_use_is_safe(self):
        icon, _engine, _shell = self.make()
        self.assertFalse(icon.running)
        self.assertFalse(icon.notify("t", "b"))      # no icon yet -> no crash
        icon.refresh()
        icon.stop()


class ShellTest(unittest.TestCase):
    """server.Shell keeps window and tray in sync - tested without a window."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nsfw-guard-shell-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.previous = os.environ.get("NSFW_GUARD_HOME")
        os.environ["NSFW_GUARD_HOME"] = self.tmp
        self.addCleanup(self._restore)

    def _restore(self):
        if self.previous is None:
            os.environ.pop("NSFW_GUARD_HOME", None)
        else:
            os.environ["NSFW_GUARD_HOME"] = self.previous

    def test_window_calls_without_a_window_are_safe(self):
        from nsfw_guard.server import Shell
        settings = core.Settings()
        engine = core.Engine(settings)
        self.addCleanup(engine.shutdown)
        shell = Shell(engine, settings)
        self.assertFalse(shell.window_visible())
        self.assertFalse(shell.tray_running())
        self.assertTrue(shell.on_closing())          # no tray -> the close is real
        shell.hide_window()
        shell.show_window()
        shell.toggle_window()
        shell.open_settings()

    def test_toggle_flips_settings_and_persists(self):
        from nsfw_guard.server import Shell
        settings = core.Settings()
        engine = core.Engine(settings)
        self.addCleanup(engine.shutdown)
        shell = Shell(engine, settings)
        self.assertFalse(shell.toggle("notify"))
        self.assertFalse(settings.get("notify"))
        self.assertFalse(core.Settings.load().get("notify"))    # really written
        self.assertTrue(shell.toggle("notify"))

    def test_close_is_cancelled_while_the_tray_runs(self):
        from nsfw_guard.server import Shell
        settings = core.Settings()
        engine = core.Engine(settings)
        self.addCleanup(engine.shutdown)
        shell = Shell(engine, settings)

        class RunningTray:
            running = True

            def notify(self, *args):
                return True

        shell.attach(None, RunningTray())
        self.assertFalse(shell.on_closing())        # False cancels the close
        settings.update({"tray_close": False})
        self.assertTrue(shell.on_closing())


if __name__ == "__main__":
    unittest.main(verbosity=2)
