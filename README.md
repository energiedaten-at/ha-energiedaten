<p align="center">
  <img src="custom_components/energiedaten/logo.png" alt="energiedaten.at" width="128">
</p>

<h1 align="center">energiedaten.at — Home Assistant Integration</h1>

<p align="center">
  Austrian smart meter data in your Energy Dashboard.<br>
  Powered by <a href="https://energiedaten.at">energiedaten.at</a> · Smart Meter Daten. Einfach nutzbar.
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Default-41BDF5.svg" alt="HACS Default"></a>
  <img src="https://img.shields.io/badge/status-beta-orange" alt="Beta">
  <a href="https://github.com/energiedaten-at/ha-energiedaten/issues"><img src="https://img.shields.io/github/issues/energiedaten-at/ha-energiedaten" alt="Issues"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/energiedaten-at/ha-energiedaten" alt="License"></a>
</p>

---

> **Beta**: this integration is under active development. It works, but expect rough edges. We welcome [bug reports](https://github.com/energiedaten-at/ha-energiedaten/issues/new?template=bug_report.yml) and [contributions](CONTRIBUTING.md).

## What It Does

This integration imports your Austrian smart meter energy data from [energiedaten.at](https://energiedaten.at) into Home Assistant's Energy Dashboard. You get quarter-hourly consumption and feed-in readings with no scraping or CSV exports to manage.

[energiedaten.at](https://energiedaten.at) handles the complexity of Austria's energy data infrastructure (EDA network) so you don't have to. Register your meter, give consent, and receive data. This integration brings that data into Home Assistant.

## Features

- **Quarter-hourly energy data** for consumption and feed-in (Einspeisung) per meter
- **Energy Dashboard ready**: cumulative kWh statistics appear directly in HA's energy panel
- **Automatic polling** every 6 hours with incremental sync
- **Manual refresh** button to trigger an immediate update
- **Re-import service** to re-fetch all historical data on demand (Developer Tools → Services)
- **Correction detection**: when the grid operator re-sends corrected readings, affected days are automatically re-fetched
- **Multi-meter support**: select which meters to import during setup
- **Reauth support** for expired API tokens

## Requirements

| Requirement | Details |
|---|---|
| Home Assistant | **2025.12.0** or newer |
| energiedaten.at account | [Sign up free](https://energiedaten.at), 1 meter included on the Community plan |
| API key | With `meters:read` and `data:read` scopes ([API quickstart](https://energiedaten.at/docs/api#tag/quickstart)). Keys expire; the Community plan caps at 365 days. |

## Installation

### HACS (Recommended)

energiedaten.at is now a **default HACS integration**, so you don't need to add a custom repository.

1. Open **HACS** in Home Assistant
2. Search for **energiedaten.at**
3. Open it and click **Download**
4. Restart Home Assistant

Or jump straight there with the one-click button:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=energiedaten-at&repository=ha-energiedaten&category=integration)

### Manual

Copy `custom_components/energiedaten/` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **energiedaten.at**
3. Enter your **API Key**
4. Select which meters to import (all connected meters are pre-selected)

### Energy Dashboard Setup

After installation, add your sensors to the Energy Dashboard:

1. Go to **Settings → Dashboards → Energy**
2. Add consumption sensors under **Grid consumption**
3. Add feed-in sensors under **Return to grid**

## Entities

### Sensors

One sensor per selected meter and OBIS code:

| Direction | Name Pattern | Example |
|---|---|---|
| Consumption | `{label} Consumption` | Wohnung Consumption |
| Feed-in | `{label} Feed-in` | PV Anlage Feed-in |

Each sensor's state shows the latest quarter-hour reading in kWh. It also writes cumulative long-term statistics for the Energy Dashboard.

Sensor attributes:

| Attribute | Description |
|---|---|
| `metering_point` | Full 33-character Zählpunkt ID |
| `energy_direction` | `consumption` or `feed_in` |
| `granularity` | Data resolution (`quarter_hour`) |
| `data_quality` | Quality of last reading (e.g., `measured`) |
| `last_data_at` | Timestamp of most recent data point |

### Button

- **Refresh**: manually triggers a data fetch from energiedaten.at

### Services

- **`energiedaten.reimport`**: resets sync progress and re-fetches all historical meter data. Existing statistics are overwritten, not duplicated. Call it from **Developer Tools → Services**.

## How It Works

energiedaten.at delivers smart meter data in batches (typically once daily) via Austria's regulated EDA network. This integration polls the energiedaten.at API every 6 hours, converts the quarter-hourly kWh readings into cumulative statistics, and writes them to Home Assistant's long-term statistics database using `async_add_external_statistics()`.

Statistics use external statistic IDs in the format `energiedaten:{metering_point}_{obis_name}` and appear in the **Energy Dashboard**. EDA data is historical, not live, so there is a natural delay of up to 24 hours.

## API Rate Limits

| Plan | Meters | Data Retention | Rate Limit |
|---|---|---|---|
| Community (free) | 1 | 90 days | 10 req/hour |
| Starter (€29/mo) | 10 | 365 days | 100 req/hour |
| Growing (€89/mo) | 40 | 2 years | 100 req/hour |

If rate-limited, the integration logs a warning and retries on the next polling cycle. See [energiedaten.at pricing](https://energiedaten.at/en/pricing) for full details.

## Troubleshooting

**No data in Energy Dashboard?**
- Data may take up to 24 hours to appear after initial setup (depends on EDA delivery schedule)
- Verify your meters show status "connected" in energiedaten.at
- Check logs: **Settings → System → Logs**, filter for `custom_components.energiedaten`

**Upgraded from 0.2.x?**
- v0.3.0 changed statistic IDs from entity-based to external format. After upgrading, go to **Settings → Dashboards → Energy** and re-select your statistics.
- The first sync after upgrade re-fetches all available history.

**Upgraded to 0.4.x from 0.3.x?**
- The team-slug field is gone; the team is now derived from your API key. Existing setups are migrated on first load, with no user action required.

**Want to re-import all data?**
- Use the `energiedaten.reimport` service in **Developer Tools → Services**. This resets sync progress and triggers a full re-fetch.

**Re-authentication required?**
- Your API key may have expired (Community plan caps at 365 days). See the [API quickstart](https://energiedaten.at/docs/api#tag/quickstart) for how to create a new one.

**Something else?**
- [Open a bug report](https://github.com/energiedaten-at/ha-energiedaten/issues/new?template=bug_report.yml)
- Check [existing issues](https://github.com/energiedaten-at/ha-energiedaten/issues) for similar problems

## Contributing

This is an open-source project and we welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Reporting bugs
- Suggesting features
- Submitting pull requests

## About energiedaten.at

[energiedaten.at](https://energiedaten.at) is Austria's developer-friendly smart meter data platform. We handle the complexity of the EDA network (consent management, data retrieval, format conversion) so you can focus on what you build with the data.

- **Website:** [energiedaten.at](https://energiedaten.at)
- **Questions about the platform:** [phillip.fickl@energiedaten.at](mailto:phillip.fickl@energiedaten.at)
- **Integration issues:** [GitHub Issues](https://github.com/energiedaten-at/ha-energiedaten/issues)

## License

[MIT](LICENSE)
