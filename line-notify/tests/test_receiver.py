"""receiver.py 的行為測試。不需要 broker、硬體，也不需要安裝 paho-mqtt。

    uv run python tests/test_receiver.py

即時接收的部分用假的 paho.mqtt.client（做法同 tests/test_sender.py），
由測試觸發「連上／斷線／收到訊息」。重播、錄製直接讀寫暫存檔。
"""

import io
import json
import os
import sys
import tempfile
import time
import types

TRANSPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "transport")
STATE_MACHINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state_machine")
sys.path.insert(0, TRANSPORT_DIR)

passed = []
failed = []


def check(name, actual, expected):
    if actual == expected:
        passed.append(name)
        print("  ✅ {}".format(name))
    else:
        failed.append(name)
        print("  ❌ {}\n       預期 {!r}，實際 {!r}".format(name, expected, actual))


class CaptureStderr(object):
    def __enter__(self):
        self.old = sys.stderr
        self.buf = io.StringIO()
        sys.stderr = self.buf
        return self

    def __exit__(self, *exc):
        sys.stderr = self.old

    def lines(self):
        return [l for l in self.buf.getvalue().splitlines() if l.strip()]


class FakeSink(object):
    def __init__(self):
        self.items = []

    def put(self, source, payload):
        self.items.append((source, payload))


class BrokenSink(object):
    def put(self, source, payload):
        raise RuntimeError("sink 壞了")


def sample(**over):
    p = {"state": "locked", "events": [], "has_target": True,
         "readings": {"blocked_prob": 0.083, "distance_m": 0.912,
                      "speed_scale": 1.0, "target_h": 0.421},
         "ts": 1000.0}
    p.update(over)
    return p


def body(payload):
    return json.dumps(payload).encode("utf-8")


print("\n【零、重播不需要 paho-mqtt】")
sys.modules["paho"] = None               # 讓 import paho 失敗
sys.modules.pop("receiver", None)
import receiver  # noqa: E402

# 不讀開發者本機的 .env 與環境變數：裡面若有雲端 broker 的設定（TLS、帳密），會蓋過測試的預設值
receiver.ENV_FILE = os.path.join(tempfile.mkdtemp(), "不存在.env")
for _key in ("JETBOT_MQTT_HOST", "JETBOT_MQTT_PORT", "JETBOT_MQTT_USERNAME", "JETBOT_MQTT_PASSWORD",
             "JETBOT_MQTT_TLS", "JETBOT_MQTT_CA_CERTS"):
    os.environ.pop(_key, None)
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "p.jsonl")
with io.open(path, "w", encoding="utf-8") as f:
    f.write(json.dumps(sample()) + "\n")
sink = FakeSink()
with CaptureStderr():
    receiver.replay(path, sink)
check("沒有 paho 也能 import receiver 並重播", len(sink.items), 1)
del sys.modules["paho"]


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
        self.loop_started = False
        self.stopped = False
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

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))

    def fire_connect(self, rc=0):
        self.on_connect(self, None, {}, rc)

    def fire_disconnect(self, rc=1):
        self.on_disconnect(self, None, rc)

    def fire_message(self, payload):
        self.on_message(self, None, FakeMsg(payload))


fake_client_mod = types.ModuleType("paho.mqtt.client")
fake_client_mod.Client = FakeClient
fake_paho = types.ModuleType("paho")
fake_mqtt = types.ModuleType("paho.mqtt")
fake_paho.mqtt = fake_mqtt
fake_mqtt.client = fake_client_mod
sys.modules["paho"] = fake_paho
sys.modules["paho.mqtt"] = fake_mqtt
sys.modules["paho.mqtt.client"] = fake_client_mod


print("\n【一、交給 sink】")
clock = [1000.25]
sink = FakeSink()
rx = receiver.MqttReceiver(sink, host="h", clock=lambda: clock[0])
got = rx.handle_bytes(body(sample()))
check("來源名稱固定為 jetbot", sink.items[0][0], "jetbot")
check("payload 以 dict 原樣交給 sink", sink.items[0][1], sample())
check("回傳交給 sink 的 dict", got, sample())
check("接受 str", rx.handle_bytes(json.dumps(sample())) is not None, True)
with CaptureStderr():
    check("中文不亂碼", rx.handle_bytes('{"state": "測試"}'.encode("utf-8"))["state"], "測試")

