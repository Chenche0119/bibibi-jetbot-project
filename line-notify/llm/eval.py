"""比較評測：以假資料情境產生的 notice 當固定題目，比較模板與 LLM 後端。

    uv run --no-project --with mlx-lm python llm/eval.py                      # 模板 vs 本地模型
    uv run --no-project --with mlx-lm python llm/eval.py --backends local,cloud  # 預留第三欄（目前會全部退回模板）
    uv run --no-project python llm/eval.py --backends template               # 不裝 mlx-lm 也能跑（只有模板）

每個後端跑兩輪：
    正式設定  逾時上限照設定（預設 3 秒）→ 退回率與原因，就是實際上線時家屬會收到的
    不限時間  逾時放寬到 60 秒 → 模型本身的輸出品質、每條檢查的通過率、產生耗時
另外量測「阻塞狀態機的時間」：同一段情境分別用直接呼叫與 background_output() 重播，記錄每拍 tick() 的耗時。

結果寫到 llm/eval-results.md（並排輸出全文）；摘要數據請抄進 llm/README.md。
本地模型不花錢，可以重複跑。
"""

import argparse
import collections
import io
import json
import os
import statistics
import sys
import time

LLM_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(LLM_DIR)
sys.path.insert(0, os.path.join(ROOT, "transport"))
sys.path.insert(0, os.path.join(ROOT, "state_machine"))
sys.path.insert(0, LLM_DIR)

import simulator  # noqa: E402
from machine import StateMachine, format_message, line_output  # noqa: E402
from formatter import LLMFormatter, background_output  # noqa: E402
from prompt import RULES, check_output  # noqa: E402

# 情境時間從 2026-09-24 14:00（本機時區）開始，訊息開頭的 HH:MM 才像真的
BASE = time.mktime((2026, 9, 24, 14, 0, 0, 0, 0, -1))
TICK = 0.1
LONG_TIMEOUT = 60.0

# simulator 的情境沒有涵蓋「持續提醒」與「同一拍兩件事」，補上
EXTRA_SCENARIOS = collections.OrderedDict([
    ("long_loss_11min", {
        "description": "long_loss 延長到 11 分鐘：會有 5 分鐘一次的提醒（reminder）",
        "segments": [{"sec": 5, "person": True}, {"sec": 1, "offset": -0.25},
                     {"sec": 660, "person": False}, {"sec": 5, "person": True, "offset": 0.0}],
    }),
    ("stuck_distance_null", {
        "description": "測距讀不到時卡住：notice 沒有 distance_m，訊息不可寫距離",
        "segments": [{"sec": 5, "person": True, "distance": None},
                     {"sec": 0.5, "blocked": True}, {"sec": 1.6, "blocked": False},
                     {"sec": 0.5, "blocked": True}, {"sec": 1.6, "blocked": False},
                     {"sec": 0.5, "blocked": True}, {"sec": 1.6, "blocked": False},
                     {"sec": 10, "blocked": True}],
    }),
])


class FakeClock(object):
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def replay(scenario, outputs=None, tail_sec=1.0, timer=None):
    """重播情境（與 tests/test_state_machine_scenarios.py 相同方式），回傳通知清單。

    timer 有給時，記錄每一拍 tick() 的實際耗時（秒）到 timer 清單：(是否有通知, 耗時)。
    """
    records = simulator.generate(scenario, fps=10, start_ts=BASE)
    clock = FakeClock(BASE)
    sm = StateMachine(outputs=outputs or [], clock=clock, verbose=False)
    end_ms = int(round((simulator.scenario_duration(scenario) + tail_sec) * 1000))
    i = 0
    for now_ms in range(0, end_ms, int(TICK * 1000)):
        clock.t = BASE + now_ms / 1000.0
        while i < len(records) and int(round(records[i]["t"] * 1000)) <= now_ms:
            sm.cache.put("jetbot", records[i]["payload"])
            i += 1
        start = time.time()
        notice = sm.tick()
        if timer is not None:
            timer.append((notice is not None, time.time() - start))
    return sm.notices


