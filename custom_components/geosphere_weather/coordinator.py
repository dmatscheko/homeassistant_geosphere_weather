"""Data update coordinator for GeoSphere Austria weather forecasts."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientTimeout
from astral import Observer
from astral.sun import elevation
from homeassistant.components.weather import (
    ATTR_CONDITION_CLEAR_NIGHT,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_BASE_URL,
    DATASET_CHEM,
    DATASET_ENSEMBLE,
    DATASET_NOWCAST,
    DATASET_NWP,
    DATASET_PARAMETERS,
    DATASET_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
)


@dataclass(slots=True)
class CurrentConditions:
    """Snapshot of current weather conditions."""

    temperature: float | None = None
    humidity: float | None = None
    dew_point: float | None = None
    pressure: float | None = None  # hPa
    cloud_cover: float | None = None  # %
    wind_speed: float | None = None  # m/s
    wind_gust: float | None = None  # m/s
    wind_bearing: float | None = None  # degrees
    condition: str | None = None
    symbol_slug: str | None = None
    precipitation_type_code: int | None = None


@dataclass(slots=True)
class HourlyPoint:
    """A single forecast step (1 h for NWP/EPS, 15 min for nowcast)."""

    time: datetime
    temperature: float | None = None
    humidity: float | None = None
    dew_point: float | None = None
    precipitation: float | None = None  # mm per step
    precipitation_probability: float | None = None  # %
    cloud_cover: float | None = None  # %
    wind_speed: float | None = None  # m/s
    wind_gust: float | None = None  # m/s
    wind_bearing: float | None = None  # degrees
    condition: str | None = None
    symbol_slug: str | None = None


@dataclass(slots=True)
class DailyPoint:
    """A single daily forecast, aggregated client-side from the hourly series."""

    day: date
    temperature_max: float | None = None
    temperature_min: float | None = None
    precipitation: float | None = None
    precipitation_probability: float | None = None
    wind_speed_max: float | None = None
    wind_bearing: float | None = None
    condition: str | None = None


@dataclass(slots=True)
class WeatherBundle:
    """All weather data exposed by the coordinator."""

    reference_time: datetime
    current: CurrentConditions
    hourly: list[HourlyPoint] = field(default_factory=list)
    daily: list[DailyPoint] = field(default_factory=list)


@dataclass(slots=True)
class AirQualityPoint:
    """A single air-quality sample (current or forecast step)."""

    time: datetime
    no2: float | None = None  # µg/m³
    o3: float | None = None  # µg/m³
    pm10: float | None = None  # µg/m³
    pm25: float | None = None  # µg/m³


@dataclass(slots=True)
class AirQualityBundle:
    """All air-quality data exposed by the coordinator."""

    reference_time: datetime
    current: AirQualityPoint
    hourly: list[AirQualityPoint] = field(default_factory=list)


type GeoSphereBundle = WeatherBundle | AirQualityBundle
type GeoSphereConfigEntry = ConfigEntry["GeoSphereDataUpdateCoordinator"]


class GeoSphereDataUpdateCoordinator(DataUpdateCoordinator[GeoSphereBundle]):
    """Fetch forecast data from the GeoSphere Austria Data Hub."""

    config_entry: GeoSphereConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GeoSphereConfigEntry,
        latitude: float,
        longitude: float,
        dataset: str,
    ) -> None:
        """Initialise the coordinator for a given dataset and location."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{dataset}_{latitude:.3f}_{longitude:.3f}",
            update_interval=DATASET_SCAN_INTERVAL[dataset],
        )
        self._latitude = latitude
        self._longitude = longitude
        self._dataset = dataset
        self._session = async_get_clientsession(hass)
        # Populated by async_setup_entry once platforms have been forwarded,
        # so async_unload_entry knows which platforms were actually loaded
        # even after the config entry's dataset has been mutated.
        self.loaded_platforms: tuple[Platform, ...] = ()

    @property
    def dataset(self) -> str:
        """Return the configured GeoSphere dataset id."""
        return self._dataset

    @property
    def latitude(self) -> float:
        """Return the configured latitude in decimal degrees."""
        return self._latitude

    @property
    def longitude(self) -> float:
        """Return the configured longitude in decimal degrees."""
        return self._longitude

    async def _async_update_data(self) -> GeoSphereBundle:
        """Fetch the latest forecast from GeoSphere."""
        params: list[tuple[str, str]] = [
            ("parameters", p) for p in DATASET_PARAMETERS[self._dataset]
        ]
        params.append(("lat_lon", f"{self._latitude},{self._longitude}"))
        params.append(("output_format", "geojson"))
        url = f"{API_BASE_URL}/timeseries/forecast/{self._dataset}"
        try:
            async with self._session.get(
                url, params=params, timeout=ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except ClientResponseError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_http_error",
                translation_placeholders={
                    "status": str(err.status),
                    "reason": err.message or "",
                },
            ) from err
        except ClientError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
        parser = _PARSERS[self._dataset]
        try:
            bundle = parser(payload)
        except (KeyError, IndexError, ValueError, TypeError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_unexpected_response",
                translation_placeholders={"error": str(err)},
            ) from err
        if isinstance(bundle, WeatherBundle):
            _apply_night_condition(bundle, self._latitude, self._longitude)
        return bundle


