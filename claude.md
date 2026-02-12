# AirLab MQTT Data Logger

## What this is
Python script that reads CO2, temperature, humidity, pressure, VOC, and NOx from a Networked Artifacts AirLab via MQTT and stores readings in SQLite. Runs on a Raspberry Pi, scheduled via crontab. Grafana dashboards via the SQLite plugin. Same pattern as [aranet4-dash](https://github.com/12ian34/aranet4-dash).

## Tech stack
- Python 3.9+ managed with [uv](https://docs.astral.sh/uv/)
- `paho-mqtt` (MQTT client) and `python-dotenv` (config)
- SQLite via stdlib `sqlite3`
- Mosquitto MQTT broker on the Pi
- Grafana + `frser-sqlite-datasource` plugin for dashboards
- crontab for scheduling (every minute, `--single` mode)

## Project structure
```
airlab-dash/
├── .env.example               # Template config (committed)
├── .env                       # Actual config (gitignored)
├── .gitignore
├── airlab_collector.py        # Main script
├── discover.py                # MQTT topic discovery helper
├── pyproject.toml             # Python dependencies (uv)
├── grafana/
│   └── dashboard.json         # Grafana dashboard (import via UI)
├── README.md                  # Full setup instructions
└── claude.md                  # This file (AI context)
```

## Database schema
Table `airlab_readings` in SQLite:
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `timestamp` DATETIME DEFAULT CURRENT_TIMESTAMP
- `co2_ppm` REAL
- `temperature_c` REAL
- `humidity_percent` REAL
- `pressure_hpa` REAL
- `voc_index` REAL
- `nox_index` REAL
- Index on `timestamp`

## Key decisions
- **uv for Python**: All dependency management and script execution via `uv sync` / `uv run`. No manual venv or pip.
- **Crontab**: Script runs in `--single` mode per invocation. Connects to MQTT, waits up to 30s for a reading, saves, exits. No daemon.
- **`.env` resolved relative to script**: `load_dotenv()` uses the script's own directory, so cron jobs work regardless of cwd.
- **MQTT single-shot**: Connects, subscribes to `{base_topic}/#`, collects messages for 3s after first arrival (handles both JSON-blob and one-value-per-topic formats), saves, disconnects.
- **Mosquitto on the Pi**: Lightweight broker, AirLab connects to it over local WiFi. Mosquitto 2.0+ requires auth by default — must create a password file via `mosquitto_passwd` and configure `/etc/mosquitto/conf.d/auth.conf`. The username/password must match across Mosquitto, `.env`, and Air Lab Studio.
- **Same Grafana pattern as aranet4-dash**: SQLite plugin reads the `.db` file directly. Timestamps stored as DATETIME DEFAULT CURRENT_TIMESTAMP, converted via `CAST(strftime('%s', timestamp) AS INTEGER)` for time series, `* 1000` for table/stat panels.

## Config (.env)
- `MQTT_HOST` — broker IP (localhost if Mosquitto on same Pi)
- `MQTT_PORT` — broker port (default 1883)
- `MQTT_USERNAME` — MQTT auth username (empty if no auth)
- `MQTT_PASSWORD` — MQTT auth password (empty if no auth)
- `MQTT_BASE_TOPIC` — base topic configured in Air Lab Studio (e.g. `airlab`)
- `DB_PATH` — path to SQLite database (default `/var/lib/airlab-dash/airlab.db`)

## AirLab device details
- Built by [Networked Artifacts](https://networkedartifacts.com) ([Crowd Supply page](https://www.crowdsupply.com/networked-artifacts/air-lab))
- Sensors: SCD41 (CO2, temp, humidity), SGP41 (VOC, NOx), BMP (pressure)
- Configured via [Air Lab Studio](https://airlab.networkedartifacts.com) (Bluetooth, Chrome-based browser — Chrome/Edge/Brave only, not Firefox/Safari)
- Setup flow: plug in USB-C → press A → set time → outdoor calibration → WiFi config → MQTT config
- **WiFi: 2.4GHz only** — will not connect to 5GHz networks. Common gotcha if router broadcasts both bands under one SSID.
- MQTT publishes one value per topic with short names: `{base_topic}/co2` (ppm), `{base_topic}/tmp` (°C), `{base_topic}/hum` (%), `{base_topic}/prs` (hPa), `{base_topic}/voc` (index), `{base_topic}/nox` (index). All values are plain numbers (not JSON).
- Supports Home Assistant auto-discovery (homeassistant/sensor/…/config topics)

## Gotchas discovered during setup
- **2.4GHz WiFi only**: AirLab silently fails to connect on 5GHz. No error — just stays "Disconnected".
- **Mosquitto 2.0+ auth**: Default config rejects all connections. Must create password file and auth.conf before AirLab can connect.
- **Credentials must match in 3 places**: Mosquitto password file, `.env`, and Air Lab Studio MQTT settings.
