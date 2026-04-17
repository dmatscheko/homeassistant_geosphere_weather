"""The GeoSphere Austria weather integration."""

from __future__ import annotations

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

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

    platforms = DATASET_PLATFORMS[dataset]

    # After a reconfigure that changes the dataset, entities from the previous
    # dataset may still be in the registry. When the platform type changes
    # (weather <-> sensor) they can never be re-used, so purge them here.
    # For same-platform dataset switches the unique_id scheme is stable, so
    # the registry keeps the entity and it rebinds cleanly.
    _purge_stale_entities(hass, entry, platforms)

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
    new_platforms: tuple[Platform, ...],
) -> None:
    """Remove registry entries whose domain no longer matches the new dataset."""
    ent_reg = er.async_get(hass)
    allowed = {platform.value for platform in new_platforms}
    stale = [
        reg_entry.entity_id
        for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        if reg_entry.domain not in allowed
    ]
    for entity_id in stale:
        ent_reg.async_remove(entity_id)
