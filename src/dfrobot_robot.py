# -*- coding:utf-8 -*-
"""
dfrobot_robot.py

給 Jetson Nano + DFRobot DC Motor Driver HAT (DFR0592, I2C 0x10) 使用的
JetBot 相容驅動。

包含兩部分:
  1. DFRobot_DC_Motor_IIC  -- DFRobot 官方驅動 (精簡自 DFRobot_RaspberryPi_Motor)
  2. Robot / Motor         -- 與 NVIDIA jetbot.Robot 相同介面的包裝層

用法 (取代 notebook 裡的 from jetbot import Robot):

    from dfrobot_robot import Robot
    robot = Robot()
    robot.forward(0.3)
    robot.stop()

原始驅動來源: https://github.com/DFRobot/DFRobot_RaspberryPi_Motor  (MIT License)
"""

import time

try:
    import smbus
except ImportError:                      # 有些映像檔叫 smbus2
    import smbus2 as smbus

try:
    import traitlets
    from traitlets.config.configurable import SingletonConfigurable
    _HAS_TRAITLETS = True
except ImportError:                      # 沒有 traitlets 也能跑，只是不能綁 widget
    _HAS_TRAITLETS = False
    class SingletonConfigurable(object):
        pass


# ---------------------------------------------------------------------------
# 1. DFRobot 官方驅動
# ---------------------------------------------------------------------------

class DFRobot_DC_Motor(object):

    _MOTOR_COUNT = 2

    _REG_SLAVE_ADDR = 0x00
    _REG_PID = 0x01
    _REG_PVD = 0x02
    _REG_CTRL_MODE = 0x03
    _REG_ENCODER1_EN = 0x04
    _REG_ENCODER1_SPEED = 0x05
    _REG_ENCODER1_REDUCTION_RATIO = 0x07
    _REG_MOTOR_PWM = 0x0e
    _REG_MOTOR1_ORIENTATION = 0x0f

    _REG_DEF_PID = 0xdf

    M1 = 0x01
    M2 = 0x02

    STA_OK = 0x00
    STA_ERR = 0x01
    STA_ERR_DEVICE_NOT_DETECTED = 0x02
    STA_ERR_SOFT_VERSION = 0x03
    STA_ERR_PARAMETER = 0x04

    last_operate_status = STA_OK

    _control_mode_dc_motor = 0x00

    CW = 0x01
    CCW = 0x02
    STOP = 0x05
    ALL = 0xffffffff

    def _write_bytes(self, reg, buf):
        raise NotImplementedError

    def _read_bytes(self, reg, length):
        raise NotImplementedError

    def __init__(self, addr):
        self._addr = addr

    def begin(self):
        pid = self._read_bytes(self._REG_PID, 1)
        self._read_bytes(self._REG_PVD, 1)
        if self.last_operate_status == self.STA_OK:
            if pid[0] != self._REG_DEF_PID:
                self.last_operate_status = self.STA_ERR_DEVICE_NOT_DETECTED
            else:
                self._set_control_mode(self._control_mode_dc_motor)
                self.motor_stop(self.ALL)
                self.set_encoder_disable(self.ALL)
        return self.last_operate_status

    def _set_control_mode(self, mode):
        self._write_bytes(self._REG_CTRL_MODE, [mode])

    def _parse_id(self, motor_id):
        if motor_id == self.ALL:
            return range(1, self._MOTOR_COUNT + 1)
        for i in motor_id:
            if i < 1 or i > self._MOTOR_COUNT:
                self.last_operate_status = self.STA_ERR_PARAMETER
                return []
        return motor_id

    def set_encoder_enable(self, motor_id):
        for i in self._parse_id(motor_id):
            self._write_bytes(self._REG_ENCODER1_EN + 5 * (i - 1), [0x01])

    def set_encoder_disable(self, motor_id):
        for i in self._parse_id(motor_id):
            self._write_bytes(self._REG_ENCODER1_EN + 5 * (i - 1), [0x00])

    def set_encoder_reduction_ratio(self, motor_id, reduction_ratio):
        reduction_ratio = int(reduction_ratio)
        if reduction_ratio < 1 or reduction_ratio > 2000:
            self.last_operate_status = self.STA_ERR_PARAMETER
            return
        for i in self._parse_id(motor_id):
            self._write_bytes(self._REG_ENCODER1_REDUCTION_RATIO + 5 * (i - 1),
                              [reduction_ratio >> 8, reduction_ratio & 0xff])

    def get_encoder_speed(self, motor_id):
        speeds = []
        for i in self._parse_id(motor_id):
            rslt = self._read_bytes(self._REG_ENCODER1_SPEED + 5 * (i - 1), 2)
            s = (rslt[0] << 8) | rslt[1]
            if s & 0x8000:
                s = -(0x10000 - s)
            speeds.append(s)
        return speeds

    def set_moter_pwm_frequency(self, frequency):
        if frequency < 100 or frequency > 12750:
            self.last_operate_status = self.STA_ERR_PARAMETER
            return
        self._write_bytes(self._REG_MOTOR_PWM, [int(frequency / 50)])
        time.sleep(0.1)

    def motor_movement(self, motor_id, orientation, speed):
        if orientation not in (self.CW, self.CCW):
            self.last_operate_status = self.STA_ERR_PARAMETER
            return
        if speed > 100.0 or speed < 0.0:
            self.last_operate_status = self.STA_ERR_PARAMETER
            return
        for i in self._parse_id(motor_id):
            reg = self._REG_MOTOR1_ORIENTATION + (i - 1) * 3
            self._write_bytes(reg, [orientation])
            self._write_bytes(reg + 1, [int(speed), int((speed * 10) % 10)])

    def motor_stop(self, motor_id):
        for i in self._parse_id(motor_id):
            self._write_bytes(self._REG_MOTOR1_ORIENTATION + 3 * (i - 1), [self.STOP])

    def detecte(self):
        found = []
        back = self._addr
        for i in range(1, 127):
            self._addr = i
            if self.begin() == self.STA_OK:
                found.append(hex(i))
        self._addr = back
        self.last_operate_status = self.STA_OK
        return found


