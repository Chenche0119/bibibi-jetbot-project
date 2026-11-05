"""LLM 訊息產生器：notice → 給家屬的文字。失敗、逾時、輸出不合格時一律退回固定模板。

    車外狀態機 ──► notice ──► LLMFormatter ──► LINE
                                 │  背景執行緒：模型（只載入一次，啟動時預熱）
                                 └─ 逾時／失敗／沒通過檢查 → machine.format_message()（模板）

用法（state_machine/ 一行都不用改）：
    from machine import StateMachine, line_output
    from formatter import LLMFormatter, background_output

    fmt = LLMFormatter()                                         # 立刻返回，模型在背景載入
    sm = StateMachine(outputs=[line_output(dry_run=True, formatter=fmt)])
    # 或：連產生文字的時間都不佔用狀態機的判斷迴圈
    sm = StateMachine(outputs=[background_output(line_output(dry_run=True, formatter=fmt), formatter=fmt)])

設定（環境變數或專案根目錄的 .env，也可以直接傳參數）：
    JETBOT_LLM_BACKEND   template / local / cloud，預設 local
    JETBOT_LLM_TIMEOUT   逾時秒數，預設 3
    JETBOT_LLM_MODEL     模型名稱，local 預設 mlx-community/Qwen2.5-3B-Instruct-4bit
"""

import collections
import os
import queue
import sys
import threading
import time

LLM_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(LLM_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "state_machine"))
sys.path.insert(0, LLM_DIR)

from machine import MESSAGE_HEADER, format_clock, format_message  # noqa: E402
from backends import BackendUnavailable, make_backend  # noqa: E402
from prompt import build_messages, check_output, compose  # noqa: E402

# ═══════════════════════════════════════════════════════════
#  設定區
# ═══════════════════════════════════════════════════════════

DEFAULT_BACKEND = "local"
DEFAULT_TIMEOUT = 3.0     # 構想書要求通報延遲 ≤ 3 秒
RECORD_SIZE = 200         # 產生紀錄最多保留幾筆

# 退回模板的原因
TEMPLATE = "template"        # 設定就是用模板
UNAVAILABLE = "unavailable"  # 沒裝 mlx-lm、不是 Apple Silicon、模型載入失敗、後端未實作
LOADING = "loading"          # 模型還在載入
BUSY = "busy"                # 上一則逾時的產生到逾時上限都還沒停下
TIMEOUT = "timeout"          # 超過逾時上限
EMPTY = "empty"              # 回應為空
CHECK = "check"              # 沒通過輸出檢查
ERROR = "error"              # 其他例外

WARNINGS = {
    UNAVAILABLE: "LLM 無法使用，之後的通知都改用模板：{detail}",
    LOADING: "模型還在載入，這則先用模板",
    BUSY: "上一則的產生還沒停下，這則先用模板（之後不再提示）",
    TIMEOUT: "LLM 超過 {timeout:g} 秒沒產生完，改用模板（之後逾時不再提示）",
    EMPTY: "LLM 回應為空，改用模板（之後不再提示）",
    CHECK: "LLM 輸出沒通過檢查（{detail}），改用模板（之後不再提示）",
    ERROR: "LLM 產生時發生錯誤，改用模板（之後不再提示）：{detail}",
}

# 預熱用的通知：與實際通知走同一段提示詞，順便建立提示詞快取
WARMUP_NOTICE = {"time": 0.0, "category": "no_target", "kind": "alert", "since": 0.0, "duration_sec": 3.0,
                 "car": {"state": "waiting", "has_target": False, "events": ["notify_no_target"]},
                 "readings": {"distance_m": 1.2, "blocked_prob": 0.05}, "others": [], "car_ts": 0.0}

# ═══════════════════════════════════════════════════════════


def load_env():
    """讀專案根目錄的 .env（已經設定的環境變數優先），與 machine.send_line() 相同規則。"""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def safe_template(notice):
    """模板版訊息。連模板都失敗時（notice 格式不對）仍給一則最基本的訊息，保證有東西可送。"""
    try:
        return format_message(notice)
    except Exception:
        try:
            clock = format_clock(notice["time"])
        except Exception:
            clock = format_clock(time.time())
        return MESSAGE_HEADER.format(clock=clock) + "\n機器人有新的狀況，請留意。"


class _Job(object):
    def __init__(self, messages):
        self.messages = messages
        self.cancelled = False      # 逾時後設為 True：產生會盡快停下，結果丟棄
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.gen_sec = None


