# -*- coding: utf-8 -*-
"""假資料產生器：照組員的規格模擬車上的跟隨狀態機，不用車子也能測車外這條線。

    【假資料產生器】 ── sender.send() ──→ Mosquitto ──→ receiver.py ──→ sink

做法分兩層，都照 docs/規格回覆-2026-09-19.md：

1. 逐幀模擬 follow_state（預設每秒 10 幀，與車上相同）
   輸入是「這一幀的世界」：畫面裡有沒有人、人多大、偏多少、有沒有被擋住……
   輸出是這一幀的 state 與 events
2. 套上車上 report_to_caregiver() 的節流，決定這一幀要不要送出

情境（scenario）用「這段時間世界長什麼樣子」描述，不是隨機亂數，
同一個情境每次產生的序列完全相同，可以當作任務三的測試題目。

時間一律用整數毫秒計算，避免 0.1 累加的浮點誤差讓「恰好 2 秒」差一幀。

命令列用法（在 repo 根目錄執行）：

    uv run python transport/simulator.py --list                       # 列出情境
    uv run python transport/simulator.py long_loss --print            # 印出來看
    uv run python transport/simulator.py long_loss --out long.jsonl   # 寫成 .jsonl
    uv run --with "paho-mqtt<2.0" python transport/simulator.py long_loss --mqtt --speed 10

在 Jupyter 裡：

    import sys; sys.path.insert(0, "transport")
    import simulator
    records = simulator.generate("long_loss")     # 每筆有 t（秒）、payload、delivered
"""

import argparse
import io
import json
import os
import sys
import time

# ── 車上參數（docs/規格回覆-2026-09-19.md 第一、二節），單位：秒 ──
CONFIRM_SECONDS = 0.75             # confirm_seconds，follow_state.py:439-450
LOST_GRACE_SECONDS = 2.0           # lost_grace_seconds，follow_state.py:123
SEARCH_MIN_OFFSET = 0.12           # search_min_offset，follow_state.py:548
CENTER_TOLERANCE = 0.15            # center_tolerance，follow_state.py:430-436
MIN_BBOX_HEIGHT = 0.30             # min_bbox_height，同上
DETOUR_ADVANCE_SECONDS = 1.5       # detour_advance_seconds，follow_state.py:267-308
IMPASSABLE_AFTER_BLOCKED = 5.0     # impassable_after_blocked_seconds，follow_state.py:149
BLOCK_WINDOW_SECONDS = 8.0         # block_window_seconds，follow_state.py:150
BLOCK_RATIO_THRESHOLD = 0.5        # block_ratio_threshold，follow_state.py:151
MAX_DETOUR_ATTEMPTS = 3            # max_detour_attempts，follow_state.py:159
NO_TARGET_NOTIFY_DELAY = 3.0       # no_target_notify_delay，follow_state.py:169
NOTIFY_COOLDOWN = 60.0             # notify_cooldown，follow_state.py:170
REPORT_INTERVAL = 1.0              # 節流心跳，第四節 report_to_caregiver()

# 模擬時實際使用的參數；測試或探討門檻時可用 generate(..., params={...}) 覆寫
DEFAULT_PARAMS = {
    "confirm_seconds": CONFIRM_SECONDS,
    "lost_grace_seconds": LOST_GRACE_SECONDS,
    "search_min_offset": SEARCH_MIN_OFFSET,
    "detour_advance_seconds": DETOUR_ADVANCE_SECONDS,
    "impassable_after_blocked_seconds": IMPASSABLE_AFTER_BLOCKED,
    "block_window_seconds": BLOCK_WINDOW_SECONDS,
    "block_ratio_threshold": BLOCK_RATIO_THRESHOLD,
    "max_detour_attempts": MAX_DETOUR_ATTEMPTS,
    "no_target_notify_delay": NO_TARGET_NOTIFY_DELAY,
    "notify_cooldown": NOTIFY_COOLDOWN,
}

DEFAULT_FPS = 10                   # 車上迴圈每秒約 10 次（進度凍結 五）

# 五個狀態（第一節，follow_state.py:18-22）
WAITING = "waiting"
CONFIRMING = "confirming"
LOCKED = "locked"
DETOUR = "detour"
IMPASSABLE = "impassable_wait"
STATES = (WAITING, CONFIRMING, LOCKED, DETOUR, IMPASSABLE)

