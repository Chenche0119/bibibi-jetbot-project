"""sender.py 的行為測試。不需要真的 broker、不需要硬體，也不需要安裝 paho-mqtt。

    uv run python tests/test_sender.py

做法：在 import sender 之前，把一個假的 paho.mqtt.client 放進 sys.modules，
由測試控制「連上／斷線／publish 回傳值」，並記錄 sender 送出了什麼。
"""

import ast
import io
import json
import os
import sys
import tempfile
import time
import types

SENDER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "transport")
SENDER_PATH = os.path.join(SENDER_DIR, "sender.py")
sys.path.insert(0, SENDER_DIR)

passed = []
failed = []


def check(name, actual, expected):
    if actual == expected:
        passed.append(name)
        print("  ✅ {}".format(name))
    else:
        failed.append(name)
        print("  ❌ {}\n       預期 {!r}，實際 {!r}".format(name, expected, actual))


# ── 假的 paho-mqtt ────────────────────────────────────────────

class FakeInfo(object):
    def __init__(self, rc):
        self.rc = rc


class FakeClient(object):
    """只模擬 sender 用到的部分；不開任何 socket、不開執行緒。"""

    instances = []
    publish_rc = 0          # 下一次 publish 的回傳碼
    publish_raises = None   # 設成例外物件時，publish 會拋出

    def __init__(self, client_id="", clean_session=True):
        self.published = []
        self.connect_args = None
        self.loop_started = False
        self.stopped = False
        self.on_connect = None
        self.on_disconnect = None
        FakeClient.instances.append(self)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delay = (min_delay, max_delay)

    def connect_async(self, host, port=1883, keepalive=60):
        self.connect_args = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self, force=False):
        self.stopped = True

    def disconnect(self):
        pass

    def publish(self, topic, payload=None, qos=0, retain=False):
        if FakeClient.publish_raises is not None:
            raise FakeClient.publish_raises
        self.published.append((topic, payload, qos, retain))
        return FakeInfo(FakeClient.publish_rc)

    # 測試用：模擬背景執行緒呼叫回呼
    def fire_connect(self, rc=0):
        self.on_connect(self, None, {}, rc)

    def fire_disconnect(self, rc=1):
        self.on_disconnect(self, None, rc)


fake_client_mod = types.ModuleType("paho.mqtt.client")
fake_client_mod.Client = FakeClient
fake_client_mod.MQTT_ERR_SUCCESS = 0
fake_client_mod.MQTT_ERR_NO_CONN = 4
fake_client_mod.connack_string = lambda rc: "connack {}".format(rc)
fake_client_mod.error_string = lambda rc: "error {}".format(rc)
fake_paho = types.ModuleType("paho")
fake_mqtt = types.ModuleType("paho.mqtt")
fake_paho.mqtt = fake_mqtt
fake_mqtt.client = fake_client_mod
sys.modules["paho"] = fake_paho
sys.modules["paho.mqtt"] = fake_mqtt
sys.modules["paho.mqtt.client"] = fake_client_mod


class CaptureStderr(object):
    """收集 sender 印到 stderr 的提示。"""

    def __enter__(self):
        self.old = sys.stderr
        self.buf = io.StringIO()
        sys.stderr = self.buf
        return self

    def __exit__(self, *exc):
        sys.stderr = self.old

    def lines(self):
        return [l for l in self.buf.getvalue().splitlines() if l.strip()]


def fresh_sender(env=None):
    """重新載入 sender，回傳 (模組, 它建立的 FakeClient)。"""
    for key in ("JETBOT_MQTT_HOST", "JETBOT_MQTT_PORT"):
        os.environ.pop(key, None)
    for key, value in (env or {}).items():
        os.environ[key] = value
    sys.modules.pop("sender", None)
    FakeClient.instances = []
    FakeClient.publish_rc = 0
    FakeClient.publish_raises = None
    with CaptureStderr():
        import sender
    return sender, FakeClient.instances[-1]


