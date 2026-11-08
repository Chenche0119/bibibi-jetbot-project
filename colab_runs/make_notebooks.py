#!/usr/bin/env python3
"""產生 v3 資料的 Colab 筆記本 run04~run08（2026-11-03）。用法：python3 make_notebooks.py"""
import nbformat as nbf, os
OUT = os.path.dirname(os.path.abspath(__file__))
DATE = "20261103"

SETUP = '''# 安裝套件（版本與 run01~run03 相同）
!pip -q install ultralytics==8.4.183 roboflow'''
DRIVE = '''# 環境偵測：Colab 或本地（Mac / Windows / Linux）皆可執行同一份程式
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
print('環境：', 'Colab' if IN_COLAB else '本地', '｜device =', DEVICE, '｜結果目錄 =', OUT)'''
DOWNLOAD = '''# 下載 Roboflow v3（YOLOv8 格式）。API key 不寫在程式裡：
# Colab 存在 Secrets 的 ROBOFLOW_API_KEY；本地用環境變數 ROBOFLOW_API_KEY
from roboflow import Roboflow
if IN_COLAB:
    from google.colab import userdata; KEY = userdata.get('ROBOFLOW_API_KEY')
else:
    KEY = os.environ['ROBOFLOW_API_KEY']
SRC = f'{WORK}/v3_raw'
if not os.path.exists(f'{SRC}/data.yaml'):
    rf = Roboflow(api_key=KEY)
    rf.workspace("ssudscompanionrobot").project("alert-yolo-v1").version(3).download("yolov8", location=SRC)
print('v3 資料：', SRC)'''
MERGE = '''# 5 類合併為 2 類：person=0、其餘（bicycle/bus/car/motorcycle）=dynamic_obstacle=1
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
        open(f'{DST}/{sp}/labels/{os.path.basename(lf)}', 'w').write('\\n'.join(out) + ('\\n' if out else ''))
yaml.safe_dump({'path': DST, 'train': 'train/images', 'val': 'valid/images', 'test': 'test/images',
                'names': {0: 'person', 1: 'dynamic_obstacle'}}, open(f'{DST}/data.yaml', 'w'))
for sp in cnt: print(sp, '影像', len(os.listdir(f'{DST}/{sp}/images')), '實例 person/dynamic_obstacle =', cnt[sp][0], cnt[sp][1])'''

OFFLINE = '''# 離線擴增（只作用在 train）：角度 {-180,-135,-90,-45,0,45,90,135}（-180 與 180 相同，已去重）× 左右翻轉 {否,是}
# = 16 種組合（含原圖）→ 664 × 16 = 10,624 張。valid / test 不動。
# 旋轉在 640x640 的 Resize 結果上進行（與訓練輸入一致），不放大畫布，超出範圍的角落補黑。
# 框：四個角點旋轉後取外接矩形；裁切後面積 < 40% 或邊長 < 12 px 的框丟棄（此門檻為暫定）。
import cv2, numpy as np
ANGLES = [-180, -135, -90, -45, 0, 45, 90, 135]
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
            open(f'{tdir}/labels/{nm}.txt', 'w').write(''.join(f'{int(c)} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\\n' for c, x, y, w, h in bb)); made += 1
print('新增', made, '張；train 現有', len(os.listdir(f'{tdir}/images')), '張')'''

def offline_code(angles, flips, head):
    c = OFFLINE.replace("ANGLES = [-180, -135, -90, -45, 0, 45, 90, 135]", f"ANGLES = {angles}")
    c = c.replace("for flip in (0, 1):", f"for flip in {flips}:")
    old = "# 離線擴增（只作用在 train）：角度 {-180,-135,-90,-45,0,45,90,135}（-180 與 180 相同，已去重）× 左右翻轉 {否,是}\n# = 16 種組合（含原圖）→ 664 × 16 = 10,624 張。valid / test 不動。"
    assert old in c
    return c.replace(old, head)