# ---------------------------------------------------------------------------
# Raw payload helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RawForecast:
    """Flattened view of the GeoSphere GeoJSON payload."""

    reference_time: datetime
    timestamps: list[datetime]
    parameters: dict[str, dict[str, Any]]

    def series(self, key: str) -> list[float | None]:
        """Return the parallel data series for a named parameter."""
        param = self.parameters.get(key)
        if not param:
            return [None] * len(self.timestamps)
        return [None if v is None else float(v) for v in param.get("data", [])]


def _extract(payload: dict[str, Any]) -> _RawForecast:
    """Pull reference time, timestamps and parameter dict out of a payload."""
    reference_time = _parse_datetime(payload["reference_time"])
    timestamps = [_parse_datetime(t) for t in payload["timestamps"]]
    if not timestamps:
        raise ValueError("GeoSphere returned an empty forecast series")
    features = payload.get("features") or []
    if not features:
        raise ValueError("GeoSphere returned no features for the requested location")
    parameters = features[0]["properties"]["parameters"]
    return _RawForecast(reference_time, timestamps, parameters)


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def _precip_delta(
    acc: list[float | None],
    idx: int,
    timestamps: list[datetime],
    reference_time: datetime,
) -> float | None:
    """Return per-step precipitation in mm from an *accumulated* series.

    The first step can span several hours since the model's reference time;
    for that step we return the average rate across the accumulation period.
    Subsequent steps are simple consecutive diffs.
    """
    current = acc[idx]
    if current is None:
        return None
    if idx == 0:
        hours = (timestamps[0] - reference_time).total_seconds() / 3600.0
        if hours <= 0:
            return max(current, 0.0)
        return max(current / hours, 0.0)
    previous = acc[idx - 1]
    if previous is None:
        return max(current, 0.0)
    return max(current - previous, 0.0)


def _wind_from_components(
    u: float | None, v: float | None
) -> tuple[float | None, float | None]:
    """Convert eastward/northward wind components to speed & meteorological bearing.

    The bearing is the compass direction the wind is coming *from*, measured
    clockwise from north.
    """
    if u is None or v is None:
        return None, None
    speed = math.sqrt(u * u + v * v)
    bearing = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    return speed, bearing


def _precip_probability(
    p10: float | None,
    p50: float | None,
    p90: float | None,
    threshold: float = 0.1,
) -> float | None:
    """Approximate probability of precipitation (%) from EPS percentiles.

    ``p90`` being above the threshold means roughly 10% of ensemble members
    predict at least that much precipitation; ``p50`` above means >=50% do;
    ``p10`` above means >=90%. We collapse that to four buckets.
    """
    if p10 is None and p50 is None and p90 is None:
        return None
    if p10 is not None and p10 >= threshold:
        return 95.0
    if p50 is not None and p50 >= threshold:
        return 75.0
    if p90 is not None and p90 >= threshold:
        return 25.0
    return 5.0


