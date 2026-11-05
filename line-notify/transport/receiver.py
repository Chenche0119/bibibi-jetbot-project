# -*- coding: utf-8 -*-
"""車外接收程式：把車上送來的回報交給 sink，自己不做任何判斷。

    Mosquitto（jetbot/status）或 .jsonl 檔 ──→ receiver ──→ sink.put("jetbot", payload)

sink 是任何有 put(source, payload) 方法的物件，例如 state_machine/machine.py 的 Cache。

兩種資料來源：

| 類別 | 資料來源 |
|---|---|
| MqttReceiver | 即時訂閱 jetbot/status；可同時錄製成 .jsonl |
| Replayer     | 重播 .jsonl；可照原本時間間隔（可加速）或盡快全部送出 |

兩者都有 start()／stop()（背景執行，不卡住 Jupyter 儲存格）與 run()（前景執行）。

Jupyter：

    import sys; sys.path.insert(0, "transport"); sys.path.insert(0, "state_machine")
    import receiver, machine
    cache = machine.Cache()
    rx = receiver.MqttReceiver(cache, record_path="record.jsonl").start()
    ...
    rx.stop(); rx.latency.print_summary()

命令列（在 repo 根目錄）：

    uv run --with "paho-mqtt<2.0" python transport/receiver.py                     # 即時接收並印出
    uv run --with "paho-mqtt<2.0" python transport/receiver.py --record rec.jsonl  # 同時錄製
    uv run python transport/receiver.py --replay rec.jsonl --speed 10              # 十倍速重播

broker 連線設定（參數 > 環境變數 > 專案根目錄 .env > 預設值），鍵名與 sender.py 相同：

    JETBOT_MQTT_HOST、JETBOT_MQTT_PORT              位址與埠號（預設 localhost；埠號 1883，啟用 TLS 時 8883）
    JETBOT_MQTT_USERNAME、JETBOT_MQTT_PASSWORD      帳密（雲端 broker 用）
    JETBOT_MQTT_TLS                                 1 啟用 TLS；不設定時不加密（區網測試）
    JETBOT_MQTT_CA_CERTS                            驗證 broker 憑證用的 CA 檔；不設定時用系統內建的 CA

設計重點：
- broker 沒開、中途斷線都不當掉，paho 背景執行緒自動重連，重連後重新訂閱
- 收到不是 JSON、不是 JSON 物件：丟棄，同一種錯誤只提示一次
- 欄位缺漏、state 或事件名稱不在規格內：照常交給 sink，只提示一次（要不要採用由任務三決定）
- 與 sender.py 相同，只用 paho-mqtt<2.0 的 API；paho 只有即時接收時才載入，重播不需要
- 只在車外筆電執行，不需相容 Python 3.6
"""

import argparse
import io
import json
import os
import signal
import sys
import threading
import time

# ── 已凍結的傳輸設定（docs/進度凍結-2026-09-16.md 變更一），與 sender.py 相同 ──
# 不 import sender：它一載入就會開始連線，那是車上的行為
TOPIC = "jetbot/status"
QOS = 0
SOURCE = "jetbot"                       # sink.put 的來源名稱，固定（任務二行前檔）

DEFAULT_HOST = "localhost"              # 區網測試時接收程式與 broker 同在車外筆電
DEFAULT_PORT = 1883
DEFAULT_TLS_PORT = 8883
KEEPALIVE = 5
RECONNECT_MIN = 1
RECONNECT_MAX = 5
ENV_HOST = "JETBOT_MQTT_HOST"
ENV_PORT = "JETBOT_MQTT_PORT"
ENV_USERNAME = "JETBOT_MQTT_USERNAME"
ENV_PASSWORD = "JETBOT_MQTT_PASSWORD"
ENV_TLS = "JETBOT_MQTT_TLS"
ENV_CA_CERTS = "JETBOT_MQTT_CA_CERTS"
TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off", "")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
# 筆電端的設定檔：與 LINE token 同一份，已被 .gitignore 忽略
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