# 13 種事件（第二節，follow_state.py:30-42）
EVENTS = (
    "enter_confirming", "cancel_confirming", "lock_acquired", "lock_lost",
    "relocked", "searching", "notify_no_target", "enter_detour", "detour_advance",
    "detour_done", "enter_impassable", "notify_impassable", "resume_follow",
)

# 手動重播／比對時用的固定起始時間（2026-09-23 10:00:00 台北時間）。
# 寫檔預設用這個值，讓同一情境每次寫出的檔案逐位元相同。
DEFAULT_START_TS = 1790128800.0


def _ms(seconds):
    return int(round(seconds * 1000))


# ══════════════════════════════════════════════════════════════
# 一、跟隨狀態機（模擬 follow_state.py）
# ══════════════════════════════════════════════════════════════

class Frame(object):
    """一幀的世界。由情境的區段展開而來。

    person      畫面裡有沒有人（目標框）
    h           目標框高度（畫面比例），當作 target_h 送出
    offset      目標框水平偏移（-1～1，0 為正中央；負為左）
    identity    身分比對是否通過
    who         人的代號；鎖定中換了代號視為「框對不上」→ relocked，倒數中換代號視為換人
    blocked     避障模型判定被擋住（這一幀車上呼叫 on_blocked() 而不是 update()）
    """

    __slots__ = ("person", "h", "offset", "identity", "who", "blocked",
                 "blocked_prob", "distance", "speed_scale", "offline")

    def __init__(self, **kw):
        for key in self.__slots__:
            setattr(self, key, kw[key])

    def candidate_ok(self):
        """鎖定條件三項同時成立（第一節 confirming 列，follow_state.py:430-436）。"""
        return (self.person and abs(self.offset) <= CENTER_TOLERANCE
                and self.h >= MIN_BBOX_HEIGHT and self.identity)


