"""sender.py、receiver.py 的連線認證（帳密、TLS）測試。不需要 broker、雲端或網路，也不需要安裝 paho-mqtt。

    uv run python tests/test_mqtt_auth.py

做法與 test_sender.py 相同：在 import 之前把假的 paho.mqtt.client 放進 sys.modules，
記錄 username_pw_set()、tls_set()、connect_async() 收到什麼。
"""

import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "transport"))

ENV_KEYS = ("JETBOT_MQTT_HOST", "JETBOT_MQTT_PORT", "JETBOT_MQTT_USERNAME", "JETBOT_MQTT_PASSWORD",
            "JETBOT_MQTT_TLS", "JETBOT_MQTT_CA_CERTS")
PASSWORD = "p@ss-不可外洩-123"

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
    """只模擬 sender／receiver 用到的部分；不開任何 socket、不開執行緒。"""

    instances = []
    tls_raises = None      # 設成例外物件時，tls_set 會拋出（模擬 CA 檔不存在）

    def __init__(self, client_id="", clean_session=True):
        self.published = []
        self.subscribed = []
        self.connect_args = None
        self.credentials = None
        self.tls = None
        self.loop_started = False
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        FakeClient.instances.append(self)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        pass

    def username_pw_set(self, username, password=None):
        self.credentials = (username, password)

    def tls_set(self, ca_certs=None, certfile=None, keyfile=None, cert_reqs=None, tls_version=None,
                ciphers=None, keyfile_password=None):
        if FakeClient.tls_raises is not None:
            raise FakeClient.tls_raises
        self.tls = {"ca_certs": ca_certs}

    def connect_async(self, host, port=1883, keepalive=60):
        self.connect_args = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self, force=False):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return FakeInfo(0)

    def fire_connect(self, rc=0):
        self.on_connect(self, None, {}, rc)

    def fire_disconnect(self, rc=1):
        self.on_disconnect(self, None, rc)


fake_client_mod = types.ModuleType("paho.mqtt.client")
fake_client_mod.Client = FakeClient
fake_client_mod.MQTT_ERR_SUCCESS = 0
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
    def __enter__(self):
        self.old = sys.stderr
        self.buf = io.StringIO()
        sys.stderr = self.buf
        return self

    def __exit__(self, *exc):
        sys.stderr = self.old

    def text(self):
        return self.buf.getvalue()

    def lines(self):
        return [l for l in self.text().splitlines() if l.strip()]


def set_env(env=None):
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    for key, value in (env or {}).items():
        os.environ[key] = value


tmpdir = tempfile.mkdtemp()
NO_FILE = os.path.join(tmpdir, "不存在.env")


def write_conf(name, text):
    path = os.path.join(tmpdir, name)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ═══════════════════════════════════════════════════════════
#  sender.py
# ═══════════════════════════════════════════════════════════

set_env({"JETBOT_MQTT_HOST": "10.0.0.5"})
sys.modules.pop("sender", None)
with CaptureStderr():
    import sender
sender.CONFIG_FILE = NO_FILE      # 不受開發者自己放的 transport/sender.env 影響


def sender_client(env=None, **kwargs):
    """依 env 重新 configure，回傳新建立的 FakeClient 與這段期間的 stderr。"""
    set_env(env)
    with CaptureStderr() as cap:
        sender.configure(**kwargs)
    return FakeClient.instances[-1], cap


print("\n【一、sender：未設定帳密與 TLS 時維持原本行為】")
client, cap = sender_client({"JETBOT_MQTT_HOST": "172.20.10.2"})
check("不呼叫 username_pw_set", client.credentials, None)
check("不呼叫 tls_set", client.tls, None)
check("埠號仍為 1883", client.connect_args[:2], ("172.20.10.2", 1883))
check("提示寫明不加密", "不加密" in cap.text(), True)
check("_resolve_address() 舊介面不變", sender._resolve_address(), ("172.20.10.2", 1883))
client, _ = sender_client({"JETBOT_MQTT_HOST": "h", "JETBOT_MQTT_TLS": "0"})
check("JETBOT_MQTT_TLS=0 視為不加密", (client.tls, client.connect_args[1]), (None, 1883))
client, _ = sender_client({"JETBOT_MQTT_HOST": "h", "JETBOT_MQTT_PASSWORD": PASSWORD})
check("只有密碼沒有帳號：不使用帳密", client.credentials, None)

