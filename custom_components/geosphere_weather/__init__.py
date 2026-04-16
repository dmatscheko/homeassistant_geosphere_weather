"""The GeoSphere Austria weather integration."""

from __future__ import annotations

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .const import (
    CONF_DATASET,
    DATASET_PLATFORMS,
    DEFAULT_DATASET,
    SUPPORTED_DATASETS,
)
from .coordinator import GeoSphereConfigEntry, GeoSphereDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: GeoSphereConfigEntry) -> bool:
    """Set up GeoSphere Austria from a config entry."""
    zone_id = entry.data[CONF_ZONE]
    dataset = entry.data.get(CONF_DATASET, DEFAULT_DATASET)
    if dataset not in SUPPORTED_DATASETS:
        raise ConfigEntryError(f"Unsupported GeoSphere dataset: {dataset}")

    zone_state = hass.states.get(zone_id)
    if zone_state is None:
        raise ConfigEntryNotReady(f"Zone {zone_id} is not available")

    latitude = zone_state.attributes.get(ATTR_LATITUDE)
    longitude = zone_state.attributes.get(ATTR_LONGITUDE)
    if latitude is None or longitude is None:
        raise ConfigEntryNotReady(
            f"Zone {zone_id} is missing latitude/longitude attributes"
        )

    coordinator = GeoSphereDataUpdateCoordinator(
        hass, entry, latitude, longitude, dataset
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(
        entry, DATASET_PLATFORMS[dataset]
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GeoSphereConfigEntry) -> bool:
    """Unload a GeoSphere Austria config entry."""
    dataset = entry.data.get(CONF_DATASET, DEFAULT_DATASET)
    return await hass.config_entries.async_unload_platforms(
        entry, DATASET_PLATFORMS[dataset]
    )
