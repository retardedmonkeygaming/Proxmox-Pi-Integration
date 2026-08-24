import time
from typing import Optional, Dict, Any

import RPi.GPIO as GPIO
import board
import digitalio
import adafruit_character_lcd.character_lcd as character_lcd
import adafruit_dht

# Map BCM number -> board pin object for digitalio
_BOARD_MAP = {
    4: board.D4, 17: board.D17, 18: board.D18, 22: board.D22,
    23: board.D23, 24: board.D24, 25: board.D25, 27: board.D27,
}

class HardwareManager:
    def __init__(self, cfg: Dict[str, Any]):
        self.hold_time = cfg.get("hold_time", 0.5)
        self.multi_tap_window = cfg.get("multi_tap_window", 0.45)
        self.buzzer_enabled = cfg.get("buzzer_enabled", True)
        self.passive_buzzer_enabled = cfg.get("passive_buzzer_enabled", True)

        touch = cfg.get("gpio_touch", 27)
        active = cfg.get("gpio_active_buzzer", 6)
        passive = cfg.get("gpio_passive_buzzer", 16)
        dht_pin = cfg.get("gpio_dht", 4)

        self.TOUCH_PIN = touch
        self.ACTIVE_BUZZER_PIN = active
        self.PASSIVE_BUZZER_PIN = passive

        # LCD – non-I2C 4-bit for now
        rs_n = cfg.get("lcd_rs", 22)
        en_n = cfg.get("lcd_en", 17)
        d4_n = cfg.get("lcd_d4", 25)
        d5_n = cfg.get("lcd_d5", 24)
        d6_n = cfg.get("lcd_d6", 23)
        d7_n = cfg.get("lcd_d7", 18)

        rs = digitalio.DigitalInOut(_BOARD_MAP.get(rs_n, board.D22))
        en = digitalio.DigitalInOut(_BOARD_MAP.get(en_n, board.D17))
        d4 = digitalio.DigitalInOut(_BOARD_MAP.get(d4_n, board.D25))
        d5 = digitalio.DigitalInOut(_BOARD_MAP.get(d5_n, board.D24))
        d6 = digitalio.DigitalInOut(_BOARD_MAP.get(d6_n, board.D23))
        d7 = digitalio.DigitalInOut(_BOARD_MAP.get(d7_n, board.D18))
        self.lcd = character_lcd.Character_LCD_Mono(rs, en, d4, d5, d6, d7, 16, 2)

        self.dht = adafruit_dht.DHT11(_BOARD_MAP.get(dht_pin, board.D4))

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.TOUCH_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(self.ACTIVE_BUZZER_PIN, GPIO.OUT)
        GPIO.output(self.ACTIVE_BUZZER_PIN, GPIO.LOW)
        GPIO.setup(self.PASSIVE_BUZZER_PIN, GPIO.OUT)
        GPIO.output(self.PASSIVE_BUZZER_PIN, GPIO.LOW)

        self._last1 = self._last2 = ""
        self._last_hum: Optional[float] = None
        self.alert_silenced = False

    def beep(self, duration=0.04, times=1):
        if not self.buzzer_enabled:
            return
        for _ in range(times):
            GPIO.output(self.ACTIVE_BUZZER_PIN, GPIO.HIGH)
            time.sleep(duration)
            GPIO.output(self.ACTIVE_BUZZER_PIN, GPIO.LOW)
            time.sleep(0.06)

    def alert_tone(self, pattern="error"):
        """Specific generic error tone on passive buzzer (pin 16)."""
        if not self.passive_buzzer_enabled or self.alert_silenced:
            return
        # short-long-short pattern
        for d in (0.12, 0.08, 0.28, 0.08, 0.12):
            GPIO.output(self.PASSIVE_BUZZER_PIN, GPIO.HIGH)
            time.sleep(d)
            GPIO.output(self.PASSIVE_BUZZER_PIN, GPIO.LOW)
            time.sleep(0.07)

    def test_beep(self):
        self.beep(0.06, 2)
        if self.passive_buzzer_enabled:
            time.sleep(0.15)
            self.alert_tone()

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
        if GPIO.input(self.TOUCH_PIN) != GPIO.HIGH:
            return None
        start = time.time()
        while GPIO.input(self.TOUCH_PIN) == GPIO.HIGH:
            time.sleep(0.012)
            if time.time() - start >= self.hold_time:
                self.beep(0.11)
                while GPIO.input(self.TOUCH_PIN) == GPIO.HIGH:
                    time.sleep(0.012)
                return "HOLD"
        self.beep(0.03)
        count = 1
        t0 = time.time()
        while time.time() - t0 < self.multi_tap_window:
            if GPIO.input(self.TOUCH_PIN) == GPIO.HIGH:
                count += 1
                self.beep(0.03)
                while GPIO.input(self.TOUCH_PIN) == GPIO.HIGH:
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
