"""Split trainval frames into train/val by block_id (never by single frame).

Usage: python3 -I split_train_val.py <preprocessing_dir> [seed] [val_ratio]

Reads metadata_split.csv, writes metadata_final.csv with split in
{train, val, test, buffer, excluded}. Blocks are assigned per class so both
classes appear in val; val_ratio is the target share of blocks per class.
"""
import csv, os, random, sys
from collections import Counter

d = sys.argv[1]
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
ratio = float(sys.argv[3]) if len(sys.argv) > 3 else 0.2

rows = list(csv.DictReader(open(os.path.join(d, "metadata_split.csv"), encoding="utf-8-sig")))
blocks = {}
for r in rows:
    if r["split"] == "trainval":
        blocks.setdefault((r["class_folder"], r["block_id"]), 0)
        blocks[(r["class_folder"], r["block_id"])] += 1

rng = random.Random(seed)
val_blocks = set()
for cls in sorted({c for c, _ in blocks}):
    ids = sorted(b for c, b in blocks if c == cls)
    rng.shuffle(ids)
    val_blocks.update(ids[:max(1, round(len(ids) * ratio))])

for r in rows:
    if r["split"] == "trainval":
        r["split"] = "val" if r["block_id"] in val_blocks else "train"

with open(os.path.join(d, "metadata_final.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

print(sorted(val_blocks))
print(dict(Counter((r["class_folder"], r["split"]) for r in rows)))
