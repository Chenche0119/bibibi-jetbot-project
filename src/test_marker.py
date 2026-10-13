"""marker.py 的離線測試。在筆電上直接跑,不需要 JetBot:

    python test_marker.py

用合成影像(灰底 + 兩個純色方塊)測邏輯,不是測門檻。
**真正的 HSV 門檻必須用實拍照校正**,見 marker_calibration/。

與 test_follow_state.py 分開的理由:那一支刻意不 import 任何第三方套件,
這一支需要 cv2 / numpy。
"""

import time

import numpy as np

import marker

PINK = (147, 20, 255)     # BGR,約 HSV H=164
GREEN = (0, 255, 0)       # BGR,HSV H=60(實際標記是螢光綠,實測 H 41~68)
LEAF = (40, 110, 70)      # BGR,HSV H 約 42、V 110:樹叢的顏色(色相同區,但暗)
FULL = [0.0, 0.0, 1.0, 1.0]


def blank():
    return np.full((480, 640, 3), 120, np.uint8)      # 中性灰背景,與相機同為 640x480


def with_marker(pink_rows=(330, 345), second=True, cols=(300, 312), pink=True):
    """在畫面下半部放一個「上桃紅 / 下螢光綠」的合成標記。

    每色 15x12 = 180 px:高於 min_pink_area(80)與 min_second_area(150),
    約等於實測 2.0m 處的大小。
    """
    img = blank()
    r0, r1 = pink_rows
    c0, c1 = cols
    if pink:
        img[r0:r1, c0:c1] = PINK
    if second:
        img[r1:r1 + (r1 - r0), c0:c1] = GREEN
    return img


def check(name, cond):
    print(('  PASS  ' if cond else '  FAIL  ') + name)
    if not cond:
        raise AssertionError(name)


def test_detect_either_colour():
    print('偵測:兩色任一成立就算(定案規則)')
    r = marker.detect(with_marker(), FULL, diagnostics=True)
    check('兩色都在 → 找到', r.found)
    check('桃紅面積正確(%d)' % r.pink_area, r.pink_area == 180)
    check('綠色面積正確(%d)' % r.second_area, r.second_area == 180)
    check('不要求雙色', not r.dual_required)
    check('bbox_px 優先畫桃紅,且是全畫面座標 %s' % (r.bbox_px,),
          r.bbox_px is not None and r.bbox_px[0] == 300 and r.bbox_px[1] == 330)

    r = marker.detect(with_marker(second=False), FULL)
    check('只有桃紅(綠色被陰影吃掉)→ 找到', r.found and r.second_area == 0)

    r = marker.detect(with_marker(pink=False), FULL)
    check('只有綠色(桃紅被陰影吃掉)→ 找到', r.found and r.pink_area == 0)
    check('只有綠色時框畫在綠色那塊 %s' % (r.bbox_px,),
          r.bbox_px is not None and r.bbox_px[1] == 345)


def test_detect_negatives():
    print('偵測:不該誤判的情況')
    check('空白畫面', not marker.detect(blank(), FULL).found)
    # 標記出現在上半身 = 不在腿的位置,應被搜尋範圍排除
    check('標記在上半身', not marker.detect(with_marker(pink_rows=(60, 75)), FULL).found)
    # 整條腿都是同一色 = 那個顏色的褲子
    pants = blank()
    pants[250:480, 170:470] = PINK
    check('大片桃紅(衣物)被 max_pink_ratio 擋下', not marker.detect(pants, FULL).found)
    pants = blank()
    pants[250:480, 170:470] = GREEN
    check('大片綠色(衣物)被 max_second_ratio 擋下', not marker.detect(pants, FULL).found)
    # 樹叢:色相跟螢光綠同一區,靠亮度(V>=150)擋掉
    bush = blank()
    bush[300:360, 100:200] = LEAF
    check('暗綠色樹叢不算標記', not marker.detect(bush, FULL).found)
    # 太小的綠點(遠處被陽光照到的葉子,實測最大 100 px)
    speck = blank()
    speck[300:310, 100:110] = GREEN
    check('100 px 的綠點低於 min_second_area', not marker.detect(speck, FULL).found)


