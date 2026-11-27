# 本地版：由 colab_runs/make_v4_runs.py 產生（以 run24 為底）。執行前：pip install ultralytics==8.4.183；需先執行 make_noparked_dataset.py 與 make_gate_split.py


RUN = 'run26s2_v3noparked_flip_mosaic_scale_control_seed2'


# 環境偵測：Colab 或本地（Mac / Windows / Linux）皆可執行同一份程式
# - Colab：掛載 Drive，權重與結果直接寫入 Drive（斷線也不會遺失，可續跑）
# - 本地：資料放 ./work，結果放 ./results/<run>
import os, sys, torch
IN_COLAB = 'google.colab' in sys.modules
if IN_COLAB:
    from google.colab import drive
    drive.mount('/content/drive')
    WORK = '/content'
    OUT = f'/content/drive/MyDrive/capstone_yolo/{RUN}'
else:
    WORK = os.path.abspath('./work')
    OUT = os.path.abspath(f'./results/{RUN}')
os.makedirs(WORK, exist_ok=True); os.makedirs(OUT, exist_ok=True)
DEVICE = 0 if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu')
WORKERS = 2 if IN_COLAB else 4
print('環境：', 'Colab' if IN_COLAB else '本地', '｜device =', DEVICE, '｜結果目錄 =', OUT)


# 使用本地衍生資料 v3_noparked（新規則：不標停放自行車），不連 Roboflow
SRC = f'{WORK}/v3_noparked'
assert os.path.exists(f'{SRC}/data.yaml'), '請先執行 colab_runs/make_noparked_dataset.py'
print('v3_noparked 資料：', SRC)


# 5 類合併為 2 類：person=0、其餘（bicycle/bus/car/motorcycle）=dynamic_obstacle=1
# 只改標籤檔，不耗 Roboflow 點數；遇到 polygon 時轉成外接矩形
import yaml, shutil, glob, collections
DST = f'{WORK}/data2_{RUN}'  # 每個 run 各用一份資料夾，避免擴增互相污染
names = yaml.safe_load(open(f'{SRC}/data.yaml'))['names']
if isinstance(names, dict): names = [names[k] for k in sorted(names)]
cmap = {i: (0 if n == 'person' else 1) for i, n in enumerate(names)}
print('原始類別順序：', names)
shutil.rmtree(DST, ignore_errors=True)
cnt = collections.defaultdict(collections.Counter)
for sp in ['train', 'valid', 'test']:
    os.makedirs(f'{DST}/{sp}/images', exist_ok=True); os.makedirs(f'{DST}/{sp}/labels', exist_ok=True)
    for im in glob.glob(f'{SRC}/{sp}/images/*'):
        shutil.copy2(im, f'{DST}/{sp}/images/')
    for lf in glob.glob(f'{SRC}/{sp}/labels/*.txt'):
        out = []
        for ln in open(lf).read().strip().splitlines():
            p = ln.split()
            c = cmap[int(p[0])]; v = list(map(float, p[1:]))
            if len(v) > 4:  # polygon -> bbox
                xs, ys = v[0::2], v[1::2]
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                v = [(x0+x1)/2, (y0+y1)/2, x1-x0, y1-y0]
            out.append(' '.join([str(c)] + [f'{a:.6f}' for a in v])); cnt[sp][c] += 1
        open(f'{DST}/{sp}/labels/{os.path.basename(lf)}', 'w').write('\n'.join(out) + ('\n' if out else ''))
yaml.safe_dump({'path': DST, 'train': 'train/images', 'val': 'valid/images', 'test': 'test/images',
                'names': {0: 'person', 1: 'dynamic_obstacle'}}, open(f'{DST}/data.yaml', 'w'))
for sp in cnt: print(sp, '影像', len(os.listdir(f'{DST}/{sp}/images')), '實例 person/dynamic_obstacle =', cnt[sp][0], cnt[sp][1])


# gate 新資料（2026/11/12）：影片 1 -> train（僅 USE_GATE 時），影片 2 -> gate_test（兩組都建，只評估用）
# 防污染：gate_test 放在 DST/gate_test，不在 train/valid/test 內，所以離線翻轉與訓練都碰不到它。
USE_GATE = False
GATE = f'{WORK}/gate_split'
assert os.path.exists(f'{GATE}/split_manifest.csv'), '請先執行 colab_runs/make_gate_split.py'
import hashlib
def _md5(p): return hashlib.md5(open(p, 'rb').read()).hexdigest()
if USE_GATE:
    for k in ('images', 'labels'):
        for f in glob.glob(f'{GATE}/train/{k}/*'):
            shutil.copy2(f, f'{DST}/train/{k}/')
