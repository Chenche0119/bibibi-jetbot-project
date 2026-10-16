"""車外狀態機：監控車上回報的 state／events，決定要不要通知家屬，並轉譯成訊息。

    receiver.py ── cache.put("jetbot", payload) ─► 覆蓋快取 ─► 每拍讀一次 ─► 查分類表 ─► 通報 ─► 輸出（LINE）
                                                                    └─► 失聯偵測（車外獨有）

判斷原則（docs/進度凍結-2026-09-16.md 變更一）：
- 直接信任車上的判斷，只看車上告訴我們的 state、events、has_target，不重算車內邏輯
- 車外只做車上做不到的三件事：失聯偵測、把多個事件整合成一則訊息、轉譯成家屬看得懂的文字

要調整「哪些事件要通知、冷卻多久、怎麼算解除」，改下方設定區的 CATEGORIES 即可，不用改判斷邏輯。
訊息文字在 TEMPLATES；要換成 LLM，把自己的 formatter 傳給 line_output()（見 format_message）。

資料格式見 docs/狀態機輸入格式.md。

用法（Jupyter 或一般 Python 皆可）：
    sm = StateMachine()
    sm.start()                                                  # 背景執行
    sm.cache.put("jetbot", {"state": "locked", "events": [], "has_target": True, "readings": {}})
    print(sm.status())
    sm.stop()
"""

import collections
import json
import os
import threading
import time
import urllib.error
import urllib.request

# ═══════════════════════════════════════════════════════════
#  設定區
# ═══════════════════════════════════════════════════════════

SOURCE = "jetbot"      # 接收程式寫入快取時用的來源名稱（transport/README.md 第二節）
TICK_HZ = 10           # 每秒判斷幾次
OFFLINE_SEC = 5.0      # 超過幾秒沒收到任何資料就判定失聯（車上每 1 秒至少送一次）
LOG_SIZE = 500         # 車上事件紀錄最多保留幾筆

# 車上的五種狀態（docs/規格回覆-2026-09-19.md 第一節）
CAR_STATES = ["waiting", "confirming", "locked", "detour", "impassable_wait"]

# ── 分類表（docs/tasks/03-state-machine.md 第二節）──
# priority        數字越小越優先。同一拍有多件事時只送一則，以最優先的為主，其餘併入同一則訊息
# cooldown        同一類異常，第一次立即通知，之後同一段異常期間每隔幾秒最多再提醒一則
# trigger_events  車上送來這些事件之一 → 這類異常成立（notify_* 會每 60 秒重發，代表還沒解除）
# recover_events  車上送來這些事件之一 → 解除
# recover_when    或者車上的欄位符合這些值 → 解除（判斷解除看 state 與 has_target，不等 notify_* 停止）
# onset_when      車上的欄位從什麼時候開始符合這些值，就從那時起算「持續多久」
# 失聯（offline）由車外自己判斷，沒有事件可查，只用 priority 與 cooldown
CATEGORIES = collections.OrderedDict([
    ("offline", {
        "priority": 1,
        "cooldown": 300.0,
    }),
    ("impassable", {
        "priority": 2,
        "cooldown": 300.0,
        "trigger_events": ["notify_impassable", "enter_impassable"],
        "recover_events": ["resume_follow"],
        "recover_when": {"state": ["waiting", "confirming", "locked", "detour"]},
        "onset_when": {"state": ["impassable_wait"]},
    }),
    ("no_target", {
        "priority": 3,
        "cooldown": 300.0,
        "trigger_events": ["notify_no_target"],
        "recover_events": ["lock_acquired"],
        "recover_when": {"state": ["locked"], "has_target": [True]},
        "onset_when": {"has_target": [False]},
    }),
])

# 車子自己處理得了的事件：只記錄、不通知。
# lock_lost 也在這裡：車上約 1 秒後會發 notify_no_target，通知那則即可，避免同一件事推兩則
LOG_ONLY_EVENTS = [
    "enter_confirming", "cancel_confirming", "lock_acquired", "relocked", "searching",
    "enter_detour", "detour_advance", "detour_done", "lock_lost",
]

