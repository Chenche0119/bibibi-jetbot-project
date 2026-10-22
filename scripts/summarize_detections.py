"""Risk 11: turn YOLO detections into a class / count / left-center-right summary for alert messages.

Usage: python3 -I summarize_detections.py <weights.pt|labels_dir> <image> [imgsz] [min_side_px]

Boxes with a side shorter than min_side_px (in imgsz pixels, default 12) are dropped so far
targets do not trigger notifications. Output is one JSON object on stdout.
"""
import json, sys
from collections import defaultdict


def position(cx, width):
    return "left" if cx < width / 3 else "right" if cx > width * 2 / 3 else "center"


def summarize(dets, width, min_side=12):
    """dets: iterable of (class_name, x1, y1, x2, y2) in pixels of the width-wide frame."""
    out = defaultdict(lambda: {"count": 0, "left": 0, "center": 0, "right": 0})
    for name, x1, y1, x2, y2 in dets:
        if min(x2 - x1, y2 - y1) < min_side:
            continue
        e = out[name]
        e["count"] += 1
        e[position((x1 + x2) / 2, width)] += 1
    return dict(out)


if __name__ == "__main__":
    weights, image = sys.argv[1], sys.argv[2]
    imgsz = int(sys.argv[3]) if len(sys.argv) > 3 else 320
    min_side = float(sys.argv[4]) if len(sys.argv) > 4 else 12
    from ultralytics import YOLO
    r = YOLO(weights).predict(image, imgsz=imgsz, verbose=False)[0]
    dets = [(r.names[int(c)], *b) for c, b in zip(r.boxes.cls.tolist(), r.boxes.xyxy.tolist())]
    w = r.orig_shape[1]
    print(json.dumps(summarize(dets, w, min_side * w / imgsz), ensure_ascii=False))
