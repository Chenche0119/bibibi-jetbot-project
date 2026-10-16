"""LLM 訊息產生器的測試。不載入模型、不呼叫 API、不需要網路：後端一律用假的。

    uv run python tests/test_llm_formatter.py

涵蓋 docs/tasks/04-llm.md 的完成標準：接上 line_output()、後端切換、各種退回模板（只提示一次）、
輸出檢查的每一條、不阻塞狀態機的背景輸出。
"""

import contextlib
import io
import json
import os
import sys
import threading
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "state_machine"))
sys.path.insert(0, os.path.join(ROOT, "transport"))
sys.path.insert(0, os.path.join(ROOT, "llm"))

import backends  # noqa: E402
import formatter as fm  # noqa: E402
import prompt  # noqa: E402
from formatter import LLMFormatter, background_output, safe_template  # noqa: E402
import machine  # noqa: E402
from machine import StateMachine, format_message, line_output  # noqa: E402

passed = []
failed = []

T = time.mktime((2026, 9, 24, 14, 27, 0, 0, 0, -1))   # 通知時間 14:27
HEADER = "【伴護機器人】14:27"


def check(name, actual, expected):
    if actual == expected:
        passed.append(name)
        print("  ✅ {}".format(name))
    else:
        failed.append(name)
        print("  ❌ {}\n       預期 {!r}，實際 {!r}".format(name, expected, actual))


def notice(category="impassable", kind="alert", duration=0.0, distance=1.2, others=None, car=True):
    readings = {"blocked_prob": 0.93}
    if distance is not None:
        readings["distance_m"] = distance
    return {"time": T, "category": category, "kind": kind, "since": T - duration, "duration_sec": duration,
            "car": {"state": "impassable_wait", "has_target": False, "events": ["enter_impassable"]} if car else None,
            "readings": readings if car else {}, "others": others or [], "car_ts": T if car else None,
            "latency_sec": None, "outputs": []}


class FakeBackend(object):
    """假的後端：固定回傳一段文字，可設定延遲、載入失敗、產生時丟例外、不理會停止要求。"""

    name = "fake"

    def __init__(self, text="車子前方被擋住，已經停在原地。目前離前方障礙約 1.2 公尺。", delay=0.0,
                 load_error=None, load_delay=0.0, generate_error=None, respect_stop=True):
        self.text = text
        self.delay = delay
        self.load_error = load_error
        self.load_delay = load_delay
        self.generate_error = generate_error
        self.respect_stop = respect_stop
        self.info = {"model": "fake"}
        self.calls = 0
        self.finished = 0          # 完整產生完的次數（包含結果被丟棄的）
        self.warmup = None

    def load(self, warmup_messages=None):
        self.warmup = warmup_messages
        time.sleep(self.load_delay)
        if self.load_error:
            raise self.load_error

    def generate(self, messages, should_stop):
        self.calls += 1
        end = time.time() + self.delay
        while time.time() < end:
            if self.respect_stop and should_stop():
                return ""
            time.sleep(0.01)
        if self.generate_error:
            raise self.generate_error
        self.finished += 1
        return self.text


def ready(backend, timeout=1.0, verbose=False):
    fmt = LLMFormatter(backend=backend, timeout=timeout, verbose=verbose)
    fmt.wait_ready(5)
    return fmt


@contextlib.contextmanager
def captured():
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old


# ═══════════════════════════════════════════════════════════
print("\n【一、接上 line_output()】")
fmt = ready(FakeBackend())
out = line_output(dry_run=True, formatter=fmt)(notice())
check("傳給 line_output 即可使用，送出的是 LLM 的文字",
      out, "LINE [dry-run] " + HEADER + " / 車子前方被擋住，已經停在原地。目前離前方障礙約 1.2 公尺。")
check("紀錄標明使用 LLM", (fmt.last["used"], fmt.last["reason"]), ("fake", None))
check("預熱：載入時用正式提示詞跑一次", fmt.backend.warmup[0]["role"], "system")


class FakeClock(object):
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