class DFRobot_DC_Motor_IIC(DFRobot_DC_Motor):

    def __init__(self, bus_id, addr):
        self._bus = smbus.SMBus(bus_id)
        DFRobot_DC_Motor.__init__(self, addr)

    def _write_bytes(self, reg, buf):
        self.last_operate_status = self.STA_ERR_DEVICE_NOT_DETECTED
        try:
            self._bus.write_i2c_block_data(self._addr, reg, buf)
            self.last_operate_status = self.STA_OK
        except Exception:
            pass

    def _read_bytes(self, reg, length):
        self.last_operate_status = self.STA_ERR_DEVICE_NOT_DETECTED
        try:
            rslt = self._bus.read_i2c_block_data(self._addr, reg, length)
            self.last_operate_status = self.STA_OK
            return rslt
        except Exception:
            return [0] * length


# ---------------------------------------------------------------------------
# 2. JetBot 相容包裝層
# ---------------------------------------------------------------------------

if _HAS_TRAITLETS:

    class Motor(traitlets.HasTraits):

        value = traitlets.Float()
        alpha = traitlets.Float(default_value=1.0)

        def __init__(self, board, channel, alpha=1.0, *args, **kwargs):
            super(Motor, self).__init__(*args, **kwargs)
            self._board = board
            self._channel = channel   # board.M1 or board.M2
            self.alpha = alpha
            self._write_value(0.0)

        @traitlets.observe('value')
        def _observe_value(self, change):
            self._write_value(change['new'])

        def _write_value(self, value):
            v = float(value) * float(self.alpha)
            v = max(-1.0, min(1.0, v))
            duty = abs(v) * 100.0
            if duty < 1.0:
                self._board.motor_stop([self._channel])
                return
            orientation = self._board.CW if v > 0 else self._board.CCW
            self._board.motor_movement([self._channel], orientation, duty)

    _Base = SingletonConfigurable

else:

    class Motor(object):

        def __init__(self, board, channel, alpha=1.0):
            self._board = board
            self._channel = channel
            self.alpha = alpha
            self._value = 0.0

        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, v):
            self._value = v
            vv = max(-1.0, min(1.0, float(v) * float(self.alpha)))
            duty = abs(vv) * 100.0
            if duty < 1.0:
                self._board.motor_stop([self._channel])
                return
            orientation = self._board.CW if vv > 0 else self._board.CCW
            self._board.motor_movement([self._channel], orientation, duty)

    _Base = object


class Robot(_Base):
    """與 jetbot.Robot 相同介面。

    參數:
        i2c_bus         Jetson Nano 40-pin 是 bus 1
        driver_address  DFR0592 預設 0x10
        left_alpha / right_alpha  若某側輪子轉反了，把該側改成 -1.0
    """

    def __init__(self, i2c_bus=1, driver_address=0x10,
                 left_alpha=1.0, right_alpha=1.0,
                 pwm_frequency=1000, *args, **kwargs):
        super(Robot, self).__init__(*args, **kwargs)

        self.board = DFRobot_DC_Motor_IIC(i2c_bus, driver_address)

        retries = 0
        while self.board.begin() != self.board.STA_OK:
            retries += 1
            if retries > 5:
                raise RuntimeError(
                    "找不到 DFRobot 馬達板 (bus=%d, addr=0x%02x)。"
                    "請確認 i2cdetect -y -r %d 能看到該位址，且馬達板已外部供電。"
                    % (i2c_bus, driver_address, i2c_bus))
            time.sleep(1)

        self.board.set_encoder_disable(self.board.ALL)
        self.board.set_moter_pwm_frequency(pwm_frequency)

        # M1 = 左輪, M2 = 右輪。若接反，把下面兩行對調即可。
        self.left_motor = Motor(self.board, self.board.M1, alpha=left_alpha)
        self.right_motor = Motor(self.board, self.board.M2, alpha=right_alpha)

    def set_motors(self, left_speed, right_speed):
        self.left_motor.value = left_speed
        self.right_motor.value = right_speed

    def forward(self, speed=1.0, duration=None):
        self.set_motors(speed, speed)
        if duration is not None:
            time.sleep(duration)
            self.stop()

    def backward(self, speed=1.0, duration=None):
        self.set_motors(-speed, -speed)
        if duration is not None:
            time.sleep(duration)
            self.stop()

    def left(self, speed=1.0, duration=None):
        self.set_motors(-speed, speed)
        if duration is not None:
            time.sleep(duration)
            self.stop()

    def right(self, speed=1.0, duration=None):
        self.set_motors(speed, -speed)
        if duration is not None:
            time.sleep(duration)
            self.stop()

    def stop(self):
        self.set_motors(0.0, 0.0)


if __name__ == '__main__':
    robot = Robot()
    print("Robot 初始化成功，開始測試")
    robot.forward(0.3, duration=1.0)
    robot.backward(0.3, duration=1.0)
    robot.left(0.3, duration=1.0)
    robot.right(0.3, duration=1.0)
    robot.stop()
    print("測試完成")
