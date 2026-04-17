---
name: airlab-dash
description: Diagnose and recover the airlab-dash Raspberry Pi MQTT to SQLite to Grafana pipeline, including AirLab Studio settings, Prevent Sleep, Mosquitto checks, collector runs, cron logs, and dashboard data gaps.
---

# AirLab Dash

Use this skill when the AirLab Grafana dashboard is blank, stale, missing several hours, or `airlab_collector.py` logs `No data received within 30s`.

## First Rule

Check `claude.md` first. It contains the latest repo-specific incidents, device gotchas, Pi addresses, and recovery notes.

Do not assume the Python collector is broken just because Grafana is blank. Prove where data stops.

## Pipeline

```text
AirLab sensor -> WiFi -> Mosquitto on pian -> airlab_collector.py -> SQLite -> Grafana
```

Expected topics under the configured base topic:

```text
airlab/co2
airlab/tmp
airlab/hum
airlab/prs
airlab/voc
airlab/nox
```

The AirLab may also publish status topics such as `airlab/bat`, `airlab/usb`, and `airlab/chg`. The collector ignores those.

## Common Root Cause

If the collector connects to Mosquitto and subscribes successfully, but receives no messages, the issue is upstream of this repo:

- AirLab is asleep or on battery behavior.
- AirLab lost WiFi/MQTT after a Pi or device reboot.
- Air Lab Studio settings were not applied.
- MQTT host, credentials, or base topic differ from `.env`.

Known fix from the 2026-04-17 outage:

1. Keep AirLab plugged into USB-C.
2. In Air Lab Studio, set MQTT host to the Pi LAN IP, port `1883`, correct username/password, and base topic `airlab`.
3. Turn **Prevent Sleep** on.
4. Press **Configure**.
5. Confirm MQTT messages appear every ~5s.

## Live Checks

Run these on the Pi via SSH.

```bash
ssh pian 'date; hostname; uptime'
```

Check the latest dashboard rows:

```bash
ssh pian 'sqlite3 /var/lib/airlab-dash/airlab.db "SELECT id, timestamp, co2_ppm, temperature_c, humidity_percent, pressure_hpa, voc_index, nox_index FROM airlab_readings ORDER BY id DESC LIMIT 10;"'
```

Check cron:

```bash
ssh pian 'tail -80 ~/dev/airlab-dash/cron.log'
```

Check Mosquitto:

```bash
ssh pian 'systemctl status mosquitto --no-pager'
ssh pian 'ss -ltnp | grep ":1883" || true'
```

Watch all MQTT traffic without printing secrets:

```bash
ssh pian 'bash -lc "cd ~/dev/airlab-dash && timeout 60 uv run discover.py --topic \"#\""'
```

If discovery shows `airlab/co2`, `airlab/tmp`, `airlab/hum`, `airlab/prs`, `airlab/voc`, and `airlab/nox`, run one collector read:

```bash
ssh pian 'bash -lc "cd ~/dev/airlab-dash && uv run airlab_collector.py --single"'
```

For the user running directly on the Pi:

```bash
cd ~/dev/airlab-dash
uv run airlab_collector.py --single
```

## Prove The Collector Path

Use a temporary DB before blaming code. This proves Mosquitto auth, topic parsing, SQLite insertion, and the collector path without touching the real dashboard DB.

```bash
ssh pian 'bash -lc '"'"'
cd ~/dev/airlab-dash
set -a
source ./.env
set +a
test_db=/tmp/airlab-debug-$(date +%s).db
(
  DB_PATH="$test_db" uv run airlab_collector.py --single
) > /tmp/airlab-debug-collector.log 2>&1 &
collector_pid=$!
sleep 2
for pair in "co2 612" "tmp 21.7" "hum 44.1" "prs 1016" "voc 88" "nox 2"; do
  set -- $pair
  mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD" -t "${MQTT_BASE_TOPIC}/$1" -m "$2"
done
wait "$collector_pid"
cat /tmp/airlab-debug-collector.log
sqlite3 "$test_db" "SELECT id, timestamp, co2_ppm, temperature_c, humidity_percent, pressure_hpa, voc_index, nox_index FROM airlab_readings ORDER BY id DESC LIMIT 1;"
'"'"''
```

## Interpret Results

- **No rows in SQLite, cron says no data, discovery sees no MQTT messages:** device/network/Studio issue. Fix AirLab power, WiFi, Prevent Sleep, and MQTT settings.
- **Discovery sees messages, collector saves a row:** pipeline is back. Grafana may need refresh or a wider time range.
- **Synthetic publish saves to temp DB, but real discovery sees nothing:** collector is fine; AirLab is not publishing to the broker.
- **Mosquitto not listening or auth rejected:** broker config issue, check `/etc/mosquitto/conf.d/auth.conf` and password file.

## Security

Never commit or quote real MQTT passwords. If a password appears in screenshots, logs, or chat transcripts, recommend rotating it once the dashboard is stable.
