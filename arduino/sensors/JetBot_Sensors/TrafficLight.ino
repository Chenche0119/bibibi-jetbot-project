
const int RED_PIN = 2;
const int YELLOW_PIN = 3;
const int GREEN_PIN = 4;

// 1 = 綠燈
// 2 = 黃燈
// 3 = 紅燈
byte currentTrafficState = 3;

// 初始化紅綠燈
void initTrafficLight() {
  pinMode(RED_PIN, OUTPUT);
  pinMode(YELLOW_PIN, OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);

  // 啟動時尚未確認照護者，預設紅燈
  setTrafficState(3);
}

// 根據 Python 傳來的狀態切換燈號
void setTrafficState(byte state) {
  if (state < 1 || state > 3) {
    return;
  }

  currentTrafficState = state;

  // 先關閉所有燈
  digitalWrite(RED_PIN, LOW);
  digitalWrite(YELLOW_PIN, LOW);
  digitalWrite(GREEN_PIN, LOW);

  switch (state) {
    case 1:
      digitalWrite(GREEN_PIN, HIGH);
      break;

    case 2:
      digitalWrite(YELLOW_PIN, HIGH);
      break;

    case 3:
      digitalWrite(RED_PIN, HIGH);
      break;
  }
}

// 取得目前狀態編號
byte getTrafficState() {
  return currentTrafficState;
}

// 取得目前燈號名稱
const char* getTrafficStatus() {
  switch (currentTrafficState) {
    case 1:
      return "GREEN";

    case 2:
      return "YELLOW";

    default:
      return "RED";
  }
}
