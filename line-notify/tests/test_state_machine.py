"""車外狀態機的行為測試。不需要硬體、broker、網路，不會呼叫 LINE API。

    uv run python tests/test_state_machine.py

以任務二情境為輸入的整合測試在 tests/test_state_machine_scenarios.py。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state_machine"))

import machine  # noqa: E402
from machine import (ALERT, RECOVERY, REMINDER, Cache, StateMachine,  # noqa: E402
                     format_message, line_output, notice_facts)

passed = []
failed = []


class FakeClock(object):
    """可控制的時鐘，讓失聯、冷卻在測試中瞬間發生。"""

    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def check(name, actual, expected):
    if actual == expected:
        passed.append(name)
        print("  ✅ {}".format(name))
    else:
        failed.append(name)
        print("  ❌ {}\n       預期 {!r}，實際 {!r}".format(name, expected, actual))


def make(outputs=None, offline_sec=None):
    clock = FakeClock(1000.0)
    sm = StateMachine(outputs=outputs if outputs is not None else [], clock=clock,
                      verbose=False, offline_sec=offline_sec)
    return sm, clock


def car(state="locked", events=None, has_target=True, distance=0.9):
    return {"state": state, "events": events or [], "has_target": has_target,
            "readings": {"blocked_prob": 0.05, "distance_m": distance, "speed_scale": 1.0,
                         "target_h": 0.4 if has_target else None}}


def send(sm, clock, payload, after=1.0):
    """時間前進 after 秒，車上送來一筆（ts 用車外時間），然後判斷一拍。回傳 (類別, 種類) 或 None。"""
    clock.t += after
    p = dict(payload)
    p.setdefault("ts", clock.t)
    sm.cache.put("jetbot", p)
    notice = sm.tick()
    return (notice["category"], notice["kind"]) if notice else None


def silent(sm, clock, seconds, step=0.1):
    """車上不送資料 seconds 秒，每拍判斷一次。回傳期間的通知 (類別, 種類) 清單。"""
    got = []
    for _ in range(int(round(seconds / step))):
        clock.t += step
        notice = sm.tick()
        if notice:
            got.append((notice["category"], notice["kind"]))
    return got


print("\n【一、覆蓋快取（沿用）】")
cache = Cache(clock=FakeClock(5.0))
cache.put("jetbot", car("waiting"))
cache.put("jetbot", car("locked"))
check("同一來源新的蓋掉舊的", cache.snapshot()["jetbot"]["state"], "locked")
check("沒帶 ts 以寫入時間補上", cache.snapshot()["jetbot"]["ts"], 5.0)
try:
    cache.put("jetbot", "locked")
    check("payload 不是 dict → 寫入時就報錯", "沒有報錯", "TypeError")
except TypeError:
    check("payload 不是 dict → 寫入時就報錯", "TypeError", "TypeError")

print("\n【二、分類表】")
check("分類表是設定：三類都在 CATEGORIES", list(machine.CATEGORIES), ["offline", "impassable", "no_target"])
check("lock_lost 只記錄不通知", "lock_lost" in machine.LOG_ONLY_EVENTS, True)
all_events = set(machine.LOG_ONLY_EVENTS)
for rule in machine.CATEGORIES.values():
    all_events |= set(rule.get("trigger_events", [])) | set(rule.get("recover_events", []))
check("規格的 13 種事件都有歸類", len(all_events), 13)

sm, clock = make()
got = [send(sm, clock, car(events=[e])) for e in machine.LOG_ONLY_EVENTS]
check("只記錄的事件都不通知", got, [None] * len(machine.LOG_ONLY_EVENTS))
check("只記錄的事件有寫進紀錄", [x["event"] for x in sm.log], machine.LOG_ONLY_EVENTS)

sm, clock = make()
check("notify_impassable → 通知卡住", send(sm, clock, car("impassable_wait", ["notify_impassable"], False)),
      ("impassable", ALERT))
sm, clock = make()
check("enter_impassable → 通知卡住", send(sm, clock, car("impassable_wait", ["enter_impassable"], False)),
      ("impassable", ALERT))
sm, clock = make()
check("enter_impassable 與 notify_impassable 同一筆 → 只一則",
      [send(sm, clock, car("impassable_wait", ["enter_impassable", "notify_impassable"], False)),
       send(sm, clock, car("impassable_wait", [], False))], [("impassable", ALERT), None])
sm, clock = make()
check("notify_no_target → 通知看不到受護者", send(sm, clock, car("waiting", ["notify_no_target"], False)),
      ("no_target", ALERT))

sm, clock = make()
check("state 是 impassable_wait 但沒有觸發事件 → 不自行判斷", send(sm, clock, car("impassable_wait", [], False)), None)
sm, clock = make()
check("跟丟寬限中（locked 且 has_target=false）→ 不通知", send(sm, clock, car("locked", [], False)), None)
check("同一筆資料只處理一次（快取沒更新就不重複看事件）", sm.tick(), None)

print("\n【三、冷卻（每一類各自計算）】")
sm, clock = make()
got = [send(sm, clock, car("waiting", ["notify_no_target"], False), after=0)]
for _ in range(5):   # 車上每 60 秒重發一次
    got.append(send(sm, clock, car("waiting", ["notify_no_target"], False), after=60))
check("notify_no_target 每 60 秒重發 → 首次立即，5 分鐘後才提醒一次",
      got, [("no_target", ALERT), None, None, None, None, ("no_target", REMINDER)])
check("冷卻期間照常讀資料，不會把重發當成新的異常", len(sm.notices), 2)

sm, clock = make()
send(sm, clock, car("waiting", ["notify_no_target"], False))
check("看不到受護者的冷卻中，卡住照樣立即通知",
      send(sm, clock, car("impassable_wait", ["notify_impassable"], False), after=10), ("impassable", ALERT))

sm, clock = make()
send(sm, clock, car("waiting", ["notify_no_target"], False))
send(sm, clock, car("locked", ["lock_acquired"], True))
check("解除後再次發生算新的一段，立即通知（不受上一段冷卻影響）",
      send(sm, clock, car("waiting", ["notify_no_target"], False), after=30), ("no_target", ALERT))

print("\n【四、恢復通知】")
sm, clock = make()
check("沒推播過卡住，resume_follow 不發恢復", send(sm, clock, car("locked", ["resume_follow"])), None)
check("沒推播過看不到受護者，lock_acquired 不發恢復", send(sm, clock, car("locked", ["lock_acquired"])), None)

sm, clock = make()
send(sm, clock, car("impassable_wait", ["enter_impassable", "notify_impassable"], False))
check("卡住 → resume_follow 發恢復", send(sm, clock, car("locked", ["resume_follow"])), ("impassable", RECOVERY))
check("恢復只發一次", send(sm, clock, car("locked")), None)

sm, clock = make()
send(sm, clock, car("impassable_wait", ["notify_impassable"], False))
check("卡住 → 沒收到 resume_follow，但 state 已離開 impassable_wait 也算解除",
      send(sm, clock, car("waiting", [], False)), ("impassable", RECOVERY))

sm, clock = make()
send(sm, clock, car("waiting", ["notify_no_target"], False))
check("看不到受護者 → 確認倒數中還不算找回", send(sm, clock, car("confirming", ["enter_confirming"])), None)
check("看不到受護者 → lock_acquired 發恢復", send(sm, clock, car("locked", ["lock_acquired"])),
      ("no_target", RECOVERY))

sm, clock = make()
send(sm, clock, car("waiting", ["notify_no_target"], False))
check("看不到受護者 → 沒收到 lock_acquired，但 locked 且 has_target 也算找回",
      send(sm, clock, car("locked", [], True)), ("no_target", RECOVERY))

print("\n【五、同一拍有多件事】")
sm, clock = make()
notice = None
send(sm, clock, car("waiting", ["notify_no_target"], False))
clock.t += 1
sm.cache.put("jetbot", dict(car("impassable_wait", ["enter_impassable", "notify_impassable", "lock_acquired"], True),
                            ts=clock.t))
notice = sm.tick()
check("卡住 ＋ 找回受護者 → 只送一則，以卡住為主", (notice["category"], notice["kind"]), ("impassable", ALERT))
check("找回受護者併入同一則", [(o["category"], o["kind"]) for o in notice["others"]], [("no_target", RECOVERY)])
check("併入的事項也會生效（不再有未解除的看不到受護者）", sorted(sm.active), ["impassable"])
check("訊息裡同時提到兩件事", "另外，已重新找到受護者。" in format_message(notice), True)

sm, clock = make()
send(sm, clock, car("impassable_wait", ["notify_impassable"], False))
silent(sm, clock, 6)
clock.t += 1
sm.cache.put("jetbot", dict(car("locked", ["resume_follow"]), ts=clock.t))
notice = sm.tick()
check("失聯恢復 ＋ 卡住解除 → 恢復之間依類別優先度：失聯恢復為主",
      [(notice["category"], notice["kind"])] + [(o["category"], o["kind"]) for o in notice["others"]],
      [("offline", RECOVERY), ("impassable", RECOVERY)])

sm, clock = make()
send(sm, clock, car("locked"))
silent(sm, clock, 6)
clock.t += 1
sm.cache.put("jetbot", dict(car("impassable_wait", ["notify_impassable"], False), ts=clock.t))
notice = sm.tick()
check("失聯恢復 ＋ 卡住 → 異常優先於恢復", (notice["category"], notice["kind"]), ("impassable", ALERT))

print("\n【六、失聯偵測】")
sm, clock = make()
check("還沒收到任何資料不判定失聯", silent(sm, clock, 10), [])
send(sm, clock, car("locked"))
check("5 秒內沒資料不算失聯", silent(sm, clock, 4.9), [])
check("超過 5 秒 → 失聯", silent(sm, clock, 0.2), [("offline", ALERT)])
check("失聯持續中不重複通知（冷卻 5 分鐘）", silent(sm, clock, 290), [])
check("失聯滿 5 分鐘再提醒一次", silent(sm, clock, 20), [("offline", REMINDER)])
check("重新收到資料 → 恢復通知", send(sm, clock, car("locked"), after=0.1), ("offline", RECOVERY))
check("失聯恢復的持續時間從最後一筆資料算起", round(sm.notices[-1]["duration_sec"]), 315)

sm, clock = make(offline_sec=2)
send(sm, clock, car("locked"))
check("失聯秒數可設定（2 秒）", silent(sm, clock, 2.2), [("offline", ALERT)])

sm, clock = make()
send(sm, clock, car("locked"))
silent(sm, clock, 3)
check("車上時鐘與車外差很多也不影響失聯判斷（用收到時間）",
      [send(sm, clock, dict(car("locked"), ts=1.0), after=0.1)] + silent(sm, clock, 4.5), [None])

print("\n【七、訊息產生器】")
sm, clock = make()
send(sm, clock, car("impassable_wait", ["notify_impassable"], False, distance=0.42))
notice = sm.notices[-1]
text = format_message(notice)
check("訊息有標題與時間", text.splitlines()[0], "【伴護機器人】" + time.strftime("%H:%M", time.localtime(clock.t)))
check("卡住的訊息寫出距離", "目前車子與前方障礙約 0.4 公尺。" in text, True)
check("notice 是結構化事實，可轉 JSON 給 LLM", sorted(json.loads(json.dumps(notice_facts(notice)))),
      ["car", "car_ts", "category", "duration_sec", "kind", "others", "readings", "since", "time"])
check("notice 帶車上目前狀態", notice["car"]["state"], "impassable_wait")

sm, clock = make()
send(sm, clock, car("impassable_wait", ["notify_impassable"], False, distance=None))
notice = sm.notices[-1]
check("distance_m 為 null → notice 裡沒有這個數值", "distance_m" in notice["readings"], False)
check("speed_scale 不放進 notice（不能用來判斷車子有沒有在動）", sorted(notice["readings"]), ["blocked_prob"])
check("distance_m 為 null → 訊息裡沒有距離", "公尺" in format_message(notice), False)

sm, clock = make()
send(sm, clock, car("locked"))
silent(sm, clock, 5.1)
notice = sm.notices[-1]
check("失聯的 notice 不帶車上狀態（現況不明）", (notice["car"], notice["readings"]), (None, {}))
text = format_message(notice)
check("失聯的訊息寫出多久沒收到資料", ("約 5 秒" in text, "收到" in text), (True, True))
check("失聯的訊息不推測車子照常運作", "照常" in text, False)

check("每種 (類別, 種類) 都有模板",
      all((c, k) in machine.TEMPLATES for c in machine.CATEGORIES for k in (ALERT, REMINDER, RECOVERY)), True)

# 任務六：每種 3 個版本、依通知時間輪流、卡住解除依車上狀態分支
def tnotice(category, kind, t, state="locked", others=()):
    return {"time": t, "since": t - 150.8, "category": category, "kind": kind, "duration_sec": 150.8,
            "car": {"state": state, "has_target": state == "locked", "events": []},
            "readings": {}, "others": list(others)}


all_versions = []
for value in machine.TEMPLATES.values():
    all_versions.extend(value["locked"] + value["not_locked"] if isinstance(value, dict) else value)
check("每種通知都有 3 個版本（卡住解除兩個分支各 3 個）",
      sorted(len(v) for value in machine.TEMPLATES.values()
             for v in (value.values() if isinstance(value, dict) else [value])), [3] * 10)
check("模板不再寫「正在搜尋」「照常運作」", [v for v in all_versions if "搜尋" in v or "照常" in v], [])
T0 = 1790128800.0
check("版本依通知時間輪流", len(set(format_message(tnotice("no_target", RECOVERY, T0 + i)) for i in range(3))), 3)
check("同一則通知每次都是同一個版本",
      format_message(tnotice("no_target", RECOVERY, T0)), format_message(tnotice("no_target", RECOVERY, T0)))
locked = [format_message(tnotice("impassable", RECOVERY, T0 + i, "locked")) for i in range(3)]
waiting = [format_message(tnotice("impassable", RECOVERY, T0 + i, "waiting")) for i in range(3)]
check("卡住解除且仍鎖定 → 寫繼續跟隨", all("跟隨" in t and "沒有" not in t for t in locked), True)
check("卡住解除但沒有鎖定 → 寫等受護者回到前方", all("受護者" in t and "等" in t for t in waiting), True)
check("卡住解除：locked 但看不到人（跟丟寬限中）時不寫繼續跟隨",
      any("繼續跟隨" in format_message(dict(tnotice("impassable", RECOVERY, T0 + i),
                                          car={"state": "locked", "has_target": False, "events": ["resume_follow"]}))
          for i in range(3)), False)
check("卡住解除：狀態不明（car 為 None）時不寫繼續跟隨",
      "繼續跟隨" in format_message(dict(tnotice("impassable", RECOVERY, T0), car=None)), False)
check("併入的失聯解除寫出中斷多久",
      format_message(tnotice("no_target", ALERT, T0, "waiting",
                             others=[{"category": "offline", "kind": RECOVERY, "duration_sec": 13.0}])).endswith(
          "另外，與機器人的連線已恢復（中斷約 13 秒）。"), True)
check("持續時間：一分鐘內寫秒", machine.format_duration(42), "約 42 秒")
check("持續時間：一分鐘到十分鐘以半分鐘為單位",
      [machine.format_duration(s) for s in (60, 75, 150, 165, 330, 599)],
      ["約 1 分鐘", "約 1 分半", "約 2 分半", "約 3 分鐘", "約 5 分半", "約 10 分鐘"])
check("持續時間：十分鐘以上寫整數分鐘", [machine.format_duration(s) for s in (600, 660, 3569)], ["約 10 分鐘", "約 11 分鐘", "約 59 分鐘"])
check("持續時間：一小時以上寫小時", [machine.format_duration(s) for s in (3600, 4800)], ["約 1 小時", "約 1 小時 20 分鐘"])
check("持續時間：剛好 .5 一律進位", [machine.format_duration(s) for s in (2.5, 3.5)], ["約 3 秒", "約 4 秒"])

print("\n【八、輸出】")
received = []


def broken(notice):
    raise RuntimeError("網路斷了")


sm, clock = make(outputs=[lambda n: received.append(n["kind"]) or "ok", broken])
send(sm, clock, car("waiting", ["notify_no_target"], False))
send(sm, clock, car("locked", ["lock_acquired"]))
check("異常、恢復，輸出端都會收到", received, [ALERT, RECOVERY])
check("某個輸出失敗會被記錄，不影響其他輸出", sm.notices[0]["outputs"], ["ok", "失敗：網路斷了"])
check("記錄從車上 ts 到輸出完成的延遲", sm.notices[0]["latency_sec"], 0.0)

line = line_output()
check("LINE 預設 dry-run，不會送出", line(sm.notices[0]).startswith("LINE [dry-run] 【伴護機器人】"), True)
line = line_output(dry_run=True, formatter=lambda n: "LLM：" + n["category"])
check("訊息產生器可替換", line(sm.notices[0]), "LINE [dry-run] LLM：no_target")

print("\n【九、背景執行】")
sm = StateMachine(outputs=[], verbose=False)
sm.start()
sm.cache.put("jetbot", car("waiting", ["notify_no_target"], False))
time.sleep(0.5)
was_running = sm.running
sm.stop()
check("start() 後在背景運作並判斷", (was_running, [(n["category"], n["kind"]) for n in sm.notices]),
      (True, [("no_target", ALERT)]))
check("stop() 後停止", sm.running, False)
check("status() 可以正常產生", "no_target" in sm.status(), True)

sm, clock = make()
sm.cache.put("jetbot", {"state": "locked", "events": "不是陣列", "ts": "昨天"})
sm.tick()
sm.cache.put("jetbot", {"foo": 1})
sm.tick()
check("格式錯誤的資料不會讓狀態機中斷", sm.notices, [])

print("\n" + "=" * 46)
print("通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
if failed:
    print("失敗項目：" + "、".join(failed))
sys.exit(1 if failed else 0)
