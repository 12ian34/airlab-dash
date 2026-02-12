#!/usr/bin/env python3
"""AirLab MQTT Data Logger with SQLite storage."""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Sensor fields we expect from the AirLab, with aliases for flexibility
FIELD_ALIASES: dict[str, str] = {
    "co2": "co2_ppm",
    "carbon_dioxide": "co2_ppm",
    "co2_ppm": "co2_ppm",
    "temperature": "temperature_c",
    "temp": "temperature_c",
    "temperature_c": "temperature_c",
    "humidity": "humidity_percent",
    "rh": "humidity_percent",
    "relative_humidity": "humidity_percent",
    "humidity_percent": "humidity_percent",
    "pressure": "pressure_hpa",
    "pressure_hpa": "pressure_hpa",
    "voc": "voc_index",
    "voc_index": "voc_index",
    "nox": "nox_index",
    "nox_index": "nox_index",
}

VALID_RANGES = {
    "co2_ppm": (150, 10000),
    "temperature_c": (-40.0, 85.0),
    "humidity_percent": (0, 100),
    "pressure_hpa": (300.0, 1200.0),
    "voc_index": (0, 500),
    "nox_index": (0, 500),
}

logger = logging.getLogger("airlab_collector")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def load_config() -> dict:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")
    return {
        "host": os.getenv("MQTT_HOST", "localhost").strip(),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "username": os.getenv("MQTT_USERNAME", "").strip(),
        "password": os.getenv("MQTT_PASSWORD", "").strip(),
        "base_topic": os.getenv("MQTT_BASE_TOPIC", "airlab").strip(),
        "db_path": os.getenv(
            "DB_PATH", str(script_dir / "airlab.db")
        ).strip(),
    }


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS airlab_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            co2_ppm REAL,
            temperature_c REAL,
            humidity_percent REAL,
            pressure_hpa REAL,
            voc_index REAL,
            nox_index REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_airlab_timestamp "
        "ON airlab_readings(timestamp)"
    )
    conn.commit()
    return conn


def canonicalize(field: str) -> str | None:
    return FIELD_ALIASES.get(field.lower().strip())


def try_float(value) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_json_payload(payload: str) -> dict[str, float]:
    """Parse a JSON object into canonical {column: value} pairs."""
    data = json.loads(payload)
    if not isinstance(data, dict):
        return {}
    reading: dict[str, float] = {}
    for key, value in data.items():
        canon = canonicalize(key)
        if canon:
            v = try_float(value)
            if v is not None:
                reading[canon] = v
    return reading


def parse_topic_value(
    topic: str, payload: str, base_topic: str
) -> dict[str, float]:
    """Parse a single-value-per-topic message (e.g. airlab/co2 -> 800)."""
    relative = topic
    if topic.startswith(base_topic):
        relative = topic[len(base_topic) :].strip("/")
    for part in reversed(relative.split("/")):
        canon = canonicalize(part)
        if canon:
            v = try_float(payload)
            if v is not None:
                return {canon: v}
    return {}


def validate_reading(reading: dict[str, float]) -> bool:
    for key, value in reading.items():
        bounds = VALID_RANGES.get(key)
        if bounds and not (bounds[0] <= value <= bounds[1]):
            logger.warning(
                "Validation failed: %s=%s (expected %s-%s)",
                key,
                value,
                bounds[0],
                bounds[1],
            )
            return False
    return True


def insert_reading(conn: sqlite3.Connection, reading: dict[str, float]) -> None:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn.execute(
                """
                INSERT INTO airlab_readings
                    (co2_ppm, temperature_c, humidity_percent,
                     pressure_hpa, voc_index, nox_index)
                VALUES
                    (:co2_ppm, :temperature_c, :humidity_percent,
                     :pressure_hpa, :voc_index, :nox_index)
                """,
                {
                    "co2_ppm": reading.get("co2_ppm"),
                    "temperature_c": reading.get("temperature_c"),
                    "humidity_percent": reading.get("humidity_percent"),
                    "pressure_hpa": reading.get("pressure_hpa"),
                    "voc_index": reading.get("voc_index"),
                    "nox_index": reading.get("nox_index"),
                },
            )
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < max_retries - 1:
                logger.warning(
                    "Database locked, retrying (%d/%d)",
                    attempt + 1,
                    max_retries,
                )
                time.sleep(0.5 * (attempt + 1))
            else:
                raise


