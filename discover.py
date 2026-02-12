#!/usr/bin/env python3
"""AirLab MQTT topic discovery helper.

Subscribes to the base topic (wildcard) and prints every message.
Run this first to see what topics and payloads your AirLab publishes.

Usage:
    uv run discover.py                     # uses .env in script dir
    uv run discover.py --topic "airlab/#"  # override topic
"""

import json
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv


def load_config() -> dict:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")
    return {
        "host": os.getenv("MQTT_HOST", "localhost").strip(),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "username": os.getenv("MQTT_USERNAME", "").strip(),
        "password": os.getenv("MQTT_PASSWORD", "").strip(),
        "base_topic": os.getenv("MQTT_BASE_TOPIC", "airlab").strip(),
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    topic = userdata["topic"]
    if reason_code == 0:
        print(f"[OK] Connected.  Subscribing to: {topic}")
        client.subscribe(topic)
    else:
        print(f"[ERROR] Connection failed: {reason_code}")


def on_message(client, userdata, msg: mqtt.MQTTMessage):
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    payload = msg.payload.decode("utf-8", errors="replace")

    try:
        data = json.loads(payload)
        payload = json.dumps(data, indent=2)
    except (json.JSONDecodeError, ValueError):
        pass

    print(f"\n[{now}] Topic: {msg.topic}")
    print(f"  Payload: {payload}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Discover AirLab MQTT topics")
    parser.add_argument("--topic", help="Override subscription topic")
    args = parser.parse_args()

    cfg = load_config()
    topic = args.topic or f"{cfg['base_topic']}/#"

    print(f"Broker:  {cfg['host']}:{cfg['port']}")
    print(f"Topic:   {topic}")
    print("Waiting for messages (Ctrl+C to quit)...\n")

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="airlab-discover",
    )
    client.user_data_set({"topic": topic})
    client.on_connect = on_connect
    client.on_message = on_message

    if cfg["username"]:
        client.username_pw_set(cfg["username"], cfg["password"])

    try:
        client.connect(cfg["host"], cfg["port"], keepalive=60)
    except Exception as e:
        print(f"[ERROR] Could not connect to {cfg['host']}:{cfg['port']} — {e}")
        sys.exit(1)

    def handle_signal(sig, frame):
        print("\nDisconnecting...")
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    client.loop_forever()


if __name__ == "__main__":
    main()
