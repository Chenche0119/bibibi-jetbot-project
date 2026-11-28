"""summarize_colab_runs.delta_vs_baseline 的離線測試:

    python3 -I test_summarize_colab_runs.py
"""
import unittest

from summarize_colab_runs import delta_vs_baseline, overfit_markdown, overfit_metrics, to_markdown


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

    def test_markdown_table(self):
        g = {"run04_baseline": [0.70, 0.74], "x": [0.8]}
        md = to_markdown(g, delta_vs_baseline(g))
        self.assertEqual(len(md.splitlines()), 4)
        self.assertIn("| x | 1 | 0.8000 | - |", md)



def _row(epoch, m50, train, val):
    r = {"epoch": str(epoch), "metrics/mAP50(B)": str(m50)}
    for k in ("box_loss", "cls_loss", "dfl_loss"):
        r[f"train/{k}"] = str(train / 3)
        r[f"val/{k}"] = str(val / 3)
    return r


class OverfitTest(unittest.TestCase):
    def test_indicators(self):
        res = [_row(1, 0.5, 6.0, 4.5), _row(2, 0.8, 4.5, 3.0), _row(3, 0.7, 3.0, 3.6)]
        best, gap, rise, drop = overfit_metrics(res)
        self.assertEqual(best["epoch"], "2")
        self.assertAlmostEqual(gap, -1.5)
        self.assertAlmostEqual(rise, 0.6)
        self.assertAlmostEqual(drop, 0.1)

    def test_overfit_markdown(self):
        rows = [{"group": "g", "loss_gap": "0.2", "val_rise": "0.0", "mAP50_drop": "0.01"},
                {"group": "g", "loss_gap": "0.4", "val_rise": "0.2", "mAP50_drop": "0.03"}]
        md = overfit_markdown(rows)
        self.assertIn("| g | 2 | +0.300 | +0.100 | +0.0200 |", md)


if __name__ == "__main__":
    unittest.main()
