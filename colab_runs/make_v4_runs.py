"""由 run24 的本地腳本產生 run26（對照）與 run27（加入 gate 影片 1），各 3 個 seed。

run26：v3_noparked + run24 的設定（翻轉 + mosaic + scale），不加 gate 資料 → 對照組
run27：同上，train 加入 gate 影片 1（241 張）→ 實驗組
兩組都在同一份 gate_test（影片 2，184 張）上評估一次；valid 仍用 v3 的 valid（不含 gate），
所以選 epoch 時完全看不到 gate_test。需先執行 make_noparked_dataset.py 與 make_gate_split.py。
"""
import os, re
HERE = os.path.dirname(os.path.abspath(__file__)) + '/local'
BASE = open(f'{HERE}/YOLOv8n_run24_v3dynamic2class_flip_mosaic_scale_20261107.py', encoding='utf-8').read()
DATE = '20261116'

LOCAL_SRC = '''# 使用本地衍生資料 v3_noparked（新規則：不標停放自行車），不連 Roboflow
SRC = f'{WORK}/v3_noparked'
assert os.path.exists(f'{SRC}/data.yaml'), '請先執行 colab_runs/make_noparked_dataset.py'
print('v3_noparked 資料：', SRC)
'''

GATE = '''# gate 新資料（2026/11/12）：影片 1 -> train（僅 USE_GATE 時），影片 2 -> gate_test（兩組都建，只評估用）
# 防污染：gate_test 放在 DST/gate_test，不在 train/valid/test 內，所以離線翻轉與訓練都碰不到它。
USE_GATE = {USE_GATE}
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
'''

GATE_EVAL = '''

# gate_test 評估：只在訓練結束後做一次，不用來選 epoch、conf 門檻或 seed（選模型看 valid）
for _conf in (0.001, 0.25):
    mg = best.val(data=f'{DST}/gate_test.yaml', split='val', imgsz=320, conf=_conf, plots=False, device=DEVICE,
                  project=f'{OUT}/runs', name=f'{RUN}_gatetest_c{_conf}', exist_ok=True)
    print(f'gate_test conf={_conf}: mAP50', round(mg.box.map50, 3), 'mAP50-95', round(mg.box.map, 3), 'P', round(mg.box.mp, 3), 'R', round(mg.box.mr, 3))
    for i, c in enumerate(mg.box.ap_class_index):
        print('  ', mg.names[int(c)], 'AP50', round(float(mg.box.ap50[i]), 3), 'P', round(float(mg.box.p[i]), 3), 'R', round(float(mg.box.r[i]), 3), '實例數', int(mg.nt_per_class[int(c)]))
'''

def make(rid, desc, use_gate, seed):
    t = BASE
    # 標頭與 RUN
    run = f'{rid}_{desc}'
    t = re.sub(r'^# 本地版.*$', f'# 本地版：由 colab_runs/make_v4_runs.py 產生（以 run24 為底）。執行前：pip install ultralytics==8.4.183；需先執行 make_noparked_dataset.py 與 make_gate_split.py', t, count=1, flags=re.M)
    assert "RUN = 'run24_v3dynamic2class_flip_mosaic_scale'" in t
    t = t.replace("RUN = 'run24_v3dynamic2class_flip_mosaic_scale'", f"RUN = '{run}'")
    # 資料來源：換掉 Roboflow 下載整格
    a = t.index('# 下載 Roboflow v3'); b = t.index('# 5 類合併為 2 類')
    t = t[:a] + LOCAL_SRC + '\n\n' + t[b:]
    # 在離線擴增之前插入 gate 資料
    k = t.index('# 離線擴增（只作用在 train）')
    t = t[:k] + GATE.replace('{USE_GATE}', str(use_gate)) + '\n\n' + t[k:]
    t = t.replace('= 2 種組合（含原圖）→ 664 × 2 = 1,328 張。', '= 2 種組合（含原圖）；train 張數見下方輸出。')
    # seed
    assert 'seed=0' in t
    t = t.replace('seed=0', f'seed={seed}')
    # 結尾 gate_test 評估
    t = t.rstrip('\n') + GATE_EVAL
    # 防呆：確認金鑰沒有留在檔案裡
    assert 'ROBOFLOW' not in t.replace('Roboflow', ''), 'ROBOFLOW 字樣殘留'
    out = f'{HERE}/YOLOv8n_{run}_{DATE}.py'
    open(out, 'w', encoding='utf-8').write(t); print(out)

for sd in (0, 1, 2):
    suf = '' if sd == 0 else f's{sd}'
    make(f'run26{suf}', 'v3noparked_flip_mosaic_scale_control' + ('' if sd == 0 else f'_seed{sd}'), False, sd)
    make(f'run27{suf}', 'v4gate_flip_mosaic_scale' + ('' if sd == 0 else f'_seed{sd}'), True, sd)
