"""標記顏色校正 —— 在筆電上跑,不需要 JetBot。

這支腳本取代了「訓練」這個步驟。HSV 門檻只有六個數字,但那六個數字必須
量出來,不能猜:同一張色紙在日光燈下、在窗邊、在走廊陰影裡,HSV 讀數會差很多。

兩個模式,對應兩件不同的事:

  --pick   量顏色。用**近距離特寫照**(約 0.3m),貼紙在畫面上夠大才框得準。
           框出桃紅區和第二色區,腳本統計實際的 HSV 分佈,印出建議門檻。

  --check  驗證距離。用 0.5 / 0.8 / 1.5 / 2.0m 的實拍照,套用目前 marker.py
           的門檻,報告每張圖抓到幾個像素、雙色在哪個距離開始失效。

用法(Windows PowerShell):
    python analyze_marker.py samples --pick
    python analyze_marker.py samples --check --save-masks

檔名規則:把距離寫進檔名就好,例如 `close_01.png`、`1.5m_03.png`。
腳本用正規式找 `數字m`,找不到也不會壞,只是報表少一欄。
"""

import argparse
import os
import re
import sys

import cv2
import numpy as np

# marker.py 的位置依 repo 結構而不同,兩個都找:
#   本專案 repo    object_following/marker.py        (= 上一層)
#   組員的 repo    src/marker.py                     (= 上一層的 src/)
_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(_PARENT, 'src'))
sys.path.insert(0, _PARENT)
import marker  # noqa: E402


IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.bmp')

# 小圖放大再讓人框,但放大後不能超出筆電螢幕,否則 selectROI 視窗會被切掉、
# 框不到下半部。舊版寫死 6 倍是給 224x168 用的;640x480 放 6 倍是 3840x2880。
# 改成「放到剛好塞進這個大小」,640x480 約 1.8 倍。
# 用 INTER_NEAREST:放大時不要內插,否則會混出原本不存在的顏色。
MAX_DISPLAY_W, MAX_DISPLAY_H = 1400, 850


def display_scale(image):
    h, w = image.shape[:2]
    return max(1.0, min(MAX_DISPLAY_W / float(w), MAX_DISPLAY_H / float(h)))


def list_images(folder):
    if not os.path.isdir(folder):
        sys.exit('找不到資料夾:%s' % folder)
    names = [n for n in sorted(os.listdir(folder))
             if n.lower().endswith(IMAGE_EXTS)]
    if not names:
        sys.exit('%s 裡面沒有圖片。先用 capture_samples.ipynb 拍幾張。' % folder)
    return [os.path.join(folder, n) for n in names]


def distance_from_name(path):
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*m', os.path.basename(path), re.I)
    return float(m.group(1)) if m else None


# --- 模式一:量顏色 --------------------------------------------------------

def pick_roi(image, title):
    """放大顯示讓人框一塊區域,回傳原始解析度下的 HSV 像素陣列。"""
    scale = display_scale(image)
    big = cv2.resize(image, None, fx=scale, fy=scale,
                     interpolation=cv2.INTER_NEAREST)
    box = cv2.selectROI(title, big, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)
    x, y, w, h = [int(round(v / scale)) for v in box]
    if w <= 0 or h <= 0:
        return None
    roi = image[y:y + h, x:x + w]
    if roi.size == 0:
        return None
    return cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3)


def hue_ranges(hues):
    """回傳 [(lo, hi), ...]。色相是環狀的,分佈跨過 0/179 接縫時要拆成兩段。"""
    lo, hi = np.percentile(hues, [2, 98])
    if hi - lo <= 90:
        return [(int(max(0, lo - 3)), int(min(179, hi + 3)))]
    # 跨接縫:整體平移 90 度讓分佈連續,算完再移回來
    shifted = (hues.astype(np.int32) + 90) % 180
    slo, shi = np.percentile(shifted, [2, 98])
    lo2 = int((slo - 90 - 3) % 180)
    hi2 = int((shi - 90 + 3) % 180)
    return [(lo2, 179), (0, hi2)]


