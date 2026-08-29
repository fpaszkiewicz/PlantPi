import csv
import json
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from datetime import datetime, timedelta

import board
import adafruit_dht
import gpiod
from PIL import Image
from RPLCD.i2c import CharLCD


BASE_DIR = Path.home() / "plant_project"
PHOTOS_DIR = BASE_DIR / "photos"
LOGS_DIR = BASE_DIR / "logs"
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

PHOTOS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


DEFAULT_CONFIG = {
    "log_interval_seconds": 60,
    "photo_interval_seconds": 600,
    "photo_retention_hours": 24,
    "log_retention_days": 7,
    "pump_flow_ml_per_second": 19.44,
    "tank_capacity_ml": 1000,
    "water_warning_threshold_ml": 250,
    "water_stop_threshold_ml": 100
}

DEFAULT_STATE = {
    "current_water_ml": 1000,
    "pump_mode": "AUTO",
    "soil_threshold_percent": 35,
    "last_photo_path": None,
    "last_light_percent": None
}


def load_json(path, default):
    if not path.exists():
        path.write_text(json.dumps(default, indent=2))
        return default.copy()

    with open(path, "r") as f:
        data = json.load(f)

    changed = False
    for key, value in default.items():
        if key not in data:
            data[key] = value
            changed = True

    if changed:
        path.write_text(json.dumps(data, indent=2))

    return data


def save_state():
    STATE_PATH.write_text(json.dumps(state, indent=2))


config = load_json(CONFIG_PATH, DEFAULT_CONFIG)
state = load_json(STATE_PATH, DEFAULT_STATE)


lcd = CharLCD(
    i2c_expander="PCF8574",
    address=0x27,
    port=1,
    cols=16,
    rows=2
)

lcd.cursor_mode = "hide"

dht = adafruit_dht.DHT22(board.D4)


ROWS = [5, 6]
COLS = [13, 19]

KEYS = [
    ["K1", "K3"],
    ["K2", "K4"],
]

