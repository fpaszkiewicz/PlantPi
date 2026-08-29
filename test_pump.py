import time
import gpiod

PIN = 17

request = gpiod.request_lines(
    "/dev/gpiochip0",
    consumer="pump-test",
    config={
        PIN: gpiod.LineSettings(
            direction=gpiod.line.Direction.OUTPUT
        )
    },
)

print("Pump ON")
request.set_value(PIN, gpiod.line.Value.ACTIVE)

time.sleep(2.5)

print("Pump OFF")
request.set_value(PIN, gpiod.line.Value.INACTIVE)

request.release()