def merged_notice():
    """同一拍兩件事：失聯中車子卡住，恢復連線的第一筆就是 enter_impassable → 卡住為主、連線恢復併入。"""
    clock = FakeClock(BASE + 3600)
    sm = StateMachine(outputs=[], clock=clock, verbose=False)
    payload = {"state": "locked", "events": [], "has_target": True,
               "readings": {"blocked_prob": 0.05, "distance_m": 1.1, "speed_scale": 1.0, "target_h": 0.4}}
    sm.cache.put("jetbot", dict(payload, ts=clock.t))
    sm.tick()
    clock.t += 6.0
    sm.tick()                       # 失聯
    clock.t += 7.0
    sm.cache.put("jetbot", {"state": "impassable_wait", "events": ["enter_impassable"], "has_target": False,
                            "ts": clock.t,
                            "readings": {"blocked_prob": 0.93, "distance_m": 0.3, "speed_scale": 0.0,
                                         "target_h": None}})
    sm.tick()
    return [n for n in sm.notices if n["others"]]


# ── 任務七補充的 6 題（docs/tasks/07-model-eval.md 第三節）──
# 前 3 題用情境產生，每個情境只取原本題目沒有的那一則通知
PICK_SCENARIOS = collections.OrderedDict([
    ("left_while_stuck", ({
        "description": "卡住期間受護者離開：解除時 state 仍是 locked 但 has_target 為 false（跟丟寬限中），不可寫繼續跟隨",
        "segments": [{"sec": 5, "person": True}, {"sec": 7, "blocked": True},
                     {"sec": 10, "blocked": False, "person": False}],
    }, ("impassable", "recovery"))),
    ("offline_long", ({
        "description": "失聯超過 5 分鐘：失聯的提醒",
        "segments": [{"sec": 10, "person": True}, {"sec": 400, "offline": True}, {"sec": 10, "offline": False}],
    }, ("offline", "reminder"))),
    ("stuck_long", ({
        "description": "卡住超過 5 分鐘：卡住的提醒（有距離）",
        "segments": [{"sec": 5, "person": True}, {"sec": 330, "blocked": True}, {"sec": 15, "blocked": False}],
    }, ("impassable", "reminder"))),
])

LOCKED = {"state": "locked", "events": [], "has_target": True,
          "readings": {"blocked_prob": 0.05, "distance_m": 1.1, "speed_scale": 1.0, "target_h": 0.4}}
NO_TARGET = {"state": "waiting", "events": [], "has_target": False,
             "readings": {"blocked_prob": 0.05, "distance_m": None, "speed_scale": 1.0, "target_h": None}}


def scripted(steps, start=BASE + 7200):
    """依腳本直接餵資料給狀態機（情境產生不了的組合），回傳所有通知。

    steps 每一步是 (秒數, payload, events)：
        payload 為 None  → 這段時間沒有資料（失聯）
        否則             → 第一筆帶 events，之後每 1 秒送一次心跳（events 為空），與車上的節流相同
    """
    clock = FakeClock(start)
    sm = StateMachine(outputs=[], clock=clock, verbose=False)
    for seconds, payload, events in steps:
        end = clock.t + seconds
        next_send = clock.t
        first = True
        while clock.t < end - 1e-9:
            if payload is not None and clock.t >= next_send - 1e-9:
                sm.cache.put("jetbot", dict(payload, events=list(events) if first else [], ts=clock.t))
                first = False
                next_send += 1.0
            sm.tick()
            clock.t = round(clock.t + TICK, 6)
    return sm.notices