def _derive_condition(
    *,
    cloud_pct: float | None,
    precipitation: float | None,
    temperature: float | None,
    default: str | None = None,
) -> str | None:
    """Derive a Home Assistant condition from cloud cover and precipitation.

    This is the fallback used when the AROME ``sy`` symbol is unavailable
    (i.e. for C-LAEF and INCA). It cannot detect thunderstorms or fog.
    """
    if precipitation is not None and precipitation >= 0.1:
        if temperature is not None:
            if temperature <= 0.5:
                return ATTR_CONDITION_SNOWY
            if temperature < 2.5:
                return ATTR_CONDITION_SNOWY_RAINY
        if precipitation >= 2.5:
            return ATTR_CONDITION_POURING
        return ATTR_CONDITION_RAINY
    if cloud_pct is None:
        return default
    if cloud_pct < 20:
        return ATTR_CONDITION_SUNNY
    if cloud_pct < 70:
        return ATTR_CONDITION_PARTLYCLOUDY
    return ATTR_CONDITION_CLOUDY


_CONDITION_PRIORITY: tuple[str, ...] = (
    ATTR_CONDITION_LIGHTNING_RAINY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_SUNNY,
    # Only picked for buckets whose hourly points are all nighttime — daily
    # buckets that contain any daytime step will land on SUNNY above.
    ATTR_CONDITION_CLEAR_NIGHT,
)


def _is_night(ts: datetime, latitude: float, longitude: float) -> bool:
    """Return True when the sun is below the horizon at ``ts`` for the given location."""
    observer = Observer(latitude=latitude, longitude=longitude)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    try:
        return bool(elevation(observer, ts) < 0.0)
    except ValueError:  # pragma: no cover - extreme latitudes near solstice
        return False


def _apply_night_condition(
    bundle: WeatherBundle, latitude: float, longitude: float
) -> None:
    """Replace ``sunny`` with ``clear-night`` for forecast points after dusk.

    AROME's ``sy`` symbol and the ``_derive_condition`` cloud-cover fallback
    both produce ``sunny`` regardless of solar position; Home Assistant
    distinguishes day and night via a separate ``clear-night`` condition.
    """
    for point in bundle.hourly:
        if point.condition == ATTR_CONDITION_SUNNY and _is_night(
            point.time, latitude, longitude
        ):
            point.condition = ATTR_CONDITION_CLEAR_NIGHT
    # ``current`` was populated from the first hourly step before we walked
    # the list, so re-sync it from whatever the first step says now.
    if bundle.hourly:
        bundle.current.condition = bundle.hourly[0].condition
    # Recompute per-day condition: parsers ran before the night fixup, so
    # buckets that are entirely at night would still be tagged ``sunny``.
    by_day: dict[date, list[str | None]] = defaultdict(list)
    for point in bundle.hourly:
        by_day[point.time.astimezone(UTC).date()].append(point.condition)
    for daily in bundle.daily:
        conditions = by_day.get(daily.day)
        if conditions:
            daily.condition = _dominant_condition(conditions)

