# -*- coding: utf-8 -*-
"""車上發送端：把跟隨狀態機的回報以 MQTT 送到車外筆電。

車上用法（跟隨狀態機那邊只需要一行）：

    import sender
    sender.send({"state": "locked", "events": [], "has_target": True, "readings": {...}})

設計重點：
- send() 不等網路：連線、重連都在 paho 的背景執行緒進行，send() 只把資料交給它
- 沒連上 broker 時資料直接丟棄，不累積（QoS 0 本來就不保證送達）
- 任何錯誤都不往外拋；同一種錯誤只印一次，避免洗版
- 不節流：節流已在車上的 report_to_caregiver() 做了，收到就送
- payload 沒有 ts 時補上發送當下的 time.time()；不修改呼叫端傳入的 dict

broker 連線設定（依優先順序，**不寫死 IP 與帳密**）：
1. 呼叫 sender.configure(host="...", username="...", tls=True, ...)
2. 環境變數（見下表）
3. 與本檔同資料夾的 sender.env（每行 KEY=VALUE，鍵名同環境變數）
4. 預設值

| 環境變數 | 用途 | 預設 |
|---|---|---|
| JETBOT_MQTT_HOST | broker 位址 | localhost（並提示一次） |
| JETBOT_MQTT_PORT | 埠號 | 1883；啟用 TLS 時 8883 |
| JETBOT_MQTT_USERNAME、JETBOT_MQTT_PASSWORD | 帳密（雲端 broker 用） | 不使用帳密 |
| JETBOT_MQTT_TLS | 1／true／yes／on 啟用 TLS | 不加密（區網測試） |
| JETBOT_MQTT_CA_CERTS | 驗證 broker 憑證用的 CA 檔 | 系統內建的 CA（雲端 broker 用這個即可） |

帳密、TLS 都沒設定時，行為與只有位址的舊版完全相同（區網、匿名、不加密）。

相容性：必須能在 Jetson Nano（JetPack 4.6，Python 3.6）執行，
不使用 3.7 以後才有的語法與模組（walrus、dataclasses 等）；MQTT 用 paho-mqtt<2.0。
除了 paho-mqtt 不依賴其他套件，整支檔案可以單獨複製到車上。
"""

import json
import os
import sys
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError as _e:
    # 讓車上的 `try: import sender` 失敗並退回印字，原因會顯示在車上的提示中
    raise ImportError(
        "sender 需要 paho-mqtt<2.0（{}）；安裝：pip3 install --user 'paho-mqtt<2.0'".format(_e))

# ── 已凍結的傳輸設定（docs/進度凍結-2026-09-16.md 變更一），不要改 ──
TOPIC = "jetbot/status"
QOS = 0
RETAIN = False

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 1883
KEEPALIVE = 5            # 秒；熱點斷掉但 TCP 沒收到 FIN 時，約 2 倍此值內察覺
RECONNECT_MIN = 1        # 重連等待（秒），失敗時逐次加倍到上限
RECONNECT_MAX = 5

DEFAULT_TLS_PORT = 8883

ENV_HOST = "JETBOT_MQTT_HOST"
ENV_PORT = "JETBOT_MQTT_PORT"
ENV_USERNAME = "JETBOT_MQTT_USERNAME"
ENV_PASSWORD = "JETBOT_MQTT_PASSWORD"
ENV_TLS = "JETBOT_MQTT_TLS"
ENV_CA_CERTS = "JETBOT_MQTT_CA_CERTS"
TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off", "")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sender.env")

_lock = threading.Lock()
_client = None
_connected = False
_host = None
_port = None
_settings = {}
_warned = set()


def _log(msg):
    """印到 stderr，前綴 [sender] 方便在車上 console 辨識。"""
    try:
        sys.stderr.write("[sender] {}\n".format(msg))
        sys.stderr.flush()
    except Exception:
        pass


def _warn_once(key, msg):
    """同一種狀況只提示一次；key 由呼叫端決定「哪些算同一種」。"""
    if key in _warned:
        return
    _warned.add(key)
    _log(msg)


