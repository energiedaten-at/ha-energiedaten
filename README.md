# energiedaten.at — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Import Austrian smart meter energy data from [energiedaten.at](https://energiedaten.at) into Home Assistant's Energy Dashboard.

## Features

- **Quarter-hourly energy data** — consumption and feed-in (Einspeisung) per meter
- **Energy Dashboard ready** — cumulative kWh statistics appear directly in HA's energy panel
- **Automatic polling** — fetches new data every 6 hours
- **Manual refresh** — button entity to trigger an immediate update
- **Multi-meter support** — select which meters to import during setup
- **Incremental import** — only fetches new data since the last successful sync
- **Reauth support** — prompts to update expired API tokens

## Requirements

- Home Assistant **2025.12.0** or newer
- An [energiedaten.at](https://energiedaten.at) account with at least one connected smart meter
- An API token with `meters:read` and `data:read` scopes

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/energiedaten/ha-energiedaten` as an **Integration**
4. Search for "energiedaten.at" and install
5. Restart Home Assistant

### Manual

Copy `custom_components/energiedaten/` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **energiedaten.at**
3. Enter your **API Token** and **Team Slug** (visible in your energiedaten.at URL)
4. Select which meters to import (all connected meters are pre-selected)

### Energy Dashboard Setup

After installation, add your sensors to the Energy Dashboard:

1. Go to **Settings → Dashboards → Energy**
2. Add consumption sensors under **Grid consumption**
3. Add feed-in sensors under **Return to grid**

## Entities

### Sensors

One sensor per selected meter:

| Direction | Name Pattern | Example |
|---|---|---|
| Consumption | `{label} Consumption` | Wohnung Consumption |
| Feed-in | `{label} Feed-in` | PV Anlage Feed-in |

Each sensor exposes these attributes:

| Attribute | Description |
|---|---|
| `metering_point` | Full 33-character Zählpunkt ID |
| `energy_direction` | `consumption` or `feed_in` |
| `granularity` | Data resolution (`quarter_hour`) |
| `data_quality` | Quality of last reading (e.g., `measured`) |
| `last_data_at` | Timestamp of most recent data point |

### Button

- **Refresh** — manually triggers a data fetch from energiedaten.at

## How It Works

The integration uses a `DataUpdateCoordinator` that polls the energiedaten.at API every 6 hours. Readings are quarter-hourly kWh values which get converted to cumulative sums and written to Home Assistant's long-term statistics database via [ha-historical-sensor](https://github.com/ldotlopez/ha-historical-sensor). Data appears in the Energy Dashboard, not as real-time entity states.

> **Note:** energiedaten.at delivers data in batches (typically once daily). The 6-hour polling interval ensures timely pickup without hitting API rate limits.

## API Rate Limits

| Plan | Meters | Data Retention | Rate Limit |
|---|---|---|---|
| Community (free) | 1 | 90 days | 100 req/hour |
| Starter+ | Multiple | Extended | 100 req/hour |

If rate-limited, the integration logs a warning and retries on the next polling cycle.

## Troubleshooting

**No data in Energy Dashboard?**
- Data may take up to 24 hours to appear after initial setup (depends on energiedaten.at delivery)
- Verify your meters show status "connected" in energiedaten.at
- Check logs for `custom_components.energiedaten` entries

**Re-authentication required?**
- Your API token may have expired — create a new one at energiedaten.at → Settings → API Tokens

## License

MIT
