import time
import gpiod

PIN = 17
HOLD_SECONDS = 30

request = gpiod.request_lines(
    "/dev/gpiochip0",
    consumer="mosfet-hold-test",
    config={
        PIN: gpiod.LineSettings(direction=gpiod.line.Direction.OUTPUT)
    },
)

try:
    print(f"MOSFET ON for {HOLD_SECONDS} seconds")
    request.set_value(PIN, gpiod.line.Value.ACTIVE)

    for remaining in range(HOLD_SECONDS, 0, -1):
        print(f"{remaining}s left")
        time.sleep(1)

    print("MOSFET OFF")
    request.set_value(PIN, gpiod.line.Value.INACTIVE)

finally:
    request.set_value(PIN, gpiod.line.Value.INACTIVE)
    request.release()

