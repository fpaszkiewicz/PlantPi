import csv
import time
from datetime import datetime
from pathlib import Path

import board
import busio
from adafruit_ads1x15.ads1115 import ADS1115
from adafruit_ads1x15.analog_in import AnalogIn


BASE_DIR = Path.home() / "plant_project"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def log_path():
    date = datetime.now().strftime("%Y-%m-%d")
    return LOGS_DIR / f"soil_voltage_{date}.csv"


i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS1115(i2c)
soil = AnalogIn(ads, 0)

while True:
    path = log_path()
    file_exists = path.exists()

    voltage = round(soil.voltage, 4)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(path, "a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(["timestamp", "soil_voltage"])

        writer.writerow([timestamp, voltage])

    time.sleep(60)