def TRAIN(run, degrees, note, seed=0, imgsz=320):
    return f'''# 訓練：設定與 run02 相同（YOLOv8n、imgsz={imgsz}、epochs=100、patience=20、batch=32、seed={seed}、weight_decay=0.0005）
# 內建擴增全部關閉；{note}
from ultralytics import YOLO
LAST = f'{{OUT}}/runs/{{RUN}}/weights/last.pt'
resume = os.path.exists(LAST)  # 斷線或中斷後再執行這格，會從 last.pt 續跑（epoch 與學習率接續）
model = YOLO(LAST if resume else 'yolov8n.pt')
print('續跑' if resume else '從頭訓練')
model.train(**(dict(resume=True) if resume else dict(data=f'{{DST}}/data.yaml', imgsz={imgsz}, epochs=100, patience=20, batch=32, seed={seed}, deterministic=True,
            weight_decay=0.0005, workers=WORKERS, device=DEVICE, project=f'{{OUT}}/runs', name=RUN, exist_ok=True, save_period=10,
            hsv_h=0, hsv_s=0, hsv_v=0, degrees={degrees}, translate=0, scale=0, shear=0, perspective=0,
            flipud=0, fliplr=0, mosaic=0, mixup=0, copy_paste=0, erasing=0)))'''

EVAL = '''# 以 best.pt 在 test 評估（test 只評估這一次；選模型看 valid）
best = YOLO(f'{OUT}/runs/{RUN}/weights/best.pt')
m = best.val(data=f'{DST}/data.yaml', split='test', imgsz=320, plots=True, device=DEVICE, project=f'{OUT}/runs', name=f'{RUN}_test', exist_ok=True)
print('mAP50', round(m.box.map50, 3), 'mAP50-95', round(m.box.map, 3), 'P', round(m.box.mp, 3), 'R', round(m.box.mr, 3))
# 逐類 AP：box.ap50 對應 ap_class_index（資料中實際出現的類別），不是全部類別名稱
for i, c in enumerate(m.box.ap_class_index):
    print(m.names[int(c)], 'AP50', round(float(m.box.ap50[i]), 3), '實例數', int(m.nt_per_class[int(c)]))'''
SAVE = '''# 複製結果到 Drive
import shutil
for d in [RUN, RUN + '_test']:
    shutil.copytree(f'/content/runs/{d}', f'{DRIVE_OUT}/{d}', dirs_exist_ok=True)
print('已儲存到', DRIVE_OUT)'''

