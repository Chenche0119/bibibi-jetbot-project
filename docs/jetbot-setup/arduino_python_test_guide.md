# Python → Arduino 通訊測試指南

## 需要的工具

- **Arduino IDE**（上傳 Sketch 用）
- **Python 3**（Mac 內建）
- **Terminal**
- **硬體**：Arduino UNO R3、麵包板、LED、220Ω 電阻、USB Type-B 線

---

## 第一步：硬體接線（麵包板）

```
Arduino Pin 13 → 電阻 220Ω → LED 長腳（陽極）
LED 短腳（陰極） → Arduino GND
```

---

## 第二步：上傳 Sketch（Arduino IDE）

選 **Tools → Board → Arduino UNO**，貼入以下程式碼後按上傳（→）：

```cpp
// Arduino C++
String inputString = "";

void setup() {
  Serial.begin(9600);
  pinMode(13, OUTPUT);
  Serial.println("Arduino Ready");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      handleCommand(inputString);
      inputString = "";
    } else {
      inputString += c;
    }
  }
}

void handleCommand(String cmd) {
  if (cmd == "LED_ON") {
    digitalWrite(13, HIGH);
    Serial.println("LED ON");
  } else if (cmd == "LED_OFF") {
    digitalWrite(13, LOW);
    Serial.println("LED OFF");
  } else {
    Serial.println("Unknown: " + cmd);
  }
}
```

---

## 第三步：安裝 pyserial

在 Terminal 執行：

```bash
pip3 install pyserial
```

---

## 第四步：找到 Arduino 的埠號

Arduino 接上 Mac USB 後，在 Terminal 執行：

```bash
ls /dev/cu.usbmodem*
```

記下輸出的埠號，例如 `/dev/cu.usbmodem14101`。

---

## 第五步：執行 Python 腳本

建立 `jetbot_controller.py`，將埠號換成上一步查到的值：

```python
# Python
import serial
import time

ser = serial.Serial('/dev/cu.usbmodem14101', 9600, timeout=2)  # 換成你的埠號
time.sleep(2)  # 等 Arduino 重置完成

ready = ser.readline().decode().strip()
print(f"狀態: {ready}")

def send(cmd):
    ser.write((cmd + '\n').encode())
    time.sleep(0.1)
    response = ser.readline().decode().strip()
    print(f"  → {cmd}: {response}")

send("LED_ON")
time.sleep(1)
send("LED_OFF")

ser.close()
```

在 Terminal 執行：

```bash
python3 jetbot_controller.py
```

---

## 預期輸出

```
狀態: Arduino Ready
  → LED_ON: LED ON
  → LED_OFF: LED OFF
```

LED 應該亮一秒後熄滅。

---

## 常見問題

| 問題 | 原因 | 解法 |
|---|---|---|
| `Permission denied` | 沒有 Serial 埠權限 | `sudo usermod -a -G dialout $USER` 後重新登入 |
| 找不到 `/dev/cu.usbmodem*` | Arduino 未連上或 USB 線不對 | 換 USB 線、確認 Arduino IDE 能認到板子 |
| 輸出亂碼 | Baud Rate 不一致 | 確認 Sketch 和 Python 都是 `9600` |
| 沒有回應 | Sketch 未上傳成功 | Arduino IDE 重新上傳 |
