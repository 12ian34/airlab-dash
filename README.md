# AirLab MQTT Data Logger

Read CO2, temperature, humidity, pressure, VOC, and NOx from a [Networked Artifacts AirLab](https://networkedartifacts.com/airlab) via MQTT and log to a local SQLite database. Visualise with Grafana.

The [AirLab](https://www.crowdsupply.com/networked-artifacts/air-lab) is a portable, open-source air quality monitor built by [Networked Artifacts](https://networkedartifacts.com) (shoutout to Joël and the team). It measures CO2, temperature, humidity, atmospheric pressure, VOCs, and NOx using Sensirion sensors (SCD41 + SGP41) and an LPS22 pressure sensor, and publishes data over WiFi via MQTT. Check out their [Crowd Supply page](https://www.crowdsupply.com/networked-artifacts/air-lab) to get one, and the [device manual](https://networkedartifacts.com/manuals/airlab/device-overview) for full details.

> **Note:** The SGP41 VOC/NOx sensor needs ~1 hour to warm up before readings are reliable. The VOC and NOx Index algorithms also need ~24 hours of continuous operation to learn a baseline — early readings are relative to limited history, so don't worry if they look odd at first.

Designed to run on a Raspberry Pi via crontab, alongside [aranet4-dash](https://github.com/12ian34/aranet4-dash). Same pattern.

## Tech stack

- Python 3.9+ managed with [uv](https://docs.astral.sh/uv/)
- [paho-mqtt](https://pypi.org/project/paho-mqtt/) (MQTT client) + [python-dotenv](https://pypi.org/project/python-dotenv/) (config)
- SQLite via stdlib `sqlite3`
- [Mosquitto](https://mosquitto.org/) MQTT broker on the Pi
- [Grafana](https://grafana.com/) + [frser-sqlite-datasource](https://github.com/fr-ser/grafana-sqlite-datasource) plugin
- crontab for scheduling (every minute, `--single` mode)

## Prerequisites

- Raspberry Pi with Raspberry Pi OS (Bookworm or later)
- [Mosquitto](https://mosquitto.org/) MQTT broker installed on the Pi
- Python 3.9+ (pre-installed on Bookworm)
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [Grafana](https://grafana.com/docs/grafana/latest/setup-grafana/installation/debian/) + [SQLite datasource plugin](https://github.com/fr-ser/grafana-sqlite-datasource)
- An AirLab on the same WiFi network as the Pi (**must be 2.4GHz** — the AirLab does not support 5GHz WiFi)
- The AirLab **must be plugged into USB-C power** for continuous MQTT — on battery it sleeps and stops publishing regardless of MQTT connection state (confirmed by the AirLab team)

## 1. Install Mosquitto

```sh
sudo apt-get update
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable mosquitto
```

Mosquitto is now running on port 1883.

### Set up Mosquitto authentication

Mosquitto 2.0+ rejects connections without auth by default. If you set a username/password in Air Lab Studio, you need to create matching credentials in Mosquitto:

```sh
# Create password file (enter your password when prompted)
sudo mosquitto_passwd -c /etc/mosquitto/passwd myuser

# Configure Mosquitto to use it
echo 'listener 1883
password_file /etc/mosquitto/passwd
allow_anonymous false' | sudo tee /etc/mosquitto/conf.d/auth.conf

# Restart
sudo systemctl restart mosquitto
```

Use the same username/password in your `.env` and in Air Lab Studio.

Verify it's working:

```sh
mosquitto_sub -h localhost -u myuser -P mypassword -t "test" &
mosquitto_pub -h localhost -u myuser -P mypassword -t "test" -m "hello"
# Should print "hello", then: kill %1
```

## 2. Clone and install

```sh
git clone https://github.com/12ian34/airlab-dash.git
cd airlab-dash
uv sync
```

## 3. Create the database directory

```sh
sudo mkdir -p /var/lib/airlab-dash
sudo chown $USER:grafana /var/lib/airlab-dash
chmod 750 /var/lib/airlab-dash
```

## 4. Configure

```sh
cp .env.example .env
nano .env
```

```env
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
MQTT_BASE_TOPIC=airlab
DB_PATH=/var/lib/airlab-dash/airlab.db
```

- `MQTT_HOST` — `localhost` since Mosquitto runs on the Pi
- `MQTT_PORT` — `1883` (default)
- `MQTT_USERNAME` / `MQTT_PASSWORD` — must match what you set in Mosquitto and Air Lab Studio (see step 1)
- `MQTT_BASE_TOPIC` — must match what you set in Air Lab Studio
- `DB_PATH` — where to store the SQLite database

## 5. Configure your AirLab

The AirLab is configured over Bluetooth using a web app. You need a Chrome-based browser (Chrome, Edge, Brave — not Firefox/Safari) on a computer with Bluetooth.

### Initial device setup

1. Plug the AirLab into USB-C power and press button **A** when prompted on the e-paper display
2. It will ask for the time, then ask you to place it outside briefly for sensor calibration
3. Bring it back inside — it's now logging locally

### WiFi setup

1. Go to [Air Lab Studio](https://airlab.networkedartifacts.com)
2. Click **Connect** and select your AirLab from the Bluetooth dropdown
3. In the sidebar, navigate to **Settings**
4. Under **Wi-Fi**, enter your network SSID and password, then click **Configure**

> **Important:** The AirLab only supports **2.4GHz WiFi**. If your router broadcasts both 2.4GHz and 5GHz under the same SSID, you may need to temporarily separate them or connect to the 2.4GHz-specific SSID.

### MQTT setup

Still in Air Lab Studio Settings, under **MQTT**:

| Field | Value |
|-------|-------|
| **Host** | Your Pi's local IP (e.g. `192.168.1.50` — find it with `hostname -I` on the Pi) |
| **Port** | `1883` |
| **Username** | Same as Mosquitto auth (step 1) |
| **Password** | Same as Mosquitto auth (step 1) |
| **Base Topic** | `airlab` (must match `.env`) |

Click **Configure**. The connection status in the sidebar should change to **Networked**. If it stays at:
- **Disconnected** — check WiFi settings (is it 2.4GHz?)
- **Connected** — WiFi works but MQTT failed (check broker IP, port, and credentials)

## 6. Discover topics

Run the discovery helper to see what your AirLab publishes:

```sh
uv run discover.py
```

You should see messages appear. Press Ctrl+C to stop. You can also use mosquitto_sub directly:

```sh
mosquitto_sub -h localhost -t "airlab/#" -v
```

## 7. Test a single reading

```sh
uv run airlab_collector.py --single
```

You should see output like:

```
2026-02-12 20:00:00 INFO Connecting to localhost:1883 ...
2026-02-12 20:00:00 INFO Connected to MQTT broker, subscribing to airlab/#
2026-02-12 20:00:01 INFO Received: {'co2_ppm': 650.0, 'temperature_c': 22.5, ...}
2026-02-12 20:00:04 INFO Saved: co2_ppm=650.0  humidity_percent=45.0  ...
```

## 8. Verify the database

```sh
sqlite3 /var/lib/airlab-dash/airlab.db
```

```sql
SELECT * FROM airlab_readings ORDER BY timestamp DESC LIMIT 5;
.quit
```

## 9. Set up crontab

```sh
crontab -e
```

Add this line to poll every minute:

```cron
* * * * * cd $HOME/dev/airlab-dash && uv run airlab_collector.py --single >> $HOME/dev/airlab-dash/cron.log 2>&1
```

Make sure the `PATH` line from your aranet4-dash crontab is present (so cron can find `uv`):

```cron
PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
```

Verify:

```sh
crontab -l
tail -f ~/dev/airlab-dash/cron.log
```

## 10. Configure Grafana datasource

If you already have the SQLite plugin from aranet4-dash, just add a second datasource:

1. Open Grafana at `http://<pi-ip>:3000`
2. Go to **Connections > Data sources > Add data source**
3. Search for **SQLite**
4. Set the path to `/var/lib/airlab-dash/airlab.db`
5. Click **Save & test**

## 11. Import the dashboard

1. In Grafana, go to **Dashboards > New > Import**
2. Upload `grafana/dashboard.json` from this repo
3. Select your AirLab SQLite datasource when prompted
4. Click **Import**

The dashboard includes:

- **Bar gauge** — latest reading for CO2, Temperature, Humidity, Pressure, VOC, NOx with colour-coded thresholds, plus "Last Updated" timestamp
- **CO2 time series** — with green/yellow/red threshold bands at 800/1000 ppm
- **Temperature time series** — comfort-zone threshold bands
- **Humidity time series** — threshold bands
- **Pressure time series** — blue line
- **VOC index time series** — with threshold bands
- **NOx index time series** — with threshold bands

## Database schema

```sql
CREATE TABLE IF NOT EXISTS airlab_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    co2_ppm REAL,
    temperature_c REAL,
    humidity_percent REAL,
    pressure_hpa REAL,
    voc_index REAL,
    nox_index REAL
);

CREATE INDEX IF NOT EXISTS idx_airlab_timestamp ON airlab_readings(timestamp);
```

## Troubleshooting

### AirLab stops publishing after a minute

The AirLab **must be plugged into USB-C power** to continuously publish via MQTT. On battery, it goes to sleep and stops WiFi/MQTT regardless of connection state. Only BLE connections keep it awake on battery (confirmed by the AirLab team). Plug it in and it will publish at the record sample rate (default 5 seconds).

### AirLab won't connect to WiFi

The AirLab only supports **2.4GHz WiFi**. It will not see or connect to 5GHz networks. If your router uses the same SSID for both bands, try:
- Connecting to the 2.4GHz-specific SSID (often has `-2G` or `2.4` in the name)
- Temporarily disabling 5GHz in your router settings
- Checking your router's client list to confirm the AirLab connected on 2.4GHz

### AirLab shows "Connected" but not "Networked"

WiFi works but can't reach the MQTT broker. Check:
- Pi's IP address is correct in Air Lab Studio
- Mosquitto is running: `sudo systemctl status mosquitto`
- Port 1883 not blocked: `sudo ufw allow 1883` (if using ufw)

### No messages in discover.py

- Verify the AirLab is powered on and shows "Networked"
- Check the base topic matches between Air Lab Studio and `.env`
- Try subscribing to everything: `mosquitto_sub -h localhost -t "#" -v`

### Collector running but no data in Grafana

- Check the cron log: `tail -20 ~/dev/airlab-dash/cron.log`
- Verify the database: `sqlite3 /var/lib/airlab-dash/airlab.db "SELECT * FROM airlab_readings ORDER BY id DESC LIMIT 5;"`
- Make sure the Grafana SQLite datasource path matches `DB_PATH`

### Grafana can't read the database

Same fix as aranet4-dash — check permissions:

```sh
ls -la /var/lib/airlab-dash/
sudo chown $USER:grafana /var/lib/airlab-dash /var/lib/airlab-dash/airlab.db
chmod 750 /var/lib/airlab-dash
chmod 640 /var/lib/airlab-dash/airlab.db
```

### AirLab says "MQTT Disconnected"

Mosquitto 2.0+ rejects unauthenticated connections by default. If you set a username/password in Air Lab Studio, you must create matching credentials in Mosquitto (see step 1). Make sure:
- The Mosquitto password file exists: `ls /etc/mosquitto/passwd`
- The auth config exists: `cat /etc/mosquitto/conf.d/auth.conf`
- The username/password in `.env` and Air Lab Studio match what you set in `mosquitto_passwd`
- Restart after changes: `sudo systemctl restart mosquitto`
