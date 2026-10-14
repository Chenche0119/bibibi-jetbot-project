"""Build metadata CSV + integrity report for the raw dataset (read-only on raw).

Usage: python3 -I build_metadata.py <raw_dir> <out_dir>
"""
import csv, datetime, glob, hashlib, os, re, sys
import cv2
import numpy as np

raw_dir, out_dir = sys.argv[1], sys.argv[2]
os.makedirs(out_dir, exist_ok=True)

BLUR_THRESHOLD = 650.0      # Laplacian variance below this -> candidate blurry
NEARDUP_THRESHOLD = 6.0     # mean abs diff of 16x16 gray thumbnails vs previous frame
SESSION_GAP_SEC = 120       # mtime gap that starts a new candidate session

files = sorted(glob.glob(os.path.join(raw_dir, "*.jpg")),
               key=lambda p: int(re.search(r"_(\d+)\.jpg$", p).group(1)))
rows, prev_thumb, prev_mtime, session = [], None, None, 1
for p in files:
    name = os.path.basename(p)
    seq = int(re.search(r"_(\d+)\.jpg$", name).group(1))
    kind = name.split("_")[1]  # road / bg
    size = os.path.getsize(p)
    mtime = os.path.getmtime(p)
    with open(p, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    im = cv2.imread(p) if size > 0 else None
    row = dict(filename=name, class_folder=kind, seq=seq, size_bytes=size,
               mtime=datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
               readable=int(im is not None), sha256=sha)
    if im is None:
        row.update(width="", height="", mean_H="", mean_S="", mean_V="", blur_lapvar="",
                   band_px="", diff_prev="", flag_blur="", flag_neardup="", session_id="")
    else:
        h, w = im.shape[:2]
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        H, S, V = (hsv[..., i].astype(int) for i in range(3))
        gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        pink = ((H >= 150) | (H <= 5)) & (S > 110) & (V > 140)
        lime = (H >= 28) & (H <= 48) & (S > 110) & (V > 150)
        thumb = cv2.resize(gray, (16, 16)).astype(np.float32)
        diff = float(np.abs(thumb - prev_thumb).mean()) if prev_thumb is not None else ""
        if prev_mtime is not None and mtime - prev_mtime > SESSION_GAP_SEC:
            session += 1
        prev_thumb, prev_mtime = thumb, mtime
        row.update(width=w, height=h, mean_H=round(float(H.mean()), 1),
                   mean_S=round(float(S.mean()), 1), mean_V=round(float(V.mean()), 1),
                   blur_lapvar=round(lap, 1), band_px=int(pink.sum() + lime.sum()),
                   diff_prev=round(diff, 1) if diff != "" else "",
                   flag_blur=int(lap < BLUR_THRESHOLD),
                   flag_neardup=int(diff != "" and diff < NEARDUP_THRESHOLD),
                   session_id=session)
    # manual columns to be filled by the team
    corrupt = im is None
    row.update(scene="", time_of_day="am", distance="", band_visible="",
               exclude=1 if corrupt else "",
               notes="原始檔損毀且無法復原，永久排除" if corrupt else "")
    rows.append(row)

cols = list(rows[0].keys())
csv_path = os.path.join(out_dir, "metadata.csv")
with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
    wr = csv.DictWriter(fh, fieldnames=cols)
    wr.writeheader(); wr.writerows(rows)

# ---- integrity report ----
bad = [r["filename"] for r in rows if not r["readable"]]
ok = [r for r in rows if r["readable"]]
def n(kind, key=None):
    rs = [r for r in ok if r["class_folder"] == kind]
    return len(rs) if key is None else sum(r[key] for r in rs)
seqs = [r["seq"] for r in rows]
missing = sorted(set(range(min(seqs), max(seqs) + 1)) - set(seqs))
dims = sorted({(r["width"], r["height"]) for r in ok})
sessions = {}
for r in ok:
    sessions.setdefault(r["session_id"], []).append(r["seq"])
with open(os.path.join(out_dir, "integrity_report.md"), "w", encoding="utf-8") as fh:
    fh.write("# 資料完整性報告\n\n")
    fh.write(f"- 來源：`{os.path.basename(raw_dir)}`（唯讀，未修改）\n")
    fh.write(f"- 檔案總數：{len(rows)}；可讀取：{len(ok)}；損毀（0 byte 或無法解碼）：{len(bad)}\n")
    fh.write(f"- road：{n('road')} 張可讀；bg：{n('bg')} 張可讀\n")
    fh.write(f"- 解析度：{dims}\n")
    fh.write(f"- 序號範圍 {min(seqs)}–{max(seqs)}；缺號：{missing if missing else '無'}\n")
    fh.write(f"- 損毀清單（已確認無法復原，永久排除）：{', '.join(bad) if bad else '無'}\n")
    fh.write(f"- 模糊候選（Laplacian 變異數 < {BLUR_THRESHOLD}）：{sum(r['flag_blur'] for r in ok)} 張\n")
    fh.write(f"- 近重複候選（與前一張縮圖差 < {NEARDUP_THRESHOLD}）：{sum(r['flag_neardup'] for r in ok)} 張\n")
    fh.write(f"- 依檔案時間間隔 > {SESSION_GAP_SEC} 秒切出的候選 session：{len(sessions)} 組"
             "（檔案時間可能是複製時間而非實際拍攝時間，僅供參考）\n")
    for sid, sq in sessions.items():
        fh.write(f"  - session {sid}：序號 {min(sq)}–{max(sq)}，{len(sq)} 張\n")
    fh.write("\n## 模糊候選\n" + "\n".join(f"- {r['filename']}（{r['blur_lapvar']}）" for r in ok if r["flag_blur"]) + "\n")
    fh.write("\n## 近重複候選\n" + "\n".join(f"- {r['filename']}（diff={r['diff_prev']}）" for r in ok if r["flag_neardup"]) + "\n")
print("done", csv_path, len(rows), "rows;", len(bad), "bad")
