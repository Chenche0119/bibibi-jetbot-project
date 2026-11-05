"""simulator.py 的行為測試：逐條對照 docs/規格回覆-2026-09-19.md。不需要 broker、硬體、paho-mqtt。

    uv run python tests/test_simulator.py
"""

import io
import json
import os
import sys
import tempfile

TRANSPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "transport")
sys.path.insert(0, TRANSPORT_DIR)

import simulator as sim  # noqa: E402

passed = []
failed = []


def check(name, actual, expected):
    if actual == expected:
        passed.append(name)
        print("  ✅ {}".format(name))
    else:
        failed.append(name)
        print("  ❌ {}\n       預期 {!r}，實際 {!r}".format(name, expected, actual))



def close(a, b):
    return a is not None and b is not None and abs(a - b) < 1e-6


def times_of(records, event):
    return [r["t"] for r in records if event in r["payload"]["events"]]


def first(records, event):
    ts = times_of(records, event)
    return ts[0] if ts else None


def gaps(values):
    return [round(b - a, 6) for a, b in zip(values, values[1:])]


def run(segments, **kw):
    return sim.generate({"segments": segments}, **kw)


ALL = {name: sim.generate(name) for name in sorted(sim.SCENARIOS)}

print("\n【一、格式：與組員規格第三節相同】")
bad_state, bad_event, bad_shape = [], [], []
for name, records in ALL.items():
    for r in records:
        p = r["payload"]
        if p["state"] not in sim.STATES:
            bad_state.append((name, p["state"]))
        if not isinstance(p["events"], list):
            bad_shape.append((name, "events"))
        for ev in p["events"]:
            if ev not in sim.EVENTS:
                bad_event.append((name, ev))
        if sorted(p) != ["events", "has_target", "readings", "state"]:
            bad_shape.append((name, sorted(p)))
        if sorted(p["readings"]) != ["blocked_prob", "distance_m", "speed_scale", "target_h"]:
            bad_shape.append((name, sorted(p["readings"])))
        if not isinstance(p["has_target"], bool):
            bad_shape.append((name, "has_target"))
        if p["has_target"] != (p["readings"]["target_h"] is not None):
            bad_shape.append((name, "target_h 與 has_target 不一致"))
check("所有情境的 state 都在五個狀態內", bad_state, [])
check("所有情境的事件都在 13 種內", bad_event, [])
check("欄位與型別符合規格（events 為陣列、無事件為 []、無目標時 target_h 為 null）", bad_shape, [])
check("規格的 13 種事件都有情境涵蓋",
      sorted(set(ev for rs in ALL.values() for r in rs for ev in r["payload"]["events"])
             | set(ev for r in run([{"sec": 3, "person": True}, {"sec": 2, "who": "B"}])
                   for ev in r["payload"]["events"])),
      sorted(sim.EVENTS))
check("預設不含 ts（即時模式交給 sender.py 補）",
      any("ts" in r["payload"] for rs in ALL.values() for r in rs), False)

print("\n【二、確認倒數 0.75 秒 → locked，發 lock_acquired】")
rs = ALL["normal"]
t_enter, t_lock = first(rs, "enter_confirming"), first(rs, "lock_acquired")
check("enter_confirming 時為 confirming", rs[0]["payload"]["state"], "confirming")
check("lock_acquired 在倒數滿 0.75 秒後的第一幀（0.8 秒）",
      close(t_lock - t_enter, 0.8), True)
check("lock_acquired 那一筆 state 為 locked",
      [r["payload"]["state"] for r in rs if "lock_acquired" in r["payload"]["events"]], ["locked"])
rs = run([{"sec": 0.5, "person": True}, {"sec": 1, "offset": 0.3}, {"sec": 1, "offset": 0.0}])
check("倒數中條件消失（偏離中央）→ cancel_confirming、回 waiting",
      [(r["t"], r["payload"]["state"]) for r in rs if "cancel_confirming" in r["payload"]["events"]],
      [(0.5, "waiting")])
rs = run([{"sec": 3, "person": True, "h": 0.25}])
check("框高 < 0.30 不進入倒數", [r["payload"]["state"] for r in rs], ["waiting"] * len(rs))
rs = run([{"sec": 3, "person": True, "identity": False}])
check("身分比對沒過不進入倒數", times_of(rs, "enter_confirming"), [])

