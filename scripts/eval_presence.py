"""P6 evaluation: per-image class-presence precision/recall on a YOLOv8 split.

Usage: python3 -I eval_presence.py <weights.pt> <dataset_dir> [split=test] [min_side_px=12] [conf=0.25]

Per the workflow doc (3.6), what matters is whether each image's set of classes is right, not
pixel-level box accuracy. Ground-truth and predicted boxes whose shorter side is below
min_side_px (measured at the 'imgsz' scale of the model input, here the label scale of
normalized coords * imgsz) are ignored, so tiny far-away targets do not count.
Prints per-class precision/recall/F1 over images and writes presence_<split>.csv.
"""
import csv, os, sys


def presence(boxes, min_side_px):
    """boxes: iterable of (cls, w_px, h_px) -> set of classes with at least one large-enough box."""
    return {c for c, w, h in boxes if min(w, h) >= min_side_px}


def score(pairs, names):
    """pairs: list of (gt_set, pred_set) per image -> {class: (precision, recall, f1, tp, fp, fn)}."""
    res = {}
    for c in names:
        tp = sum(c in g and c in p for g, p in pairs)
        fp = sum(c not in g and c in p for g, p in pairs)
        fn = sum(c in g and c not in p for g, p in pairs)
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
        res[c] = (pr, rc, f1, tp, fp, fn)
    return res


def read_gt(label_path, imgsz):
    if not os.path.exists(label_path):
        return []
    out = []
    for line in open(label_path, encoding="utf-8"):
        p = line.split()
        if len(p) >= 5:
            out.append((int(p[0]), float(p[3]) * imgsz, float(p[4]) * imgsz))
    return out


def main():
    weights, ds = sys.argv[1:3]
    split = sys.argv[3] if len(sys.argv) > 3 else "test"
    min_side = float(sys.argv[4]) if len(sys.argv) > 4 else 12
    conf = float(sys.argv[5]) if len(sys.argv) > 5 else 0.25

    import yaml
    from ultralytics import YOLO

    model = YOLO(weights)
    names = yaml.safe_load(open(os.path.join(ds, "data.yaml"), encoding="utf-8"))["names"]
    names = dict(enumerate(names)) if isinstance(names, list) else {int(k): v for k, v in names.items()}
    imgsz = int(model.overrides.get("imgsz") or 320)

    img_dir = os.path.join(ds, split, "images")
    lbl_dir = os.path.join(ds, split, "labels")
    files = sorted(f for f in os.listdir(img_dir) if f.lower().endswith((".jpg", ".jpeg", ".png")))

    pairs = []
    for f in files:
        gt = presence(read_gt(os.path.join(lbl_dir, os.path.splitext(f)[0] + ".txt"), imgsz), min_side)
        r = model.predict(os.path.join(img_dir, f), imgsz=imgsz, conf=conf, verbose=False)[0]
        pred = presence(
            ((int(c), w * imgsz / r.orig_shape[1], h * imgsz / r.orig_shape[0])
             for c, (w, h) in zip(r.boxes.cls.tolist(), r.boxes.xywh[:, 2:].tolist())),
            min_side,
        )
        pairs.append((gt, pred))

    res = score(pairs, names)
    out = f"presence_{split}.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["class", "precision", "recall", "f1", "tp", "fp", "fn"])
        for c, (pr, rc, f1, tp, fp, fn) in res.items():
            w.writerow([names[c], f"{pr:.4f}", f"{rc:.4f}", f"{f1:.4f}", tp, fp, fn])
    print(open(out, encoding="utf-8").read())
    print(f"{len(files)} images ({split}), min_side={min_side}px, conf={conf} -> {out}")


if __name__ == "__main__":
    main()