class LLMFormatter(object):
    """可直接傳給 line_output(formatter=...) 的訊息產生器。

    模型的載入與產生都在同一條背景執行緒（MLX 的運算串流綁定執行緒），呼叫端只等到逾時為止：
    逾時就回傳模板並把那次產生標記為取消，遲到的結果直接丟棄，同一則通知只會回傳一次文字。
    任何例外都在這裡吞掉，永遠回傳一則可以送出的訊息。
    """

    def __init__(self, backend=None, timeout=None, model=None, verbose=True):
        """
        backend: 後端名稱（template／local／cloud）或後端物件（測試時傳假的）。不給就讀 JETBOT_LLM_BACKEND
        timeout: 逾時秒數，不給就讀 JETBOT_LLM_TIMEOUT，再沒有就是 3 秒
        model:   模型名稱，不給就讀 JETBOT_LLM_MODEL，再沒有就用後端的預設
        verbose: 退回模板時是否印出提示（每種原因只提示一次）
        """
        load_env()
        if timeout is None:
            timeout = float(os.environ.get("JETBOT_LLM_TIMEOUT", DEFAULT_TIMEOUT))
        self.timeout = float(timeout)
        self.verbose = verbose
        self.records = collections.deque(maxlen=RECORD_SIZE)
        self.last = None
        self.unavailable = None          # 無法使用的原因；None 代表可用或還在載入
        self.ready = threading.Event()   # 模型載入並預熱完成
        self.loaded = threading.Event()  # 載入結束（不論成功或失敗）
        self._warned = set()
        self._starts = {}   # background_output 交來的通知：從排入背景的時間起算逾時
        self._slot = threading.Semaphore(1)   # 模型一次只做一則；拿到才能送工作給背景執行緒
        self._jobs = queue.Queue()
        self._closing = False
        self._thread = None

        if backend is None or isinstance(backend, str):
            name = backend or os.environ.get("JETBOT_LLM_BACKEND", DEFAULT_BACKEND)
            try:
                backend = make_backend(name, model=model or os.environ.get("JETBOT_LLM_MODEL") or None)
            except BackendUnavailable as e:
                self.backend_name = name
                self.backend = None
                self._mark_unavailable(str(e))
                return
        self.backend = backend
        self.backend_name = backend.name
        if self.backend_name == TEMPLATE:
            self.loaded.set()
            return
        # 啟動時就在背景載入並預熱，不等第一則通知；載入期間的通知先用模板
        self._thread = threading.Thread(target=self._worker, name="llm-formatter")
        self._thread.daemon = True
        self._thread.start()

    # ── 對外介面 ──
    def __call__(self, notice):
        """notice → 訊息文字。最多等 timeout 秒，任何情況都回傳一則可以送出的訊息。"""
        start = self._starts.pop(id(notice), None) or time.time()
        record = {"time": notice.get("time") if isinstance(notice, dict) else None,
                  "category": None, "kind": None, "backend": self.backend_name,
                  "used": TEMPLATE, "reason": None, "failed_rules": [], "llm_text": None,
                  "gen_sec": None, "elapsed_sec": None, "text": None}
        try:
            record["category"], record["kind"] = notice.get("category"), notice.get("kind")
            text = self._generate(notice, start, record)
        except Exception as e:   # 不論發生什麼都不能往外拋，否則這則通知就送不出去
            record["reason"] = ERROR
            self._warn(ERROR, detail=repr(e))
            text = None
        if text is None:
            text = safe_template(notice)
        else:
            record["used"] = self.backend_name
        record["text"] = text
        record["elapsed_sec"] = round(time.time() - start, 3)
        self.records.append(record)
        self.last = record
        return text

    def start_clock(self, notice, at):
        """指定這則通知的逾時從何時起算（background_output 用：從排入背景時算，排隊的時間也算在內）。"""
        self._starts[id(notice)] = at

    def wait_ready(self, timeout=None):
        """等模型載入完成（eval、示範用）。回傳是否可以使用。"""
        self.loaded.wait(timeout)
        return self.ready.is_set()

    def wait_idle(self, timeout=None):
        """等背景執行緒把手上的工作（包含已逾時、正在停下的）做完。回傳是否閒置。"""
        if not self._slot.acquire(timeout=timeout):
            return False
        self._slot.release()
        return True

    def close(self):
        self._closing = True
        if self._thread is not None:
            self._jobs.put(None)
            self._thread.join(timeout=5.0)

    @property
    def info(self):
        return dict(getattr(self.backend, "info", {}) or {})

    # ── 內部 ──
    def _generate(self, notice, start, record):
        """試著用 LLM 產生。回傳通過檢查的文字，或 None（退回模板，原因寫在 record）。"""
        if self.backend_name == TEMPLATE:
            record["reason"] = TEMPLATE
            return None
        if self.unavailable is not None:
            record["reason"] = UNAVAILABLE
            return None
        if not self.ready.is_set():
            record["reason"] = LOADING
            self._warn(LOADING)
            return None

        job = _Job(build_messages(notice))
        # 上一則逾時的產生可能還在停下（例如正在處理提示詞，要到下一個 token 才會檢查取消），
        # 在逾時上限內等它讓出來；等不到就用模板
        if not self._slot.acquire(timeout=max(0.0, self.timeout - (time.time() - start))):
            record["reason"] = BUSY
            self._warn(BUSY)
            return None
        self._jobs.put(job)

        remaining = self.timeout - (time.time() - start)
        if not job.done.wait(max(0.0, remaining)):
            job.cancelled = True     # 遲到的結果丟棄；模板已經要送出去了
            record["reason"] = TIMEOUT
            self._warn(TIMEOUT)
            return None
        record["gen_sec"] = job.gen_sec
        if job.error is not None:
            record["reason"] = ERROR
            self._warn(ERROR, detail=repr(job.error))
            return None

        text = compose(job.result, notice)
        record["llm_text"] = text
        if not text:
            record["reason"] = EMPTY
            record["failed_rules"] = ["empty"]
            self._warn(EMPTY)
            return None
        failed = check_output(text, notice)
        if failed:
            record["reason"] = CHECK
            record["failed_rules"] = failed
            self._warn(CHECK, detail="、".join(failed))
            return None
        return text

    def _worker(self):
        try:
            self.backend.load(build_messages(WARMUP_NOTICE))
            self.ready.set()
        except Exception as e:
            self._mark_unavailable(str(e) if isinstance(e, BackendUnavailable) else repr(e))
        finally:
            self.loaded.set()
        if not self.ready.is_set():
            return

        while True:
            job = self._jobs.get()
            if job is None:
                break
            start = time.time()
            try:
                if not job.cancelled:
                    job.result = self.backend.generate(
                        job.messages, lambda job=job: job.cancelled or self._closing)
            except Exception as e:
                job.error = e
            finally:
                job.gen_sec = round(time.time() - start, 3)
                job.done.set()
                self._slot.release()

    def _mark_unavailable(self, detail):
        self.unavailable = detail
        self.loaded.set()
        self._warn(UNAVAILABLE, detail=detail)

    def _warn(self, reason, **kwargs):
        """每種原因只提示一次。"""
        if reason in self._warned:
            return
        self._warned.add(reason)
        if self.verbose:
            print("[LLM] " + WARNINGS[reason].format(timeout=self.timeout, **kwargs))