print("\n【二、sender：帳密與 TLS 從環境變數帶入】")
env = {"JETBOT_MQTT_HOST": "abc.s1.eu.hivemq.cloud", "JETBOT_MQTT_USERNAME": "jetbot",
       "JETBOT_MQTT_PASSWORD": PASSWORD, "JETBOT_MQTT_TLS": "1"}
client, cap = sender_client(env)
check("帳密帶入 username_pw_set", client.credentials, ("jetbot", PASSWORD))
check("啟用 TLS，沒指定 CA 時用系統內建（ca_certs=None）", client.tls, {"ca_certs": None})
check("TLS 沒指定埠號時預設 8883", client.connect_args[:2], ("abc.s1.eu.hivemq.cloud", 8883))
check("提示寫明 TLS、使用帳密", "TLS，使用帳密" in cap.text(), True)
check("密碼不出現在任何提示", PASSWORD in cap.text(), False)
check("帳號也不出現在提示", "jetbot，" in cap.text() or "帳號 jetbot" in cap.text(), False)

client, _ = sender_client(dict(env, JETBOT_MQTT_PORT="8884", JETBOT_MQTT_CA_CERTS="/tmp/ca.crt"))
check("指定埠號時沿用", client.connect_args[1], 8884)
check("JETBOT_MQTT_CA_CERTS 帶入 tls_set", client.tls, {"ca_certs": "/tmp/ca.crt"})
for word in ("true", "YES", "on"):
    client, _ = sender_client({"JETBOT_MQTT_HOST": "h", "JETBOT_MQTT_TLS": word})
    check("JETBOT_MQTT_TLS={} 視為啟用".format(word), client.tls is not None, True)
client, cap = sender_client({"JETBOT_MQTT_HOST": "h", "JETBOT_MQTT_TLS": "加密"})
check("看不懂的 TLS 設定當作啟用（寧可連不上也不明文送帳密）", client.tls is not None, True)
check("看不懂的 TLS 設定會提示", "看不懂" in cap.text(), True)

print("\n【三、sender：優先順序 參數 > 環境變數 > sender.env > 預設值】")
sender.CONFIG_FILE = write_conf("sender.env", "\n".join([
    "# 車上的設定檔", "JETBOT_MQTT_HOST=broker.example.com", "JETBOT_MQTT_USERNAME=car",
    "JETBOT_MQTT_PASSWORD='{}'".format(PASSWORD), "JETBOT_MQTT_TLS=1", ""]))
client, _ = sender_client({})
check("只有 sender.env：位址、帳密、TLS 都讀檔", (client.connect_args[:2], client.credentials, client.tls),
      (("broker.example.com", 8883), ("car", PASSWORD), {"ca_certs": None}))
client, _ = sender_client({"JETBOT_MQTT_USERNAME": "env-user", "JETBOT_MQTT_TLS": "0",
                           "JETBOT_MQTT_PORT": "1883"})
check("環境變數優先於 sender.env", (client.credentials[0], client.tls), ("env-user", None))
client, _ = sender_client({"JETBOT_MQTT_USERNAME": "env-user"}, username="arg-user", password="arg-pw",
                          tls=False, port=1884)
check("configure() 參數優先於環境變數", (client.credentials, client.tls, client.connect_args[1]),
      (("arg-user", "arg-pw"), None, 1884))
sender.CONFIG_FILE = NO_FILE