# ── 規格內的欄位與名稱（docs/規格回覆-2026-09-19.md 第一～三節）──
STATES = ("waiting", "confirming", "locked", "detour", "impassable_wait")
EVENTS = (
    "enter_confirming", "cancel_confirming", "lock_acquired", "lock_lost",
    "relocked", "searching", "notify_no_target", "enter_detour", "detour_advance",
    "detour_done", "enter_impassable", "notify_impassable", "resume_follow",
)
READINGS = ("blocked_prob", "distance_m", "speed_scale", "target_h")


class _Warner(object):
    """同一種狀況只提示一次。"""

    def __init__(self, prefix):
        self.prefix = prefix
        self.seen = set()
        self._lock = threading.Lock()

    def once(self, key, msg):
        with self._lock:
            if key in self.seen:
                return
            self.seen.add(key)
        self.log(msg)

    def forget(self, *keys):
        with self._lock:
            for key in keys:
                self.seen.discard(key)

    def log(self, msg):
        try:
            sys.stderr.write("[{}] {}\n".format(self.prefix, msg))
            sys.stderr.flush()
        except Exception:
            pass


def read_env_file(path):
    """讀 KEY=VALUE 格式的設定檔，忽略空行與 # 註解；檔案不存在回傳空 dict。不寫入 os.environ。"""
    conf = {}
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                conf[key.strip()] = value.strip().strip("\"'")
    except (IOError, OSError):
        pass
    return conf


def resolve_mqtt_settings(host=None, port=None, username=None, password=None, tls=None, ca_certs=None,
                          env_file=None, warn=None):
    """決定 broker 連線設定：參數 > 環境變數 > .env > 預設值。規則與 sender.py 相同。

    回傳 dict：host、port、username、password、tls、ca_certs。
    warn 是 fn(key, msg)，設定有問題時呼叫（同一種只提示一次由呼叫端負責）。
    """
    conf = read_env_file(ENV_FILE if env_file is None else env_file)
    warn = warn or (lambda key, msg: None)

    def pick(value, key):
        if value is not None and value != "":
            return value
        return os.environ.get(key) or conf.get(key) or None

    host = pick(host, ENV_HOST) or DEFAULT_HOST
    port = pick(port, ENV_PORT)
    username = pick(username, ENV_USERNAME)
    password = pick(password, ENV_PASSWORD)
    tls = pick(tls, ENV_TLS) or False
    ca_certs = pick(ca_certs, ENV_CA_CERTS)

    if not isinstance(tls, bool):
        text = str(tls).strip().lower()
        if text in TRUE_WORDS:
            tls = True
        elif text in FALSE_WORDS:
            tls = False
        else:
            # 認不得就當作啟用：寧可連不上，也不要把帳密明文送出
            warn("bad_tls", "{}={!r} 看不懂，當作啟用 TLS；請填 1 或 0".format(ENV_TLS, tls))
            tls = True
    default_port = DEFAULT_TLS_PORT if tls else DEFAULT_PORT
    try:
        port = int(port) if port else default_port
    except ValueError:
        warn("bad_port", "埠號 {!r} 不是數字，改用 {}".format(port, default_port))
        port = default_port
    if not username:
        username = password = None
    elif not tls and host not in LOCAL_HOSTS:
        warn("plain_password", "有設定帳密但沒有啟用 TLS，帳密會以明文傳送；連雲端 broker 請設定 {}=1".format(ENV_TLS))
    return {"host": host, "port": port, "username": username, "password": password,
            "tls": tls, "ca_certs": ca_certs}


def describe_settings(settings):
    """連線方式的說明文字，給提示用；不含帳號與密碼。"""
    parts = ["TLS" if settings.get("tls") else "不加密"]
    if settings.get("username"):
        parts.append("使用帳密")
    return "，".join(parts)


