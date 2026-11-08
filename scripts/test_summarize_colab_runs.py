"""summarize_colab_runs.delta_vs_baseline 的離線測試:

    python3 -I test_summarize_colab_runs.py
"""
import unittest

from summarize_colab_runs import delta_vs_baseline


class DeltaTest(unittest.TestCase):
    def test_noise_verdicts(self):
        groups = {"run04_baseline": [0.70, 0.74, 0.74], "a": [0.75], "b": [0.90]}
        out = delta_vs_baseline(groups)
        self.assertEqual(out["run04_baseline"][1], "baseline")
        self.assertEqual(out["a"][1], "within noise")
        self.assertEqual(out["b"][1], "above noise")

    def test_single_seed_baseline(self):
        out = delta_vs_baseline({"x_baseline": [0.7], "y": [0.8]})
        self.assertEqual(out["y"][1], "no baseline std")

    def test_no_baseline(self):
        self.assertEqual(delta_vs_baseline({"y": [0.8]}), {})


if __name__ == "__main__":
    unittest.main()
