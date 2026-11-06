"""Run one row of the P5 experiment matrix with Ultralytics YOLOv8 (Colab).

Usage: python3 -I train_yolo.py <experiment_matrix.csv> <run_id> <dataset_dir> [out_dir]

<dataset_dir> is a YOLOv8 export (images/{train,val,test}, labels/..., data.yaml).
sampling < 100 keeps a seeded random subset of train images only (val/test untouched),
by writing a train list file and a derived data yaml; hsv_h comes from the matrix row.
"""
import csv, os, random, sys

matrix, run_id, ds = sys.argv[1:4]
out = sys.argv[4] if len(sys.argv) > 4 else "runs_p5"

row = next(r for r in csv.DictReader(open(matrix, encoding="utf-8")) if r["run_id"] == run_id)
seed, sampling = int(row["seed"]), int(row["sampling"])

import yaml
from ultralytics import YOLO

cfg = yaml.safe_load(open(os.path.join(ds, "data.yaml"), encoding="utf-8"))
cfg["path"] = os.path.abspath(ds)
if sampling < 100:
    train_dir = os.path.join(cfg["path"], "train", "images")
    imgs = sorted(f for f in os.listdir(train_dir) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    keep = random.Random(seed).sample(imgs, max(1, round(len(imgs) * sampling / 100)))
    lst = os.path.join(cfg["path"], f"train_{run_id}.txt")
    with open(lst, "w") as fh:
        fh.write("\n".join(os.path.join(train_dir, f) for f in sorted(keep)))
    cfg["train"] = lst
derived = os.path.join(cfg["path"], f"data_{run_id}.yaml")
yaml.safe_dump(cfg, open(derived, "w", encoding="utf-8"))

YOLO(row["model"]).train(
    data=derived, imgsz=int(row["imgsz"]), epochs=int(row["epochs"]), patience=int(row["patience"]),
    batch=int(row["batch"]), weight_decay=float(row["weight_decay"]), hsv_h=float(row["hsv_h"]),
    seed=seed, project=out, name=run_id, exist_ok=True,
)
