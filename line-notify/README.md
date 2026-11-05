# line-notify：車外監控與 LINE 通報

智慧伴護機器人的**車外**這一段：接收車上跟隨狀態機的回報，判斷要不要通知家屬，產生訊息送到 LINE。

```
【車上 JetBot】follow_state ─ sender.send(payload) ─┐
                                                    │ MQTT（TLS＋帳密）
                                    EMQX Cloud（雲端 broker）
                                                    │
【車外筆電】 run.py：接收 → 車外狀態機（含失聯偵測）→ 訊息產生（本地 LLM／模板）→ LINE
```

- 車上只需要 `transport/sender.py`，其餘都在筆電上執行
- 實測（2026-10-11）：車上送出 → 家屬收到 LINE 約 **0.45 秒**（構想書要求 3 秒內）
- LINE 用 Messaging API（構想書原寫的 LINE Notify 已於 2025-03-31 停止服務）

## 資料夾

| 路徑 | 內容 |
|---|---|
| `transport/sender.py` | **車上用**：`send(payload)` 不等網路、失敗不拋例外、斷線自動重連；相容 Python 3.6 |
| `transport/receiver.py` | 筆電：訂閱 MQTT、格式檢查、寫入覆蓋快取 |
| `transport/simulator.py` | 假資料產生器：照車上規格逐幀模擬跟隨狀態機，10 種情境，不用開車就能測 |
| `transport/broker/` | 本地 Mosquitto（Docker），`secure/` 是 TLS＋帳密版 |
| `state_machine/machine.py` | 車外狀態機：分類、冷卻、失聯偵測、30 句通知模板 |
| `llm/` | 本地 LLM 轉譯（Apple Silicon，MLX），失敗或逾時自動改用模板 |
| `run.py` | 一個指令啟動整條線 |
| `tests/` | 8 支測試，不需要硬體、網路或 LLM 模型 |

各資料夾的細節見 `transport/README.md`、`llm/README.md`。

## 部署 sender.py 到 JetBot

`notebooks/live_demo_following_sm.ipynb` 已經寫好 `import sender`（載入失敗時退回 console 印字，不影響車子運作），所以只要：

```
object_following/                    ← JetBot 上跟 notebook 同一個資料夾
├─ live_demo_following_sm.ipynb
├─ follow_state.py 等（見根目錄 README）
├─ sender.py                         ← line-notify/transport/sender.py
└─ sender.env                        ← 連線設定，不進版控（見下方）
```

車上需要 `paho-mqtt<2.0`（`pip3 install "paho-mqtt<2.0"`，JetPack 4.6 的 Python 3.6）。

`sender.env`（4 行，帳密向負責人索取，**不要 commit**）：

```
JETBOT_MQTT_HOST=<EMQX 位址>
JETBOT_MQTT_TLS=1
JETBOT_MQTT_USERNAME=<帳號>
JETBOT_MQTT_PASSWORD=<密碼>
```

連不上雲端時先在車上執行 `date` 確認系統時間：TLS 會檢查憑證有效期限。

## 在筆電上執行

需要 [uv](https://docs.astral.sh/uv/)。

```bash
cd line-notify
cp .env.example .env      # 填 LINE token 與 broker 設定；.env 不進版控
```

```bash
uv run --with "paho-mqtt<2.0" python run.py --llm template     # LINE 預設 dry-run，加 --send-line 才會真的送
```

另開一個終端機，用假資料代替車子：

```bash
uv run --with "paho-mqtt<2.0" python transport/simulator.py long_loss --mqtt
```

沒有雲端帳號時，可以先跑本地 broker：`docker compose -f transport/broker/docker-compose.yml up -d`，`.env` 的 broker 設定留空即可。

測試：

```bash
for t in tests/test_*.py; do uv run --with "paho-mqtt<2.0" python "$t"; done
```

## 目前狀態（2026-10-16）

| 項目 | 狀態 |
|---|---|
| 假資料 → 雲端 broker → run.py → LINE | ✅ 實測通過（含手機熱點、中途斷網自動重連） |
| sender.py 上真車 | ⬜ 等車上串接 |
| run.py 執行位置 | 專題實作放筆電（Amphetamine 防睡眠）；完全雲端化列為未來展望 |

已知限制：筆電關機時通報停止；推播對象目前是 broadcast（所有加 Bot 好友的人都會收到）；LINE 免費方案每月 200 則。

> 程式註解中提到的 `docs/…`（規格回覆、進度凍結、任務行前檔）是負責人本地 repo 的設計文件，未放進這個 repo。
