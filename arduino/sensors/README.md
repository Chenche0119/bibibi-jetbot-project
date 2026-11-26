# Arduino 感測器與狀態顯示模組

本資料夾為 JetBot 智慧伴護機器人上的 **Arduino UNO 感測器與顯示模組**。Arduino 負責讀取左右兩顆 HC-SR04 超音波感測器、更新 SSD1306 OLED，以及接收 Jetson Nano／Python 經 USB Serial 傳來的追蹤狀態並顯示紅、黃、綠燈。

> 此文件依 2026-11 討論中的無蜂鳴器修正版程式與已確認接線整理。上傳前請與實際五份 `.ino` 原始碼及實機接線再次比對；本文件不代表已對整個 GitHub 版本完成編譯或實機驗證。

## 檔案結構

目前採用 Arduino IDE 可直接開啟的 Sketch 資料夾：

```text
arduino/sensors/
├── README.md
└── JetBot_Sensors/
    ├── JetBot_Sensors.ino       # 初始化、主迴圈、USB Serial 指令
    ├── Ultrasonic_left.ino      # 左 HC-SR04
    ├── Ultrasonic_right.ino     # 右 HC-SR04
    ├── OLED.ino                 # OLED 距離顯示
    └── TrafficLight.ino         # Python 追蹤狀態燈號
```

主 Sketch 檔名與目錄都為 `JetBot_Sensors`，所有 `.ino` 放在同一個 Sketch 資料夾。

## 硬體及接線

| 模組 | Arduino UNO | 功能 |
|---|---|---|
| 左 HC-SR04 | TRIG D11、ECHO D12 | 左側障礙物距離 |
| 右 HC-SR04 | TRIG D9、ECHO D10 | 右側障礙物距離 |
| SSD1306 OLED 128×64 | SDA（A4）、SCL（A5） | 顯示左右測距；使用 **Hardware I²C** |
| 紅綠燈模組 | 紅 D2、黃 D3、綠 D4 | 顯示追蹤狀態 |
| Jetson Nano | UNO USB Serial | 傳送追蹤狀態、讀取測距 |

UNO 的額外 `SDA`、`SCL` 腳位分別與 A4、A5 共用硬體 I²C 訊號。OLED 請依模組規格供電；紅綠燈程式假設燈號為 `HIGH` 時亮燈，實際仍需確認模組電路與限流設計。

**目前不使用：** VL53L0X 雷射測距、蜂鳴器。

## 軟體需求

- Arduino UNO、Arduino IDE。
- Arduino Library Manager 安裝 **U8g2**（使用 `U8x8lib.h`，文字模式減少 SRAM 使用量）。
- Serial baud rate：**9600**。
- Jetson Nano 端如需透過 Python 連線，可使用 `pyserial`；實際裝置名稱可能為 `/dev/ttyACM0` 或其他名稱。

## USB Serial 通訊協定

所有命令以 **換行 `\n`** 結束，回覆為 ASCII 文字、每筆一行。

### Jetson Nano／Python → Arduino

| 指令 | 用途 | Arduino 回覆（示例） |
|---|---|---|
| `1\n` | 綠燈：已辨識並正常跟隨 | `TRAFFIC:GREEN` |
| `2\n` | 黃燈：照護者停止或減速 | `TRAFFIC:YELLOW` |
| `3\n` | 紅燈：未辨識或跟丟 | `TRAFFIC:RED` |
| `GET_ALL\n` | 取得左右測距 | 依序兩行 `DIST_LEFT:<值>`、`DIST_RIGHT:<值>` |
| `GET_ULTRASONIC_LEFT\n` | 左側測距 | `DIST_LEFT:<值>` |
| `GET_ULTRASONIC_RIGHT\n` | 右側測距 | `DIST_RIGHT:<值>` |
| `GET_TRAFFIC\n` | 目前燈號 | `TRAFFIC:RED` 等 |
| `GET_ULTRASONIC\n` 或 `GET_DISTANCE\n` | 舊版相容命令（**只回傳左側**） | `DIST:<值>` |

距離以 **cm** 表示，顯示到小數一位。量測失敗時回覆 `INVALID`，例如 `DIST_LEFT:INVALID`。當收到未知命令時回覆 `ERROR:UNKNOWN_COMMAND`。

### 資料更新與逾時

- Arduino 約每 **300 ms** 更新左右兩組超音波讀數，讀取兩組之間有約 **60 ms** 間隔；`GET_ALL` 回傳的是最近一次快取值，而不是命令到達時立即重新測距。
- 影像辨識 Python 應定期重送目前的 `1`、`2`、`3`，建議約每 **0.5 秒**一次。
- 接收到一次有效狀態後，若超過 **2 秒**沒有再次收到，Arduino 會轉為紅燈並輸出 `VISION_TIMEOUT`。
- 啟動時紅燈為預設燈號（仍應以 `TrafficLight.ino` 實際版本確認）。
- **紅燈與逾時提示不會自動停止 JetBot 馬達。** 馬達安全停止與避障策略須由車上控制程式獨立實作。

## Arduino IDE 操作

