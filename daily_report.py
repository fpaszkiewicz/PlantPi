import csv
import json
import smtplib
import statistics
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from collections import defaultdict
import subprocess
import os

from dotenv import load_dotenv


BASE_DIR = Path.home() / "plant_project"
LOGS_DIR = BASE_DIR / "logs"
EMAIL_CONFIG_PATH = BASE_DIR / "email_config.json"
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)


def report_date():
    # For normal daily report: yesterday
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # For testing today, temporarily use:
    # return datetime.now().strftime("%Y-%m-%d")


def safe_float(value):
    try:
        if value in ("", "None", None):
            return None
        return float(value)
    except Exception:
        return None


def avg(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(statistics.mean(values), 2)


def fmt(value, suffix=""):
    if value is None:
        return "--"
    return f"{value}{suffix}"


date_str = report_date()
log_path = LOGS_DIR / f"log_{date_str}.csv"

timelapse_path = BASE_DIR / "timelapses" / f"timelapse_{date_str}.mp4"

try:
    subprocess.run(
        ["python", str(BASE_DIR / "make_timelapse.py")],
        check=True
    )
except Exception:
    timelapse_path = None

if not log_path.exists():
    print(f"No log file for {date_str}: {log_path}")
    raise SystemExit(1)


with open(EMAIL_CONFIG_PATH, "r") as f:
    email_config = json.load(f)

smtp_host = email_config["smtp_host"]
smtp_port = email_config["smtp_port"]
sender_email = email_config["sender_email"]
receiver_email = email_config["receiver_email"]
email_password = os.getenv("PLANTPI_EMAIL_PASSWORD")

if not email_password:
    print("Missing PLANTPI_EMAIL_PASSWORD in .env")
    raise SystemExit(1)


rows = []
hourly = defaultdict(lambda: {
    "temp": [],
    "air": [],
    "soil": [],
    "light": [],
    "water": [],
    "pump_seconds": [],
    "water_used": []
})

with open(log_path, "r") as f:
    reader = csv.DictReader(f)

    for row in reader:
        rows.append(row)

        ts = row.get("timestamp", "")
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            hour_key = dt.strftime("%H:00")
        except Exception:
            continue

        hourly[hour_key]["temp"].append(safe_float(row.get("temperature_c")))
        hourly[hour_key]["air"].append(safe_float(row.get("air_humidity_percent")))
        hourly[hour_key]["soil"].append(safe_float(row.get("soil_moisture_percent")))
        hourly[hour_key]["light"].append(safe_float(row.get("light_percent")))
        hourly[hour_key]["water"].append(safe_float(row.get("current_water_ml")))
        hourly[hour_key]["pump_seconds"].append(safe_float(row.get("pump_today_seconds")))
        hourly[hour_key]["water_used"].append(safe_float(row.get("estimated_water_used_today_ml")))


all_temp = []
all_air = []
all_soil = []
all_light = []
all_water = []
all_pump_seconds = []
all_water_used = []

for h in hourly.values():
    all_temp += h["temp"]
    all_air += h["air"]
    all_soil += h["soil"]
    all_light += h["light"]
    all_water += h["water"]
    all_pump_seconds += h["pump_seconds"]
    all_water_used += h["water_used"]

final_water = [v for v in all_water if v is not None]
final_water = final_water[-1] if final_water else None

final_pump_seconds = [v for v in all_pump_seconds if v is not None]
final_pump_seconds = final_pump_seconds[-1] if final_pump_seconds else 0

final_water_used = [v for v in all_water_used if v is not None]
final_water_used = final_water_used[-1] if final_water_used else 0


lines = []

lines.append("PlantPi Daily Report")
lines.append(f"Date: {date_str}")
lines.append("")
lines.append("Daily summary")
lines.append(f"Average temperature: {fmt(avg(all_temp), ' C')}")
lines.append(f"Average air humidity: {fmt(avg(all_air), ' %')}")
lines.append(f"Average soil moisture: {fmt(avg(all_soil), ' %')}")
lines.append(f"Average light level: {fmt(avg(all_light), ' %')}")
lines.append(f"Pump runtime: {final_pump_seconds} s")
lines.append(f"Estimated water used: {final_water_used} ml")
lines.append(f"Remaining water: {fmt(final_water, ' ml')}")
lines.append(f"Log entries: {len(rows)}")
lines.append("")
lines.append("Hourly breakdown")
lines.append("Hour | Temp C | Air % | Soil % | Light % | Pump s | Water used ml")

previous_used = 0

for hour in [f"{h:02d}:00" for h in range(24)]:
    data = hourly.get(hour)

    if not data:
        lines.append(f"{hour} | -- | -- | -- | -- | -- | --")
        continue

    pump_values = [v for v in data["pump_seconds"] if v is not None]
    used_values = [v for v in data["water_used"] if v is not None]

    hour_pump = "--"
    hour_used = "--"

    if pump_values:
        hour_pump = round(max(pump_values) - min(pump_values), 1)

    if used_values:
        current_max_used = max(used_values)
        hour_used = round(current_max_used - previous_used, 1)
        previous_used = current_max_used

    lines.append(
        f"{hour} | "
        f"{fmt(avg(data['temp']))} | "
        f"{fmt(avg(data['air']))} | "
        f"{fmt(avg(data['soil']))} | "
        f"{fmt(avg(data['light']))} | "
        f"{hour_pump} | "
        f"{hour_used}"
    )

report = "\n".join(lines)

print(report)

def cleanup_old_logs(retention_days=7):
    cutoff = datetime.now() - timedelta(days=retention_days)

    for file in LOGS_DIR.glob("log_*.csv"):
        try:
            modified = datetime.fromtimestamp(file.stat().st_mtime)
            if modified < cutoff:
                file.unlink()
        except Exception:
            pass


msg = EmailMessage()
msg["Subject"] = f"PlantPi Daily Report - {date_str}"
msg["From"] = sender_email
msg["To"] = receiver_email
msg.set_content(report)

with open(log_path, "rb") as f:
    msg.add_attachment(
        f.read(),
        maintype="text",
        subtype="csv",
        filename=log_path.name
    )

if timelapse_path and timelapse_path.exists():
    with open(timelapse_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="video",
            subtype="mp4",
            filename=timelapse_path.name
        )

with smtplib.SMTP(smtp_host, smtp_port) as server:
    server.starttls()
    server.login(sender_email, email_password)
    server.send_message(msg)


def cleanup_timelapses():
    timelapse_dir = BASE_DIR / "timelapses"

    for file in timelapse_dir.glob("*.mp4"):
        try:
            file.unlink()
        except Exception:
            pass


print("Email sent.")

#cleanup_timelapses()
#print("Timelapses cleaned.")

cleanup_old_logs()
print("Old logs cleaned.")