def summarise(name, pixels):
    """印出統計並回傳可直接貼進 marker.py 的門檻字串。"""
    h, s, v = pixels[:, 0], pixels[:, 1], pixels[:, 2]
    clipped = np.count_nonzero((v >= 250) & (s < 60)) / float(len(pixels))

    print('')
    print('--- %s(%d 個像素)---' % (name, len(pixels)))
    print('  H  中位數 %3d   2%%~98%% %3d ~ %3d' % (
        np.median(h), np.percentile(h, 2), np.percentile(h, 98)))
    print('  S  中位數 %3d   5%%      %3d' % (np.median(s), np.percentile(s, 5)))
    print('  V  中位數 %3d   5%%      %3d' % (np.median(v), np.percentile(v, 5)))
    print('  過曝像素(V>=250 且 S<60):%.1f%%' % (clipped * 100))
    if clipped > 0.05:
        print('  >> 過曝偏高。這就是亮面材質的鏡面高光,那些像素的色相已經不可信。')
        print('     對策:改用霧面材質,或把相機的 exposure / gain 調低。')

    s_lo = int(max(0, np.percentile(s, 5) - 25))
    v_lo = int(max(0, np.percentile(v, 5) - 25))
    parts = ['((%d, %d, %d), (%d, %d, %d))' % (lo, s_lo, v_lo, hi, 255, 255)
             for lo, hi in hue_ranges(h)]
    return '[' + ', '.join(parts) + ']'


def run_pick(paths):
    print('')
    print('操作:滑鼠拖曳框出色塊 → 按 Enter 或空白鍵確認 → 按 c 跳過這一塊')
    print('建議用近距離特寫照來量顏色,框得準數字才準。')
    print('框「緊」一點,不要框到旁邊的褲子或地板。')

    pink_px, second_px = [], []
    for path in paths:
        image = cv2.imread(path)
        if image is None:
            print('讀不到 %s,跳過' % path)
            continue
        print('')
        print('=== %s (%dx%d) ===' % (os.path.basename(path),
                                      image.shape[1], image.shape[0]))
        got = pick_roi(image, '1/2 framing PINK - %s' % os.path.basename(path))
        if got is not None:
            pink_px.append(got)
        got = pick_roi(image, '2/2 framing SECOND COLOUR - %s' % os.path.basename(path))
        if got is not None:
            second_px.append(got)

    if not pink_px:
        sys.exit('沒有框到任何桃紅像素,無法統計。')

    print('')
    print('=' * 68)
    print('統計結果')
    print('=' * 68)
    pink_line = summarise('桃紅(主鑑別色)', np.vstack(pink_px))
    second_line = None
    if second_px:
        second_line = summarise('第二色(確認色)', np.vstack(second_px))

    print('')
    print('=' * 68)
    print('把下面兩行貼進 marker.py 的 MarkerConfig,取代原本的估計值:')
    print('=' * 68)
    print('    pink_ranges = %s' % pink_line)
    if second_line:
        print('    second_ranges = %s' % second_line)
    print('')
    print('貼完再跑一次 --check 確認實拍照都抓得到。')


# --- 模式二:驗證距離 ------------------------------------------------------

def _check_config():
    """校正照沒有人框,bbox 只能給整張畫面。

    這時「人框下半部」就變成「整張畫面下半部」,意義完全不同:JetBot 鏡頭貼近
    地面,人站在畫面上半部,小腿標記約在畫面高度 30~50% —— 只搜下半部會全部
    判成 FAIL。所以 --check 一律搜整張畫面,只驗證顏色門檻與面積;
    「只搜人框下半部」的效果要上車用真的人框才量得到。
    """
    cfg = marker.MarkerConfig()
    cfg.search_bottom_fraction = 1.0
    return cfg