TBL = '| 指標 | valid | test |\n|---|---|---|\n| mAP50 | {vm50} | {tm50} |\n| mAP50-95 | {vm95} | {tm95} |\n| Precision | {vp} | {tp} |\n| Recall | {vr} | {tr} |\n\n| 類別（test，實例數） | AP50 | AP50-95 |\n|---|---|---|\n| person（137） | {pa50} | {pa95} |\n| dynamic_obstacle（91） | {da50} | {da95} |\n'
CONCL = {
 'run04': TBL.format(vm50='0.719', vm95='0.376', vp='0.864', vr='0.622', tm50='0.677', tm95='0.346', tp='0.695', tr='0.627', pa50='0.826', pa95='0.391', da50='0.528', da95='0.302') + '\n本機 Mac M4 Pro（MPS）執行，best 在第 15 epoch，第 35 epoch early stop（約 4 分鐘）。這是 v3 的對照組。dynamic_obstacle 的 valid recall 只有 0.400，是最弱處。\n\n限制：單一 seed；MPS 的 deterministic 有警告（部分運算無確定性實作）；與 run01～03（Colab T4、v2 資料）硬體與資料皆不同，不可直接比較。',
 'run05': TBL.format(vm50='0.662', vm95='0.346', vp='0.790', vr='0.606', tm50='0.579', tm95='0.289', tp='0.749', tr='0.572', pa50='0.588', pa95='0.268', da50='0.570', da95='0.309') + '\n相對 run04（test）：mAP50 -0.098、mAP50-95 -0.057、person AP50 -0.238（皆超出 ±0.03 雜訊，明顯變差）；dynamic_obstacle AP50 +0.042（略超出雜訊，但 test 僅 91 個實例）、recall 0.495 → 0.560。四組擴增中 mAP50 最低。best 在第 30 epoch，共 50 epoch（約 77 分鐘，train 664 → 10,624 張）。\n\n可能原因（推測，未驗證）：大角度（±90°、±135°、180°）使 person 呈倒立或橫躺，與人行道實際情境不符；旋轉後補黑與外接框變鬆；與翻轉混在一起，無法分別歸因。\n\n限制：單一 seed；與 run06～08 同時差在大角度與翻轉，只能整體比較；擴增丟棄框的比例（面積 <40% 或邊長 <12 px，暫定）尚未檢查對 person 實例數的影響。',
 'run09': TBL.format(vm50='0.712', vm95='0.395', vp='0.803', vr='0.654', tm50='0.690', tm95='0.378', tp='0.742', tr='0.695', pa50='0.798', pa95='0.395', da50='0.583', da95='0.362') + '\n相對 run04（test）：mAP50 +0.013（雜訊內）；mAP50-95 +0.032（略超出雜訊，唯一變好的 mAP50-95）；person AP50 -0.028（雜訊內）；dynamic_obstacle AP50 +0.055、recall 0.495 → 0.609。best 在第 39 epoch，共 59 epoch（約 11 分鐘，train 664 → 1,328 張）。\n\n解讀：單獨翻轉沒有傷害 person，並可能小幅改善 dynamic_obstacle，是 run05 拆分中最無害的一項。\n\n限制：單一 seed；test 僅 91 個 dynamic_obstacle；翻轉的好處與人行道左右對稱有關，換場景未必成立。',
 'run10': TBL.format(vm50='0.728', vm95='0.387', vp='0.764', vr='0.700', tm50='0.652', tm95='0.327', tp='0.786', tr='0.580', pa50='0.824', pa95='0.385', da50='0.480', da95='0.268') + '\n相對 run04（test）：mAP50 -0.025（雜訊內）；mAP50-95 -0.019（雜訊內）；person AP50 -0.002（持平）；dynamic_obstacle AP50 -0.048（超出雜訊，變差）、recall 0.495 → 0.484。best 在第 32 epoch，共 52 epoch（約 15 分鐘，train 664 → 1,992 張）。\n\n解讀：固定 ±45° 離線擴增對 person 沒有影響，也沒有帶來好處；dynamic_obstacle 略差。\n\n限制：單一 seed；固定角度離線擴增與 run08 的內建隨機旋轉不同，兩者不能直接當同一件事比較。',
 'run11': TBL.format(vm50='0.729', vm95='0.402', vp='0.846', vr='0.624', tm50='0.666', tm95='0.331', tp='0.776', tr='0.654', pa50='0.736', pa95='0.360', da50='0.597', da95='0.303') + '\n相對 run04（test）：mAP50 -0.011（雜訊內）；mAP50-95 -0.015（雜訊內）；person AP50 -0.090（超出雜訊，變差）；dynamic_obstacle AP50 +0.069、recall 0.495 → 0.593。best 在第 47 epoch，共 67 epoch（約 36 分鐘，train 664 → 3,984 張）。\n\n解讀：大角度（±90°、±135°、180°，人倒立或橫躺）使 person 變差，同時提升 dynamic_obstacle，兩者互相抵銷，整體 mAP50 看不出差異。\n\n限制：單一 seed；person 與 dynamic_obstacle 的反向變化，原因（推測：車輛倒置較不影響外觀辨識，person 倒置則偏離實際情境）尚未驗證。',
 'run04s1': TBL.format(vm50='0.726', vm95='0.415', vp='0.843', vr='0.633', tm50='0.634', tm95='0.331', tp='0.771', tr='0.601', pa50='0.769', pa95='0.368', da50='0.498', da95='0.295') + '\n\nseed=1；best 在第 42 epoch，共 62 epoch。與 seed=0（test mAP50 0.677）相差 -0.043，與 seed=2 合併的平均與標準差見報告 v0.3 第 4.2 節。',
 'run04s2': TBL.format(vm50='0.712', vm95='0.390', vp='0.792', vr='0.642', tm50='0.702', tm95='0.377', tp='0.761', tr='0.614', pa50='0.825', pa95='0.421', da50='0.579', da95='0.333') + '\n\nseed=2；best 在第 25 epoch，共 45 epoch。與 seed=0（test mAP50 0.677）相差 +0.025。run04 三個 seed 的 test mAP50 為 0.677、0.634、0.702，平均 0.671、標準差 0.034（見報告 v0.3 第 4.2 節）。',
 'run09s1': TBL.format(vm50='0.736', vm95='0.404', vp='0.827', vr='0.674', tm50='0.686', tm95='0.383', tp='0.799', tr='0.624', pa50='0.754', pa95='0.385', da50='0.619', da95='0.381') + '\n\nseed=1；best 在第 55 epoch，共 75 epoch。與同 seed 的 run04 相比：mAP50 +0.052、mAP50-95 +0.052、person AP50 -0.015、dynamic_obstacle AP50 +0.121。',
 'run09s2': TBL.format(vm50='0.714', vm95='0.396', vp='0.823', vr='0.653', tm50='0.723', tm95='0.384', tp='0.765', tr='0.691', pa50='0.810', pa95='0.400', da50='0.636', da95='0.369') + '\n\nseed=2；best 在第 26 epoch，共 46 epoch。與同 seed 的 run04 相比：mAP50 +0.021、mAP50-95 +0.007、person AP50 -0.015、dynamic_obstacle AP50 +0.057。run09 三個 seed 的 test 平均見報告 v0.3 第 4.2 節。',
 'run12': TBL.format(vm50='0.794', vm95='0.429', vp='0.869', vr='0.698', tm50='0.648', tm95='0.341', tp='0.714', tr='0.601', pa50='0.733', pa95='0.354', da50='0.563', da95='0.327') + '\n\nseed=0；imgsz=416，其餘同 run09。best 在第 9 epoch，共 29 epoch（約 8 分鐘）。與同 seed 的 run09（320）相比：test mAP50 -0.042、mAP50-95 -0.037。三個 seed 的平均比較見報告 v0.3 第 4.3 節。',
 'run12s1': TBL.format(vm50='0.749', vm95='0.397', vp='0.869', vr='0.621', tm50='0.700', tm95='0.351', tp='0.709', tr='0.693', pa50='0.779', pa95='0.352', da50='0.622', da95='0.350') + '\n\nseed=1；imgsz=416。best 在第 26 epoch，共 46 epoch（約 13 分鐘）。與同 seed 的 run09 相比：test mAP50 +0.014、mAP50-95 -0.032。',
 'run12s2': TBL.format(vm50='0.688', vm95='0.388', vp='0.731', vr='0.654', tm50='0.713', tm95='0.364', tp='0.774', tr='0.680', pa50='0.796', pa95='0.367', da50='0.629', da95='0.361') + '\n\nseed=2；imgsz=416。best 在第 45 epoch，共 65 epoch（約 18 分鐘）。與同 seed 的 run09 相比：test mAP50 -0.010、mAP50-95 -0.020。\n\n三個 seed 的平均：imgsz=416 的 test mAP50 0.687 ± 0.034（run09 為 0.700 ± 0.020）、mAP50-95 0.352 ± 0.012（run09 為 0.382 ± 0.003）。結論：imgsz=416 沒有改善，mAP50-95 反而三個 seed 皆較低。',
 'run06': TBL.format(vm50='0.697', vm95='0.323', vp='0.724', vr='0.678', tm50='0.691', tm95='0.295', tp='0.743', tr='0.645', pa50='0.847', pa95='0.331', da50='0.535', da95='0.259') + '\n相對 run04（test）：mAP50 +0.014（在 ±0.03 內，不下結論）；mAP50-95 -0.051（超出雜訊）；person AP50 +0.021、dynamic_obstacle AP50 +0.007（皆在雜訊內）；dynamic_obstacle recall 0.495 → 0.549。best 在第 18 epoch，共 38 epoch。\n\n限制：單一 seed；mAP50-95 下降可能與旋轉後外接框變鬆有關（未驗證）；valid 只有 60 個 dynamic_obstacle，valid 指標變動大。',
 'run07': TBL.format(vm50='0.701', vm95='0.356', vp='0.763', vr='0.699', tm50='0.654', tm95='0.307', tp='0.691', tr='0.671', pa50='0.759', pa95='0.335', da50='0.550', da95='0.279') + '\n相對 run04（test）：mAP50 -0.023（在 ±0.03 內，不下結論）；mAP50-95 -0.039（超出雜訊）；person AP50 -0.067（超出雜訊，變差）；dynamic_obstacle AP50 +0.022（雜訊內），recall 0.495 → 0.626（本組最高）。best 在第 34 epoch，共 54 epoch。\n\n限制：單一 seed；person 下降與 dynamic_obstacle 上升互相抵銷，整體 mAP50 看不出差異。',
 'run08': TBL.format(vm50='0.745', vm95='0.329', vp='0.816', vr='0.660', tm50='0.657', tm95='0.269', tp='0.798', tr='0.629', pa50='0.809', pa95='0.297', da50='0.505', da95='0.242') + '\n相對 run04（test）：mAP50 -0.020（在 ±0.03 內，不下結論）；mAP50-95 -0.077（超出雜訊，四組最差）；person AP50 -0.017、dynamic_obstacle AP50 -0.023（皆在雜訊內）。valid mAP50 0.745 為四組最高，但 test 最低，顯示 valid 對選模型的參考性有限。best 在第 49 epoch，共 69 epoch。\n\n限制：單一 seed；旋轉 45° 後外接框明顯變鬆（面積比 0.2 變 0.28）。',
}

