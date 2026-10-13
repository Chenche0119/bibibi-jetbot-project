"""目標身分標記偵測(v1:螢光雙色布條 + HSV 色域門檻)。

取代原本規劃的 re-ID 外觀比對。做法是在被跟隨者**一條腿的腳踝包一圈**
「上桃紅 / 下螢光綠」的雙色布條,用 HSV 門檻找出來。

**兩色任一被認出就算數**(2026-10-06 定案,require_both = False)。
用兩個顏色是為了互相備援,不是為了雙重確認:一個顏色在某種光線下太淡、
跟背景太像、或被陰影吃掉時,另一個顏色還在。實測例子:0.5m_03 那張的桃紅
落在褲管陰影裡只剩 705 px,綠色仍有 1881 px。

為什麼不讓 YOLO 多學一個 marker 類別(2026-10-06 定案):
  1. 這件事只要判斷「人框下半部有沒有這兩個特定顏色、而且相鄰」,是顏色查表
     問題,不是辨識問題。HSV 門檻零訓練資料;YOLO 要多標 900~1300 張。
  2. 標記太小,YOLO 看不到:YOLO 預計以 imgsz 320 推論,小腿貼紙在 1.5m 處
     只剩約 4x7 px。HSV 直接跑在 640x480 原始畫面上,同距離有 8x14 px。
  3. 身分驗證與人物偵測分開,YOLO 換版本(v8 ↔ v4-tiny)不影響這裡。
  YOLO 只負責框出 person,身分由這支檔案在人框內驗證。

只貼一條腿的代價:
  走路時標記會被另一條腿週期性遮住(步頻約 1Hz,每次約 0.3~0.5 秒)。
  標記一消失 identity_score 就回 0,所以靠 grace_seconds(1.5 秒)撐過遮擋;
  只在**已鎖定**後適用,鎖定那一關仍必須真的看到標記。
  包住腳踝一圈(而不是只貼外側)讓正面、背面、側面都看得到,補回一部分。

這支檔案只 import cv2 / numpy,**不碰 jetbot / torch**,所以可以在筆電上直接
拿照片測試(見 marker_calibration/)。

接法(notebook 三行):
    import marker
    sm.identity_score = marker.MarkerVerifier()
    config.identity_threshold = 0.5
"""

import time
from collections import namedtuple

import cv2
import numpy as np