print("\n【三、倒數中換人：同一筆兩個事件，計時歸零】")
rs = ALL["switch_confirm"]
pair = [r for r in rs if len(r["payload"]["events"]) == 2]
check("同一筆出現 [cancel_confirming, enter_confirming]",
      [r["payload"]["events"] for r in pair], [["cancel_confirming", "enter_confirming"]])
check("換人後狀態仍為 confirming", pair[0]["payload"]["state"], "confirming")
check("從換人那一刻重新倒數 0.75 秒（1.3 秒鎖定）", close(first(rs, "lock_acquired"), 1.3), True)

print("\n【四、跟丟：寬限 2 秒內仍 locked、has_target=false】")
rs = ALL["brief_loss"]
t_search = first(rs, "searching")
check("短暫跟丟：消失那一幀發 searching（偏移 0.3 >= 0.12）", close(t_search, 7.0), True)
check("短暫跟丟：searching 那一筆 state=locked、has_target=false",
      [(r["payload"]["state"], r["payload"]["has_target"]) for r in rs if r["t"] == t_search],
      [("locked", False)])
check("短暫跟丟（1.5 秒 < 2 秒）不出現 lock_lost", times_of(rs, "lock_lost"), [])
check("短暫跟丟：全程沒有離開 locked（鎖定之後）",
      set(r["payload"]["state"] for r in rs if r["t"] >= 0.8), {"locked"})
check("短暫跟丟：寬限中 has_target 都是 false",
      set(r["payload"]["has_target"] for r in rs if 7.0 <= r["t"] < 8.5), {False})

rs = ALL["long_loss"]
t_gone = first(rs, "searching")
t_lost = first(rs, "lock_lost")
check("長時間跟丟：lock_lost 恰好在消失後 2.0 秒", close(t_lost - t_gone, 2.0), True)
check("長時間跟丟：寬限中每一筆都是 locked＋has_target=false",
      set((r["payload"]["state"], r["payload"]["has_target"])
          for r in rs if t_gone <= r["t"] < t_lost), {("locked", False)})
check("長時間跟丟：lock_lost 那一筆 state 變 waiting",
      [r["payload"]["state"] for r in rs if r["t"] == t_lost], ["waiting"])
rs = run([{"sec": 3, "person": True}, {"sec": 1, "offset": 0.05}, {"sec": 3, "person": False}])
check("消失前偏移 0.05 < 0.12：不發 searching", times_of(rs, "searching"), [])
rs = run([{"sec": 3, "person": True}, {"sec": 1, "offset": -0.12}, {"sec": 3, "person": False}])
check("消失前偏移 -0.12（絕對值 = 0.12）：發 searching", len(times_of(rs, "searching")), 1)

print("\n【五、畫面無人 3 秒 → notify_no_target，每 60 秒重發】")
rs = ALL["long_loss"]
notes = times_of(rs, "notify_no_target")
check("第一次 notify_no_target 在畫面無人滿 3.0 秒時", close(notes[0] - t_gone, 3.0), True)
check("無人期間（150 秒）共發 3 次", len(notes), 3)
check("重發間隔恰好 60 秒", gaps(notes), [60.0, 60.0])
check("人回來後不再發", [t for t in notes if t > 156], [])
rs = run([{"sec": 2.9, "person": False}, {"sec": 1, "person": True}])
check("無人不滿 3 秒不發", times_of(rs, "notify_no_target"), [])

print("\n【六、繞行成功】")
rs = ALL["detour_ok"]
seq = [ev for r in rs for ev in r["payload"]["events"]
       if ev in ("enter_detour", "detour_advance", "detour_done")]
check("事件順序 enter_detour → detour_advance → detour_done",
      seq, ["enter_detour", "detour_advance", "detour_done"])
check("轉到淨空後強制直行 1.5 秒才 detour_done",
      close(first(rs, "detour_done") - first(rs, "detour_advance"), 1.5), True)
check("繞行中 state 為 detour",
      set(r["payload"]["state"] for r in rs
          if first(rs, "enter_detour") <= r["t"] < first(rs, "detour_done")), {"detour"})
check("仍有鎖定目標 → detour_done 回 locked",
      [r["payload"]["state"] for r in rs if "detour_done" in r["payload"]["events"]], ["locked"])
rs = run([{"sec": 1, "person": False}, {"sec": 0.5, "blocked": True}, {"sec": 3, "blocked": False}])
check("沒有鎖定目標 → detour_done 回 waiting",
      [r["payload"]["state"] for r in rs if "detour_done" in r["payload"]["events"]], ["waiting"])