print("\n【四、sender：帳密明文的提示】")
_, cap = sender_client({"JETBOT_MQTT_HOST": "broker.example.com", "JETBOT_MQTT_USERNAME": "u",
                        "JETBOT_MQTT_PASSWORD": PASSWORD})
check("連外部主機有帳密卻沒 TLS：提示會明文傳送", "明文" in cap.text(), True)
_, cap = sender_client({"JETBOT_MQTT_HOST": "localhost", "JETBOT_MQTT_USERNAME": "u",
                        "JETBOT_MQTT_PASSWORD": PASSWORD})
check("連 localhost 不提示明文", "明文" in cap.text(), False)

print("\n【五、sender：既有行為不退步】")
client, _ = sender_client(env)
check("TLS 模式仍用 connect_async＋loop_start（不等網路）", (client.connect_args is not None,
                                                             client.loop_started), (True, True))
with CaptureStderr() as cap:
    sender.send({"state": "locked"})
    check("未連上時 send() 丟棄", client.published, [])
    client.fire_connect(0)
    sender.send({"state": "locked"})
    check("連上後 send() 送出", len(client.published), 1)
    client.fire_disconnect(1)
    sender.send({"state": "locked"})
    client.fire_connect(0)
    sender.send({"state": "locked"})
check("斷線重連後恢復送出", len(client.published), 2)
check("送出內容與未加密時相同（topic、JSON）", (client.published[-1][0],
                                            json.loads(client.published[-1][1].decode("utf-8"))["state"]),
      ("jetbot/status", "locked"))
with CaptureStderr() as cap:
    client.fire_connect(4)
    client.fire_connect(4)
check("帳密錯誤（rc=4）只提示一次，並提醒檢查帳密", (len(cap.lines()), "帳號密碼" in cap.text()), (1, True))
check("帳密錯誤時為未連線", sender.is_connected(), False)

print("\n【六、sender：TLS 設定失敗】")
FakeClient.tls_raises = IOError("找不到 CA 檔")
client, cap = sender_client(dict(env, JETBOT_MQTT_CA_CERTS="/不存在/ca.crt"))
FakeClient.tls_raises = None
check("TLS 設定失敗時不連線（不退回明文）", (client.connect_args, client.loop_started), (None, False))
check("TLS 設定失敗會提示", "TLS 設定失敗" in cap.text(), True)
with CaptureStderr():
    for _ in range(20):
        sender.send({"state": "locked"})
check("之後 send() 照常立刻返回、不拋例外、不送出", client.published, [])


# ═══════════════════════════════════════════════════════════
#  receiver.py
# ═══════════════════════════════════════════════════════════

import receiver  # noqa: E402


class FakeSink(object):
    def __init__(self):
        self.items = []

    def put(self, source, payload):
        self.items.append((source, payload))


def receiver_client(env=None, env_file=NO_FILE, **kwargs):
    set_env(env)
    with CaptureStderr() as cap:
        rx = receiver.MqttReceiver(FakeSink(), env_file=env_file, **kwargs).start()
    return rx, FakeClient.instances[-1], cap


print("\n【七、receiver：未設定帳密與 TLS 時維持原本行為】")
rx, client, cap = receiver_client({})
check("預設 localhost:1883", client.connect_args[:2], ("localhost", 1883))
check("不呼叫 username_pw_set、tls_set", (client.credentials, client.tls), (None, None))
check("提示寫明不加密", "不加密" in cap.text(), True)
rx.stop()
rx, client, _ = receiver_client({"JETBOT_MQTT_HOST": "10.0.0.9", "JETBOT_MQTT_PORT": "1999"})
check("環境變數的位址、埠號照舊有效", client.connect_args[:2], ("10.0.0.9", 1999))
rx.stop()
rx, client, cap = receiver_client({"JETBOT_MQTT_PORT": "abc"})
check("埠號不是數字：提示並改用 1883", (client.connect_args[1], "不是數字" in cap.text()), (1883, True))
rx.stop()