def sample():
    return {"state": "locked", "events": [], "has_target": True,
            "readings": {"blocked_prob": 0.083, "distance_m": 0.912,
                         "speed_scale": 1.0, "target_h": 0.421}}


print("\n【一、載入】")
sender, client = fresh_sender({"JETBOT_MQTT_HOST": "10.0.0.5"})
check("broker 沒開也能載入，且已在背景啟動連線", client.loop_started, True)
check("使用凍結的 topic", sender.TOPIC, "jetbot/status")
check("一開始是未連線", sender.is_connected(), False)

print("\n【二、broker 位址可設定，不寫死】")
check("讀環境變數 JETBOT_MQTT_HOST，預設埠 1883", client.connect_args[:2], ("10.0.0.5", 1883))
sender, client = fresh_sender({"JETBOT_MQTT_HOST": "10.0.0.6", "JETBOT_MQTT_PORT": "11883"})
check("讀環境變數 JETBOT_MQTT_PORT", client.connect_args[:2], ("10.0.0.6", 11883))

tmpdir = tempfile.mkdtemp()
conf_path = os.path.join(tmpdir, "sender.env")
with io.open(conf_path, "w", encoding="utf-8") as f:
    f.write("# 車上的設定檔\nJETBOT_MQTT_HOST = 172.20.10.2\n\nJETBOT_MQTT_PORT=1884\n")
check("sender.env 解析（略過註解與空行）", sender._read_config_file(conf_path),
      {"JETBOT_MQTT_HOST": "172.20.10.2", "JETBOT_MQTT_PORT": "1884"})
sender.CONFIG_FILE = conf_path
os.environ.pop("JETBOT_MQTT_HOST", None)
os.environ.pop("JETBOT_MQTT_PORT", None)
check("沒有環境變數時讀 sender.env", sender._resolve_address(), ("172.20.10.2", 1884))
os.environ["JETBOT_MQTT_HOST"] = "10.0.0.7"
check("環境變數優先於 sender.env", sender._resolve_address()[0], "10.0.0.7")
check("configure 參數優先於環境變數", sender._resolve_address("10.0.0.8", 1999), ("10.0.0.8", 1999))
os.environ.pop("JETBOT_MQTT_HOST", None)
sender.CONFIG_FILE = os.path.join(tmpdir, "不存在.env")
with CaptureStderr() as cap:
    check("都沒設定時退回 localhost", sender._resolve_address(), ("localhost", 1883))
    sender._resolve_address()
check("退回 localhost 只提示一次", len(cap.lines()), 1)

sender, client = fresh_sender({"JETBOT_MQTT_HOST": "10.0.0.5"})
with CaptureStderr():
    sender.configure(host="10.0.0.9", port=1885)
new_client = FakeClient.instances[-1]
check("configure() 換成新位址並重新連線", new_client.connect_args[:2], ("10.0.0.9", 1885))
check("configure() 停掉舊的 client", client.stopped, True)
with CaptureStderr():
    new_client.fire_connect(0)
    client.fire_disconnect(0)
check("舊 client 的斷線回呼不影響新連線", sender.is_connected(), True)

print("\n【三、未連線時：立刻返回、丟棄、不拋例外】")
sender, client = fresh_sender({"JETBOT_MQTT_HOST": "10.0.0.5"})
with CaptureStderr() as cap:
    t0 = time.perf_counter()
    for _ in range(1000):
        sender.send(sample())
    elapsed_ms = (time.perf_counter() - t0) * 1000
check("未連線時不呼叫 publish（資料丟棄，不累積）", client.published, [])
check("未連線 1000 次 send 總耗時 < 200 ms（實測 {:.1f} ms）".format(elapsed_ms),
      elapsed_ms < 200, True)
check("未連線的提示只印一次", len(cap.lines()), 1)

print("\n【四、連上後發送】")
with CaptureStderr():
    client.fire_connect(0)