def build(fname, run, title_md, degrees=0, offline=False, note='', seed=0, imgsz=320, train_code=None, extra_eval=None):
    nb = nbf.v4.new_notebook(); C = nb.cells
    C.append(nbf.v4.new_markdown_cell(title_md))
    C.append(nbf.v4.new_code_cell(f"RUN = '{run}'"))
    C.append(nbf.v4.new_code_cell(SETUP))
    C.append(nbf.v4.new_code_cell(DRIVE.replace('{run}', run)))
    C.append(nbf.v4.new_code_cell(DOWNLOAD))
    C.append(nbf.v4.new_code_cell(MERGE))
    if offline: C.append(nbf.v4.new_code_cell(OFFLINE if offline is True else offline))
    C.append(nbf.v4.new_code_cell(train_code or TRAIN(run, degrees, note, seed, imgsz)))
    C.append(nbf.v4.new_code_cell(EVAL.replace('imgsz=320', f'imgsz={imgsz}')))
    if extra_eval: C.append(nbf.v4.new_code_cell(extra_eval))
    C.append(nbf.v4.new_markdown_cell('## 本版結論\n\n' + CONCL.get(run.split('_')[0], '（跑完後填寫：test 與 valid 指標、與 run04 的差異、是否超出單一 seed 的雜訊 ±0.03、限制）')))
    nbf.write(nb, os.path.join(OUT, fname))
    os.makedirs(os.path.join(OUT, 'local'), exist_ok=True)
    py = [f'# 本地版：由 {fname} 自動產生，內容相同。執行前：pip install ultralytics==8.4.183 roboflow；export ROBOFLOW_API_KEY=...\n']
    for c in C:
        if c.cell_type == 'code' and '!pip' not in c.source:
            py.append(c.source + '\n')
    open(os.path.join(OUT, 'local', fname.replace('.ipynb', '.py')), 'w').write('\n\n'.join(py))