class MarkerConfig(object):
    """所有可調參數集中在這裡。跑過 marker_calibration/analyze_marker.py 之後,
    用它印出來的建議值取代下面的顏色門檻。
    """

    # --- 顏色門檻 ---------------------------------------------------------
    # OpenCV 的 HSV 是 H 0~179、S 0~255、V 0~255,
    # 不是一般繪圖軟體的 H 0~360、S 0~100、V 0~100。抄色票時要先換算。
    #
    # 2026-10-06 實測校正(samples_20261006,14 張 0.3m 特寫,2 位配戴者,
    # 室內木地板/磨石子 + 戶外紅磚,共約 10 萬個桃紅像素、8.6 萬個綠色像素)。
    # 先用 analyze_marker.py 的公式(H 取 2%~98% 再各放寬 3;S、V 取第 5 百分位
    # 再減 25)算出起點,再依個別照片手動放寬兩處,理由寫在各行。
    #
    # 寫成 list 是為了處理色相環繞:桃紅若偏紅而跨過 H=179/0 的接縫,
    # 就要拆成兩段,例如
    #   [((170, 150, 90), (179, 255, 255)), ((0, 150, 90), (6, 255, 255))]

    # 桃紅粉 —— 主鑑別色。實測 H 161~171(中位數 166),S 中位數 153。
    # 公式值 S>=92、V>=145;放寬成 S>=85(戶外紅磚那兩張的 S 第 5 百分位只有
    # 88~91)、V>=110(室內較暗那張 V 第 5 百分位 131,遠距離與陰影只會更暗)。
    # 紅磚地板的色相在 H 0~15,與 158~174 不重疊,放寬 S 不會把地板抓進來。
    pink_ranges = [((158, 85, 110), (174, 255, 255))]

    # 螢光綠(實際買到的是綠,不是原本規劃的黃)。實測 H 41~68,
    # **室內偏 59~67、戶外偏 42~52**,所以範圍要寬。S 只有 84~160,公式值 S>=61。
    #
    # ⚠️ V 下限刻意拉高到 150(公式值 128)。綠色現在可以**單獨**成立,
    #    不再只在桃紅旁邊找,而戶外的樹叢、盆栽色相正好落在同一區(H 38~48)。
    #    能把兩者分開的是亮度:螢光布 V 中位數 214,樹葉 V 中位數 119、
    #    98% 在 148 以下。V>=150 保留 91% 的標記綠,背景綠只剩約 2%。
    #    被犧牲掉的是陰影裡的那段綠 —— 那時候靠桃紅,這正是兩色互補的用意。
    second_ranges = [((38, 61, 150), (71, 255, 255))]

    # --- 搜尋範圍 ---------------------------------------------------------
    # 只找人框的下半部。標記貼在小腿,限制範圍能擋掉「粉紅色上衣」這類誤判。
    # 近距離(0.45m)時畫面只涵蓋地板到腰,人框會被上緣裁掉,但下半部仍是腿。
    search_bottom_fraction = 0.55

    # --- 面積門檻 ---------------------------------------------------------
    # ⚠️ 這些數字綁定畫面解析度。detect() 吃的是 live_demo 的**原始相機畫面**,
    #    目前是 640x480(與 capture_samples.ipynb 一致)。改相機解析度就要重新校正。
    #
    # 推導:640x480 與感測器 816x616 同為 4:3,橫豎每像素尺度相同,
    # 1.5m 處 1px 約 1.18cm。10x16cm 的標記(每半 8cm):
    #   1.5m 處約 8x14 px,桃紅那半約 8x7  = 56 px
    #   0.8m(跟隨距離)處約 16x25 px,桃紅那半約 16x13 = 200 px
    #
    # 2026-10-06 實測(58 張)推翻了上面的推估:實際的標記是**包住腳踝一圈**的
    # 布條,比 10x16cm 大,2.0m 處桃紅最少仍有 209 px(1.5m 最少 228 px)。
    # 另一邊,把標記附近遮掉後,畫面其他地方最大的桃紅雜訊是 24 px。
    # 門檻取 80:比真標記最小值低 2.6 倍、比雜訊最大值高 3 倍,兩邊都有餘裕。
    # (舊值 28 只比雜訊多 4 px,戶外光線一變就可能誤判)
    min_pink_area = 80
    # 綠色單獨成立時的門檻。實測真標記綠最少 187 px(2.0m),背景綠最大 100 px
    # (0.5m_03 遠處被陽光照到的樹葉,在人的外面)。取 150。
    # ⚠️ 間距只有 1.25~1.5 倍,比桃紅(209 vs 24)窄得多 —— 綠色是兩色中
    #    比較容易誤判的那個。上車後實際只搜人框下半部,背景樹叢大多會被排除,
    #    但樹叢正好在腿旁邊時仍可能進到搜尋範圍。
    min_second_area = 150
    # 反向防呆:某個顏色佔搜尋區域超過這個比例,那不是標記,是一件同色的褲子。
    max_pink_ratio = 0.35
    max_second_ratio = 0.35

    # --- 判定規則 ---------------------------------------------------------
    # False(定案):桃紅「或」綠色任一成立就算找到標記。
    # True:舊規則,必須先找到桃紅,再在它旁邊找到綠色。保留給報告做對照。
    require_both = False

    # 以下只在 require_both = True 時使用。
    # 在桃紅色塊的上下各延伸這個倍率(以色塊高度為單位)去找第二個顏色。
    neighbourhood_scale = 1.5
    # 第二色面積要達到桃紅面積的這個比例才算數。
    second_area_ratio = 0.25
    # 距離自適應:框高低於此值時退化成只認桃紅。0.0 = 全程要求雙色。
    dual_color_min_bbox_h = 0.0

    # --- 遮擋寬限 ---------------------------------------------------------
    # 轉身、兩腿交錯、褲管蓋住造成的短暫消失,不該立刻判定跟丟。
    grace_seconds = 1.5


MarkerResult = namedtuple(
    'MarkerResult',
    ['found', 'pink_area', 'second_area', 'dual_required', 'bbox_px', 'clipped_ratio'])

EMPTY_RESULT = MarkerResult(False, 0, 0, False, None, 0.0)


# --- 內部工具 --------------------------------------------------------------