# AROME ``sy`` weather symbol codes mapped to Home Assistant conditions.
# Legend published at
# https://github.com/Geosphere-Austria/dataset-api-docs/issues/30#issuecomment-2042539848
_SY_TO_CONDITION: dict[int, str] = {
    1: ATTR_CONDITION_SUNNY,             # Wolkenlos (cloudless)
    2: ATTR_CONDITION_SUNNY,             # Heiter (fair)
    3: ATTR_CONDITION_PARTLYCLOUDY,      # Wolkig (partly cloudy)
    4: ATTR_CONDITION_CLOUDY,            # Stark bewölkt (mostly cloudy)
    5: ATTR_CONDITION_CLOUDY,            # Bedeckt (overcast)
    6: ATTR_CONDITION_FOG,               # Bodennebel (ground fog)
    7: ATTR_CONDITION_FOG,               # Hochnebel (high fog / stratus)
    8: ATTR_CONDITION_RAINY,             # Leichter Regen (light rain)
    9: ATTR_CONDITION_RAINY,             # Mäßiger Regen (moderate rain)
    10: ATTR_CONDITION_POURING,          # Starker Regen (heavy rain)
    11: ATTR_CONDITION_SNOWY_RAINY,      # Leichter Schneeregen (sleet)
    12: ATTR_CONDITION_SNOWY_RAINY,      # Mäßiger Schneeregen
    13: ATTR_CONDITION_SNOWY_RAINY,      # Starker Schneeregen
    14: ATTR_CONDITION_SNOWY,            # Leichter Schneefall (light snow)
    15: ATTR_CONDITION_SNOWY,            # Mäßiger Schneefall (moderate snow)
    16: ATTR_CONDITION_SNOWY,            # Starker Schneefall (heavy snow)
    17: ATTR_CONDITION_RAINY,            # Leichter Regenschauer (rain shower)
    18: ATTR_CONDITION_RAINY,            # Mäßiger Regenschauer
    19: ATTR_CONDITION_POURING,          # Starker Regenschauer (heavy rain shower)
    20: ATTR_CONDITION_SNOWY_RAINY,      # Leichter Schneeregenschauer (sleet shower)
    21: ATTR_CONDITION_SNOWY_RAINY,      # Mäßiger Schneeregenschauer
    22: ATTR_CONDITION_SNOWY_RAINY,      # Starker Schneeregenschauer
    23: ATTR_CONDITION_SNOWY,            # Leichter Schneeschauer (snow shower)
    24: ATTR_CONDITION_SNOWY,            # Mäßiger Schneeschauer
    25: ATTR_CONDITION_SNOWY,            # Starker Schneeschauer (heavy snow shower)
    26: ATTR_CONDITION_LIGHTNING_RAINY,  # Leichtes Gewitter (thunderstorm)
    27: ATTR_CONDITION_LIGHTNING_RAINY,  # Mäßiges Gewitter
    28: ATTR_CONDITION_LIGHTNING_RAINY,  # Starkes Gewitter (severe thunderstorm)
    29: ATTR_CONDITION_LIGHTNING_RAINY,  # Gewitter mit Schneeregen (thunderstorm + sleet)
    30: ATTR_CONDITION_LIGHTNING_RAINY,  # Starkes Gewitter mit Schneeregen
    31: ATTR_CONDITION_LIGHTNING_RAINY,  # Gewitter mit Schneefall (thunderstorm + snow)
    32: ATTR_CONDITION_LIGHTNING_RAINY,  # Starkes Gewitter mit Schneefall
}


# Stable English slugs for AROME ``sy`` codes. The slug is the *state* of the
# weather_symbol enum sensor; it is never shown to the user directly — the
# Home Assistant translation system maps it to a localized label via
# ``entity.sensor.weather_symbol.state.<slug>`` in strings.json.
SY_TO_SLUG: dict[int, str] = {
    1: "cloudless",
    2: "fair",
    3: "partly_cloudy",
    4: "mostly_cloudy",
    5: "overcast",
    6: "ground_fog",
    7: "high_fog",
    8: "light_rain",
    9: "moderate_rain",
    10: "heavy_rain",
    11: "light_sleet",
    12: "moderate_sleet",
    13: "heavy_sleet",
    14: "light_snow",
    15: "moderate_snow",
    16: "heavy_snow",
    17: "light_rain_shower",
    18: "moderate_rain_shower",
    19: "heavy_rain_shower",
    20: "light_sleet_shower",
    21: "moderate_sleet_shower",
    22: "heavy_sleet_shower",
    23: "light_snow_shower",
    24: "moderate_snow_shower",
    25: "heavy_snow_shower",
    26: "light_thunderstorm",
    27: "moderate_thunderstorm",
    28: "heavy_thunderstorm",
    29: "thunderstorm_sleet",
    30: "heavy_thunderstorm_sleet",
    31: "thunderstorm_snow",
    32: "heavy_thunderstorm_snow",
}

WEATHER_SYMBOL_OPTIONS: tuple[str, ...] = tuple(SY_TO_SLUG.values())


