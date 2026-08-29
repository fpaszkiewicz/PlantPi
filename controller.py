import time
import subprocess
from datetime import datetime
from pathlib import Path

import board
import adafruit_dht
import gpiod

from RPLCD.i2c import CharLCD


# ---------- LCD ----------
lcd = CharLCD(
    i2c_expander="PCF8574",
    address=0x27,
    port=1,
    cols=16,
    rows=2
)

# ---------- DHT22 ----------
dht = adafruit_dht.DHT22(board.D4)

# ---------- KEYPAD ----------
ROWS = [5, 6]
COLS = [13, 19]

KEYS = [
    ["K1", "K4"],
    ["K2", "K3"],
]

gpio_request = gpiod.request_lines(
    "/dev/gpiochip0",
    consumer="plantpi-controller",
    config={
        5: gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT),
        6: gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT),
        13: gpiod.LineSettings(
            direction=gpiod.line.Direction.INPUT,
            bias=gpiod.line.Bias.PULL_UP,
        ),
        19: gpiod.LineSettings(
            direction=gpiod.line.Direction.INPUT,
            bias=gpiod.line.Bias.PULL_UP,
        ),
    },
)

# ---------- STATE ----------
screen = 0
screen_count = 5

temperature = None
humidity = None

soil_value = None      # placeholder until ADS1115
threshold = 35

pump_mode = "AUTO"
pump_state = "OFF"
pump_until = 0

status_message = "All OK"
last_key = None
last_lcd = ""


# ---------- HELPERS ----------
def lcd_print(line1, line2=""):
    global last_lcd

    line1 = line1[:16].ljust(16)
    line2 = line2[:16].ljust(16)
    content = line1 + line2

    if content == last_lcd:
        return

    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string(line1)
    lcd.cursor_pos = (1, 0)
    lcd.write_string(line2)

    last_lcd = content


def read_keypad():
    pressed = None

    for r, row_pin in enumerate(ROWS):
        gpio_request.set_value(row_pin, gpiod.line.Value.INACTIVE)

        for c, col_pin in enumerate(COLS):
            if gpio_request.get_value(col_pin) == gpiod.line.Value.INACTIVE:
                pressed = KEYS[r][c]

        gpio_request.set_value(row_pin, gpiod.line.Value.ACTIVE)

    return pressed


def read_dht():
    global temperature, humidity, status_message

    try:
        temperature = dht.temperature
        humidity = dht.humidity
        status_message = "All OK"
    except Exception:
        status_message = "DHT Error"


def trigger_fake_pump():
    global pump_state, pump_until, status_message

    pump_state = "ON"
    pump_until = time.time() + 3
    status_message = "Pump simulated"


def update_pump():
    global pump_state

    if pump_state == "ON" and time.time() >= pump_until:
        pump_state = "OFF"


def capture_photo():
    global status_message

    photos_dir = Path.home() / "plant_project" / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    filename = datetime.now().strftime("capture_%Y%m%d_%H%M%S.jpg")
    path = photos_dir / filename

    lcd_print("Capturing...", "")

    try:
        subprocess.run(
            ["rpicam-still", "-o", str(path), "--timeout", "1000"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        status_message = filename[:16]
        lcd_print("Photo saved", filename[:16])
        time.sleep(2)
    except Exception:
        status_message = "Camera Error"
        lcd_print("Camera Error", "")
        time.sleep(2)


def handle_key(key):
    global screen, threshold, pump_mode

    if key == "K1":
        screen = (screen + 1) % screen_count

    elif key == "K2":
        if screen == 1:
            threshold = min(100, threshold + 1)
        elif screen == 2:
            pump_mode = "MANUAL" if pump_mode == "AUTO" else "AUTO"

    elif key == "K3":
        if screen == 1:
            threshold = max(0, threshold - 1)
        elif screen == 2:
            pump_mode = "MANUAL" if pump_mode == "AUTO" else "AUTO"

    elif key == "K4":
        if screen == 2:
            trigger_fake_pump()
        elif screen == 3:
            capture_photo()


def render_screen():
    if screen == 0:
        if temperature is None or humidity is None:
            lcd_print("Temp: --.- C", "Air : --.- %")
        else:
            lcd_print(
                f"Temp: {temperature:4.1f} C",
                f"Air : {humidity:4.1f} %"
            )

    elif screen == 1:
        if soil_value is None:
            lcd_print("Soil: -- %", f"Thr : {threshold:3d} %")
        else:
            lcd_print(f"Soil: {soil_value:3d} %", f"Thr : {threshold:3d} %")

    elif screen == 2:
        if pump_state == "ON":
            remaining = max(0, int(pump_until - time.time()) + 1)
            lcd_print("Pump: ON", f"Time: {remaining}s")
        else:
            lcd_print(f"Pump: {pump_mode}", "Ready")

    elif screen == 3:
        lcd_print("Camera Ready", "K4 = Capture")

    elif screen == 4:
        lcd_print("PlantPi v1", status_message)


# ---------- MAIN ----------
try:
    lcd_print("PlantPi", "Starting...")
    time.sleep(1)

    last_sensor_read = 0

    while True:
        now = time.time()

        if now - last_sensor_read > 2:
            read_dht()
            last_sensor_read = now

        update_pump()

        key = read_keypad()

        if key and key != last_key:
            handle_key(key)

        last_key = key

        render_screen()
        time.sleep(0.05)

except KeyboardInterrupt:
    lcd.clear()

finally:
    gpio_request.release()
    lcd.clear()
