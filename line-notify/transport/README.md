# transport：車上 → 車外的 MQTT 傳輸

```
車上 follow_state → report_to_caregiver() → sender.send(payload)   ← transport/sender.py
                                                   │ MQTT publish（topic jetbot/status，QoS 0，不 retain）
                                                   ↓
                              車外筆電 Mosquitto（Docker，埠 1883）  ← transport/broker/
                                                   ↓
                              接收程式（任務二，transport/receiver.py）
```

| 檔案 | 用途 | 在哪裡執行 |
|---|---|---|
| `sender.py` | 車上呼叫 `sender.send(payload)`，把資料送到車外 | 車上 Jetson Nano（Python 3.6） |
| `broker/docker-compose.yml`、`broker/mosquitto.conf` | MQTT broker | 車外筆電（Docker） |

傳輸設定依 `docs/進度凍結-2026-09-16.md` 變更一，已凍結：topic `jetbot/status`、QoS 0、retain 關閉、JSON（UTF-8）。

---

## 一、在車外筆電啟動 broker

需要 Docker Desktop（已開啟且不是暫停狀態）。

```bash
cd transport/broker && docker compose up
```

看到 `mosquitto version 2.x running` 就是啟動成功。`Ctrl+C` 停止；要在背景執行改用 `docker compose up -d`，停止用 `docker compose down`。

> ⚠️ **僅限區網測試**：Mosquitto 2.x 預設只接受本機連線、拒絕匿名。`mosquitto.conf` 刻意開放所有網卡的 1883 並允許匿名，
> 熱點內任何裝置都能收發資料。**不要在公開網路使用這份設定。**

### 確認 broker 可用

另開一個終端機訂閱（Mac 不用安裝 mosquitto，直接用容器內附的工具）：

```bash
docker exec -it jetbot-mosquitto mosquitto_sub -t jetbot/status -v
```

再開一個終端機，從筆電本機送一筆：

```bash
uv run --with "paho-mqtt<2.0" python -c "import time, sender; time.sleep(1); sender.send({'state': 'locked', 'events': [], 'has_target': True, 'readings': {}}); time.sleep(0.5)"
```

（在 `transport/` 目錄下執行；`time.sleep(1)` 是等背景連線完成，車上不需要這樣做。）訂閱端會印出：

```
jetbot/status {"state": "locked", "events": [], "has_target": true, "readings": {}, "ts": 1790000000.123}
```

## 二、查筆電的 IP（車上要連這個位址）

筆電與車子都要連在**同一支手機的熱點**上。筆電查 IP：

```bash
ipconfig getifaddr en0
```

- 沒有輸出時，改試 `ipconfig getifaddr en1`，或到「系統設定 → Wi-Fi → 已連線網路的『詳細資訊』」查看
- iPhone 熱點通常是 `172.20.10.x`，Android 熱點常見 `192.168.43.x`，但**每次連線都可能不同**，每次上車前都要重查
- 若 macOS 防火牆有開，第一次啟動時要允許 Docker 接受連入連線

從車上確認連得到筆電（在 Jetson 終端機）：

```bash
nc -zv 172.20.10.2 1883
```

（把 IP 換成剛查到的；顯示 `succeeded` 代表通了。沒有 `nc` 可略過，直接看 `sender` 的提示。）

## 三、把 `sender.py` 放上車

### 1. 安裝 `paho-mqtt<2.0`（車上只要做一次）

**一定要 1.x 版**：2.x 不支援 Python 3.6，API 也不同。以下指令在 **Jetson 上**執行（車上是 JetPack 系統內建的 pip3，與 Mac 端「一律用 uv」的規則無關）：

```bash
pip3 install --user "paho-mqtt<2.0"
```

車子暫時連不上網路時，先在 Mac 下載原始碼套件再傳上車（1.6.1 只有原始碼套件，純 Python，不需編譯）：

```bash
curl -LO https://files.pythonhosted.org/packages/source/p/paho-mqtt/paho-mqtt-1.6.1.tar.gz
```

```bash
scp paho-mqtt-1.6.1.tar.gz jetbot@<車子IP>:~/
```

然後在 Jetson 上：

```bash
pip3 install --user ~/paho-mqtt-1.6.1.tar.gz
```

確認版本：

```bash
python3 -c "import paho.mqtt; print(paho.mqtt.__version__)"
```

> 如果車上的 Jupyter 是跑在 JetBot 的 Docker 容器裡，套件要裝在**容器內**（在 Jupyter 的 Terminal 執行上述指令即可）。

### 2. 複製 `sender.py`

`sender.py` 是單一檔案，除了 `paho-mqtt` 不依賴其他套件。放到**車上 notebook 所在的資料夾**（組員的程式在 `object_following/`），車上的 `import sender` 就會找到它：

```bash
scp transport/sender.py jetbot@<車子IP>:~/Notebooks/object_following/
```

（實際路徑以車上 notebook 位置為準。）

### 3. 設定 broker 位址（不寫死 IP）

依優先順序，任選一種：

| 方式 | 做法 | 適合 |
|---|---|---|
| 1. 程式內指定 | `import sender` 之後執行 `sender.configure(host="172.20.10.2")` | 臨時換 IP |
| 2. 環境變數 | notebook 第一格、`import sender` **之前**執行 `%env JETBOT_MQTT_HOST=172.20.10.2` | 不想碰檔案 |
| 3. 設定檔 | 在 `sender.py` 旁邊建立 `sender.env`（見下方） | 最省事，改一次檔案就好 |
| 4. 都沒設 | 退回 `localhost`，並提示一次 | 只適合在筆電本機測試 |

