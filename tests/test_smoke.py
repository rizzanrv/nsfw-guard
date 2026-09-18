"""Headless smoke tests: no GUI, no network, real inference on a synthetic frame.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
