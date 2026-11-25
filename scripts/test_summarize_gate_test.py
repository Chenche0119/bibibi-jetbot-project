"""summarize_gate_test 的離線測試（在 scripts 目錄執行）:

    python3 test_summarize_gate_test.py
"""
import unittest

from summarize_gate_test import parse_log, summarize, to_markdown

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


if __name__ == "__main__":
    unittest.main()
