# 由 bike_review/bicycle_review.csv 中 your_status == 'parked' 的列，從 v3_raw 建立 v3_noparked（不動 v3_raw、不動 Roboflow）
# 用法：python make_noparked_dataset.py（在 colab_runs/local 資料夾外也可執行；路徑以本檔所在位置為準）
import csv, os, shutil, collections
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, 'local', 'work')
SRC, DST = f'{WORK}/v3_raw', f'{WORK}/v3_noparked'
rows = list(csv.DictReader(open(f'{HERE}/bike_review/bicycle_review.csv', encoding='utf-8-sig')))
assert len(rows) == 137 and all(r['your_status'].strip() in ('parked', 'moving') for r in rows), 'CSV 不完整'
drop = collections.defaultdict(set)
for r in rows:
    if r['your_status'].strip() == 'parked': drop[(r['split'], r['image'])].add(int(r['label_line']))
shutil.rmtree(DST, ignore_errors=True); shutil.copytree(SRC, DST)
removed = collections.Counter(); left = collections.defaultdict(collections.Counter)
for sp in ('train', 'valid', 'test'):
    import glob
    for lf in sorted(glob.glob(f'{DST}/{sp}/labels/*.txt')):
        base = os.path.splitext(os.path.basename(lf))[0]
        ls = [l for l in open(lf).read().strip().splitlines() if l.strip()]
        keep = []
        for i, l in enumerate(ls):
            if i in drop.get((sp, base), ()):
                assert l.split()[0] == '0', (lf, i, l)  # 只允許移除 bicycle
                removed[sp] += 1
            else:
                keep.append(l); left[sp][l.split()[0]] += 1
        open(lf, 'w').write('\n'.join(keep) + ('\n' if keep else ''))
assert sum(removed.values()) == sum(len(v) for v in drop.values()), '有標註沒找到'
print('移除', dict(removed))
names = ['bicycle', 'bus', 'car', 'motorcycle', 'person']
for sp in ('train', 'valid', 'test'): print(sp, {names[int(k)]: v for k, v in sorted(left[sp].items())})