class FollowStateSim(object):
    """車上跟隨狀態機的行為模擬。只模擬車外看得到的部分（state、events）。

    規格沒寫清楚、這裡選最簡單做法的地方，以「【自選】」標註，並列在 README。
    所有門檻比較一律用 >=（「滿 0.75 秒」「超過 2 秒」都是經過時間 >= 參數值）【自選】。
    """

    def __init__(self, frame_ms, params=None):
        self.frame_ms = frame_ms
        self.params = dict(DEFAULT_PARAMS)
        for key, value in (params or {}).items():
            if key not in self.params:
                raise KeyError("沒有這個參數：{}".format(key))
            self.params[key] = value
        self.reset()

    def param_ms(self, name):
        return _ms(self.params[name])

    def reset(self):
        self.state = WAITING
        self.locked_who = None          # 持有的鎖定目標；None 表示沒有鎖定
        self.confirm_who = None
        self.confirm_since = None
        self.lost_since = None          # 跟丟寬限起點
        self.last_offset = 0.0
        self.empty_since = None         # 畫面連續無人的起點
        self.detour_phase = None        # "turn" / "advance"（不是狀態，第一節）
        self.advance_since = None
        self.detour_attempts = 0
        self.blocked_since = None       # 連續被擋的起點
        self.block_history = []         # 最近 8 秒每一幀是否被擋，(t, bool)
        self.last_notify = {}           # 各 notify_* 上次發出的時間
        self.last_reason = None         # 進入 impassable_wait 的原因（只供測試與除錯）

    # ── 共用 ──

    def _should_notify(self, name, now):
        """notify_* 的 60 秒冷卻，follow_state.py:596-598。
        冷卻各事件分開計算，狀況解除時不歸零【自選】。"""
        last = self.last_notify.get(name)
        if last is None or now - last >= self.param_ms('notify_cooldown'):
            self.last_notify[name] = now
            return True
        return False

    def _record_block(self, now, blocked):
        self.block_history.append((now, blocked))
        start = now - self.param_ms('block_window_seconds')
        while self.block_history and self.block_history[0][0] <= start:
            self.block_history.pop(0)

    def _block_ratio_exceeded(self):
        """(b) 8 秒視窗內被擋比例過半，follow_state.py:317-329。
        分母固定為整個視窗（8 秒）而非已累積的幀數，剛開機時才不會一擋就判定【自選】。"""
        blocked_ms = sum(self.frame_ms for _, b in self.block_history if b)
        return blocked_ms > self.params['block_ratio_threshold'] * self.param_ms('block_window_seconds')

    def _impassable_reason(self, now):
        """三條件任一成立即回傳原因（第一節「impassable_wait 的三個進入條件」）。"""
        if (self.blocked_since is not None
                and now - self.blocked_since >= self.param_ms('impassable_after_blocked_seconds')):
            return "blocked_seconds"
        if self._block_ratio_exceeded():
            return "block_ratio"
        if self.detour_attempts > self.params['max_detour_attempts']:
            return "detour_attempts"
        return None

    def _enter_impassable(self, now, reason, events):
        """follow_state.py:560-579。鎖定保留、不消耗跟丟寬限（第一節）。"""
        self.state = IMPASSABLE
        self.detour_phase = None
        self.detour_attempts = 0        # 進入後歸零，否則解除時 (c) 會立刻再成立【自選】
        self.lost_since = None
        self.last_reason = reason
        events.append("enter_impassable")
        if self._should_notify("notify_impassable", now):
            events.append("notify_impassable")

    def _leave_to_follow(self):
        """回 locked 或 waiting，取決於當下是否仍持有鎖定目標（follow_state.py:302, 353）。"""
        self.state = LOCKED if self.locked_who is not None else WAITING

    # ── 每一幀 ──

    def step(self, now, frame):
        """處理一幀；被擋住呼叫 on_blocked()，否則 update()（follow_state.py:229-265、338）。"""
        if frame.blocked:
            return self.on_blocked(now)
        return self.update(now, frame)

    def on_blocked(self, now):
        events = []                                   # 每次呼叫新建 list（follow_state.py:239）
        self._record_block(now, True)
        if self.blocked_since is None:
            self.blocked_since = now

        if self.state == IMPASSABLE:
            if self._should_notify("notify_impassable", now):
                events.append("notify_impassable")
            return events

        if self.state != DETOUR:
            # 開始一次新的繞行；次數超過上限就改判 impassable（(c)，follow_state.py:276-277）
            self.detour_attempts += 1
            reason = self._impassable_reason(now)
            if reason:
                self._enter_impassable(now, reason, events)
                return events
            # 被擋住一律進 detour（「任何狀態 ──被擋住──→ detour」）。
            # 從 confirming 被打斷時倒數作廢，不發 cancel_confirming【自選】
            self.state = DETOUR
            self.detour_phase = "turn"
            self.confirm_who = None
            self.confirm_since = None
            self.lost_since = None      # 繞行中不消耗跟丟寬限【自選】
            events.append("enter_detour")
            return events

        # 已在 detour：轉向中就繼續轉；直行階段是「強制」直行，被擋也不改階段【自選】
        reason = self._impassable_reason(now)
        if reason:
            self._enter_impassable(now, reason, events)
        return events

    def update(self, now, frame):
        events = []                                   # follow_state.py:338
        self._record_block(now, False)
        self.blocked_since = None

        if self.state == IMPASSABLE:
            # 下一次 update() 且不再判定為擺盪 → 解除（follow_state.py:349-354）
            if self._block_ratio_exceeded():
                if self._should_notify("notify_impassable", now):
                    events.append("notify_impassable")
                self._check_no_target(now, frame, events)
                return events
            self._leave_to_follow()
            events.append("resume_follow")
            if self.state == LOCKED and not frame.person:
                self.lost_since = now   # 解除時目標不在畫面，從這一幀開始算跟丟寬限【自選】
            self._check_no_target(now, frame, events)
            return events

        reason = self._impassable_reason(now)        # update() 這邊也要檢查（第一節）
        if reason:
            self._enter_impassable(now, reason, events)
            return events

        if self.state == DETOUR:
            if self.detour_phase == "turn":
                self.detour_phase = "advance"        # 已轉到淨空，開始強制直行
                self.advance_since = now
                events.append("detour_advance")
            elif now - self.advance_since >= self.param_ms('detour_advance_seconds'):
                self.detour_phase = None
                self._leave_to_follow()
                events.append("detour_done")
                if self.state == LOCKED and not frame.person:
                    self.lost_since = now            # 同 resume_follow【自選】
            self._check_no_target(now, frame, events)
            return events

        if self.state == WAITING:
            if frame.candidate_ok():
                self.state = CONFIRMING
                self.confirm_who = frame.who
                self.confirm_since = now
                events.append("enter_confirming")
        elif self.state == CONFIRMING:
            if not frame.candidate_ok():
                self.state = WAITING
                self.confirm_who = None
                events.append("cancel_confirming")
            elif frame.who != self.confirm_who:
                # 倒數中換人：計時歸零、狀態不變，兩個事件同一筆（follow_state.py:472-473）
                self.confirm_who = frame.who
                self.confirm_since = now
                events.extend(["cancel_confirming", "enter_confirming"])
            elif now - self.confirm_since >= self.param_ms('confirm_seconds'):
                self.state = LOCKED
                self.locked_who = frame.who
                self.confirm_who = None
                self.lost_since = None
                self.last_offset = frame.offset
                events.append("lock_acquired")
        elif self.state == LOCKED:
            self._update_locked(now, frame, events)

        self._check_no_target(now, frame, events)
        return events

    def _update_locked(self, now, frame, events):
        if frame.person:
            if self.lost_since is None and frame.who != self.locked_who:
                events.append("relocked")            # 框對不上，改選最近的候選框（follow_state.py:500）
            self.locked_who = frame.who
            self.lost_since = None
            self.last_offset = frame.offset
            return
        # 跟丟（_handle_lost，follow_state.py:530-558）
        if self.lost_since is None:
            self.lost_since = now
            if abs(self.last_offset) >= self.params['search_min_offset']:
                events.append("searching")
        elif now - self.lost_since >= self.param_ms('lost_grace_seconds'):
            self.state = WAITING
            self.locked_who = None
            self.lost_since = None
            events.append("lock_lost")

    def _check_no_target(self, now, frame, events):
        """畫面連續無人超過 3 秒發 notify_no_target，持續時每 60 秒重發（follow_state.py:594）。
        只在 update() 的幀檢查；被擋的幀不看畫面，但也不中斷「連續無人」的計時【自選】。"""
        if frame.person:
            self.empty_since = None
            return
        if self.empty_since is None:
            self.empty_since = now
        elif (now - self.empty_since >= self.param_ms('no_target_notify_delay')
              and self._should_notify("notify_no_target", now)):
            events.append("notify_no_target")