# 通知的種類
ALERT = "alert"          # 這段異常的第一則
REMINDER = "reminder"    # 異常持續中，冷卻過後再提醒
RECOVERY = "recovery"    # 解除（只有先前推播過這段異常才會有）

# ── 訊息模板（固定模板版的 formatter 使用，也是 LLM 的範例與退路）──
# 由 Claude 產生、人工審核（docs/tasks/06-template-upgrade.md）。只能寫該文件第一節的事實。
# 每種通知 3 個版本，依通知時間輪流（template_versions、format_message）；{duration} 代入 format_duration() 的文字
# 值為 dict 時依車上狀態分支（is_following）：卡住解除時車子可能繼續跟隨（locked），也可能沒在跟（not_locked）
MESSAGE_HEADER = "【伴護機器人】{clock}"
TEMPLATES = {
    ("offline", ALERT): [
        "與機器人失去連線，已經{duration}沒有收到資料，暫時無法得知車子的狀況。連線恢復後會再通知您。",
        "機器人斷線了，{duration}沒有收到任何資料。這段期間無法回報車子的狀況，恢復後會立即告訴您。",
        "和機器人的連線中斷，已{duration}沒收到資料，目前無法確認車子的情形。恢復連線後會再通知。",
    ],
    ("offline", REMINDER): [
        "與機器人仍然沒有連線，已經中斷{duration}了。",
        "提醒您：機器人已斷線{duration}，目前仍收不到資料。",
        "連線還沒恢復，已經{duration}沒有收到車子的資料。",
    ],
    ("offline", RECOVERY): [
        "與機器人的連線已經恢復，剛才中斷了{duration}。",
        "機器人重新連線了（中斷{duration}），可以繼續回報狀況。",
        "連線恢復了，先前有{duration}沒收到資料。",
    ],
    ("impassable", ALERT): [
        "車子前方被擋住、繞不過去，已經停在原地等待。障礙移開後會自動恢復。",
        "前方有東西擋住，車子無法通過，已先停下來。前方淨空後會自動接著走。",
        "車子繞不過前方的障礙，目前停在原地，等障礙移開再繼續。",
    ],
    ("impassable", REMINDER): [
        "車子仍停在原地，前方還是無法通過，已經{duration}了。",
        "提醒您：車子被擋住已{duration}，目前仍在原地等待前方淨空。",
        "前方的障礙還沒有排除，車子已停了{duration}。",
    ],
    ("impassable", RECOVERY): {
        "locked": [
            "前方已經可以通過，車子繼續跟隨了。剛才停了{duration}。",
            "障礙已排除，車子恢復跟隨（停留{duration}）。",
            "車子前方的障礙移開了，已接著跟隨受護者，先前停了{duration}。",
        ],
        "not_locked": [
            "前方已經可以通過（停了{duration}），但車子目前沒有看到受護者，正在原地等受護者回到前方。",
            "障礙已排除，不過車子現在沒有鎖定受護者，會停在原地等受護者回到前方（先前停了{duration}）。",
            "前方的障礙移開了（停了{duration}），但車子還沒找到受護者，暫時停在原地等待。",
        ],
    },
    ("no_target", ALERT): [
        "車子看不到受護者了，已經停在原地等待。受護者回到車子正前方站定後，車子會自動重新跟上。",
        "車子目前看不到受護者，先停在原地不動。等受護者回到車子前方，就會恢復跟隨。",
        "車子跟丟了受護者，現在停在原地等待，不會自己去找。受護者回到車子正前方後會自動接續跟隨。",
    ],
    ("no_target", REMINDER): [
        "車子到現在還是看不到受護者，已經{duration}了，仍停在原地等待。",
        "提醒您：車子看不到受護者已{duration}，目前仍停在原地。",
        "車子仍然沒有看到受護者（已{duration}），還在原地等受護者回到前方。",
    ],
    ("no_target", RECOVERY): [
        "車子已經重新找到受護者，恢復跟隨了。剛才{duration}沒看到人。",
        "受護者回到車子前方，車子已重新跟上（中斷{duration}）。",
        "車子重新看到受護者並恢復跟隨，先前中斷了{duration}。",
    ],
}
# 併入同一則訊息的其他事項（同一拍有多件事時），接在「另外，」後面
OTHER_TEMPLATES = {
    ("offline", RECOVERY): "與機器人的連線已恢復（中斷{duration}）。",
    ("impassable", ALERT): "車子前方被擋住，已停在原地。",
    ("impassable", REMINDER): "車子仍被擋在原地。",
    ("impassable", RECOVERY): "前方已經可以通過。",
    ("no_target", ALERT): "車子目前也看不到受護者。",
    ("no_target", REMINDER): "車子仍然看不到受護者。",
    ("no_target", RECOVERY): "已重新找到受護者。",
}
# notice 裡要放哪些數值（給訊息產生器與 LLM）。speed_scale 不能判斷車子有沒有在動、target_h 對家屬沒有意義，不放
NOTICE_READINGS = ["distance_m", "blocked_prob"]
# 各類別要在訊息裡寫出哪些數值（解除時不寫）；數值為 null（不知道）時整行不寫
READING_TEMPLATES = {
    "impassable": [("distance_m", "目前車子與前方障礙約 {:.1f} 公尺。")],
}

