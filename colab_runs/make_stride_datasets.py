# 由 v3_noparked 建立「影格抽稀」的訓練資料：train 依檔名排序後，每 k 張只留 1 張（valid / test 完全不動）。
# 目的：檢驗提高影格取樣率是否有幫助（若只留一半影格也不掉，代表現有影格冗餘很高）。
# 排序：alert_*_am_NNNN 以（前綴、編號）；其餘（UUID 檔名）以 UUID 第一段（時間序）。
# 用法：python make_stride_datasets.py
import os, re, glob, shutil, collections
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, 'local', 'work')
SRC = f'{WORK}/v3_noparked'
def key(stem):
    m = re.match(r'(alert_[a-z]+_am)_(\d+)_jpg', stem)
    if m: return (0, m.group(1), int(m.group(2)))
    return (1, stem.split('-')[0], 0)
imgs = sorted(glob.glob(f'{SRC}/train/images/*'), key=lambda p: key(os.path.splitext(os.path.basename(p))[0]))
print('train 影像', len(imgs))
for k in (2, 4):
    DST = f'{WORK}/v3_noparked_s{k}'
    shutil.rmtree(DST, ignore_errors=True); shutil.copytree(SRC, DST)
    keep = {os.path.splitext(os.path.basename(p))[0] for p in imgs[::k]}
    for sub in ('images', 'labels'):
        for f in glob.glob(f'{DST}/train/{sub}/*'):
            if os.path.splitext(os.path.basename(f))[0] not in keep: os.remove(f)
    n = len(glob.glob(f'{DST}/train/images/*')); nl = len(glob.glob(f'{DST}/train/labels/*'))
    cnt = collections.Counter()
    for lf in glob.glob(f'{DST}/train/labels/*.txt'):
        for l in open(lf).read().strip().splitlines():
            if l.strip(): cnt['person' if l.split()[0] == '4' else 'dynamic_obstacle'] += 1
    print(f'stride {k}: train 影像 {n}、標註檔 {nl}、實例', dict(cnt))
