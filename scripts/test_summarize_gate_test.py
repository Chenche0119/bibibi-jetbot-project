"""summarize_gate_test 的離線測試（在 scripts 目錄執行）:

    python3 test_summarize_gate_test.py
"""
import unittest

from summarize_gate_test import (compare_to_baseline, comparison_markdown, parse_args, parse_log,
                                 summarize, to_markdown)

LOG = (
    "progress 10%\rprogress 100%\n"
    "mAP50 0.874 mAP50-95 0.516 P 0.83 R 0.818\n"
    "person AP50 0.9 實例數 137\n"
    "gate_test conf=0.001: mAP50 0.805 mAP50-95 0.366 P 0.847 R 0.743\n"
    "   person AP50 0.785 P 0.924 R 0.679 實例數 36\n"
    "   dynamic_obstacle AP50 0.825 P 0.769 R 0.808 實例數 144\n"
    "Ultralytics 8.4.183\n"
    "   person AP50 0.111 P 0.1 R 0.1 實例數 1\n"
    "gate_test conf=0.25: mAP50 0.657 mAP50-95 0.32 P 0.902 R 0.673\n"
)


class ParseTest(unittest.TestCase):
    def test_parse(self):
        m = parse_log(LOG)
        self.assertEqual(m["test_mAP50"], 0.874)
        self.assertEqual(m["gate0.001_mAP50"], 0.805)
        self.assertEqual(m["gate0.001_person_AP50"], 0.785)
        self.assertEqual(m["gate0.25_R"], 0.673)
        self.assertNotIn("gate0.25_person_AP50", m)

    def test_group_and_markdown(self):
        runs = {"run27": {"gate0.001_mAP50": 0.8}, "run27s1": {"gate0.001_mAP50": 0.7},
                "run26": {"gate0.001_mAP50": 0.4}}
        s = summarize(runs, ["gate0.001_mAP50"])
        self.assertAlmostEqual(s["run27"]["gate0.001_mAP50"][0], 0.75)
        self.assertEqual(s["run27"]["gate0.001_mAP50"][2], 2)
        md = to_markdown(s, ["gate0.001_mAP50"])
        self.assertIn("| run26 | 1 | 0.400 |", md)
        self.assertIn("| run27 | 2 | 0.750 ± 0.071 |", md)


class BaselineTest(unittest.TestCase):
    def setUp(self):
        runs = {"run26": {"a": 0.40, "b": 0.88}, "run26s1": {"a": 0.50, "b": 0.89},
                "run27": {"a": 0.80, "b": 0.885}, "run27s1": {"a": 0.79, "b": 0.88},
                "run28": {"a": 0.42}}
        self.s = summarize(runs, ["a", "b"])

    def test_verdicts(self):
        c = compare_to_baseline(self.s, "run26", ["a", "b"])
        self.assertNotIn("run26", c)
        self.assertAlmostEqual(c["run27"]["a"][0], 0.345)
        self.assertEqual(c["run27"]["a"][1], "above noise")
        self.assertEqual(c["run27"]["b"][1], "within noise")
        self.assertEqual(c["run28"]["a"][1], "within noise")
        self.assertNotIn("b", c["run28"])

    def test_no_baseline_std_and_missing(self):
        s = summarize({"run26": {"a": 0.4}, "run27": {"a": 0.8}}, ["a"])
        self.assertEqual(compare_to_baseline(s, "run26", ["a"])["run27"]["a"][1], "no baseline std")
        with self.assertRaises(KeyError):
            compare_to_baseline(s, "run99", ["a"])

    def test_markdown_and_args(self):
        c = compare_to_baseline(self.s, "run26", ["a"])
        self.assertIn("| run27 | +0.345 (above noise) |", comparison_markdown(c, "run26", ["a"]))
        self.assertEqual(parse_args(["logs", "--baseline", "run26", "--md"]), (True, "run26", ["logs"]))


if __name__ == "__main__":
    unittest.main()
