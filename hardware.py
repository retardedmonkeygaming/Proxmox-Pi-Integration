import time
import RPi.GPIO as GPIO
import board
import digitalio
import adafruit_character_lcd.character_lcd as character_lcd
import adafruit_dht

LCD_RS, LCD_EN = board.D22, board.D17
LCD_D4, LCD_D5, LCD_D6, LCD_D7 = board.D25, board.D24, board.D23, board.D18
TOUCH_PIN, DHT_PIN = 27, board.D4
ACTIVE_BUZZER = 6

class HardwareManager:
    def __init__(self, hold_time=0.5, multi_tap_window=0.45):
        self.hold_time = hold_time
        self.multi_tap_window = multi_tap_window

        rs, en = digitalio.DigitalInOut(LCD_RS), digitalio.DigitalInOut(LCD_EN)
        d4, d5 = digitalio.DigitalInOut(LCD_D4), digitalio.DigitalInOut(LCD_D5)
        d6, d7 = digitalio.DigitalInOut(LCD_D6), digitalio.DigitalInOut(LCD_D7)
        self.lcd = character_lcd.Character_LCD_Mono(rs, en, d4, d5, d6, d7, 16, 2)

        self.dht = adafruit_dht.DHT11(DHT_PIN)

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TOUCH_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(ACTIVE_BUZZER, GPIO.OUT)
        GPIO.output(ACTIVE_BUZZER, GPIO.LOW)

        self.last_line1 = ""
        self.last_line2 = ""

    def beep(self, duration=0.04):
        GPIO.output(ACTIVE_BUZZER, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(ACTIVE_BUZZER, GPIO.LOW)

    def display_text(self, line1, line2=""):
        """Updates display WITHOUT flickering by writing padded fixed-length buffers."""
        line1_padded = line1[:16].ljust(16)
        line2_padded = line2[:16].ljust(16)

        if line1_padded != self.last_line1 or line2_padded != self.last_line2:
            self.lcd.cursor_position(0, 0)
            self.lcd.message = f"{line1_padded}\n{line2_padded}"
            self.last_line1 = line1_padded
            self.last_line2 = line2_padded

    def read_touch_gesture(self):
        """Detects SINGLE, DOUBLE, TRIPLE, or HOLD gesture based on config thresholds."""
        if GPIO.input(TOUCH_PIN) == GPIO.HIGH:
            press_start = time.time()
            
            # Check for HOLD
            while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                time.sleep(0.02)
                if time.time() - press_start >= self.hold_time:
                    self.beep(0.12)
                    while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                        time.sleep(0.02)
                    return 'HOLD'

            self.beep(0.03)
            tap_count = 1
            first_tap_time = time.time()

            # Wait inside multi-tap window for additional taps
            while time.time() - first_tap_time < self.multi_tap_window:
                if GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                    tap_count += 1
                    self.beep(0.03)
                    # Wait for finger release
                    while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                        time.sleep(0.02)
                    if tap_count == 3:
                        break
                time.sleep(0.02)

            if tap_count >= 3:
                return 'TRIPLE'
            elif tap_count == 2:
                return 'DOUBLE'
            else:
                return 'SINGLE'

        return None

    def read_dht(self):
        try:
            return self.dht.temperature, self.dht.humidity
        except RuntimeError:
            return None, None

    def cleanup(self):
        self.lcd.clear()
        GPIO.cleanup()