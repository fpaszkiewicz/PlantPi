import csv
import json
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import board
import busio
import adafruit_dht
import gpiod
from PIL import Image
from RPLCD.i2c import CharLCD

from adafruit_ads1x15.ads1115 import ADS1115
from adafruit_ads1x15.analog_in import AnalogIn


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
    "soil_dry_voltage": 1.53,
    "soil_wet_voltage": 1.10,
    "soil_dry_percent": 35,
    "soil_wet_percent": 70,

    "last_photo_path": None,
    "last_light_percent": None
}

def reload_state():
    global state
    state = load_json(STATE_PATH, DEFAULT_STATE)

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
time.sleep(0.5)
lcd.clear()
lcd.cursor_mode = "hide"


def create_dht():
    return adafruit_dht.DHT22(board.D4)

dht = create_dht()


# ---------- ADS1115 / SOIL ----------
ads = None
soil_channel = None

try:
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS1115(i2c)
    soil_channel = AnalogIn(ads, 0)
except Exception:
    ads = None
    soil_channel = None


# ---------- KEYPAD ----------
ROWS = [5, 6]
COLS = [13, 19]

# Physical mapping from previous working controller:
# K1 = next/back
# K2 = up/increase
# K3 = select/action
# K4 = down/decrease
KEYS = [
    ["K1", "K3"],
    ["K2", "K4"],
]

gpio_request = gpiod.request_lines(
    "/dev/gpiochip0",
    consumer="plantpi-controller-v3",
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


# ---------- RUNTIME STATE ----------
screen = 0
screen_count = 6

temperature = None
humidity = None

soil_value = None          # percent
soil_voltage = None        # voltage
soil_status = "SOIL INIT"

pump_state = "OFF"
pump_until = 0
pump_today_seconds = 0

dht_error_count = 0
status_message = "All OK"
last_key = None
last_lcd = ""

settings_index = 0
editing_setting = False

last_sensor_read = 0
last_logged_minute = None
last_photo_slot = None

last_watering_check = 0
watering_process = None

# ---------- TIME HELPERS ----------
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


last_report_day = current_day_key()


# ---------- LCD / KEYPAD ----------
def lcd_print(line1, line2=""):
    global last_lcd

    line1 = str(line1)[:16].ljust(16)
    line2 = str(line2)[:16].ljust(16)
    content = line1 + line2

    if content == last_lcd:
        return

    lcd.clear()
    lcd.cursor_mode = "hide"
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


# ---------- SENSOR READS ----------
def read_dht():
    global dht, temperature, humidity, status_message, dht_error_count

    try:
        temp = dht.temperature
        hum = dht.humidity

        if temp is not None and hum is not None:
            temperature = temp
            humidity = hum
            dht_error_count = 0
            status_message = "All OK"

    except Exception:
        dht_error_count += 1

        try:
            dht.exit()
        except Exception:
            pass

        try:
            dht = create_dht()
        except Exception:
            pass

        if dht_error_count >= 5:
            temperature = None
            humidity = None
            status_message = "DHT Error"


def clamp(value, low, high):
    return max(low, min(high, value))


def map_soil_voltage_to_percent(voltage):
    dry_v = float(state["soil_dry_voltage"])
    wet_v = float(state["soil_wet_voltage"])

    if abs(dry_v - wet_v) < 0.001:
        return None

    percent = (dry_v - voltage) / (dry_v - wet_v) * 100

    return round(clamp(percent, 0, 100), 1)


def read_soil():
    global soil_value, soil_voltage, soil_status, status_message

    if soil_channel is None:
        soil_value = None
        soil_voltage = None
        soil_status = "ADS Error"
        status_message = "ADS Error"
        return

    try:
        voltage = soil_channel.voltage
        percent = map_soil_voltage_to_percent(voltage)

        soil_voltage = round(voltage, 3)
        soil_value = percent

        if percent is None:
            soil_status = "CAL Error"
        elif percent < state["soil_threshold_percent"]:
            soil_status = "DRY"
        else:
            soil_status = "OK"

    except Exception:
        soil_value = None
        soil_voltage = None
        soil_status = "Soil Error"
        status_message = "Soil Error"


# ---------- PHOTO / LIGHT ----------
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
            [
                "rpicam-still",
                "-o", str(path),
                "--timeout", "1000",
                "--width", "1280",
                "--height", "720",
                "--quality", "60"
            ],
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


# ---------- PUMP ----------
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
        pump_line.set_value(0)
        pump_state = "OFF"


# ---------- LOGGING ----------
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
                "soil_voltage",
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
            soil_voltage,
            state["soil_threshold_percent"],
            light_percent if light_percent is not None else state["last_light_percent"],
            pump_state,
            state["pump_mode"],
            round(state["current_water_ml"], 1),
            pump_today_seconds,
            round(estimated_water_used, 1),
            photo_path if photo_path else state["last_photo_path"]
        ])



# ---------- SETTINGS ----------
def get_settings_items():
    if state["pump_mode"] == "MANUAL":
        return [
            ("Mode", "mode"),
            ("Water now", "water_now"),
        ]

    return [
        ("Mode", "mode"),
        ("DryT", "dry_t"),
        ("WetT", "wet_t"),
    ]