for k in ('images', 'labels'):
    os.makedirs(f'{DST}/gate_test/{k}', exist_ok=True)
    for f in glob.glob(f'{GATE}/test/{k}/*'):
        shutil.copy2(f, f'{DST}/gate_test/{k}/')
yaml.safe_dump({'path': DST, 'train': 'train/images', 'val': 'gate_test/images', 'names': {0: 'person', 1: 'dynamic_obstacle'}},
               open(f'{DST}/gate_test.yaml', 'w'))
# 洩漏檢查（失敗即中止）：gate_test 的檔名與內容都不可出現在 train / valid / test
_gt = {os.path.basename(f) for f in glob.glob(f'{DST}/gate_test/images/*')}
_gh = {_md5(f) for f in glob.glob(f'{DST}/gate_test/images/*')}
for sp in ('train', 'valid', 'test'):
    fs = glob.glob(f'{DST}/{sp}/images/*')
    assert not _gt & {os.path.basename(f) for f in fs}, f'gate_test 檔名出現在 {sp}'
    assert not _gh & {_md5(f) for f in fs}, f'gate_test 內容出現在 {sp}'
print('gate_test', len(_gt), '張；USE_GATE =', USE_GATE, '；train 現有', len(os.listdir(f'{DST}/train/images')), '張；洩漏檢查通過')


# 離線擴增（只作用在 train）：只做左右翻轉，不旋轉（角度 {0}）× 左右翻轉 {否,是}
# = 2 種組合（含原圖）；train 張數見下方輸出。valid / test 不動。
# 旋轉在 640x640 的 Resize 結果上進行（與訓練輸入一致），不放大畫布，超出範圍的角落補黑。
# 框：四個角點旋轉後取外接矩形；裁切後面積 < 40% 或邊長 < 12 px 的框丟棄（此門檻為暫定）。
import cv2, numpy as np
ANGLES = [0]
MIN_KEEP, MIN_PX = 0.4, 12
def aug_one(img, boxes, angle, flip):
    H, W = img.shape[:2]
    bx = [list(b) for b in boxes]
    if flip:
        img = img[:, ::-1].copy()
        for b in bx: b[1] = 1 - b[1]
    M = cv2.getRotationMatrix2D((W/2, H/2), angle, 1.0)
    if angle != 0: img = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    res = []
    for c, xc, yc, w, h in bx:
        x0, x1, y0, y1 = (xc-w/2)*W, (xc+w/2)*W, (yc-h/2)*H, (yc+h/2)*H
        pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]]).T
        r = M @ pts
        a0, a1, b0, b1 = r[0].min(), r[0].max(), r[1].min(), r[1].max()
        full = (a1-a0)*(b1-b0)
        a0, a1, b0, b1 = max(a0, 0), min(a1, W), max(b0, 0), min(b1, H)
        if a1 <= a0 or b1 <= b0: continue
        if (a1-a0)*(b1-b0) < MIN_KEEP*full or (a1-a0) < MIN_PX or (b1-b0) < MIN_PX: continue
        res.append((int(c), (a0+a1)/2/W, (b0+b1)/2/H, (a1-a0)/W, (b1-b0)/H))
    return img, res
tdir = f'{DST}/train'
made = 0
for ip in sorted(glob.glob(f'{tdir}/images/*')):
    stem = os.path.splitext(os.path.basename(ip))[0]
    if '_rot' in stem: continue
    img = cv2.imread(ip)
    lp = f'{tdir}/labels/{stem}.txt'
    boxes = [tuple(map(float, l.split())) for l in open(lp).read().strip().splitlines()] if os.path.exists(lp) else []
    for flip in (0, 1):
        for ang in ANGLES:
            if ang == 0 and flip == 0: continue  # 原圖
            im2, bb = aug_one(img, boxes, ang, flip)
            nm = f'{stem}_rot{ang}_f{flip}'
            cv2.imwrite(f'{tdir}/images/{nm}.jpg', im2)
            open(f'{tdir}/labels/{nm}.txt', 'w').write(''.join(f'{int(c)} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n' for c, x, y, w, h in bb)); made += 1
print('新增', made, '張；train 現有', len(os.listdir(f'{tdir}/images')), '張')


