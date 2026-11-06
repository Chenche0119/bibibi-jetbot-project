"""Generate the staged P5 experiment matrix (see docs/yolo/YOLO模型建置流程_草案.md).

Usage: python3 -I make_experiment_matrix.py <out.csv> [best_version] [seeds]

Stage 1: 100% data, compare dataset versions (raw, v1-v5), default weight_decay.
Stage 2: best version from stage 1, sampling rate x weight_decay.
One row per training run; hsv_h fixed to 0 because the colour-band cue must not be shifted.
"""
import csv, itertools, sys

out = sys.argv[1]
best = sys.argv[2] if len(sys.argv) > 2 else "TBD"
seeds = range(int(sys.argv[3]) if len(sys.argv) > 3 else 3)
FIXED = {"model": "yolov8n.pt", "imgsz": 320, "epochs": 100, "patience": 20, "batch": 32, "hsv_h": 0}

runs = []
for v, s in itertools.product(["raw", "v1", "v2", "v3", "v4", "v5"], seeds):
    runs.append(dict(stage=1, version=v, sampling=100, weight_decay=0.0005, seed=s))
for sr, wd, s in itertools.product([25, 50, 75, 100], [0.0001, 0.0005, 0.005], seeds):
    if sr == 100 and wd == 0.0005:
        continue  # already covered by stage 1
    runs.append(dict(stage=2, version=best, sampling=sr, weight_decay=wd, seed=s))

cols = ["run_id", "stage", "version", "sampling", "weight_decay", "seed"] + list(FIXED)
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
    for i, r in enumerate(runs, 1):
        w.writerow({"run_id": f"r{i:03d}", **r, **FIXED})
print(len(runs), "runs")