`sender.env` 內容（埠號沒寫就是 1883）：

```
# 筆電在熱點上的 IP，每次上車前確認
JETBOT_MQTT_HOST=172.20.10.2
JETBOT_MQTT_PORT=1883
```

### 4. 在車上看到的提示

`sender` 的提示都印到 stderr，前綴 `[sender]`，**同一種狀況只印一次**：

```
[sender] 嘗試連線 broker 172.20.10.2:1883（背景進行，不影響車上）
[sender] 已連上 broker 172.20.10.2:1883，topic=jetbot/status
[sender] 與 broker 斷線（rc=1），背景自動重連中
[sender] 尚未連上 broker 172.20.10.2:1883，資料先丟棄（連上後會再提示）
```

一直停在「尚未連上」：檢查筆電 IP 是否變了、broker 是否啟動、兩台是否在同一個熱點、熱點是否允許裝置互連。

---

## `sender.py` 的行為

| 情況 | 行為 |
|---|---|
| `import sender` 時 broker 還沒開 | 照常載入，連線在背景重試 |
| 沒安裝 `paho-mqtt` | `import sender` 拋出 `ImportError` 並說明安裝方式；車上的 `try: import sender` 會退回印字 |
| 沒連上 broker 時 `send()` | 立刻返回，這筆直接丟棄，不累積 |
| broker 斷線後恢復 | 背景每 1～5 秒重試一次，連上後自動恢復發送 |
| 熱點斷掉但 TCP 沒收到斷線通知 | keepalive 5 秒，約 10 秒內察覺並進入重連 |
| payload 沒有 `ts` | 補上發送當下的 `time.time()`（Unix 秒，float） |
| payload 已有 `ts` | 不覆蓋 |
| 呼叫端傳入的 dict | 不修改（先複製再加 `ts`） |
| payload 不是 dict、無法轉 JSON、發送錯誤 | 不拋例外，這筆略過，同一種錯誤只提示一次 |
| 節流 | 不做（車上的 `report_to_caregiver()` 已節流），收到就送 |

其他函式：`sender.is_connected()` 回傳目前是否連線（除錯用）；`sender.stop()` 停止背景連線（一般不需要）。

### 不阻塞的量測結果

2026-09-23 在 M1 MacBook Air（macOS、Python 3.13、paho-mqtt 1.6.1）量測，模擬車上每秒 10 次呼叫 `send()`：

| 情境 | 呼叫次數 | 中位數 | p99 | 最大 |
|---|---|---|---|---|
| broker 沒開（連線被拒） | 97 | 0.157 ms | 0.208 ms | 0.208 ms |
| 位址不存在（TCP 連線逾時，模擬 IP 設錯） | 145 | 0.152 ms | 0.245 ms | 0.271 ms |

`import sender` 本身約 45～60 ms（大部分是載入 paho），同樣不等網路。

### 斷線重連的實測

同一天以筆電區網 IP 連 Docker 裡的 Mosquitto 2.1.2，`send()` 每秒 10 次、共 35 秒，第 8 秒 `docker stop`、第 19 秒 `docker start`：

| 期間 | 送出 | 訂閱端收到 |
|---|---|---|
| 0～8 秒（已連線） | 76 | 76 |
| 8～20 秒（broker 關閉＋重連中） | 116 | 0（丟棄，符合設計） |
| 20～35 秒（broker 啟動約 1 秒後自動重連） | 144 | 144 |

- 已連線時 `send()` 中位數 0.18 ms、p99 3.2 ms、最大 6.8 ms。偶發的數毫秒來自 Python 執行緒切換（GIL 切換間隔 5 ms，paho 背景執行緒正在跑時要等它讓出），不是在等網路
- 本機送達延遲中位數 1 ms、最大 10 ms（不含熱點的無線延遲）

> Jetson Nano 的 CPU 比 M1 慢，實際數字會較大，但 `send()` 在任何情境下都不做網路等待，量級應維持在毫秒以下。**尚未在 Jetson 上實測。**

## 測試

不需要 broker、硬體，也不需要安裝 paho-mqtt（測試內建假的 paho）：

```bash
uv run python tests/test_sender.py
```

---
---

# 任務二：假資料產生器與接收程式

```
【假資料產生器】simulator.py ── sender.send() ──→ Mosquitto ──→ receiver.py ── sink.put("jetbot", payload)
                                                                               ↓
                                                              覆蓋快取 → 車外狀態機（任務三）
```

| 檔案 | 用途 | 在哪裡執行 |
|---|---|---|
| `simulator.py` | 照組員規格模擬車上的跟隨狀態機，產生與真車相同的回報 | 車外筆電 |
| `receiver.py` | 即時訂閱 MQTT 或重播 `.jsonl`，把每筆交給 sink；可錄製、量延遲 | 車外筆電 |

兩支都只在筆電執行，不需相容 Python 3.6；MQTT 與 `sender.py` 同樣用 `paho-mqtt<2.0`。

## 一、假資料產生器 `simulator.py`

### 怎麼模擬

1. **逐幀模擬 `follow_state`**：每秒 10 幀（同車上），輸入是「這一幀的世界」（有沒有人、框多高、偏多少、有沒有被擋），輸出 `state` 與 `events`。所有參數與規則照 `docs/規格回覆-2026-09-19.md`，程式裡逐條註明出處
2. **套上車上的節流**：`state` 改變或 `events` 非空立刻送，否則距上次送出滿 1.0 秒才送（與 `report_to_caregiver()` 相同）

