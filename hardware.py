import time
from typing import Optional

import RPi.GPIO as GPIO
import board
import digitalio
import adafruit_character_lcd.character_lcd as character_lcd
import adafruit_dht

LCD_RS, LCD_EN = board.D22, board.D17
LCD_D4, LCD_D5, LCD_D6, LCD_D7 = board.D25, board.D24, board.D23, board.D18
TOUCH_PIN = 27
DHT_PIN = board.D4
ACTIVE_BUZZER_PIN = 6      # clicks / short beeps
PASSIVE_BUZZER_PIN = 20    # alert tones

class HardwareManager:
    def __init__(self, hold_time=0.5, multi_tap_window=0.45,
                 buzzer_enabled=True, passive_buzzer_enabled=True):
        self.hold_time = hold_time
        self.multi_tap_window = multi_tap_window
        self.buzzer_enabled = buzzer_enabled
        self.passive_buzzer_enabled = passive_buzzer_enabled

        rs = digitalio.DigitalInOut(LCD_RS)
        en = digitalio.DigitalInOut(LCD_EN)
        d4 = digitalio.DigitalInOut(LCD_D4)
        d5 = digitalio.DigitalInOut(LCD_D5)
        d6 = digitalio.DigitalInOut(LCD_D6)
        d7 = digitalio.DigitalInOut(LCD_D7)
        self.lcd = character_lcd.Character_LCD_Mono(rs, en, d4, d5, d6, d7, 16, 2)

        self.dht = adafruit_dht.DHT11(DHT_PIN)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TOUCH_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(ACTIVE_BUZZER_PIN, GPIO.OUT)
        GPIO.output(ACTIVE_BUZZER_PIN, GPIO.LOW)
        GPIO.setup(PASSIVE_BUZZER_PIN, GPIO.OUT)
        GPIO.output(PASSIVE_BUZZER_PIN, GPIO.LOW)

        self._last1 = self._last2 = ""
        self._last_hum: Optional[float] = None
        self._alert_active = False

    def beep(self, duration=0.04, times=1):
        """Short click on active buzzer (GPIO 6)"""
        if not self.buzzer_enabled:
            return
        for _ in range(times):
            GPIO.output(ACTIVE_BUZZER_PIN, GPIO.HIGH)
            time.sleep(duration)
            GPIO.output(ACTIVE_BUZZER_PIN, GPIO.LOW)
            time.sleep(0.06)

    def alert_tone(self, duration=0.25, times=2):
        """Alert tone on passive buzzer (pin 20)"""
        if not self.passive_buzzer_enabled:
            return
        for _ in range(times):
            GPIO.output(PASSIVE_BUZZER_PIN, GPIO.HIGH)
            time.sleep(duration)
            GPIO.output(PASSIVE_BUZZER_PIN, GPIO.LOW)
            time.sleep(0.12)

    def display(self, line1: str, line2: str = ""):
        l1 = f"{(line1 or '')[:16]:<16}"
        l2 = f"{(line2 or '')[:16]:<16}"
        if l1 != self._last1 or l2 != self._last2:
            self.lcd.cursor_position(0, 0)
            self.lcd.message = f"{l1}\n{l2}"
            self._last1, self._last2 = l1, l2

    def force_display(self, line1: str, line2: str = ""):
        l1 = f"{(line1 or '')[:16]:<16}"
        l2 = f"{(line2 or '')[:16]:<16}"
        self.lcd.cursor_position(0, 0)
        self.lcd.message = f"{l1}\n{l2}"
        self._last1, self._last2 = l1, l2

    def get_display_text(self) -> tuple:
        return self._last1.strip(), self._last2.strip()

    def read_gesture(self) -> Optional[str]:
        if GPIO.input(TOUCH_PIN) != GPIO.HIGH:
            return None
        start = time.time()
        while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
            time.sleep(0.012)
            if time.time() - start >= self.hold_time:
                self.beep(0.11)
                while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                    time.sleep(0.012)
                return "HOLD"
        self.beep(0.03)
        count = 1
        t0 = time.time()
        while time.time() - t0 < self.multi_tap_window:
            if GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                count += 1
                self.beep(0.03)
                while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                    time.sleep(0.012)
                if count >= 3:
                    break
            time.sleep(0.012)
        return "TRIPLE" if count >= 3 else "DOUBLE" if count == 2 else "SINGLE"

    def read_humidity(self) -> Optional[float]:
        try:
            h = self.dht.humidity
            if h is not None:
                self._last_hum = h
                return h
        except RuntimeError:
            pass
        return self._last_hum

    def cleanup(self):
        try:
            self.lcd.clear()
        except Exception:
            pass
        GPIO.cleanup()