HDR = '# {run}｜{desc}\n\n資料：Roboflow v3（`v3-raw-640-5class-20261103`，941 張；Train 664／Valid 135／Test 142），合併為 person／dynamic_obstacle。\n\n**變更（相對前一版）**\n\n{chg}\n\n共同設定：YOLOv8n、imgsz=320、epochs=100、patience=20、batch=32、seed=0、weight_decay=0.0005。僅 seed=0 一次，差距在約 0.03 內不下結論。'
specs = [
 ('run04', 'v3dynamic2class_baseline', 'v3 raw baseline（無擴增）', '- 資料由 v2 換成 v3（train +165、valid +45、test +67，同一天早上、同一人行道）。\n- 擴增全部關閉，作為 run05～run08 的對照。', 0, False, '本版不做任何擴增'),
 ('run05', 'v3dynamic2class_offline45grid', '離線擴增：45° 網格 × 左右翻轉', '- 只擴增 train：角度 {-180,-135,-90,-45,0,45,90,135}（-180 與 180 相同，已去重）× 左右翻轉 = 16 種組合（含原圖），664 → 10,624 張。\n- 本版為離線產生的固定資料集；內建擴增關閉。\n- 與 run06～08 的差異有兩個（大角度與翻轉），結果只能整體比較，無法分別歸因。', 0, True, '擴增已在上一步離線完成'),
 ('run06', 'v3dynamic2class_rot15', '內建旋轉 degrees=15', '- 僅 degrees=15（每張每次在 ±15° 均勻隨機）；不加翻轉。', 15, False, '只開 degrees=15'),
 ('run07', 'v3dynamic2class_rot30', '內建旋轉 degrees=30', '- 僅 degrees=30（每張每次在 ±30° 均勻隨機）；不加翻轉。', 30, False, '只開 degrees=30'),
 ('run08', 'v3dynamic2class_rot45', '內建旋轉 degrees=45', '- 僅 degrees=45（每張每次在 ±45° 均勻隨機）；不加翻轉。', 45, False, '只開 degrees=45'),
]
for rid, desc, title, chg, deg, off, note in specs:
    run = f'{rid}_{desc}'
    build(f'YOLOv8n_{rid}_{desc}_{DATE}.ipynb', run, HDR.format(run=f'YOLOv8n_{rid}', desc=title, chg=chg), deg, off, note)
print('done')

