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
├── .claude/
│   └── skills/airlab-dash/    # Repo-local Claude skill for AirLab recovery
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
- Built by [Networked Artifacts](https://networkedartifacts.com) ([Crowd Supply page](https://www.crowdsupply.com/networked-artifacts/air-lab)), [manual](https://networkedartifacts.com/manuals/airlab/device-overview)
- Sensors: SCD41 (CO2, temp, humidity), SGP41 (VOC, NOx), LPS22 (pressure)
- SGP41 needs **1 hour warm-up** before VOC/NOx are reliable. The VOC/NOx Index algorithms need **24 hours** of continuous operation to learn a baseline — readings before that are relative to limited history.
- ESP32S3 microcontroller, 1500 mAh LiPo, E-Paper 296x128, open firmware
- Configured via [Air Lab Studio](https://airlab.networkedartifacts.com) (Bluetooth, Chrome-based browser — Chrome/Edge/Brave only, not Firefox/Safari)
- Setup flow: plug in USB-C → press A → set time → outdoor calibration → WiFi config → MQTT config
- **WiFi: 2.4GHz only** — will not connect to 5GHz networks. Common gotcha if router broadcasts both bands under one SSID.
- MQTT publishes one value per topic with short names: `{base_topic}/co2` (ppm), `{base_topic}/tmp` (°C), `{base_topic}/hum` (%), `{base_topic}/prs` (hPa), `{base_topic}/voc` (index), `{base_topic}/nox` (index). All values are plain numbers (not JSON).
- Supports Home Assistant auto-discovery (homeassistant/sensor/…/config topics)

## Gotchas discovered during setup
- **Must be plugged into USB-C for MQTT**: On battery, the AirLab sleeps and stops WiFi/MQTT publishing — no matter what. Only BLE connections keep it awake on battery. Confirmed by the AirLab team (Joel). For continuous monitoring, keep it plugged in.
- **2.4GHz WiFi only**: AirLab silently fails to connect on 5GHz. No error — just stays "Disconnected".
- **Mosquitto 2.0+ auth**: Default config rejects all connections. Must create password file and auth.conf before AirLab can connect.
- **Credentials must match in 3 places**: Mosquitto password file, `.env`, and Air Lab Studio MQTT settings.
- **Default sample rates**: Sleep 30s, Record 5s, Long-term 60s. When plugged in, it publishes every 5s (record rate). Cron runs every 1 minute so we get one reading per minute.

## Troubleshooting summary (investigated Apr 2026)

Context: Pi (`pian`) had been **rebooted**; unrelated edits existed in `../aranet4-dash`. User saw problems in cron logs over roughly the prior two hours.

### What was tried (on the Pi via `ssh pian`)

| Check | Result |
|--------|--------|
| `uptime` / `last -x reboot` | Reboot **~14:13** same day; services came up normally afterward. |
| `crontab -l` | `airlab-dash` and `aranet4-dash` entries present; PATH includes `uv`. |
| `tail ~/dev/airlab-dash/cron.log` | Every minute: **connects** to `localhost:1883`, **subscribes** to `airlab/#`, then **`No data received within 30s`** / **`Failed to read from AirLab`**. |
| Last **`Saved:`** lines in that log | Last successful ingest **~14:13** (same window as reboot); **no** further saves in the sampled period. |
| `tail ~/dev/aranet4-dash/cron.log` | **Healthy** minute-by-minute reads; **one** transient `not found during scan` then recovery (typical BLE). |
| `systemctl status mosquitto` | **active (running)** since shortly after boot; config loads `auth.conf`. |
| `journalctl` (priority errors, last ~2h) | Mostly unrelated noise (bluetoothd, wpa_supplicant, caddy/sudo). Nothing indicating Mosquitto or cron broken for MQTT. |
| `mosquitto_sub -h localhost -u … -P … -t "#" -v` (short timeout) | **No** `not authorised` → credentials accepted; **no messages** in the listen window (empty broker traffic from clients). |
| Wrong MQTT password on `mosquitto_sub` | Immediate **`Connection Refused: not authorised`** — confirms broker auth is enforced and distinguishes “bad password” from “no publishers”. |

### What works vs what does not

**Works**

- **Mosquitto** on the Pi: service running, auth enabled, local clients with correct user/password can connect.
- **airlab_collector.py** path: reaches broker, subscribes to `{MQTT_BASE_TOPIC}/#` (logs showed `airlab/#` with default base topic).
- **aranet4-dash** cron: sustained successful BLE reads aside from occasional single-minute misses.

**Does not work (symptom)**

- **No MQTT payloads** from the AirLab (or none under the configured base topic) for extended periods → collector times out after 30s every run.

**Ruled out (for that incident)**

- “Reboot broke Mosquitto” — service was up.
- “aranet4-dash repo changes broke airlab” — different stack (BLE vs MQTT); no causal link in logs.
- “Collector cannot authenticate” — connection + subscribe succeed; anonymous subscribe correctly fails with `not authorised`.

**Most likely cause**

- **Device/network path**: AirLab not publishing (USB power vs battery, WiFi 2.4GHz / disconnect, MQTT host or credentials in **Air Lab Studio** not matching the Pi broker, or base topic mismatch vs `MQTT_BASE_TOPIC` in `.env`).

### How to learn the real topics

1. **Config**: `MQTT_BASE_TOPIC` in `~/dev/airlab-dash/.env` must match Studio. Expected suffixes: `co2`, `tmp`, `hum`, `prs`, `voc`, `nox` under `{base}/…` (see `FIELD_ALIASES` in `airlab_collector.py`).
2. **Discovery**: on the Pi, `cd ~/dev/airlab-dash && uv run discover.py` (optional: `--topic "airlab/#"`) prints every **topic** and payload received.
3. **Manual tap**: `mosquitto_sub -h localhost -u USER -P PASS -t "airlab/#" -v` (narrower than `#` on a busy LAN).

### Commands reference (Pi / `ssh pian`)

```bash
# Broker
systemctl status mosquitto --no-pager
sudo journalctl -u mosquitto --since today --no-pager

# Collector log
tail -f ~/dev/airlab-dash/cron.log

# Listen for any client traffic (use real credentials; do not commit them)
timeout 30 mosquitto_sub -h localhost -p 1883 -u MQTT_USER -P 'MQTT_PASS' -t "airlab/#" -v

# Publish a test (subscriber should print it)
mosquitto_pub -h localhost -p 1883 -u MQTT_USER -P 'MQTT_PASS' -t "airlab/test" -m "hello"
```

### What to try next (ordered)

1. **AirLab on USB-C** (not battery-only) so WiFi/MQTT stays active.
2. In **Air Lab Studio**: confirm WiFi connected, MQTT **host** = Pi’s reachable IP or hostname, **port** 1883, **user/password** match Mosquitto and `.env`, **base topic** matches `MQTT_BASE_TOPIC`.
3. Run **`uv run discover.py`** on the Pi while the AirLab should be publishing; if nothing prints, the problem is still upstream of this repo.
4. If Studio shows connected but discover shows nothing, compare **base topic** character-for-character (typo / old `homeassistant/…` only is a different pattern).
5. **Router**: 2.4GHz-only constraint for AirLab; Pi Ethernet vs WiFi does not change AirLab’s need for 2.4GHz WiFi to the same LAN as the broker.

### Security note

MQTT passwords were used in a live chat during debugging. If transcripts are retained, **rotate** the Mosquitto password (`mosquitto_passwd`), update **`/etc/mosquitto/`** user file, **`.env`**, and **Air Lab Studio** together.

## Follow-up investigation (2026-04-17 19:33-19:36 BST)

Fresh live checks on `pian` confirmed the same failure mode:

- Pi is up since **2026-04-17 14:13 BST**; Mosquitto is listening on `0.0.0.0:1883` and `[::]:1883`.
- Current Pi addresses include LAN `192.168.1.155` and Tailscale `100.72.45.59`.
- SQLite latest real dashboard row is still **2026-04-17 14:13:09 BST** (`id=61332`, `co2_ppm=696`).
- Cron still runs every minute and connects/subscribes successfully, but logs `No data received within 30s`.
- `uv run discover.py --topic "#"` connected to Mosquitto for 20s and saw **zero MQTT messages**.
- Synthetic MQTT publish to the expected `{MQTT_BASE_TOPIC}/co2`, `tmp`, `hum`, `prs`, `voc`, `nox` topics was received by `airlab_collector.py` and saved to a temporary DB. This proves broker auth, topic parsing, insertion, and the collector path still work.

Conclusion at that point: the remaining fault was upstream of this repo. The AirLab device was not publishing to the Pi broker after the reboot, or was publishing to a different host/network. First physical fix to try: keep AirLab on USB-C, power-cycle/reconnect it, then use Air Lab Studio to verify MQTT host is the current Pi LAN IP (`192.168.1.155`) or a hostname the device can resolve, port `1883`, matching credentials, and base topic `airlab`.

Recovery observed shortly after:

- Air Lab Studio MQTT settings matched the Pi: host `192.168.1.155`, port `1883`, username set, base topic `airlab`.
- `Prevent Sleep` was visible as **off** in Studio; enable it and press **Configure** when recovering this failure mode.
- Live MQTT discovery then showed messages every ~5s on `airlab/co2`, `airlab/tmp`, `airlab/hum`, `airlab/voc`, `airlab/nox`, `airlab/prs`, plus `airlab/usb=ON` and `airlab/chg=ON`.
- Manual collector recovery command:

```bash
cd ~/dev/airlab-dash
uv run airlab_collector.py --single
```

- The manual run at **2026-04-17 19:42 BST** saved fresh readings again (`co2_ppm=532`, `temperature_c=23.5`, `humidity_percent=37.5`, `pressure_hpa=1016`, `voc_index=32`, `nox_index=2`). Cron should continue normal minute-by-minute ingestion after that.