時間用整數毫秒計算，所以「恰好 2 秒」不會因為浮點誤差差一幀。**不用亂數**，同一情境每次產生的序列完全相同。

### 內建情境

```bash
uv run python transport/simulator.py --list
```

| 情境 | 長度 | 驗證什麼 | 關鍵事件（秒） |
|---|---|---|---|
| `normal` | 60 s | 基準：鎖定後只有心跳 | 0 `enter_confirming`、0.8 `lock_acquired` |
| `brief_loss` | 18.5 s | 跟丟 1.5 秒（< 寬限 2 秒）後找回 | 7.0 `searching`，**沒有** `lock_lost` |
| `long_loss` | 161 s | 長時間跟丟 | 6.0 `searching`、8.0 `lock_lost`、9.0／69.0／129.0 `notify_no_target` |
| `detour_ok` | 15.8 s | 繞行成功 | 5.0 `enter_detour`、5.8 `detour_advance`、7.3 `detour_done` → `locked` |
| `detour_stuck` | 156.3 s | 連續繞行超過 3 次卡住 | 3 次繞行後 11.3 `enter_impassable`＋`notify_impassable`、71.3／131.3 重發、145.2 `resume_follow` |
| `blocked_long` | 22 s | 長時間被擋 | 9.0 `enter_impassable`（實際由「8 秒過半」觸發，見規格疑問 1） |
| `oscillate` | 26 s | 擺盪（擋 1 秒通 0.5 秒） | 11.0 `enter_impassable`（8 秒內被擋過半） |
| `switch_confirm` | 10.5 s | 倒數中換人 | 0.5 `["cancel_confirming", "enter_confirming"]`、1.3 `lock_acquired` |
| `distance_null` | 20 s | 測距讀不到 | 5～15 秒 `distance_m` 為 `null` |
| `offline` | 32 s | 車子失聯 | 10～22 秒停止發送（車外看到 13 秒空窗） |

### 新增情境

情境是一串「區段」，每段寫「這段時間世界長什麼樣子」，**沒寫的欄位沿用上一段**：

```python
"my_case": {
    "description": "人走遠又回來",
    "segments": [
        {"sec": 5, "person": True},                     # 人站在正前方
        {"sec": 2, "offset": 0.3},                      # 往右走
        {"sec": 10, "person": False},                   # 消失 10 秒
        {"sec": 5, "person": True, "offset": 0.0},      # 回來
    ],
},
```

| 欄位 | 意思 | 初始值 |
|---|---|---|
| `sec` | 這段幾秒（必填） | — |
| `person` | 畫面裡有沒有人 | `False` |
| `h` | 目標框高度；鎖定需要 ≥ 0.30 | 0.40 |
| `offset` | 水平偏移（−1～1）；鎖定需要 \|offset\| ≤ 0.15；跟丟前 ≥ 0.12 會發 `searching` | 0.0 |
| `identity` | 身分比對是否通過 | `True` |
| `who` | 人的代號；倒數中換代號＝換人，鎖定中換代號＝`relocked` | `"A"` |
| `blocked` | 是否被擋住 | `False` |
| `blocked_prob` | 避障機率；不寫時被擋 0.93、沒擋 0.05 | — |
| `distance` | 測距（公尺），`None` 送出 `null` | 1.2 |
| `speed_scale` | 前進速度倍率 | 1.0 |
| `offline` | 資料送不出去（車上照常運作） | `False` |
| `note` | 註解 | — |

加在 `simulator.py` 的 `SCENARIOS`，或寫成同結構的 `.json` 檔用 `--scenario-file my.json` 載入。欄位打錯字會直接報錯。

### 輸出方式

```bash
# 只印出（除錯）
uv run python transport/simulator.py long_loss --print

# 寫成 .jsonl（格式同組員的桌上錄製：payload＋ts；ts 預設固定起點，檔案可重現；--start-ts now 用現在時間）
uv run python transport/simulator.py long_loss --out long_loss.jsonl

# 經 sender.py 送到 MQTT，即時（ts 由 sender.py 補）
uv run --with "paho-mqtt<2.0" python transport/simulator.py long_loss --mqtt

# 10 倍速（ts 由產生器用模擬時間填入，「每 60 秒重發」在 ts 上仍是 60 秒）
uv run --with "paho-mqtt<2.0" python transport/simulator.py long_loss --mqtt --speed 10
```

broker 位址沿用 `sender.py` 的規則（環境變數 `JETBOT_MQTT_HOST`、`sender.env`，或 `--host`）。`--mqtt` 會先等 `sender` 連上（最多 5 秒）才開始送，避免前幾筆被丟棄。

Jupyter：

```python
import sys; sys.path.insert(0, "transport")
import simulator
records = simulator.generate("long_loss")       # [{"t": 秒, "payload": {...}, "delivered": True, "reason": None}, ...]
```

## 二、接收程式 `receiver.py`

### 與任務三的介面

```python
sink.put("jetbot", payload)    # payload 為 dict；來源名稱固定 "jetbot"
```

sink 是任何有 `put(source, payload)` 的物件，`state_machine/machine.py` 的 `Cache` 直接可用。接收程式**不做任何判斷**。

### 命令列

```bash
# 即時接收並印出每一筆（Ctrl+C 結束時印延遲統計）
uv run --with "paho-mqtt<2.0" python transport/receiver.py

# 同時錄製成 .jsonl（附加寫入）；每 10 秒印一次延遲統計
uv run --with "paho-mqtt<2.0" python transport/receiver.py --record rec.jsonl --stats-every 10

# 重播：不給 --speed 為盡快全部送出；--speed 1 照原本間隔；--speed 10 十倍速
uv run python transport/receiver.py --replay rec.jsonl --speed 10

# 重播時把 ts 整體平移到現在（間隔不變），接會檢查資料過期的狀態機時用
uv run python transport/receiver.py --replay rec.jsonl --speed 1 --retime
```