DATE2 = "20261104"
specs2 = [
 ('run09', 'v3dynamic2class_offlineflip', '離線擴增：只做左右翻轉（拆分 run05）', '- 只擴增 train：只做左右翻轉，不旋轉；664 → 1,328 張（含原圖）。\n- 目的：拆分 run05，單獨檢驗翻轉的效果；與 run04（無擴增）比較。',
  offline_code([0], (0, 1), "# 離線擴增（只作用在 train）：只做左右翻轉，不旋轉（角度 {0}）× 左右翻轉 {否,是}\n# = 2 種組合（含原圖）→ 664 × 2 = 1,328 張。valid / test 不動。"), '擴增已在上一步離線完成'),
 ('run10', 'v3dynamic2class_offlinesmallangle', '離線擴增：小角度 {-45,0,45}，不翻轉（拆分 run05）', '- 只擴增 train：角度 {-45,0,45}，不翻轉；664 → 1,992 張（含原圖）。\n- 目的：拆分 run05，單獨檢驗小角度（與 run08 的內建 ±45° 隨機旋轉不同，這裡是固定角度離線產生）；與 run04 比較。',
  offline_code([-45, 0, 45], (0,), "# 離線擴增（只作用在 train）：角度 {-45,0,45}，不翻轉\n# = 3 種組合（含原圖）→ 664 × 3 = 1,992 張。valid / test 不動。"), '擴增已在上一步離線完成'),
 ('run11', 'v3dynamic2class_offlinelargeangle', '離線擴增：大角度 {-180,-135,-90,0,90,135}，不翻轉（拆分 run05）', '- 只擴增 train：角度 {-180,-135,-90,0,90,135}，不翻轉；664 → 3,984 張（含原圖）。\n- 目的：拆分 run05，單獨檢驗大角度（人倒立或橫躺）；與 run04 比較。',
  offline_code([-180, -135, -90, 0, 90, 135], (0,), "# 離線擴增（只作用在 train）：角度 {-180,-135,-90,0,90,135}，不翻轉\n# = 6 種組合（含原圖）→ 664 × 6 = 3,984 張。valid / test 不動。"), '擴增已在上一步離線完成'),
]
for rid, desc, title, chg, off, note in specs2:
    run = f'{rid}_{desc}'
    build(f'YOLOv8n_{rid}_{desc}_{DATE2}.ipynb', run, HDR.format(run=f'YOLOv8n_{rid}', desc=title, chg=chg), 0, off, note)
print('done2')

specs3 = [
 ('run04s1', 'v3dynamic2class_baseline_seed1', 'run04 重跑（seed=1）', '- 與 run04 完全相同，僅 seed 由 0 改為 1，用於估計 seed 變異。', 0, False, '本版不做任何擴增', 1),
 ('run04s2', 'v3dynamic2class_baseline_seed2', 'run04 重跑（seed=2）', '- 與 run04 完全相同，僅 seed 由 0 改為 2，用於估計 seed 變異。', 0, False, '本版不做任何擴增', 2),
 ('run09s1', 'v3dynamic2class_offlineflip_seed1', 'run09 重跑（seed=1）', '- 與 run09（離線只做左右翻轉，train 1,328 張）完全相同，僅 seed 由 0 改為 1。', 0, offline_code([0], (0, 1), "# 離線擴增（只作用在 train）：只做左右翻轉，不旋轉（角度 {0}）× 左右翻轉 {否,是}\n# = 2 種組合（含原圖）→ 664 × 2 = 1,328 張。valid / test 不動。"), '擴增已在上一步離線完成', 1),
 ('run09s2', 'v3dynamic2class_offlineflip_seed2', 'run09 重跑（seed=2）', '- 與 run09（離線只做左右翻轉，train 1,328 張）完全相同，僅 seed 由 0 改為 2。', 0, offline_code([0], (0, 1), "# 離線擴增（只作用在 train）：只做左右翻轉，不旋轉（角度 {0}）× 左右翻轉 {否,是}\n# = 2 種組合（含原圖）→ 664 × 2 = 1,328 張。valid / test 不動。"), '擴增已在上一步離線完成', 2),
]
for rid, desc, title, chg, deg, off, note, sd in specs3:
    run = f'{rid}_{desc}'
    build(f'YOLOv8n_{rid}_{desc}_{DATE2}.ipynb', run, HDR.format(run=f'YOLOv8n_{rid}', desc=title, chg=chg).replace('僅 seed=0 一次，', f'本版 seed={sd}；與 seed=0 的 run 合併估計變異，'), deg, off, note, sd)
print('done3')

