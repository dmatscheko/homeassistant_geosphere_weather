# GeoSphere Austria Weather for Home Assistant

A Home Assistant custom integration that provides weather forecasts for Austria
and the surrounding Alpine region, based on the open
[GeoSphere Austria Data Hub](https://data.hub.geosphere.at/) API.

## Features

- Four selectable GeoSphere datasets, chosen when you add the integration:

  | Model | Dataset ID | Horizon | Step | Recalculation interval | Grid size | HA platform | Highlights |
  |-------|------------|---------|------|------------------------|-----------|-------------|------------|
  | **AROME** | [`nwp-v1-1h-2500m`](https://data.hub.geosphere.at/dataset/nwp-v1-1h-2500m) | 60 h | 1 h | 3 h | 2.5 km | `weather` | High-resolution weather model for temperature, precipitation, wind, humidity, thunderstorms, cloud cover, and pressure |
  | **C-LAEF** | [`ensemble-v1-1h-2500m`](https://data.hub.geosphere.at/dataset/ensemble-v1-1h-2500m) | 60 h | 1 h | 12 h | 2.5 km | `weather` | Ensemble forecast for temperature, precipitation, wind, and cloud cover (derived from p10/p50/p90) |
  | **INCA** | [`nowcast-v1-15min-1km`](https://data.hub.geosphere.at/dataset/nowcast-v1-15min-1km) | 3 h | 15 min | 15 min | 1 km | `weather` | Short-term forecast (nowcast) for temperature, precipitation, wind, and humidity |
  | **WRF-Chem** | [`chem-v2-1h-3km`](https://data.hub.geosphere.at/dataset/chem-v2-1h-3km) | 73 h | 1 h | 24 h | 3 km | `sensor` | Pollutant forecast for NO₂, O₃, PM10, and PM2.5 |

- Location is chosen as a Home Assistant zone. Its latitude/longitude are
  forwarded to the API as `lat_lon=<lat>,<lon>`.
- A bounding-box check in the config flow prevents adding locations outside
  the selected dataset's coverage area.
- Weather datasets create a single `weather` entity with current conditions
  plus hourly forecast, and (for AROME / C-LAEF) a daily forecast
  aggregated client-side. AROME additionally exposes a `weather_symbol`
  enum sensor with the fine-grained model symbol (e.g. "Heavy rain shower",
  "Thunderstorm with snow") — fully translatable via Home Assistant's
  i18n system. INCA exposes an analogous `precipitation_type` sensor.
  The air-quality dataset creates four `sensor` entities (one per pollutant).
- Each created device exposes a **link to the selected dataset's documentation
  page on the GeoSphere Data Hub** via its `configuration_url` — click the
  device card in *Settings → Devices & Services* to open it.
- You can add the integration multiple times for the same zone — e.g. once
  with C-LAEF for a multi-day weather outlook, once with INCA for a
  high-frequency nowcast, and once with WRF-Chem for air quality.

## Installation

### HACS (recommended)

1. In HACS, open *Integrations* → menu → *Custom repositories*, add
   `https://github.com/dmatscheko/homeassistant_geosphere_weather` with category
   *Integration*.
2. Install *GeoSphere Austria Weather* and restart Home Assistant.
3. *Settings → Devices & Services → Add Integration* → search for
   "GeoSphere Austria Weather".

### Manual

Copy `custom_components/geosphere_weather/` into your Home Assistant
configuration directory (`config/custom_components/geosphere_weather/`) and
restart Home Assistant. Then add the integration from
*Settings → Devices & Services → Add Integration → "GeoSphere Austria Weather"*.

## Removal

*Settings → Devices & Services*, open the GeoSphere Austria entry and choose
*Delete*. If installed via HACS, uninstall through HACS and restart Home
Assistant; if installed manually, delete `custom_components/geosphere_weather/`
and restart.

## Development

This project uses [uv](https://docs.astral.sh/uv/) for Python environment and
dependency management. Install uv with:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Sync the dev environment once:

```bash
uv sync --group dev
```

Then run the quality gates exactly as CI does — all commands run inside the
uv-managed virtualenv, no manual activation needed:

```bash
uv run ruff check custom_components tests
uv run mypy --strict custom_components/geosphere_weather
uv run pytest
```

CI on GitHub Actions runs the same three commands on every push and pull
request via [.github/workflows/ci.yml](.github/workflows/ci.yml).

## Attribution & license

Weather data: © [GeoSphere Austria](https://data.hub.geosphere.at/), licensed
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The Data Hub
API currently requires no authentication and is rate-limited to roughly
5 requests per second / 240 per hour per IP.

Integration code: licensed under the Apache License 2.0 (see [`LICENSE`](LICENSE.md)),
partially modelled after the official
[`open_meteo`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/open_meteo)
integration.

## Notes & caveats

- The AROME `sy` (weather symbol) codes are mapped to Home Assistant
  conditions using the [published legend](https://github.com/Geosphere-Austria/dataset-api-docs/issues/30#issuecomment-2042539848).
  For datasets without a symbol code (C-LAEF, INCA), the condition is
  derived from cloud cover and precipitation instead. The full 32-state
  AROME symbol is exposed as a separate `sensor.<title>_weather_symbol`
  entity whose state is translated by Home Assistant.
- The API snaps the requested coordinates to the nearest model grid cell
  (≈2.5 km for AROME, ≈1 km for INCA). Do not expect metre-precision.
- AROME is re-run every 3 hours and C-LAEF every 12 hours; the integration polls every
  30 minutes. INCA is re-run every 15 minutes; the nowcast is polled every
  10 minutes. WRF-Chem chemistry is polled every 60 minutes.
- The WRF-Chem dataset has a different shape from the weather datasets — it
  produces `sensor.<title>_nitrogen_dioxide`, `…_ozone`, `…_particulate_matter_pm10`,
  `…_particulate_matter_pm2_5` entities instead of a `weather.<title>` entity.

/dmatscheko