1. 開啟 `JetBot_Sensors/JetBot_Sensors.ino`（其餘四個 `.ino` 應同時出現在分頁）。
2. 選取 **Arduino UNO** 與實際 USB 序列埠。
3. 確認已安裝 U8g2，先按 **Verify** 檢查編譯，再按 **Upload**。
4. 開啟 Serial Monitor：**9600 baud**、Line ending 設為 **Newline**。
5. 確認啟動畫面／Serial 顯示 `READY`，OLED 持續顯示左右距離。
6. 輸入 `GET_ALL`，確認左右距離兩行回覆；分別將平整紙板靠近左右感測器，檢查數值是否合理變動。
7. 依序輸入 `1`、`2`、`3`，確認對應綠、黃、紅燈；測試 `1` 後停止發送 2 秒以上，確認會自動紅燈。

## Jetson Nano／Python 簡易測試

```python
import time
import serial

ser = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
time.sleep(3)  # 開啟序列埠可能重置 UNO
ser.reset_input_buffer()

ser.write(b'GET_ALL\n')
ser.flush()
for _ in range(2):
    print(ser.readline().decode('utf-8', errors='replace').strip())

ser.write(b'3\n')
ser.flush()
print(ser.readline().decode('utf-8', errors='replace').strip())
ser.close()
```

> 若 Jupyter 在 Docker 容器內執行，需確保 Arduino 裝置已正確映射到容器。`/dev/ttyACM0` 是範例路徑；以 Jetson Nano Host 上實際裝置為準。不要讓多個 Python 程式同時開啟同一序列埠。測試紅綠燈期間請讓車輪離地或確保馬達停止。

## 與現有組員程式的相容性（正式整合前必讀）

本儲存庫另有較早的 `arduino/arduino_feedback/arduino_feedback.ino`、`src/feedback.py`、`src/rangefinder.py` 與 `src/test_feedback.py`。**它們和本模組目前不能直接互通**：

| 項目 | 本模組 `JetBot_Sensors` | 組員既有 `arduino_feedback`／`feedback.py` |
|---|---|---|
| Serial 鮑率 | `9600` | `115200` |
| 燈號命令 | `1`、`2`、`3`，一種狀態對應一顆燈 | `LIGHT:100` 等，可控制多顆燈與閃爍 |
| 距離資料 | `GET_ALL` 請求後，回傳左右 `DIST_LEFT`／`DIST_RIGHT`，單位 cm | Arduino 主動推送 `D:<mm>`，Python 讀取並濾波 |
| 硬體 | 雙 HC-SR04、OLED、紅綠燈；無雷射、無蜂鳴器 | VL53L0X、紅綠燈、蜂鳴器 |
| 腳位 | 紅 D2、黃 D3、綠 D4；右超音波 D9/D10，左 D11/D12 | 紅 D10、黃 D9、綠 D8、蜂鳴器 D11 |
| 安全／保活 | Python 需每 2 秒內至少送一次 `1/2/3`，建議每 0.5 秒 | `Feedback._write()` 只在燈號組合改變時送出，不會持續保活 |

若要把目前雙超音波韌體接上既有 `src/feedback.py`，請先由組員共同決定最新的**唯一通訊規格與燈號狀態語意**，再同步修改 Python 端與測試檔；不能單純修改 Serial 鮑率就視為完成。特別是舊版狀態機的 `waiting`（紅慢閃）、`confirming`（黃快閃）、`detour`（黃+綠）等，無法直接用單一 `1/2/3` 命令完全表達；需明確決定是簡化狀態或擴充新韌體協定。

舊版 `rangefinder.py` 依賴 `feedback.py` 持續收到 `D:<mm>`；新版不再主動推播雷射數據，因此須改成定期請求 `GET_ALL`、解析兩側回應，並決定避障優先權與資料過期保護。兩份 Arduino 韌體也**不可同時燒錄到同一塊 UNO**；接腳配置不同，不可混用。

## 尚待確認的整合責任

- 誰負責修改 `src/feedback.py`、`src/rangefinder.py` 及相關測試。
- 新版三色燈只表示追蹤狀態，或仍需支援組員原先的閃爍、多燈組合與繞行狀態。
- Python 與 Arduino 誰負責保活，以及車子在跟丟、USB 中斷、感測器無效時的**實際馬達停止**方式。
- 最終 Arduino 韌體已整理至 `arduino/sensors/JetBot_Sensors/`；仍須由組員確認整個專題採用的唯一燒錄入口。

## 已知限制與後續改善

- HC-SR04 對感測角度、目標表面與環境回波敏感；有數值回傳不等於已證明測距準確。仍需以固定距離紙板量測誤差及無效率。
- 目前輸出為最近一次讀數，並未提供讀數產生時間或濾波後數據；正式閉迴路避障前建議增加資料時效性檢查。
- Python 狀態 `1/2/3` 是影像辨識／跟隨狀態訊號，不代表超音波避障判定。障礙物過近時，馬達必須有獨立安全停止邏輯。
- Serial 連線失效時，Arduino 可以顯示紅燈；但 Python 與馬達控制器仍必須處理 USB 中斷、讀取逾時與失聯。
- 本模組沒有整合雷射與蜂鳴器；舊 Notebook 或舊 `.ino` 若仍含相關命令／程式，請標記為歷史版本，避免誤用。