broker 位址：`--host` > 環境變數 `JETBOT_MQTT_HOST` > `localhost`（接收程式與 broker 同在筆電）。重播不需要 paho-mqtt。

### Jupyter（背景執行，不卡住儲存格）

```python
import sys; sys.path.insert(0, "transport"); sys.path.insert(0, "state_machine")
import receiver, machine

cache = machine.Cache()
rx = receiver.MqttReceiver(cache, record_path="rec.jsonl").start()
# ……其他儲存格照常執行，cache.snapshot()["jetbot"] 就是最新一筆……
rx.stop()
rx.latency.print_summary()

rp = receiver.Replayer(cache, "long_loss.jsonl", speed=10).start()   # 背景重播
rp.wait()        # 或 rp.stop() 中途停止
```

### 各種情況的行為

| 情況 | 行為 |
|---|---|
| 啟動時 broker 還沒開 | 照常啟動，背景每 1～5 秒重試 |
| 中途斷線 | 提示一次，背景自動重連，**連上後重新訂閱** |
| 不是 JSON、不是 UTF-8、JSON 但不是物件 | 丟棄，同一種錯誤只提示一次 |
| 欄位缺漏、`state` 或事件名稱不在規格內 | **照常交給 sink**，同一種只提示一次 |
| `sink.put()` 拋例外 | 不往外拋，提示一次，接收照常 |
| 錄製 | 每行一筆收到的 payload（含 `ts`），格式同組員桌上錄製；丟棄的資料不錄 |

### 延遲量測

每筆記錄「收到時間 − `ts`」，`rx.latency.summary()` 回傳筆數、中位數、最大、最小（秒）。

> ⚠️ **時鐘限制**：實車時 `ts` 是車上的時鐘、收到時間是筆電的時鐘，兩者不一定同步，數值會混入時鐘差（甚至為負）。實車數據要加註這個限制。
> 用產生器在同一台筆電、**即時模式（1 倍速）**測試沒有這個問題；加速模式的 `ts` 是模擬時間，延遲值沒有意義。

## 三、端到端實測（2026-09-23）

環境：M1 MacBook Air、macOS、Python 3.13、paho-mqtt 1.6.1、Docker 裡的 Mosquitto 2（`transport/broker/`），全部在同一台筆電。

操作步驟：

```bash
# 終端機 1：先開接收程式（這時 broker 還沒開）
uv run --with "paho-mqtt<2.0" python transport/receiver.py --record rec.jsonl --quiet

# 終端機 2：啟動 broker
cd transport/broker && docker compose up -d

# 終端機 3：產生器
uv run --with "paho-mqtt<2.0" python transport/simulator.py normal --mqtt
```

結果：

| 測試 | 結果 |
|---|---|
| broker 未啟動時開接收程式 | 照常執行；broker 啟動後數秒內（重試間隔最長 5 秒）自動連上並訂閱 |
| `normal` 即時 1 倍速（61 筆） | 全數收到；延遲中位數 **2.0 ms**、最大 **7.5 ms**、最小 1.2 ms |
| `detour_ok` 即時 1 倍速（18 筆） | 全數收到，內容（去掉 `ts`）與 `simulator.generate()` 完全相同；`ts` 間隔與模擬間隔最大差 8.4 ms |
| `long_loss` 20 倍速（163 筆，8.7 秒跑完 161 秒情境） | 全數收到、內容一致；收到的 `notify_no_target` 的 `ts` 間隔 **60.0、60.0 秒**，`lock_lost − searching` = **2.0 秒** |
| `long_loss` 10 倍速，中途 `docker stop` 4 秒再 `docker start` | 接收程式與 sender 都提示斷線一次、自動重連、重新訂閱；斷線期間的資料依設計丟棄（163 筆收到 93 筆），接收程式沒有當掉 |
| 用 `mosquitto_pub` 送 `not json`、`[1,2]`、`{"state":"lost","events":["x"]}` | 前兩筆丟棄、各提示一次；第三筆照常交給 sink 並提示缺欄位與未知名稱 |
| 重播錄製檔、重播產生器寫的 `.jsonl` | 都可用，筆數與內容一致 |

延遲是本機（不經過手機熱點）的數字；實車還要加上熱點無線延遲與上述時鐘限制。

## 四、模擬時自選的做法（規格沒寫清楚之處）

以下都選最簡單的做法，程式中以「【自選】」標註。**跟真車行為可能不同，等實車錄製後比對。**

