# -*- coding: utf-8 -*-
"""車外主程式：把接收、車外狀態機、訊息產生器、LINE 串成一支可長時間執行的程式。

    MQTT broker ──► MqttReceiver ──► 覆蓋快取 ──► StateMachine ──► 背景輸出 ──► 訊息產生器 ──► LINE
    （本地或雲端）   transport/        state_machine/                llm/          模板或本地 LLM   預設 dry-run
                                                                                     └─► 通知紀錄（logs/*.jsonl）

用法（在 repo 根目錄）：

    # 預設：連 .env／環境變數設定的 broker、訊息產生器看 JETBOT_LLM_BACKEND、LINE 為 dry-run
    uv run --with "paho-mqtt<2.0" python run.py

    # 只用模板（不需要 mlx-lm）
    uv run --with "paho-mqtt<2.0" python run.py --llm template

    # 本地 LLM（Apple Silicon）
    uv run --no-project --with "paho-mqtt<2.0" --with mlx-lm python run.py --llm local

    # 真的送出 LINE（會消耗免費額度，每月約 200 則）
    uv run --with "paho-mqtt<2.0" python run.py --send-line

    # 不接 broker，重播錄製檔（示範、除錯用）
    uv run python run.py --replay rec.jsonl

設定（參數 > 環境變數 > 專案根目錄 .env > 預設值）：

| 設定 | 參數 | 環境變數 | 預設 |
|---|---|---|---|
| broker 位址、埠號 | --host、--port | JETBOT_MQTT_HOST、JETBOT_MQTT_PORT | localhost、1883（TLS 為 8883） |
| broker 帳密 | （不提供，避免留在 shell 紀錄） | JETBOT_MQTT_USERNAME、JETBOT_MQTT_PASSWORD | 不使用 |
| TLS | --tls | JETBOT_MQTT_TLS | 不加密 |
| 訊息產生器 | --llm | JETBOT_LLM_BACKEND | local（載入失敗自動退回模板） |
| 實際送出 LINE | --send-line | JETBOT_SEND_LINE=1 | dry-run |
| 通知紀錄檔 | --log | — | logs/notices-日期-時間.jsonl |

結束：Ctrl+C 或 kill（SIGTERM）都會停止接收、等排隊中的通知送完、印出統計並寫入紀錄檔最後一行。
"""

import argparse
import io
import json
import os
import resource
import signal
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
for _sub in ("transport", "state_machine", "llm"):
    sys.path.insert(0, os.path.join(ROOT, _sub))

import machine  # noqa: E402
import receiver as receiver_mod  # noqa: E402
from formatter import LLMFormatter, background_output, load_env  # noqa: E402

# ═══════════════════════════════════════════════════════════
#  設定區
# ═══════════════════════════════════════════════════════════

LOG_DIR = os.path.join(ROOT, "logs")
STATUS_EVERY = 60.0      # 每幾秒印一行運作狀態；0 不印
NOTICES_KEEP = 200       # 狀態機的通知紀錄在記憶體中最多保留幾則（完整紀錄在檔案裡）
LATENCY_KEEP = 10000     # 延遲統計最多保留幾筆
DRAIN_TIMEOUT = 15.0     # 結束時最多等幾秒，讓排隊中的通知產生文字並送出
CONNECT_HINT_SEC = 15.0  # 啟動後幾秒還沒連上 broker 就提示檢查設定
ENV_SEND_LINE = "JETBOT_SEND_LINE"

# ═══════════════════════════════════════════════════════════


def _log(msg):
    try:
        sys.stderr.write("[run] {}\n".format(msg))
        sys.stderr.flush()
    except Exception:
        pass


def _stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _ms(seconds):
    return "—" if seconds is None else "{:.0f} ms".format(seconds * 1000)


def _peak_rss_mb():
    """本程序目前為止的最大常駐記憶體（MB）。macOS 的 ru_maxrss 單位是 bytes，Linux 是 KB。"""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024.0 * 1024.0) if sys.platform == "darwin" else rss / 1024.0


