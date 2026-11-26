#include <string.h>

// ==========================================
// 左右超音波（保留原本兩份 .ino）
// ==========================================
void initUltrasonic();
float readUltrasonicCm();
void initUltrasonicRight();
float readUltrasonicRightCm();

// ==========================================
// OLED（使用原本已正常運作的 Hardware I2C）
// ==========================================
bool initOLED();
void updateOLED(float left, float right);

// ==========================================
// 紅綠燈（由 Python 傳入 1 / 2 / 3）
// ==========================================
void initTrafficLight();
void setTrafficState(byte state);
const char* getTrafficStatus();

float leftDistance = -1;
float rightDistance = -1;

unsigned long lastMeasureTime = 0;
const unsigned long MEASURE_INTERVAL = 300;

unsigned long lastVisionTime = 0;
bool visionConnected = false;
const unsigned long VISION_TIMEOUT = 2000;

void printDistance(const char* prefix, float distance) {
  Serial.print(prefix);
  if (distance < 0) {
    Serial.println("INVALID");
  } else {
    Serial.println(distance, 1);
  }
}

void refreshMeasurements() {
  leftDistance = readUltrasonicCm();
  delay(60);  // 降低左右超音波互相干擾
  rightDistance = readUltrasonicRightCm();
  updateOLED(leftDistance, rightDistance);
}

void receiveVisionState(byte state) {
  setTrafficState(state);
  lastVisionTime = millis();
  visionConnected = true;
  Serial.print("TRAFFIC:");
  Serial.println(getTrafficStatus());
}

void handleCommand(const char* command) {
  if (strcmp(command, "1") == 0) {
    receiveVisionState(1);
  } else if (strcmp(command, "2") == 0) {
    receiveVisionState(2);
  } else if (strcmp(command, "3") == 0) {
    receiveVisionState(3);
  } else if (strcmp(command, "GET_ULTRASONIC") == 0 ||
             strcmp(command, "GET_DISTANCE") == 0) {
    printDistance("DIST:", leftDistance);
  } else if (strcmp(command, "GET_ULTRASONIC_LEFT") == 0) {
    printDistance("DIST_LEFT:", leftDistance);
  } else if (strcmp(command, "GET_ULTRASONIC_RIGHT") == 0) {
    printDistance("DIST_RIGHT:", rightDistance);
  } else if (strcmp(command, "GET_ALL") == 0) {
    printDistance("DIST_LEFT:", leftDistance);
    printDistance("DIST_RIGHT:", rightDistance);
  } else if (strcmp(command, "GET_TRAFFIC") == 0) {
    Serial.print("TRAFFIC:");
    Serial.println(getTrafficStatus());
  } else {
    Serial.println("ERROR:UNKNOWN_COMMAND");
  }
}

void setup() {
  Serial.begin(9600);

  initUltrasonic();
  initUltrasonicRight();
  initTrafficLight();  // 初始狀態為紅燈（依 TrafficLight.ino 設定）
  initOLED();

  refreshMeasurements();
  lastMeasureTime = millis();
  Serial.println("READY");
}

void loop() {
  if (millis() - lastMeasureTime >= MEASURE_INTERVAL) {
    refreshMeasurements();
    lastMeasureTime = millis();
  }

  static char command[32];
  static byte index = 0;

  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      command[index] = '\0';
      if (index > 0) {
        handleCommand(command);
      }
      index = 0;
    } else if (index < sizeof(command) - 1) {
      command[index++] = c;
    } else {
      index = 0;
      Serial.println("ERROR:COMMAND_TOO_LONG");
    }
  }

  // 若超過 2 秒沒有收到影像辨識狀態，亮紅燈
  if (visionConnected && millis() - lastVisionTime >= VISION_TIMEOUT) {
    setTrafficState(3);
    visionConnected = false;
    Serial.println("VISION_TIMEOUT");
  }
}
