import time
from typing import Optional, Dict, Any

import RPi.GPIO as GPIO

_BOARD_MAP = None

def _get_board_map():
    global _BOARD_MAP
    if _BOARD_MAP is None:
        import board
        _BOARD_MAP = {
            4: board.D4, 5: board.D5, 6: board.D6, 12: board.D12, 13: board.D13,
            16: board.D16, 17: board.D17, 18: board.D18, 19: board.D19, 20: board.D20,
            21: board.D21, 22: board.D22, 23: board.D23, 24: board.D24, 25: board.D25,
            26: board.D26, 27: board.D27,
        }
    return _BOARD_MAP

class HardwareManager:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.standalone = cfg.get("standalone", False)
        self.hold_time = cfg.get("hold_time", 0.5)
        self.multi_tap_window = cfg.get("multi_tap_window", 0.45)
        self.buzzer_enabled = cfg.get("buzzer_enabled", True) and cfg.get("has_active_buzzer", True)
        self.passive_buzzer_enabled = cfg.get("passive_buzzer_enabled", True) and cfg.get("has_passive_buzzer", True)
        self.alert_silenced = False
        self._last1 = self._last2 = ""
        self.lcd = None
        self.TOUCH_PIN = cfg.get("gpio_touch", 27)
        self.ACTIVE_BUZZER_PIN = cfg.get("gpio_active_buzzer", 6)
        self.PASSIVE_BUZZER_PIN = cfg.get("gpio_passive_buzzer", 16)

        if self.standalone:
            return
        try:
            GPIO.setmode(GPIO.BCM)
        except Exception:
            self.standalone = True
            return

        if cfg.get("has_active_buzzer", True):
            try:
                GPIO.setup(self.ACTIVE_BUZZER_PIN, GPIO.OUT)
                GPIO.output(self.ACTIVE_BUZZER_PIN, GPIO.LOW)
            except Exception:
                self.buzzer_enabled = False

        if cfg.get("has_passive_buzzer", True):
            try:
                GPIO.setup(self.PASSIVE_BUZZER_PIN, GPIO.OUT)
                GPIO.output(self.PASSIVE_BUZZER_PIN, GPIO.LOW)
            except Exception:
                self.passive_buzzer_enabled = False

        if cfg.get("has_touch", True):
            try:
                GPIO.setup(self.TOUCH_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            except Exception:
                pass

        if cfg.get("has_lcd", True):
            self._init_lcd(cfg)

    def _init_lcd(self, cfg):
        try:
            import digitalio
            import adafruit_character_lcd.character_lcd as character_lcd
            mode = cfg.get("lcd_mode", "parallel")
            if mode == "i2c":
                try:
                    import board, busio
                    from adafruit_character_lcd.character_lcd_i2c import Character_LCD_I2C
                    i2c = busio.I2C(board.SCL, board.SDA)
                    addr = int(str(cfg.get("lcd_i2c_addr", "0x27")), 0)
                    self.lcd = Character_LCD_I2C(i2c, 16, 2, address=addr)
                except Exception:
                    self.lcd = None
            else:
                bm = _get_board_map()
                rs = digitalio.DigitalInOut(bm.get(cfg.get("lcd_rs", 22)))
                en = digitalio.DigitalInOut(bm.get(cfg.get("lcd_en", 17)))
                d4 = digitalio.DigitalInOut(bm.get(cfg.get("lcd_d4", 25)))
                d5 = digitalio.DigitalInOut(bm.get(cfg.get("lcd_d5", 24)))
                d6 = digitalio.DigitalInOut(bm.get(cfg.get("lcd_d6", 23)))
                d7 = digitalio.DigitalInOut(bm.get(cfg.get("lcd_d7", 18)))
                self.lcd = character_lcd.Character_LCD_Mono(rs, en, d4, d5, d6, d7, 16, 2)
        except Exception:
            self.lcd = None

    def beep(self, duration=0.04, times=1):
        if not self.buzzer_enabled or self.standalone:
            return
        try:
            for _ in range(times):
                GPIO.output(self.ACTIVE_BUZZER_PIN, GPIO.HIGH)
                time.sleep(duration)
                GPIO.output(self.ACTIVE_BUZZER_PIN, GPIO.LOW)
                time.sleep(0.05)
        except Exception:
            pass

    def _tone(self, freq_approx_ms, duration):
        """Software square-wave on passive pin for a more audible tone."""
        if not self.passive_buzzer_enabled or self.standalone:
            return
        try:
            half = max(0.001, freq_approx_ms / 2000.0)
            end = time.time() + duration
            while time.time() < end:
                GPIO.output(self.PASSIVE_BUZZER_PIN, GPIO.HIGH)
                time.sleep(half)
                GPIO.output(self.PASSIVE_BUZZER_PIN, GPIO.LOW)
                time.sleep(half)
        except Exception:
            pass

    def alert_tone(self):
        """Clear 3-beep alarm pattern (not just clicks)."""
        if not self.passive_buzzer_enabled or self.alert_silenced or self.standalone:
            return
        # three rising tones
        for ms in (3.0, 2.2, 1.6):
            self._tone(ms, 0.18)
            time.sleep(0.08)
        time.sleep(0.15)
        self._tone(2.0, 0.35)

    def test_beep(self):
        self.beep(0.05, 2)
        time.sleep(0.2)
        self.alert_tone()

    def display(self, line1: str, line2: str = ""):
        l1 = f"{(line1 or '')[:16]:<16}"
        l2 = f"{(line2 or '')[:16]:<16}"
        if not self.lcd:
            self._last1, self._last2 = l1, l2
            return
        if l1 != self._last1 or l2 != self._last2:
            try:
                self.lcd.cursor_position(0, 0)
                self.lcd.message = f"{l1}\n{l2}"
            except Exception:
                pass
            self._last1, self._last2 = l1, l2

    def force_display(self, line1: str, line2: str = ""):
        l1 = f"{(line1 or '')[:16]:<16}"
        l2 = f"{(line2 or '')[:16]:<16}"
        if not self.lcd:
            self._last1, self._last2 = l1, l2
            return
        try:
            self.lcd.cursor_position(0, 0)
            self.lcd.message = f"{l1}\n{l2}"
        except Exception:
            pass
        self._last1, self._last2 = l1, l2

    def get_display_text(self) -> tuple:
        return self._last1.strip(), self._last2.strip()

    def read_gesture(self) -> Optional[str]:
        if self.standalone or not self.cfg.get("has_touch", True):
            return None
        try:
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
        except Exception:
            return None

    def cleanup(self):
        try:
            if self.lcd:
                self.lcd.clear()
        except Exception:
            pass
        try:
            GPIO.cleanup()
        except Exception:
            pass