# ═══════════════════════════════════════════════════════════

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 覆蓋快取 ──
class Cache(object):
    """每個來源只留最新一筆，新的直接蓋掉舊的。"""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()   # 兩端可能從不同執行緒同時寫入
        self._data = {}

    def put(self, source, payload):
        """寫入某個來源的最新資料。payload 沒帶 ts 時，以寫入當下的時間補上。"""
        if not isinstance(payload, dict):
            raise TypeError("payload 必須是 dict，收到 {}".format(type(payload).__name__))
        entry = dict(payload)
        if entry.get("ts") is None:
            entry["ts"] = self._clock()
        with self._lock:
            self._data[source] = entry

    def snapshot(self):
        with self._lock:
            return {source: dict(entry) for source, entry in self._data.items()}


# ── 判斷 ──
def matches(payload, condition):
    """payload 的每個欄位都在 condition 允許的值之內才算符合。condition 為空或 None 時不符合。"""
    if not condition:
        return False
    for field, allowed in condition.items():
        if payload.get(field) not in allowed:
            return False
    return True


def car_events(payload):
    """取出 events；格式不對時當作沒有事件，狀態機不中斷。"""
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    return [str(e) for e in events]


def usable_readings(payload):
    """NOTICE_READINGS 中有值的數值。null 代表「不知道」（例如 distance_m 讀不到），不可當作任何值，直接拿掉。"""
    readings = payload.get("readings")
    if not isinstance(readings, dict):
        return {}
    out = {}
    for key in NOTICE_READINGS:
        value = readings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[key] = value
    return out


def rank(item):
    """同一拍有多件事時的排序：異常（依類別優先度）排在前面，解除一律排在所有異常之後。"""
    category, kind = item
    return (1 if kind == RECOVERY else 0, CATEGORIES[category]["priority"])