# ══════════════════════════════════════════════════════════════
# 二、情境
# ══════════════════════════════════════════════════════════════
#
# 一個情境是一串「區段」，每段描述一段時間內世界的樣子。
# **沒寫的欄位沿用上一段**，所以只要寫「這段改了什麼」。
#
#   sec           這段持續幾秒（必填）
#   person        畫面裡有沒有人
#   h             目標框高度（0～1）；鎖定需要 >= 0.30
#   offset        水平偏移（-1～1）；鎖定需要 |offset| <= 0.15，跟丟前 >= 0.12 會發 searching
#   identity      身分比對是否通過
#   who           人的代號（換代號＝換了一個人）
#   blocked       是否被擋住
#   blocked_prob  避障機率；沒寫時被擋 0.93、沒擋 0.05
#   distance      測距（公尺）；None 代表讀不到，會送出 null
#   speed_scale   前進速度倍率
#   offline       這段時間車子照常運作，但資料送不出來（模擬失聯）
#   note          註解，不影響資料
#
# 新增情境：在 SCENARIOS 加一筆，或寫成同樣結構的 .json 檔，用 --scenario-file 載入。

INITIAL = {
    "person": False, "h": 0.40, "offset": 0.0, "identity": True, "who": "A",
    "blocked": False, "blocked_prob": None, "distance": 1.2, "speed_scale": 1.0,
    "offline": False,
}
SEGMENT_KEYS = set(INITIAL) | {"sec", "note"}