def check_payload(payload):
    """回傳這筆資料不符合規格之處（list of (key, 說明)）；符合規格回傳 []。只檢查，不修改。"""
    problems = []

    def bad(key, msg):
        problems.append((key, msg))

    for field in ("state", "events", "has_target", "readings", "ts"):
        if field not in payload:
            bad("missing:" + field, "缺少欄位 {}".format(field))

    state = payload.get("state")
    if "state" in payload and state not in STATES:
        bad("state:{!r}".format(state), "state {!r} 不在規格的五個狀態內".format(state))

    events = payload.get("events")
    if "events" in payload:
        if not isinstance(events, list):
            bad("events_type", "events 應為陣列，收到 {}".format(type(events).__name__))
        else:
            for ev in events:
                if ev not in EVENTS:
                    bad("event:{!r}".format(ev), "事件 {!r} 不在規格的 13 種內".format(ev))

    if "has_target" in payload and not isinstance(payload["has_target"], bool):
        bad("has_target_type", "has_target 應為 true/false")

    readings = payload.get("readings")
    if "readings" in payload:
        if not isinstance(readings, dict):
            bad("readings_type", "readings 應為物件")
        else:
            for key in READINGS:
                if key not in readings:
                    bad("missing:readings." + key, "缺少欄位 readings.{}".format(key))

    ts = payload.get("ts")
    if "ts" in payload and (isinstance(ts, bool) or not isinstance(ts, (int, float))):
        bad("ts_type", "ts 應為 Unix 秒（數字），收到 {!r}".format(ts))
    return problems


class LatencyStats(object):
    """記錄「收到時間 − ts」。

    ⚠️ 車上與筆電的時鐘不一定同步，實車數據會混入兩邊的時鐘差（可能是負數）。
    用產生器在同一台筆電、即時模式（1 倍速）測試時沒有這個問題；
    加速模式的 ts 是模擬時間，延遲值沒有意義。
    """

    def __init__(self, maxlen=100000):
        self._lock = threading.Lock()
        self._values = []
        self._maxlen = maxlen

    def add(self, seconds):
        with self._lock:
            self._values.append(seconds)
            if len(self._values) > self._maxlen:
                del self._values[0]

    def reset(self):
        with self._lock:
            self._values = []

    def summary(self):
        """回傳 {"count", "median", "max", "min"}，單位秒；沒有資料時 median/max/min 為 None。"""
        with self._lock:
            values = sorted(self._values)
        n = len(values)
        if n == 0:
            return {"count": 0, "median": None, "max": None, "min": None}
        mid = n // 2
        median = values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2.0
        return {"count": n, "median": median, "max": values[-1], "min": values[0]}

    def format_summary(self):
        s = self.summary()
        if s["count"] == 0:
            return "延遲統計：沒有資料"
        return "延遲統計（收到時間 − ts）：{} 筆，中位數 {:.1f} ms，最大 {:.1f} ms，最小 {:.1f} ms".format(
            s["count"], s["median"] * 1000, s["max"] * 1000, s["min"] * 1000)

    def print_summary(self):
        print(self.format_summary())


