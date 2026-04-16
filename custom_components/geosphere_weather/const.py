"""Constants for the GeoSphere Austria weather integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "geosphere_weather"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "GeoSphere Austria"
ATTRIBUTION: Final = (
    "Data provided by GeoSphere Austria (data.hub.geosphere.at, CC BY 4.0)"
)

# Config entry keys
CONF_DATASET: Final = "dataset"

API_BASE_URL: Final = "https://dataset.api.hub.geosphere.at/v1"

# Dataset IDs — these are the API resource IDs used in the request URL
# (`/v1/timeseries/forecast/{resource_id}`).
DATASET_NWP: Final = "nwp-v1-1h-2500m"            # AROME, 60 h @ 1 h
DATASET_ENSEMBLE: Final = "ensemble-v1-1h-2500m"  # C-LAEF, percentile ensemble, 60 h @ 1 h
DATASET_NOWCAST: Final = "nowcast-v1-15min-1km"   # INCA, 3 h @ 15 min
DATASET_CHEM: Final = "chem-v2-1h-3km"            # WRF-Chem air quality, 73 h @ 1 h

SUPPORTED_DATASETS: Final = (
    DATASET_NWP,
    DATASET_ENSEMBLE,
    DATASET_NOWCAST,
    DATASET_CHEM,
)

DEFAULT_DATASET: Final = DATASET_NWP

# Per-dataset platforms. Weather datasets go to the `weather` platform; the
# air-quality dataset produces `sensor` entities instead (no HA WeatherEntity
# slot makes sense for µg/m³ pollutants).
DATASET_PLATFORMS: Final[dict[str, tuple[Platform, ...]]] = {
    DATASET_NWP: (Platform.WEATHER,),
    DATASET_ENSEMBLE: (Platform.WEATHER,),
    DATASET_NOWCAST: (Platform.WEATHER,),
    DATASET_CHEM: (Platform.SENSOR,),
}

# Short display names used in the config-entry title and device model.
DATASET_SHORT_NAMES: Final[dict[str, str]] = {
    DATASET_NWP: "AROME",
    DATASET_ENSEMBLE: "C-LAEF",
    DATASET_NOWCAST: "INCA",
    DATASET_CHEM: "WRF-Chem",
}

# Per-dataset bounding box (min_lat, min_lon, max_lat, max_lon).
DATASET_BBOX: Final[dict[str, tuple[float, float, float, float]]] = {
    DATASET_NWP: (42.98, 5.50, 51.82, 22.10),       # Alpine NWP domain
    DATASET_ENSEMBLE: (42.98, 5.50, 51.82, 22.10),  # Same as AROME
    DATASET_NOWCAST: (45.50, 8.10, 49.48, 17.74),   # Austria + small margin
    DATASET_CHEM: (40.91, 2.90, 53.74, 23.70),      # Central Europe
}

# Per-dataset poll interval.
DATASET_SCAN_INTERVAL: Final[dict[str, timedelta]] = {
    DATASET_NWP: timedelta(minutes=30),
    DATASET_ENSEMBLE: timedelta(minutes=30),
    DATASET_NOWCAST: timedelta(minutes=10),
    DATASET_CHEM: timedelta(minutes=60),
}

# Link to each dataset's page on the GeoSphere Data Hub. Used as the
# `configuration_url` on the created device so users can jump straight to the
# official model description.
DATASET_DOC_URL: Final[dict[str, str]] = {
    ds: f"https://data.hub.geosphere.at/dataset/{ds}" for ds in SUPPORTED_DATASETS
}

# Parameters requested per dataset.
# See `GET /v1/timeseries/forecast/{resource_id}/metadata` for the full list.
NWP_PARAMETERS: Final = (
    "t2m",       # 2 m temperature, °C
    "rh2m",      # 2 m relative humidity, %
    "tcc",       # total cloud cover, fraction 0..1
    "sp",        # surface pressure, Pa
    "rr_acc",    # total precipitation, accumulated since init, mm
    "u10m",      # 10 m eastward wind component, m/s
    "v10m",      # 10 m northward wind component, m/s
    "ugust",     # gust eastward component, m/s
    "vgust",     # gust northward component, m/s
    "sy",        # GeoSphere weather symbol (legend not publicly documented)
    "mnt2m",     # min 2 m temperature in last forecast period, °C
    "mxt2m",     # max 2 m temperature in last forecast period, °C
)

ENSEMBLE_PARAMETERS: Final = (
    "t2m_p10", "t2m_p50", "t2m_p90",
    "tcc_p50",
    "rr_p10", "rr_p50", "rr_p90",
    "u10m_p50", "v10m_p50",
    "mnt2m_p50", "mxt2m_p50",
)
# Note: the ensemble dataset exposes no relative-humidity percentiles, so
# humidity is not available when the C-LAEF model is selected.

NOWCAST_PARAMETERS: Final = (
    "t2m",    # 2 m temperature, °C
    "td",     # 2 m dew point, °C
    "rh2m",   # 2 m relative humidity, %
    "rr",     # 15-min precipitation sum, mm (NOT accumulated)
    "pt",     # precipitation type (legend not publicly documented)
    "ff",     # 10 m scalar wind speed, m/s
    "dd",     # 10 m wind direction, degrees
    "fx",     # 10 m gust speed, m/s
)

CHEM_PARAMETERS: Final = (
    "no2surf",   # NO2 concentration near surface, µg/m³
    "o3surf",    # O3 concentration near surface, µg/m³
    "pm10surf",  # PM10 concentration near surface, µg/m³
    "pm25surf",  # PM2.5 concentration near surface, µg/m³
)

DATASET_PARAMETERS: Final[dict[str, tuple[str, ...]]] = {
    DATASET_NWP: NWP_PARAMETERS,
    DATASET_ENSEMBLE: ENSEMBLE_PARAMETERS,
    DATASET_NOWCAST: NOWCAST_PARAMETERS,
    DATASET_CHEM: CHEM_PARAMETERS,
}