clock = FakeClock(T)
sm = StateMachine(outputs=[line_output(dry_run=True, formatter=fmt)], clock=clock, verbose=False)
sm.cache.put("jetbot", {"state": "impassable_wait", "events": ["enter_impassable"], "has_target": False,
                        "readings": {"blocked_prob": 0.93, "distance_m": 1.2, "speed_scale": 0, "target_h": None}})
n = sm.tick()
check("狀態機（未修改）的通知經過 LLM 產生器", n["outputs"][0].startswith("LINE [dry-run] " + HEADER + " / 車子前方"), True)
fmt.close()

# ═══════════════════════════════════════════════════════════
print("\n【二、後端切換】")
check("三種後端都有位置", sorted(backends.BACKENDS), ["cloud", "local", "template"])
fmt = LLMFormatter(backend="template", verbose=False)
check("template：直接用模板", fmt(notice()), format_message(notice()))
check("template：原因記為 template", fmt.last["reason"], "template")

with captured() as buf:
    fmt = LLMFormatter(backend="cloud", verbose=True)
    fmt.wait_ready(5)
    text = fmt(notice())
check("cloud：預留未實作，退回模板", text, format_message(notice()))
check("cloud：提示尚未實作", "尚未實作" in buf.getvalue(), True)

fmt = LLMFormatter(backend="no_such_backend", verbose=False)
check("不認得的後端名稱：退回模板", (fmt(notice()), fmt.last["reason"]), (format_message(notice()), "unavailable"))

os.environ["JETBOT_LLM_BACKEND"] = "template"
os.environ["JETBOT_LLM_TIMEOUT"] = "1.5"
fmt = LLMFormatter(verbose=False)
check("環境變數切換後端與逾時", (fmt.backend_name, fmt.timeout), ("template", 1.5))
del os.environ["JETBOT_LLM_BACKEND"]
del os.environ["JETBOT_LLM_TIMEOUT"]
check("預設後端是 local、逾時 3 秒", (fm.DEFAULT_BACKEND, fm.DEFAULT_TIMEOUT), ("local", 3.0))

# ═══════════════════════════════════════════════════════════
print("\n【三、什麼情況都有訊息可送（退回模板，只提示一次）】")
# 沒安裝 mlx-lm：讓 import 失敗（不管這台有沒有裝，都不會真的載入模型）
saved = {k: sys.modules.get(k) for k in ("mlx", "mlx.core", "mlx_lm")}
for k in saved:
    sys.modules[k] = None
try:
    with captured() as buf:
        fmt = LLMFormatter(backend="local", verbose=True)
        fmt.wait_ready(5)
        texts = [fmt(notice()), fmt(notice())]
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
check("沒有 mlx-lm（或不是 Apple Silicon）：用模板", texts, [format_message(notice())] * 2)
check("沒有 mlx-lm：原因 unavailable", fmt.last["reason"], "unavailable")
check("沒有 mlx-lm：只提示一次", buf.getvalue().count("[LLM]"), 1)

with captured() as buf:
    fmt = LLMFormatter(backend=FakeBackend(load_error=RuntimeError("找不到模型檔")), verbose=True)
    fmt.wait_ready(5)
    texts = [fmt(notice()), fmt(notice())]
check("模型載入失敗：用模板", texts, [format_message(notice())] * 2)
check("模型載入失敗：只提示一次", buf.getvalue().count("[LLM]"), 1)

fake = FakeBackend(load_delay=0.5)
fmt = LLMFormatter(backend=fake, verbose=False)
check("模型還在載入：先用模板", (fmt(notice()), fmt.last["reason"]), (format_message(notice()), "loading"))
fmt.wait_ready(5)
check("載入完成後改用 LLM", fmt(notice()).startswith(HEADER + "\n車子前方"), True)
fmt.close()

fake = FakeBackend(delay=1.0)
with captured() as buf:
    fmt = ready(fake, timeout=0.2, verbose=True)
    start = time.time()
    text = fmt(notice())
    elapsed = time.time() - start
    fmt.wait_idle(5)
    fmt(notice())