SCENARIOS = {
    "normal": {
        "description": "正常跟隨：鎖定後只有每秒心跳，沒有需要通知的事",
        "segments": [
            {"sec": 20, "person": True, "note": "人站在正前方，0.8 秒後鎖定"},
            {"sec": 20, "distance": 0.8, "h": 0.5, "note": "走近一點"},
            {"sec": 20, "distance": 1.5, "h": 0.33, "offset": -0.08},
        ],
    },
    "brief_loss": {
        "description": "短暫跟丟後找回（1.5 秒 < 寬限 2 秒）：有 searching，沒有 lock_lost",
        "segments": [
            {"sec": 5, "person": True},
            {"sec": 2, "offset": 0.3, "note": "人往右走，偏移 0.3 >= 0.12"},
            {"sec": 1.5, "person": False, "note": "消失 1.5 秒"},
            {"sec": 10, "person": True, "offset": 0.05},
        ],
    },
    "long_loss": {
        "description": "長時間跟丟：searching → 2 秒後 lock_lost → 畫面無人滿 3 秒 notify_no_target，每 60 秒重發",
        "segments": [
            {"sec": 5, "person": True},
            {"sec": 1, "offset": -0.25, "note": "人往左走"},
            {"sec": 150, "person": False, "note": "消失兩分半"},
            {"sec": 5, "person": True, "offset": 0.0, "note": "回來，重新確認鎖定"},
        ],
    },
    "detour_ok": {
        "description": "繞行成功：enter_detour → detour_advance → 1.5 秒後 detour_done → 回 locked",
        "segments": [
            {"sec": 5, "person": True},
            {"sec": 0.8, "blocked": True, "note": "前方有障礙，原地左轉"},
            {"sec": 10, "blocked": False, "note": "轉到淨空，強制直行 1.5 秒後回到跟隨"},
        ],
    },
    "detour_stuck": {
        "description": "繞行失敗卡住：連續繞行超過 3 次 → impassable_wait，notify_impassable 每 60 秒重發，障礙移開後 resume_follow",
        "segments": [
            {"sec": 5, "person": True},
            {"sec": 0.5, "blocked": True, "note": "第 1 次繞行"},
            {"sec": 1.6, "blocked": False},
            {"sec": 0.5, "blocked": True, "note": "第 2 次"},
            {"sec": 1.6, "blocked": False},
            {"sec": 0.5, "blocked": True, "note": "第 3 次"},
            {"sec": 1.6, "blocked": False},
            {"sec": 130, "blocked": True, "note": "第 4 次 → 判定繞不過去，一直卡著"},
            {"sec": 15, "blocked": False, "note": "障礙移開；8 秒視窗內被擋比例降到一半以下才解除"},
        ],
    },
    "blocked_long": {
        # 照規格，連續被擋 4.1 秒時 8 秒視窗已過半，(b) 會比 (a) 的 5 秒先成立；
        # 見 README「規格疑問」。要單獨看 (a)，用 params={"block_ratio_threshold": 1.0}
        "description": "長時間被擋 → impassable_wait（照規格實際由條件 b 在 4.1 秒觸發，早於條件 a 的 5 秒）",
        "segments": [
            {"sec": 5, "person": True},
            {"sec": 7, "blocked": True},
            {"sec": 10, "blocked": False},
        ],
    },
    "oscillate": {
        "description": "擺盪：擋 1 秒、通 0.5 秒反覆，8 秒內被擋比例過半 → impassable_wait（條件 b）",
        "segments": [{"sec": 5, "person": True}]
        + [{"sec": 1.0, "blocked": True}, {"sec": 0.5, "blocked": False}] * 6
        + [{"sec": 12, "blocked": False}],
    },
    "switch_confirm": {
        "description": "倒數中換人：同一筆出現 [cancel_confirming, enter_confirming]，計時歸零",
        "segments": [
            {"sec": 0.5, "person": True, "who": "A", "note": "A 站定，倒數中"},
            {"sec": 10, "who": "B", "note": "換成 B，從頭倒數"},
        ],
    },
    "distance_null": {
        "description": "測距讀不到：distance_m 為 null（沒接線、超出量程、深色衣物，車外無法區分）",
        "segments": [
            {"sec": 5, "person": True},
            {"sec": 10, "distance": None, "note": "讀不到"},
            {"sec": 5, "distance": 1.0},
        ],
    },
    "offline": {
        "description": "車子失聯：跟隨中停止發送 12 秒再恢復（車上照常運作）",
        "segments": [
            {"sec": 10, "person": True},
            {"sec": 12, "offline": True, "note": "熱點斷線，資料送不出來"},
            {"sec": 10, "offline": False},
        ],
    },
}


def load_scenario_file(path):
    """讀 .json 情境檔，格式同 SCENARIOS 的一筆：{"description": ..., "segments": [...]}。"""
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve(scenario):
    if isinstance(scenario, dict):
        return scenario
    if scenario in SCENARIOS:
        return SCENARIOS[scenario]
    raise KeyError("沒有這個情境：{}（可用：{}）".format(scenario, ", ".join(sorted(SCENARIOS))))


