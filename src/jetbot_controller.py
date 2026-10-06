import time

# 切換這一行就能在模擬/真實之間切換
USE_MOCK = True

if USE_MOCK:
    from mock_arduino import MockSerial as Serial
else:
    from serial import Serial

class JetBotController:
    def __init__(self, port='/dev/cu.usbmodem14101', baudrate=9600):
        self.ser = Serial(port, baudrate, timeout=2)
        time.sleep(0.1)
        ready = self.ser.readline().decode().strip()
        print(f"狀態: {ready}")

    def send(self, command):
        self.ser.write((command + '\n').encode())
        time.sleep(0.1)
        response = self.ser.readline().decode().strip()
        print(f"  → {command}: {response}")
        return response

    def run_demo(self):
        self.send("LED_ON")
        self.send("FORWARD")
        time.sleep(1)
        self.send("STOP")
        self.send("LED_OFF")

    def close(self):
        self.ser.close()


if __name__ == "__main__":
    bot = JetBotController()
    bot.run_demo()
    bot.close()