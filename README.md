<p align="center">
  <img src="custom_components/energiedaten/logo.png" alt="energiedaten.at" width="128">
</p>

<h1 align="center">energiedaten.at — Home Assistant Integration</h1>

<p align="center">
  Austrian smart meter data in your Energy Dashboard.<br>
  Powered by <a href="https://energiedaten.at">energiedaten.at</a> — Smart Meter Daten. Einfach nutzbar.
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Beta">
  <a href="https://github.com/energiedaten-at/ha-energiedaten/issues"><img src="https://img.shields.io/github/issues/energiedaten-at/ha-energiedaten" alt="Issues"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/energiedaten-at/ha-energiedaten" alt="License"></a>
</p>

---

> **Beta** — This integration is under active development. It works, but expect rough edges. We welcome [bug reports](https://github.com/energiedaten-at/ha-energiedaten/issues/new?template=bug_report.yml) and [contributions](CONTRIBUTING.md).

## What It Does

This integration imports your Austrian smart meter energy data from [energiedaten.at](https://energiedaten.at) into Home Assistant's Energy Dashboard. No scraping, no manual CSV uploads — just quarter-hourly consumption and feed-in data, delivered automatically.

[energiedaten.at](https://energiedaten.at) handles the complexity of Austria's energy data infrastructure (EDA network) so you don't have to. You register your meter, give consent, and receive data. This integration brings that data into Home Assistant.

## Features

- **Quarter-hourly energy data** — consumption and feed-in (Einspeisung) per meter
- **Energy Dashboard ready** — cumulative kWh statistics appear directly in HA's energy panel
- **Automatic polling** — fetches new data every 6 hours
- **Manual refresh** — button entity to trigger an immediate update
- **Multi-meter support** — select which meters to import during setup
- **Incremental import** — only fetches new data since the last successful sync
- **Reauth support** — prompts to update expired API tokens

## Requirements

| Requirement | Details |
|---|---|
| Home Assistant | **2025.12.0** or newer |
| energiedaten.at account | [Sign up free](https://energiedaten.at) — 1 meter included on the Community plan |
| API token | With `meters:read` and `data:read` scopes ([create one here](https://energiedaten.at/settings/api-tokens)) |

## Installation

### HACS (Recommended)

1. Open **HACS** in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/energiedaten-at/ha-energiedaten` as an **Integration**
4. Search for **energiedaten.at** and install
5. Restart Home Assistant

### Manual

Copy `custom_components/energiedaten/` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **energiedaten.at**
3. Enter your **API Token** and **Team Slug** (visible in your energiedaten.at URL, e.g. `energiedaten.at/teams/mein-haushalt` → slug is `mein-haushalt`)
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

energiedaten.at delivers smart meter data in batches (typically once daily) via Austria's regulated EDA network. This integration polls the energiedaten.at API every 6 hours, converts the quarter-hourly kWh readings into cumulative statistics, and writes them to Home Assistant's long-term statistics database via [ha-historical-sensor](https://github.com/ldotlopez/ha-historical-sensor).

Data appears in the **Energy Dashboard**, not as real-time entity states. This is by design — EDA data is historical, not live.

## API Rate Limits

| Plan | Meters | Data Retention | Rate Limit |
|---|---|---|---|
| Community (free) | 1 | 90 days | 10 req/hour |
| Starter (€29/mo) | 10 | 365 days | 100 req/hour |
| Growing (€89/mo) | 40 | 2 years | 100 req/hour |

If rate-limited, the integration logs a warning and retries on the next polling cycle. See [energiedaten.at pricing](https://energiedaten.at/pricing) for full details.

## Troubleshooting

**No data in Energy Dashboard?**
- Data may take up to 24 hours to appear after initial setup (depends on EDA delivery schedule)
- Verify your meters show status "connected" in energiedaten.at
- Check logs: **Settings → System → Logs**, filter for `custom_components.energiedaten`

**Re-authentication required?**
- Your API token may have expired — create a new one at [energiedaten.at → Settings → API Tokens](https://energiedaten.at/settings/api-tokens)

**Something else?**
- [Open a bug report](https://github.com/energiedaten-at/ha-energiedaten/issues/new?template=bug_report.yml)
- Check [existing issues](https://github.com/energiedaten-at/ha-energiedaten/issues) — someone may have hit the same problem

## Contributing

This is an open-source project and we welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Reporting bugs
- Suggesting features
- Submitting pull requests

## About energiedaten.at

[energiedaten.at](https://energiedaten.at) is Austria's developer-friendly smart meter data platform. We handle the complexity of the EDA network — consent management, data retrieval, format conversion — so you can focus on what you build with the data.

- **Website:** [energiedaten.at](https://energiedaten.at)
- **Questions about the platform:** [phillip.fickl@energiedaten.at](mailto:phillip.fickl@energiedaten.at)
- **Integration issues:** [GitHub Issues](https://github.com/energiedaten-at/ha-energiedaten/issues)

## License

[MIT](LICENSE)