def scripted_notices():
    """後 3 題：[(題目名稱, notice)]，各取最後一則（就是要測的那一則）。"""
    cases = collections.OrderedDict([
        # 失聯中受護者離開：恢復連線的第一筆就是 notify_no_target → 看不到受護者為主、連線恢復併入
        ("reconnect_no_target", [(3, LOCKED, []), (8, None, []), (1, NO_TARGET, ["notify_no_target"])]),
        # 看不到受護者期間失聯，恢復連線時已重新跟上 → 連線恢復為主、找回受護者併入
        ("reconnect_relocked", [(3, LOCKED, []), (5, NO_TARGET, ["notify_no_target"]), (8, None, []),
                                (1, LOCKED, ["lock_acquired"])]),
        # 看不到受護者 1 小時 20 分鐘後找回：「約 1 小時 20 分鐘」
        ("long_loss_80min", [(3, LOCKED, []), (4800, NO_TARGET, ["notify_no_target"]),
                             (1, LOCKED, ["lock_acquired"])]),
    ])
    return [("{}#1".format(name), scripted(steps)[-1]) for name, steps in cases.items()]


def collect_notices():
    """所有題目：[(題目名稱, notice)]。前 16 題與任務六相同，後 6 題是任務七補充的。"""
    items = []
    scenarios = collections.OrderedDict(sorted(simulator.SCENARIOS.items()))
    scenarios.update(EXTRA_SCENARIOS)
    for name, scenario in scenarios.items():
        for n, notice in enumerate(replay(scenario)):
            items.append(("{}#{}".format(name, n + 1), notice))
    for n, notice in enumerate(merged_notice()):
        items.append(("merged_offline_impassable#{}".format(n + 1), notice))
    for name, (scenario, (category, kind)) in PICK_SCENARIOS.items():
        picked = [n for n in replay(scenario) if (n["category"], n["kind"]) == (category, kind)]
        items.append(("{}#1".format(name), picked[0]))
    items.extend(scripted_notices())
    return items, list(scenarios) + list(PICK_SCENARIOS) + ["scripted×3"]


def run_backend(fmt, notices, timeout):
    """用一個後端跑完所有題目，回傳每題的紀錄。模型只載入一次，兩輪只改逾時上限。"""
    fmt.timeout = timeout
    rows = []
    for label, notice in notices:
        fmt.wait_idle(LONG_TIMEOUT)   # 實際上通知之間隔好幾秒，上一則逾時的產生早就停了；這裡等它停下再出題
        fmt(notice)
        row = dict(fmt.last)
        row["label"] = label
        if row["llm_text"]:
            row["checks"] = check_output(row["llm_text"], notice)
        rows.append(row)
    return rows


def measure_blocking(fmt, timeout):
    """同一段情境（卡住＋失聯）分別直接呼叫與交給背景，量測每拍 tick() 耗時。"""
    scenario = {"description": "阻塞量測：卡住 → 解除 → 失聯 → 恢復",
                "segments": simulator.SCENARIOS["detour_stuck"]["segments"][:8]
                + [{"sec": 10, "blocked": False}, {"sec": 8, "offline": True}, {"sec": 3, "offline": False}]}
    fmt.timeout = timeout
    out = {}
    for mode in ("direct", "background"):
        output = line_output(dry_run=True, formatter=fmt)
        if mode == "background":
            output = background_output(output, formatter=fmt)
        timer = []
        replay(scenario, outputs=[output], timer=timer)
        if mode == "background":
            output.join()
        with_notice = [t for has, t in timer if has]
        without = [t for has, t in timer if not has]
        out[mode] = {"notices": len(with_notice),
                     "notice_tick_max_ms": round(max(with_notice) * 1000, 1) if with_notice else None,
                     "notice_tick_median_ms": round(statistics.median(with_notice) * 1000, 1) if with_notice else None,
                     "other_tick_max_ms": round(max(without) * 1000, 2) if without else None}
        if mode == "background":
            out[mode]["background_done_sec"] = [r["done_sec"] for r in output.results]
    return out


def summarize(rows):
    """延遲、退回率與原因、各條檢查的通過數。"""
    gen = [r["gen_sec"] for r in rows if r["gen_sec"] is not None]
    elapsed = [r["elapsed_sec"] for r in rows]
    reasons = collections.Counter(r["reason"] for r in rows if r["used"] == "template")
    rule_fail = collections.Counter(rule for r in rows for rule in r.get("checks", []))
    checked = sum(1 for r in rows if "checks" in r)
    return {
        "total": len(rows),
        "used_llm": sum(1 for r in rows if r["used"] != "template"),
        "fallback": sum(1 for r in rows if r["used"] == "template"),
        "reasons": dict(reasons),
        "gen_median": round(statistics.median(gen), 2) if gen else None,
        "gen_max": round(max(gen), 2) if gen else None,
        "elapsed_median": round(statistics.median(elapsed), 3) if elapsed else None,
        "elapsed_max": round(max(elapsed), 3) if elapsed else None,
        "checked": checked,
        "rule_pass": collections.OrderedDict((rule, checked - rule_fail[rule]) for rule, _ in RULES),
    }


