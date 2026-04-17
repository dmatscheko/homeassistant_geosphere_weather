"""The GeoSphere Austria weather integration."""

from __future__ import annotations

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DATASET,
    DATASET_PLATFORMS,
    DATASET_SENSOR_KEYS,
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

    platforms = DATASET_PLATFORMS[dataset]

    # After a reconfigure that changes the dataset, registry entries from the
    # previous dataset may no longer match the new entity set — either because
    # the platform type changed (weather <-> sensor) or because the sensor
    # keys differ (e.g. AROME -> WRF-Chem, both produce sensors but with
    # different unique_id suffixes). Purge anything that won't be re-bound.
    _purge_stale_entities(hass, entry, dataset)

    coordinator = GeoSphereDataUpdateCoordinator(
        hass, entry, latitude, longitude, dataset
    )
    await coordinator.async_config_entry_first_refresh()
    coordinator.loaded_platforms = platforms
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GeoSphereConfigEntry) -> bool:
    """Unload a GeoSphere Austria config entry."""
    # Prefer the platforms that were actually loaded: on a reconfigure-reload
    # the entry data already reflects the *new* dataset, but runtime_data
    # still points at the old coordinator, so its loaded_platforms is what
    # HA currently has set up.
    coordinator = getattr(entry, "runtime_data", None)
    platforms: tuple[Platform, ...] | None = getattr(
        coordinator, "loaded_platforms", None
    )
    if platforms is None:
        platforms = DATASET_PLATFORMS[entry.data.get(CONF_DATASET, DEFAULT_DATASET)]
    return await hass.config_entries.async_unload_platforms(entry, platforms)


def _purge_stale_entities(
    hass: HomeAssistant,
    entry: GeoSphereConfigEntry,
    dataset: str,
) -> None:
    """Remove registry entries that don't match the new dataset's entity set."""
    platforms = DATASET_PLATFORMS[dataset]
    expected_unique_ids: set[str] = set()
    if Platform.WEATHER in platforms:
        expected_unique_ids.add(entry.entry_id)
    for key in DATASET_SENSOR_KEYS[dataset]:
        expected_unique_ids.add(f"{entry.entry_id}_{key}")

    ent_reg = er.async_get(hass)
    stale = [
        reg_entry.entity_id
        for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        if reg_entry.unique_id not in expected_unique_ids
    ]
    for entity_id in stale:
        ent_reg.async_remove(entity_id)
