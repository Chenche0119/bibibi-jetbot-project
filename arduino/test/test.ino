String inputString = "";

void setup() {
  Serial.begin(9600);
  pinMode(10, OUTPUT);
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
    digitalWrite(10, HIGH);
    Serial.println("LED ON");
  } else if (cmd == "LED_OFF") {
    digitalWrite(10, LOW);
    Serial.println("LED OFF");
  } else {
    Serial.println("Unknown: " + cmd);
  }
}