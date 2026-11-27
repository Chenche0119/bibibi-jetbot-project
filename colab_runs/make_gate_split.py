"""把 gate-noon（2026/11/12）新資料依「來源影片」切成 train／test，並做洩漏檢查。

規則（避免資料污染）：
- 以來源影片切分，不以單張切分：影片 1（081721）→ gate_train；影片 2（082754）→ gate_test。
- gate_test 只用來最後評估，不拿來選 epoch、conf 門檻或 seed；不做任何擴增。
- 標籤由 Roboflow 匯出 zip 讀入，依「類別名稱」對應成 person=0、dynamic_obstacle=1（不依賴類別編號）。
- 影像用 frames_selected 裡的原始 JPG（不是 Roboflow 重新編碼的版本）。

輸出：local/work/gate_split/{train,test}/{images,labels}、split_manifest.csv、leak_check.txt
用法：python make_gate_split.py <roboflow_zip> <frames_selected_dir>
"""
import csv, glob, hashlib, os, shutil, sys, zipfile
import cv2, numpy as np, yaml

ZIP, FRAMES = sys.argv[1], sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = f'{HERE}/local/work/gate_split'
V3 = f'{HERE}/local/work/v3_noparked'
TRAIN_VIDEO, TEST_VIDEO = 'gate-noon-20261112-081721.avi', 'gate-noon-20261112-082754.avi'

shutil.rmtree(OUT, ignore_errors=True)
for sp in ('train', 'test'):
    for k in ('images', 'labels'):
        os.makedirs(f'{OUT}/{sp}/{k}')

# 1) 讀 Roboflow 匯出：類別名稱 -> 我們的編號
tmp = f'{OUT}/_zip'
zipfile.ZipFile(ZIP).extractall(tmp)
names = yaml.safe_load(open(f'{tmp}/data.yaml'))['names']
if isinstance(names, dict):
    names = [names[k] for k in sorted(names)]
cmap = {i: {'person': 0, 'dynamic_obstacle': 1}[n] for i, n in enumerate(names)}
print('Roboflow 類別順序', names, '->', cmap)
labels = {}
for lf in glob.glob(f'{tmp}/train/labels/*.txt'):
    stem = os.path.basename(lf).split('_jpg.rf.')[0]
    rows = []
    for ln in open(lf).read().strip().splitlines():
        p = ln.split()
        if p:
            rows.append(' '.join([str(cmap[int(p[0])])] + p[1:]))
    labels[stem] = rows

# 2) 依來源影片分配
man = []
for r in csv.DictReader(open(f'{FRAMES}/extraction_log.csv')):
    stem = r['filename'][:-4]
    sp = 'train' if r['source_video'] == TRAIN_VIDEO else 'test' if r['source_video'] == TEST_VIDEO else None
    assert sp, r['source_video']
    assert stem in labels, f'缺標籤：{stem}'
    shutil.copy2(f"{FRAMES}/{r['filename']}", f'{OUT}/{sp}/images/')
    open(f'{OUT}/{sp}/labels/{stem}.txt', 'w').write('\n'.join(labels[stem]) + ('\n' if labels[stem] else ''))
    man.append([r['filename'], sp, r['source_video'], r['time_s'], len(labels[stem])])
shutil.rmtree(tmp)
with open(f'{OUT}/split_manifest.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['filename', 'split', 'source_video', 'time_s', 'n_boxes']); w.writerows(man)

# 3) 洩漏檢查（失敗就中止）
rep = []
def listing(d): return sorted(os.listdir(d))
tr, te = listing(f'{OUT}/train/images'), listing(f'{OUT}/test/images')
rep.append(f'gate train {len(tr)} 張／gate test {len(te)} 張；標籤檔數 {len(listing(OUT + "/train/labels"))}／{len(listing(OUT + "/test/labels"))}')
assert not set(tr) & set(te), '檔名重複'
assert len(set(r[0] for r in man)) == len(man), '清單內有重複檔名'
assert not {r[2] for r in man if r[1] == 'train'} & {r[2] for r in man if r[1] == 'test'}, '同一支影片跨切分'

def md5(p): return hashlib.md5(open(p, 'rb').read()).hexdigest()
def dhash(p):
    g = cv2.resize(cv2.cvtColor(cv2.imread(p), cv2.COLOR_BGR2GRAY), (9, 8))
    return (g[:, 1:] > g[:, :-1]).flatten()
def collect(d): return sorted(glob.glob(f'{d}/*.jpg') + glob.glob(f'{d}/*.png'))
v3 = {sp: collect(f'{V3}/{sp}/images') for sp in ('train', 'valid', 'test')}
gt, gte = collect(f'{OUT}/train/images'), collect(f'{OUT}/test/images')
m5 = {}
for name, files in [('gate_train', gt)] + [(f'v3_{k}', v) for k, v in v3.items()]:
    for p in files:
        m5.setdefault(md5(p), []).append(name)
exact = [(p, m5[md5(p)]) for p in gte if md5(p) in m5]
rep.append(f'gate test 與其他集合的完全相同檔案：{len(exact)}（必須為 0）')
assert not exact, exact[:3]

# 近似重複：gate test 對 gate train 與 v3 全部影像的最小 dHash 距離（只報告，同地點相鄰影片預期偏近）
ref = [(p, dhash(p), 'gate_train') for p in gt] + [(p, dhash(p), f'v3_{k}') for k, v in v3.items() for p in v]
H = np.array([h for _, h, _ in ref])
d = []
for p in gte:
    dist = (H != dhash(p)).sum(1); j = int(dist.argmin()); d.append((int(dist[j]), os.path.basename(p), ref[j][2]))
dd = np.array([x[0] for x in d])
rep.append(f'gate test 對最近鄰（64 位元 dHash）距離：最小 {dd.min()}，中位數 {int(np.median(dd))}，距離 <= 4 的有 {(dd <= 4).sum()} 張')
for x in sorted(d)[:5]:
    rep.append(f'  最近 5 名：{x}')
rep.append('距離 <= 4 的張數若大於 0，代表 gate test 有影像與訓練影像幾乎相同，需人工檢視。')
open(f'{OUT}/leak_check.txt', 'w').write('\n'.join(rep) + '\n')
print('\n'.join(rep))