def expand_frames(scenario, fps=DEFAULT_FPS):
    """把情境展開成逐幀的 (時間毫秒, Frame)。"""
    segments = _resolve(scenario)["segments"]
    frame_ms = 1000 // fps
    world = dict(INITIAL)
    t = 0
    frames = []
    for i, seg in enumerate(segments):
        unknown = set(seg) - SEGMENT_KEYS
        if unknown:
            raise ValueError("第 {} 段有不認得的欄位：{}".format(i + 1, sorted(unknown)))
        if "sec" not in seg:
            raise ValueError("第 {} 段缺少 sec".format(i + 1))
        world.update((k, v) for k, v in seg.items() if k in INITIAL)
        end = t + _ms(seg["sec"])
        while t < end:
            frames.append((t, Frame(**world)))
            t += frame_ms
    return frames


# ══════════════════════════════════════════════════════════════
# 三、產生資料（狀態機 ＋ 車上節流）
# ══════════════════════════════════════════════════════════════

def _payload(state, events, frame):
    """組成與車上 report_to_caregiver() 相同的 JSON（第三節）。"""
    blocked_prob = frame.blocked_prob
    if blocked_prob is None:
        blocked_prob = 0.93 if frame.blocked else 0.05
    # 被擋的幀走 on_blocked()，不處理偵測框，所以沒有目標框【自選，依第三節範例三】
    has_target = bool(frame.person and not frame.blocked)
    return {
        "state": state,
        "events": list(events),
        "has_target": has_target,
        "readings": {
            "blocked_prob": round(blocked_prob, 3),
            "distance_m": None if frame.distance is None else round(frame.distance, 3),
            "speed_scale": frame.speed_scale,
            "target_h": round(frame.h, 3) if has_target else None,
        },
    }


def generate(scenario, fps=DEFAULT_FPS, start_ts=None, include_offline=False, params=None):
    """產生一個情境的回報序列。

    回傳 list，每筆 dict：
        t           模擬時間（秒，從 0 開始）
        payload     要交給 sender.send() 的內容（start_ts 有給時含 ts = start_ts + t）
        delivered   False 代表失聯中送不出去（只有 include_offline=True 才會出現）
        reason      這筆若進入 impassable_wait，記錄是哪個條件觸發（除錯用，不是 payload 的一部分）

    params 可覆寫車上參數（見 DEFAULT_PARAMS），預設與組員規格相同。

    節流與車上 report_to_caregiver() 完全相同（第四節）：
        state 改變或 events 非空 → 立刻送；否則距離上次送出滿 1.0 秒才送。
    失聯期間車上照常呼叫 send()，節流計時照走，只是資料到不了車外。
    """
    frames = expand_frames(scenario, fps)
    sim = FollowStateSim(1000 // fps, params)
    last_state = None
    last_at = None
    out = []
    for now, frame in frames:
        sim.last_reason = None
        events = sim.step(now, frame)
        changed = (sim.state != last_state) or bool(events)
        if not changed and last_at is not None and now - last_at < _ms(REPORT_INTERVAL):
            continue
        last_state = sim.state
        last_at = now
        if frame.offline and not include_offline:
            continue
        t = now / 1000.0
        payload = _payload(sim.state, events, frame)
        if start_ts is not None:
            payload["ts"] = round(start_ts + t, 3)
        out.append({"t": t, "payload": payload, "delivered": not frame.offline,
                    "reason": sim.last_reason})
    return out


def scenario_duration(scenario):
    return sum(seg["sec"] for seg in _resolve(scenario)["segments"])


# ══════════════════════════════════════════════════════════════
# 四、輸出
# ══════════════════════════════════════════════════════════════

def write_jsonl(records, path):
    """每行一筆 JSON，格式與規格回覆第五節的桌上錄製相同（payload ＋ ts）。"""
    with io.open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec["payload"], ensure_ascii=False) + "\n")