def test_require_both_legacy():
    print('舊規則 require_both=True(保留給報告對照)')
    cfg = marker.MarkerConfig()
    cfg.require_both = True
    check('兩色都在 → 找到', marker.detect(with_marker(), FULL, cfg).found)
    check('只有桃紅 → 不算', not marker.detect(with_marker(second=False), FULL, cfg).found)
    check('只有綠色 → 不算', not marker.detect(with_marker(pink=False), FULL, cfg).found)


def test_verifier_grace():
    print('遮擋寬限:只在已鎖定時適用')
    sentinel = object()                      # 假裝是 LockedTarget

    v = marker.MarkerVerifier()
    check('看到標記回 1.0', v(with_marker(), FULL, None) == 1.0)
    check('已鎖定 + 短暫遮住 → 寬限期內仍回 1.0',
          v(blank(), FULL, sentinel) == 1.0)

    v2 = marker.MarkerVerifier()
    v2(with_marker(), FULL, None)
    check('未鎖定 + 沒看到標記 → 0.0(寬限不適用於鎖定關卡)',
          v2(blank(), FULL, None) == 0.0)

    v3 = marker.MarkerVerifier()
    v3.config.grace_seconds = 0.05
    v3(with_marker(), FULL, None)
    time.sleep(0.08)
    check('已鎖定 + 超過寬限 → 0.0', v3(blank(), FULL, sentinel) == 0.0)


def test_stability():
    print('穩定度統計:用假的時間軸,不用真的等')
    PERSON = [{'bbox': FULL}]
    on, off = with_marker(), blank()

    # 配戴者:10 FPS 跑 3 秒,中間 0.5 秒被腿擋住 → 最長空檔約 0.6 秒,沒超過寬限
    st = marker.MarkerStability(start=0.0)
    for i in range(30):
        t = 0.1 * (i + 1)
        st.record(t, off if 10 <= i < 15 else on, PERSON)
    check('幀數 30', st.frames == 30)
    check('認出 25 幀(%d)' % st.found_frames, st.found_frames == 25)
    check('兩色都成立 25 幀', st.both_frames == 25)
    check('最長空檔約 0.6 秒(%.2f)' % st.longest_gap, abs(st.longest_gap - 0.6) < 1e-6)
    check('沒有超過寬限', st.gaps_over_grace == 0)

    # 遮太久:中間 2 秒認不出 → 超過 1.5 秒寬限一次
    st = marker.MarkerStability(start=0.0)
    for i in range(40):
        st.record(0.1 * (i + 1), off if 10 <= i < 30 else on, PERSON)
    check('2 秒空檔被記成超過寬限', st.gaps_over_grace == 1 and st.longest_gap > 1.5)

    # 結尾一直認不出也要算:報告裡的「超過寬限」要包含最後那段
    st = marker.MarkerStability(start=0.0)
    st.record(0.1, on, PERSON)
    for i in range(20):
        st.record(0.2 + 0.1 * i, off, PERSON)
    check('結尾的空檔也算進報告', '超過寬限 1.5 秒的次數    1' in st.report())

    # 路人:有人、沒布條 → 一幀都不該認出
    st = marker.MarkerStability(start=0.0)
    for i in range(10):
        st.record(0.1 * (i + 1), off, PERSON)
    check('路人:偵測到人 10 幀、認出 0 幀',
          st.person_frames == 10 and st.found_frames == 0)

    # 避障接管的幀只計數,不影響認出率
    st.record_skipped(1.1)
    check('略過的幀另外計', st.skipped == 1 and st.frames == 10)


def test_speed():
    print('速度:必須遠小於一幀的預算')
    img = with_marker()
    t = time.time()
    for _ in range(500):
        marker.detect(img, FULL)
    per_ms = (time.time() - t) / 500 * 1000.0
    print('  每次 detect(): %.3f ms(筆電)' % per_ms)
    check('筆電上 < 2ms', per_ms < 2.0)


if __name__ == '__main__':
    tests = [
        test_detect_either_colour,
        test_detect_negatives,
        test_require_both_legacy,
        test_verifier_grace,
        test_stability,
        test_speed,
    ]
    for t in tests:
        t()
    print('\n全部通過(%d 組)' % len(tests))