print("\n【二、格式錯誤：丟棄，同一種只提示一次】")
sink = FakeSink()
rx = receiver.MqttReceiver(sink, host="h")
with CaptureStderr() as cap:
    r1 = rx.handle_bytes(b"not json")
    rx.handle_bytes(b"{broken")
    rx.handle_bytes(b"\xff\xfe")          # 不是 UTF-8
check("不是 JSON：丟棄（回傳 None、sink 沒收到）", (r1, sink.items), (None, []))
check("不是 JSON：三次只提示一次", len(cap.lines()), 1)
with CaptureStderr() as cap:
    rx.handle_bytes(b"[1, 2]")
    rx.handle_bytes(b"42")
    rx.handle_bytes(b"null")
check("JSON 但不是物件：丟棄", sink.items, [])
check("JSON 但不是物件：只提示一次", len(cap.lines()), 1)
check("丟棄筆數有記錄", rx.dropped, 6)

print("\n【三、不符規格：照常交給 sink，只提示一次】")
sink = FakeSink()
rx = receiver.MqttReceiver(sink, host="h")
with CaptureStderr() as cap:
    rx.handle_bytes(body(sample(state="lost")))
    rx.handle_bytes(body(sample(state="lost")))
    rx.handle_bytes(body(sample(events=["fall_detected"])))
    rx.handle_bytes(body(sample(events=["fall_detected", "lock_lost"])))
    no_readings = sample()
    del no_readings["readings"]
    rx.handle_bytes(body(no_readings))
    rx.handle_bytes(body(no_readings))
    no_ts = sample()
    del no_ts["ts"]
    rx.handle_bytes(body(no_ts))
    rx.handle_bytes(body(sample(events="lock_lost")))
    rx.handle_bytes(body({"hello": 1}))
check("全部照常交給 sink", len(sink.items), 9)
text = "\n".join(cap.lines())
check("未知 state 只提示一次", text.count("'lost'"), 1)
check("未知事件只提示一次", text.count("fall_detected"), 1)
check("缺少 readings 只提示一次", text.count("缺少欄位 readings"), 1)
check("缺少 ts 有提示", "缺少欄位 ts" in text, True)
check("events 不是陣列有提示", "events 應為陣列" in text, True)
check("合規格的資料不提示", receiver.check_payload(sample()), [])
check("distance_m 為 null 合規格", receiver.check_payload(
    sample(readings={"blocked_prob": 0.1, "distance_m": None, "speed_scale": 1.0,
                     "target_h": None})), [])

print("\n【四、sink 出錯不當掉】")
rx = receiver.MqttReceiver(BrokenSink(), host="h")
with CaptureStderr() as cap:
    rx.handle_bytes(body(sample()))
    rx.handle_bytes(body(sample()))
check("sink.put 拋例外：不往外拋，只提示一次", len(cap.lines()), 1)
try:
    receiver.MqttReceiver(object())
    check("sink 沒有 put() 時建立就報錯", False, True)
except TypeError:
    check("sink 沒有 put() 時建立就報錯", True, True)

print("\n【五、延遲量測】")
clock = [0.0]
rx = receiver.MqttReceiver(FakeSink(), host="h", clock=lambda: clock[0])
for ts, recv in ((100.0, 100.010), (101.0, 101.030), (102.0, 102.020), (103.0, 103.200)):
    clock[0] = recv
    rx.handle_bytes(body(sample(ts=ts)))
clock[0] = 5.0
with CaptureStderr():
    rx.handle_bytes(body(no_ts))          # 沒有 ts 的不列入