check("被擋的幀 has_target=false、blocked_prob 高",
      [(r["payload"]["has_target"], r["payload"]["readings"]["blocked_prob"])
       for r in ALL["detour_ok"] if "enter_detour" in r["payload"]["events"]], [(False, 0.93)])

print("\n【七、進入 impassable_wait 的三個條件】")
rs = ALL["detour_stuck"]
enter = [r for r in rs if "enter_impassable" in r["payload"]["events"]]
check("(c) 繞行 3 次後第 4 次被擋 → enter_impassable",
      (len(times_of(rs, "enter_detour")), [r["reason"] for r in enter]), (3, ["detour_attempts"]))
check("進入那一筆同時發 notify_impassable（同規格範例三）",
      enter[0]["payload"]["events"], ["enter_impassable", "notify_impassable"])
imp_notes = times_of(rs, "notify_impassable")
check("notify_impassable 每 60 秒重發（130 秒內 3 次）", gaps(imp_notes), [60.0, 60.0])
check("無法通過期間 state 皆為 impassable_wait",
      set(r["payload"]["state"] for r in rs
          if enter[0]["t"] <= r["t"] < first(rs, "resume_follow")), {"impassable_wait"})
resume = [r for r in rs if "resume_follow" in r["payload"]["events"]]
check("障礙解除 → resume_follow，仍有鎖定目標回 locked",
      [r["payload"]["state"] for r in resume], ["locked"])
check("解除要等 8 秒視窗內被擋比例不再過半（清除後 3.9 秒）",
      close(resume[0]["t"] - 141.3, 3.9), True)

rs = run([{"sec": 1, "person": False}, {"sec": 7, "blocked": True}, {"sec": 6, "blocked": False}])
check("沒有鎖定目標 → resume_follow 回 waiting",
      [r["payload"]["state"] for r in rs if "resume_follow" in r["payload"]["events"]], ["waiting"])

rs = sim.generate("blocked_long", params={"block_ratio_threshold": 1.0})
enter = [r for r in rs if "enter_impassable" in r["payload"]["events"]]
check("(a) 連續被擋恰好 5.0 秒 → enter_impassable（關掉條件 b 單獨驗證）",
      [(r["t"], r["reason"]) for r in enter], [(10.0, "blocked_seconds")])
rs = ALL["blocked_long"]
check("照規格參數，連續被擋時 (b) 在 4.1 秒先成立（見 README 規格疑問）",
      [(r["t"], r["reason"]) for r in rs if "enter_impassable" in r["payload"]["events"]],
      [(9.0, "block_ratio")])
rs = ALL["oscillate"]
enter = [r for r in rs if "enter_impassable" in r["payload"]["events"]]
check("(b) 擺盪：8 秒內被擋比例過半 → enter_impassable",
      [r["reason"] for r in enter], ["block_ratio"])
check("(b) 擺盪：觸發時繞行次數還沒超過 3 次", len(times_of(rs, "enter_detour")) <= 3, True)

print("\n【八、relocked、測距 null】")
rs = run([{"sec": 3, "person": True}, {"sec": 2, "who": "B"}])
check("鎖定中框對不上（換成 B）→ relocked 一次、仍 locked",
      [(r["t"], r["payload"]["state"]) for r in rs if "relocked" in r["payload"]["events"]],
      [(3.0, "locked")])
rs = ALL["distance_null"]
nulls = [r for r in rs if r["payload"]["readings"]["distance_m"] is None]
check("讀不到時 distance_m 為 null（欄位不省略）", len(nulls) >= 10, True)
check("讀不到的期間是 5～15 秒", (nulls[0]["t"] >= 5, nulls[-1]["t"] < 15), (True, True))
check("distance_m 單位公尺（1.2 不是 120）", rs[0]["payload"]["readings"]["distance_m"], 1.2)

print("\n【九、節流：與車上 report_to_caregiver() 完全相同】")
bad_gap, missed = [], []
for name, records in ALL.items():
    if name == "offline":
        continue
    for a, b in zip(records, records[1:]):
        g = round(b["t"] - a["t"], 6)
        quiet = not b["payload"]["events"] and b["payload"]["state"] == a["payload"]["state"]
        if quiet and g != 1.0:
            bad_gap.append((name, a["t"], g))
        if g > 1.0:
            bad_gap.append((name, a["t"], g))
    # 逐幀重跑狀態機，確認每一個「狀態改變或有事件」的幀都有送出
    frames = sim.expand_frames(name)
    fs = sim.FollowStateSim(100)
    emitted = set(round(r["t"], 3) for r in records)
    prev = None
    for now, frame in frames:
        evs = fs.step(now, frame)
        if (evs or fs.state != prev) and round(now / 1000.0, 3) not in emitted:
            missed.append((name, now))
        prev = fs.state