class _Base(object):
    """解析、檢查、交給 sink、錄製、量延遲；MQTT 與重播共用。"""

    prefix = "receiver"

    def __init__(self, sink, record_path=None, clock=time.time, echo=False):
        if not hasattr(sink, "put"):
            raise TypeError("sink 必須有 put(source, payload) 方法")
        self.sink = sink
        self.clock = clock
        self.echo = echo
        self.warn = _Warner(self.prefix)
        self.latency = LatencyStats()
        self.count = 0                  # 交給 sink 的筆數
        self.dropped = 0                # 格式錯誤丟棄的筆數
        self._record_path = record_path
        self._record_file = None
        self._record_lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()

    # ── 單筆處理 ──

    def handle_bytes(self, body, measure_latency=True):
        """處理一筆原始資料（bytes 或 str）。回傳交給 sink 的 dict；丟棄時回傳 None。"""
        received = self.clock()
        try:
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            self.dropped += 1
            self.warn.once("not_json", "收到不是 JSON 的資料，已丟棄（之後同類錯誤不再提示）：{!r}".format(
                body[:80]))
            return None
        if not isinstance(payload, dict):
            self.dropped += 1
            self.warn.once("not_object", "收到的 JSON 不是物件（{}），已丟棄（之後不再提示）".format(
                type(payload).__name__))
            return None
        return self.handle_payload(payload, received if measure_latency else None)

    def handle_payload(self, payload, received=None):
        """把一筆 dict 交給 sink。received 有給時記錄延遲。"""
        for key, msg in check_payload(payload):
            self.warn.once(key, "{}；照常交給 sink（之後同類情況不再提示）".format(msg))

        ts = payload.get("ts")
        if received is not None and isinstance(ts, (int, float)) and not isinstance(ts, bool):
            self.latency.add(received - ts)

        self._record(payload)
        try:
            self.sink.put(SOURCE, payload)
        except Exception as e:
            self.warn.once("sink_{}".format(type(e).__name__),
                           "sink.put 發生錯誤（{}: {}），這筆略過".format(type(e).__name__, e))
        else:
            self.count += 1
        if self.echo:
            try:
                sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except Exception:
                pass
        return payload

    # ── 錄製（格式同規格回覆第五節：每行一筆 payload，含 ts）──

    def _record(self, payload):
        if not self._record_path:
            return
        try:
            with self._record_lock:
                if self._record_file is None:
                    self._record_file = io.open(self._record_path, "a", encoding="utf-8")
                self._record_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._record_file.flush()
        except (IOError, OSError) as e:
            self.warn.once("record", "錄製寫檔失敗（{}），接收照常進行".format(e))

    def _close_record(self):
        with self._record_lock:
            if self._record_file is not None:
                self._record_file.close()
                self._record_file = None

    # ── 背景執行 ──

    def start(self):
        """背景執行，立刻返回（不卡住 Jupyter 儲存格）。回傳自己，方便 rx = X(...).start()。"""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_safely, name=self.prefix)
        self._thread.daemon = True
        self._thread.start()
        return self

    def _run_safely(self):
        try:
            self.run()
        except Exception as e:
            self.warn.log("背景執行發生未預期錯誤（{}: {}），已停止".format(type(e).__name__, e))

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        self._close_record()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout=None):
        """等背景執行結束（重播用）。"""
        if self._thread is not None:
            self._thread.join(timeout)