check("on_connect 後為已連線", sender.is_connected(), True)
payload = sample()
before = json.dumps(payload, sort_keys=True)
t_before = time.time()
sender.send(payload)
t_after = time.time()
topic, body, qos, retain = client.published[-1]
data = json.loads(body.decode("utf-8"))
check("topic、QoS 0、retain 關閉", (topic, qos, retain), ("jetbot/status", 0, False))
check("payload 是 UTF-8 bytes", isinstance(body, bytes), True)
check("沒有 ts 時補上發送當下時間", t_before <= data["ts"] <= t_after, True)
check("ts 是 float", isinstance(data["ts"], float), True)
check("其他欄位原封不動", {k: v for k, v in data.items() if k != "ts"}, sample())
check("不修改呼叫端傳入的 dict", json.dumps(payload, sort_keys=True), before)
check("呼叫端的 dict 沒有被加上 ts", "ts" in payload, False)

p = sample()
p["ts"] = 1234.5
sender.send(p)
check("已有 ts 不覆蓋", json.loads(client.published[-1][1].decode("utf-8"))["ts"], 1234.5)

p = sample()
p["events"] = ["notify_no_target"]
p["note"] = "測試中文"
sender.send(p)
raw = client.published[-1][1]
check("中文以 UTF-8 原樣送出（不轉成 \\u 跳脫）", "測試中文".encode("utf-8") in raw, True)

n = len(client.published)
for _ in range(20):
    sender.send(sample())
check("不節流：連續 20 次 send 就送 20 筆", len(client.published) - n, 20)

print("\n【五、錯誤處理：吞掉、不洗版】")
with CaptureStderr() as cap:
    FakeClient.publish_rc = 4
    for _ in range(50):
        sender.send(sample())
    FakeClient.publish_rc = 0
    FakeClient.publish_raises = OSError("網路斷了")
    for _ in range(50):
        sender.send(sample())
    FakeClient.publish_raises = None
    for _ in range(50):
        sender.send({"state": "locked", "bad": object()})
    for _ in range(50):
        sender.send("not a dict")
check("四種錯誤各 50 次都不拋例外，且各只提示一次", len(cap.lines()), 4)

print("\n【六、斷線與重連】")
with CaptureStderr() as cap:
    client.fire_disconnect(rc=7)
    check("斷線後為未連線", sender.is_connected(), False)
    n = len(client.published)
    for _ in range(30):
        sender.send(sample())
    check("斷線期間的資料丟棄", len(client.published) - n, 0)
    client.fire_connect(0)
    sender.send(sample())
    check("重連後資料恢復送出", len(client.published) - n, 1)
    client.fire_disconnect(rc=7)
    sender.send(sample())
lines = cap.lines()
check("斷線→丟棄→重連→再斷線：提示為 斷線、丟棄、連上、斷線、丟棄 各一次", len(lines), 5)

with CaptureStderr() as cap:
    client.fire_connect(5)
    client.fire_connect(5)
check("broker 拒絕連線只提示一次，且維持未連線", (len(cap.lines()), sender.is_connected()), (1, False))

print("\n【七、Python 3.6 相容性（靜態檢查）】")
with io.open(SENDER_PATH, encoding="utf-8") as f:
    source = f.read()
try:
    ast.parse(source, feature_version=(3, 6))
    check("以 Python 3.6 文法解析成功（無 walrus 等新語法）", True, True)
except SyntaxError as e:
    check("以 Python 3.6 文法解析成功（無 walrus 等新語法）", str(e), True)
tree = ast.parse(source)
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imported.add((node.module or "").split(".")[0])
check("不使用 dataclasses、__future__ 等 3.7+ 才能用的模組",
      sorted(imported & {"dataclasses", "__future__", "contextvars", "importlib_resources"}), [])
check("除了 paho 只用標準函式庫",
      sorted(imported - {"json", "os", "sys", "threading", "time", "paho"}), [])
check("沒有 f-string（沿用 .format() 風格）",
      any(isinstance(n, ast.JoinedStr) for n in ast.walk(tree)), False)

print("\n" + "=" * 46)
print("通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
if failed:
    print("失敗項目：" + "、".join(failed))
sys.exit(1 if failed else 0)