# INCA ``pt`` precipitation-type slugs. Only partially known; codes not
# present in this map are exposed as ``None`` so the sensor reports
# "unknown" rather than an untranslated number.
# This mapping might contain errors since we have not yet observed all
# the INCA pt codes in the wild, only in the data.
#
# Value interpretation based on empirical observations:
# PT Value,Type,Min. Temp.,Max. Temp.,Mean Value,Typical Range (5-95 % Percentile),Number of Cells
# 1,Rain / Regen,+0.21 °C,+21.58 °C,+11.08 °C,+4.2 bis +16.0 °C,1 202 631
# 3,Sleet / Schneeregen,+1.90 °C,+1.97 °C,+1.94 °C,~+1.9 °C (very narrow),nur 3
# 5,Freezing Rain / Gefrierender Regen,-3.20 °C,+4.98 °C,+0.53 °C,-0.9 bis +2.1 °C,144 383
# 7,Snow / Schnee,-1.17 °C,+6.35 °C,+2.41 °C,+1.2 bis +3.4 °C,40 643
# 255,No Precipitation / Kein Niederschlag,-2.92 °C,+22.09 °C,+11.99 °C,+4.9 bis +18.8 °C,2 540 043
PT_TO_SLUG: dict[int, str] = {
    1: "rain",
    3: "sleet",
    5: "freezing_rain",
    7: "snow",
    255: "none",
}

PRECIPITATION_TYPE_OPTIONS: tuple[str, ...] = tuple(PT_TO_SLUG.values())


def _condition_from_pt(
    pt_code: int | None, precipitation: float | None
) -> str | None:
    """Derive a condition from the INCA precipitation type and amount.

    Returns ``None`` for unknown or unmapped codes so the caller can fall
    back to ``_derive_condition``.
    See _PT_DESCRIPTION for the pt_code values that are currently mapped.
    """
    if pt_code == 1:
        if precipitation is not None and precipitation >= 2.5:
            return ATTR_CONDITION_POURING
        if precipitation is not None and precipitation >= 0.1:
            return ATTR_CONDITION_RAINY
    if pt_code == 7:
        return ATTR_CONDITION_SNOWY
    return None


def _dominant_condition(conditions: list[str | None]) -> str | None:
    """Pick the most significant condition across a set of forecast steps."""
    present = {c for c in conditions if c}
    if not present:
        return None
    for candidate in _CONDITION_PRIORITY:
        if candidate in present:
            return candidate
    return next(iter(present))


# ---------------------------------------------------------------------------
# Dataset-specific parsers
# ---------------------------------------------------------------------------


def _parse_nwp(payload: dict[str, Any]) -> WeatherBundle:
    """Translate the AROME response into a WeatherBundle."""
    raw = _extract(payload)

    t2m = raw.series("t2m")
    rh2m = raw.series("rh2m")
    tcc = raw.series("tcc")
    sp = raw.series("sp")
    rr_acc = raw.series("rr_acc")
    u10m = raw.series("u10m")
    v10m = raw.series("v10m")
    ugust = raw.series("ugust")
    vgust = raw.series("vgust")
    sy = raw.series("sy")
    mnt2m = raw.series("mnt2m")
    mxt2m = raw.series("mxt2m")

    hourly: list[HourlyPoint] = []
    for idx, ts in enumerate(raw.timestamps):
        precipitation = _precip_delta(rr_acc, idx, raw.timestamps, raw.reference_time)
        wind_speed, wind_bearing = _wind_from_components(u10m[idx], v10m[idx])
        gust_speed, _ = _wind_from_components(ugust[idx], vgust[idx])
        tcc_val = tcc[idx]
        cloud_pct = tcc_val * 100.0 if tcc_val is not None else None
        sy_val = sy[idx]
        sy_code = int(sy_val) if sy_val is not None else None
        condition = _SY_TO_CONDITION.get(sy_code) if sy_code is not None else None
        if condition is None:
            condition = _derive_condition(
                cloud_pct=cloud_pct,
                precipitation=precipitation,
                temperature=t2m[idx],
            )
        symbol_slug = SY_TO_SLUG.get(sy_code) if sy_code is not None else None
        hourly.append(
            HourlyPoint(
                time=ts,
                temperature=t2m[idx],
                humidity=rh2m[idx],
                precipitation=precipitation,
                cloud_cover=cloud_pct,
                wind_speed=wind_speed,
                wind_gust=gust_speed,
                wind_bearing=wind_bearing,
                condition=condition,
                symbol_slug=symbol_slug,
            )
        )

    first = hourly[0]
    current = CurrentConditions(
        temperature=first.temperature,
        humidity=first.humidity,
        pressure=(sp[0] / 100.0) if sp[0] is not None else None,
        cloud_cover=first.cloud_cover,
        wind_speed=first.wind_speed,
        wind_gust=first.wind_gust,
        wind_bearing=first.wind_bearing,
        condition=first.condition,
        symbol_slug=first.symbol_slug,
    )

    daily = _aggregate_daily(hourly, mnt2m, mxt2m)

    return WeatherBundle(
        reference_time=raw.reference_time,
        current=current,
        hourly=hourly,
        daily=daily,
    )