1. **門檻比較一律用 `>=`**：「滿 0.75 秒」「超過 2 秒」「超過 3 秒」都當作經過時間 ≥ 參數值。以 10 fps 計，`lock_acquired` 在進入倒數後 0.8 秒（第一個 ≥ 0.75 的幀），`lock_lost` 在消失後恰好 2.0 秒
2. **`notify_no_target` 的 3 秒從畫面變空的那一幀算起**（規格：「畫面中連續無人超過 3 秒」），所以是 `lock_lost` 後 1 秒，不是 `lock_lost` 後 3 秒
3. **`notify_*` 冷卻各事件分開計算，狀況解除時不歸零**：解除後 60 秒內再發生，第一則要等冷卻結束
4. **被擋住的幀不處理偵測框**：`has_target` 為 `false`、`target_h` 為 `null`（依規格範例三）；也不中斷「畫面連續無人」的計時
5. **「8 秒內被擋比例」的分母固定為 8 秒**，不是已累積的幀數，否則剛開機時被擋一幀就是 100%
6. **連續繞行次數**：每次 `enter_detour` 加一，第 4 次被擋時改判 `impassable_wait`（不發 `enter_detour`）；**只在進入 `impassable_wait` 時歸零**（否則解除時條件 c 會立刻再成立）。規格沒說一般跟隨多久後歸零，目前不歸零
7. **`detour` 內被擋**：轉向階段繼續轉；直行階段是「強制」直行，不改階段、不發事件
8. **`detour`／`impassable_wait` 期間保留鎖定、不消耗跟丟寬限**；結束時若目標不在畫面，從那一幀開始算寬限
9. **從 `confirming` 被擋而進入 `detour`**：倒數作廢，不發 `cancel_confirming`
10. **`relocked`**：以「鎖定中人的代號改變」代表「框與上一幀對不上」
11. **失聯**：車上照常運作、節流計時照走，只是資料送不出去
12. **第一筆**：開機後第一幀一定送出（節流還沒有「上一筆」）

## 五、規格疑問（需與組員確認）

1. **條件 (a)「連續被擋 5 秒」在規格參數下不會先觸發**：連續被擋到 4.1 秒時，8 秒視窗內被擋時間已超過 4 秒（過半），條件 (b) 會先成立。除非車上的比例計算方式不同（例如分母、視窗起點、只在某些狀態記錄），否則 (a) 實際上是用不到的。測試中以 `params={"block_ratio_threshold": 1.0}` 關掉 (b) 單獨驗證 (a)
2. 連續繞行次數何時歸零（見自選 6）
3. `notify_*` 冷卻在狀況解除時是否歸零（見自選 3）
4. 被擋的幀 `has_target` 是否一定為 `false`（見自選 4）

## 測試

不需要 broker、硬體、paho-mqtt：

```bash
uv run python tests/test_simulator.py
```

```bash
uv run python tests/test_receiver.py
```

---
---

# 任務五：雲端 MQTT 與長期執行的主程式 `run.py`

```
車子（手機熱點上網）── sender.py ──► 雲端 MQTT broker（TLS＋帳密）◄── run.py（筆電，任何網路）
                                                                      │
                                         receiver → 覆蓋快取 → 車外狀態機 → 訊息產生器 → LINE（預設 dry-run）
                                                                                        └─► logs/notices-*.jsonl
```

車子和筆電不用連同一個網路，各自能上網就好。判斷、轉譯、通報這幾層**完全沒改**。

| 檔案 | 用途 |
|---|---|
| `sender.py`、`receiver.py` | 加上帳密、TLS 設定；沒設定時行為與之前相同 |
| `../run.py` | 單一進入點：接收 → 狀態機 → 訊息產生器 → LINE，可長時間執行 |
| `broker/secure/` | 本地「加密 broker」（TLS＋帳密、自簽憑證），不需要雲端帳號就能測加密連線 |
| `../.env.example` | 筆電端的設定範本 |
| `../tests/test_mqtt_auth.py`、`../tests/test_run.py` | 測試（不需要 broker、雲端、網路） |

## 一、連線設定

同一份程式，**只改設定**就能切換區網或雲端：

| 設定 | 用途 | 沒設定時 |
|---|---|---|
| `JETBOT_MQTT_HOST`、`JETBOT_MQTT_PORT` | broker 位址、埠號 | `localhost`；埠號 1883，啟用 TLS 時 8883 |
| `JETBOT_MQTT_USERNAME`、`JETBOT_MQTT_PASSWORD` | 帳密 | 不使用帳密（匿名） |
| `JETBOT_MQTT_TLS` | `1`／`true`／`yes`／`on` 啟用 TLS | 不加密 |
| `JETBOT_MQTT_CA_CERTS` | 驗證 broker 憑證用的 CA 檔 | 系統內建的 CA（雲端 broker 用這個即可） |

優先順序：**參數 > 環境變數 > 設定檔 > 預設值**。設定檔在兩端不同：

| 哪一端 | 設定檔 | 備註 |
|---|---|---|
| 車上 `sender.py` | 同資料夾的 `sender.env` | 與任務一相同的檔案，多加帳密與 TLS 幾行 |
| 筆電 `receiver.py`、`run.py` | 專案根目錄的 `.env` | 與 LINE token 同一份 |

兩個檔案都已被 `.gitignore` 忽略（`*.env`），**帳密不會進版控**。帳密也不提供命令列參數，避免留在 shell 的歷史紀錄；提示訊息只寫「TLS，使用帳密」，不印出帳號和密碼。

設定有問題時的行為：

| 情況 | 行為 |
|---|---|
| 帳密錯誤 | 提示一次 `broker 拒絕連線（rc=5）；請檢查帳號密碼`，背景繼續重試 |
| 有帳密但沒開 TLS、連的不是 localhost | 提示一次「帳密會以明文傳送」 |
| `JETBOT_MQTT_TLS` 填了看不懂的值 | 當作**啟用**並提示（寧可連不上，也不要把帳密明文送出） |
| CA 檔不存在 | 提示一次，**不連線**（不會退回不加密）；`send()` 照常立刻返回、丟棄資料 |
| TLS 沒開、CA 不對、位址錯 | paho 1.x 背景重試時不回報原因，只會一直連不上；`run.py` 啟動 15 秒還沒連上時提示要檢查哪些設定 |

既有行為都沒變：`send()` 不等網路、失敗不拋例外、斷線自動重連（1～5 秒）、重連後重新訂閱。車上仍只需要 `paho-mqtt<2.0`，`sender.py` 仍可單獨複製上車。

