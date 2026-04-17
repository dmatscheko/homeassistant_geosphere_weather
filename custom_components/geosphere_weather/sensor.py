"""Sensor platform for the GeoSphere Austria integration.

Provides two distinct kinds of sensor entities:

* Air-quality measurements (µg/m³) for the WRF-Chem dataset.
* An enum "weather symbol" / "precipitation type" sensor for the AROME
  and INCA datasets, exposing the raw model symbol in a way Home Assistant
  can translate via ``strings.json``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    DATASET_CHEM,
    DATASET_DOC_URL,
    DATASET_NOWCAST,
    DATASET_NWP,
    DATASET_SHORT_NAMES,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import (
    PRECIPITATION_TYPE_OPTIONS,
    WEATHER_SYMBOL_OPTIONS,
    AirQualityBundle,
    GeoSphereBundle,
    GeoSphereConfigEntry,
    GeoSphereDataUpdateCoordinator,
    WeatherBundle,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GeoSphereSensorDescription(SensorEntityDescription):
    """Describes a GeoSphere sensor backed by the coordinator bundle."""

    value_fn: Callable[[GeoSphereBundle], float | str | None]


def _air_quality(field: str) -> Callable[[GeoSphereBundle], float | None]:
    def getter(bundle: GeoSphereBundle) -> float | None:
        if not isinstance(bundle, AirQualityBundle):
            return None
        value = getattr(bundle.current, field)
        return None if value is None else float(value)

    return getter


def _symbol_slug(bundle: GeoSphereBundle) -> str | None:
    if not isinstance(bundle, WeatherBundle):
        return None
    return bundle.current.symbol_slug


AIR_QUALITY_DESCRIPTIONS: tuple[GeoSphereSensorDescription, ...] = (
    GeoSphereSensorDescription(
        key="no2",
        translation_key="no2",
        device_class=SensorDeviceClass.NITROGEN_DIOXIDE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=_air_quality("no2"),
    ),
    GeoSphereSensorDescription(
        key="o3",
        translation_key="o3",
        device_class=SensorDeviceClass.OZONE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=_air_quality("o3"),
    ),
    GeoSphereSensorDescription(
        key="pm10",
        translation_key="pm10",
        device_class=SensorDeviceClass.PM10,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=_air_quality("pm10"),
    ),
    GeoSphereSensorDescription(
        key="pm25",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=_air_quality("pm25"),
    ),
)

WEATHER_SYMBOL_DESCRIPTION = GeoSphereSensorDescription(
    key="weather_symbol",
    translation_key="weather_symbol",
    device_class=SensorDeviceClass.ENUM,
    options=list(WEATHER_SYMBOL_OPTIONS),
    value_fn=_symbol_slug,
)

PRECIPITATION_TYPE_DESCRIPTION = GeoSphereSensorDescription(
    key="precipitation_type",
    translation_key="precipitation_type",
    device_class=SensorDeviceClass.ENUM,
    options=list(PRECIPITATION_TYPE_OPTIONS),
    value_fn=_symbol_slug,
)


_DATASET_DESCRIPTIONS: dict[str, tuple[GeoSphereSensorDescription, ...]] = {
    DATASET_NWP: (WEATHER_SYMBOL_DESCRIPTION,),
    DATASET_NOWCAST: (PRECIPITATION_TYPE_DESCRIPTION,),
    DATASET_CHEM: AIR_QUALITY_DESCRIPTIONS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoSphereConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GeoSphere sensors (air quality or weather symbol) from an entry."""
    coordinator = entry.runtime_data
    descriptions = _DATASET_DESCRIPTIONS.get(coordinator.dataset, ())
    async_add_entities(
        GeoSphereSensor(entry=entry, coordinator=coordinator, description=desc)
        for desc in descriptions
    )


class GeoSphereSensor(
    CoordinatorEntity[GeoSphereDataUpdateCoordinator], SensorEntity
):
    """A sensor backed by the GeoSphere coordinator bundle."""

    entity_description: GeoSphereSensorDescription
    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        entry: GeoSphereConfigEntry,
        coordinator: GeoSphereDataUpdateCoordinator,
        description: GeoSphereSensorDescription,
    ) -> None:
        """Initialise a sensor entity for one description on one config entry."""
        super().__init__(coordinator=coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        dataset_label = DATASET_SHORT_NAMES.get(
            coordinator.dataset, coordinator.dataset
        )
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=dataset_label,
            name=entry.title,
            configuration_url=DATASET_DOC_URL.get(coordinator.dataset),
        )

    @property
    def native_value(self) -> float | str | None:
        """Return the current value from the coordinator bundle."""
        bundle = self.coordinator.data
        if bundle is None:
            return None
        return self.entity_description.value_fn(bundle)

    @property
    def extra_state_attributes(self) -> dict[str, float | str] | None:
        """Expose the model reference time and location metadata."""
        bundle = self.coordinator.data
        if bundle is None:
            return None
        return {
            "dataset": self.coordinator.dataset,
            "reference_time": bundle.reference_time.isoformat(),
            "latitude": self.coordinator.latitude,
            "longitude": self.coordinator.longitude,
        }
