# 嗶嗶嗶專 - JetBot 智慧伴護機器人專題

以 JetBot（Jetson Nano）+ Arduino 為核心的畢業專題，包含物件跟隨狀態機、影像蒐集與標記校正。

## 資料夾結構

| 路徑 | 內容 |
|---|---|
| `docs/proposals/` | 專題計畫構想書、市場分析、甘特圖 |
| `docs/jetbot-setup/` | JetBot 連線 SOP、硬體整合、WiFi 診斷、YOLOv8 轉 TensorRT |
| `docs/design/` | 功能規劃、演算法流程圖、資料流程圖、狀態機實作說明 |
| `docs/reports/` | 進度報告、組內討論、影像蒐集工作分配 |
| `src/` | Python 程式：跟隨模組（`follow_state.py` 狀態機、`marker.py` 標記辨識、`feedback.py` 燈號/蜂鳴器/序列埠、`rangefinder.py` 測距）、馬達驅動 `dfrobot_robot.py`、離線測試 `test_*.py`、`jetbot_controller.py` |
| `notebooks/` | 資料蒐集與跟隨狀態機 Jupyter notebook |
| `arduino/` | Arduino 程式：`arduino_feedback/`（正式韌體：紅綠燈、蜂鳴器、VL53L0X 雷射，115200）、測試程式 |
| `marker_calibration/` | 標記顏色 HSV 校正流程 |
| `docs/yolo/` | YOLO 模型建置流程草案、討論問答紀錄 |
| `preprocessing/` | 資料集 metadata、完整性報告、train/test 切分結果 |
| `scripts/` | 前處理腳本（`build_metadata.py`、`split_dataset.py`） |
| `dataset/` | free / blocked 影像資料集 |
| `data/` | 影像蒐集範例資料夾、task_A_alert_20261013 警示影像 |

## 部署跟隨模組到 JetBot

`notebooks/live_demo_following_sm.ipynb` 會 `import` 下面這些檔案，**上傳到 JetBot 時要跟 notebook 放在同一個資料夾**：

```
object_following/                    ← JetBot 上任意一個資料夾
├─ live_demo_following_sm.ipynb      ← notebooks/
├─ follow_state.py                   ← src/
├─ marker.py                         ← src/
├─ feedback.py                       ← src/
├─ rangefinder.py                    ← src/
├─ dfrobot_robot.py                  ← src/
└─ ssd_mobilenet_v2_coco.engine      ← 不在版控(模型檔),要在 JetBot 上重編
```

改過 `.py` 之後要 **Kernel → Restart**，Jupyter 不會自動重載模組。

在筆電上跑離線測試（不需要 JetBot）：

```bash
cd src
python test_follow_state.py   # 狀態機 27 組
python test_marker.py         # 標記辨識 6 組
python test_feedback.py       # 燈號/序列埠 10 組
```

### 身分標記（定案）

被跟隨者**一條腿的腳踝包一圈布條，上桃紅、下螢光綠**，由 `src/marker.py` 用 HSV 色塊辨識，
**桃紅或綠色任一被認出就算數**。YOLO 只負責框出 `person`，不學 `marker` 類別。
校正流程與實測數據見 `marker_calibration/README.md`。

`large-files/`（簡報 pptx、dataset.zip、WiFi 網卡驅動 zip）僅保留在本機，未納入版控。