# ── 狀態機 ──
class StateMachine(object):

    def __init__(self, outputs=None, clock=time.time, verbose=True, offline_sec=None):
        """
        outputs: 要通知家屬時依序呼叫的函式清單，每個都是 fn(notice) → 結果說明。
                 預設只有 LINE（dry-run）。之後要加其他輸出，加一個函式進來即可。
        clock:   車外的時鐘。測試時換成假時鐘，可以瞬間快轉。
        verbose: 通知時是否印出。Jupyter 背景執行時建議設 False，改用 status() 查看。
        offline_sec: 失聯門檻（秒），不給就用 OFFLINE_SEC。
        """
        self.cache = Cache(clock=clock)
        self.outputs = list(outputs) if outputs is not None else [line_output()]
        self.verbose = verbose
        self.offline_sec = OFFLINE_SEC if offline_sec is None else float(offline_sec)
        self.active = {}      # 已推播、尚未解除的異常：{類別: {"since", "last_sent", "sent"}}
        self.notices = []     # 每一則通知的紀錄
        self.log = collections.deque(maxlen=LOG_SIZE)   # 車上送來的每個事件（含只記錄不通知的）
        self.last = None      # 最近一拍的細節
        self._clock = clock
        self._last_ts = None      # 上一筆處理過的資料的 ts，用來分辨快取裡是不是新資料
        self._last_rx = None      # 最近一次收到新資料的車外時間
        self._payload = None      # 最近一筆車上資料
        self._onset = {}          # 各類別的狀況從何時開始（車外時間）
        self._lock = threading.Lock()   # 背景執行時，讓 active／last／notices 一起更新、一起讀
        self._stop = threading.Event()
        self._thread = None

    def tick(self):
        """讀一次快取並判斷。有通知時回傳 notice，否則回傳 None。"""
        now = self._clock()
        entry = self.cache.snapshot().get(SOURCE)
        fresh = entry is not None and (self._last_ts is None or entry.get("ts") != self._last_ts)

        with self._lock:
            if fresh:
                candidates = self._judge_payload(now, entry)
            else:
                candidates = self._judge_offline(now)
            notice = self._decide(now, candidates) if candidates else None
            if notice:
                self.notices.append(notice)
            self.last = {"time": now, "fresh": fresh,
                         "silence": None if self._last_rx is None else now - self._last_rx,
                         "payload": self._payload, "active": sorted(self.active)}

        # 輸出（例如送 LINE）可能要等網路，放在鎖外面，才不會卡住畫面讀取
        if notice:
            notice["outputs"].extend(self._emit(notice))
            if notice["car_ts"] is not None:
                notice["latency_sec"] = self._clock() - notice["car_ts"]
            if self.verbose:
                print(format_notice_log(notice))
        return notice

    def _judge_payload(self, now, payload):
        """處理一筆新資料，回傳這一拍成立的 (類別, 種類) 清單。"""
        self._last_ts = payload.get("ts")
        self._last_rx = now
        self._payload = payload
        events = car_events(payload)
        for event in events:
            self.log.append({"time": now, "ts": payload.get("ts"), "event": event,
                             "state": payload.get("state"), "has_target": payload.get("has_target")})

        candidates = []
        if "offline" in self.active:
            candidates.append(("offline", RECOVERY))
        self._onset.pop("offline", None)

        for category, rule in CATEGORIES.items():
            if "trigger_events" not in rule:
                continue
            if matches(payload, rule.get("onset_when")):
                self._onset.setdefault(category, now)
            else:
                self._onset.pop(category, None)

            if any(e in events for e in rule["trigger_events"]):
                candidates.extend(self._anomaly(now, category))
            elif category in self.active and (
                    any(e in events for e in rule.get("recover_events", []))
                    or matches(payload, rule.get("recover_when"))):
                candidates.append((category, RECOVERY))
        return candidates

    def _judge_offline(self, now):
        """沒有新資料的一拍：只檢查失聯。還沒收到過任何資料時不判定失聯。"""
        if self._last_rx is None or now - self._last_rx <= self.offline_sec:
            return []
        self._onset.setdefault("offline", self._last_rx)
        return self._anomaly(now, "offline")

    def _anomaly(self, now, category):
        """異常成立：這段異常還沒通知過就立即通知；已通知過則冷卻結束才再提醒。"""
        state = self.active.get(category)
        if state is None:
            return [(category, ALERT)]
        if now - state["last_sent"] >= CATEGORIES[category]["cooldown"]:
            return [(category, REMINDER)]
        return []

    def _decide(self, now, candidates):
        """同一拍有多件事時只送一則：以最優先的為主，其餘併入 others。"""
        candidates = sorted(set(candidates), key=rank)
        items = []
        for category, kind in candidates:
            if kind == RECOVERY:
                state = self.active.pop(category)
                since = state["since"]
            else:
                state = self.active.setdefault(category, {
                    "since": min(self._onset.get(category, now), now), "sent": 0})
                state["last_sent"] = now
                state["sent"] += 1
                since = state["since"]
            items.append({"category": category, "kind": kind, "since": since,
                          "duration_sec": round(now - since, 1)})

        primary, others = items[0], items[1:]
        payload = self._payload or {}
        online = primary["category"] != "offline" or primary["kind"] == RECOVERY
        car = None
        if online and payload:
            car = {"state": payload.get("state"), "has_target": payload.get("has_target"),
                   "events": car_events(payload)}
        return {
            "time": now,
            "category": primary["category"],
            "kind": primary["kind"],
            "since": primary["since"],
            "duration_sec": primary["duration_sec"],
            "car": car,                                         # 失聯時為 None（車上現況不明）
            "readings": usable_readings(payload) if car else {},  # 只含有值的數值
            "others": [{"category": o["category"], "kind": o["kind"],
                        "duration_sec": o["duration_sec"]} for o in others],
            "car_ts": payload.get("ts") if car else None,       # 觸發這則通知的那筆資料的車上時間
            "latency_sec": None,                                # car_ts 到輸出完成的時間，輸出後填入
            "outputs": [],
        }

    def _emit(self, notice):
        results = []
        for output in self.outputs:
            try:
                results.append(str(output(notice)))
            except Exception as e:   # 某個輸出失敗，不能讓狀態機停下來
                results.append("失敗：{}".format(e))
        return results

    def view(self):
        """一次取得 (目前未解除的異常, 最近一拍細節, 通知紀錄)，保證三者是同一時刻的。"""
        with self._lock:
            return ({k: dict(v) for k, v in self.active.items()}, self.last, list(self.notices))

    def status(self):
        """目前狀況的文字摘要。"""
        active, last, notices = self.view()
        lines = ["車外狀態機（{}）".format("運作中" if self.running else "未運作")]
        if last is None:
            return lines[0]
        if last["payload"] is None:
            lines.append("尚未收到車上資料")
        else:
            p = last["payload"]
            lines.append("車上：state={} has_target={} events={}".format(
                p.get("state"), p.get("has_target"), car_events(p)))
            lines.append("距上次收到資料 {:.1f} 秒（失聯門檻 {:g} 秒）".format(last["silence"], self.offline_sec))
        if active:
            lines.append("未解除的異常：" + "、".join(
                "{}（{:.0f} 秒）".format(c, last["time"] - s["since"]) for c, s in sorted(active.items())))
        else:
            lines.append("未解除的異常：無")
        lines.append("通知紀錄（共 {} 則）".format(len(notices)))
        for notice in notices[-5:]:
            lines.append("  " + format_notice_log(notice))
        return "\n".join(lines)

    # ── 持續運作 ──
    def run(self, max_ticks=None):
        """在目前的執行緒持續運作（會卡住），Ctrl-C 結束。"""
        self._stop.clear()
        self._loop(max_ticks)

    def start(self):
        """在背景執行緒運作，不會卡住 Jupyter 儲存格。"""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(None,))
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _loop(self, max_ticks):
        count = 0
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:   # 單拍出錯不能讓整個狀態機停下
                print("判斷時發生錯誤，已略過這一拍：{!r}".format(e))
            count += 1
            if max_ticks is not None and count >= max_ticks:
                break
            self._stop.wait(1.0 / TICK_HZ)