def run_check(paths, save_masks):
    cfg = _check_config()
    out_dir = os.path.join(os.path.dirname(paths[0]), 'check_out')
    if save_masks and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    print('')
    print('套用 marker.py 目前的門檻,搜尋整張畫面(校正照沒有人框)。')
    print('判定規則:%s' % ('桃紅與綠色都要有(require_both=True)' if cfg.require_both
                           else '桃紅或綠色任一成立(require_both=False)'))
    print('桃紅/綠色欄位是「單獨成立」的色塊面積,0 代表該色單獨不成立。')
    print('')
    print('%-24s %7s %7s %7s %7s' % ('檔名', '桃紅px', '綠色px', '判定', '過曝%'))
    print('-' * 60)

    rows = []
    for path in paths:
        image = cv2.imread(path)
        if image is None:
            continue
        r = marker.detect(image, [0.0, 0.0, 1.0, 1.0], cfg, diagnostics=True)
        name = os.path.basename(path)
        # bg_ 開頭的是沒有標記的背景照,找到東西才是錯
        is_bg = name.lower().startswith('bg')
        ok = (not r.found) if is_bg else r.found
        verdict = ('OK' if ok else 'FAIL') + ('(bg)' if is_bg else '')
        print('%-24s %7d %7d %7s %6.1f%%' % (
            name[:24], r.pink_area, r.second_area, verdict, r.clipped_ratio * 100))
        rows.append((distance_from_name(path), is_bg, ok, r.pink_area > 0, r.second_area > 0))

        if save_masks:
            vis = image.copy()
            marker.draw_debug(vis, r)
            scale = display_scale(vis)
            big = cv2.resize(vis, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(os.path.join(out_dir, name), big)

    print('')
    marked = [r for r in rows if not r[1]]
    bgs = [r for r in rows if r[1]]
    if marked:
        print('有標記的照片:%d 張,判定成功 %d 張' % (
            len(marked), sum(1 for r in marked if r[2])))
        print('  只靠桃紅單獨就成立:%d 張   只靠綠色單獨就成立:%d 張' % (
            sum(1 for r in marked if r[3]), sum(1 for r in marked if r[4])))
        print('  兩色都成立:%d 張   只有一色成立(另一色靠不住):%d 張' % (
            sum(1 for r in marked if r[3] and r[4]),
            sum(1 for r in marked if r[2] and not (r[3] and r[4]))))
    if bgs:
        print('背景照(不該找到):%d 張,誤判 %d 張' % (
            len(bgs), sum(1 for r in bgs if not r[2])))
    else:
        print('沒有背景照(檔名 bg_ 開頭)。誤判率要另外拍無標記的照片才量得到。')

    known = [r for r in marked if r[0] is not None]
    if known:
        ok_d = [r[0] for r in known if r[2]]
        print('成功的最遠距離:%s' % ('%.2f m' % max(ok_d) if ok_d else '全部失敗'))
        bad = sorted(set(r[0] for r in known if not r[2]))
        if bad:
            print('有失敗的距離:%s' % ', '.join('%.1fm' % d for d in bad))

    if save_masks:
        print('')
        print('遮罩疊圖存到:%s' % out_dir)


def main():
    ap = argparse.ArgumentParser(description='螢光標記 HSV 門檻校正')
    ap.add_argument('folder', help='放實拍照片的資料夾')
    ap.add_argument('--pick', action='store_true', help='互動框選,量出 HSV 門檻')
    ap.add_argument('--check', action='store_true', help='套用目前門檻,驗證各距離')
    ap.add_argument('--save-masks', action='store_true', help='--check 時存疊圖')
    args = ap.parse_args()

    if not args.pick and not args.check:
        ap.error('至少要指定 --pick 或 --check')

    paths = list_images(args.folder)
    print('找到 %d 張圖片。' % len(paths))
    if args.pick:
        run_pick(paths)
    if args.check:
        run_check(paths, args.save_masks)


if __name__ == '__main__':
    main()
