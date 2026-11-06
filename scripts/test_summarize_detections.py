"""summarize_detections.summarize 的離線測試,不需要 ultralytics:

    python3 -I test_summarize_detections.py
"""
import unittest

from summarize_detections import position, summarize


class SummarizeTest(unittest.TestCase):
    def test_position_thirds(self):
        self.assertEqual(position(10, 300), "left")
        self.assertEqual(position(150, 300), "center")
        self.assertEqual(position(290, 300), "right")

    def test_counts_and_positions(self):
        dets = [("person", 0, 0, 40, 40), ("person", 130, 0, 170, 40), ("pole", 260, 0, 300, 40)]
        out = summarize(dets, 300)
        self.assertEqual(out["person"], {"count": 2, "left": 1, "center": 1, "right": 0})
        self.assertEqual(out["pole"]["right"], 1)

    def test_small_boxes_dropped(self):
        out = summarize([("person", 0, 0, 40, 8)], 300, min_side=12)
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