def _parse_ensemble(payload: dict[str, Any]) -> WeatherBundle:
    """Translate the C-LAEF ensemble response into a WeatherBundle.

    Uses the 50th percentile as the point forecast and derives a coarse
    precipitation probability from the p10/p50/p90 spread.
    """
    raw = _extract(payload)

    t2m = raw.series("t2m_p50")
    tcc = raw.series("tcc_p50")
    rr_p10 = raw.series("rr_p10")
    rr_p50 = raw.series("rr_p50")
    rr_p90 = raw.series("rr_p90")
    u10m = raw.series("u10m_p50")
    v10m = raw.series("v10m_p50")
    mnt2m = raw.series("mnt2m_p50")
    mxt2m = raw.series("mxt2m_p50")

    hourly: list[HourlyPoint] = []
    for idx, ts in enumerate(raw.timestamps):
        precipitation = _precip_delta(rr_p50, idx, raw.timestamps, raw.reference_time)
        p10 = _precip_delta(rr_p10, idx, raw.timestamps, raw.reference_time)
        p90 = _precip_delta(rr_p90, idx, raw.timestamps, raw.reference_time)
        precip_prob = _precip_probability(p10, precipitation, p90)
        wind_speed, wind_bearing = _wind_from_components(u10m[idx], v10m[idx])
        tcc_val = tcc[idx]
        cloud_pct = tcc_val * 100.0 if tcc_val is not None else None
        condition = _derive_condition(
            cloud_pct=cloud_pct,
            precipitation=precipitation,
            temperature=t2m[idx],
        )
        hourly.append(
            HourlyPoint(
                time=ts,
                temperature=t2m[idx],
                precipitation=precipitation,
                precipitation_probability=precip_prob,
                cloud_cover=cloud_pct,
                wind_speed=wind_speed,
                wind_bearing=wind_bearing,
                condition=condition,
            )
        )

    first = hourly[0]
    current = CurrentConditions(
        temperature=first.temperature,
        cloud_cover=first.cloud_cover,
        wind_speed=first.wind_speed,
        wind_bearing=first.wind_bearing,
        condition=first.condition,
    )

    daily = _aggregate_daily(hourly, mnt2m, mxt2m)

    return WeatherBundle(
        reference_time=raw.reference_time,
        current=current,
        hourly=hourly,
        daily=daily,
    )


def _parse_nowcast(payload: dict[str, Any]) -> WeatherBundle:
    """Translate the INCA response into a WeatherBundle.

    Notes:
    - ``rr`` is per-15-min precipitation, already differenced.
    - ``ff``/``dd``/``fx`` are scalar wind speed/direction/gust.
    - Cloud cover and pressure are not available from this dataset.
    - Only ~3 h of data is returned, so we do not expose a daily forecast.
    """
    raw = _extract(payload)

    t2m = raw.series("t2m")
    td = raw.series("td")
    rh2m = raw.series("rh2m")
    rr = raw.series("rr")
    pt = raw.series("pt")
    ff = raw.series("ff")
    dd = raw.series("dd")
    fx = raw.series("fx")

    hourly: list[HourlyPoint] = []
    for idx, ts in enumerate(raw.timestamps):
        precipitation = rr[idx]
        pt_val = pt[idx]
        pt_code = int(pt_val) if pt_val is not None else None
        condition = _condition_from_pt(pt_code, precipitation)
        if condition is None:
            condition = _derive_condition(
                cloud_pct=None,
                precipitation=precipitation,
                temperature=t2m[idx],
                default=ATTR_CONDITION_PARTLYCLOUDY,
            )
        hourly.append(
            HourlyPoint(
                time=ts,
                temperature=t2m[idx],
                humidity=rh2m[idx],
                dew_point=td[idx],
                precipitation=precipitation,
                wind_speed=ff[idx],
                wind_gust=fx[idx],
                wind_bearing=dd[idx],
                condition=condition,
                symbol_slug=(
                    PT_TO_SLUG.get(pt_code) if pt_code is not None else None
                ),
            )
        )

    first = hourly[0]
    current = CurrentConditions(
        temperature=first.temperature,
        humidity=first.humidity,
        dew_point=first.dew_point,
        wind_speed=first.wind_speed,
        wind_gust=first.wind_gust,
        wind_bearing=first.wind_bearing,
        condition=first.condition,
        symbol_slug=first.symbol_slug,
        precipitation_type_code=(
            int(pt[0]) if pt and pt[0] is not None else None
        ),
    )

    return WeatherBundle(
        reference_time=raw.reference_time,
        current=current,
        hourly=hourly,
        daily=[],
    )