def _pace(t, speed, wall_start):
    """等到模擬時間 t 對應的真實時間。speed <= 0 表示不等。"""
    if not speed or speed <= 0:
        return
    delay = wall_start + t / speed - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def play(records, emit, speed=1.0, stamp_ts=None):
    """依模擬時間把 records 一筆筆交給 emit(payload)。

    speed      1.0 即時；10 為十倍速；0 盡快
    stamp_ts   非即時（加速或盡快）時由產生器用模擬時間填 ts（起點＝開始播放的真實時間）；
               即時模式不填，交給 sender.py 補上發送當下的時間
    """
    if stamp_ts is None:
        stamp_ts = speed != 1.0
    wall_start = time.monotonic()
    ts_start = time.time()
    for rec in records:
        _pace(rec["t"], speed, wall_start)
        payload = dict(rec["payload"])
        if stamp_ts:
            payload["ts"] = round(ts_start + rec["t"], 3)
        emit(payload)


def _print_record(payload, t=None):
    prefix = "" if t is None else "{:7.1f}s  ".format(t)
    sys.stdout.write(prefix + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _connect_sender(host, port, wait):
    """載入 transport/sender.py；等它在背景連上 broker（最多 wait 秒），避免前幾筆被丟棄。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import sender
    if host or port:
        sender.configure(host=host, port=port)
    deadline = time.monotonic() + wait
    while not sender.is_connected() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not sender.is_connected():
        sys.stderr.write("[simulator] {} 秒內沒連上 broker，照樣開始送（資料會被 sender 丟棄）\n".format(wait))
    return sender


def main(argv=None):
    ap = argparse.ArgumentParser(description="假資料產生器：模擬車上跟隨狀態機的回報")
    ap.add_argument("scenario", nargs="?", help="情境名稱（--list 看全部）")
    ap.add_argument("--list", action="store_true", help="列出內建情境")
    ap.add_argument("--scenario-file", help="從 .json 讀情境")
    ap.add_argument("--mqtt", action="store_true", help="經 sender.py 送到 MQTT")
    ap.add_argument("--out", help="寫成 .jsonl 檔")
    ap.add_argument("--print", dest="do_print", action="store_true", help="印出每一筆")
    ap.add_argument("--speed", type=float, default=None,
                    help="播放速度；--mqtt 預設 1（即時），其他模式預設 0（不等）")
    ap.add_argument("--host", help="broker 位址（沒給時照 sender.py 的規則）")
    ap.add_argument("--port", type=int, help="broker 埠號")
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS, help="模擬的車上迴圈頻率")
    ap.add_argument("--start-ts", default=None,
                    help="寫檔時的起始 ts；預設固定值（可重現），給 now 則用現在時間")
    args = ap.parse_args(argv)

    if args.list:
        for name in sorted(SCENARIOS):
            sc = SCENARIOS[name]
            print("{:16s} {:6.1f}s  {}".format(name, scenario_duration(sc), sc["description"]))
        return 0

    if args.scenario_file:
        scenario = load_scenario_file(args.scenario_file)
    elif args.scenario:
        scenario = args.scenario
    else:
        ap.error("請指定情境名稱或 --scenario-file")
    if not (args.mqtt or args.out or args.do_print):
        args.do_print = True

    if args.out:
        if args.start_ts == "now":
            start_ts = time.time()
        elif args.start_ts:
            start_ts = float(args.start_ts)
        else:
            start_ts = DEFAULT_START_TS
        records = generate(scenario, fps=args.fps, start_ts=start_ts)
        write_jsonl(records, args.out)
        sys.stderr.write("[simulator] 已寫入 {} 筆到 {}\n".format(len(records), args.out))
        if not (args.mqtt or args.do_print):
            return 0

    records = generate(scenario, fps=args.fps)
    speed = args.speed if args.speed is not None else (1.0 if args.mqtt else 0.0)

    if args.mqtt:
        sender = _connect_sender(args.host, args.port, wait=5.0)

        def emit(payload):
            sender.send(payload)
            if args.do_print:
                _print_record(payload)

        sys.stderr.write("[simulator] 開始送出 {} 筆，速度 {}x，情境長 {:.1f} 秒\n".format(
            len(records), speed or "盡快", scenario_duration(scenario)))
        try:
            play(records, emit, speed=speed)
        except KeyboardInterrupt:
            sys.stderr.write("[simulator] 中斷\n")
        time.sleep(0.5)                 # 讓 paho 背景執行緒把最後幾筆送完
        sender.stop()
    else:
        wall_start = time.monotonic()
        for rec in records:
            _pace(rec["t"], speed, wall_start)
            _print_record(rec["payload"], rec["t"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