def _read_config_file(path):
    """讀 KEY=VALUE 格式的設定檔，忽略空行與 # 註解；檔案不存在回傳空 dict。"""
    conf = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                conf[key.strip()] = value.strip().strip("\"'")
    except (IOError, OSError):
        pass
    return conf


def _parse_bool(value):
    """把設定值轉成 True/False。認不得的值當作 True（寧可要求加密連不上，也不要把帳密明文送出）。"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text not in FALSE_WORDS:
        _warn_once("bad_tls", "{}={!r} 看不懂，當作啟用 TLS；請填 1 或 0".format(ENV_TLS, value))
        return True
    return False


def _resolve_settings(host=None, port=None, username=None, password=None, tls=None, ca_certs=None):
    """依優先順序決定連線設定：參數 > 環境變數 > sender.env > 預設值。

    回傳 dict：host、port、username、password、tls、ca_certs。
    沒有帳號時 username、password 為 None；沒有指定 CA 檔時 ca_certs 為 None（用系統內建的 CA）。
    """
    conf = _read_config_file(CONFIG_FILE)

    def pick(value, key):
        if value is not None and value != "":
            return value
        return os.environ.get(key) or conf.get(key) or None

    host = pick(host, ENV_HOST)
    port = pick(port, ENV_PORT)
    username = pick(username, ENV_USERNAME)
    password = pick(password, ENV_PASSWORD)
    tls = _parse_bool(pick(tls, ENV_TLS) or False)
    ca_certs = pick(ca_certs, ENV_CA_CERTS)

    if not host:
        _warn_once("default_host",
                   "沒有設定 broker 位址，暫用 {}；請設定環境變數 {} 或 {}".format(
                       DEFAULT_HOST, ENV_HOST, CONFIG_FILE))
        host = DEFAULT_HOST
    default_port = DEFAULT_TLS_PORT if tls else DEFAULT_PORT
    try:
        port = int(port) if port else default_port
    except ValueError:
        _warn_once("bad_port", "埠號 {!r} 不是數字，改用 {}".format(port, default_port))
        port = default_port
    if not username:
        username = password = None
    elif not tls and host not in LOCAL_HOSTS:
        _warn_once("plain_password",
                   "有設定帳密但沒有啟用 TLS，帳密會以明文傳送；連雲端 broker 請設定 {}=1".format(ENV_TLS))
    return {"host": host, "port": port, "username": username, "password": password,
            "tls": tls, "ca_certs": ca_certs}


def _resolve_address(host=None, port=None):
    """只取 broker 位址與埠號（舊介面，保留給既有程式與測試）。"""
    settings = _resolve_settings(host, port)
    return settings["host"], settings["port"]


def _describe(settings):
    """連線方式的說明文字，給提示用；不含帳號與密碼。"""
    parts = ["TLS" if settings.get("tls") else "不加密"]
    if settings.get("username"):
        parts.append("使用帳密")
    return "，".join(parts)


def _on_connect(client, userdata, flags, rc):
    global _connected
    if client is not _client:      # configure() 換掉的舊 client，忽略
        return
    if rc == 0:
        _connected = True
        # 連上後清掉「未連線」類的提示紀錄，下次斷線時會再提示一次
        for key in ("not_connected", "disconnected"):
            _warned.discard(key)
        _log("已連上 broker {}:{}（{}），topic={}".format(_host, _port, _describe(_settings), TOPIC))
    else:
        _connected = False
        hint = "；請檢查帳號密碼" if rc in (4, 5) else ""
        _warn_once("connack_{}".format(rc),
                   "broker 拒絕連線（rc={}：{}）{}".format(rc, mqtt.connack_string(rc), hint))


def _on_disconnect(client, userdata, rc):
    global _connected
    if client is not _client:
        return
    was_connected = _connected
    _connected = False
    if rc != 0 and was_connected:
        _warn_once("disconnected", "與 broker 斷線（rc={}），背景自動重連中".format(rc))


def _start(host=None, port=None, username=None, password=None, tls=None, ca_certs=None):
    """建立 client 並啟動背景執行緒；只做本機操作，不等網路。呼叫前需持有 _lock。"""
    global _client, _host, _port, _settings
    _settings = _resolve_settings(host, port, username, password, tls, ca_certs)
    _host, _port = _settings["host"], _settings["port"]

    client = mqtt.Client(client_id="", clean_session=True)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.reconnect_delay_set(min_delay=RECONNECT_MIN, max_delay=RECONNECT_MAX)
    if _settings["username"]:
        client.username_pw_set(_settings["username"], _settings["password"])
    if _settings["tls"]:
        try:
            # ca_certs 為 None 時用系統內建的 CA，並驗證憑證與主機名稱（雲端 broker 的正式憑證）
            client.tls_set(ca_certs=_settings["ca_certs"])
        except Exception as e:
            # 例如 CA 檔不存在。不退回不加密連線（帳密會外洩），這個 client 不連線，send() 照常丟棄
            _client = client
            _warn_once("tls_setup", "TLS 設定失敗（{}: {}），不會連線；請檢查 {}".format(
                type(e).__name__, e, ENV_CA_CERTS))
            return
    # connect_async 只記下位址，真正的連線（含 DNS）在 loop_start 的背景執行緒進行；
    # 第一次連不上也會一直重試（paho 1.x 的 _thread_main 使用 retry_first_connection=True）
    _client = client               # 先設好，回呼才認得這個 client
    client.connect_async(_host, _port, keepalive=KEEPALIVE)
    client.loop_start()
    _log("嘗試連線 broker {}:{}（{}，背景進行，不影響車上）".format(_host, _port, _describe(_settings)))


def configure(host=None, port=None, username=None, password=None, tls=None, ca_certs=None):
    """指定 broker 連線設定並（重新）啟動連線。可在 Jupyter 裡於 import 後呼叫。

    沒有給的參數依環境變數、sender.env、預設值決定。
    """
    global _connected
    try:
        with _lock:
            old = _client
            _connected = False
            _warned.clear()
            _start(host, port, username, password, tls, ca_certs)
        if old is not None:
            old.disconnect()
            old.loop_stop()
    except Exception as e:
        _warn_once("configure", "configure 失敗：{}".format(e))


def stop():
    """停止背景連線（一般不需要呼叫；執行緒是 daemon，程式結束時會自動收掉）。

    停止後再呼叫 send() 會重新啟動連線。
    """
    global _client, _connected
    with _lock:
        client = _client
        _client = None
        _connected = False
    if client is not None:
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            pass


def is_connected():
    """目前是否連著 broker（僅供除錯顯示）。"""
    return _connected


def send(payload):
    """把一筆回報送到車外。立刻返回，永不拋例外。

    payload 沒有 ts 時補上現在時間（Unix 秒，float）；已有 ts 不覆蓋。
    沒連上 broker 時這筆直接丟棄。
    """
    try:
        if _client is None:
            with _lock:
                if _client is None:
                    _start()

        if not isinstance(payload, dict):
            _warn_once("not_dict", "payload 必須是 dict，收到 {}，已略過".format(
                type(payload).__name__))
            return

        data = dict(payload)          # 淺複製即可：只在最上層加 ts，不動呼叫端的物件
        if "ts" not in data:
            data["ts"] = time.time()

        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            _warn_once("json", "payload 無法轉成 JSON（{}），已略過".format(e))
            return

        if not _connected:
            _warn_once("not_connected",
                       "尚未連上 broker {}:{}，資料先丟棄（連上後會再提示）".format(_host, _port))
            return

        info = _client.publish(TOPIC, body, qos=QOS, retain=RETAIN)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            _warn_once("publish_rc_{}".format(info.rc),
                       "發送失敗（rc={}：{}）".format(info.rc, mqtt.error_string(info.rc)))
    except Exception as e:
        _warn_once("unexpected_{}".format(type(e).__name__),
                   "send 發生未預期錯誤（{}: {}），已略過".format(type(e).__name__, e))


# 載入時就開始背景連線，讓第一筆資料到來前有機會先連上。
# 失敗也不影響載入：send() 會在第一次呼叫時再試著啟動。
try:
    with _lock:
        _start()
except Exception as _e:
    _client = None
    _warn_once("start", "背景連線啟動失敗（{}），將於第一次 send() 時重試".format(_e))
