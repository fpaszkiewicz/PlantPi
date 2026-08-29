import time
import board
import adafruit_dht

dht = adafruit_dht.DHT22(board.D4)

while True:
    try:
        temp = dht.temperature
        hum = dht.humidity

        print(f"Temp: {temp} C")
        print(f"Humidity: {hum} %")

    except Exception as e:
        print(e)

    time.sleep(2)
