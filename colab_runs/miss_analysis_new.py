import os, glob, json, sys, collections
import numpy as np
from ultralytics import YOLO

ROOT = '/Users/che/Desktop/嗶嗶嗶專/colab_runs/local'
TEST = f'{ROOT}/work/v3_noparked/test'
NAMES = ['bicycle', 'bus', 'car', 'motorcycle', 'person']
MODELS = {
    'run13': ['run13_v3noparked_baseline', 'run13s1_v3noparked_baseline_seed1', 'run13s2_v3noparked_baseline_seed2'],
    'run14': ['run14_v3noparked_offlineflip', 'run14s1_v3noparked_offlineflip_seed1', 'run14s2_v3noparked_offlineflip_seed2'],
}
S = 640  # labels are normalised; Roboflow export is 640x640 (stretch)

def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1]); x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0

# ground truth
gts = []  # dict per GT
imgs = sorted(glob.glob(f'{TEST}/images/*'))
for ip in imgs:
    stem = os.path.splitext(os.path.basename(ip))[0]
    lp = f'{TEST}/labels/{stem}.txt'
    if not os.path.exists(lp): continue
    for l in open(lp).read().strip().splitlines():
        c, xc, yc, w, h = l.split(); c = int(c); xc, yc, w, h = map(float, (xc, yc, w, h))
        gts.append(dict(img=stem, orig=NAMES[c], cls=0 if c == 4 else 1, xc=xc, yc=yc, w=w, h=h,
                        box=((xc - w / 2) * S, (yc - h / 2) * S, (xc + w / 2) * S, (yc + h / 2) * S)))
print('GT', len(gts), collections.Counter(g['cls'] for g in gts), collections.Counter(g['orig'] for g in gts))

def size_bin(g):
    s = (g['w'] * S * g['h'] * S) ** 0.5
    return '<32px' if s < 32 else '32-64px' if s < 64 else '64-128px' if s < 128 else '>=128px'
def pos_bin(g): return 'top' if g['yc'] < 1 / 3 else 'mid' if g['yc'] < 2 / 3 else 'bottom'
def edge(g): return 'touch_edge' if (g['xc'] - g['w'] / 2 < 0.01 or g['xc'] + g['w'] / 2 > 0.99 or g['yc'] - g['h'] / 2 < 0.01 or g['yc'] + g['h'] / 2 > 0.99) else 'inside'
for g in gts: g['size'] = size_bin(g); g['pos'] = pos_bin(g); g['edge'] = edge(g)

# predictions
res = {}  # group -> list of per-GT outcomes per seed ; and FP counts
for grp, runs in MODELS.items():
    outs = []
    fps = []
    for r in runs:
        w = f'{ROOT}/results/{r}/runs/{r}/weights/best.pt'
        m = YOLO(w)
        preds = {}
        for ip in imgs:
            stem = os.path.splitext(os.path.basename(ip))[0]
            rr = m.predict(ip, imgsz=320, conf=0.25, iou=0.7, device='mps', verbose=False)[0]
            b = rr.boxes
            preds[stem] = [(*bb.xyxyn[0].tolist(), int(bb.cls[0]), float(bb.conf[0])) for bb in b]
        o = []
        matched = collections.defaultdict(set)
        for gi, g in enumerate(gts):
            ps = [(iou(g['box'], tuple(v * S for v in p[:4])), p) for p in preds[g['img']]]
            ok = [(i, p) for i, p in ps if i >= 0.5 and p[4] == g['cls']]
            wrong = [(i, p) for i, p in ps if i >= 0.5 and p[4] != g['cls']]
            near = [(i, p) for i, p in ps if 0.1 <= i < 0.5 and p[4] == g['cls']]
            o.append('hit' if ok else 'wrongcls' if wrong else 'loc' if near else 'miss')
            for i, p in ok: matched[g['img']].add(id(p))
        outs.append(o)
        # false positives: predictions with no GT of same class at IoU>=0.5
        fp = collections.Counter()
        for stem, ps in preds.items():
            for p in ps:
                gg = [g for g in gts if g['img'] == stem and g['cls'] == p[4] and iou(g['box'], tuple(v * S for v in p[:4])) >= 0.5]
                if not gg: fp[p[4]] += 1
        fps.append(fp)
    res[grp] = (outs, fps)

def table(grp, key, cls):
    outs, _ = res[grp]
    rows = collections.OrderedDict()
    for gi, g in enumerate(gts):
        if g['cls'] != cls: continue
        k = g[key]; d = rows.setdefault(k, [0, 0, 0, 0, 0])  # n, hit, wrongcls, loc, miss  (summed over seeds)
        for o in outs:
            d[0] += 1; d[{'hit': 1, 'wrongcls': 2, 'loc': 3, 'miss': 4}[o[gi]]] += 1
    return rows

lines = []
def P(s=''): print(s); lines.append(s)
for cls, cname in ((1, 'dynamic_obstacle'), (0, 'person')):
    for key, title in (('orig', '原始類別'), ('size', '目標大小（640 空間，sqrt(面積)）'), ('pos', '垂直位置'), ('edge', '是否貼邊')):
        if cls == 0 and key == 'orig': continue
        P(f'\n### {cname}｜依{title}（GT 數為 3 seed 累計）')
        P('| 組別 | 分組 | GT 數(÷3) | 命中率 | 判成別類 | 框不準 | 完全漏掉 |')
        P('|---|---|---|---|---|---|---|')
        for grp in MODELS:
            for k, d in table(grp, key, cls).items():
                n = d[0]
                P(f'| {grp} | {k} | {n // 3} | {d[1] / n:.2f} | {d[2] / n:.2f} | {d[3] / n:.2f} | {d[4] / n:.2f} |')

P('\n### 各組總體（3 seed 平均）')
for grp in MODELS:
    outs, fps = res[grp]
    for cls, cname in ((1, 'dynamic_obstacle'), (0, 'person')):
        idx = [i for i, g in enumerate(gts) if g['cls'] == cls]
        tot = len(idx) * len(outs)
        c = collections.Counter(o[i] for o in outs for i in idx)
        P(f'- {grp} {cname}: 命中 {c["hit"] / tot:.3f}，判成別類 {c["wrongcls"] / tot:.3f}，框不準 {c["loc"] / tot:.3f}，完全漏掉 {c["miss"] / tot:.3f}；平均誤報 {np.mean([f[cls] for f in fps]):.1f} 個')

# consistently missed dynamic_obstacle GT (all 6 models fail)
P('\n### 6 個模型全部沒命中的 dynamic_obstacle（GT）')
cons = []
for gi, g in enumerate(gts):
    if g['cls'] != 1: continue
    allo = [o[gi] for grp in MODELS for o in res[grp][0]]
    if all(x != 'hit' for x in allo): cons.append((g, collections.Counter(allo)))
n_dyn = sum(1 for g in gts if g['cls'] == 1)
P(f'共 {len(cons)} / {n_dyn} 個')
for g, c in cons[:40]:
    P(f"- {g['img'][:40]} | {g['orig']} | {g['size']} | {g['pos']} | {g['edge']} | w×h={g['w'] * S:.0f}×{g['h'] * S:.0f} | {dict(c)}")
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'miss_analysis_new_output.txt'), 'w').write('\n'.join(lines))