## 二、雲端 broker 設定步驟

使用 **EMQX Cloud Serverless 免費方案**（2026-10-11 申請：每月 100 萬連線分鐘、1 GB 流量，只開 TLS 8883，支援帳密）。任何支援 MQTT 3.1.1＋TLS＋帳密的服務都可以，步驟相同。

> 原本規劃的 HiveMQ Cloud Serverless 已於 **2026-09-30 停止新申請**（現有的 2026-12-31 停止服務），只剩付費方案（Starter 約 US$249／月），所以改用 EMQX。

流量估算：車上每秒約送 1 筆、每筆約 300 bytes，broker 收一次、轉給筆電一次，24 小時不停跑一個月約 1.5 GB，**超過免費的 1 GB**。只在測試、展示時開啟沒有問題；要長時間運作時再評估（付費約 US$0.15／GB，或自架主機）。

1. **註冊**：到 EMQX Cloud **國際版**主控台（`cloud-intl.emqx.com`）註冊，第一次登入建立一個 Project
2. **建立 broker**：Project Overview 的 **EMQX Broker** 卡片按 **New**
   - Plan 選 **Serverless**
   - Region 選 **Asia–Pacific**（目前用的是 Google Cloud `asia-southeast1`，新加坡）
   - **Spend Limit 維持 0**：只用免費額度，用完就暫停，**不會產生費用**（卡片上會顯示「Pay as you go」，是計費方式的名稱，不代表會收費）
   - Deployment Name 例如 `jetbot-broker`，按 **Deploy**，等狀態變成 **Running**
3. **位址**：進入 deployment 的 Overview，記下 **Address**（像 `xxxxxxxx.ala.asia-southeast1.emqxsl.com`），埠號 8883
4. **建立帳密**：左側 **Access Control** → **Authentication** → **Add**（車子與筆電可共用一組，也可以各建一組）
5. **車上**：在 `sender.py` 旁邊的 `sender.env` 寫入：

   ```
   JETBOT_MQTT_HOST=xxxxxxxx.ala.asia-southeast1.emqxsl.com
   JETBOT_MQTT_TLS=1
   JETBOT_MQTT_USERNAME=填你的帳號
   JETBOT_MQTT_PASSWORD=填你的密碼
   ```

   埠號不用寫（啟用 TLS 時自動用 8883）。`sender.py` 本身要換成這個任務的新版（重新 `scp` 一次）。

6. **筆電**：在專案根目錄的 `.env` 寫入同樣四行（格式見 `.env.example`）
7. **確認**：執行 `run.py`，看到下面兩行就是通了

   ```
   [sender] 已連上 broker xxxxxxxx.ala.asia-southeast1.emqxsl.com:8883（TLS，使用帳密），topic=jetbot/status
   [receiver] 已連上 broker xxxxxxxx.ala.asia-southeast1.emqxsl.com:8883（TLS，使用帳密），訂閱 jetbot/status
   ```

憑證：EMQX 用正式 CA 簽發的憑證，Mac 用系統內建的 CA 即可連上，不需要 `JETBOT_MQTT_CA_CERTS`。主控台 Overview 頁可以下載 CA 檔，車上若因 CA 清單太舊而連不上時再用。

要換回區網測試：把兩邊設定檔裡這四行刪掉或註解，改回筆電 IP 即可，程式不用改。

> ⚠️ **車上的時鐘**：雲端 broker 用正式憑證，TLS 會檢查憑證有效期限。Jetson 若沒有連網校時、時間停在很久以前，會因為「憑證尚未生效」而連不上。連不上時先在車上執行 `date` 確認時間。
>
> 車上系統內建的 CA 清單要能驗證雲端的憑證。JetPack 4.6（Ubuntu 18.04）的 `ca-certificates` 若太舊可能不認得，屆時更新該套件，或下載服務商提供的 CA 檔用 `JETBOT_MQTT_CA_CERTS` 指定。**尚未在 Jetson 上實測。**

## 三、本地加密 broker（不需要雲端帳號）

`broker/secure/` 是一個 TLS＋帳密的 Mosquitto，連線方式與雲端相同（8883、TLS、帳密），只是憑證是自簽的。用來在筆電上測試加密連線，或在還沒申請雲端之前先驗證設定。

```bash
cd transport/broker/secure && ./setup.sh
```

```bash
cd transport/broker/secure && docker compose up -d
```

`setup.sh` 會產生自簽憑證（`certs/`）、雜湊過的密碼檔（`passwd`），以及用戶端設定 `test.env`（帳號 `jetbot`、密碼隨機產生；也可以先設定環境變數 `JETBOT_MQTT_USERNAME`、`JETBOT_MQTT_PASSWORD` 自己指定）。這些檔案都不進版控。

用這組設定執行（在 repo 根目錄；`set -a` 讓 `test.env` 的內容變成環境變數，不會印出來）：

```bash
set -a; . transport/broker/secure/test.env; set +a; uv run --with "paho-mqtt<2.0" python run.py --llm template
```

## 四、`run.py`

```bash
# 預設：訊息產生器看 JETBOT_LLM_BACKEND（預設 local，沒裝 mlx-lm 時自動退回模板）、LINE 為 dry-run
uv run --with "paho-mqtt<2.0" python run.py

# 只用模板
uv run --with "paho-mqtt<2.0" python run.py --llm template

# 本地 LLM（Apple Silicon）
uv run --no-project --with "paho-mqtt<2.0" --with mlx-lm python run.py --llm local

# 真的送出 LINE（會消耗免費額度，每月約 200 則）
uv run --with "paho-mqtt<2.0" python run.py --send-line

# 不接 broker，重播錄製檔（示範、除錯）
uv run python run.py --replay rec.jsonl
```

