"""Sensor platform for the GeoSphere Austria integration (air quality)."""

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
    DATASET_DOC_URL,
    DATASET_SHORT_NAMES,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import (
    AirQualityBundle,
    GeoSphereConfigEntry,
    GeoSphereDataUpdateCoordinator,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GeoSphereSensorDescription(SensorEntityDescription):
    """Describes a GeoSphere air-quality sensor."""

    value_fn: Callable[[AirQualityBundle], float | None]


SENSOR_DESCRIPTIONS: tuple[GeoSphereSensorDescription, ...] = (
    GeoSphereSensorDescription(
        key="no2",
        translation_key="no2",
        device_class=SensorDeviceClass.NITROGEN_DIOXIDE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=lambda bundle: bundle.current.no2,
    ),
    GeoSphereSensorDescription(
        key="o3",
        translation_key="o3",
        device_class=SensorDeviceClass.OZONE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=lambda bundle: bundle.current.o3,
    ),
    GeoSphereSensorDescription(
        key="pm10",
        translation_key="pm10",
        device_class=SensorDeviceClass.PM10,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=lambda bundle: bundle.current.pm10,
    ),
    GeoSphereSensorDescription(
        key="pm25",
        translation_key="pm25",
        device_class=SensorDeviceClass.PM25,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        suggested_display_precision=1,
        value_fn=lambda bundle: bundle.current.pm25,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GeoSphereConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GeoSphere air-quality sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        GeoSphereAirQualitySensor(entry=entry, coordinator=coordinator, description=desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class GeoSphereAirQualitySensor(
    CoordinatorEntity[GeoSphereDataUpdateCoordinator], SensorEntity
):
    """A single air-quality sensor backed by the GeoSphere chem dataset."""

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
        """Initialise an air-quality sensor."""
        super().__init__(coordinator=coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
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
    def native_value(self) -> float | None:
        """Return the current pollutant concentration in µg/m³."""
        bundle = self.coordinator.data
        if not isinstance(bundle, AirQualityBundle):
            return None
        return self.entity_description.value_fn(bundle)

    @property
    def extra_state_attributes(self) -> dict[str, float | str] | None:
        """Expose the model reference time and location metadata."""
        bundle = self.coordinator.data
        if not isinstance(bundle, AirQualityBundle):
            return None
        return {
            "dataset": self.coordinator.dataset,
            "reference_time": bundle.reference_time.isoformat(),
            "latitude": self.coordinator.latitude,
            "longitude": self.coordinator.longitude,
        }
