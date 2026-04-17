"""Tests for the diagnostics platform."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.geosphere_weather.const import (
    CONF_DATASET,
    DATASET_NWP,
    DOMAIN,
)
from custom_components.geosphere_weather.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .fixtures import nwp_payload
from .test_init import _mock_session_get

ZONE_ID = "zone.home"
REDACTED = "**REDACTED**"


async def test_diagnostics_redacts_sensitive_fields(hass: HomeAssistant) -> None:
    """Diagnostics must redact zone id, coordinates and unique_id."""
    hass.states.async_set(
        ZONE_ID,
        "zoning",
        {
            "friendly_name": "Home",
            ATTR_LATITUDE: 48.208,
            ATTR_LONGITUDE: 16.373,
        },
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
        unique_id=f"{ZONE_ID}_{DATASET_NWP}",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.geosphere_weather.coordinator.async_get_clientsession"
    ) as mock_session:
        mock_session.return_value.get = _mock_session_get(nwp_payload())
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"][CONF_ZONE] == REDACTED
    assert diagnostics["entry"]["unique_id"] == REDACTED
    assert diagnostics["coordinator"]["latitude"] == REDACTED
    assert diagnostics["coordinator"]["longitude"] == REDACTED
    assert diagnostics["coordinator"]["dataset"] == DATASET_NWP
    # The forecast payload itself is non-sensitive and should pass through.
    assert isinstance(diagnostics["data"], dict)
    assert "reference_time" in diagnostics["data"]


async def test_diagnostics_handles_missing_coordinator_data(
    hass: HomeAssistant,
) -> None:
    """If no update has succeeded yet, diagnostics still renders with data=None."""
    from custom_components.geosphere_weather.coordinator import (
        GeoSphereDataUpdateCoordinator,
    )

    hass.states.async_set(
        ZONE_ID,
        "zoning",
        {
            "friendly_name": "Home",
            ATTR_LATITUDE: 48.208,
            ATTR_LONGITUDE: 16.373,
        },
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ZONE: ZONE_ID, CONF_DATASET: DATASET_NWP},
        unique_id=f"{ZONE_ID}_{DATASET_NWP}",
    )
    entry.add_to_hass(hass)

    coordinator = GeoSphereDataUpdateCoordinator(
        hass, entry, 48.208, 16.373, DATASET_NWP
    )
    entry.runtime_data = coordinator  # type: ignore[misc]

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["data"] is None
    assert diagnostics["coordinator"]["dataset"] == DATASET_NWP
    assert diagnostics["coordinator"]["latitude"] == REDACTED