def background_output(output, formatter=None):
    """把輸出端包成背景執行：狀態機那一拍立刻返回，產生文字與送 LINE 在背景依序進行。

    output 是 machine.line_output(...) 這類 fn(notice) -> str。
    formatter 是 output 裡用的 LLMFormatter：有給的話，逾時從排入背景的時間起算，
    同一拍後面排隊的通知不會各自再等滿 timeout（排太久就直接用模板）。
    背景完成後，真正的輸出結果會補進 notice["outputs"]（開頭標「背景：」）。
    代價：notice["latency_sec"] 只量到交給背景為止，不含產生文字與送出的時間，
    真正的耗時看 wrapped.results。
    """
    jobs = queue.Queue()
    results = collections.deque(maxlen=RECORD_SIZE)

    def worker():
        while True:
            notice, queued_at = jobs.get()
            if formatter is not None:
                formatter.start_clock(notice, queued_at)
            try:
                result = str(output(notice))
            except Exception as e:   # 與 StateMachine._emit 相同：失敗不能讓背景停下
                result = "失敗：{}".format(e)
            notice.setdefault("outputs", []).append("背景：" + result)
            results.append({"time": notice.get("time"), "result": result,
                            "done_sec": round(time.time() - queued_at, 3)})
            jobs.task_done()

    thread = threading.Thread(target=worker, name="llm-background-output")
    thread.daemon = True
    thread.start()

    def wrapped(notice):
        jobs.put((notice, time.time()))
        return "已交給背景輸出"

    wrapped.join = jobs.join          # 等背景全部送完（測試、eval 用）
    wrapped.results = results
    return wrapped