check("逾時：用模板", text, format_message(notice()))
check("逾時：原因 timeout", fmt.last["reason"], "timeout")
check("逾時：最多等到逾時上限（0.2 秒）", elapsed < 0.4, True)
check("逾時：兩次只提示一次", buf.getvalue().count("逾時"), 1)
check("逾時：產生被要求停下，沒有跑完", fake.finished, 0)
fmt.close()

# 遲到的結果要丟棄：後端不理會停止要求，照樣跑完；那則通知仍只送出一次（模板）
fake = FakeBackend(delay=0.4, respect_stop=False)
fmt = ready(fake, timeout=0.1)
sent = []


def counting_output(n):
    text = line_output(dry_run=True, formatter=fmt)(n)
    sent.append(text)
    return text


clock = FakeClock(T)
sm = StateMachine(outputs=[counting_output], clock=clock, verbose=False)
sm.cache.put("jetbot", {"state": "impassable_wait", "events": ["enter_impassable"], "has_target": False,
                        "readings": {"distance_m": 1.2}})
sm.tick()
time.sleep(0.6)   # 等後端把遲到的結果產生完
check("遲到的結果已產生完", fake.finished, 1)
check("同一則通知只送一次，送的是模板", (len(sent), sent[0]),
      (1, "LINE [dry-run] " + format_message(sm.notices[0]).replace("\n", " / ")))
check("遲到的結果沒有出現在任何輸出", any("目前離前方障礙" in s for s in sent), False)

# 上一則逾時的產生停不下來時，下一則在逾時上限內等不到 → 模板
fake = FakeBackend(delay=1.0, respect_stop=False)
fmt = ready(fake, timeout=0.2)
fmt(notice())
fmt(notice())
check("上一則還沒停下：用模板，原因 busy", fmt.last["reason"], "busy")
check("上一則還沒停下：沒有排第二份工作", fake.calls, 1)
fmt.wait_idle(5)
fmt.close()

fmt = ready(FakeBackend(text="   \n  "))
check("回應為空：用模板", (fmt(notice()), fmt.last["reason"]), (format_message(notice()), "empty"))
fmt.close()

fmt = ready(FakeBackend(text="車子前方被擋住，has_target 為 false。"))
check("沒通過檢查：用模板", (fmt(notice()), fmt.last["reason"]), (format_message(notice()), "check"))
check("沒通過檢查：記錄哪幾條", fmt.last["failed_rules"], ["no_fields", "no_english"])
check("沒通過檢查：保留模型原文供除錯", "has_target" in fmt.last["llm_text"], True)
fmt.close()

with captured() as buf:
    fmt = ready(FakeBackend(generate_error=ValueError("GPU 爆了")), verbose=True)
    text = fmt(notice())
check("產生時丟例外：不往外拋，用模板", (text, fmt.last["reason"]), (format_message(notice()), "error"))
check("產生時丟例外：有提示", "GPU 爆了" in buf.getvalue(), True)
fmt.close()

fmt = LLMFormatter(backend="template", verbose=False)
check("notice 格式錯誤（連模板都失敗）：仍給最基本的訊息",
      fmt({"time": T, "category": "???", "kind": "alert"}), HEADER + "\n機器人有新的狀況，請留意。")
check("notice 不是 dict：也不往外拋", safe_template(None).startswith("【伴護機器人】"), True)