class MqttReceiver(_Base):
    """即時訂閱 jetbot/status。

    連線設定：參數 > 環境變數 > 專案根目錄 .env > 預設值（見 resolve_mqtt_settings）。
    帳密、TLS 都沒設定時，與只有位址的舊版行為相同。
    """

    prefix = "receiver"

    def __init__(self, sink, host=None, port=None, record_path=None, clock=time.time, echo=False,
                 username=None, password=None, tls=None, ca_certs=None, env_file=None):
        _Base.__init__(self, sink, record_path=record_path, clock=clock, echo=echo)
        self.settings = resolve_mqtt_settings(host, port, username, password, tls, ca_certs,
                                              env_file=env_file, warn=self.warn.once)
        self.host = self.settings["host"]
        self.port = self.settings["port"]
        self.connected = False
        self.connects = 0               # 連上的次數（含重連），給長時間執行的統計用
        self.disconnects = 0            # 非預期斷線的次數
        self._client = None

    def _on_connect(self, client, userdata, flags, rc):
        if client is not self._client:
            return
        if rc == 0:
            self.connected = True
            self.connects += 1
            self.warn.forget("disconnected")
            # 每次連上都重新訂閱：clean_session 下斷線重連後舊訂閱不會保留
            client.subscribe(TOPIC, qos=QOS)
            self.warn.log("已連上 broker {}:{}（{}），訂閱 {}".format(
                self.host, self.port, describe_settings(self.settings), TOPIC))
        else:
            self.connected = False
            hint = "；請檢查帳號密碼" if rc in (4, 5) else ""
            self.warn.once("connack_{}".format(rc), "broker 拒絕連線（rc={}）{}".format(rc, hint))

    def _on_disconnect(self, client, userdata, rc):
        if client is not self._client:
            return
        was = self.connected
        self.connected = False
        if rc != 0 and was:
            self.disconnects += 1
            self.warn.once("disconnected", "與 broker 斷線（rc={}），背景自動重連中".format(rc))

    def _on_message(self, client, userdata, msg):
        try:
            self.handle_bytes(msg.payload)
        except Exception as e:          # 絕不讓 paho 的背景執行緒因為一筆資料掛掉
            self.warn.once("unexpected_{}".format(type(e).__name__),
                           "處理資料時發生未預期錯誤（{}: {}），這筆略過".format(type(e).__name__, e))

    def start(self):
        """開始背景接收，立刻返回。broker 還沒開也不會失敗，會一直在背景重試。"""
        if self._client is not None:
            return self
        self._stop.clear()
        import paho.mqtt.client as mqtt     # 只有即時接收需要 paho-mqtt<2.0

        client = mqtt.Client(client_id="", clean_session=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=RECONNECT_MIN, max_delay=RECONNECT_MAX)
        if self.settings["username"]:
            client.username_pw_set(self.settings["username"], self.settings["password"])
        self._client = client
        if self.settings["tls"]:
            try:
                # ca_certs 為 None 時用系統內建的 CA，並驗證憑證與主機名稱
                client.tls_set(ca_certs=self.settings["ca_certs"])
            except Exception as e:
                # 例如 CA 檔不存在。不退回不加密連線（帳密會外洩），也不連線
                self.warn.once("tls_setup", "TLS 設定失敗（{}: {}），不會連線；請檢查 {}".format(
                    type(e).__name__, e, ENV_CA_CERTS))
                return self
        # 與 sender.py 相同：connect_async＋loop_start，連線與重連都在背景執行緒，
        # 第一次連不上也會持續重試
        client.connect_async(self.host, self.port, keepalive=KEEPALIVE)
        client.loop_start()
        self.warn.log("嘗試連線 broker {}:{}（{}，背景進行）".format(
            self.host, self.port, describe_settings(self.settings)))
        return self

    def stop(self, timeout=5.0):
        client = self._client
        self._client = None
        self.connected = False
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass
        self._stop.set()
        self._close_record()

    def is_running(self):
        return self._client is not None

    def run(self, stats_every=None):
        """前景執行直到 Ctrl+C。stats_every 秒數有給時定期印延遲統計。"""
        self.start()
        last = time.monotonic()
        try:
            while not self._stop.wait(0.2):
                if stats_every and time.monotonic() - last >= stats_every:
                    last = time.monotonic()
                    self.warn.log(self.latency.format_summary())
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


