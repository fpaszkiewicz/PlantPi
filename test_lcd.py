from RPLCD.i2c import CharLCD
from time import sleep

lcd = CharLCD(
    i2c_expander='PCF8574',
    address=0x27,
    port=1,
    cols=16,
    rows=2
)

lcd.clear()

while True:
    lcd.cursor_pos = (0, 0)
    lcd.write_string('PlantPi Ready')

    lcd.cursor_pos = (1, 0)
    lcd.write_string('LCD works      ')

    sleep(1)