gpio_request = gpiod.request_lines(
    "/dev/gpiochip0",
    consumer="plantpi-controller-v2",
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


screen = 0
screen_count = 6

temperature = None
humidity = None
soil_value = None

pump_state = "OFF"
pump_until = 0
pump_today_seconds = 0

status_message = "All OK"
last_key = None
last_lcd = ""


def now_csv():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_file():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def current_minute_key():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def current_photo_slot():
    now = datetime.now()
    rounded_minute = (now.minute // 10) * 10
    return now.strftime(f"%Y-%m-%d %H:{rounded_minute:02d}")


def current_day_key():
    return datetime.now().strftime("%Y-%m-%d")


def current_minute_key():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def current_photo_slot():
    now = datetime.now()
    rounded_minute = (now.minute // 10) * 10
    return now.strftime(f"%Y-%m-%d %H:{rounded_minute:02d}")


def current_day_key():
    return datetime.now().strftime("%Y-%m-%d")


last_sensor_read = 0
last_logged_minute = None
last_photo_slot = None
last_report_day = current_day_key()
last_cleanup_time = 0


def lcd_print(line1, line2=""):
    global last_lcd

    line1 = str(line1)[:16].ljust(16)
    line2 = str(line2)[:16].ljust(16)
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


def analyze_light(photo_path):
    try:
        with Image.open(photo_path) as img:
            gray = img.convert("L")
            pixels = list(gray.getdata())
            avg = sum(pixels) / len(pixels)
            return round((avg / 255) * 100, 1)
    except Exception:
        return None


def capture_photo():
    global status_message

    filename = f"photo_{now_file()}.jpg"
    path = PHOTOS_DIR / filename

    lcd_print("Capturing...", "")

    try:
        subprocess.run(
            ["rpicam-still", "-o", str(path), "--timeout", "1000"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        light = analyze_light(path)

        state["last_photo_path"] = str(path)
        state["last_light_percent"] = light
        save_state()

        if light is None:
            status_message = "Light Error"
        else:
            status_message = f"Light {light:.1f}%"

        lcd_print("Photo saved", f"Light {light:.1f}%" if light is not None else "Light error")
        time.sleep(2)

        return str(path), light

    except Exception:
        status_message = "Camera Error"
        lcd_print("Camera Error", "")
        time.sleep(2)
        return None, None


def trigger_fake_pump(seconds=3):
    global pump_state, pump_until, pump_today_seconds, status_message

    if state["current_water_ml"] <= config["water_stop_threshold_ml"]:
        status_message = "Water too low"
        lcd_print("PUMP BLOCKED", "LOW WATER")
        time.sleep(2)
        return

    pump_state = "ON"
    pump_until = time.time() + seconds
    pump_today_seconds += seconds

    used = seconds * config["pump_flow_ml_per_second"]
    state["current_water_ml"] = max(0, state["current_water_ml"] - used)
    save_state()

    status_message = "Pump simulated"


def update_pump():
    global pump_state

    if pump_state == "ON" and time.time() >= pump_until:
        pump_state = "OFF"


def get_log_path():
    date = datetime.now().strftime("%Y-%m-%d")
    return LOGS_DIR / f"log_{date}.csv"


def append_log(photo_path=None, light_percent=None):
    log_path = get_log_path()
    file_exists = log_path.exists()

    with open(log_path, "a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "timestamp",
                "temperature_c",
                "air_humidity_percent",
                "soil_moisture_percent",
                "soil_threshold_percent",
                "light_percent",
                "pump_state",
                "pump_mode",
                "current_water_ml",
                "pump_today_seconds",
                "estimated_water_used_today_ml",
                "photo_path"
            ])

        estimated_water_used = pump_today_seconds * config["pump_flow_ml_per_second"]

        writer.writerow([
            now_csv(),
            temperature,
            humidity,
            soil_value,
            state["soil_threshold_percent"],
            light_percent if light_percent is not None else state["last_light_percent"],
            pump_state,
            state["pump_mode"],
            round(state["current_water_ml"], 1),
            pump_today_seconds,
            round(estimated_water_used, 1),
            photo_path if photo_path else state["last_photo_path"]
        ])


def cleanup_old_files():
    now = datetime.now()

    log_cutoff = now - timedelta(days=config["log_retention_days"])
    for file in LOGS_DIR.glob("*.csv"):
        try:
            modified = datetime.fromtimestamp(file.stat().st_mtime)
            if modified < log_cutoff:
                file.unlink()
        except Exception:
            pass


def handle_key(key):
    global screen

    if key == "K1":
        screen = (screen + 1) % screen_count

    elif key == "K2":
        if screen == 1:
            state["soil_threshold_percent"] = min(100, state["soil_threshold_percent"] + 1)
            save_state()
        elif screen == 2:
            state["pump_mode"] = "MANUAL" if state["pump_mode"] == "AUTO" else "AUTO"
            save_state()

    elif key == "K4":
        if screen == 1:
            state["soil_threshold_percent"] = max(0, state["soil_threshold_percent"] - 1)
            save_state()
        elif screen == 2:
            state["pump_mode"] = "MANUAL" if state["pump_mode"] == "AUTO" else "AUTO"
            save_state()

    elif key == "K3":
        if screen == 2:
            trigger_fake_pump()
        elif screen == 3:
            photo_path, light = capture_photo()
            append_log(photo_path, light)
        elif screen == 4:
            state["current_water_ml"] = config["tank_capacity_ml"]
            save_state()
            lcd_print("Tank refilled", f"{state['current_water_ml']:.0f} ml")
            time.sleep(2)


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
            lcd_print("Soil: -- %", f"Thr : {state['soil_threshold_percent']:3d} %")
        else:
            lcd_print(f"Soil: {soil_value:3d} %", f"Thr : {state['soil_threshold_percent']:3d} %")

    elif screen == 2:
        if pump_state == "ON":
            remaining = max(0, int(pump_until - time.time()) + 1)
            lcd_print("Pump: ON", f"Time: {remaining}s")
        else:
            lcd_print(f"Pump: {state['pump_mode']}", "K3 = Trigger")

    elif screen == 3:
        light = state["last_light_percent"]
        if light is None:
            lcd_print("Camera Ready", "K3 = Capture")
        else:
            lcd_print("Camera Ready", f"Light: {light:.1f}%")

    elif screen == 4:
        water = state["current_water_ml"]
        if water <= config["water_stop_threshold_ml"]:
            lcd_print("Water: STOP", f"{water:.0f} ml K3=Fill")
        elif water <= config["water_warning_threshold_ml"]:
            lcd_print("Water: LOW", f"{water:.0f} ml K3=Fill")
        else:
            lcd_print("Water: OK", f"{water:.0f} ml")

    elif screen == 5:
        lcd_print("PlantPi v2", status_message)


try:
    lcd_print("PlantPi v2", "Starting...")
    time.sleep(1)

    cleanup_old_files()

    while True:
        now = time.time()

        if now - last_sensor_read >= 2:
            read_dht()
            last_sensor_read = now

        update_pump()

        minute_key = current_minute_key()
        photo_slot = current_photo_slot()
        day_key = current_day_key()
        current_second = datetime.now().second
        current_minute = datetime.now().minute

        # Log exactly once per full minute
        if minute_key != last_logged_minute and current_second == 0:
            append_log()
            last_logged_minute = minute_key

        # Photo/light sample once every full 10-minute slot
        if (
            photo_slot != last_photo_slot
            and current_minute % 10 == 0
            and current_second == 5
        ):
            photo_path, light = capture_photo()
            append_log(photo_path, light)
            last_photo_slot = photo_slot

        # New day trigger: send report for previous day
        if day_key != last_report_day:
            status_message = "Sending report"

            try:
                subprocess.run(
                    ["python", str(BASE_DIR / "daily_report.py")],
                    check=True
                )

                status_message = "Report sent"

            except Exception:
                status_message = "Report error"

            last_report_day = day_key



        if now - last_cleanup_time >= 3600:
            cleanup_old_files()
            last_cleanup_time = now

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
