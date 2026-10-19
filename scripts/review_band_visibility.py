"""D5 review list: images whose wristband may be visible at 640x480 but too small at model input size.

Usage: python3 -I review_band_visibility.py [metadata=preprocessing/metadata_final.csv] [imgsz=320] [min_px=100]

band_px is measured at 640x480; at imgsz x imgsz the area scales by (imgsz/640)*(imgsz/480).
Road images (not excluded) whose scaled band area is below min_px are written to
preprocessing/band_review.csv for manual re-check against the labeling rule (3.6 / risk 9).
"""
import csv, sys

meta = sys.argv[1] if len(sys.argv) > 1 else "preprocessing/metadata_final.csv"
imgsz = int(sys.argv[2]) if len(sys.argv) > 2 else 320
min_px = float(sys.argv[3]) if len(sys.argv) > 3 else 100
scale = (imgsz / 640) * (imgsz / 480)

rows = []
for r in csv.DictReader(open(meta, encoding="utf-8-sig")):
    if r["class_folder"] != "road" or r["exclude"] == "1" or not r["band_px"]:
        continue
    scaled = float(r["band_px"]) * scale
    if scaled < min_px:
        rows.append((r["filename"], r["band_px"], round(scaled, 1), r["split"]))

rows.sort(key=lambda x: float(x[1]))
with open("preprocessing/band_review.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["filename", "band_px_640", f"band_px_{imgsz}", "split"])
    w.writerows(rows)
print(f"{len(rows)} images below {min_px}px at {imgsz}x{imgsz} -> preprocessing/band_review.csv")