specs4 = [
 ('run12', 'v3dynamic2class_offlineflip_img416', 'run09 設定改 imgsz=416（seed=0）', 0),
 ('run12s1', 'v3dynamic2class_offlineflip_img416_seed1', 'run09 設定改 imgsz=416（seed=1）', 1),
 ('run12s2', 'v3dynamic2class_offlineflip_img416_seed2', 'run09 設定改 imgsz=416（seed=2）', 2),
]
for rid, desc, title, sd in specs4:
    run = f'{rid}_{desc}'
    chg = f'- 與 run09（離線只做左右翻轉，train 1,328 張）相同，僅訓練與評估的 imgsz 由 320 改為 416（seed={sd}）。\n- 目的：檢驗較大輸入尺寸是否改善小目標與 dynamic_obstacle 的 recall；同時記錄推論速度。與 run09 的 3 個 seed 比較。'
    hdr = HDR.format(run=f'YOLOv8n_{rid}', desc=title, chg=chg).replace('僅 seed=0 一次，', f'本版 seed={sd}；與其他 seed 合併估計變異，').replace('imgsz=320、', 'imgsz=416、')
    build(f'YOLOv8n_{rid}_{desc}_{DATE2}.ipynb', run, hdr, 0, offline_code([0], (0, 1), "# 離線擴增（只作用在 train）：只做左右翻轉，不旋轉（角度 {0}）× 左右翻轉 {否,是}\n# = 2 種組合（含原圖）→ 664 × 2 = 1,328 張。valid / test 不動。"), '擴增已在上一步離線完成', sd, 416)
print('done4')


# ---- run19~run21（低成本組，2026-11-07）：以 run09（離線只翻轉、imgsz=320）為基準，一次只改一個因素，各 3 個 seed ----
DATE3 = "20261107"
FLIP_ONLY = offline_code([0], (0, 1), "# 離線擴增（只作用在 train）：只做左右翻轉，不旋轉（角度 {0}）× 左右翻轉 {否,是}\n# = 2 種組合（含原圖）→ 664 × 2 = 1,328 張。valid / test 不動。")

def TRAIN_X(seed, note, aug, extra):
    # aug：擴增相關參數；extra：epochs / patience / 損失權重等其他參數
    return f"""# 訓練：以 run09 為基準（YOLOv8n、imgsz=320、batch=32、seed={seed}、weight_decay=0.0005），只改下面標示的因素
# {note}
from ultralytics import YOLO
LAST = f'{{OUT}}/runs/{{RUN}}/weights/last.pt'
resume = os.path.exists(LAST)  # 斷線或中斷後再執行這格，會從 last.pt 續跑
model = YOLO(LAST if resume else 'yolov8n.pt')
print('續跑' if resume else '從頭訓練')
model.train(**(dict(resume=True) if resume else dict(data=f'{{DST}}/data.yaml', imgsz=320, batch=32, seed={seed}, deterministic=True,
            weight_decay=0.0005, workers=WORKERS, device=DEVICE, project=f'{{OUT}}/runs', name=RUN, exist_ok=True, save_period=10,
            {extra},
            {aug})))"""

AUG_OFF = "hsv_h=0, hsv_s=0, hsv_v=0, degrees=0, translate=0, scale=0, shear=0, perspective=0, flipud=0, fliplr=0, mosaic=0, mixup=0, copy_paste=0, erasing=0"
BASE_X = "epochs=100, patience=20"

# 5.1：用 valid 的 F1-信心曲線選門檻（不在 test 上選，避免洩漏），再看 test 在該門檻的 P / R
CONF_PICK = """# 以 valid 的 F1-信心曲線選推論 conf 門檻；test 只在這個門檻上讀 P / R（不在 test 上調門檻）
import numpy as np
mv = best.val(data=f'{DST}/data.yaml', split='val', imgsz=320, plots=False, device=DEVICE, project=f'{OUT}/runs', name=f'{RUN}_valconf', exist_ok=True)
px = np.asarray(mv.box.px); f1m = np.asarray(mv.box.f1_curve).mean(0)
i = int(f1m.argmax()); print('valid 平均 F1 最大的 conf =', round(float(px[i]), 3), '｜F1 =', round(float(f1m[i]), 3))
for k, c in enumerate(mv.box.ap_class_index):
    fk = np.asarray(mv.box.f1_curve)[k]; j = int(fk.argmax())
    print(mv.names[int(c)], '各類最佳 conf', round(float(px[j]), 3), '｜valid F1', round(float(fk[j]), 3))
print('test 在 conf*：P', round(float(np.asarray(m.box.p_curve).mean(0)[i]), 3), 'R', round(float(np.asarray(m.box.r_curve).mean(0)[i]), 3),
      '｜對照 conf=0.25 與 0.15 的 test P / R：',
      [(t, round(float(np.asarray(m.box.p_curve).mean(0)[int(t * 999)]), 3), round(float(np.asarray(m.box.r_curve).mean(0)[int(t * 999)]), 3)) for t in (0.25, 0.15)])"""

