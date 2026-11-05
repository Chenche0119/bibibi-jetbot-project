"""整合測試：以任務二的假資料情境（transport/simulator.py）當輸入，驗證車外狀態機會發出哪些通知。

不需要 broker、硬體、網路；用假時鐘重播，161 秒的情境一瞬間跑完。

    uv run python tests/test_state_machine_scenarios.py

重播方式：車外每 0.1 秒判斷一拍（與 TICK_HZ 相同）；每拍先把「這個時間點以前車上送出的資料」
寫進覆蓋快取，再判斷。ts 用模擬時間，車外時鐘與車上時鐘相同。
"""

import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "state_machine"))
sys.path.insert(0, os.path.join(ROOT, "transport"))

import simulator  # noqa: E402
from machine import (ALERT, RECOVERY, REMINDER, StateMachine,  # noqa: E402
                     format_message, line_output)

passed = []
failed = []
TICK_MS = 100


class FakeClock(object):
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


def replay(scenario, tail_sec=1.0):
    """重播一個情境，回傳 (狀態機, 通知清單 [(時間, 類別, 種類)])。

    tail_sec：情境結束後再多判斷幾秒。情境結束＝車上不再送資料，超過失聯門檻就會判定失聯，
    所以預設只多跑 1 秒。
    """
    records = simulator.generate(scenario, fps=10, start_ts=0.0)
    clock = FakeClock()
    sm = StateMachine(outputs=[line_output(dry_run=True)], clock=clock, verbose=False)
    end_ms = int(round((simulator.scenario_duration(scenario) + tail_sec) * 1000))
    i = 0
    for now_ms in range(0, end_ms, TICK_MS):
        clock.t = now_ms / 1000.0
        while i < len(records) and int(round(records[i]["t"] * 1000)) <= now_ms:
            sm.cache.put("jetbot", records[i]["payload"])
            i += 1
        sm.tick()
    got = [(round(n["time"], 1), n["category"], n["kind"]) for n in sm.notices]
    return sm, got


def texts(sm):
    return [format_message(n) for n in sm.notices]


print("\n【normal】正常跟隨")
sm, got = replay("normal")
check("完全不通知", got, [])

print("\n【brief_loss】短暫跟丟 1.5 秒")
sm, got = replay("brief_loss")
check("不通知（2 秒寬限內找回）", got, [])

print("\n【long_loss】長時間跟丟（notify_no_target 在 9、69、129 秒）")
sm, got = replay("long_loss")
check("一則看不到受護者、找回後一則恢復；60 秒重發不再推播",
      got, [(9.0, "no_target", ALERT), (156.8, "no_target", RECOVERY)])
check("看不到受護者從 has_target 變 false 起算（6.0 秒）", sm.notices[0]["duration_sec"], 3.0)
check("恢復訊息寫出中斷多久（150.8 秒）", "約 2 分半" in texts(sm)[1], True)

LONGER = {
    "description": "long_loss 延長：消失 11 分鐘，驗證每 5 分鐘最多一則",
    "segments": [
        {"sec": 5, "person": True},
        {"sec": 1, "offset": -0.25},
        {"sec": 660, "person": False},
        {"sec": 5, "person": True, "offset": 0.0},
    ],
}
sm, got = replay(LONGER)
check("持續 11 分鐘：首次立即，之後每 5 分鐘最多一則（跟著 60 秒重發的節奏）",
      got, [(9.0, "no_target", ALERT), (309.0, "no_target", REMINDER),
            (609.0, "no_target", REMINDER), (666.8, "no_target", RECOVERY)])

print("\n【detour_ok】繞行成功")
sm, got = replay("detour_ok")
check("不通知", got, [])
check("繞行事件有記錄", [x["event"] for x in sm.log if "detour" in x["event"]],
      ["enter_detour", "detour_advance", "detour_done"])

print("\n【detour_stuck】繞行失敗卡住")
sm, got = replay("detour_stuck")
check("一則卡住、解除後一則恢復；60 秒重發不再推播",
      got, [(11.3, "impassable", ALERT), (145.2, "impassable", RECOVERY)])
check("卡住的訊息寫出距離（1.2 公尺）", "目前車子與前方障礙約 1.2 公尺。" in texts(sm)[0], True)

print("\n【switch_confirm】倒數中換人")
sm, got = replay("switch_confirm")
check("不通知", got, [])
check("同一筆兩個事件都有記錄", [x["event"] for x in sm.log][:3],
      ["enter_confirming", "cancel_confirming", "enter_confirming"])

print("\n【distance_null】測距讀不到")
sm, got = replay("distance_null")
check("不通知", got, [])
# 同一段資料若剛好卡住，訊息也不得出現距離
NULL_STUCK = {
    "description": "測距讀不到時卡住",
    "segments": [
        {"sec": 5, "person": True, "distance": None},
        {"sec": 0.5, "blocked": True}, {"sec": 1.6, "blocked": False},
        {"sec": 0.5, "blocked": True}, {"sec": 1.6, "blocked": False},
        {"sec": 0.5, "blocked": True}, {"sec": 1.6, "blocked": False},
        {"sec": 10, "blocked": True},
    ],
}
sm, got = replay(NULL_STUCK)
check("distance_m 為 null 時卡住：照樣通知", [(c, k) for _, c, k in got], [("impassable", ALERT)])
check("distance_m 為 null 時卡住：訊息裡沒有距離", any("公尺" in t for t in texts(sm)), False)

print("\n【offline】失聯 12 秒（車外看到 13 秒空窗：最後一筆 9.8 秒，下一筆 22.8 秒）")
sm, got = replay("offline")
check("一則失聯、恢復後一則恢復", got, [(14.9, "offline", ALERT), (22.8, "offline", RECOVERY)])
check("失聯訊息不帶車上狀態", sm.notices[0]["car"], None)
check("恢復訊息寫出中斷多久", "約 13 秒" in texts(sm)[1], True)

sm, got = replay("normal", tail_sec=6.0)
check("情境結束後車上不再送資料 → 超過 5 秒判定失聯", got, [(64.9, "offline", ALERT)])

print("\n" + "=" * 46)
print("通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
if failed:
    print("失敗項目：" + "、".join(failed))
sys.exit(1 if failed else 0)
