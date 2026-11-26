
#include <U8x8lib.h>
#include <string.h>

// ==========================================
// SSD1306 OLED 128x64
// Software I2C
//
// OLED SCL -> Arduino D6
// OLED SDA -> Arduino D5
// ==========================================

U8X8_SSD1306_128X64_NONAME_HW_I2C oled(
  U8X8_PIN_NONE
);

bool oledReady = false;


// ==========================================
// OLED 初始化
// ==========================================

bool initOLED() {

  // I2C 位址 0x3C
  // U8x8 使用 8-bit 位址
  oled.setI2CAddress(0x3C * 2);

  // 啟動 OLED
  oled.begin();

  // 開啟螢幕顯示
  oled.setPowerSave(0);

  // 螢幕文字顛倒
  oled.setFlipMode(1);
  
  // 設定文字字型
  oled.setFont(u8x8_font_chroma48medium8_r);

  // 清除畫面
  oled.clearDisplay();

  // 顯示標題
  oled.drawString(0, 0, "JETBOT DISTANCE");

  oledReady = true;

  Serial.println("OLED_INIT_SENT");

  return true;
}


// ==========================================
// 顯示單行距離
// ==========================================

void drawDistanceRow(
  byte row,
  const char* label,
  float distance
) {

  char line[17];

  // 先將整行填滿空白
  // 避免舊數字殘留
  memset(line, ' ', 16);
  line[16] = '\0';

  // 寫入感測器名稱
  for (byte i = 0; i < 6 && label[i]; i++) {
    line[i] = label[i];
  }

  // 測距無效
  if (distance < 0 || distance > 400) {

    memcpy(line + 7, "N/A", 3);

  } else {

    char number[10];

    // 浮點數轉字串
    // 保留小數點後一位
    dtostrf(distance, 5, 1, number);

    byte len = strlen(number);

    if (len > 5) {
      len = 5;
    }

    memcpy(line + 7, number, len);

    // 顯示單位 cm
    memcpy(line + 12, "cm", 2);
  }

  // 更新指定文字列
  oled.drawString(0, row, line);
}


// ==========================================
// 更新左右超音波測距
// ==========================================

void updateOLED(
  float leftDistance,
  float rightDistance
) {

  if (!oledReady) {
    return;
  }

  // 左側超音波
  drawDistanceRow(
    2,
    "LEFT :",
    leftDistance
  );

  // 右側超音波
  drawDistanceRow(
    4,
    "RIGHT:",
    rightDistance
  );
}