# ═══════════════════════════════════════════════════════════
print("\n【四、輸出檢查（每一條）】")
N = notice()   # 卡住，距離 1.2 公尺
OK = HEADER + "\n車子前方被擋住，已經停在原地。目前離前方障礙約 1.2 公尺。"
check("合格的訊息全部通過", prompt.check_output(OK, N), [])
check("empty：空字串", prompt.check_output("", N), ["empty"])
check("traditional：簡體字", "traditional" in prompt.check_output(HEADER + "\n车子前方被挡住，已经停在原地。", N), True)
check("traditional：大陸用語", "traditional" in prompt.check_output(HEADER + "\n前方無法通過，已停下。這是最新信息。", N), True)
check("length：121 字", "length" in prompt.check_output(HEADER + "\n前方無法通過" + "。" * 115, N), True)
check("length：120 字剛好通過", "length" in prompt.check_output(HEADER + "\n前方無法通過" + "。" * 114, N), False)
check("length：只有開頭沒有本文", "length" in prompt.check_output(HEADER, N), True)
check("no_fields：欄位名稱", "no_fields" in prompt.check_output(HEADER + "\n前方無法通過，impassable_wait。", N), True)
check("no_fields：JSON 符號", "no_fields" in prompt.check_output(HEADER + "\n前方無法通過 {距離}", N), True)
check("no_english：英文句子",
      "no_english" in prompt.check_output(HEADER + "\n前方無法通過。The robot is stuck.", N), True)
check("no_english：提示詞殘留", "no_english" in prompt.check_output(HEADER + "\n訊息：前方無法通過。", N), True)
check("numbers：距離對不上", prompt.check_output(HEADER + "\n前方無法通過，距離約 1.5 公尺。", N), ["numbers"])
check("numbers：換算成公分可以", prompt.check_output(HEADER + "\n前方無法通過，距離約 120 公分。", N), [])
check("numbers：時間對不上",
      "numbers" in prompt.check_output(HEADER + "\n前方無法通過，已經約 30 秒。", notice(duration=13.0)), True)
check("numbers：時間對得上",
      prompt.check_output(HEADER + "\n前方無法通過，已經約 13 秒。", notice(duration=13.0)), [])
check("numbers：分鐘依 format_duration 換算（170 秒 → 3 分鐘）",
      prompt.check_output(HEADER + "\n前方無法通過，已經約 3 分鐘。", notice(duration=170.0)), [])
check("numbers：中文數字也檢查",
      "numbers" in prompt.check_output(HEADER + "\n前方無法通過，已經三十秒。", notice(duration=13.0)), True)
check("numbers：距離讀不到（null）時寫出距離",
      "numbers" in prompt.check_output(HEADER + "\n前方無法通過，距離約 0.4 公尺。", notice(distance=None)), True)
check("numbers：併入的其他事項的時間也算",
      prompt.check_output(HEADER + "\n前方無法通過。另外，中斷約 13 秒的連線已恢復。",
                          notice(others=[{"category": "offline", "kind": "recovery", "duration_sec": 13.0}])), [])
check("no_medical：醫療建議", "no_medical" in prompt.check_output(HEADER + "\n前方無法通過，請立即送醫。", N), True)
check("no_medical：推測身體狀況", "no_medical" in prompt.check_output(HEADER + "\n前方無法通過，受護者可能跌倒。", N), True)
check("header：時間錯", "header" in prompt.check_output("【伴護機器人】09:00\n前方無法通過。", N), True)
check("header：沒有開頭", "header" in prompt.check_output("前方無法通過。\n車子停在原地。", N), True)
check("header：開頭重複", "header" in prompt.check_output(HEADER + "\n【伴護機器人】前方無法通過。", N), True)
check("consistent：沒失聯卻提到連線",
      "consistent" in prompt.check_output(HEADER + "\n車子找到受護者，連線恢復了。",
                                          notice(category="no_target", kind="recovery", duration=150.0)), True)
check("consistent：沒提到主要的狀況",
      "consistent" in prompt.check_output(HEADER + "\n一切正常。", notice(category="no_target")), True)
# 任務六：2026-09-29 實際送出的兩則 LLM 訊息，數字對但意思錯
check("duration_semantics：「約 7 秒前就被擋住了」",
      prompt.check_output(HEADER + "\n車子已經可以通過前方障礙了，約 7 秒前就被擋住了。",
                          notice(kind="recovery", duration=6.9)), ["duration_semantics"])
check("duration_semantics：「約 13 秒前就已經重新連上」",
      prompt.check_output(HEADER + "\n和機器人的連線已經恢復，約 13 秒前就已經重新連上。",
                          notice(category="offline", kind="recovery", duration=13.1)), ["duration_semantics"])
