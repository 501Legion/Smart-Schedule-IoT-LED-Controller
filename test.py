from flask import Flask
import RPi.GPIO as GPIO

app = Flask(__name__)

LED = 8
GPIO.setmode(GPIO.BOARD)
GPIO.setup(LED, GPIO.OUT, initial = GPIO.LOW)

@app.route("/")
def helloworld():
    return "123"

@app.route("/LED/<state>")
def LED_ON(state):
    if state == "on":
        GPIO.output(LED, GPIO.HIGH)
    else:
        GPIO.output(LED, GPIO.LOW)
    return "LED + " + state

@app.route("/LED/CLEAN")
def GPIO_CLEANUP():
    GPIO.cleanup()
    return "clean"


if __name__ == "__main__":
    app.run(host = "0.0.0.0")

GPIO.setmode(GPIO.BOARD)
GPIO.setup(LED, GPIO.OUT, initial = GPIO.LOW)
