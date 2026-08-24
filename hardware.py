import time
import RPi.GPIO as GPIO
import board
import digitalio
import adafruit_character_lcd.character_lcd as character_lcd
import adafruit_dht

# GPIO Pin Assignment
LCD_RS = board.D22
LCD_EN = board.D17
LCD_D4 = board.D25
LCD_D5 = board.D24
LCD_D6 = board.D23
LCD_D7 = board.D18

TOUCH_PIN = 27
DHT_PIN = board.D4
ACTIVE_BUZZER = 6
PASSIVE_BUZZER = 16

class HardwareManager:
    def __init__(self):
        # LCD Setup
        rs = digitalio.DigitalInOut(LCD_RS)
        en = digitalio.DigitalInOut(LCD_EN)
        d4 = digitalio.DigitalInOut(LCD_D4)
        d5 = digitalio.DigitalInOut(LCD_D5)
        d6 = digitalio.DigitalInOut(LCD_D6)
        d7 = digitalio.DigitalInOut(LCD_D7)
        self.lcd = character_lcd.Character_LCD_Mono(rs, en, d4, d5, d6, d7, 16, 2)

        # Sensors
        self.dht = adafruit_dht.DHT11(DHT_PIN)

        # GPIO Configuration
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TOUCH_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(ACTIVE_BUZZER, GPIO.OUT)
        GPIO.setup(PASSIVE_BUZZER, GPIO.OUT)
        GPIO.output(ACTIVE_BUZZER, GPIO.LOW)
        GPIO.output(PASSIVE_BUZZER, GPIO.LOW)

    def beep_short(self):
        GPIO.output(ACTIVE_BUZZER, GPIO.HIGH)
        time.sleep(0.04)
        GPIO.output(ACTIVE_BUZZER, GPIO.LOW)

    def beep_long(self):
        GPIO.output(ACTIVE_BUZZER, GPIO.HIGH)
        time.sleep(0.18)
        GPIO.output(ACTIVE_BUZZER, GPIO.LOW)

    def read_touch_event(self):
        """Returns 'SHORT' for tap, 'LONG' for 1.2s press, or None."""
        if GPIO.input(TOUCH_PIN) == GPIO.HIGH:
            start_time = time.time()
            while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                time.sleep(0.05)
                if time.time() - start_time >= 1.2:
                    self.beep_long()
                    # Wait for release
                    while GPIO.input(TOUCH_PIN) == GPIO.HIGH:
                        time.sleep(0.05)
                    return 'LONG'
            self.beep_short()
            return 'SHORT'
        return None

    def read_dht(self):
        try:
            return self.dht.temperature, self.dht.humidity
        except RuntimeError:
            return None, None

    def display_text(self, line1, line2=""):
        self.lcd.clear()
        line1 = line1[:16].ljust(16)
        line2 = line2[:16].ljust(16)
        self.lcd.message = f"{line1}\n{line2}"

    def cleanup(self):
        self.lcd.clear()
        GPIO.cleanup()