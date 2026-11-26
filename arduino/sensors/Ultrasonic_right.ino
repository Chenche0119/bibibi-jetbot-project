
const int TRIG_PIN_RIGHT = 9;
const int ECHO_PIN_RIGHT = 10;

// 初始化右側超音波感測器
void initUltrasonicRight() {
  pinMode(TRIG_PIN_RIGHT, OUTPUT);
  pinMode(ECHO_PIN_RIGHT, INPUT);

  digitalWrite(TRIG_PIN_RIGHT, LOW);
}

// 取得右側超音波距離（公分）
float readUltrasonicRightCm() {
  digitalWrite(TRIG_PIN_RIGHT, LOW);
  delayMicroseconds(2);

  digitalWrite(TRIG_PIN_RIGHT, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN_RIGHT, LOW);

  unsigned long duration =
      pulseIn(ECHO_PIN_RIGHT, HIGH, 30000UL);

  if (duration == 0) {
    return -1;
  }

  float distance = duration * 0.0343f / 2.0f;

  if (distance < 2 || distance > 400) {
    return -1;
  }

  return distance;
}

