#!/usr/bin/env python3
"""由 run04 的本地腳本改出 run01~run03 的本地重跑版（v2 資料，seed=0，MPS/本地）。

與原 Colab 筆記本相同的設定：yolov8n.pt、imgsz=320、epochs=100、patience=20、batch=32、seed=0、
weight_decay=0.0005，只關 mosaic/hsv/fliplr/scale/translate/degrees（run03 為 degrees=15）。
注意：原筆記本沒有關 erasing / shear / perspective 以外的預設值，erasing 維持 Ultralytics 預設 0.4，
因此不傳 erasing=0（與 run04 之後的 run 不同）。用法：python3 make_local_reruns.py
"""
import os, re
HERE = os.path.dirname(os.path.abspath(__file__))
base = open(os.path.join(HERE, 'local', 'YOLOv8n_run04_v3dynamic2class_baseline_20261103.py'), encoding='utf-8').read()
base = base.replace("# 本地版：由 YOLOv8n_run04_v3dynamic2class_baseline_20261103.ipynb 自動產生，內容相同。", "# 本地重跑版（由 make_local_reruns.py 由 run04 腳本改出）。")
base = base.replace("RUN = 'run04_v3dynamic2class_baseline'", "RUN = '__RUN__'")
base = base.replace("v3_raw", "v2_raw").replace(".version(3)", ".version(2)").replace("v3 資料", "v2 資料")
TRAIN_OLD = """hsv_h=0, hsv_s=0, hsv_v=0, degrees=0, translate=0, scale=0, shear=0, perspective=0,
            flipud=0, fliplr=0, mosaic=0, mixup=0, copy_paste=0, erasing=0)))"""
assert TRAIN_OLD in base
TRAIN_NEW = "mosaic=0, hsv_h=0, hsv_s=0, hsv_v=0, fliplr=0, scale=0, translate=0, degrees=__DEG__)))"
FIVE = '''# 5 類保持原樣（不合併）；只把 polygon 轉成外接矩形，並寫出本地 data.yaml
import yaml, shutil, glob, collections
DST = f'{WORK}/data2_{RUN}'
names = yaml.safe_load(open(f'{SRC}/data.yaml'))['names']
if isinstance(names, dict): names = [names[k] for k in sorted(names)]
print('類別順序：', names)
shutil.rmtree(DST, ignore_errors=True)
cnt = collections.defaultdict(collections.Counter)
for sp in ['train', 'valid', 'test']:
    os.makedirs(f'{DST}/{sp}/images', exist_ok=True); os.makedirs(f'{DST}/{sp}/labels', exist_ok=True)
    for im in glob.glob(f'{SRC}/{sp}/images/*'):
        shutil.copy2(im, f'{DST}/{sp}/images/')
    for lf in glob.glob(f'{SRC}/{sp}/labels/*.txt'):
        out = []
        for ln in open(lf).read().strip().splitlines():
            p = ln.split(); c = int(p[0]); v = list(map(float, p[1:]))
            if len(v) > 4:  # polygon -> bbox
                xs, ys = v[0::2], v[1::2]
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                v = [(x0+x1)/2, (y0+y1)/2, x1-x0, y1-y0]
            out.append(' '.join([str(c)] + [f'{a:.6f}' for a in v])); cnt[sp][c] += 1
        open(f'{DST}/{sp}/labels/{os.path.basename(lf)}', 'w').write('\\n'.join(out) + ('\\n' if out else ''))
yaml.safe_dump({'path': DST, 'train': 'train/images', 'val': 'valid/images', 'test': 'test/images',
                'names': {i: n for i, n in enumerate(names)}}, open(f'{DST}/data.yaml', 'w'))
for sp in cnt: print(sp, '影像', len(os.listdir(f'{DST}/{sp}/images')), '各類實例數', dict(sorted(cnt[sp].items())))'''
i = base.index("# 5 類合併為 2 類"); j = base.index("# 訓練：")
runs = [('run01_v2raw5class_baseline', 0, True), ('run02_v2dynamic2class_baseline', 0, False), ('run03_v2dynamic2class_rot15', 15, False)]
for run, deg, five in runs:
    s = base
    if five:
        s = s[:i] + FIVE + "\n\n\n" + s[j:]
    s = s.replace(TRAIN_OLD, TRAIN_NEW.replace('__DEG__', str(deg)))
    s = s.replace('__RUN__', run)
    out = os.path.join(HERE, 'local', f'YOLOv8n_{run}_rerun_20261107.py')
    open(out, 'w', encoding='utf-8').write(s)
    print(out)