def _to_pixel_box(bbox, w, h):
    """正規化 bbox [x1,y1,x2,y2](0~1)轉成像素座標,並保證至少 1x1。"""
    x1 = int(round(max(0.0, min(1.0, bbox[0])) * w))
    y1 = int(round(max(0.0, min(1.0, bbox[1])) * h))
    x2 = int(round(max(0.0, min(1.0, bbox[2])) * w))
    y2 = int(round(max(0.0, min(1.0, bbox[3])) * h))
    if x2 <= x1:
        x1, x2 = max(0, x1 - 1), min(w, x1 + 1)
    if y2 <= y1:
        y1, y2 = max(0, y1 - 1), min(h, y1 + 1)
    return x1, y1, x2, y2


def _build_mask(hsv, ranges):
    """多段色域取聯集,用來處理色相環繞。"""
    mask = None
    for lo, hi in ranges:
        part = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        mask = part if mask is None else cv2.bitwise_or(mask, part)
    return mask


def _largest_blob(mask, min_area):
    """回傳 (面積, x, y, w, h),找不到夠大的回 None。

    刻意**不做形態學開運算**:3x3 的 kernel 會把 4px 寬的標記整個侵蝕掉。
    在這個解析度下,雜訊要靠面積門檻濾,不能靠形態學。

    用 connectedComponentsWithStats 而不是 contourArea:後者算的是多邊形面積,
    對 4x3 這種極小色塊會嚴重低估。

    效能:connectedComponentsWithStats 是整支 detect() 最貴的一步(筆電上每次
    約 1ms,佔一半以上),而它的成本跟「掃過的面積」成正比。兩個捷徑,
    都不改變結果:
      1. 該色像素總數不到門檻 → 不可能有夠大的色塊,直接跳過
      2. 只在「所有該色像素的外接框」裡做連通分析 —— 標記通常是畫面上
         唯一的那塊顏色,外接框就是標記本身,比整個搜尋區域小一兩個數量級
    """
    if cv2.countNonZero(mask) < min_area:
        return None
    ox, oy, ow, oh = cv2.boundingRect(mask)      # 非零像素的外接框
    sub = mask[oy:oy + oh, ox:ox + ow]

    num, _labels, stats, _cent = cv2.connectedComponentsWithStats(sub, connectivity=8)
    best_i, best_area = -1, 0
    for i in range(1, num):          # label 0 是背景
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area > best_area:
            best_i, best_area = i, area
    if best_i < 0 or best_area < min_area:
        return None
    return (best_area,
            ox + int(stats[best_i, cv2.CC_STAT_LEFT]),
            oy + int(stats[best_i, cv2.CC_STAT_TOP]),
            int(stats[best_i, cv2.CC_STAT_WIDTH]),
            int(stats[best_i, cv2.CC_STAT_HEIGHT]))


def _colour_blob(hsv, ranges, min_area, max_ratio, roi_pixels):
    """找某個顏色最大的色塊,回傳 (面積, x, y, w, h);不成立回 None。

    不成立的兩種情況:太小(雜訊),或太大(佔搜尋區域超過 max_ratio,
    那是一件同色的衣物,不是標記)。
    """
    blob = _largest_blob(_build_mask(hsv, ranges), min_area)
    if blob is None:
        return None
    if roi_pixels and float(blob[0]) / roi_pixels > max_ratio:
        return None
    return blob


def _clipped_ratio(hsv):
    """過曝比例:又亮又不飽和 = 被壓成白色,色相已經不可信。

    亮面材質在燈下產生鏡面高光時就會出現這種像素。校正時用來判斷
    該買霧面還是亮面。
    """
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    clipped = np.count_nonzero((v >= 250) & (s < 60))
    total = hsv.shape[0] * hsv.shape[1]
    return float(clipped) / total if total else 0.0


# --- 主要進入點 ------------------------------------------------------------

