# 掃描信心門檻（conf）：在修訂標註規則的資料（v3_noparked）上，用 run13／run14 的 best.pt 評估 recall、precision、誤報。
# 方法：每張影像以 conf=0.01 預測一次，再依門檻過濾；同類別內依信心由高到低與 GT 一對一配對（IoU >= 0.5）。
# 用法：python conf_sweep.py（路徑以本檔所在位置為準）；結果寫到 conf_sweep_output.txt
import os, glob, collections
import numpy as np
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = f'{HERE}/local'
DATA = f'{ROOT}/work/v3_noparked'
GROUPS = {
    'run13(無擴增)': ['run13_v3noparked_baseline', 'run13s1_v3noparked_baseline_seed1', 'run13s2_v3noparked_baseline_seed2'],
    'run14(只翻轉)': ['run14_v3noparked_offlineflip', 'run14s1_v3noparked_offlineflip_seed1', 'run14s2_v3noparked_offlineflip_seed2'],
}
THRS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70]
S = 640

def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1]); x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x1 - x0) * max(0, y1 - y0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0

def load_gt(split):
    out = {}
    for ip in sorted(glob.glob(f'{DATA}/{split}/images/*')):
        st = os.path.splitext(os.path.basename(ip))[0]
        g = []
        for l in open(f'{DATA}/{split}/labels/{st}.txt').read().strip().splitlines():
            if not l.strip(): continue
            c, xc, yc, w, h = l.split(); c = int(c); xc, yc, w, h = (float(v) * S for v in (xc, yc, w, h))
            g.append((0 if c == 4 else 1, (xc - w / 2, yc - h / 2, xc + w / 2, yc + h / 2)))
        out[ip] = g
    return out

def evaluate(preds, gts, thr):
    # 回傳每類 (TP, FP, FN)
    res = {0: [0, 0, 0], 1: [0, 0, 0]}
    for ip, g in gts.items():
        p = [x for x in preds[ip] if x[5] >= thr]
        for cls in (0, 1):
            gg = [b for c, b in g if c == cls]
            pp = sorted([x for x in p if x[4] == cls], key=lambda x: -x[5])
            used = set(); tp = 0
            for x in pp:
                best, bi = 0.5, -1
                for j, b in enumerate(gg):
                    if j in used: continue
                    v = iou(b, x[:4])
                    if v >= best: best, bi = v, j
                if bi >= 0: used.add(bi); tp += 1
            res[cls][0] += tp; res[cls][1] += len(pp) - tp; res[cls][2] += len(gg) - tp
    return res

splits = {sp: load_gt(sp) for sp in ('valid', 'test')}
allres = {}  # group -> split -> thr -> cls -> list over seeds of (tp,fp,fn)
for grp, runs in GROUPS.items():
    allres[grp] = {sp: {t: {0: [], 1: []} for t in THRS} for sp in splits}
    for r in runs:
        m = YOLO(f'{ROOT}/results/{r}/runs/{r}/weights/best.pt')
        for sp, gts in splits.items():
            preds = {}
            for ip in gts:
                rr = m.predict(ip, imgsz=320, conf=0.01, iou=0.7, device='mps', verbose=False)[0]
                preds[ip] = [(*[v * S for v in b.xyxyn[0].tolist()], int(b.cls[0]), float(b.conf[0])) for b in rr.boxes]
            for t in THRS:
                e = evaluate(preds, gts, t)
                for c in (0, 1): allres[grp][sp][t][c].append(e[c])

lines = []
def P(s=''): print(s); lines.append(s)
def agg(lst):
    tp = np.mean([x[0] for x in lst]); fp = np.mean([x[1] for x in lst]); fn = np.mean([x[2] for x in lst])
    rec = tp / (tp + fn) if tp + fn else 0; pre = tp / (tp + fp) if tp + fp else 0
    f1 = 2 * rec * pre / (rec + pre) if rec + pre else 0
    return rec, pre, f1, fp, tp + fn
for grp in GROUPS:
    for sp in ('valid', 'test'):
        n_img = len(splits[sp])
        for cls, cn in ((1, 'dynamic_obstacle'), (0, 'person')):
            P(f'\n### {grp}｜{sp}｜{cn}（3 個 seed 平均；GT {agg(allres[grp][sp][THRS[0]][cls])[4]:.0f} 個，影像 {n_img} 張）')
            P('| conf | recall | precision | F1 | 每張誤報數 |')
            P('|---|---|---|---|---|')
            for t in THRS:
                rec, pre, f1, fp, _ = agg(allres[grp][sp][t][cls])
                P(f'| {t:.2f} | {rec:.3f} | {pre:.3f} | {f1:.3f} | {fp / n_img:.3f} |')
P('\n### 在 valid 上挑門檻、在 test 上報告（dynamic_obstacle）')
P('| 組別 | 挑選規則 | valid 選到的 conf | test recall | test precision | test 每張誤報 |')
P('|---|---|---|---|---|---|')
for grp in GROUPS:
    v = {t: agg(allres[grp]['valid'][t][1]) for t in THRS}
    picks = {'F1 最大': max(THRS, key=lambda t: v[t][2])}
    ok = [t for t in THRS if v[t][0] >= 0.80]; picks['recall>=0.80 的最高門檻'] = max(ok) if ok else None
    ok = [t for t in THRS if v[t][0] >= 0.70]; picks['recall>=0.70 的最高門檻'] = max(ok) if ok else None
    for name, t in picks.items():
        if t is None: P(f'| {grp} | {name} | valid 達不到 | - | - | - |'); continue
        rec, pre, f1, fp, _ = agg(allres[grp]['test'][t][1])
        P(f'| {grp} | {name} | {t:.2f} | {rec:.3f} | {pre:.3f} | {fp / len(splits["test"]):.3f} |')
open(f'{HERE}/conf_sweep_output.txt', 'w', encoding='utf-8').write('\n'.join(lines))