s = rx.latency.summary()
check("筆數（沒有 ts 的不算）", s["count"], 4)
check("中位數（偶數筆取中間兩個平均）", round(s["median"], 6), 0.025)
check("最大值", round(s["max"], 6), 0.2)
check("文字摘要含筆數、中位數、最大值",
      rx.latency.format_summary(),
      "延遲統計（收到時間 − ts）：4 筆，中位數 25.0 ms，最大 200.0 ms，最小 10.0 ms")
check("沒有資料時不當掉", receiver.LatencyStats().format_summary(), "延遲統計：沒有資料")

print("\n【六、即時 MQTT：broker 未啟動、斷線、重連】")
for key in ("JETBOT_MQTT_HOST", "JETBOT_MQTT_PORT"):
    os.environ.pop(key, None)
sink = FakeSink()
FakeClient.instances = []
rx = receiver.MqttReceiver(sink)
with CaptureStderr():
    t0 = time.perf_counter()
    ret = rx.start()
    elapsed = time.perf_counter() - t0
client = FakeClient.instances[-1]
check("start() 立刻返回並回傳自己（Jupyter 可 rx = X().start()）", (ret is rx, elapsed < 0.5), (True, True))
check("預設 localhost:1883", client.connect_args[:2], ("localhost", 1883))
check("用 connect_async＋loop_start（連不上也在背景重試，不當掉）",
      client.loop_started, True)
check("設定重連間隔 1～5 秒（同 sender.py）", client.reconnect_delay, (1, 5))
check("還沒連上時為未連線", rx.connected, False)
with CaptureStderr():
    client.fire_connect(0)
check("連上後訂閱 jetbot/status（QoS 0）", client.subscribed, [("jetbot/status", 0)])
client.fire_message(body(sample()))
check("收到訊息交給 sink", len(sink.items), 1)
with CaptureStderr() as cap:
    client.fire_disconnect(1)
    client.fire_disconnect(1)
check("斷線提示一次、不當掉", (len(cap.lines()), rx.connected), (1, False))
with CaptureStderr():
    client.fire_connect(0)
check("重連後重新訂閱", client.subscribed, [("jetbot/status", 0)] * 2)
with CaptureStderr() as cap:
    client.fire_disconnect(1)
check("再次斷線會再提示", len(cap.lines()), 1)
with CaptureStderr():
    client.fire_connect(0)
    client.fire_message(b"garbage")
    client.fire_message(None)             # 連 bytes 都不是
client.fire_message(body(sample()))
check("收到錯誤格式後照常接收", len(sink.items), 2)
with CaptureStderr():
    client.fire_connect(5)
check("broker 拒絕連線不當掉", rx.connected, False)
rx.stop()
check("stop() 停掉背景執行緒", (client.stopped, rx.is_running()), (True, False))
os.environ["JETBOT_MQTT_HOST"] = "10.1.2.3"
os.environ["JETBOT_MQTT_PORT"] = "1999"
rx2 = receiver.MqttReceiver(FakeSink())
check("讀環境變數 JETBOT_MQTT_HOST／PORT", (rx2.host, rx2.port), ("10.1.2.3", 1999))
check("參數優先於環境變數", receiver.MqttReceiver(FakeSink(), host="x", port=1).host, "x")
os.environ.pop("JETBOT_MQTT_HOST")
os.environ.pop("JETBOT_MQTT_PORT")

print("\n【七、錄製 → 重播】")
rec = os.path.join(tmp, "rec.jsonl")
sink = FakeSink()
FakeClient.instances = []
with CaptureStderr():
    rx = receiver.MqttReceiver(sink, record_path=rec).start()
    client = FakeClient.instances[-1]
    client.fire_connect(0)
    sent = [sample(ts=2000.0 + i * 0.5, events=(["lock_lost"] if i == 2 else []))
            for i in range(5)]
    for p in sent:
        client.fire_message(body(p))
    client.fire_message(b"not json")      # 丟棄的不錄
    rx.stop()
lines = [json.loads(l) for l in io.open(rec, encoding="utf-8")]
check("錄製檔每行一筆、與收到的相同（丟棄的不錄）", lines, sent)