check("consistent：看不到受護者卻寫成收不到資料（2026-10-01 評測）",
      "consistent" in prompt.check_output(HEADER + "\n車子剛剛失去受護者的訊號，已經約 3 秒沒有收到資料。",
                                          notice(category="no_target", duration=3.0)), True)
check("事實：看不到受護者（發生）不給持續時間",
      "duration_text" in prompt.facts_for_prompt(notice(category="no_target", duration=3.0)), False)
check("duration_semantics：「停了約 7 秒」可以",
      prompt.check_output(HEADER + "\n前方已經可以通過，車子繼續跟隨了。剛才停了約 7 秒。",
                          notice(kind="recovery", duration=6.9)), [])

# 任務六：所有模板版本代入各種時間、距離，都要通過全部檢查
template_bad = []
for (category, kind), value in machine.TEMPLATES.items():
    for state in (["locked", "waiting"] if isinstance(value, dict) else ["locked"]):
        for i in range(3):
            for seconds in (3.0, 7.0, 150.8, 660.0, 4800.0):
                for distance in (0.42, None):
                    n = notice(category=category, kind=kind, duration=seconds, distance=distance)
                    n["time"] = T + i
                    n["car"] = {"state": state, "has_target": state == "locked", "events": []}
                    failed_rules = prompt.check_output(format_message(n), n)
                    if failed_rules:
                        template_bad.append((category, kind, state, i, seconds, distance, failed_rules))
check("所有模板版本 × 時間 × 有無距離都通過檢查", template_bad, [])

# 模板本身（退路）一定要通過所有檢查
import eval as llm_eval  # noqa: E402
items, _ = llm_eval.collect_notices()
bad = [(label, prompt.check_output(format_message(n), n)) for label, n in items
       if prompt.check_output(format_message(n), n)]
check("所有情境的模板訊息都通過檢查（{} 則）".format(len(items)), bad, [])
check("情境涵蓋 alert／reminder／recovery 與同一拍兩件事",
      sorted(set(n["kind"] for _, n in items)) + [any(n["others"] for _, n in items)],
      ["alert", "recovery", "reminder", True])

# ═══════════════════════════════════════════════════════════
print("\n【五、提示詞】")
facts = prompt.facts_for_prompt(notice(duration=170.0))
check("事實：時間已換成人話", facts["duration_text"], "約 3 分鐘")
check("事實：不含 Unix 秒數與車上內部欄位", [k for k in ("time", "since", "car_ts", "car", "outputs") if k in facts], [])
check("事實：卡住時給距離", facts["readings"], {"distance_m": 1.2})
check("事實：null 的距離不出現", prompt.facts_for_prompt(notice(distance=None))["readings"], {})
check("事實：看不到受護者時不給距離", prompt.facts_for_prompt(notice(category="no_target"))["readings"], {})
check("事實：剛發生（不到 1 秒）不給持續時間", "duration_text" in prompt.facts_for_prompt(notice(duration=0.0)), False)
recovered = notice(kind="recovery")
recovered["car"] = {"state": "locked", "has_target": True, "events": ["resume_follow"]}
check("事實：卡住解除且仍鎖定 → following 為 true", prompt.facts_for_prompt(recovered)["following"], True)
recovered["car"] = {"state": "waiting", "has_target": False, "events": ["resume_follow"]}
check("事實：卡住解除但沒有鎖定 → following 為 false", prompt.facts_for_prompt(recovered)["following"], False)
recovered["car"] = {"state": "locked", "has_target": False, "events": ["resume_follow"]}
check("事實：卡住解除時 locked 但看不到人（跟丟寬限中）→ following 為 false",
      prompt.facts_for_prompt(recovered)["following"], False)
check("事實：其他通知沒有 following", "following" in prompt.facts_for_prompt(notice()), False)
check("提示詞與範例不再寫「正在搜尋」「照常運作」",
      [t for t in [prompt.SYSTEM_PROMPT] + [a for _, a in prompt.FEW_SHOTS]
       if "正在搜尋" in t.replace("不要說正在搜尋", "") or "照常運作" in t.replace("不要說車子照常運作", "")], [])