HDR3 = '# {run}｜{desc}\n\n資料：Roboflow v3（941 張；Train 664／Valid 135／Test 142），合併為 person／dynamic_obstacle。\n\n**變更（相對 run09）**\n\n{chg}\n\n共同設定：YOLOv8n、imgsz=320、batch=32、weight_decay=0.0005、離線只做左右翻轉（train 1,328 張）。本版 seed={sd}；run09 三個 seed 的 test mAP50-95 為 0.378、0.383、0.384（平均 0.382），差距要超出 seed 變異才下結論。'

specs5 = [
 ('run19', 'v3dynamic2class_flip_hsv', 'run09 加 HSV 光度擴增（建議 1.2）',
  '- 只多開 HSV 光度擴增：hsv_h=0.015、hsv_s=0.5、hsv_v=0.3（比 Ultralytics 預設 0.7 / 0.4 保守）。幾何擴增仍全關。\n- 高斯模糊：ultralytics==8.4.183 沒有 `augmentations` 參數，只剩內建 Albumentations 的 p=0.01 模糊，等於沒開，因此本版不含模糊。',
  dict(note='只開 HSV 光度擴增，幾何擴增全關', aug=AUG_OFF.replace('hsv_h=0, hsv_s=0, hsv_v=0', 'hsv_h=0.015, hsv_s=0.5, hsv_v=0.3'), extra=BASE_X)),
 ('run20', 'v3dynamic2class_flip_box9dfl2', 'run09 調高 box 與 dfl 損失權重（建議 2.1）',
  '- 只改損失權重：box 7.5 → 9.0、dfl 1.5 → 2.0（建議範圍 box 8.5～10、dfl 2.0～2.5，取偏保守的中間值）。擴增仍全關。',
  dict(note='只改 box=9.0、dfl=2.0', aug=AUG_OFF, extra=BASE_X + ', box=9.0, dfl=2.0')),
 ('run21', 'v3dynamic2class_flip_mosaic_close25', 'run09 開 mosaic，最後 25 epoch 關閉（建議 1.3）',
  '- 開 mosaic=1.0，close_mosaic=25。epochs 固定 100，patience=100（等於關掉早停），否則 early stop 會在 30～75 epoch 就結束，永遠進不了關閉 mosaic 的階段。\n- 與 run09 的差異有兩個（mosaic 與早停），這是 close_mosaic 要生效的必要代價；比較時請同時看 best epoch，必要時再加一組「patience=100、mosaic=0」對照。',
  dict(note='mosaic=1.0、close_mosaic=25、epochs=100、patience=100（關閉早停）', aug=AUG_OFF.replace('mosaic=0', 'mosaic=1.0'), extra='epochs=100, patience=100, close_mosaic=25')),
 ('run22', 'v3dynamic2class_flip_nostop', 'run09 關掉早停、跑滿 100 epoch（run21 的控制組）',
  '- 只關掉早停：epochs=100、patience=100，mosaic 仍關閉（close_mosaic=0）。擴增與損失權重都維持 run09。\n- 用途：與 run21 的差異只剩 mosaic，可以分開「mosaic」與「訓練更久」各自的貢獻。',
  dict(note='mosaic 關閉、epochs=100、patience=100（關閉早停）', aug=AUG_OFF, extra='epochs=100, patience=100, close_mosaic=0')),
]
for rid, desc, title, chg, cfg in specs5:
    for suffix, sd in (('', 0), ('s1', 1), ('s2', 2)):
        run = f'{rid}{suffix}_{desc}' + (f'_seed{sd}' if sd else '')
        hdr = HDR3.format(run=f'YOLOv8n_{rid}{suffix}', desc=title, chg=chg, sd=sd)
        build(f'YOLOv8n_{rid}{suffix}_{desc}' + (f'_seed{sd}' if sd else '') + f'_{DATE3}.ipynb', run, hdr, 0, FLIP_ONLY, cfg['note'], sd, 320,
              train_code=TRAIN_X(sd, cfg['note'], cfg['aug'], cfg['extra']), extra_eval=CONF_PICK)
print('done5')