sink = FakeSink()
with CaptureStderr():
    r = receiver.replay(rec, sink)
check("錄製檔可以重播，內容一致", [p for _, p in sink.items], sent)
check("重播的來源名稱也是 jetbot", set(s for s, _ in sink.items), {"jetbot"})

print("\n【八、重播的時間】")
sink = FakeSink()
t0 = time.monotonic()
with CaptureStderr():
    receiver.replay(rec, sink, speed=None)
check("盡快全部送出（< 0.2 秒）", time.monotonic() - t0 < 0.2, True)

stamps = []


class TimingSink(object):
    def put(self, source, payload):
        stamps.append(time.monotonic())


t0 = time.monotonic()
with CaptureStderr():
    receiver.replay(rec, TimingSink(), speed=4.0)
span = stamps[-1] - stamps[0]
check("4 倍速照原本間隔重播（2 秒資料約 0.5 秒，實測 {:.3f}）".format(span),
      0.45 <= span <= 0.7, True)

sink = FakeSink()
before = time.time()
with CaptureStderr():
    receiver.replay(rec, sink, retime=True)
ts = [p["ts"] for _, p in sink.items]
check("retime：第一筆 ts 平移到現在", abs(ts[0] - before) < 0.5, True)
check("retime：間隔不變", [round(b - a, 3) for a, b in zip(ts, ts[1:])], [0.5] * 4)
check("retime 不改檔案內容", json.loads(io.open(rec, encoding="utf-8").readline())["ts"], 2000.0)

bad = os.path.join(tmp, "bad.jsonl")
with io.open(bad, "w", encoding="utf-8") as f:
    f.write(json.dumps(sample()) + "\n\n不是 JSON\n[1]\n" + json.dumps(sample(state="x")) + "\n")
sink = FakeSink()
with CaptureStderr() as cap:
    r = receiver.replay(bad, sink)
check("重播時略過空行、壞行，其餘照送", (len(sink.items), r.dropped), (2, 2))

print("\n【九、背景重播 start()／stop()】")
long_file = os.path.join(tmp, "long.jsonl")
with io.open(long_file, "w", encoding="utf-8") as f:
    for i in range(20):
        f.write(json.dumps(sample(ts=3000.0 + i)) + "\n")
sink = FakeSink()
t0 = time.monotonic()
rp = receiver.Replayer(sink, long_file, speed=1.0).start()
check("start() 立刻返回（不卡住儲存格）", time.monotonic() - t0 < 0.1, True)
time.sleep(0.3)
check("背景執行中", rp.is_running(), True)
t0 = time.monotonic()
rp.stop()
check("stop() 能中斷等待中的重播（< 0.5 秒）", time.monotonic() - t0 < 0.5, True)
check("中斷時只送出前面幾筆", 1 <= len(sink.items) < 20, True)

print("\n【十、產生器寫的檔可以直接重播】")
import simulator  # noqa: E402
gen = os.path.join(tmp, "gen.jsonl")
records = simulator.generate("detour_stuck", start_ts=simulator.DEFAULT_START_TS)
simulator.write_jsonl(records, gen)
sink = FakeSink()
with CaptureStderr() as cap:
    receiver.replay(gen, sink)
check("筆數一致", len(sink.items), len(records))
check("內容一致", [p for _, p in sink.items], [r["payload"] for r in records])
check("產生器的資料完全合規格（沒有任何提示）", cap.lines(), [])

print("\n【十一、與 state_machine 的 Cache 相容】")
sys.path.insert(0, STATE_MACHINE_DIR)
try:
    import machine
except Exception as e:                    # machine.py 需要的套件不在時不算失敗
    print("  （略過：無法載入 machine.py：{}）".format(e))
else:
    cache = machine.Cache()
    with CaptureStderr():
        receiver.replay(gen, cache)
    snap = cache.snapshot()
    check("Cache 收到最新一筆（覆蓋快取）", snap["jetbot"], records[-1]["payload"])

print("\n通過 {} 項，失敗 {} 項".format(len(passed), len(failed)))
sys.exit(1 if failed else 0)
