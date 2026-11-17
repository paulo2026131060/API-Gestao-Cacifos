import json
import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):

    payload = json.loads(msg.payload)

    print(payload)

client = mqtt.Client()

client.username_pw_set(
    "app-id",
    "NNSXS.XXXXXXXXXXXXXXXX"
)

client.connect(
    "eu1.cloud.thethings.network",
    1883,
    60
)

client.subscribe(
    "v3/app-id/devices/+/up"
)

client.on_message = on_message

client.loop_forever()