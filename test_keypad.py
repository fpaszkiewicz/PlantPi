import time
import gpiod

ROWS = [5, 6]
COLS = [13, 19]

KEYS = [
    ["K1", "K3"],
    ["K2", "K4"],
]

request = gpiod.request_lines(
    "/dev/gpiochip0",
    consumer="matrix-keypad-test",
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

last_pressed = None

print("Press keypad buttons... CTRL+C to exit")

try:
    while True:
        pressed = None

        for r, row_pin in enumerate(ROWS):
            request.set_value(row_pin, gpiod.line.Value.INACTIVE)

            for c, col_pin in enumerate(COLS):
                if request.get_value(col_pin) == gpiod.line.Value.INACTIVE:
                    pressed = KEYS[r][c]

            request.set_value(row_pin, gpiod.line.Value.ACTIVE)

        if pressed and pressed != last_pressed:
            print(f"{pressed} pressed")

        last_pressed = pressed
        time.sleep(0.05)

finally:
    request.release()