print("\n【八、receiver：帳密與 TLS】")
rx, client, cap = receiver_client(env)
check("帳密帶入 username_pw_set", client.credentials, ("jetbot", PASSWORD))
check("啟用 TLS，用系統內建 CA", client.tls, {"ca_certs": None})
check("TLS 預設埠號 8883", client.connect_args[:2], ("abc.s1.eu.hivemq.cloud", 8883))
check("密碼不出現在提示", PASSWORD in cap.text(), False)
with CaptureStderr() as cap:
    client.fire_connect(0)
check("連上後訂閱 jetbot/status", client.subscribed, [("jetbot/status", 0)])
check("連上的提示寫明 TLS", "TLS" in cap.text(), True)
with CaptureStderr():
    client.fire_disconnect(1)
    client.fire_connect(0)
check("重連後重新訂閱", len(client.subscribed), 2)
check("連上、斷線次數有記錄", (rx.connects, rx.disconnects), (2, 1))
with CaptureStderr() as cap:
    client.fire_connect(5)
check("未授權（rc=5）提醒檢查帳密", "帳號密碼" in cap.text(), True)
rx.stop()

print("\n【九、receiver：優先順序 參數 > 環境變數 > .env > 預設值】")
dot_env = write_conf("project.env", "\n".join([
    "LINE_CHANNEL_ACCESS_TOKEN=不相關的設定", "JETBOT_MQTT_HOST=cloud.example.com",
    "JETBOT_MQTT_USERNAME=laptop", "JETBOT_MQTT_PASSWORD=\"{}\"".format(PASSWORD),
    "JETBOT_MQTT_TLS=yes", "JETBOT_MQTT_CA_CERTS=/tmp/ca.crt", ""]))
rx, client, _ = receiver_client({}, env_file=dot_env)
check("只有 .env：全部讀檔（引號去掉）", (client.connect_args[:2], client.credentials, client.tls),
      (("cloud.example.com", 8883), ("laptop", PASSWORD), {"ca_certs": "/tmp/ca.crt"}))
check(".env 不寫入 os.environ", os.environ.get("JETBOT_MQTT_HOST"), None)
rx.stop()
rx, client, _ = receiver_client({"JETBOT_MQTT_HOST": "env.example.com", "JETBOT_MQTT_TLS": "0"},
                                env_file=dot_env)
check("環境變數優先於 .env", (client.connect_args[:2], client.tls), (("env.example.com", 1883), None))
rx.stop()
rx, client, _ = receiver_client({"JETBOT_MQTT_HOST": "env.example.com"}, env_file=dot_env,
                                host="arg.example.com", port=18883, tls=True, username="arg")
check("參數優先於環境變數", (client.connect_args[:2], client.credentials[0]),
      (("arg.example.com", 18883), "arg"))
rx.stop()

print("\n【十、receiver：TLS 設定失敗】")
FakeClient.tls_raises = IOError("找不到 CA 檔")
rx, client, cap = receiver_client(env)
FakeClient.tls_raises = None
check("TLS 設定失敗時不連線（不退回明文）", (client.connect_args, client.loop_started), (None, False))
check("TLS 設定失敗會提示", "TLS 設定失敗" in cap.text(), True)
rx.stop()

print("\n【十一、receiver 命令列】")
set_env({})
ns = []
orig = receiver.MqttReceiver


class Spy(orig):
    def __init__(self, *args, **kwargs):
        ns.append(kwargs)
        orig.__init__(self, *args, **kwargs)

    def run(self, stats_every=None):
        pass


receiver.MqttReceiver = Spy
with CaptureStderr():
    receiver.main(["--tls", "--quiet"])
    receiver.main(["--quiet"])
receiver.MqttReceiver = orig
check("--tls 帶入 tls=True；沒給時為 None（交給設定決定）", (ns[0]["tls"], ns[1]["tls"]), (True, None))

set_env({})
print("\n" + "=" * 46)
print("通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
if failed:
    print("失敗項目：" + "、".join(failed))
sys.exit(1 if failed else 0)