| 設定 | 參數 | 環境變數／`.env` | 預設 |
|---|---|---|---|
| broker | `--host`、`--port`、`--tls` | 見第一節 | `localhost:1883`、不加密 |
| 訊息產生器 | `--llm template/local/cloud` | `JETBOT_LLM_BACKEND` | `local` |
| LLM 逾時 | `--llm-timeout` | `JETBOT_LLM_TIMEOUT` | 3 秒 |
| 實際送出 LINE | `--send-line` | `JETBOT_SEND_LINE=1` | **dry-run** |
| 通知紀錄檔 | `--log FILE`、`--no-log` | — | `logs/notices-日期-時間.jsonl` |
| 錄製原始資料 | `--record FILE` | — | 不錄 |
| 失聯門檻 | `--offline-sec` | — | 5 秒 |
| 狀態列 | `--status-every` | — | 每 60 秒一行，0 不印 |

| 項目 | 做法 |
|---|---|
| 預設 dry-run | 要加 `--send-line` 或 `JETBOT_SEND_LINE=1` 才真的送；指定了卻找不到 `LINE_CHANNEL_ACCESS_TOKEN` 時直接結束（結束碼 2），不會跑到一半才發現 |
| LLM 預熱 | `LLMFormatter` 在背景載入並預熱，啟動不等它；預熱期間的通知用模板，完成時印一行提示 |
| 不卡住判斷 | 產生文字與送 LINE 由 `background_output` 在背景執行緒依序處理，狀態機每秒 10 拍照常判斷 |
| broker 斷線 | 接收端自動重連、重新訂閱；斷線期間車外狀態機照常判定失聯並通知 |
| 結束 | Ctrl+C 與 `kill` 都會停止接收、等排隊中的通知送完（最多 15 秒）、印出統計，並在紀錄檔最後寫一行統計 |
| 記憶體 | 狀態機的通知清單只留最近 200 則（完整紀錄在檔案裡），延遲統計最多 1 萬筆，其餘紀錄本來就有上限 |

### 通知紀錄檔

每行一則 JSON，可直接拿來做報告數據：

```json
{"type": "notice", "time_str": "2026-09-26 16:08:22", "category": "no_target", "kind": "alert",
 "text": "【伴護機器人】16:08\n車子看不到受護者，正在搜尋。", "generator": "template", "fallback_reason": "template",
 "line": "dry-run", "dry_run": true, "latency_sec": 0.028, "broker_connected": true, ...}
```

| 欄位 | 意思 |
|---|---|
| `generator`、`fallback_reason`、`gen_sec` | 實際用了模板或 LLM；退回模板的原因（`loading` 預熱中、`timeout` 逾時等）；LLM 產生秒數 |
| `line` | `dry-run`、`HTTP 200`，或失敗原因 |
| `latency_sec` | 車上 `ts` → 通知輸出完成（含產生文字與送 LINE）。失聯通知沒有對應的車上資料，為 `null` |
| `broker_connected` | 通知當下筆電是否連著 broker。失聯通知時為 `false` 代表是**筆電這端斷線**，`true` 代表 broker 正常、是車子沒送資料 |

最後一行 `"type": "summary"` 是結束時的統計（收到筆數、通知則數、連線次數、延遲、記憶體峰值）。

> 重播時 `--speed` 不要超過約 5：狀態機每秒讀快取 10 次，資料來得比這快時，覆蓋快取會把中間含事件的資料蓋掉（覆蓋快取的設計本來就是這樣，實車每秒約 1 筆沒有這個問題）。加速重播的 `ts` 是模擬時間，所以不統計延遲。

## 五、實測結果（2026-09-26）

環境：M1 MacBook Air、macOS、Python 3.13、paho-mqtt 1.6.1、Docker 裡的 Mosquitto 2.1.2，全部在同一台筆電。

### 本地無加密 broker：`simulator.py --mqtt` → `run.py`

```bash
uv run --with "paho-mqtt<2.0" python run.py --llm template
```

```bash
uv run --with "paho-mqtt<2.0" python transport/simulator.py long_loss --mqtt --host localhost
```

| 項目 | 結果 |
|---|---|
| 收到資料 | 163／163 筆，延遲（車上 → 收到）中位數 **3 ms**、最大 11 ms |
| 通知 | `no_target/alert`（延遲 28 ms）→ `no_target/recovery`（87 ms）→ 產生器結束 5 秒後 `offline/alert`，共 3 則，都寫進紀錄檔 |
| `kill` 結束 | 結束碼 0，印出統計，紀錄檔最後一行是統計 |

### 本地加密 broker（TLS＋帳密），中途停掉 broker

`simulator.py normal --mqtt`（60 秒），第 18 秒 `docker stop`、16 秒後 `docker start`，`sender.py` 與 `run.py` 都用 `test.env` 的設定：

| 時間 | 事件 |
|---|---|
| 0 秒 | `sender`、`receiver` 都顯示 `已連上 broker localhost:8883（TLS，使用帳密）` |
| broker 停止 | 兩邊都提示斷線一次；**5.1 秒後** `offline/alert`，紀錄檔 `broker_connected: false` |
| broker 啟動 | 兩邊約 1 秒內自動重連，`receiver` 重新訂閱；隨即 `offline/recovery`（中斷約 18 秒，延遲 91 ms） |
| 產生器結束 | 5 秒後 `offline/alert`（`broker_connected: true`，代表是車子沒送） |
| 統計 | 收到 44 筆（61 筆中斷線期間的 17 筆依設計丟棄）、連上 2 次、非預期斷線 1 次、延遲中位數 2 ms |