def detect(image, bbox, config=None, diagnostics=False):
    """在 image 的 bbox 範圍內找標記。

    image:       BGR numpy 陣列(相機原生格式,不用先轉)
    bbox:        正規化的人框 [x1, y1, x2, y2]
    diagnostics: True 才計算過曝比例(校正時用,上車時關掉省時間)
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return EMPTY_RESULT

    cfg = config or MarkerConfig()
    h, w = image.shape[:2]
    x1, y1, x2, y2 = _to_pixel_box(bbox, w, h)

    # 只取人框下半部。ROI 的座標是相對的,最後要加回 (x1, top) 才是全畫面座標。
    top = y1 + int((y2 - y1) * (1.0 - cfg.search_bottom_fraction))
    roi = image[top:y2, x1:x2]
    if roi.size == 0:
        return EMPTY_RESULT

    # 只轉 ROI 不轉整張:cvtColor 是逐像素運算,整張 224x168 比一小塊腿部貴得多
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    clipped = _clipped_ratio(hsv) if diagnostics else 0.0

    roi_pixels = roi.shape[0] * roi.shape[1]

    if not cfg.require_both:
        # 定案規則:兩色各自獨立判斷,任一成立就算找到。
        pink = _colour_blob(hsv, cfg.pink_ranges, cfg.min_pink_area,
                            cfg.max_pink_ratio, roi_pixels)
        green = _colour_blob(hsv, cfg.second_ranges, cfg.min_second_area,
                             cfg.max_second_ratio, roi_pixels)
        pink_area = pink[0] if pink else 0
        green_area = green[0] if green else 0
        # 畫框優先用桃紅(色塊比較完整);只有綠色時才畫綠色那塊
        best = pink if pink else green
        if best is None:
            return EMPTY_RESULT._replace(pink_area=pink_area, second_area=green_area,
                                         clipped_ratio=clipped)
        _a, bx, by, bw, bh = best
        return MarkerResult(True, pink_area, green_area, False,
                            (x1 + bx, top + by, bw, bh), clipped)

    # --- 以下為舊規則(require_both = True):先找桃紅,再在旁邊找第二色 ---
    pink_mask = _build_mask(hsv, cfg.pink_ranges)
    blob = _largest_blob(pink_mask, cfg.min_pink_area)
    if blob is None:
        return EMPTY_RESULT._replace(clipped_ratio=clipped)

    pink_area, bx, by, bw, bh = blob

    if roi_pixels and float(pink_area) / roi_pixels > cfg.max_pink_ratio:
        # 桃紅佔滿了整個腿部區域 → 這是衣物,不是標記
        return EMPTY_RESULT._replace(pink_area=pink_area, clipped_ratio=clipped)

    abs_box = (x1 + bx, top + by, bw, bh)
    bbox_h = abs(bbox[3] - bbox[1])
    dual_required = bbox_h >= cfg.dual_color_min_bbox_h

    if not dual_required:
        # 遠距離:解析不出雙色結構,只認桃紅
        return MarkerResult(True, pink_area, 0, False, abs_box, clipped)

    # 近距離:在桃紅色塊的鄰域找第二個顏色。
    # 刻意不檢查「黃色一定在桃紅下方」—— 在 4~6px 的尺度下,質心的上下關係
    # 已經不可靠了。「兩個螢光色緊鄰」本身就是極罕見的巧合,這樣就夠了。
    pad_y = max(1, int(round(bh * cfg.neighbourhood_scale)))
    pad_x = max(1, int(round(bw * 0.5)))
    ny1 = max(0, by - pad_y)
    ny2 = min(hsv.shape[0], by + bh + pad_y)
    nx1 = max(0, bx - pad_x)
    nx2 = min(hsv.shape[1], bx + bw + pad_x)

    second_mask = _build_mask(hsv[ny1:ny2, nx1:nx2], cfg.second_ranges)
    second_area = int(cv2.countNonZero(second_mask))

    found = second_area >= cfg.second_area_ratio * pink_area
    return MarkerResult(found, pink_area, second_area, True, abs_box, clipped)


class MarkerVerifier(object):
    """接到 FollowStateMachine.identity_score 的介面上。

    簽章與 follow_state.default_identity_score 完全一致:
        (image, bbox, locked_target) -> 0.0 ~ 1.0
    搭配 FollowConfig.identity_threshold = 0.5 使用。
    """

    def __init__(self, config=None):
        self.config = config or MarkerConfig()
        self.last_result = EMPTY_RESULT
        self._last_ok = None

    def __call__(self, image, bbox, locked_target):
        result = detect(image, bbox, self.config)
        self.last_result = result

        if result.found:
            self._last_ok = time.time()
            return 1.0

        # 遮擋寬限**只在已經鎖定時適用**。
        #
        # 鎖定那一關必須看到真的標記,否則前一個目標留下的寬限會讓一個
        # 沒貼標記的人在 1.5 秒內被鎖定 —— 那就完全失去身分驗證的意義了。
        if locked_target is not None and self._last_ok is not None:
            if time.time() - self._last_ok < self.config.grace_seconds:
                return 1.0

        return 0.0

    def reset(self):
        self.last_result = EMPTY_RESULT
        self._last_ok = None


def draw_debug(image, result, color=(255, 0, 255)):
    """把偵測到的標記框畫在畫面上,給現場校正看。座標已是全畫面像素。"""
    if result is None or result.bbox_px is None:
        return
    x, y, w, h = result.bbox_px
    cv2.rectangle(image, (x, y), (x + w, y + h), color, 1)


class MarkerStability(object):
    """現場穩定度統計:連續畫面裡,布條有多少比例被認出來、最長斷了多久。

    照片測試只能證明「靜止時認得出來」。實際跟隨時人在走、兩腿互相遮、
    畫面會晃,要看的是連續畫面。判斷標準:

      配戴者測試  每一段「認不出」都要短於 grace_seconds,跟隨才不會中斷。
                  所以看「最長空檔」,不是只看平均認出率。
      路人測試    沒貼布條的人應該**一幀都不被認出**,認出就是誤判。

    用法(notebook):
        stability = marker.MarkerStability(verifier.config)   # 開始
        ...每幀 execute() 裡呼叫 stability.record(now, image, detections)
        print(stability.report())                             # 結束

    刻意自己呼叫 detect(),不讀 verifier.last_result:狀態機只對**候選框**
    呼叫 verifier,沒偵測到人的那幾幀 last_result 會停在舊值,統計就錯了。
    """

    def __init__(self, config=None, start=None):
        self.config = config or MarkerConfig()
        self.start = time.time() if start is None else start
        self.end = self.start
        self.frames = 0           # 有跑偵測的幀
        self.skipped = 0          # 避障接管、沒跑偵測的幀
        self.person_frames = 0    # 至少偵測到一個人
        self.found_frames = 0     # 至少一個人身上認出布條
        self.pink_frames = 0      # 桃紅單獨成立
        self.green_frames = 0     # 綠色單獨成立
        self.both_frames = 0      # 兩色都成立
        self.longest_gap = 0.0    # 最長連續認不出的秒數
        self.gaps_over_grace = 0  # 超過寬限的空檔次數(= 會跟丟的次數)
        self._last_found = self.start

    def _close_gap(self, now):
        gap = now - self._last_found
        if gap > self.longest_gap:
            self.longest_gap = gap
        if gap > self.config.grace_seconds:
            self.gaps_over_grace += 1

    def record(self, now, image, detections):
        """detections:這一幀已依 label 篩過的人框,[{'bbox': [...]}, ...]"""
        self.frames += 1
        self.end = now
        if detections:
            self.person_frames += 1
        pink = green = found = False
        for det in detections:
            r = detect(image, det['bbox'], self.config)
            found = found or r.found
            pink = pink or r.pink_area > 0
            green = green or r.second_area > 0
        if found:
            self.found_frames += 1
            self._close_gap(now)
            self._last_found = now
        self.pink_frames += pink
        self.green_frames += green
        self.both_frames += pink and green

    def record_skipped(self, now):
        self.skipped += 1
        self.end = now

    def report(self, end=None):
        end = self.end if end is None else end
        secs = max(end - self.start, 1e-6)
        # 結束時若正處於「認不出」,這一段也要算進去
        tail = end - self._last_found
        longest = max(self.longest_gap, tail)
        over = self.gaps_over_grace + (1 if tail > self.config.grace_seconds else 0)

        def pct(n):
            return 100.0 * n / self.frames if self.frames else 0.0

        return '\n'.join([
            '=' * 52,
            '標記穩定度統計(%.1f 秒,%d 幀,約 %.1f FPS)' % (
                secs, self.frames, self.frames / secs),
            '=' * 52,
            '  偵測到人的幀          %5d  (%5.1f%%)' % (self.person_frames, pct(self.person_frames)),
            '  認出布條的幀          %5d  (%5.1f%%)' % (self.found_frames, pct(self.found_frames)),
            '    桃紅單獨成立        %5d  (%5.1f%%)' % (self.pink_frames, pct(self.pink_frames)),
            '    綠色單獨成立        %5d  (%5.1f%%)' % (self.green_frames, pct(self.green_frames)),
            '    兩色都成立          %5d  (%5.1f%%)' % (self.both_frames, pct(self.both_frames)),
            '  避障接管、略過的幀    %5d' % self.skipped,
            '-' * 52,
            '  最長連續認不出        %5.2f 秒' % longest,
            '  超過寬限 %.1f 秒的次數 %4d   (配戴者測試應為 0)' % (
                self.config.grace_seconds, over),
            '-' * 52,
            '  配戴者測試:看「最長連續認不出」要小於寬限、「超過寬限」要是 0',
            '  路人測試  :「認出布條的幀」要是 0,不是 0 就是誤判,',
            '              看是桃紅還是綠色造成的',
            '=' * 52,
        ])