def _parse_chem(payload: dict[str, Any]) -> AirQualityBundle:
    """Translate the WRF-Chem air-quality response into an AirQualityBundle."""
    raw = _extract(payload)
    no2 = raw.series("no2surf")
    o3 = raw.series("o3surf")
    pm10 = raw.series("pm10surf")
    pm25 = raw.series("pm25surf")

    hourly = [
        AirQualityPoint(
            time=ts,
            no2=no2[idx],
            o3=o3[idx],
            pm10=pm10[idx],
            pm25=pm25[idx],
        )
        for idx, ts in enumerate(raw.timestamps)
    ]
    first = hourly[0]
    current = AirQualityPoint(
        time=first.time,
        no2=first.no2,
        o3=first.o3,
        pm10=first.pm10,
        pm25=first.pm25,
    )
    return AirQualityBundle(
        reference_time=raw.reference_time,
        current=current,
        hourly=hourly,
    )


_PARSERS: dict[str, Callable[[dict[str, Any]], GeoSphereBundle]] = {
    DATASET_NWP: _parse_nwp,
    DATASET_ENSEMBLE: _parse_ensemble,
    DATASET_NOWCAST: _parse_nowcast,
    DATASET_CHEM: _parse_chem,
}


# ---------------------------------------------------------------------------
# Daily aggregation
# ---------------------------------------------------------------------------


def _aggregate_daily(
    hourly: list[HourlyPoint],
    mnt2m: list[float | None],
    mxt2m: list[float | None],
) -> list[DailyPoint]:
    """Aggregate the hourly series into daily buckets keyed by UTC date."""
    buckets: dict[date, list[int]] = defaultdict(list)
    for idx, point in enumerate(hourly):
        buckets[point.time.astimezone(UTC).date()].append(idx)

    daily: list[DailyPoint] = []
    for day in sorted(buckets):
        indices = buckets[day]
        temps: list[float] = [
            t for i in indices if (t := hourly[i].temperature) is not None
        ]
        mn_values: list[float] = [v for i in indices if (v := mnt2m[i]) is not None]
        mx_values: list[float] = [v for i in indices if (v := mxt2m[i]) is not None]
        precip_values: list[float] = [
            p for i in indices if (p := hourly[i].precipitation) is not None
        ]
        precip_probs: list[float] = [
            p for i in indices if (p := hourly[i].precipitation_probability) is not None
        ]
        wind_values: list[tuple[int, float]] = [
            (i, s) for i in indices if (s := hourly[i].wind_speed) is not None
        ]
        max_speed: float | None
        wind_bearing: float | None
        if wind_values:
            max_i, max_speed = max(wind_values, key=lambda pair: pair[1])
            wind_bearing = hourly[max_i].wind_bearing
        else:
            max_speed = None
            wind_bearing = None
        condition = _dominant_condition([hourly[i].condition for i in indices])
        daily.append(
            DailyPoint(
                day=day,
                temperature_max=(
                    max(mx_values) if mx_values else (max(temps) if temps else None)
                ),
                temperature_min=(
                    min(mn_values) if mn_values else (min(temps) if temps else None)
                ),
                precipitation=sum(precip_values) if precip_values else 0.0,
                precipitation_probability=(
                    max(precip_probs) if precip_probs else None
                ),
                wind_speed_max=max_speed,
                wind_bearing=wind_bearing,
                condition=condition,
            )
        )
    return daily