check("事實：others 也換成人話",
      prompt.facts_for_prompt(notice(others=[{"category": "offline", "kind": "recovery",
                                               "duration_sec": 13.0}]))["others"],
      [{"category": "offline", "kind": "recovery", "duration_text": "約 13 秒"}])
messages = prompt.build_messages(notice())
check("訊息：系統提示詞 → 範例一問一答 → 事實",
      [m["role"] for m in messages], ["system"] + ["user", "assistant"] * len(prompt.FEW_SHOTS) + ["user"])
check("訊息：最後一則是這次的事實（JSON）", json.loads(messages[-1]["content"])["category"], "impassable")
check("範例有兩三個以上", len(prompt.FEW_SHOTS) >= 3, True)


def shot_notice(facts):
    seconds = {"約 3 秒": 3, "約 7 秒": 7, "約 8 秒": 8, "約 13 秒": 13, "約 20 秒": 20,
               "約 2 分鐘": 120, "約 2 分半": 150, "約 5 分鐘": 300}
    return {"time": T, "since": T, "category": facts["category"], "kind": facts["kind"],
            "duration_sec": seconds.get(facts.get("duration_text"), 0.0), "readings": facts["readings"],
            "others": [{"category": o["category"], "kind": o["kind"], "duration_sec": seconds[o["duration_text"]]}
                       for o in facts["others"]]}


check("範例的理想訊息本身都通過檢查",
      [prompt.check_output(HEADER + "\n" + answer, shot_notice(f)) for f, answer in prompt.FEW_SHOTS],
      [[]] * len(prompt.FEW_SHOTS))
check("整理：去掉模型自己寫的開頭", prompt.compose("【伴護機器人】09:00\n前方無法通過。", N), HEADER + "\n前方無法通過。")
check("整理：空回應", prompt.compose("  \n ", N), "")

# ═══════════════════════════════════════════════════════════
print("\n【六、不阻塞狀態機（background_output）】")
fake = FakeBackend(delay=0.3)
fmt = ready(fake, timeout=2.0)
output = background_output(line_output(dry_run=True, formatter=fmt), formatter=fmt)
clock = FakeClock(T)
sm = StateMachine(outputs=[output], clock=clock, verbose=False)
sm.cache.put("jetbot", {"state": "impassable_wait", "events": ["enter_impassable"], "has_target": False,
                        "readings": {"distance_m": 1.2}})
start = time.time()
n = sm.tick()
blocked = time.time() - start
check("有通知的那一拍立刻返回（< 50 ms，後端要 300 ms）", blocked < 0.05, True)
check("當下的輸出紀錄：已交給背景", n["outputs"], ["已交給背景輸出"])
output.join()
check("背景完成後補上真正的輸出（LLM 文字）", n["outputs"][1].startswith("背景：LINE [dry-run] " + HEADER + " / 車子前方"), True)

# 同一時間排進兩則：逾時從排入背景時起算，第二則不會再等滿一次
fake = FakeBackend(delay=0.4)
fmt = ready(fake, timeout=0.6)
output = background_output(line_output(dry_run=True, formatter=fmt), formatter=fmt)
a, b = notice(), notice(category="no_target", distance=None)
start = time.time()
output(a)
output(b)
output.join()
total = time.time() - start
check("第一則用 LLM", a["outputs"][0].startswith("背景：LINE [dry-run] " + HEADER + " / 車子前方"), True)
check("第二則排隊超過逾時上限 → 模板", b["outputs"][0], "背景：LINE [dry-run] " + format_message(b).replace("\n", " / "))
check("兩則總共不超過逾時上限太多（< 0.9 秒）", total < 0.9, True)
fmt.close()

check("背景輸出的執行緒是 daemon（不會卡住程式結束）",
      all(t.daemon for t in threading.enumerate() if t.name.startswith("llm-")), True)

print("\n" + "=" * 46)
print("通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
if failed:
    print("失敗項目：" + "、".join(failed))
sys.exit(1 if failed else 0)