class Replayer(_Base):
    """重播 .jsonl（產生器寫的檔、接收程式錄的檔、組員桌上錄製的檔格式都相同）。

    speed    None 或 0：盡快全部送出；1.0：照原本時間間隔；10：十倍速
    retime   True 時把 ts 整體平移，讓第一筆的 ts 等於開始重播的時間（間隔不變）。
             接到會檢查資料是否過期的狀態機時要開；想保留原始 ts 時關（預設）
    重播不量延遲（ts 是過去的時間）。
    """

    prefix = "replay"

    def __init__(self, sink, path, speed=None, retime=False, clock=time.time, echo=False):
        _Base.__init__(self, sink, record_path=None, clock=clock, echo=echo)
        self.path = path
        self.speed = speed
        self.retime = retime

    def _lines(self):
        with io.open(self.path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    yield lineno, line

    def run(self):
        """前景重播，播完或 stop() 後返回。回傳交給 sink 的筆數。"""
        first_ts = None
        wall_start = None
        shift = 0.0
        for lineno, line in self._lines():
            if self._stop.is_set():
                break
            try:
                payload = json.loads(line)
            except ValueError:
                self.dropped += 1
                self.warn.once("not_json", "第 {} 行不是 JSON，已略過（之後不再提示）".format(lineno))
                continue
            if not isinstance(payload, dict):
                self.dropped += 1
                self.warn.once("not_object", "第 {} 行不是 JSON 物件，已略過（之後不再提示）".format(lineno))
                continue

            ts = payload.get("ts")
            has_ts = isinstance(ts, (int, float)) and not isinstance(ts, bool)
            if has_ts and first_ts is None:
                first_ts = ts
                wall_start = time.monotonic()
                shift = self.clock() - ts if self.retime else 0.0
            if self.speed and self.speed > 0:
                if not has_ts:
                    self.warn.once("no_ts", "第 {} 行沒有 ts，無法照時間間隔重播，這類資料立即送出".format(lineno))
                else:
                    delay = wall_start + (ts - first_ts) / self.speed - time.monotonic()
                    if delay > 0 and self._stop.wait(delay):
                        break
            if has_ts and self.retime:
                payload["ts"] = round(ts + shift, 3)
            self.handle_payload(payload)
        return self.count


def replay(path, sink, speed=None, retime=False, echo=False):
    """前景重播的捷徑。回傳 Replayer（可看 .count、.dropped）。"""
    r = Replayer(sink, path, speed=speed, retime=retime, echo=echo)
    r.run()
    return r


class PrintSink(object):
    """命令列預設的 sink：什麼都不存，只數筆數（印出由 echo 負責）。"""

    def __init__(self):
        self.count = 0

    def put(self, source, payload):
        self.count += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="車外接收程式：MQTT 或 .jsonl → sink")
    ap.add_argument("--replay", metavar="FILE", help="重播 .jsonl，而不是訂閱 MQTT")
    ap.add_argument("--speed", type=float, default=None,
                    help="重播速度：1 照原本間隔、10 十倍速；不給為盡快全部送出")
    ap.add_argument("--retime", action="store_true", help="重播時把 ts 平移到現在")
    ap.add_argument("--record", metavar="FILE", help="即時接收時同時錄製成 .jsonl（附加寫入）")
    ap.add_argument("--host", help="broker 位址（預設環境變數 JETBOT_MQTT_HOST、.env 或 localhost）")
    ap.add_argument("--port", type=int, help="broker 埠號（預設 1883，TLS 為 8883）")
    ap.add_argument("--tls", action="store_true", default=None,
                    help="啟用 TLS（預設看 JETBOT_MQTT_TLS）；帳密只從環境變數或 .env 讀，不提供命令列參數")
    ap.add_argument("--quiet", action="store_true", help="不印出每一筆")
    ap.add_argument("--stats-every", type=float, default=None, help="每幾秒印一次延遲統計")
    args = ap.parse_args(argv)

    sink = PrintSink()
    if args.replay:
        r = replay(args.replay, sink, speed=args.speed, retime=args.retime, echo=not args.quiet)
        sys.stderr.write("[replay] 完成：交給 sink {} 筆，丟棄 {} 筆\n".format(r.count, r.dropped))
        return 0

    rx = MqttReceiver(sink, host=args.host, port=args.port, record_path=args.record,
                      echo=not args.quiet, tls=args.tls)
    # kill（SIGTERM）也照 Ctrl+C 的方式收尾並印統計
    signal.signal(signal.SIGTERM, lambda signum, frame: rx._stop.set())
    rx.run(stats_every=args.stats_every)
    sys.stderr.write("[receiver] 結束：交給 sink {} 筆，丟棄 {} 筆\n".format(rx.count, rx.dropped))
    sys.stderr.write("[receiver] {}\n".format(rx.latency.format_summary()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