日誌與紀錄檔中搜尋密碼：0 筆。

錯誤設定（連同一台加密 broker）：

| 設定 | 結果 |
|---|---|
| 正確設定 | 連上 |
| 密碼錯誤 | 連不上，提示 `rc=5；請檢查帳號密碼` |
| 沒開 TLS 連 8883 | 連不上（`run.py` 15 秒後提示檢查設定） |
| CA 不對（用系統 CA 驗自簽憑證） | 連不上，不會退回不驗證 |
| CA 檔不存在 | 提示 `TLS 設定失敗`，不連線 |

### 本地 LLM

```bash
uv run --no-project --with "paho-mqtt<2.0" --with mlx-lm python run.py --llm local --replay long_loss.jsonl --speed 5
```

啟動立刻開始接收；第 3 秒的 `no_target/alert` 在模型預熱中，用模板（`fallback_reason: loading`）；**啟動後 19.4 秒預熱完成**，之後的 `no_target/recovery` 由本地模型產生（2.6 秒）。記憶體峰值 516 MB（含模型）。

### 長時間執行

`run.py` 連續執行 60 分鐘；模擬器不停輪播 10 種情境（即時 1 倍速）；每 10 分鐘 `docker restart` 一次 broker；每 30 秒用 `ps` 記錄一次記憶體（RSS）。

| 項目 | 結果 |
|---|---|
| 運作 | 1 小時 0 分 15 秒不中斷，`kill` 後結束碼 0，印出統計 |
| 收到資料 | 3,648 筆，格式錯誤 0 筆；延遲中位數 2 ms、最大 129 ms |
| broker 重啟 5 次 | 5 次都自動重連（連上 6 次、非預期斷線 5 次） |
| 通知 | 69 則（無法通過 20＋20、看不到人 7＋7、失聯 8＋7，通知＋解除），延遲中位數 60 ms、最大 110 ms |
| 記憶體（RSS） | 開始 24.8 MB → 30 分鐘 15.0 MB → 60 分鐘 13.1 MB，範圍 11.0～25.2 MB，**沒有持續成長** |

- 記憶體往下掉是 macOS 壓縮閒置記憶體，不是程式釋放；重點是一小時內沒有往上爬
- `docker restart` 約 1～2 秒就重連完成，短於失聯門檻 5 秒，所以 broker 重啟**沒有**觸發失聯通知（8 則失聯通知都來自 `offline` 情境與結束時）。broker 長時間中斷的失聯通知見上面加密 broker 的實測
- 只測了 1 小時、只用模板；「連續數小時」與本地 LLM 長時間執行尚未實測

## 六、雲端 broker 實測（2026-10-11，EMQX Serverless 新加坡）

同一台筆電：`simulator.py --mqtt`（經 `sender.py`）→ **EMQX Cloud**（TLS＋帳密）→ `run.py --llm template`。
兩端都經過網際網路，所以延遲是車上與筆電**各走一趟**的保守值。

| 測試 | 網路 | 結果 |
|---|---|---|
| 1. `normal`＋`blocked_long` | 家裡網路 | 85／85 筆；車上 → 收到 中位數 **72 ms**（最大 272 ms）；通知產生完成 118～158 ms |
| 2. 同上 | **手機熱點**（iPhone） | 85／85 筆；車上 → 收到 中位數 **103 ms**（最大 301 ms）；通知產生完成 117～201 ms |
| 3. `long_loss`，第 30 秒關網路、第 50 秒打開 | 家裡網路 | 5.1 秒後失聯通知；兩端自動重連，實際中斷約 26 秒（Wi-Fi 重連＋TLS 重連），隨後連線恢復通知；跟丟狀態跨斷線正確延續（沒有重複的「看不到受護者」，最後的找回通知正確） |
| 4. `blocked_long`，**實際送出 LINE** | 家裡網路 | 2／2 HTTP 200；**車上送出 → LINE 送達 427～457 ms** |

兩個情境之間模擬器重新啟動、重新連線約數秒，沒有誤發失聯通知。

**測試 3 發現的問題**（dry-run 看不出來）：

- 失聯通知是在**筆電自己也斷網**時產生的。若實際送出 LINE 會失敗，而 `run.py` **失敗不重試**，家屬只會收到後面的「連線恢復」
- 通知紀錄的 `broker_connected` 在失聯通知當下是 `true`：筆電要十幾秒才察覺自己斷線（比 5 秒的失聯門檻慢），這個欄位無法可靠區分「車子失聯」與「筆電斷網」

實際部署時筆電在家、網路穩定，較少遇到；但 `run.py` 搬上雲端主機之前應補上 LINE 送出失敗的重試。

### 未驗證

- **尚未在 Jetson（Python 3.6）上實測** `sender.py` 的 TLS：已用 Python 3.6 文法做靜態檢查，`tls_set()`、`username_pw_set()` 是 paho-mqtt 1.x 就有的 API；車上的系統時間與 CA 清單也要確認（見第二節）
- **只有車子端斷線**的情況：第六節的測試兩端在同一台電腦，關網路時會一起斷
- 車子連熱點、筆電連家裡網路的實際延遲（第六節是兩端都走同一個網路的保守值）

## 測試

不需要 broker、雲端、網路、LLM 模型，也不會呼叫 LINE：

```bash
uv run python tests/test_mqtt_auth.py
```

```bash
uv run python tests/test_run.py
```
