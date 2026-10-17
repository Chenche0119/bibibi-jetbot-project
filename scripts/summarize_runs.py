"""Summarize P5 runs: join experiment matrix with each run's Ultralytics results.csv.

Usage: python3 -I summarize_runs.py <experiment_matrix.csv> <runs_dir> [out.csv]

Takes the best epoch (max mAP50) per run; prints a table and, per (stage, version, sampling,
weight_decay, hsv_h) group, the mean/std of mAP50 across seeds. Runs without results are skipped.
"""
import csv, os, statistics as st, sys
from collections import defaultdict

matrix, runs = sys.argv[1:3]
out = sys.argv[3] if len(sys.argv) > 3 else "p5_summary.csv"
KEYS = ("stage", "version", "sampling", "weight_decay", "hsv_h")

rows = []
for r in csv.DictReader(open(matrix, encoding="utf-8")):
    path = os.path.join(runs, r["run_id"], "results.csv")
    if not os.path.exists(path):
        continue
    res = [{k.strip(): v.strip() for k, v in x.items()} for x in csv.DictReader(open(path, encoding="utf-8"))]
    if not res:
        continue
    best = max(res, key=lambda x: float(x["metrics/mAP50(B)"]))
    rows.append({**{k: r[k] for k in ("run_id", "seed") + KEYS}, "epoch": best["epoch"],
                 "precision": best["metrics/precision(B)"], "recall": best["metrics/recall(B)"],
                 "mAP50": best["metrics/mAP50(B)"], "mAP50-95": best["metrics/mAP50-95(B)"]})

if rows:
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

groups = defaultdict(list)
for r in rows:
    groups[tuple(r[k] for k in KEYS)].append(float(r["mAP50"]))
print(*KEYS, "n", "mAP50_mean", "mAP50_std", sep="\t")
for k, v in groups.items():
    print(*k, len(v), f"{st.mean(v):.4f}", f"{st.stdev(v):.4f}" if len(v) > 1 else "-", sep="\t")
print(f"{len(rows)} runs summarized -> {out}")
