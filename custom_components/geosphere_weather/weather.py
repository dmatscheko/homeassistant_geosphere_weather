"""Weather platform for the GeoSphere Austria integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.weather import (
    ATTR_FORECAST_CLOUD_COVERAGE,
    ATTR_FORECAST_CONDITION,
    ATTR_FORECAST_HUMIDITY,
    ATTR_FORECAST_NATIVE_DEW_POINT,
    ATTR_FORECAST_NATIVE_PRECIPITATION,
    ATTR_FORECAST_NATIVE_TEMP,
    ATTR_FORECAST_NATIVE_TEMP_LOW,
    ATTR_FORECAST_NATIVE_WIND_GUST_SPEED,
    ATTR_FORECAST_NATIVE_WIND_SPEED,
    ATTR_FORECAST_PRECIPITATION_PROBABILITY,
    ATTR_FORECAST_WIND_BEARING,
    Forecast,
    SingleCoordinatorWeatherEntity,
)
from homeassistant.components.weather.const import WeatherEntityFeature
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTRIBUTION,
    DATASET_DOC_URL,
    DATASET_NOWCAST,
    DATASET_SHORT_NAMES,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import (
    GeoSphereConfigEntry,
    GeoSphereDataUpdateCoordinator,
    WeatherBundle,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoSphereConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the GeoSphere weather entity from a config entry."""
    async_add_entities(
        [GeoSphereWeatherEntity(entry=entry, coordinator=entry.runtime_data)]
    )


class GeoSphereWeatherEntity(
    SingleCoordinatorWeatherEntity[GeoSphereDataUpdateCoordinator]
):
    """A Home Assistant weather entity backed by GeoSphere forecasts."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_name = None
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.METERS_PER_SECOND

    def __init__(
        self,
        *,
        entry: GeoSphereConfigEntry,
        coordinator: GeoSphereDataUpdateCoordinator,
    ) -> None:
        """Initialise the weather entity."""
        # Nowcast only covers the next ~3 hours, so we don't expose a daily
        # forecast for it.
        if coordinator.dataset == DATASET_NOWCAST:
            self._attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
        else:
            self._attr_supported_features = (
                WeatherEntityFeature.FORECAST_HOURLY
                | WeatherEntityFeature.FORECAST_DAILY
            )
        super().__init__(coordinator=coordinator)
        self._attr_unique_id = entry.entry_id
        dataset_label = DATASET_SHORT_NAMES.get(coordinator.dataset, coordinator.dataset)
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=dataset_label,
            name=entry.title,
            configuration_url=DATASET_DOC_URL.get(coordinator.dataset),
        )

    @property
    def _bundle(self) -> WeatherBundle:
        """Return the coordinator data narrowed to WeatherBundle.

        The weather platform is only instantiated for weather datasets, so the
        bundle is guaranteed to be a WeatherBundle at runtime.
        """
        data = self.coordinator.data
        assert isinstance(data, WeatherBundle)
        return data

    @property
    def condition(self) -> str | None:
        """Return the current weather condition."""
        return self._bundle.current.condition

    @property
    def native_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._bundle.current.temperature

    @property
    def humidity(self) -> float | None:
        """Return the current relative humidity."""
        return self._bundle.current.humidity

    @property
    def native_dew_point(self) -> float | None:
        """Return the current dew point (nowcast only)."""
        return self._bundle.current.dew_point

    @property
    def native_pressure(self) -> float | None:
        """Return the current surface pressure in hPa."""
        return self._bundle.current.pressure

    @property
    def cloud_coverage(self) -> float | None:
        """Return the current cloud coverage in percent."""
        return self._bundle.current.cloud_cover

    @property
    def native_wind_speed(self) -> float | None:
        """Return the current wind speed."""
        return self._bundle.current.wind_speed

    @property
    def native_wind_gust_speed(self) -> float | None:
        """Return the current wind gust speed."""
        return self._bundle.current.wind_gust

    @property
    def wind_bearing(self) -> float | None:
        """Return the current wind bearing (meteorological)."""
        return self._bundle.current.wind_bearing

    @property
    def extra_state_attributes(self) -> dict[str, float | str] | None:
        """Expose raw GeoSphere attributes that have no first-class HA slot."""
        data = self._bundle
        attributes: dict[str, float | str] = {
            "dataset": self.coordinator.dataset,
            "reference_time": data.reference_time.isoformat(),
            "latitude": self.coordinator.latitude,
            "longitude": self.coordinator.longitude,
        }
        return attributes

    @callback
    def _async_forecast_daily(self) -> list[Forecast] | None:
        """Return the daily forecast in native units."""
        daily = self._bundle.daily
        if not daily:
            return None
        forecasts: list[Forecast] = []
        for point in daily:
            forecast_datetime = datetime.combine(
                point.day, datetime.min.time(), tzinfo=dt_util.UTC
            )
            forecast: Forecast = {
                "datetime": forecast_datetime.isoformat(),
                ATTR_FORECAST_CONDITION: point.condition,
                ATTR_FORECAST_NATIVE_TEMP: point.temperature_max,
                ATTR_FORECAST_NATIVE_TEMP_LOW: point.temperature_min,
                ATTR_FORECAST_NATIVE_PRECIPITATION: point.precipitation,
                ATTR_FORECAST_PRECIPITATION_PROBABILITY: (
                    None
                    if point.precipitation_probability is None
                    else int(point.precipitation_probability)
                ),
                ATTR_FORECAST_NATIVE_WIND_SPEED: point.wind_speed_max,
                ATTR_FORECAST_WIND_BEARING: point.wind_bearing,
            }
            forecasts.append(forecast)
        return forecasts

    @callback
    def _async_forecast_hourly(self) -> list[Forecast] | None:
        """Return the hourly (or 15-min) forecast in native units."""
        hourly = self._bundle.hourly
        if not hourly:
            return None
        now = dt_util.utcnow()
        forecasts: list[Forecast] = []
        for point in hourly:
            time = point.time
            if time.tzinfo is None:
                time = time.replace(tzinfo=dt_util.UTC)
            if time < now:
                continue
            forecast: Forecast = {
                "datetime": time.isoformat(),
                ATTR_FORECAST_CONDITION: point.condition,
                ATTR_FORECAST_NATIVE_TEMP: point.temperature,
                ATTR_FORECAST_HUMIDITY: point.humidity,
                ATTR_FORECAST_NATIVE_DEW_POINT: point.dew_point,
                ATTR_FORECAST_NATIVE_PRECIPITATION: point.precipitation,
                ATTR_FORECAST_PRECIPITATION_PROBABILITY: (
                    None
                    if point.precipitation_probability is None
                    else int(point.precipitation_probability)
                ),
                ATTR_FORECAST_CLOUD_COVERAGE: (
                    None if point.cloud_cover is None else int(point.cloud_cover)
                ),
                ATTR_FORECAST_NATIVE_WIND_SPEED: point.wind_speed,
                ATTR_FORECAST_NATIVE_WIND_GUST_SPEED: point.wind_gust,
                ATTR_FORECAST_WIND_BEARING: point.wind_bearing,
            }
            forecasts.append(forecast)
        return forecasts