def _is_true(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


class NoticeRecorder(object):
    """輸出端：notice → 訊息產生器 → LINE（或 dry-run）→ 寫一行到通知紀錄檔。

    由 background_output 在背景執行緒依序呼叫，狀態機的判斷迴圈不會被產生文字或送 LINE 卡住。
    紀錄檔每行一則 JSON（type 為 notice）；結束時最後一行是統計（type 為 summary）。
    """

    def __init__(self, formatter, send_line=False, log_path=None, clock=time.time,
                 line_sender=None, connected=None, measure_latency=True, verbose=True):
        """
        formatter:   fn(notice) -> str，通常是 LLMFormatter
        send_line:   True 才真的呼叫 LINE API；預設 dry-run
        log_path:    通知紀錄檔（附加寫入）；None 不寫檔
        line_sender: fn(text) -> (status, body)，預設 machine.send_line（測試時換成假的）
        connected:   fn() -> bool，記錄通知當下筆電是否連著 broker（分辨「車子失聯」與「筆電斷網」）
        measure_latency: False 時不算延遲（加速重播的 ts 是模擬時間，算出來沒有意義）
        """
        self.formatter = formatter
        self.send_line = send_line
        self.clock = clock
        self.line_sender = line_sender
        self.connected = connected
        self.verbose = verbose
        self.measure_latency = measure_latency
        self.log_path = log_path
        self.count = 0
        self.line_ok = 0
        self.line_failed = 0
        self.used = {}                     # 訊息產生器實際用了什麼：{"local": n, "template": n}
        self.latency = receiver_mod.LatencyStats(maxlen=LATENCY_KEEP)   # 車上 ts → 輸出完成
        self._lock = threading.Lock()
        self._file = None
        if log_path:
            parent = os.path.dirname(os.path.abspath(log_path))
            if not os.path.isdir(parent):
                os.makedirs(parent)
            self._file = io.open(log_path, "a", encoding="utf-8")

    def __call__(self, notice):
        text = self.formatter(notice)
        gen = getattr(self.formatter, "last", None) or {}
        used = gen.get("used") or "template"

        if self.send_line:
            sender = self.line_sender or machine.send_line
            try:
                status, body = sender(text)
                ok = status == 200
                line = "HTTP {}".format(status) + ("" if ok else " {}".format(body or "").rstrip())
            except Exception as e:     # 例如沒有 token、網路斷了；不能讓背景輸出停下
                ok = False
                line = "失敗：{}".format(e)
        else:
            ok = None
            line = "dry-run"

        done = self.clock()
        car_ts = notice.get("car_ts")
        latency = done - car_ts if self.measure_latency and isinstance(car_ts, (int, float)) else None
        record = {
            "type": "notice",
            "time": notice.get("time"),
            "time_str": _stamp(notice.get("time") or done),
            "category": notice.get("category"),
            "kind": notice.get("kind"),
            "others": notice.get("others"),
            "duration_sec": notice.get("duration_sec"),
            "car": notice.get("car"),
            "readings": notice.get("readings"),
            "car_ts": car_ts,
            "text": text,
            "generator": used,
            "fallback_reason": gen.get("reason"),
            "gen_sec": gen.get("gen_sec"),
            "line": line,
            "dry_run": not self.send_line,
            "done": done,
            "latency_sec": None if latency is None else round(latency, 4),
            "broker_connected": self.connected() if self.connected else None,
        }
        with self._lock:
            self.count += 1
            self.used[used] = self.used.get(used, 0) + 1
            if ok is True:
                self.line_ok += 1
            elif ok is False:
                self.line_failed += 1
            if latency is not None:
                self.latency.add(latency)
            self._write(record)
        if self.verbose:
            _log("通知 {}/{}{}（{}，LINE {}，延遲 {}）\n      {}".format(
                record["category"], record["kind"],
                "".join("＋{}/{}".format(o["category"], o["kind"]) for o in record["others"] or []),
                used, line, _ms(latency), text.replace("\n", "\n      ")))
        return "LINE {} {}".format(line, text.replace("\n", " / "))

    def _write(self, record):
        if self._file is None:
            return
        try:
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._file.flush()
        except (IOError, OSError, ValueError) as e:
            _log("寫入通知紀錄失敗（{}），通報照常進行".format(e))

    def write_summary(self, stats):
        with self._lock:
            self._write(dict(stats, type="summary"))

    def close(self):
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


class Runner(object):
    """整條線的組裝與生命週期。run() 前景執行到 Ctrl+C／kill；測試時可直接 start()／stop()。"""

    def __init__(self, send_line=False, llm=None, llm_timeout=None, log_path=None,
                 replay_path=None, speed=1.0, record_path=None, mqtt=None,
                 offline_sec=None, status_every=STATUS_EVERY, formatter=None, line_sender=None,
                 verbose=True):
        """
        send_line:   True 才真的送 LINE
        llm:         template／local／cloud；None 時看 JETBOT_LLM_BACKEND（預設 local）
        log_path:    通知紀錄檔；None 不寫檔
        replay_path: 有給就重播 .jsonl，不接 broker
        mqtt:        交給 MqttReceiver 的連線設定（host、port、username、password、tls、ca_certs）
        formatter:   直接指定訊息產生器（測試用）；有給時忽略 llm、llm_timeout
        """
        self.verbose = verbose
        self.status_every = status_every
        self.send_line = send_line
        self.formatter = formatter or LLMFormatter(backend=llm, timeout=llm_timeout, verbose=verbose)
        self.replay_path = replay_path
        self.receiver = None
        self.recorder = NoticeRecorder(self.formatter, send_line=send_line, log_path=log_path,
                                       line_sender=line_sender, connected=self._broker_connected,
                                       measure_latency=not replay_path or speed == 1, verbose=verbose)
        self.output = background_output(
            self.recorder, formatter=self.formatter if isinstance(self.formatter, LLMFormatter) else None)
        self.sm = machine.StateMachine(outputs=[self.output], verbose=False, offline_sec=offline_sec)
        if replay_path:
            # retime：把 ts 平移到現在，狀態機與延遲統計才看得懂
            self.receiver = receiver_mod.Replayer(self.sm.cache, replay_path, speed=speed, retime=True)
        else:
            self.receiver = receiver_mod.MqttReceiver(self.sm.cache, record_path=record_path, **(mqtt or {}))
        self.started_at = None
        self.stopped_at = None
        self._stop = threading.Event()
        self._last_status = None
        self._llm_announced = False
        self._connect_hinted = False

    def _broker_connected(self):
        if self.receiver is None or self.replay_path:
            return None
        return bool(self.receiver.connected)

    # ── 生命週期 ──
    def start(self):
        self.started_at = time.time()
        self._last_status = time.monotonic()
        self.sm.start()
        self.receiver.start()
        if self.verbose:
            _log("啟動：來源 {}、訊息產生器 {}、LINE {}".format(
                "重播 " + self.replay_path if self.replay_path else "MQTT {}:{}（{}）".format(
                    self.receiver.host, self.receiver.port, receiver_mod.describe_settings(self.receiver.settings)),
                getattr(self.formatter, "backend_name", "自訂"),
                "實際送出" if self.send_line else "dry-run（加 --send-line 才會真的送）"))
            if self.recorder.log_path:
                _log("通知紀錄：{}".format(self.recorder.log_path))
        return self

    def poll(self):
        """主迴圈每一圈做的雜事：修剪記憶體中的紀錄、提示 LLM 預熱完成、定期印狀態。"""
        # 狀態機的 notices 會一直長；完整紀錄已寫進檔案，記憶體只留最近幾則
        with self.sm._lock:
            if len(self.sm.notices) > NOTICES_KEEP:
                del self.sm.notices[:-NOTICES_KEEP]

        fmt = self.formatter
        if (not self._llm_announced and isinstance(fmt, LLMFormatter)
                and fmt.backend_name != "template" and fmt.loaded.is_set()):
            self._llm_announced = True
            if fmt.ready.is_set() and self.verbose:
                _log("LLM 預熱完成（啟動後 {:.1f} 秒），之後的通知改用 {}".format(
                    time.time() - self.started_at, fmt.backend_name))

        # paho 1.x 背景重試時不回報失敗原因（TLS 沒開、CA 不對、位址錯都只是一直連不上），這裡提示一次
        if (not self._connect_hinted and not self.replay_path and not self.receiver.connects
                and time.time() - self.started_at >= CONNECT_HINT_SEC):
            self._connect_hinted = True
            if self.verbose:
                _log("{:g} 秒內沒連上 broker {}:{}（{}），背景持續重試中。請檢查：位址與埠號、"
                     "雲端 broker 是否要設定 JETBOT_MQTT_TLS=1、自簽憑證的 JETBOT_MQTT_CA_CERTS、網路".format(
                         CONNECT_HINT_SEC, self.receiver.host, self.receiver.port,
                         receiver_mod.describe_settings(self.receiver.settings)))

        if self.status_every and time.monotonic() - self._last_status >= self.status_every:
            self._last_status = time.monotonic()
            if self.verbose:
                _log(self.status_line())

    def status_line(self):
        active = sorted(self.sm.active)
        if self.replay_path:
            link = "重播中" if self.receiver.is_running() else "重播完畢"
        else:
            link = "broker 已連線" if self.receiver.connected else "broker 未連線"
        return "運作 {}｜{}｜收到 {} 筆（丟棄 {}）｜通知 {} 則｜未解除：{}｜記憶體峰值 {:.0f} MB".format(
            _format_elapsed(time.time() - self.started_at), link, self.receiver.count, self.receiver.dropped,
            self.recorder.count, "、".join(active) or "無", _peak_rss_mb())

    def stop(self):
        """停止接收與判斷，等排隊中的通知送完，關閉紀錄檔。可重複呼叫。"""
        if self.stopped_at is not None:
            return
        self.stopped_at = time.time()
        self.receiver.stop()
        self.sm.stop()
        waiter = threading.Thread(target=self.output.join)
        waiter.daemon = True
        waiter.start()
        waiter.join(DRAIN_TIMEOUT)
        if waiter.is_alive():
            _log("還有通知沒送完（等了 {:g} 秒），直接結束".format(DRAIN_TIMEOUT))
        if isinstance(self.formatter, LLMFormatter):
            self.formatter.close()
        self.recorder.write_summary(self.stats())
        self.recorder.close()

    def request_stop(self, *args):
        """signal handler：只設旗標，收尾交給主迴圈。"""
        self._stop.set()

    def run(self):
        """前景執行，直到 Ctrl+C 或 kill。回傳統計 dict。"""
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        self.start()
        try:
            while not self._stop.wait(0.5):
                try:
                    self.poll()
                except Exception as e:      # 雜事出錯不能讓整條線停下
                    _log("主迴圈發生錯誤（{!r}），繼續執行".format(e))
        finally:
            if self.verbose:
                _log("收到結束訊號，收尾中……")
            self.stop()
        stats = self.stats()
        if self.verbose:
            sys.stderr.write(format_stats(stats) + "\n")
        return stats

    # ── 統計 ──
    def stats(self):
        end = self.stopped_at or time.time()
        rx_lat = self.receiver.latency.summary()
        out_lat = self.recorder.latency.summary()
        return {
            "started": self.started_at,
            "started_str": _stamp(self.started_at) if self.started_at else None,
            "elapsed_sec": round(end - self.started_at, 1) if self.started_at else 0.0,
            "received": self.receiver.count,
            "dropped": self.receiver.dropped,
            "connects": getattr(self.receiver, "connects", None),
            "disconnects": getattr(self.receiver, "disconnects", None),
            "notices": self.recorder.count,
            "generators": dict(self.recorder.used),
            "line_sent": self.send_line,
            "line_ok": self.recorder.line_ok,
            "line_failed": self.recorder.line_failed,
            "receive_latency": rx_lat,          # 車上 ts → 筆電收到
            "notice_latency": out_lat,          # 車上 ts → 通知輸出完成（含產生文字與 LINE）
            "peak_rss_mb": round(_peak_rss_mb(), 1),
            "log_path": self.recorder.log_path,
        }


def _format_elapsed(seconds):
    seconds = int(max(0, seconds))
    return "{}:{:02d}:{:02d}".format(seconds // 3600, seconds % 3600 // 60, seconds % 60)


def _format_latency(summary):
    if not summary["count"]:
        return "沒有資料"
    return "{} 筆，中位數 {}，最大 {}，最小 {}".format(
        summary["count"], _ms(summary["median"]), _ms(summary["max"]), _ms(summary["min"]))


def format_stats(stats):
    lines = ["", "══════ 統計 ══════",
             "運作時間：{}（自 {}）".format(_format_elapsed(stats["elapsed_sec"]), stats["started_str"]),
             "收到資料：{} 筆（格式錯誤丟棄 {} 筆）".format(stats["received"], stats["dropped"])]
    if stats["connects"] is not None:
        lines.append("broker：連上 {} 次，非預期斷線 {} 次".format(stats["connects"], stats["disconnects"]))
    gens = "、".join("{} {} 則".format(k, v) for k, v in sorted(stats["generators"].items())) or "無"
    lines.append("通知：{} 則（訊息產生器：{}）".format(stats["notices"], gens))
    if stats["line_sent"]:
        lines.append("LINE：成功 {} 則，失敗 {} 則".format(stats["line_ok"], stats["line_failed"]))
    else:
        lines.append("LINE：dry-run，未實際送出")
    lines.append("延遲 車上→收到：" + _format_latency(stats["receive_latency"]))
    lines.append("延遲 車上→通知完成：" + _format_latency(stats["notice_latency"]))
    lines.append("（實車時車上與筆電時鐘不一定同步，延遲含時鐘差）")
    lines.append("記憶體峰值：{} MB".format(stats["peak_rss_mb"]))
    if stats["log_path"]:
        lines.append("通知紀錄：{}".format(stats["log_path"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="車外主程式：MQTT → 車外狀態機 → 訊息產生器 → LINE（預設 dry-run）")
    ap.add_argument("--host", help="broker 位址（預設 JETBOT_MQTT_HOST、.env 或 localhost）")
    ap.add_argument("--port", type=int, help="broker 埠號（預設 1883，TLS 為 8883）")
    ap.add_argument("--tls", action="store_true", default=None, help="啟用 TLS（預設看 JETBOT_MQTT_TLS）")
    ap.add_argument("--llm", choices=["template", "local", "cloud"],
                    help="訊息產生器（預設看 JETBOT_LLM_BACKEND，再沒有是 local）")
    ap.add_argument("--llm-timeout", type=float, help="LLM 逾時秒數（預設 3）")
    ap.add_argument("--send-line", action="store_true", help="真的送出 LINE（預設 dry-run）")
    ap.add_argument("--log", metavar="FILE", help="通知紀錄檔（預設 logs/notices-日期-時間.jsonl）")
    ap.add_argument("--no-log", action="store_true", help="不寫通知紀錄檔")
    ap.add_argument("--record", metavar="FILE", help="同時把收到的原始資料錄成 .jsonl")
    ap.add_argument("--replay", metavar="FILE", help="重播 .jsonl，不接 broker")
    ap.add_argument("--speed", type=float, default=1.0, help="重播速度（預設 1 照原本間隔）")
    ap.add_argument("--offline-sec", type=float, help="失聯門檻秒數（預設 5）")
    ap.add_argument("--status-every", type=float, default=STATUS_EVERY,
                    help="每幾秒印一行運作狀態（預設 60；0 不印）")
    args = ap.parse_args(argv)

    load_env()     # 專案根目錄 .env（已設定的環境變數優先），LINE token 與各項設定都在這裡
    send_line = args.send_line or _is_true(os.environ.get(ENV_SEND_LINE))
    if send_line and not os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"):
        _log("指定了實際送出 LINE，但找不到 LINE_CHANNEL_ACCESS_TOKEN；請確認 .env，或拿掉 --send-line 用 dry-run")
        return 2

    log_path = None
    if not args.no_log:
        log_path = args.log or os.path.join(LOG_DIR, "notices-{}.jsonl".format(time.strftime("%Y%m%d-%H%M%S")))

    runner = Runner(send_line=send_line, llm=args.llm, llm_timeout=args.llm_timeout, log_path=log_path,
                    replay_path=args.replay, speed=args.speed, record_path=args.record,
                    mqtt={"host": args.host, "port": args.port, "tls": args.tls},
                    offline_sec=args.offline_sec, status_every=args.status_every)
    runner.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