check("沒有事件且狀態沒變時，恰好每 1.0 秒送一筆；任何間隔都不超過 1 秒", bad_gap, [])
check("狀態改變或有事件的每一幀都立刻送出", missed, [])
rs = ALL["normal"]
check("正常跟隨：鎖定後只有心跳（沒有任何事件）",
      [r["payload"]["events"] for r in rs if r["t"] > 0.8], [[]] * (len(rs) - 2))
check("正常跟隨 60 秒約 60 筆", len(rs), 61)

print("\n【十、失聯】")
rs = ALL["offline"]
g = gaps([r["t"] for r in rs])
# 最後一筆在 9.8 秒，失聯 10～22 秒，恢復後下一次心跳在 22.8 秒
check("中途停止發送 12 秒（車外看到的空窗 = 12 秒＋心跳相位 = 13.0 秒）", max(g), 13.0)
full = sim.generate("offline", include_offline=True)
check("include_offline=True 時標出送不出去的筆數",
      len([r for r in full if not r["delivered"]]), 12)
check("失聯期間車上的節流照走（除了鎖定那一筆與空窗，其餘都是整秒心跳）",
      g.count(1.0), len(g) - 2)

print("\n【十一、可重現】")
check("同一情境重跑兩次，序列完全相同",
      all(sim.generate(n) == ALL[n] for n in ALL), True)
tmp = tempfile.mkdtemp()
p1, p2 = os.path.join(tmp, "a.jsonl"), os.path.join(tmp, "b.jsonl")
sim.write_jsonl(sim.generate("long_loss", start_ts=sim.DEFAULT_START_TS), p1)
sim.write_jsonl(sim.generate("long_loss", start_ts=sim.DEFAULT_START_TS), p2)
check("寫出的 .jsonl 逐位元相同", io.open(p1, "rb").read() == io.open(p2, "rb").read(), True)

print("\n【十二、輸出與時間】")
lines = [json.loads(l) for l in io.open(p1, encoding="utf-8")]
check(".jsonl 每行一筆，筆數等於序列長度", len(lines), len(ALL["long_loss"]))
check(".jsonl 每筆 = payload ＋ ts（規格回覆第五節格式）",
      all(sorted(l) == ["events", "has_target", "readings", "state", "ts"] for l in lines), True)
check("ts 用模擬時間：間隔等於 t 的間隔",
      gaps([l["ts"] for l in lines]) == gaps([r["t"] for r in ALL["long_loss"]]), True)

got = []
sim.play(ALL["long_loss"], got.append, speed=0)
check("加速／盡快播放時由產生器填 ts，間隔為模擬時間（重發恰好 60 秒）",
      gaps([p["ts"] for p in got if "notify_no_target" in p["events"]]), [60.0, 60.0])
got = []
sim.play(ALL["switch_confirm"][:3], got.append, speed=1000.0)
check("10 倍速這類加速同樣填 ts", all("ts" in p for p in got), True)
got = []
sim.play(ALL["switch_confirm"][:2], got.append, speed=1.0)
check("即時模式不填 ts（交給 sender.py）", any("ts" in p for p in got), False)
check("play() 不修改原本的序列", "ts" in ALL["switch_confirm"][0]["payload"], False)

out = os.path.join(tmp, "cli.jsonl")
old = sys.stderr
sys.stderr = io.StringIO()
try:
    sim.main(["detour_ok", "--out", out])
finally:
    sys.stderr = old
check("命令列 --out 寫檔", len(io.open(out, encoding="utf-8").readlines()), len(ALL["detour_ok"]))

sc_path = os.path.join(tmp, "my.json")
with io.open(sc_path, "w", encoding="utf-8") as f:
    json.dump({"description": "自訂", "segments": [{"sec": 2, "person": True}]}, f)
check("可從 .json 檔載入情境",
      len(sim.generate(sim.load_scenario_file(sc_path))), 3)
try:
    sim.generate({"segments": [{"sec": 1, "persn": True}]})
    check("情境欄位打錯字會報錯", False, True)
except ValueError:
    check("情境欄位打錯字會報錯", True, True)

print("\n通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
sys.exit(1 if failed else 0)