# 訓練：以 run09 為基準（YOLOv8n、imgsz=320、batch=32、seed=2、weight_decay=0.0005），只改下面標示的因素
# mosaic=1.0、scale=0.5、translate=0.1、close_mosaic=0、epochs=100、patience=100
from ultralytics import YOLO
LAST = f'{OUT}/runs/{RUN}/weights/last.pt'
resume = os.path.exists(LAST)  # 斷線或中斷後再執行這格，會從 last.pt 續跑
model = YOLO(LAST if resume else 'yolov8n.pt')
print('續跑' if resume else '從頭訓練')
model.train(**(dict(resume=True) if resume else dict(data=f'{DST}/data.yaml', imgsz=320, batch=32, seed=2, deterministic=True,
            weight_decay=0.0005, workers=WORKERS, device=DEVICE, project=f'{OUT}/runs', name=RUN, exist_ok=True, save_period=10,
            epochs=100, patience=100, close_mosaic=0,
            hsv_h=0, hsv_s=0, hsv_v=0, degrees=0, translate=0.1, scale=0.5, shear=0, perspective=0, flipud=0, fliplr=0, mosaic=1.0, mixup=0, copy_paste=0, erasing=0)))


# 以 best.pt 在 test 評估（test 只評估這一次；選模型看 valid）
best = YOLO(f'{OUT}/runs/{RUN}/weights/best.pt')
m = best.val(data=f'{DST}/data.yaml', split='test', imgsz=320, plots=True, device=DEVICE, project=f'{OUT}/runs', name=f'{RUN}_test', exist_ok=True)
print('mAP50', round(m.box.map50, 3), 'mAP50-95', round(m.box.map, 3), 'P', round(m.box.mp, 3), 'R', round(m.box.mr, 3))
# 逐類 AP：box.ap50 對應 ap_class_index（資料中實際出現的類別），不是全部類別名稱
for i, c in enumerate(m.box.ap_class_index):
    print(m.names[int(c)], 'AP50', round(float(m.box.ap50[i]), 3), '實例數', int(m.nt_per_class[int(c)]))


# 以 valid 的 F1-信心曲線選推論 conf 門檻；test 只在這個門檻上讀 P / R（不在 test 上調門檻）
import numpy as np
mv = best.val(data=f'{DST}/data.yaml', split='val', imgsz=320, plots=False, device=DEVICE, project=f'{OUT}/runs', name=f'{RUN}_valconf', exist_ok=True)
px = np.asarray(mv.box.px); f1m = np.asarray(mv.box.f1_curve).mean(0)
i = int(f1m.argmax()); print('valid 平均 F1 最大的 conf =', round(float(px[i]), 3), '｜F1 =', round(float(f1m[i]), 3))
for k, c in enumerate(mv.box.ap_class_index):
    fk = np.asarray(mv.box.f1_curve)[k]; j = int(fk.argmax())
    print(mv.names[int(c)], '各類最佳 conf', round(float(px[j]), 3), '｜valid F1', round(float(fk[j]), 3))
print('test 在 conf*：P', round(float(np.asarray(m.box.p_curve).mean(0)[i]), 3), 'R', round(float(np.asarray(m.box.r_curve).mean(0)[i]), 3),
      '｜對照 conf=0.25 與 0.15 的 test P / R：',
      [(t, round(float(np.asarray(m.box.p_curve).mean(0)[int(t * 999)]), 3), round(float(np.asarray(m.box.r_curve).mean(0)[int(t * 999)]), 3)) for t in (0.25, 0.15)])

# gate_test 評估：只在訓練結束後做一次，不用來選 epoch、conf 門檻或 seed（選模型看 valid）
for _conf in (0.001, 0.25):
    mg = best.val(data=f'{DST}/gate_test.yaml', split='val', imgsz=320, conf=_conf, plots=False, device=DEVICE,
                  project=f'{OUT}/runs', name=f'{RUN}_gatetest_c{_conf}', exist_ok=True)
    print(f'gate_test conf={_conf}: mAP50', round(mg.box.map50, 3), 'mAP50-95', round(mg.box.map, 3), 'P', round(mg.box.mp, 3), 'R', round(mg.box.mr, 3))
    for i, c in enumerate(mg.box.ap_class_index):
        print('  ', mg.names[int(c)], 'AP50', round(float(mg.box.ap50[i]), 3), 'P', round(float(mg.box.p[i]), 3), 'R', round(float(mg.box.r[i]), 3), '實例數', int(mg.nt_per_class[int(c)]))
