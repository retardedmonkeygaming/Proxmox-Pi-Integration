import time
from typing import Optional, Tuple

import RPi.GPIO as GPIO
import board
import digitalio
import adafruit_character_lcd.character_lcd as character_lcd
import adafruit_dht

# Pin definitions (BCM)
LCD_RS, LCD_EN = board.D22, board.D17
LCD_D4, LCD_D5, LCD_D6, LCD_D7 = board.D25, board.D24, board.D23, board.D18
TOUCH_PIN = 27
DHT_PIN = board.D4
ACTIVE_BUZZER = 6

class HardwareManager:
    def __init__(self, hold_time: float = 0.5, multi_tap_window: float = 0.45, buzzer_enabled: bool = True):
        self.hold_time = hold_time
        self.multi_tap_window = multi_tap_window
        self.buzzer_enabled = buzzer_enabled

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
        GPIO.setup(ACTIVE_BUZZER, GPIO.OUT)
        GPIO.output(ACTIVE_BUZZER, GPIO.LOW)

        self.last_line1 = ""
        self.last_line2 = ""
        self._last_temp: Optional[float] = None
        self._last_hum: Optional[float] = None

    def beep(self, duration: float = 0.04) -> None:
        if not self.buzzer_enabled:
            return
        GPIO.output(ACTIVE_BUZZER, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(ACTIVE_BUZZER, GPIO.LOW)

    def display_text(self, line1: str, line2: str = "") -> None:
        """Flicker-free differential update. Both lines forced to exactly 16 chars."""
        line1_padded = (line1 or "")[:16].ljust(16)
        line2_padded = (line2 or "")[:16].ljust(16)

        if line1_padded != self.last_line1 or line2_padded != self.last_line2:
            self.lcd.cursor_position(0, 0)
            self.lcd.message = f"{line1_padded}\n{line2_padded}"
            self.last_line1 = line1_padded
            self.last_line2 = line2_padded

    def read_touch_gesture(self) -> Optional[str]:
        """Returns SINGLE, DOUBLE, TRIPLE, HOLD or None."""
        if GPIO.input(TOUCH_PIN) != GPIO.HIGH:
            return None

        press_start = time.time()
        while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
            time.sleep(0.015)
            if time.time() - press_start >= self.hold_time:
                self.beep(0.12)
                while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                    time.sleep(0.015)
                return "HOLD"

        self.beep(0.03)
        tap_count = 1
        first_tap_time = time.time()

        while time.time() - first_tap_time < self.multi_tap_window:
            if GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                tap_count += 1
                self.beep(0.03)
                while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                    time.sleep(0.015)
                if tap_count >= 3:
                    break
            time.sleep(0.015)

        if tap_count >= 3:
            return "TRIPLE"
        if tap_count == 2:
            return "DOUBLE"
        return "SINGLE"

    def read_dht(self) -> Tuple[Optional[float], Optional[float]]:
        try:
            t = self.dht.temperature
            h = self.dht.humidity
            if t is not None and h is not None:
                self._last_temp = t
                self._last_hum = h
                return t, h
        except RuntimeError:
            pass
        # Return last good values so LCD never shows N/A after first success
        return self._last_temp, self._last_hum

    def cleanup(self) -> None:
        try:
            self.lcd.clear()
        except Exception:
            pass
        GPIO.cleanup()