# ── 訊息產生器 ──
def format_duration(seconds):
    """給家屬看的時間長度。

    一分鐘內寫秒；1～10 分鐘以半分鐘為單位（150 秒 →「約 2 分半」）；
    10 分鐘以上寫整數分鐘；一小時以上寫「約 1 小時 20 分鐘」。
    """
    seconds = max(0, int(seconds + 0.5))      # 四捨五入（round() 遇到 .5 會取偶數）
    if seconds < 60:
        return "約 {} 秒".format(seconds)
    if seconds < 600:
        minutes, half = divmod(int(seconds / 30.0 + 0.5), 2)
        return "約 {} 分半".format(minutes) if half else "約 {} 分鐘".format(minutes)
    minutes = int(seconds / 60.0 + 0.5)
    if minutes < 60:
        return "約 {} 分鐘".format(minutes)
    hours, rest = divmod(minutes, 60)
    return "約 {} 小時 {} 分鐘".format(hours, rest) if rest else "約 {} 小時".format(hours)


def format_clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def is_following(car):
    """車子是否正在跟隨受護者：state 為 locked 且看得到目標。

    只看 state 不夠：locked 但 has_target 為 false 代表「跟丟寬限中」，人已經不在畫面，
    約 2 秒後就變 waiting（docs/規格回覆-2026-09-19.md 第一節）。car 為 None（失聯、現況不明）時當作沒有。
    """
    car = car or {}
    return car.get("state") == "locked" and car.get("has_target") is True


