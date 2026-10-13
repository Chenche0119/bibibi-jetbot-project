# 嗶嗶嗶專 - JetBot 智慧伴護機器人專題

以 JetBot（Jetson Nano）+ Arduino 為核心的畢業專題，包含物件跟隨狀態機、影像蒐集與標記校正。

## 資料夾結構

| 路徑 | 內容 |
|---|---|
| `docs/proposals/` | 專題計畫構想書、市場分析、甘特圖 |
| `docs/jetbot-setup/` | JetBot 連線 SOP、硬體整合、WiFi 診斷、YOLOv8 轉 TensorRT |
| `docs/design/` | 功能規劃、演算法流程圖、資料流程圖、狀態機實作說明 |
| `docs/reports/` | 進度報告、組內討論、影像蒐集工作分配 |
| `src/` | Python 程式（`dfrobot_robot.py`、`jetbot_controller.py`、`marker.py`） |
| `notebooks/` | 資料蒐集與跟隨狀態機 Jupyter notebook |
| `arduino/` | Arduino 測試程式 |
| `marker_calibration/` | 標記顏色 HSV 校正流程 |
| `dataset/` | free / blocked 影像資料集 |
| `data/` | 影像蒐集範例資料夾、task_A_alert_20261013 警示影像 |

`large-files/`（簡報 pptx、dataset.zip、WiFi 網卡驅動 zip）僅保留在本機，未納入版控。
