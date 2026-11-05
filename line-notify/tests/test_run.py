"""run.py（車外主程式）的測試。不需要 broker、雲端、網路、LLM 模型，也不會呼叫 LINE API。

    uv run python tests/test_run.py

做法：
- 假的 paho.mqtt.client 放進 sys.modules，由測試模擬「連上／收到資料／斷線」
- 訊息產生器用模板後端；LINE 換成假的 line_sender，記錄有沒有被呼叫
- 結束訊號（Ctrl+C、kill）用子程序實際送訊號，重播模式不需要 paho
"""

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

passed = []
failed = []


def check(name, actual, expected):
    if actual == expected:
        passed.append(name)
        print("  ✅ {}".format(name))
    else:
        failed.append(name)
        print("  ❌ {}\n       預期 {!r}，實際 {!r}".format(name, expected, actual))


def wait_until(cond, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ── 假的 paho-mqtt ────────────────────────────────────────────

class FakeMsg(object):
    def __init__(self, payload):
        self.payload = payload
        self.topic = "jetbot/status"


class FakeClient(object):
    instances = []

    def __init__(self, client_id="", clean_session=True):
        self.subscribed = []
        self.connect_args = None
        self.credentials = None
        self.tls = None
        FakeClient.instances.append(self)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        pass

    def username_pw_set(self, username, password=None):
        self.credentials = (username, password)

    def tls_set(self, ca_certs=None, **kwargs):
        self.tls = {"ca_certs": ca_certs}

    def connect_async(self, host, port=1883, keepalive=60):
        self.connect_args = (host, port, keepalive)

    def loop_start(self):
        pass

    def loop_stop(self, force=False):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def fire_connect(self, rc=0):
        self.on_connect(self, None, {}, rc)

    def fire_disconnect(self, rc=1):
        self.on_disconnect(self, None, rc)

    def fire_message(self, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.on_message(self, None, FakeMsg(body))


fake_client_mod = types.ModuleType("paho.mqtt.client")
fake_client_mod.Client = FakeClient
fake_paho = types.ModuleType("paho")
fake_mqtt = types.ModuleType("paho.mqtt")
fake_paho.mqtt = fake_mqtt
fake_mqtt.client = fake_client_mod
sys.modules["paho"] = fake_paho
sys.modules["paho.mqtt"] = fake_mqtt
sys.modules["paho.mqtt.client"] = fake_client_mod

for key in ("JETBOT_MQTT_HOST", "JETBOT_MQTT_PORT", "JETBOT_MQTT_USERNAME", "JETBOT_MQTT_PASSWORD",
            "JETBOT_MQTT_TLS", "JETBOT_MQTT_CA_CERTS", "JETBOT_SEND_LINE"):
    os.environ.pop(key, None)

import run  # noqa: E402
import formatter as formatter_mod  # noqa: E402
import receiver as receiver_mod  # noqa: E402
from formatter import LLMFormatter  # noqa: E402

tmpdir = tempfile.mkdtemp()
NO_FILE = os.path.join(tmpdir, "不存在.env")

# 不讀開發者本機的 .env：裡面若有雲端 broker 的設定（TLS、帳密），
# load_env() 會把它們寫進環境變數，優先順序高於測試指定的設定（2026-10-11 接上雲端 broker 後發現）
formatter_mod.load_env = lambda: None
receiver_mod.ENV_FILE = NO_FILE


def payload(state="locked", events=None, has_target=True, **over):
    p = {"state": state, "events": events or [], "has_target": has_target,
         "readings": {"blocked_prob": 0.05, "distance_m": 1.2, "speed_scale": 1.0, "target_h": 0.4},
         "ts": time.time()}
    p.update(over)
    return p


def read_log(path):
    with io.open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class FakeLine(object):
    """假的 LINE：記錄收到的文字，回傳指定的結果。"""

    def __init__(self, result=(200, "{}"), raises=None):
        self.texts = []
        self.result = result
        self.raises = raises

    def __call__(self, text):
        self.texts.append(text)
        if self.raises is not None:
            raise self.raises
        return self.result


def make_runner(**kwargs):
    options = dict(formatter=LLMFormatter(backend="template", verbose=False),
                   mqtt={"host": "broker.test", "env_file": NO_FILE},
                   offline_sec=0.5, status_every=0, verbose=False)
    options.update(kwargs)
    return run.Runner(**options)


print("\n【一、MQTT → 狀態機 → 訊息產生器 → 輸出端，一路走通】")
log_path = os.path.join(tmpdir, "notices.jsonl")
line = FakeLine()
runner = make_runner(log_path=log_path, line_sender=line).start()
client = FakeClient.instances[-1]
check("接收端依設定連線", client.connect_args[:2], ("broker.test", 1883))
client.fire_connect(0)
check("連上後訂閱 jetbot/status", client.subscribed, ["jetbot/status"])

client.fire_message(payload())
client.fire_message(payload(state="waiting", events=["lock_lost", "notify_no_target"], has_target=False))
check("收到車上的 notify_no_target 後產生一則通知", wait_until(lambda: runner.recorder.count == 1), True)
records = read_log(log_path)
rec = records[0]
check("紀錄檔：種類 no_target/alert", (rec["type"], rec["category"], rec["kind"]),
      ("notice", "no_target", "alert"))
check("紀錄檔：訊息內容是模板文字", rec["text"] in [run.machine.MESSAGE_HEADER.format(clock=run.machine.format_clock(rec["time"])) + "\n" + v
                                         for v in run.machine.TEMPLATES[("no_target", "alert")]], True)
check("紀錄檔：有時間（數字與文字）", (isinstance(rec["time"], float), len(rec["time_str"])), (True, 19))
check("紀錄檔：訊息產生器為 template", rec["generator"], "template")
check("預設 dry-run：紀錄為 dry-run", (rec["dry_run"], rec["line"]), (True, "dry-run"))
check("預設 dry-run：沒有呼叫 LINE", line.texts, [])
check("紀錄檔：通知當下 broker 為已連線", rec["broker_connected"], True)
check("延遲（ts → 輸出完成）在 0～1 秒", 0 <= rec["latency_sec"] < 1.0, True)

print("\n【二、broker 斷線：狀態機照常判定失聯並通知，恢復後自動復原】")
client.fire_disconnect(1)
check("斷線超過失聯門檻後通知 offline/alert",
      wait_until(lambda: runner.recorder.count == 2 and read_log(log_path)[-1]["category"] == "offline"), True)
rec = read_log(log_path)[-1]
check("失聯通知的 kind 為 alert", rec["kind"], "alert")
check("紀錄檔記下當時 broker 未連線（分辨是筆電斷網）", rec["broker_connected"], False)
client.fire_connect(0)
check("重連後重新訂閱", len(client.subscribed), 2)
client.fire_message(payload(events=["lock_acquired"]))
check("恢復收到資料後通知解除", wait_until(lambda: runner.recorder.count == 3), True)
rec = read_log(log_path)[-1]
check("解除通知：offline/recovery，並併入 no_target 解除",
      (rec["category"], rec["kind"], [(o["category"], o["kind"]) for o in rec["others"]]),
      ("offline", "recovery", [("no_target", "recovery")]))
check("連上、斷線次數", (runner.receiver.connects, runner.receiver.disconnects), (2, 1))

print("\n【三、記憶體不持續成長】")
runner.sm.notices.extend([{"dummy": i} for i in range(run.NOTICES_KEEP + 300)])
runner.poll()
check("poll() 把狀態機的通知紀錄修剪到上限", len(runner.sm.notices), run.NOTICES_KEEP)
check("延遲統計有上限", runner.recorder.latency._maxlen, run.LATENCY_KEEP)

print("\n【四、結束與統計】")
runner.stop()
stats = runner.stats()
check("統計：收到 3 筆", stats["received"], 3)
check("統計：通知 3 則", stats["notices"], 3)
check("統計：訊息產生器用量", stats["generators"], {"template": 3})
check("統計：有延遲資料", (stats["receive_latency"]["count"], stats["notice_latency"]["count"]), (3, 2))
records = read_log(log_path)
check("紀錄檔最後一行是統計（type=summary）", (records[-1]["type"], records[-1]["notices"]), ("summary", 3))
check("stop() 可重複呼叫", runner.stop(), None)
text = run.format_stats(stats)
check("統計文字含收到筆數、通知則數、延遲", all(s in text for s in ("收到資料：3 筆", "通知：3 則", "中位數")), True)
check("統計文字註明 dry-run", "dry-run" in text, True)

print("\n【五、明確指定才實際送出 LINE】")
line = FakeLine()
runner = make_runner(send_line=True, line_sender=line).start()
client = FakeClient.instances[-1]
client.fire_connect(0)
client.fire_message(payload(state="impassable_wait", events=["enter_impassable", "notify_impassable"],
                            has_target=False))
check("send_line=True 時呼叫 LINE 一次", wait_until(lambda: len(line.texts) == 1), True)
check("送出的文字是通知內容", any(w in line.texts[0] for w in ("無法通過", "繞不過")), True)
runner.stop()
check("LINE 成功則數", (runner.recorder.line_ok, runner.recorder.line_failed), (1, 0))

line = FakeLine(raises=RuntimeError("找不到 LINE_CHANNEL_ACCESS_TOKEN"))
runner = make_runner(send_line=True, line_sender=line).start()
client = FakeClient.instances[-1]
client.fire_connect(0)
client.fire_message(payload(state="waiting", events=["notify_no_target"], has_target=False))
check("LINE 失敗不影響整條線", wait_until(lambda: runner.recorder.count == 1), True)
client.fire_message(payload())
time.sleep(0.2)
check("LINE 失敗後狀態機仍在運作", runner.sm.running, True)
runner.stop()
check("LINE 失敗有計數（通知與隨後的解除各一則）", runner.recorder.line_failed, 2)

line = FakeLine(result=(429, '{"message":"You have reached your monthly limit."}'))
runner = make_runner(send_line=True, line_sender=line).start()
client = FakeClient.instances[-1]
client.fire_connect(0)
client.fire_message(payload(state="waiting", events=["notify_no_target"], has_target=False))
wait_until(lambda: runner.recorder.count == 1)
runner.stop()
check("LINE 回應非 200 算失敗", (runner.recorder.line_ok, runner.recorder.line_failed), (0, 1))

print("\n【六、帳密與 TLS 設定傳到接收端】")
runner = make_runner(mqtt={"host": "cloud.test", "tls": True, "username": "u", "password": "p",
                           "env_file": NO_FILE}).start()
client = FakeClient.instances[-1]
check("TLS、帳密、8883", (client.tls, client.credentials, client.connect_args[1]),
      ({"ca_certs": None}, ("u", "p"), 8883))
runner.stop()

print("\n【六之二、一直連不上時提示一次】")
runner = make_runner(verbose=True).start()
runner.started_at -= run.CONNECT_HINT_SEC + 1
with io.StringIO() as buf:
    old, sys.stderr = sys.stderr, buf
    try:
        runner.poll()
        runner.poll()
    finally:
        sys.stderr = old
    hint = buf.getvalue()
check("超過時間沒連上：提示檢查 TLS 等設定，只提示一次", (hint.count("沒連上 broker"), "JETBOT_MQTT_TLS" in hint), (1, True))
runner.stop()
runner = make_runner(verbose=True).start()
FakeClient.instances[-1].fire_connect(0)
runner.started_at -= run.CONNECT_HINT_SEC + 1
with io.StringIO() as buf:
    old, sys.stderr = sys.stderr, buf
    try:
        runner.poll()
    finally:
        sys.stderr = old
    hint = buf.getvalue()
check("已連上過就不提示", "沒連上 broker" in hint, False)
runner.stop()

print("\n【七、命令列：預設 dry-run，沒有 token 時拒絕實際送出】")
saved = (run.load_env, os.environ.pop("LINE_CHANNEL_ACCESS_TOKEN", None))
run.load_env = lambda: None
with io.StringIO() as buf:
    old, sys.stderr = sys.stderr, buf
    try:
        code = run.main(["--send-line", "--no-log", "--llm", "template"])
    finally:
        sys.stderr = old
check("--send-line 但沒有 token：結束碼 2，不啟動", code, 2)
run.load_env = saved[0]
if saved[1] is not None:
    os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = saved[1]
check("JETBOT_SEND_LINE 只認 1/true/yes/on", [run._is_true(v) for v in ("1", "true", "YES", "0", "", None)],
      [True, True, True, False, False, False])

print("\n【八、Ctrl+C 與 kill 都會收尾並印出統計（子程序實測）】")
replay_path = os.path.join(tmpdir, "replay.jsonl")
with io.open(replay_path, "w", encoding="utf-8") as f:
    t0 = 1790000000.0
    f.write(json.dumps(payload(ts=t0)) + "\n")
    f.write(json.dumps(payload(state="waiting", events=["notify_no_target"], has_target=False, ts=t0 + 0.5)) + "\n")
    f.write(json.dumps(payload(ts=t0 + 1.0)) + "\n")

env = dict(os.environ)
for key in ("JETBOT_SEND_LINE", "JETBOT_LLM_BACKEND"):
    env.pop(key, None)
for sig, name in ((signal.SIGTERM, "kill（SIGTERM）"), (signal.SIGINT, "Ctrl+C（SIGINT）")):
    log = os.path.join(tmpdir, "sig-{}.jsonl".format(int(sig)))
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "run.py"), "--replay", replay_path, "--llm", "template",
         "--log", log, "--status-every", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=ROOT)
    wait_until(lambda: os.path.exists(log) and os.path.getsize(log) > 0, timeout=10.0)
    time.sleep(1.5)       # 讓重播把最後一筆送完
    proc.send_signal(sig)
    try:
        _, err = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, err = proc.communicate()
    err = err.decode("utf-8", "replace")
    check("{}：結束碼 0".format(name), proc.returncode, 0)
    check("{}：印出統計".format(name), "統計" in err and "收到資料：3 筆" in err, True)
    records = read_log(log)
    check("{}：紀錄檔有通知與最後的統計".format(name),
          ([r["type"] for r in records][:1], records[-1]["type"]), (["notice"], "summary"))

print("\n" + "=" * 46)
print("通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
if failed:
    print("失敗項目：" + "、".join(failed))
sys.exit(1 if failed else 0)
