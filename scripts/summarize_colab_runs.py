"""Summarize real Colab runs under colab_runs/local/results.

Usage: python3 -I summarize_colab_runs.py [results_dir] [out.csv]

Each run dir holds runs/<run_id>/results.csv (Ultralytics). Takes the best epoch (max mAP50)
per run, groups runs by name with the seed suffix (sN) removed, and prints mean/std of mAP50
across seeds plus the delta against the baseline group (flagged 'above noise' only when
|delta| > 2 x baseline std). Writes per-run rows to out.csv (default colab_runs_summary.csv).
"""
import csv, glob, os, re, statistics as st, sys
from collections import defaultdict


def delta_vs_baseline(groups, baseline_key="baseline"):
    """Return {group: (delta, verdict)} against the first group whose name has baseline_key."""
    base = next((g for g in groups if baseline_key in g), None)
    if base is None:
        return {}
    bv = groups[base]
    b_mean = st.mean(bv)
    b_std = st.stdev(bv) if len(bv) > 1 else None
    out = {}
    for g, v in groups.items():
        d = st.mean(v) - b_mean
        if g == base:
            out[g] = (0.0, "baseline")
        elif b_std is None:
            out[g] = (d, "no baseline std")
        else:
            out[g] = (d, "above noise" if abs(d) > 2 * b_std else "within noise")
    return out


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "colab_runs/local/results"
    out = sys.argv[2] if len(sys.argv) > 2 else "colab_runs_summary.csv"

    rows = []
    for path in sorted(glob.glob(os.path.join(root, "*", "runs", "*", "results.csv"))):
        run_id = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as fh:
            res = [{k.strip(): v.strip() for k, v in x.items()} for x in csv.DictReader(fh)]
        if not res:
            continue
        best = max(res, key=lambda x: float(x["metrics/mAP50(B)"]))
        m = re.match(r"run(\d+)(?:s(\d+))?_(.+?)(?:_seed\d+)?$", run_id)
        rows.append({
            "run_id": run_id,
            "group": f"run{m.group(1)}_{m.group(3)}" if m else run_id,
            "seed": m.group(2) or "0" if m else "?",
            "epochs_run": len(res), "best_epoch": best["epoch"],
            "precision": best["metrics/precision(B)"], "recall": best["metrics/recall(B)"],
            "mAP50": best["metrics/mAP50(B)"], "mAP50-95": best["metrics/mAP50-95(B)"],
        })

    if rows:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)

    groups = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(float(r["mAP50"]))
    deltas = delta_vs_baseline(groups)
    print("group", "n", "mAP50_mean", "mAP50_std", "delta_vs_baseline", "verdict", sep="\t")
    for g, v in groups.items():
        d, verdict = deltas.get(g, (None, "-"))
        print(g, len(v), f"{st.mean(v):.4f}", f"{st.stdev(v):.4f}" if len(v) > 1 else "-",
              f"{d:+.4f}" if d is not None else "-", verdict, sep="\t")
    print(f"{len(rows)} runs summarized -> {out}")


if __name__ == "__main__":
    main()
