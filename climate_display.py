import time
import board
import adafruit_dht

from RPLCD.i2c import CharLCD

lcd = CharLCD(
    i2c_expander='PCF8574',
    address=0x27,
    port=1,
    cols=16,
    rows=2
)

dht = adafruit_dht.DHT22(board.D4)

lcd.clear()

while True:
    try:
        temp = dht.temperature
        hum = dht.humidity

        lcd.clear()

        lcd.cursor_pos = (0, 0)
        lcd.write_string(f"Temp: {temp:.1f} C")

        lcd.cursor_pos = (1, 0)
        lcd.write_string(f"Hum:  {hum:.1f} %")

        print(f"Temp: {temp:.1f} C")
        print(f"Humidity: {hum:.1f} %")
        print("------")

    except Exception as e:
        print(e)

    time.sleep(2)