def setting_display(name, key):
    if key == "mode":
        return f"Mode: {state['pump_mode'].title()}"
    if key == "dry_t":
        return f"DryT: {state['soil_threshold_percent']}%"
    if key == "wet_t":
        return f"WetT: {state['soil_wet_percent']}%"
    if key == "water_now":
        if pump_state == "ON":
            remaining = max(0, int(pump_until - time.time()) + 1)
            return f"Watering {remaining}s"
        return "Water now"
    return name


def active_setting_key():
    items = get_settings_items()
    if not items:
        return None
    return items[settings_index % len(items)][1]


def edit_setting(delta):
    key = active_setting_key()

    if key == "dry_t":
        value = int(clamp(state["soil_threshold_percent"] + delta, 0, 100))
        state["soil_threshold_percent"] = value
        state["soil_dry_percent"] = value
        save_state()

    elif key == "wet_t":
        state["soil_wet_percent"] = int(clamp(state["soil_wet_percent"] + delta, 0, 100))
        save_state()


def select_setting():
    global editing_setting, settings_index

    key = active_setting_key()

    if key == "mode":
        state["pump_mode"] = "MANUAL" if state["pump_mode"] == "AUTO" else "AUTO"
        editing_setting = False
        settings_index = 0
        save_state()

    elif key in ("dry_t", "wet_t"):
        editing_setting = not editing_setting
        save_state()

    elif key == "water_now":
        subprocess.Popen(
            [
                "/home/plant/plant_project/venv/bin/python",
                str(BASE_DIR / "watering_cycle.py"),
                "--force"
            ]
        )
        seconds = float(config["watering_initial_seconds"])
        used = seconds * float(config["pump_flow_ml_per_second"])
        state["current_water_ml"] = max(0, state["current_water_ml"] - used)
        save_state()
        status_message = "Watering start"

# ---------- KEY HANDLING ----------
def handle_key(key):
    global screen, settings_index, editing_setting

    # Settings screen
    if screen == 4:
        items = get_settings_items()

        if key == "K1":
            editing_setting = False
            screen = (screen + 1) % screen_count
            return

        if key == "K2":
            if editing_setting:
                edit_setting(+1)
            else:
                settings_index = (settings_index - 1) % len(items)
            return

        if key == "K4":
            if editing_setting:
                edit_setting(-1)
            else:
                settings_index = (settings_index + 1) % len(items)
            return

        if key == "K3":
            select_setting()
            return

    # Main screens
    if key == "K1":
        screen = (screen + 1) % screen_count

    elif key == "K3":
        if screen == 2:
            photo_path, light = capture_photo()
            append_log(photo_path, light)
        elif screen == 3:
            state["current_water_ml"] = config["tank_capacity_ml"]
            save_state()
            lcd_print("Tank refilled", f"{state['current_water_ml']:.0f} ml")
            time.sleep(2)

# ---------- RENDER ----------
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
            lcd_print("Soil: --%", f"Tresh: {state['soil_threshold_percent']}%")
        else:
            lcd_print(
                f"Soil: {soil_value:4.1f}%",
                f"Tresh: {state['soil_threshold_percent']}%"
            )

    elif screen == 2:
        light = state["last_light_percent"]
        if light is None:
            lcd_print("Camera Ready", "")
        else:
            lcd_print("Camera Ready", f"Light: {light:.1f}%")

    elif screen == 3:
        water = state["current_water_ml"]
        if water <= config["water_stop_threshold_ml"]:
            lcd_print("Water: STOP", f"{water:.0f} ml")
        elif water <= config["water_warning_threshold_ml"]:
            lcd_print("Water: LOW", f"{water:.0f} ml")
        else:
            lcd_print("Water: OK", f"{water:.0f} ml")

    elif screen == 4:
        items = get_settings_items()
        index = settings_index % len(items)
        name, key = items[index]
        value = setting_display(name, key)

        line1 = f"Setting {index + 1}/{len(items)}"
        line2 = value

        if editing_setting:
            blink_visible = int(time.time() * 2) % 2 == 0
            if not blink_visible:
                line2 = "" 

        lcd_print(line1, line2)

    elif screen == 5:
        lcd_print("PlantPi v3", status_message)

# ---------- MAIN ----------
try:
    lcd_print("PlantPi v3", "Starting...")
    time.sleep(1)


    while True:
        now = time.time()

        if now - last_sensor_read >= 10:
            read_dht()
            read_soil()
            last_sensor_read = now

        update_pump()
        if watering_process is not None and watering_process.poll() is not None:
            reload_state()

        # Auto watering check
        if now - last_watering_check >= config.get("watering_check_interval_seconds", 21600):
            last_watering_check = now

            if state["pump_mode"] == "AUTO":
                if soil_value is not None and soil_value < state["soil_threshold_percent"]:
                    if watering_process is None or watering_process.poll() is not None:
                        status_message = "Watering start"

                        watering_process = subprocess.Popen(
                            [
                                "/home/plant/plant_project/venv/bin/python",
                                str(BASE_DIR / "watering_cycle.py")
                            ]
                        )

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
