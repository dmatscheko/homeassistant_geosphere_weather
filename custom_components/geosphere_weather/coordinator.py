"""Data update coordinator for GeoSphere Austria weather forecasts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
import math
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientTimeout

from homeassistant.components.weather import (
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_SUNNY,
)
from homeassistant.config_entries import ConfigEntry
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
    symbol: int | None = None


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
    symbol: int | None = None


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
                f"GeoSphere API returned HTTP {err.status}: {err.message}"
            ) from err
        except ClientError as err:
            raise UpdateFailed(f"Error communicating with GeoSphere API: {err}") from err
        parser = _PARSERS[self._dataset]
        try:
            return parser(payload)
        except (KeyError, IndexError, ValueError, TypeError) as err:
            raise UpdateFailed(f"Unexpected GeoSphere API response: {err}") from err


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

    GeoSphere's ``sy`` symbol codes are not publicly documented, so we do not
    rely on them for the primary condition. The raw symbol is still surfaced
    as an entity attribute for users who want to map it themselves.
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
    ATTR_CONDITION_POURING,
    ATTR_CONDITION_SNOWY,
    ATTR_CONDITION_SNOWY_RAINY,
    ATTR_CONDITION_RAINY,
    ATTR_CONDITION_FOG,
    ATTR_CONDITION_CLOUDY,
    ATTR_CONDITION_PARTLYCLOUDY,
    ATTR_CONDITION_SUNNY,
)


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
        cloud_pct = tcc[idx] * 100.0 if tcc[idx] is not None else None
        symbol = int(sy[idx]) if sy[idx] is not None else None
        condition = _derive_condition(
            cloud_pct=cloud_pct,
            precipitation=precipitation,
            temperature=t2m[idx],
        )
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
                symbol=symbol,
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
        symbol=first.symbol,
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
        cloud_pct = tcc[idx] * 100.0 if tcc[idx] is not None else None
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
        symbol = int(pt[idx]) if pt[idx] is not None else None
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
                symbol=symbol,
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
        symbol=first.symbol,
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
        temps = [
            hourly[i].temperature for i in indices if hourly[i].temperature is not None
        ]
        mn_values = [mnt2m[i] for i in indices if mnt2m[i] is not None]
        mx_values = [mxt2m[i] for i in indices if mxt2m[i] is not None]
        precip_values = [
            hourly[i].precipitation
            for i in indices
            if hourly[i].precipitation is not None
        ]
        precip_probs = [
            hourly[i].precipitation_probability
            for i in indices
            if hourly[i].precipitation_probability is not None
        ]
        wind_values = [
            (i, hourly[i].wind_speed)
            for i in indices
            if hourly[i].wind_speed is not None
        ]
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
