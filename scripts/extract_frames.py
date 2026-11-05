#!/usr/bin/env python3
"""補拍影片抽幀（2026-10-29）。

定點錄影 -> alert_<location>_<slot>_NNNN.jpg（NNNN 接續資料夾內既有最大序號）
前進錄影 -> <uuid1>.jpg，並寫 extraction_log.csv

預設 --dry-run：只掃描影片、算模糊度分佈與預計張數，不寫任何影像。
加 --write 才會輸出影像；輸出一律放在 --out 指定的新資料夾，不動既有檔案。
"""
import argparse, csv, glob, json, os, re, sys, uuid
import cv2
import numpy as np


def blur_score(img):
    return cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


def scan_frames(path, interval_s):
    """每隔 interval_s 秒取一幀，回傳 (frame_idx, time_s, img)。"""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
    step = max(1, int(round(fps * interval_s)))
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, img = cap.retrieve()
            if ok:
                yield idx, idx / fps, img
        idx += 1
    cap.release()


def next_serial(folder):
    mx = 0
    for f in os.listdir(folder):
        m = re.match(r"alert_.*_(\d{4})\.jpg$", f)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="raw/task_A_alert_20261029")
    ap.add_argument("--serial-dir", required=True, help="掃描既有 alert_*_NNNN.jpg 以決定起始序號")
    ap.add_argument("--out", required=True, help="輸出資料夾（新資料夾）")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--forward-interval", type=float, default=1.0, help="前進錄影抽幀間隔（秒）")
    ap.add_argument("--blur-thresh", type=float, default=0.0, help="blur_score 低於此值剔除")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--report", default=None, help="dry-run 時輸出每幀 blur 的 csv")
    a = ap.parse_args()

    serial = next_serial(a.serial_dir)
    print(f"起始序號：{serial:04d}｜間隔 {a.interval}s｜blur 門檻 {a.blur_thresh}｜{'WRITE' if a.write else 'DRY-RUN'}")
    rows, rep = [], []

    jobs = [(p, "static") for p in sorted(glob.glob(os.path.join(a.root, "videos", "*.avi")))]
    jobs += [(p, "forward") for p in sorted(glob.glob(os.path.join(a.root, "forward", "videos", "*.avi")))]
    if a.write:
        os.makedirs(os.path.join(a.out, "static"), exist_ok=True)
        os.makedirs(os.path.join(a.out, "forward"), exist_ok=True)

    for path, mode in jobs:
        meta = json.load(open(path[:-4] + ".json"))
        loc, slot = meta.get("location", "road"), meta.get("slot", "am")
        name = os.path.basename(path)
        scores, kept = [], 0
        itv = a.interval if mode == "static" else a.forward_interval
        for idx, t, img in scan_frames(path, itv):
            b = blur_score(img)
            scores.append(b)
            rep.append((name, mode, idx, round(t, 2), round(b, 1)))
            if b < a.blur_thresh:
                continue
            kept += 1
            if a.write:
                if mode == "static":
                    fn = f"alert_{loc}_{slot}_{serial:04d}.jpg"
                    serial += 1
                    sub = "static"
                else:
                    fn = f"{uuid.uuid1()}.jpg"
                    sub = "forward"
                cv2.imwrite(os.path.join(a.out, sub, fn), img)
                rows.append([fn, name, idx, round(t, 2), round(b, 1), loc, slot])
        s = np.array(scores) if scores else np.array([0.0])
        print(f"{name:45s} {mode:7s} 候選 {len(scores):4d}｜保留 {kept:4d}｜blur min/p10/med/max = "
              f"{s.min():.0f}/{np.percentile(s,10):.0f}/{np.median(s):.0f}/{s.max():.0f}")

    if a.report:
        with open(a.report, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["source_video", "mode", "frame_idx", "time_s", "blur_score"]); w.writerows(rep)
    if a.write:
        with open(os.path.join(a.out, "extraction_log.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["filename", "source_video", "frame_idx", "time_s", "blur_score", "location", "slot"]); w.writerows(rows)
        print(f"已輸出 {len(rows)} 張到 {a.out}；log: extraction_log.csv")


if __name__ == "__main__":
    sys.exit(main())
