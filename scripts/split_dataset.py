"""Assign scene-level test split on top of metadata.csv.

Usage: python3 -I split_dataset.py <preprocessing_dir>

Rules
- Corrupt / exclude=1 files -> split = excluded
- Test = whole contiguous blocks (by sequence number), never single random frames
- BUFFER frames on each side of a test block are set aside (split = buffer)
  so near-identical neighbours cannot leak between trainval and test
- Everything else -> trainval (train/val is split later, by block_id, not by frame)
"""
import csv, os, sys
from collections import Counter

d = sys.argv[1]
BLOCK = {"road": 30, "bg": 15}
TEST_BLOCKS = {            # (class_folder, first_seq_of_block) chosen from visual review
    ("road", 211): "shorts / hedge walkway",
    ("road", 481): "long pants / arch gate",
    ("bg", 622):"bg / street with vehicles",
}
BUFFER = 3

rows = list(csv.DictReader(open(os.path.join(d, "metadata.csv"), encoding="utf-8-sig")))
by_class = {}
for r in rows:
    by_class.setdefault(r["class_folder"], []).append(int(r["seq"]))
start = {k: min(v) for k, v in by_class.items()}

test_ranges = {}
for (cls, first), why in TEST_BLOCKS.items():
    test_ranges[(cls, first)] = (first, first + BLOCK[cls] - 1, why)

for r in rows:
    cls, seq = r["class_folder"], int(r["seq"])
    r["block_id"] = f"{cls}_b{(seq - start[cls]) // BLOCK[cls]:02d}"
    if r["exclude"] == "1":
        r["split"] = "excluded"; continue
    r["split"] = "trainval"
    for (c, _), (a, b, _) in test_ranges.items():
        if c != cls:
            continue
        if a <= seq <= b:
            r["split"] = "test"
        elif a - BUFFER <= seq < a or b < seq <= b + BUFFER:
            r["split"] = "buffer"

cols = list(rows[0].keys())
with open(os.path.join(d, "metadata_split.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)

with open(os.path.join(d, "test_set_files.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(r["filename"] for r in rows if r["split"] == "test") + "\n")

c = Counter((r["class_folder"], r["split"]) for r in rows)
tot = Counter(r["split"] for r in rows)
print(dict(tot)); print(dict(c))
print("test share of usable: %.1f%%" % (100 * tot["test"] / (tot["test"] + tot["trainval"] + tot["buffer"])))