def template_versions(notice):
    """這則通知可用的模板版本清單（依類別、種類，必要時再依車上狀態分支）。"""
    versions = TEMPLATES[(notice["category"], notice["kind"])]
    if isinstance(versions, dict):
        versions = versions["locked" if is_following(notice.get("car")) else "not_locked"]
    return versions


def format_message(notice):
    """固定模板版的訊息產生器：notice → 給家屬的文字。

    這是可替換的介面：任何 fn(notice) -> str 都可以傳給 line_output(formatter=...)。
    LLM 版本讀同一個 notice（結構化的事實），失敗時退回這個函式。
    版本依通知時間輪流挑選：同一則通知每次都是同一個版本，測試與評測可重現。
    """
    duration = format_duration(notice["duration_sec"])
    versions = template_versions(notice)
    lines = [MESSAGE_HEADER.format(clock=format_clock(notice["time"]))]
    lines.append(versions[int(notice["time"]) % len(versions)].format(duration=duration))
    if notice["kind"] != RECOVERY:
        for field, template in READING_TEMPLATES.get(notice["category"], []):
            if field in notice["readings"]:
                lines.append(template.format(notice["readings"][field]))
    for other in notice["others"]:
        text = OTHER_TEMPLATES.get((other["category"], other["kind"]))
        if text:
            lines.append("另外，" + text.format(duration=format_duration(other["duration_sec"])))
    return "\n".join(lines)


def notice_facts(notice):
    """notice 中給 LLM 用的事實（去掉輸出結果等執行紀錄），可直接 json.dumps。"""
    return {k: v for k, v in notice.items() if k not in ("outputs", "latency_sec")}


# ── 輸出 ──
def format_notice_log(notice):
    now = notice["time"]
    stamp = "{:.1f}s".format(now) if now < 1e8 else time.strftime("%H:%M:%S", time.localtime(now))
    others = "".join("＋{}/{}".format(o["category"], o["kind"]) for o in notice["others"])
    return "{}  {}/{}{}｜持續 {:.1f} 秒｜{}".format(
        stamp, notice["category"], notice["kind"], others, notice["duration_sec"],
        "；".join(notice.get("outputs", [])) or "無輸出")


def line_output(dry_run=True, formatter=None):
    """LINE 通報：收到 notice 就用 formatter 轉成文字送出。預設 dry-run，不會真的送出。

    要不要通知、冷卻多久由狀態機依分類表決定（每一類各自計算），這裡不再另外節流。
    formatter 預設是固定模板 format_message；任務四換成 LLM 版本時傳入即可。
    """
    fmt = formatter or format_message

    def output(notice):
        text = fmt(notice)
        if dry_run:
            return "LINE [dry-run] " + text.replace("\n", " / ")
        status, body = send_line(text)
        return "LINE HTTP {} {}".format(status, body or "").strip()

    return output


def send_line(text):
    """推一則文字訊息到 LINE，回傳 (status, body)。token 讀專案根目錄的 .env。"""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    to = os.environ.get("LINE_TO", "")
    if not token:
        raise RuntimeError("找不到 LINE_CHANNEL_ACCESS_TOKEN，請確認 .env 已填好")

    payload = {"messages": [{"type": "text", "text": text[:5000]}]}
    url = "https://api.line.me/v2/bot/message/broadcast"
    if to:
        payload["to"] = to
        url = "https://api.line.me/v2/bot/message/push"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