def reason_text(row):
    if row["used"] != "template":
        return "✅ 採用"
    if row["reason"] == "check":
        return "退回：檢查未過（{}）".format("、".join(row["failed_rules"]))
    return "退回：{}".format(row["reason"])


def cell(text):
    return (text or "—").replace("\n", "<br>").replace("|", "｜")


def report(notices, scenario_names, results, blocking, args):
    lines = ["# LLM 轉譯比較評測結果", "",
             "- 產生時間：{}".format(time.strftime("%Y-%m-%d %H:%M")),
             "- 指令：`{}`".format(" ".join(["llm/eval.py"] + sys.argv[1:])),
             "- 題目：{} 個情境重播出的 {} 則 notice（另含一則人工組出的「同一拍兩件事」）".format(
                 len(scenario_names), len(notices)),
             "- 逾時上限（正式設定）：{:g} 秒；不限時間的一輪放寬到 {:g} 秒".format(args.timeout, LONG_TIMEOUT), ""]

    for name, res in results.items():
        info = res["info"]
        lines += ["## 後端：{}".format(name), ""]
        if info.get("unavailable"):
            lines += ["無法使用：{}（全部退回模板）".format(info["unavailable"]), ""]
            continue
        lines += ["| 項目 | 數值 |", "|---|---|"]
        for key, label in [("model", "模型"), ("load_sec", "模型載入（秒）"), ("warmup_sec", "預熱（秒，含建立提示詞快取）"),
                           ("ready_sec", "從建立到可用（秒）"), ("prefix_tokens", "快取的固定前綴（token）"),
                           ("rss_mb", "程序最大常駐記憶體（MB）"), ("mlx_active_mb", "MLX 配置記憶體（MB）"),
                           ("mlx_peak_mb", "MLX 記憶體高峰（MB）")]:
            if key in info:
                lines.append("| {} | {} |".format(label, info[key]))
        lines.append("")
        for mode, title in [("strict", "正式設定（逾時 {:g} 秒）".format(args.timeout)),
                            ("relaxed", "不限時間（{:g} 秒）".format(LONG_TIMEOUT))]:
            s = res[mode]["summary"]
            lines += ["### {}".format(title), "",
                      "| 項目 | 數值 |", "|---|---|",
                      "| 採用 LLM | {} / {} |".format(s["used_llm"], s["total"]),
                      "| 退回模板 | {} / {}（{}） |".format(
                          s["fallback"], s["total"],
                          "、".join("{} {}".format(k, v) for k, v in sorted(s["reasons"].items())) or "無"),
                      "| 模型產生耗時 中位數／最大（秒） | {} ／ {} |".format(s["gen_median"], s["gen_max"]),
                      "| 呼叫 formatter 耗時 中位數／最大（秒） | {} ／ {} |".format(
                          s["elapsed_median"], s["elapsed_max"]), ""]
            if mode == "relaxed":
                lines += ["各條檢查通過數（共 {} 則有模型輸出）：".format(s["checked"]), "",
                          "| 規則 | 說明 | 通過 |", "|---|---|---|"]
                for rule, desc in RULES:
                    lines.append("| `{}` | {} | {} / {} |".format(rule, desc, s["rule_pass"][rule], s["checked"]))
                lines.append("")

    if blocking:
        lines += ["## 阻塞狀態機的時間", "",
                  "同一段情境（卡住 → 解除 → 失聯 → 恢復）重播，量測每一拍 `tick()` 的實際耗時。", "",
                  "| 接法 | 有通知的拍 中位數／最大（ms） | 沒有通知的拍 最大（ms） | 背景完成耗時（秒） |",
                  "|---|---|---|---|"]
        for mode, label in [("direct", "`line_output(formatter=fmt)`"),
                            ("background", "`background_output(line_output(formatter=fmt))`")]:
            b = blocking[mode]
            lines.append("| {} | {} ／ {} | {} | {} |".format(
                label, b["notice_tick_median_ms"], b["notice_tick_max_ms"], b["other_tick_max_ms"],
                b.get("background_done_sec", "—")))
        lines.append("")

    backends = list(results)
    lines += ["## 並排輸出", "",
              "「模板（參考答案）」欄是 Claude 產生、人工審核的模板（docs/tasks/06-template-upgrade.md），"
              "也是 LLM 範例的來源，用來對照 LLM 寫的意思對不對。"
              "LLM 欄是**不限時間**那一輪的模型輸出（沒通過檢查的也列出，方便看錯在哪），"
              "最後一欄是正式設定下家屬實際會收到的版本。", ""]
    header = ["題目", "模板（參考答案）"]
    for name in backends:
        header += [name + "（模型輸出）", name + " 檢查／耗時", name + "（正式設定）"]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for i, (label, notice) in enumerate(notices):
        row = [label + "<br>`{}/{}{}`".format(notice["category"], notice["kind"],
                                               "".join("＋{}/{}".format(o["category"], o["kind"])
                                                       for o in notice["others"])),
               cell(format_message(notice))]
        for name in backends:
            relaxed = results[name]["relaxed"]["rows"][i]
            strict = results[name]["strict"]["rows"][i]
            row += [cell(relaxed["llm_text"]),
                    "{}<br>{} 秒".format(reason_text(relaxed), relaxed["gen_sec"]),
                    ("（LLM）" if strict["used"] != "template" else "（模板，{}）".format(strict["reason"]))]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="比較模板與 LLM 後端的訊息")
    parser.add_argument("--backends", default="local", help="逗號分隔：local、cloud、template（模板一定會列出）")
    parser.add_argument("--timeout", type=float, default=3.0, help="正式設定的逾時秒數")
    parser.add_argument("--model", default=None, help="本地模型名稱（預設見 backends.py）")
    parser.add_argument("--out", default=os.path.join(LLM_DIR, "eval-results.md"))
    parser.add_argument("--json", default=None, help="另存原始紀錄（JSON）")
    parser.add_argument("--no-blocking", action="store_true", help="不量測阻塞時間")
    args = parser.parse_args(argv)

    notices, scenario_names = collect_notices()
    print("題目：{} 則 notice".format(len(notices)))
    results = collections.OrderedDict()
    blocking = None
    for name in [b.strip() for b in args.backends.split(",") if b.strip()]:
        start = time.time()
        fmt = LLMFormatter(backend=name, timeout=args.timeout, model=args.model, verbose=False)
        fmt.wait_ready(300)
        info = fmt.info
        info["ready_sec"] = round(time.time() - start, 2)
        info["unavailable"] = fmt.unavailable
        res = {"info": info}
        for mode, timeout in [("strict", args.timeout), ("relaxed", LONG_TIMEOUT)]:
            print("[{}] {}（逾時 {:g} 秒）...".format(name, mode, timeout))
            rows = run_backend(fmt, notices, timeout)
            res[mode] = {"rows": rows, "summary": summarize(rows)}
            print("    採用 {used_llm}/{total}，退回 {reasons}，產生耗時中位數 {gen_median} 秒、最大 {gen_max} 秒".format(
                **res[mode]["summary"]))
        results[name] = res
        if blocking is None and fmt.ready.is_set() and not args.no_blocking:
            print("[{}] 量測阻塞時間...".format(name))
            blocking = measure_blocking(fmt, args.timeout)
            print("    {}".format(json.dumps(blocking, ensure_ascii=False)))
        fmt.close()

    text = report(notices, scenario_names, results, blocking, args)
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print("結果已寫到 {}".format(args.out))
    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as f:
            json.dump({"results": results, "blocking": blocking}, f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
