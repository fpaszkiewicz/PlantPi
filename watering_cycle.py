import sys
import json
import time
from datetime import datetime
from pathlib import Path

import board
import busio
import gpiod
from adafruit_ads1x15.ads1115 import ADS1115
from adafruit_ads1x15.analog_in import AnalogIn


BASE_DIR = Path.home() / "plant_project"
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
LOG_PATH = BASE_DIR / "logs" / "watering_cycle.log"


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")
    print(msg)


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2))


def clamp(value, low, high):
    return max(low, min(high, value))


def voltage_to_percent(voltage, state):
    dry_v = float(state["soil_dry_voltage"])
    wet_v = float(state["soil_wet_voltage"])

    if abs(dry_v - wet_v) < 0.001:
        return None

    percent = (dry_v - voltage) / (dry_v - wet_v) * 100
    return round(clamp(percent, 0, 100), 1)


def read_soil():
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS1115(i2c)
    chan = AnalogIn(ads, 0)

    voltage = round(chan.voltage, 3)
    return voltage


def pump_on_for(seconds, config, state):
    if not config.get("watering_enabled", False):
        log(f"Pump skipped: watering_enabled=false, simulated {seconds}s")
        return

    gpio_pin = int(config.get("pump_gpio", 27))

    request = gpiod.request_lines(
        "/dev/gpiochip0",
        consumer="watering-cycle",
        config={
            gpio_pin: gpiod.LineSettings(
                direction=gpiod.line.Direction.OUTPUT
            )
        },
    )

    try:
        log(f"Pump ON for {seconds}s")
        request.set_value(gpio_pin, gpiod.line.Value.ACTIVE)
        time.sleep(seconds)
        request.set_value(gpio_pin, gpiod.line.Value.INACTIVE)
        log("Pump OFF")

    finally:
        request.set_value(gpio_pin, gpiod.line.Value.INACTIVE)
        request.release()

    used_ml = seconds * float(config["pump_flow_ml_per_second"])
    state["current_water_ml"] = max(0, state["current_water_ml"] - used_ml)
    save_state(state)


def main():
    force = "--force" in sys.argv
    config = load_json(CONFIG_PATH)
    state = load_json(STATE_PATH)

    initial_seconds = float(config.get("watering_initial_seconds", 5))
    delay_seconds = int(config.get("watering_check_delay_seconds", 3600))
    max_cycles = int(config.get("watering_max_cycles", 3))

    dry_t = float(state["soil_threshold_percent"])
    wet_t = float(state["soil_wet_percent"])

    log("Watering cycle started")

    for cycle in range(1, max_cycles + 1):
        state = load_json(STATE_PATH)

        voltage = read_soil()
        percent = voltage_to_percent(voltage, state)

        log(f"Cycle {cycle}: soil={percent}% voltage={voltage}V")

        if not force and percent is not None and percent >= wet_t:
            log(f"Soil already >= WetT ({wet_t}%). Stop.")
            return

        if state["current_water_ml"] <= config["water_stop_threshold_ml"]:
            log("Watering blocked: low water")
            return

        pump_on_for(initial_seconds, config, state)

        if cycle < max_cycles:
            log(f"Waiting {delay_seconds}s before recheck")
            time.sleep(delay_seconds)

    log("Watering cycle finished: max cycles reached")


if __name__ == "__main__":
    main()
