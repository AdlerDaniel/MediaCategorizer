import unittest
from pathlib import Path
from media_categorizer.naming import filter_duplicate_tags, render_rename
from media_categorizer.view_geometry import PanZoom


class NamingTests(unittest.TestCase):
    def test_existing_prefixes_are_not_repeated(self):
        categories = [{"name": name} for name in ("GOOD", "POST")]
        tags, skipped = filter_duplicate_tags(Path("GOOD_IMG.jpg"), ["good", "POST", "post"], categories)
        self.assertEqual(tags, ["POST"])
        self.assertEqual(skipped, ["good"])
        self.assertEqual(render_rename(Path("GOOD_IMG.jpg"), tags, 0), "POST_GOOD_IMG.jpg")

    def test_prefix_is_not_found_in_middle_of_filename(self):
        tags, _ = filter_duplicate_tags(Path("IMG_GOOD.jpg"), ["GOOD"], [{"name": "GOOD"}])
        self.assertEqual(tags, ["GOOD"])

    def test_template_keeps_extension_and_cleans_forbidden_characters(self):
        self.assertEqual(render_rename(Path("image.jpg"), ["GOOD"], 41, "{tags}:{index}"), "GOOD_0042.jpg")


class GeometryTests(unittest.TestCase):
    def test_fit_fills_limiting_axis_without_distortion(self):
        view = PanZoom()
        self.assertEqual(view.rectangle(1000, 800, 100, 50), (0, 150, 1000, 500))

    def test_zoom_keeps_image_point_under_cursor(self):
        view = PanZoom(percent=200)
        area, source, anchor = (1000, 800), (1600, 900), (720, 420)
        x, y, w, h = view.rectangle(*area, *source)
        original_point = ((anchor[0] - x) / w, (anchor[1] - y) / h)
        view.zoom(250, area, source, anchor)
        x, y, w, h = view.rectangle(*area, *source)
        self.assertAlmostEqual((anchor[0] - x) / w, original_point[0])
        self.assertAlmostEqual((anchor[1] - y) / h, original_point[1])

    def test_pan_stops_at_edges_and_fit_resets_it(self):
        view = PanZoom(percent=200)
        area, source = (800, 600), (800, 600)
        view.pan(10000, -10000, area, source)
        self.assertEqual(view.rectangle(*area, *source), (0, -600, 1600, 1200))
        view.zoom(100, area, source)
        self.assertEqual(view.rectangle(*area, *source), (0, 0, 800, 600))

    def test_resize_clamps_old_pan(self):
        view = PanZoom(percent=200, offset_x=300, offset_y=300)
        x, y, w, h = view.rectangle(200, 200, 800, 600)
        self.assertGreaterEqual(x + w, 200)
        self.assertGreaterEqual(y + h, 200)
        self.assertLessEqual(x, 0)
        self.assertLessEqual(y, 0)


if __name__ == "__main__":
    unittest.main()
