from datetime import datetime, timedelta
from pathlib import Path
import subprocess


TIMELAPSE_RETENTION_DAYS = 7

BASE_DIR = Path.home() / "plant_project"
PHOTOS_DIR = BASE_DIR / "photos"
TIMELAPSE_DIR = BASE_DIR / "timelapses"

TIMELAPSE_DIR.mkdir(exist_ok=True)


def target_date():
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # for testing today:
    # return datetime.now().strftime("%Y-%m-%d")


date_str = target_date()

photo_files = sorted(
    [
        p for p in PHOTOS_DIR.glob("photo_*.jpg")
        if date_str in p.name
    ]
)

if not photo_files:
    print("No photos found.")
    raise SystemExit(1)


temp_dir = TIMELAPSE_DIR / f"temp_{date_str}"
temp_dir.mkdir(exist_ok=True)

print(f"Preparing {len(photo_files)} frames...")

for idx, src in enumerate(photo_files):
    dst = temp_dir / f"frame_{idx:05d}.jpg"
    dst.write_bytes(src.read_bytes())


output_path = TIMELAPSE_DIR / f"timelapse_{date_str}.mp4"

cmd = [
    "ffmpeg",
    "-y",
    "-framerate", "8",
    "-i", str(temp_dir / "frame_%05d.jpg"),
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "30",
    "-pix_fmt", "yuv420p",
    str(output_path)
]

print("Creating timelapse...")

subprocess.run(cmd, check=True)

print("Cleaning temporary frames...")
for file in temp_dir.glob("*.jpg"):
    file.unlink()

temp_dir.rmdir()

print("Deleting all photos...")
for file in PHOTOS_DIR.glob("*.jpg"):
    try:
        file.unlink()
    except Exception:
        pass


print(f"Timelapse saved:")
print(output_path)

