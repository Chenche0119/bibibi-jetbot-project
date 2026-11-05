#!/usr/bin/env python3
"""依 picks.csv 產生補拍資料的 train/valid/test 切分計畫（2026-10-29）。

規則：
1. 一般類別（person、empty）：整支影片只進一個切分（test=071820、valid=073035、其餘 train）。
   前進錄影（uuid1）全放 train。
2. 動態障礙物（bus、motorcycle、bicycle、car，合併為 dynamic_obstacle）：把相鄰（<=GAP 秒）的同類影格合成「事件」，事件整組進同一切分，
   依各切分缺額貪婪分配到 valid / test / train（目標比例 6:2:2）。
3. 緩衝：事件被分到某切分後，同一支影片中與事件相距 <= BUFFER 秒、且原本會進其他切分的影格一律排除，避免近乎相同的影格跨切分。
4. 下採樣：empty 共 50（test 25／valid 10／train 15）、person 共 100（train 70／valid 15／test 15），各池內等距取樣。
5. 捨棄（drop）與未標記（pick 空白）不納入。
"""
import csv, os, re, shutil, sys, collections

PICKS, FRAMES, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
GAP, BUFFER = 10.0, 6.0
VIDEO_SPLIT = {"071820": "test", "073035": "valid"}
DYN = ["bus", "motorcycle", "bicycle", "car"]  # 合併為 dynamic_obstacle 後一起分配
TARGET = {"train": 0.6, "valid": 0.2, "test": 0.2}
CAP = {"empty": {"test": 25, "valid": 10, "train": 15}, "person": {"train": 70, "valid": 15, "test": 15}}


def vid(v):
    m = re.search(r"-(\d{6})\.avi$", v)
    return m.group(1) if m else "fwd"


rows = list(csv.DictReader(open(PICKS, encoding="utf-8-sig")))
for r in rows:
    r["t"] = float(r["time_s"]); r["vid"] = vid(r["source_video"])
    r["base"] = VIDEO_SPLIT.get(r["vid"], "train")
    r["split"] = None; r["note"] = ""
keep = [r for r in rows if r["pick"] not in ("", "drop")]

# --- dynamic_obstacle 事件
events = []  # ("dyn", vid, [rows])
for v in sorted({r["vid"] for r in keep if r["pick"] in DYN}):
    fr = sorted([r for r in keep if r["pick"] in DYN and r["vid"] == v], key=lambda r: r["t"])
    cur = [fr[0]]
    for r in fr[1:]:
        if r["t"] - cur[-1]["t"] <= GAP: cur.append(r)
        else: events.append(("dyn", v, cur)); cur = [r]
    events.append(("dyn", v, cur))
got = collections.Counter()
for p, v, fr in sorted(events, key=lambda e: -len(e[2])):
    tot = sum(got.values()) + len(fr)
    split = max(TARGET, key=lambda s: TARGET[s] * tot - got[s])
    got[split] += len(fr)
    for r in fr: r["split"] = split; r["note"] = "dyn-event"

# --- 緩衝：排除與事件相距太近、但分屬不同切分的影格
ev_frames = [(r["vid"], r["t"], r["split"]) for p, v, fr in events for r in fr]
for r in keep:
    if r["split"] is None:
        for v, t, s in ev_frames:
            if v == r["vid"] and abs(t - r["t"]) <= BUFFER and s != r["base"]:
                r["split"] = "excluded"; r["note"] = "buffer"; break

# --- 一般類別
for r in keep:
    if r["split"] is None: r["split"] = r["base"]

# --- 下採樣
def even_pick(lst, n):
    lst = sorted(lst, key=lambda r: (r["vid"], r["t"]))
    if len(lst) <= n: return lst
    return [lst[int(i * len(lst) / n)] for i in range(n)]

for p, caps in CAP.items():
    for s, n in caps.items():
        pool = [r for r in keep if r["pick"] == p and r["split"] == s]
        chosen = {id(r) for r in even_pick(pool, n)}
        for r in pool:
            if id(r) not in chosen: r["split"] = "excluded"; r["note"] = "downsample"

# --- 輸出
os.makedirs(OUT, exist_ok=True)
fields = ["filename", "source_video", "time_s", "pick", "split", "note"]
with open(os.path.join(OUT, "split_plan.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader()
    for r in rows:
        if r["pick"] in ("", "drop"): r["split"] = r["split"] or "not_used"; r["note"] = r["note"] or ("blank" if r["pick"] == "" else "drop")
        w.writerow(r)
for s in TARGET:
    os.makedirs(os.path.join(OUT, s), exist_ok=True)
for r in keep:
    if r["split"] in TARGET:
        sub = "static" if r["filename"].startswith("alert_") else "forward"
        shutil.copy2(os.path.join(FRAMES, sub, r["filename"]), os.path.join(OUT, r["split"], r["filename"]))

tab = collections.defaultdict(collections.Counter)
for r in keep:
    if r["split"] in TARGET: tab[r["split"]][r["pick"]] += 1
print("切分結果（影像張數；car/bus/motorcycle/bicycle 皆屬 dynamic_obstacle）")
for s in TARGET: print(f"{s:6s} {sum(tab[s].values()):4d}", dict(tab[s]))
print("排除：", collections.Counter(r["note"] for r in keep if r["split"] == "excluded"))