# ── Single-shot MQTT read ─────────────────────────────────────────────────────


def read_airlab(cfg: dict, timeout: int = 30) -> dict[str, float] | None:
    """Connect to MQTT, collect one reading, disconnect."""
    base_topic = cfg["base_topic"]
    subscribe_topic = f"{base_topic}/#"
    reading: dict[str, float] = {}
    got_data = False
    first_msg_time: list[float] = []  # mutable container for closure

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("Connected to MQTT broker, subscribing to %s", subscribe_topic)
            client.subscribe(subscribe_topic)
        else:
            logger.error("MQTT connection failed: %s", reason_code)

    def on_message(client, userdata, msg: mqtt.MQTTMessage):
        nonlocal got_data
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        topic = msg.topic

        # Skip HA discovery config messages
        if topic.endswith("/config"):
            return

        # Try JSON object first
        parsed: dict[str, float] = {}
        try:
            parsed = parse_json_payload(payload)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fall back to single-value-per-topic
        if not parsed:
            parsed = parse_topic_value(topic, payload, base_topic)

        if parsed:
            reading.update(parsed)
            got_data = True
            if not first_msg_time:
                first_msg_time.append(time.monotonic())
            logger.info("Received: %s", parsed)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"airlab-collector-{os.getpid()}",
    )
    client.on_connect = on_connect
    client.on_message = on_message

    if cfg["username"]:
        client.username_pw_set(cfg["username"], cfg["password"])

    logger.info("Connecting to %s:%s ...", cfg["host"], cfg["port"])
    try:
        client.connect(cfg["host"], cfg["port"], keepalive=60)
    except Exception as e:
        logger.error("Could not connect to MQTT broker: %s", e)
        return None

    # Poll until we have data or timeout
    client.loop_start()
    deadline = time.monotonic() + timeout
    collect_window = 3  # seconds after first message to collect more values

    while time.monotonic() < deadline:
        time.sleep(0.5)
        # If we got data, wait a bit more for additional topic messages
        if first_msg_time:
            elapsed_since_first = time.monotonic() - first_msg_time[0]
            if elapsed_since_first >= collect_window:
                break

    client.loop_stop()
    client.disconnect()

    if not got_data:
        logger.error("No data received within %ds", timeout)
        return None

    return reading


def single_reading(cfg: dict) -> None:
    """Take a single reading and exit (for cron)."""
    conn = init_db(cfg["db_path"])
    try:
        reading = read_airlab(cfg)
        if reading is None:
            logger.error("Failed to read from AirLab")
            sys.exit(1)

        if not reading:
            logger.error("Empty reading from AirLab")
            sys.exit(1)

        if validate_reading(reading):
            insert_reading(conn, reading)
            logger.info(
                "Saved: %s",
                "  ".join(f"{k}={v}" for k, v in sorted(reading.items())),
            )
        else:
            logger.warning("Reading failed validation, not saved")
    finally:
        conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    setup_logging()
    cfg = load_config()

    parser = argparse.ArgumentParser(description="AirLab MQTT Data Logger")
    parser.add_argument(
        "--single",
        action="store_true",
        help="Take a single reading and exit (for cron)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="MQTT wait timeout in seconds (default 30)",
    )
    args = parser.parse_args()

    if args.single:
        single_reading(cfg)
    else:
        # Default: also single mode (no daemon)
        single_reading(cfg)


if __name__ == "__main__":
    main()
